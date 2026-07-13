"""Backfill KLAX high-temperature model candidates from Open-Meteo and Herbie.

The output is meant for model selection, not live trading. It keeps the two
provider families separate:

* Open-Meteo candidates are fetched from Open-Meteo Single Runs.
* Direct NOAA candidates are fetched through Herbie/NOAA GRIB2 archives.
  Herbie writes downloaded GRIB/subset files into a cache directory so reruns
  do not have to download the same file again.

Both families are scored against KLAX NOAA/NCEI daily TMAX and written into a
common long table plus side-by-side and summary scoreboards.

Example:
    python scripts/backfill_klax_open_meteo_herbie_scoreboard.py ^
      --start-date 2026-06-09 ^
      --end-date 2026-07-08 ^
      --allow-partial
"""

from __future__ import annotations

import argparse
import io
import logging
import math
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

BOT_REPO = Path(r"C:\Users\jarve\Documents\Codex\kalshi_weather")
BOT_SRC = BOT_REPO / "src"
if str(BOT_SRC) not in sys.path:
    sys.path.insert(0, str(BOT_SRC))

from kalshi_weather.data.herbie_client import (  # noqa: E402
    NOAA_HERBIE_MODELS as NOAA_HERBIE_MODEL_SPECS,
    extract_nearest_temperature,
    forecast_hours_for_model_window,
    recent_cycles_for_model,
)
from kalshi_weather.model.lax_high_temp import (  # noqa: E402
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_TIMEZONE,
    lax_climate_day_utc,
)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_klax_model_estimate_run_history import (  # noqa: E402
    SingleRunResult,
    asof_times,
    availability_lag,
    build_run_requests,
    fetch_all_single_runs,
    market_day_label,
    market_window,
    read_json,
    timestamp_to_pt_iso,
    timestamp_to_pt_label,
)
from build_klax_temperature_history import (  # noqa: E402
    DEFAULT_MODEL_ORDER,
    DISALLOWED_MODELS,
    actuals_to_frame,
    build_session,
    fetch_noaa_actuals,
    parse_date_arg,
    parse_model_override,
    resolve_location,
)

LOGGER = logging.getLogger("klax_open_meteo_herbie_scoreboard")
PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc

DEFAULT_RAW_DIR = Path("data/raw/klax_temperature_history")
DEFAULT_OUT_DIR = Path("data/processed/klax_temperature_history")
DEFAULT_HERBIE_CACHE = DEFAULT_RAW_DIR / "herbie_direct"
DIRECT_HERBIE_CANONICAL_MODELS = {"nbm", "gfs", "hrrr", "rap"}
DEFAULT_OPEN_METEO_MODELS = [
    model
    for model in DEFAULT_MODEL_ORDER
    if model not in DISALLOWED_MODELS and model not in DIRECT_HERBIE_CANONICAL_MODELS
]
DEFAULT_HERBIE_MODELS = ["nbm", "gfs", "hrrr", "rap"]


@dataclass(frozen=True)
class HerbieCycleResult:
    successful: bool
    model_id: str
    cycle_utc: datetime
    forecast_hours_used: tuple[int, ...]
    future_high_f: float | None
    source_url: str | None
    source_path: str | None
    error_message: str | None
    attempt_count: int
    success_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", help="YYYY-MM-DD. Default is 30 days before --end-date.")
    parser.add_argument("--end-date", help="YYYY-MM-DD. Default is yesterday in KLAX local time.")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--herbie-cache-dir",
        default=str(DEFAULT_HERBIE_CACHE),
        help=(
            "Directory where Herbie saves downloaded NOAA GRIB/subset files. "
            "Use a new/empty directory for a fresh direct-Herbie backfill."
        ),
    )
    parser.add_argument(
        "--open-meteo-models",
        default=",".join(DEFAULT_OPEN_METEO_MODELS),
        help=(
            "Comma-separated Open-Meteo candidate models. Defaults exclude "
            "nbm/gfs/hrrr/rap because those are evaluated through direct Herbie."
        ),
    )
    parser.add_argument(
        "--herbie-models",
        default=",".join(DEFAULT_HERBIE_MODELS),
        help="Comma-separated direct NOAA/Herbie models.",
    )
    parser.add_argument("--market-open-hour", type=int, default=7)
    parser.add_argument("--market-end-hour", type=int, default=18)
    parser.add_argument("--asof-frequency-hours", type=int, default=1)
    parser.add_argument("--max-workers", type=int, default=4, help="Open-Meteo download workers.")
    parser.add_argument("--max-cycles", type=int, default=6, help="Recent Herbie cycles to try per as-of.")
    parser.add_argument("--max-forecast-hours", type=int, default=48)
    parser.add_argument("--max-failed-attempts-per-cycle", type=int, default=8)
    parser.add_argument("--refresh-open-meteo", action="store_true", help="Re-download cached Open-Meteo runs.")
    parser.add_argument("--refresh-actuals", action="store_true", help="Re-download cached NOAA actuals.")
    parser.add_argument("--skip-open-meteo", action="store_true")
    parser.add_argument("--skip-herbie", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write outputs even when some model runs fail.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the requested date/model plan without downloading or processing data.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def target_dates(start_date: date, end_date: date) -> list[date]:
    values: list[date] = []
    current = start_date
    while current <= end_date:
        values.append(current)
        current += timedelta(days=1)
    return values


def default_start_date(end_date: date) -> date:
    return end_date - timedelta(days=29)


def parse_model_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def ensure_dirs(raw_dir: Path, out_dir: Path) -> None:
    (raw_dir / "noaa_actuals").mkdir(parents=True, exist_ok=True)
    (raw_dir / "open_meteo_single_runs").mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)


def actuals_by_date_frame(actuals: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        str(row.date): {
            "actual_high_f": float(row.actual_high_f),
            "actual_station_id": row.actual_station_id,
            "actual_source": row.actual_source,
        }
        for row in actuals.itertuples(index=False)
    }


def winning_bracket(actual_high_f: float) -> tuple[int, int]:
    lower = math.floor(actual_high_f)
    upper = math.ceil(actual_high_f)
    if lower == upper:
        upper = lower + 1
    return lower, upper


def bracket_hit(estimate_high_f: float | None, actual_high_f: float | None) -> bool | None:
    if estimate_high_f is None or actual_high_f is None:
        return None
    lower, upper = winning_bracket(float(actual_high_f))
    return lower <= float(estimate_high_f) <= upper


def climate_window_local_naive(target_date: date) -> tuple[datetime, datetime]:
    start_utc, end_utc = lax_climate_day_utc(target_date)
    return (
        start_utc.astimezone(PT).replace(tzinfo=None),
        end_utc.astimezone(PT).replace(tzinfo=None),
    )


def open_meteo_run_estimate_for_target(
    result: SingleRunResult,
    target_date: date,
) -> dict[str, Any] | None:
    if not result.succeeded:
        return None
    payload = read_json(result.raw_path)
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    frame = pd.DataFrame(hourly)
    if frame.empty or "time" not in frame.columns or "temperature_2m" not in frame.columns:
        return None
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame["temperature_2m"] = pd.to_numeric(frame["temperature_2m"], errors="coerce")
    start_local, end_local = climate_window_local_naive(target_date)
    subset = frame[(frame["time"] >= start_local) & (frame["time"] < end_local)].copy()
    temps = subset["temperature_2m"].dropna()
    if temps.empty:
        return None
    return {
        "estimated_high_f": round(float(temps.max()), 1),
        "source_hour_count": int(temps.count()),
        "forecast_first_hour_pt": str(subset["time"].min()),
        "forecast_last_hour_pt": str(subset["time"].max()),
    }


def build_open_meteo_run_history(
    results: list[SingleRunResult],
    actuals: pd.DataFrame,
    dates: list[date],
    location: Any,
    market_open_hour: int,
    market_end_hour: int,
) -> pd.DataFrame:
    actual_by_date = actuals_by_date_frame(actuals)
    date_set = set(dates)
    window_by_date = {
        target_date: market_window(
            target_date,
            location.timezone_name,
            market_open_hour,
            market_end_hour,
        )
        for target_date in dates
    }
    climate_window_by_date = {target_date: climate_window_local_naive(target_date) for target_date in dates}
    location_tz = ZoneInfo(location.timezone_name)
    rows: list[dict[str, Any]] = []
    for result in results:
        if not result.succeeded:
            continue
        payload = read_json(result.raw_path)
        hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
        frame = pd.DataFrame(hourly)
        if frame.empty or "time" not in frame.columns or "temperature_2m" not in frame.columns:
            continue
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
        frame["temperature_2m"] = pd.to_numeric(frame["temperature_2m"], errors="coerce")
        frame = frame.dropna(subset=["time", "temperature_2m"])
        if frame.empty:
            continue
        first_date = (frame["time"].min() - pd.Timedelta(days=1)).date()
        last_date = (frame["time"].max() + pd.Timedelta(days=1)).date()
        available_utc = result.run_utc + availability_lag(result.model)
        available_local = available_utc.astimezone(location_tz)
        current_date = first_date
        while current_date <= last_date:
            target_date = current_date
            current_date = current_date + timedelta(days=1)
            if target_date not in date_set:
                continue
            target_key = target_date.isoformat()
            actual = actual_by_date.get(target_key)
            if actual is None:
                continue
            window_start, window_end = window_by_date[target_date]
            if available_local > window_end:
                continue
            start_local, end_local = climate_window_by_date[target_date]
            subset = frame[(frame["time"] >= start_local) & (frame["time"] < end_local)].copy()
            temps = subset["temperature_2m"].dropna()
            if temps.empty:
                continue
            estimate = {
                "estimated_high_f": round(float(temps.max()), 1),
                "source_hour_count": int(temps.count()),
                "forecast_first_hour_pt": str(subset["time"].min()),
                "forecast_last_hour_pt": str(subset["time"].max()),
            }
            error = round(float(estimate["estimated_high_f"]) - float(actual["actual_high_f"]), 3)
            rows.append(
                {
                    "date": target_key,
                    "location": location.location,
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "actual_station_id": actual["actual_station_id"],
                    "actual_high_f": actual["actual_high_f"],
                    "provider_family": "open_meteo",
                    "model_key": f"open_meteo:{result.model}",
                    "model": result.model,
                    "model_source": "open_meteo_single_runs",
                    "model_id_used": result.model_id_used,
                    "run_time_pt": timestamp_to_pt_label(result.run_utc, location.timezone_name),
                    "estimate_available_pt": timestamp_to_pt_label(
                        available_local,
                        location.timezone_name,
                    ),
                    "estimate_available_pt_iso": timestamp_to_pt_iso(
                        available_local,
                        location.timezone_name,
                    ),
                    "run_utc": result.run_utc.isoformat(),
                    "cycle_utc": result.run_utc.isoformat(),
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
                    "within_winning_bracket": bracket_hit(
                        estimate["estimated_high_f"],
                        actual["actual_high_f"],
                    ),
                    "source_hour_count": estimate["source_hour_count"],
                    "forecast_hours_used": None,
                    "forecast_first_hour_pt": estimate["forecast_first_hour_pt"],
                    "forecast_last_hour_pt": estimate["forecast_last_hour_pt"],
                    "successful": True,
                    "error_message": None,
                    "estimate_logic": "model_run_full_klax_climate_day",
                }
            )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["date", "model_key", "estimate_available_pt_iso", "run_utc"])


def build_open_meteo_asof_history(
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
    actual_by_date = actuals_by_date_frame(actuals)
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
            model_key = f"open_meteo:{model}"
            model_history = target_history[target_history["model_key"] == model_key].sort_values(
                "_estimate_available_utc"
            )
            if model_history.empty:
                continue
            available_times = model_history["_estimate_available_utc"]
            for asof in target_asof_times:
                asof_utc = pd.Timestamp(asof).tz_convert("UTC")
                position = int(available_times.searchsorted(asof_utc, side="right")) - 1
                if position < 0:
                    continue
                latest = model_history.iloc[position]
                row = latest.drop(labels=["_estimate_available_utc"]).to_dict()
                row.update(
                    {
                        "asof_pt": timestamp_to_pt_label(asof, location.timezone_name),
                        "asof_date_pt": asof.date().isoformat(),
                        "asof_hour_pt": asof.hour,
                        "asof_market_day": market_day_label(asof.date(), target_date),
                        "asof_utc": asof.astimezone(UTC).isoformat(),
                        "hours_since_market_open_asof": round(
                            (asof - window_start).total_seconds() / 3600,
                            2,
                        ),
                        "hours_before_target_end_asof": round(
                            (window_end - asof).total_seconds() / 3600,
                            2,
                        ),
                    }
                )
                rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["date", "model_key", "hours_since_market_open_asof", "asof_pt"])


def herbie_fetch_cycle_result(
    *,
    model_id: str,
    cycle_utc: datetime,
    target_date: date,
    cache_dir: Path,
    max_forecast_hours: int,
    max_failed_attempts: int,
) -> HerbieCycleResult:
    from herbie import Herbie

    model_spec = NOAA_HERBIE_MODEL_SPECS.get(model_id)
    if model_spec is None:
        return HerbieCycleResult(
            successful=False,
            model_id=model_id,
            cycle_utc=cycle_utc,
            forecast_hours_used=(),
            future_high_f=None,
            source_url=None,
            source_path=None,
            error_message=f"Unsupported Herbie model: {model_id}",
            attempt_count=0,
            success_count=0,
        )

    window_start_utc, window_end_utc = lax_climate_day_utc(target_date)
    forecast_hours = forecast_hours_for_model_window(
        cycle_utc,
        window_start_utc,
        window_end_utc,
        model_spec.get("forecast_hours", [0, max_forecast_hours]),
        max_forecast_hours,
    )
    if not forecast_hours:
        return HerbieCycleResult(
            successful=False,
            model_id=model_id,
            cycle_utc=cycle_utc,
            forecast_hours_used=(),
            future_high_f=None,
            source_url=None,
            source_path=None,
            error_message="No forecast hours intersect the KLAX climate-day window.",
            attempt_count=0,
            success_count=0,
        )

    values: list[tuple[int, float, str | None]] = []
    errors: list[str] = []
    attempt_count = 0
    for fxx in forecast_hours:
        if attempt_count >= max_failed_attempts and not values:
            break
        for search in model_spec.get("search_strings", ["TMP:2 m"]):
            if attempt_count >= max_failed_attempts and not values:
                break
            attempt_count += 1
            try:
                kwargs: dict[str, Any] = {
                    "model": model_spec.get("model", model_id),
                    "product": model_spec["product"],
                    "fxx": int(fxx),
                    "verbose": False,
                    "save_dir": str(cache_dir),
                }
                if "domain" in model_spec:
                    kwargs["domain"] = model_spec["domain"]
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    herbie_obj = Herbie(cycle_utc.replace(tzinfo=None), **kwargs)
                    dataset = herbie_obj.xarray(search)
                source = getattr(herbie_obj, "grib", None) or getattr(herbie_obj, "idx", None)
                try:
                    extraction = extract_nearest_temperature(
                        dataset,
                        LAX_LATITUDE,
                        LAX_LONGITUDE,
                        forecast_hour=fxx,
                        valid_time_utc=cycle_utc + timedelta(hours=fxx),
                        source_url=str(source) if source else None,
                    )
                finally:
                    close = getattr(dataset, "close", None)
                    if callable(close):
                        close()
                values.append((int(fxx), float(extraction.value_f), str(source) if source else None))
                break
            except Exception as exc:  # noqa: BLE001 - try next search/fxx
                errors.append(f"{cycle_utc.isoformat()} f{fxx} {search}: {exc}")
    if not values:
        return HerbieCycleResult(
            successful=False,
            model_id=model_id,
            cycle_utc=cycle_utc,
            forecast_hours_used=(),
            future_high_f=None,
            source_url=None,
            source_path=None,
            error_message="No usable 2-meter temperature values returned by Herbie. "
            + " | ".join(errors[:8]),
            attempt_count=attempt_count,
            success_count=0,
        )

    best_fxx, future_high, source = max(values, key=lambda item: item[1])
    return HerbieCycleResult(
        successful=True,
        model_id=model_id,
        cycle_utc=cycle_utc,
        forecast_hours_used=tuple(sorted({item[0] for item in values})),
        future_high_f=future_high,
        source_url=source if source and source.startswith("http") else None,
        source_path=source if source and not source.startswith("http") else None,
        error_message=None,
        attempt_count=attempt_count,
        success_count=len(values),
    )


def build_herbie_asof_history(
    *,
    actuals: pd.DataFrame,
    dates: list[date],
    models: list[str],
    location: Any,
    cache_dir: Path,
    market_open_hour: int,
    market_end_hour: int,
    frequency_hours: int,
    max_cycles: int,
    max_forecast_hours: int,
    max_failed_attempts: int,
    allow_partial: bool,
) -> pd.DataFrame:
    actual_by_date = actuals_by_date_frame(actuals)
    cycle_cache: dict[tuple[str, datetime, date], HerbieCycleResult] = {}
    rows: list[dict[str, Any]] = []
    total_tasks = len(dates) * max(1, len(models)) * max(1, len(asof_times(dates[0], location.timezone_name, market_open_hour, market_end_hour, frequency_hours)))
    completed = 0
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
        for asof in asof_times(
            target_date,
            location.timezone_name,
            market_open_hour,
            market_end_hour,
            frequency_hours,
        ):
            asof_utc = asof.astimezone(UTC)
            for model_id in models:
                completed += 1
                if completed % 100 == 0:
                    LOGGER.info("Herbie progress: %s/%s model-asof rows", completed, total_tasks)
                model_spec = NOAA_HERBIE_MODEL_SPECS.get(model_id)
                if model_spec is None:
                    if allow_partial:
                        continue
                    raise ValueError(f"Unsupported Herbie model: {model_id}")
                cycles = recent_cycles_for_model(
                    model_id,
                    asof_utc,
                    model_spec.get("cycle_hours", "hourly"),
                    max_cycles=max_cycles,
                )
                selected: HerbieCycleResult | None = None
                attempted: list[HerbieCycleResult] = []
                for cycle in cycles:
                    key = (model_id, cycle, target_date)
                    if key not in cycle_cache:
                        cycle_cache[key] = herbie_fetch_cycle_result(
                            model_id=model_id,
                            cycle_utc=cycle,
                            target_date=target_date,
                            cache_dir=cache_dir,
                            max_forecast_hours=max_forecast_hours,
                            max_failed_attempts=max_failed_attempts,
                        )
                    result = cycle_cache[key]
                    attempted.append(result)
                    if result.successful:
                        selected = result
                        break
                error_message = None
                if selected is None:
                    error_message = "No successful cycle. " + " | ".join(
                        item.error_message or "unknown error" for item in attempted[:3]
                    )
                estimated_high = selected.future_high_f if selected and selected.successful else None
                error = (
                    round(float(estimated_high) - float(actual["actual_high_f"]), 3)
                    if estimated_high is not None
                    else None
                )
                rows.append(
                    {
                        "date": target_key,
                        "location": location.location,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "actual_station_id": actual["actual_station_id"],
                        "actual_high_f": actual["actual_high_f"],
                        "provider_family": "noaa_herbie",
                        "model_key": f"noaa_herbie:{model_id}",
                        "model": model_id,
                        "model_source": "direct_noaa_herbie",
                        "model_id_used": model_id,
                        "asof_pt": timestamp_to_pt_label(asof, location.timezone_name),
                        "asof_date_pt": asof.date().isoformat(),
                        "asof_hour_pt": asof.hour,
                        "asof_market_day": market_day_label(asof.date(), target_date),
                        "asof_utc": asof_utc.isoformat(),
                        "hours_since_market_open_asof": round(
                            (asof - window_start).total_seconds() / 3600,
                            2,
                        ),
                        "hours_before_target_end_asof": round(
                            (window_end - asof).total_seconds() / 3600,
                            2,
                        ),
                        "run_time_pt": timestamp_to_pt_label(selected.cycle_utc, location.timezone_name)
                        if selected
                        else None,
                        "estimate_available_pt": timestamp_to_pt_label(selected.cycle_utc, location.timezone_name)
                        if selected
                        else None,
                        "estimate_available_pt_iso": timestamp_to_pt_iso(selected.cycle_utc, location.timezone_name)
                        if selected
                        else None,
                        "run_utc": selected.cycle_utc.isoformat() if selected else None,
                        "cycle_utc": selected.cycle_utc.isoformat() if selected else None,
                        "estimate_available_utc": selected.cycle_utc.isoformat() if selected else None,
                        "estimated_high_f": round(float(estimated_high), 3)
                        if estimated_high is not None
                        else None,
                        "error_f": error,
                        "abs_error_f": abs(error) if error is not None else None,
                        "within_winning_bracket": bracket_hit(estimated_high, actual["actual_high_f"]),
                        "source_hour_count": selected.success_count if selected else 0,
                        "forecast_hours_used": ",".join(str(value) for value in selected.forecast_hours_used)
                        if selected
                        else None,
                        "forecast_first_hour_pt": None,
                        "forecast_last_hour_pt": None,
                        "successful": bool(selected and selected.successful),
                        "error_message": error_message,
                        "attempt_count": sum(item.attempt_count for item in attempted),
                        "successful_cycle_count": 1 if selected else 0,
                        "source_url": selected.source_url if selected else None,
                        "source_path": selected.source_path if selected else None,
                        "estimate_logic": "model_run_full_klax_climate_day",
                    }
                )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["date", "model_key", "hours_since_market_open_asof", "asof_pt"])


def metric_summary(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    scored = frame[frame["estimated_high_f"].notna()].copy()
    if scored.empty:
        return pd.DataFrame()
    scored["squared_error_f"] = scored["error_f"].astype(float) ** 2
    scored["within_0_5f"] = scored["abs_error_f"].astype(float) <= 0.5
    scored["within_1f"] = scored["abs_error_f"].astype(float) <= 1.0
    scored["within_2f"] = scored["abs_error_f"].astype(float) <= 2.0
    summary = (
        scored.groupby(group_columns, dropna=False)
        .agg(
            rows=("estimated_high_f", "count"),
            dates=("date", "nunique"),
            mean_error_f=("error_f", "mean"),
            mean_abs_error_f=("abs_error_f", "mean"),
            median_abs_error_f=("abs_error_f", "median"),
            rmse_f=("squared_error_f", lambda value: math.sqrt(float(value.mean()))),
            max_abs_error_f=("abs_error_f", "max"),
            within_0_5f_rate=("within_0_5f", "mean"),
            within_1f_rate=("within_1f", "mean"),
            within_2f_rate=("within_2f", "mean"),
            bracket_hit_rate=("within_winning_bracket", "mean"),
        )
        .reset_index()
        .sort_values(["mean_abs_error_f", "rmse_f"])
    )
    return summary


def side_by_side(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    index_cols = [
        "date",
        "asof_pt",
        "asof_hour_pt",
        "asof_market_day",
        "hours_since_market_open_asof",
        "actual_high_f",
    ]
    values = frame[index_cols + ["model_key", "estimated_high_f", "error_f"]].copy()
    estimate_pivot = values.pivot_table(
        index=index_cols,
        columns="model_key",
        values="estimated_high_f",
        aggfunc="last",
    )
    error_pivot = values.pivot_table(
        index=index_cols,
        columns="model_key",
        values="error_f",
        aggfunc="last",
    )
    estimate_pivot.columns = [f"{column}__estimated_high_f" for column in estimate_pivot.columns]
    error_pivot.columns = [f"{column}__error_f" for column in error_pivot.columns]
    output = pd.concat([estimate_pivot, error_pivot], axis=1).reset_index()
    return output.sort_values(["date", "hours_since_market_open_asof", "asof_pt"])


def write_outputs(
    *,
    out_dir: Path,
    open_meteo: pd.DataFrame,
    herbie: pd.DataFrame,
    combined: pd.DataFrame,
) -> dict[str, Path]:
    paths = {
        "open_meteo": out_dir / "klax_open_meteo_candidate_asof_history.csv",
        "herbie": out_dir / "klax_direct_herbie_candidate_asof_history.csv",
        "combined": out_dir / "klax_combined_candidate_asof_history.csv",
        "side_by_side": out_dir / "klax_candidate_side_by_side.csv",
        "scoreboard_model": out_dir / "klax_candidate_scoreboard_by_model.csv",
        "scoreboard_model_hour": out_dir / "klax_candidate_scoreboard_by_model_hour.csv",
    }
    open_meteo.to_csv(paths["open_meteo"], index=False)
    herbie.to_csv(paths["herbie"], index=False)
    combined.to_csv(paths["combined"], index=False)
    side_by_side(combined).to_csv(paths["side_by_side"], index=False)
    metric_summary(combined, ["provider_family", "model_key", "model_source"]).to_csv(
        paths["scoreboard_model"],
        index=False,
    )
    metric_summary(
        combined,
        ["provider_family", "model_key", "model_source", "asof_market_day", "asof_hour_pt"],
    ).to_csv(paths["scoreboard_model_hour"], index=False)
    return paths


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    location = resolve_location()
    end_date = parse_date_arg(args.end_date, default_yesterday_tz=location.timezone_name)
    start_date = parse_date_arg(args.start_date) if args.start_date else default_start_date(end_date)
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")
    if args.asof_frequency_hours < 1:
        raise ValueError("--asof-frequency-hours must be at least 1")

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    herbie_cache_dir = Path(args.herbie_cache_dir)
    open_meteo_models = [
        model for model in parse_model_list(args.open_meteo_models) if model not in DISALLOWED_MODELS
    ]
    herbie_models = parse_model_list(args.herbie_models)
    dates = target_dates(start_date, end_date)

    print(
        "\n".join(
            [
                "KLAX Open-Meteo + direct Herbie candidate backfill",
                "==================================================",
                f"Date range: {start_date} to {end_date} ({len(dates)} days)",
                f"As-of window: previous-day {args.market_open_hour}:00 PT through target-day {args.market_end_hour}:00 PT",
                f"As-of frequency: every {args.asof_frequency_hours} hour(s)",
                f"Open-Meteo models: {', '.join(open_meteo_models) if not args.skip_open_meteo else 'skipped'}",
                f"Direct Herbie models: {', '.join(herbie_models) if not args.skip_herbie else 'skipped'}",
                f"Raw dir: {raw_dir}",
                f"Output dir: {out_dir}",
                f"Herbie cache dir: {herbie_cache_dir}",
            ]
        )
    )
    if args.dry_run:
        return 0

    ensure_dirs(raw_dir, out_dir)
    session = build_session()
    actuals_raw = fetch_noaa_actuals(
        session=session,
        location=location,
        start_date=start_date,
        end_date=end_date,
        raw_dir=raw_dir,
        refresh=args.refresh_actuals,
    )
    actuals = actuals_to_frame(actuals_raw, location)

    open_meteo_asof = pd.DataFrame()
    if not args.skip_open_meteo and open_meteo_models:
        requests_to_fetch, _model_ids = build_run_requests(
            raw_dir=raw_dir,
            models=open_meteo_models,
            dates=dates,
            location_timezone=location.timezone_name,
            start_date=start_date,
            end_date=end_date,
            market_open_hour=args.market_open_hour,
            market_end_hour=args.market_end_hour,
        )
        LOGGER.info("Open-Meteo Single Runs requested: %s", len(requests_to_fetch))
        open_meteo_results = fetch_all_single_runs(
            requests_to_fetch,
            location=location,
            refresh=args.refresh_open_meteo,
            max_workers=args.max_workers,
        )
        failed_open_meteo = [result for result in open_meteo_results if not result.succeeded]
        if failed_open_meteo and not args.allow_partial:
            raise SystemExit(
                f"{len(failed_open_meteo)} Open-Meteo single-run downloads failed. "
                "Re-run with --allow-partial to aggregate successes."
            )
        open_meteo_run_history = build_open_meteo_run_history(
            results=open_meteo_results,
            actuals=actuals,
            dates=dates,
            location=location,
            market_open_hour=args.market_open_hour,
            market_end_hour=args.market_end_hour,
        )
        open_meteo_asof = build_open_meteo_asof_history(
            run_history=open_meteo_run_history,
            actuals=actuals,
            dates=dates,
            models=open_meteo_models,
            location=location,
            market_open_hour=args.market_open_hour,
            market_end_hour=args.market_end_hour,
            frequency_hours=args.asof_frequency_hours,
        )

    herbie_asof = pd.DataFrame()
    if not args.skip_herbie and herbie_models:
        herbie_asof = build_herbie_asof_history(
            actuals=actuals,
            dates=dates,
            models=herbie_models,
            location=location,
            cache_dir=herbie_cache_dir,
            market_open_hour=args.market_open_hour,
            market_end_hour=args.market_end_hour,
            frequency_hours=args.asof_frequency_hours,
            max_cycles=args.max_cycles,
            max_forecast_hours=args.max_forecast_hours,
            max_failed_attempts=args.max_failed_attempts_per_cycle,
            allow_partial=args.allow_partial,
        )

    combined = pd.concat([open_meteo_asof, herbie_asof], ignore_index=True, sort=False)
    if combined.empty:
        raise SystemExit("No candidate rows were produced.")
    paths = write_outputs(
        out_dir=out_dir,
        open_meteo=open_meteo_asof,
        herbie=herbie_asof,
        combined=combined,
    )

    scoreboard = metric_summary(combined, ["provider_family", "model_key", "model_source"])
    print("")
    print("Candidate scoreboard preview")
    print("============================")
    preview_cols = [
        "provider_family",
        "model_key",
        "rows",
        "dates",
        "mean_abs_error_f",
        "rmse_f",
        "bracket_hit_rate",
    ]
    if not scoreboard.empty:
        print(scoreboard[preview_cols].head(20).to_string(index=False))
    print("")
    print("Wrote outputs")
    print("=============")
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
