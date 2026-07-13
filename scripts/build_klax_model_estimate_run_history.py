"""Build KLAX model-estimate history through each trading window.

This complements build_klax_temperature_history.py. The earlier script scores
fixed lead-day estimates from Open-Meteo Previous Runs. This script uses
Open-Meteo Single Runs to reconstruct what each model estimated at different
times from the prior-day market open through the target day.

Example:
    python scripts/build_klax_model_estimate_run_history.py ^
      --start-date 2026-06-09 ^
      --end-date 2026-07-08 ^
      --allow-partial

Outputs:
    data/processed/klax_temperature_history/klax_model_estimate_run_history_long.csv
    data/processed/klax_temperature_history/klax_model_estimate_market_asof_hourly.csv
    data/processed/klax_temperature_history/run_history_coverage_report.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_klax_temperature_history import (  # noqa: E402
    DISALLOWED_MODELS,
    actuals_to_frame,
    build_session,
    date_range_label,
    fetch_noaa_actuals,
    model_aliases,
    parse_date_arg,
    parse_model_override,
    read_json,
    requested_url,
    resolve_location,
    response_payload,
    utc_now_iso,
    write_json,
)

LOGGER = logging.getLogger("klax_model_estimate_run_history")

OPEN_METEO_SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"

GLOBAL_UTC_CYCLES = (0, 6, 12, 18)
THREE_HOURLY_UTC_CYCLES = (0, 3, 6, 9, 12, 15, 18, 21)
MODEL_RUN_CYCLES_UTC = {
    "gfs_seamless": GLOBAL_UTC_CYCLES,
    "gfs013": GLOBAL_UTC_CYCLES,
    "gfs_global": GLOBAL_UTC_CYCLES,
    "gfs": GLOBAL_UTC_CYCLES,
    "ecmwf_ifs": GLOBAL_UTC_CYCLES,
    "aifs": GLOBAL_UTC_CYCLES,
    "nam": GLOBAL_UTC_CYCLES,
    "nam_conus": GLOBAL_UTC_CYCLES,
    "hrrr": THREE_HOURLY_UTC_CYCLES,
    "nbm": THREE_HOURLY_UTC_CYCLES,
    "rap": THREE_HOURLY_UTC_CYCLES,
}
MODEL_AVAILABILITY_LAG_HOURS = {
    "gfs_seamless": 6,
    "gfs013": 6,
    "gfs_global": 6,
    "gfs": 6,
    "ecmwf_ifs": 6,
    "aifs": 6,
    "nam": 3,
    "nam_conus": 3,
    "hrrr": 2,
    "nbm": 2,
    "rap": 2,
}
SINGLE_RUN_MODEL_ALIASES = {
    "gfs_seamless": ["gfs_seamless"],
    "gfs013": ["gfs013", "gfs_global"],
    "gfs_global": ["gfs_global", "gfs025"],
    "gfs": ["gfs_global", "gfs_seamless", "gfs"],
    "nam": ["ncep_nam_conus", "nam_conus", "nam"],
    "nam_conus": ["ncep_nam_conus", "nam_conus"],
    "hrrr": ["ncep_hrrr_conus", "hrrr"],
    "nbm": ["ncep_nbm_conus", "nbm"],
    "ecmwf_ifs": ["ecmwf_ifs", "ecmwf_ifs025"],
    "aifs": ["ecmwf_aifs025", "aifs"],
    "rap": ["ncep_rap_conus", "rap"],
}


@dataclass(frozen=True)
class SingleRunRequest:
    model: str
    model_id_used: str
    run_utc: datetime
    raw_path: Path
    metadata_path: Path


@dataclass(frozen=True)
class SingleRunResult:
    model: str
    model_id_used: str
    run_utc: datetime
    raw_path: Path
    metadata_path: Path
    status: str
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--raw-dir", default="data/raw/klax_temperature_history")
    parser.add_argument("--out-dir", default="data/processed/klax_temperature_history")
    parser.add_argument("--models", help="Optional comma-separated model override")
    parser.add_argument("--refresh", action="store_true", help="Overwrite cached raw run files")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Create outputs even when some model runs fail",
    )
    parser.add_argument(
        "--market-open-hour",
        type=int,
        default=7,
        help="Local hour on the prior day when the next-day market opens",
    )
    parser.add_argument(
        "--market-end-hour",
        type=int,
        default=23,
        help="Local hour on the target day through which estimates are evaluated",
    )
    parser.add_argument(
        "--asof-frequency-hours",
        type=int,
        default=1,
        help="Spacing for the market-as-of output; 1 means every local hour",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Parallel Open-Meteo Single Runs downloads",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def ensure_dirs(raw_dir: Path, out_dir: Path) -> None:
    (raw_dir / "noaa_actuals").mkdir(parents=True, exist_ok=True)
    (raw_dir / "open_meteo_single_runs").mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)


def target_dates(start_date: date, end_date: date) -> list[date]:
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def market_window(
    target_date: date,
    timezone_name: str,
    market_open_hour: int,
    market_end_hour: int,
) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(target_date - timedelta(days=1), time(market_open_hour), tzinfo=zone)
    end = datetime.combine(target_date, time(market_end_hour), tzinfo=zone)
    return start, end


def asof_times(
    target_date: date,
    timezone_name: str,
    market_open_hour: int,
    market_end_hour: int,
    frequency_hours: int,
) -> list[datetime]:
    start, end = market_window(target_date, timezone_name, market_open_hour, market_end_hour)
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(hours=frequency_hours)
    return values


def run_cycles_for_model(model: str) -> tuple[int, ...]:
    return MODEL_RUN_CYCLES_UTC.get(model, GLOBAL_UTC_CYCLES)


def availability_lag(model: str) -> timedelta:
    return timedelta(hours=MODEL_AVAILABILITY_LAG_HOURS.get(model, 6))


def floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def ceil_to_hour(value: datetime) -> datetime:
    floored = floor_to_hour(value)
    return floored if floored == value else floored + timedelta(hours=1)


def candidate_runs_for_model(
    model: str,
    dates: list[date],
    timezone_name: str,
    market_open_hour: int,
    market_end_hour: int,
) -> list[datetime]:
    cycles = set(run_cycles_for_model(model))
    lag = availability_lag(model)
    run_values: set[datetime] = set()
    for target_date in dates:
        start_local, end_local = market_window(
            target_date,
            timezone_name,
            market_open_hour,
            market_end_hour,
        )
        start_utc = floor_to_hour(start_local.astimezone(timezone.utc) - lag - timedelta(hours=12))
        end_utc = ceil_to_hour(end_local.astimezone(timezone.utc) - lag)
        current = start_utc
        while current <= end_utc:
            if current.hour in cycles:
                run_values.add(current)
            current += timedelta(hours=1)
    return sorted(run_values)


def compact_run(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%MZ")


def previous_runs_model_id(
    raw_dir: Path,
    model: str,
    start_date: date,
    end_date: date,
) -> str | None:
    label = date_range_label(start_date, end_date)
    metadata_path = raw_dir / "open_meteo" / model / f"{model}_{label}_metadata.json"
    if not metadata_path.exists():
        return None
    metadata = read_json(metadata_path)
    model_id = metadata.get("actual_open_meteo_model_id_used")
    return str(model_id) if model_id else None


def preferred_model_ids(
    raw_dir: Path,
    model: str,
    start_date: date,
    end_date: date,
) -> list[str]:
    preferred = previous_runs_model_id(raw_dir, model, start_date, end_date)
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(SINGLE_RUN_MODEL_ALIASES.get(model, model_aliases(model)))
    return list(dict.fromkeys(candidates))


def build_run_requests(
    raw_dir: Path,
    models: list[str],
    dates: list[date],
    location_timezone: str,
    start_date: date,
    end_date: date,
    market_open_hour: int,
    market_end_hour: int,
) -> tuple[list[SingleRunRequest], dict[str, list[str]]]:
    requests_by_key: dict[tuple[str, str, datetime], SingleRunRequest] = {}
    model_ids_by_model: dict[str, list[str]] = {}
    for model in models:
        model_ids = preferred_model_ids(raw_dir, model, start_date, end_date)
        model_ids_by_model[model] = model_ids
        if model in DISALLOWED_MODELS:
            continue
        for model_id in model_ids[:1]:
            for run_utc in candidate_runs_for_model(
                model,
                dates,
                location_timezone,
                market_open_hour,
                market_end_hour,
            ):
                model_dir = raw_dir / "open_meteo_single_runs" / model
                stem = f"{model}_{model_id}_{compact_run(run_utc)}"
                requests_by_key[(model, model_id, run_utc)] = SingleRunRequest(
                    model=model,
                    model_id_used=model_id,
                    run_utc=run_utc,
                    raw_path=model_dir / f"{stem}.json",
                    metadata_path=model_dir / f"{stem}_metadata.json",
                )
    return sorted(requests_by_key.values(), key=lambda item: (item.model, item.run_utc)), model_ids_by_model


def single_run_params(location: Any, request: SingleRunRequest) -> dict[str, Any]:
    return {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone_name,
        "temperature_unit": "fahrenheit",
        "models": request.model_id_used,
        "run": request.run_utc.strftime("%Y-%m-%dT%H:%M"),
        "hourly": "temperature_2m",
        "forecast_days": 4,
    }


def is_single_run_success(response: requests.Response, payload: Any) -> bool:
    if not response.ok or not isinstance(payload, dict):
        return False
    hourly = payload.get("hourly")
    return isinstance(hourly, dict) and "time" in hourly and "temperature_2m" in hourly


def fetch_single_run(
    request: SingleRunRequest,
    location: Any,
    refresh: bool,
) -> SingleRunResult:
    if request.raw_path.exists() and request.metadata_path.exists() and not refresh:
        metadata = read_json(request.metadata_path)
        status = str(metadata.get("status") or "success")
        return SingleRunResult(
            model=request.model,
            model_id_used=request.model_id_used,
            run_utc=request.run_utc,
            raw_path=request.raw_path,
            metadata_path=request.metadata_path,
            status=status,
            error=metadata.get("error"),
        )

    session = build_session()
    params = single_run_params(location, request)
    response = session.get(OPEN_METEO_SINGLE_RUNS_URL, params=params, timeout=60)
    payload = response_payload(response)
    requested = response.url or requested_url(OPEN_METEO_SINGLE_RUNS_URL, params)
    metadata = {
        "canonical_model": request.model,
        "actual_open_meteo_model_id_used": request.model_id_used,
        "run_utc": request.run_utc.isoformat(),
        "requested_url": requested,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone_name,
        "downloaded_at_utc": utc_now_iso(),
        "status_code": response.status_code,
        "status": "success" if is_single_run_success(response, payload) else "error",
        "error": None if is_single_run_success(response, payload) else single_run_error(response, payload),
    }
    write_json(request.raw_path, payload)
    write_json(request.metadata_path, metadata)
    return SingleRunResult(
        model=request.model,
        model_id_used=request.model_id_used,
        run_utc=request.run_utc,
        raw_path=request.raw_path,
        metadata_path=request.metadata_path,
        status=str(metadata["status"]),
        error=metadata["error"],
    )


def single_run_error(response: requests.Response, payload: Any) -> str:
    reason = payload.get("reason") if isinstance(payload, dict) else None
    message = reason or str(payload)[:500]
    return f"HTTP {response.status_code}: {message}"


def fetch_all_single_runs(
    requests_to_fetch: list[SingleRunRequest],
    location: Any,
    refresh: bool,
    max_workers: int,
) -> list[SingleRunResult]:
    results: list[SingleRunResult] = []
    if not requests_to_fetch:
        return results
    LOGGER.info("Fetching or reusing %s Open-Meteo Single Runs", len(requests_to_fetch))
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(fetch_single_run, request, location, refresh): request
            for request in requests_to_fetch
        }
        completed = 0
        for future in as_completed(futures):
            completed += 1
            request = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = SingleRunResult(
                    model=request.model,
                    model_id_used=request.model_id_used,
                    run_utc=request.run_utc,
                    raw_path=request.raw_path,
                    metadata_path=request.metadata_path,
                    status="error",
                    error=str(exc),
                )
            results.append(result)
            if completed % 100 == 0 or completed == len(requests_to_fetch):
                LOGGER.info("Single Runs progress: %s/%s", completed, len(requests_to_fetch))
    return sorted(results, key=lambda item: (item.model, item.run_utc))


def raw_run_estimate_for_target(
    result: SingleRunResult,
    target_date: date,
    timezone_name: str,
) -> dict[str, Any] | None:
    if not result.succeeded:
        return None
    payload = read_json(result.raw_path)
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    frame = pd.DataFrame(hourly)
    if frame.empty or "time" not in frame.columns or "temperature_2m" not in frame.columns:
        return None
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["date"] = frame["time"].dt.date
    subset = frame[frame["date"] == target_date].copy()
    subset["temperature_2m"] = pd.to_numeric(subset["temperature_2m"], errors="coerce")
    temps = subset["temperature_2m"].dropna()
    if temps.empty:
        return None
    return {
        "estimated_high_f": round(float(temps.max()), 1),
        "source_hour_count": int(temps.count()),
        "forecast_first_hour_pt": timestamp_to_pt_label(subset["time"].min(), timezone_name),
        "forecast_last_hour_pt": timestamp_to_pt_label(subset["time"].max(), timezone_name),
    }


def timestamp_to_pt_iso(value: Any, timezone_name: str) -> str:
    return to_pt_datetime(value, timezone_name).isoformat()


def timestamp_to_pt_label(value: Any, timezone_name: str) -> str:
    return to_pt_datetime(value, timezone_name).strftime("%Y-%m-%d %H:%M PT")


def to_pt_datetime(value: Any, timezone_name: str) -> datetime:
    parsed = pd.Timestamp(value).to_pydatetime()
    zone = ZoneInfo(timezone_name)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    else:
        parsed = parsed.astimezone(zone)
    return parsed


def run_history_rows(
    results: list[SingleRunResult],
    actuals: pd.DataFrame,
    dates: list[date],
    location: Any,
    market_open_hour: int,
    market_end_hour: int,
) -> pd.DataFrame:
    actual_by_date = {
        date.fromisoformat(str(row.date)): {
            "actual_high_f": float(row.actual_high_f),
            "actual_station_id": row.actual_station_id,
        }
        for row in actuals.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for target_date in dates:
        actual = actual_by_date.get(target_date)
        if actual is None:
            continue
        window_start, window_end = market_window(
            target_date,
            location.timezone_name,
            market_open_hour,
            market_end_hour,
        )
        for result in results:
            if not result.succeeded:
                continue
            available_utc = result.run_utc + availability_lag(result.model)
            available_local = available_utc.astimezone(ZoneInfo(location.timezone_name))
            if available_local > window_end:
                continue
            estimate = raw_run_estimate_for_target(result, target_date, location.timezone_name)
            if estimate is None:
                continue
            error = round(float(estimate["estimated_high_f"]) - float(actual["actual_high_f"]), 1)
            rows.append(
                {
                    "date": target_date.isoformat(),
                    "location": location.location,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "actual_station_id": actual["actual_station_id"],
                    "actual_high_f": actual["actual_high_f"],
                    "model": result.model,
                    "model_source": "open_meteo_single_runs",
                    "model_id_used": result.model_id_used,
                    "run_time_pt": timestamp_to_pt_label(result.run_utc, location.timezone_name),
                    "estimate_available_pt": timestamp_to_pt_label(
                        available_local,
                        location.timezone_name,
                    ),
                    "market_window_start_pt": timestamp_to_pt_label(
                        window_start,
                        location.timezone_name,
                    ),
                    "market_window_end_pt": timestamp_to_pt_label(
                        window_end,
                        location.timezone_name,
                    ),
                    "estimate_available_pt_iso": timestamp_to_pt_iso(
                        available_local,
                        location.timezone_name,
                    ),
                    "run_utc": result.run_utc.isoformat(),
                    "estimate_available_utc": available_utc.isoformat(),
                    "available_by_market_open": available_local <= window_start,
                    "hours_since_market_open": round(
                        (available_local - window_start).total_seconds() / 3600,
                        2,
                    ),
                    "hours_before_target_end": round(
                        (window_end - available_local).total_seconds() / 3600,
                        2,
                    ),
                    "estimated_high_f": estimate["estimated_high_f"],
                    "error_f": error,
                    "abs_error_f": abs(error),
                    "source_hour_count": estimate["source_hour_count"],
                    "forecast_first_hour_pt": estimate["forecast_first_hour_pt"],
                    "forecast_last_hour_pt": estimate["forecast_last_hour_pt"],
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["date", "model", "estimate_available_pt_iso", "run_utc"])


def market_asof_rows(
    run_history: pd.DataFrame,
    actuals: pd.DataFrame,
    dates: list[date],
    models: list[str],
    location: Any,
    market_open_hour: int,
    market_end_hour: int,
    frequency_hours: int,
) -> pd.DataFrame:
    if run_history.empty:
        return pd.DataFrame()
    actual_by_date = {
        str(row.date): {
            "actual_high_f": float(row.actual_high_f),
            "actual_station_id": row.actual_station_id,
        }
        for row in actuals.itertuples(index=False)
    }
    run_history = run_history.copy()
    run_history["_estimate_available_utc"] = pd.to_datetime(
        run_history["estimate_available_pt_iso"],
        utc=True,
    )
    rows: list[dict[str, Any]] = []
    for target_date in dates:
        target_key = target_date.isoformat()
        actual = actual_by_date.get(target_key)
        if actual is None:
            continue
        window_start, window_end = market_window(
            target_date,
            location.timezone_name,
            market_open_hour,
            market_end_hour,
        )
        target_history = run_history[run_history["date"] == target_key]
        target_asof_times = asof_times(
            target_date,
            location.timezone_name,
            market_open_hour,
            market_end_hour,
            frequency_hours,
        )
        for model in models:
            model_history = target_history[target_history["model"] == model].sort_values(
                "_estimate_available_utc"
            )
            if model_history.empty:
                continue
            available_times = model_history["_estimate_available_utc"]
            for asof in target_asof_times:
                asof_key = timestamp_to_pt_label(asof, location.timezone_name)
                asof_utc = pd.Timestamp(asof).tz_convert("UTC")
                position = int(available_times.searchsorted(asof_utc, side="right")) - 1
                if position < 0:
                    continue
                latest = model_history.iloc[position]
                rows.append(
                    {
                        "date": target_key,
                        "location": location.location,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "actual_station_id": actual["actual_station_id"],
                        "actual_high_f": actual["actual_high_f"],
                        "asof_pt": asof_key,
                        "asof_date_pt": asof.date().isoformat(),
                        "asof_hour_pt": asof.hour,
                        "asof_market_day": market_day_label(asof.date(), target_date),
                        "hours_since_market_open": round(
                            (asof - window_start).total_seconds() / 3600,
                            2,
                        ),
                        "hours_before_target_end": round(
                            (window_end - asof).total_seconds() / 3600,
                            2,
                        ),
                        "model": model,
                        "model_source": "open_meteo_single_runs",
                        "model_id_used": latest["model_id_used"],
                        "run_time_pt": latest["run_time_pt"],
                        "estimate_available_pt": latest["estimate_available_pt"],
                        "asof_utc": asof.astimezone(timezone.utc).isoformat(),
                        "run_utc": latest["run_utc"],
                        "estimated_high_f": latest["estimated_high_f"],
                        "error_f": latest["error_f"],
                        "abs_error_f": latest["abs_error_f"],
                        "source_hour_count": latest["source_hour_count"],
                    }
                )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["date", "model", "hours_since_market_open", "asof_pt"])


def market_day_label(asof_date: date, target_date: date) -> str:
    if asof_date < target_date:
        return "previous_day"
    if asof_date == target_date:
        return "target_day"
    return "after_target_day"


def coverage_report(run_history: pd.DataFrame, dates: list[date], models: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    total_dates = len(set(run_history["date"])) if not run_history.empty else 0
    for model in models:
        subset = run_history[run_history["model"] == model] if not run_history.empty else pd.DataFrame()
        rows.append(
            {
                "model": model,
                "model_id_used": subset["model_id_used"].dropna().iloc[0]
                if not subset.empty and not subset["model_id_used"].dropna().empty
                else None,
                "first_date_available": subset["date"].min() if not subset.empty else None,
                "last_date_available": subset["date"].max() if not subset.empty else None,
                "dates_with_estimate": subset["date"].nunique() if not subset.empty else 0,
                "dates_missing_estimate": max(0, total_dates - subset["date"].nunique())
                if total_dates
                else len(dates),
                "run_rows": len(subset),
                "mean_error_f": round(float(subset["error_f"].mean()), 3)
                if not subset.empty
                else None,
                "mean_abs_error_f": round(float(subset["abs_error_f"].mean()), 3)
                if not subset.empty
                else None,
            }
        )
    return pd.DataFrame(rows)


def write_failed_runs(results: list[SingleRunResult], out_dir: Path) -> Path:
    rows = [
        {
            "model": result.model,
            "model_id_used": result.model_id_used,
            "run_utc": result.run_utc.isoformat(),
            "reason": result.error,
            "timestamp": utc_now_iso(),
        }
        for result in results
        if not result.succeeded
    ]
    path = out_dir / "run_history_failed_runs.csv"
    pd.DataFrame(
        rows,
        columns=["model", "model_id_used", "run_utc", "reason", "timestamp"],
    ).to_csv(path, index=False)
    return path


def write_outputs(
    run_history: pd.DataFrame,
    asof_history: pd.DataFrame,
    coverage: pd.DataFrame,
    out_dir: Path,
) -> tuple[Path, Path, Path]:
    run_path = out_dir / "klax_model_estimate_run_history_long.csv"
    asof_path = out_dir / "klax_model_estimate_market_asof_hourly.csv"
    coverage_path = out_dir / "run_history_coverage_report.csv"
    run_output = run_history.drop(
        columns=[col for col in run_history.columns if col.endswith("_pt_iso")],
        errors="ignore",
    )
    run_output.to_csv(run_path, index=False)
    try:
        asof_history.to_csv(asof_path, index=False)
    except PermissionError:
        asof_path = out_dir / "klax_model_estimate_market_asof_by_model_hourly.csv"
        asof_history.to_csv(asof_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    return run_path, asof_path, coverage_path


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    location = resolve_location()
    start_date = parse_date_arg(args.start_date)
    end_date = parse_date_arg(args.end_date, default_yesterday_tz=location.timezone_name)
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")
    if args.asof_frequency_hours < 1:
        raise ValueError("--asof-frequency-hours must be at least 1")

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    ensure_dirs(raw_dir, out_dir)

    requested_models = parse_model_override(args.models)
    requested_models = [model for model in requested_models if model not in DISALLOWED_MODELS]
    dates = target_dates(start_date, end_date)
    LOGGER.info(
        "Building run-history estimates for %s to %s at %s (%s,%s)",
        start_date,
        end_date,
        location.location,
        location.latitude,
        location.longitude,
    )

    session = build_session()
    actuals_raw = fetch_noaa_actuals(
        session=session,
        location=location,
        start_date=start_date,
        end_date=end_date,
        raw_dir=raw_dir,
        refresh=args.refresh,
    )
    actuals = actuals_to_frame(actuals_raw, location)
    actual_dates = {date.fromisoformat(str(value)) for value in actuals["date"]}
    missing_actual_dates = [day for day in dates if day not in actual_dates]
    if missing_actual_dates:
        LOGGER.warning(
            "Scored run-history outputs will omit %s date(s) without NOAA actuals: %s",
            len(missing_actual_dates),
            ", ".join(day.isoformat() for day in missing_actual_dates[:20]),
        )

    requests_to_fetch, _model_ids = build_run_requests(
        raw_dir=raw_dir,
        models=requested_models,
        dates=dates,
        location_timezone=location.timezone_name,
        start_date=start_date,
        end_date=end_date,
        market_open_hour=args.market_open_hour,
        market_end_hour=args.market_end_hour,
    )
    results = fetch_all_single_runs(
        requests_to_fetch,
        location=location,
        refresh=args.refresh,
        max_workers=args.max_workers,
    )
    failed = [result for result in results if not result.succeeded]
    failed_path = write_failed_runs(results, out_dir)
    if failed and not args.allow_partial:
        raise SystemExit(
            f"{len(failed)} single-run downloads failed. "
            "No transformed outputs were created. Re-run with --allow-partial to aggregate successes."
        )

    run_history = run_history_rows(
        results=results,
        actuals=actuals,
        dates=dates,
        location=location,
        market_open_hour=args.market_open_hour,
        market_end_hour=args.market_end_hour,
    )
    asof_history = market_asof_rows(
        run_history=run_history,
        actuals=actuals,
        dates=dates,
        models=requested_models,
        location=location,
        market_open_hour=args.market_open_hour,
        market_end_hour=args.market_end_hour,
        frequency_hours=args.asof_frequency_hours,
    )
    coverage = coverage_report(run_history, dates, requested_models)
    run_path, asof_path, coverage_path = write_outputs(run_history, asof_history, coverage, out_dir)

    succeeded = [result for result in results if result.succeeded]
    print(
        "\n".join(
            [
                "",
                "KLAX model estimate run-history summary",
                "=======================================",
                f"Date range requested: {start_date} to {end_date}",
                f"Dates with NOAA actuals: {len(actual_dates)}",
                f"Models requested: {', '.join(requested_models)}",
                f"Single-run raw requests: {len(results)}",
                f"Single-run successes: {len(succeeded)}",
                f"Single-run failures: {len(failed)}",
                f"Run-history rows: {len(run_history)}",
                f"Market-asof rows: {len(asof_history)}",
                f"Run-history output: {run_path}",
                f"Market-asof output: {asof_path}",
                f"Coverage report: {coverage_path}",
                f"Failed-runs report: {failed_path}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
