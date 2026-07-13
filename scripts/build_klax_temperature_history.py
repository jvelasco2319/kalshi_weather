"""Build a KLAX model-vs-actual daily high-temperature backtest dataset.

Usage:
    python scripts/build_klax_temperature_history.py ^
      --start-date 2024-01-01 ^
      --end-date 2026-07-08 ^
      --lead-days 1,2,3,4,5,6,7

The script intentionally downloads and caches every requested raw source before
building any merged outputs. By default it refuses to create processed CSVs when
any requested model feed fails; pass --allow-partial to aggregate successful
feeds while still recording failed feeds in skipped_models.csv.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kalshi_weather.config import load_settings  # noqa: E402
from kalshi_weather.model.lax_high_temp import (  # noqa: E402
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_STATION_ID,
    LAX_TIMEZONE,
)

LOGGER = logging.getLogger("klax_temperature_history")

NOAA_DAILY_SUMMARIES_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
OPEN_METEO_PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

LOCATION_NAME = "KLAX"
STATION_NAME = "LOS ANGELES INTERNATIONAL AIRPORT, CA US"
NOAA_STATION_ID = "USW00023174"
GHCND_STATION_ID = "GHCND:USW00023174"
ELEVATION_M = 29.7

DISALLOWED_MODELS = {"best_match", "current_weighted_blend"}
DEFAULT_MODEL_ORDER = [
    "gfs_seamless",
    "gfs013",
    "gfs_global",
    "nam",
    "nam_conus",
    "ecmwf_ifs",
    "aifs",
    "hrrr",
    "nbm",
    "gfs",
    "rap",
]
MODEL_ALIASES = {
    "gfs_seamless": ["gfs_seamless"],
    "gfs013": ["gfs013", "gfs_global"],
    "gfs_global": ["gfs_global", "gfs025"],
    "nam": ["nam", "ncep_nam_conus", "nam_conus"],
    "nam_conus": ["nam_conus", "ncep_nam_conus"],
    "hrrr": ["hrrr", "ncep_hrrr_conus"],
    "nbm": ["nbm", "ncep_nbm_conus"],
    "ecmwf_ifs": ["ecmwf_ifs", "ecmwf_ifs025"],
    "aifs": ["aifs", "ecmwf_aifs025"],
    "gfs": ["gfs", "gfs_global", "gfs_seamless"],
    "rap": ["rap", "ncep_rap_conus"],
}


@dataclass(frozen=True)
class LocationConfig:
    location: str
    station: str
    station_name: str
    noaa_station_id: str
    ghcnd_station_id: str
    latitude: float
    longitude: float
    timezone_name: str
    elevation_m: float


@dataclass(frozen=True)
class DownloadResult:
    model: str
    model_id_used: str | None
    raw_path: Path
    metadata_path: Path
    status: str
    error: str | None
    attempted_aliases: list[str]
    query_pattern: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


def build_session() -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "kalshi-weather-klax-history/0.1"})
    return session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--raw-dir", default="data/raw/klax_temperature_history")
    parser.add_argument("--out-dir", default="data/processed/klax_temperature_history")
    parser.add_argument("--lead-days", default="1,2,3,4,5,6,7")
    parser.add_argument("--models", help="Optional comma-separated model override")
    parser.add_argument("--refresh", action="store_true", help="Overwrite cached raw files")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Create processed CSVs even when one or more model feeds fail",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_date_arg(value: str | None, *, default_yesterday_tz: str | None = None) -> date:
    if value:
        return date.fromisoformat(value)
    if default_yesterday_tz is None:
        raise ValueError("date value is required")
    return datetime.now(ZoneInfo(default_yesterday_tz)).date() - timedelta(days=1)


def parse_lead_days(value: str) -> list[int]:
    days = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not days:
        raise ValueError("--lead-days must include at least one integer")
    invalid = [day for day in days if day < 1 or day > 7]
    if invalid:
        raise ValueError(f"lead days must be 1 through 7, got {invalid}")
    return sorted(dict.fromkeys(days))


def parse_model_override(value: str | None) -> list[str]:
    if value:
        models = [item.strip() for item in value.split(",") if item.strip()]
    else:
        models = list(DEFAULT_MODEL_ORDER)
    return [model for model in models if model not in DISALLOWED_MODELS]


def resolve_location() -> LocationConfig:
    settings = load_settings()
    station = settings.default_station or LAX_STATION_ID
    return LocationConfig(
        location=LOCATION_NAME,
        station=station,
        station_name=STATION_NAME,
        noaa_station_id=NOAA_STATION_ID,
        ghcnd_station_id=GHCND_STATION_ID,
        latitude=float(LAX_LATITUDE),
        longitude=float(LAX_LONGITUDE),
        timezone_name=LAX_TIMEZONE,
        elevation_m=ELEVATION_M,
    )


def ensure_dirs(raw_dir: Path, out_dir: Path) -> None:
    (raw_dir / "noaa_actuals").mkdir(parents=True, exist_ok=True)
    (raw_dir / "open_meteo").mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)


def date_range_label(start_date: date, end_date: date) -> str:
    return f"{start_date.isoformat()}_{end_date.isoformat()}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def requested_url(base_url: str, params: dict[str, Any]) -> str:
    return f"{base_url}?{urlencode(params, doseq=False)}"


def response_payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def fetch_noaa_actuals(
    session: requests.Session,
    location: LocationConfig,
    start_date: date,
    end_date: date,
    raw_dir: Path,
    refresh: bool,
) -> Path:
    label = date_range_label(start_date, end_date)
    raw_path = raw_dir / "noaa_actuals" / f"klax_daily_tmax_{label}.json"
    metadata_path = raw_dir / "noaa_actuals" / f"klax_daily_tmax_{label}_metadata.json"
    if raw_path.exists() and not refresh:
        LOGGER.info("Using cached NOAA actuals: %s", raw_path)
        return raw_path

    params = {
        "dataset": "daily-summaries",
        "stations": location.noaa_station_id,
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dataTypes": "TMAX",
        "units": "standard",
        "format": "json",
    }
    url = requested_url(NOAA_DAILY_SUMMARIES_URL, params)
    LOGGER.info("Downloading NOAA actuals: %s to %s", start_date, end_date)
    response = session.get(NOAA_DAILY_SUMMARIES_URL, params=params, timeout=60)
    payload = response_payload(response)
    metadata = {
        "source": "noaa_ncei_daily_summaries",
        "station_id": location.noaa_station_id,
        "requested_url": response.url or url,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "downloaded_at_utc": utc_now_iso(),
        "status_code": response.status_code,
        "status": "success" if response.ok else "error",
        "error": None if response.ok else str(payload)[:1000],
    }
    write_json(raw_path, payload)
    write_json(metadata_path, metadata)
    response.raise_for_status()
    return raw_path


def actuals_to_frame(raw_path: Path, location: LocationConfig) -> pd.DataFrame:
    payload = read_json(raw_path)
    if isinstance(payload, dict):
        rows = payload.get("data", [])
    else:
        rows = payload
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("DATE") or row.get("date")
        raw_tmax = row.get("TMAX") or row.get("tmax")
        if raw_date is None or raw_tmax in (None, ""):
            continue
        try:
            actual_high = float(raw_tmax)
        except (TypeError, ValueError):
            LOGGER.warning("Skipping unparseable TMAX row: %s", row)
            continue
        output.append(
            {
                "date": str(raw_date)[:10],
                "actual_high_f": actual_high,
                "actual_source": "NOAA/NCEI Daily Summaries TMAX",
                "actual_station_id": location.noaa_station_id,
                "actual_station_name": location.station_name,
            }
        )
    frame = pd.DataFrame(output)
    if frame.empty:
        raise RuntimeError(f"No NOAA TMAX rows parsed from {raw_path}")
    frame = frame.drop_duplicates(subset=["date"]).sort_values("date")
    return frame


def model_aliases(model: str) -> list[str]:
    return list(dict.fromkeys(MODEL_ALIASES.get(model, [model])))


def hourly_variables(lead_days: list[int]) -> list[str]:
    return [f"temperature_2m_previous_day{day}" for day in lead_days]


def fetch_model_raw(
    session: requests.Session,
    location: LocationConfig,
    model: str,
    lead_days: list[int],
    start_date: date,
    end_date: date,
    raw_dir: Path,
    refresh: bool,
) -> DownloadResult:
    model_dir = raw_dir / "open_meteo" / model
    label = date_range_label(start_date, end_date)
    raw_path = model_dir / f"{model}_{label}.json"
    metadata_path = model_dir / f"{model}_{label}_metadata.json"
    if raw_path.exists() and not refresh:
        metadata = read_json(metadata_path) if metadata_path.exists() else {}
        status = str(metadata.get("status") or "success")
        LOGGER.info("Using cached model raw for %s: %s", model, raw_path)
        return DownloadResult(
            model=model,
            model_id_used=metadata.get("actual_open_meteo_model_id_used") or model,
            raw_path=raw_path,
            metadata_path=metadata_path,
            status=status,
            error=metadata.get("error"),
            attempted_aliases=list(metadata.get("attempted_aliases") or [model]),
            query_pattern=metadata.get("query_pattern"),
        )

    aliases = model_aliases(model)
    errors: list[str] = []
    for alias in aliases:
        for pattern in ("start_end", "past_days"):
            params = open_meteo_params(
                location=location,
                model_id=alias,
                lead_days=lead_days,
                start_date=start_date,
                end_date=end_date,
                pattern=pattern,
            )
            response = session.get(OPEN_METEO_PREVIOUS_RUNS_URL, params=params, timeout=90)
            payload = response_payload(response)
            if is_open_meteo_success(response, payload, lead_days):
                write_json(raw_path, payload)
                metadata = model_metadata(
                    model=model,
                    model_id_used=alias,
                    location=location,
                    start_date=start_date,
                    end_date=end_date,
                    response=response,
                    requested_params=params,
                    attempted_aliases=aliases,
                    status="success",
                    query_pattern=pattern,
                    error=None,
                )
                write_json(metadata_path, metadata)
                LOGGER.info("Downloaded %s using alias %s via %s", model, alias, pattern)
                return DownloadResult(
                    model=model,
                    model_id_used=alias,
                    raw_path=raw_path,
                    metadata_path=metadata_path,
                    status="success",
                    error=None,
                    attempted_aliases=aliases,
                    query_pattern=pattern,
                )
            errors.append(open_meteo_error(alias, pattern, response, payload))

    error_text = " | ".join(errors)
    write_json(
        metadata_path,
        model_metadata(
            model=model,
            model_id_used=None,
            location=location,
            start_date=start_date,
            end_date=end_date,
            response=None,
            requested_params={},
            attempted_aliases=aliases,
            status="skipped",
            query_pattern=None,
            error=error_text,
        ),
    )
    LOGGER.warning("Skipping model %s after failed aliases: %s", model, aliases)
    return DownloadResult(
        model=model,
        model_id_used=None,
        raw_path=raw_path,
        metadata_path=metadata_path,
        status="skipped",
        error=error_text,
        attempted_aliases=aliases,
    )


def open_meteo_params(
    *,
    location: LocationConfig,
    model_id: str,
    lead_days: list[int],
    start_date: date,
    end_date: date,
    pattern: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone_name,
        "temperature_unit": "fahrenheit",
        "models": model_id,
        "hourly": ",".join(hourly_variables(lead_days)),
    }
    if pattern == "start_end":
        params["start_date"] = start_date.isoformat()
        params["end_date"] = end_date.isoformat()
    elif pattern == "past_days":
        local_today = datetime.now(ZoneInfo(location.timezone_name)).date()
        past_days = max(1, (local_today - start_date).days + 1)
        params["past_days"] = past_days
    else:
        raise ValueError(f"unknown query pattern: {pattern}")
    return params


def is_open_meteo_success(
    response: requests.Response,
    payload: Any,
    lead_days: list[int],
) -> bool:
    if not response.ok or not isinstance(payload, dict):
        return False
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or "time" not in hourly:
        return False
    return any(variable in hourly for variable in hourly_variables(lead_days))


def open_meteo_error(alias: str, pattern: str, response: requests.Response, payload: Any) -> str:
    reason = payload.get("reason") if isinstance(payload, dict) else None
    message = reason or str(payload)[:500]
    return f"{alias}/{pattern}: HTTP {response.status_code}: {message}"


def model_metadata(
    *,
    model: str,
    model_id_used: str | None,
    location: LocationConfig,
    start_date: date,
    end_date: date,
    response: requests.Response | None,
    requested_params: dict[str, Any],
    attempted_aliases: list[str],
    status: str,
    query_pattern: str | None,
    error: str | None,
) -> dict[str, Any]:
    requested_url_value = (
        response.url
        if response is not None
        else requested_url(OPEN_METEO_PREVIOUS_RUNS_URL, requested_params)
    )
    return {
        "canonical_model": model,
        "actual_open_meteo_model_id_used": model_id_used,
        "requested_url": requested_url_value,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone_name,
        "downloaded_at_utc": utc_now_iso(),
        "status_code": response.status_code if response is not None else None,
        "status": status,
        "query_pattern": query_pattern,
        "attempted_aliases": attempted_aliases,
        "error": error,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def model_raw_to_frame(
    result: DownloadResult,
    lead_days: list[int],
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    payload = read_json(result.raw_path)
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    frame = pd.DataFrame(hourly)
    if frame.empty or "time" not in frame.columns:
        return pd.DataFrame(
            columns=["date", "model", "model_id_used", "lead_day", "estimated_high_f", "source_hour_count"]
        )
    frame["date"] = pd.to_datetime(frame["time"], errors="coerce").dt.date.astype(str)
    start = start_date.isoformat()
    end = end_date.isoformat()
    frame = frame[(frame["date"] >= start) & (frame["date"] <= end)]

    rows: list[dict[str, Any]] = []
    for day in lead_days:
        variable = f"temperature_2m_previous_day{day}"
        if variable not in frame.columns:
            continue
        values = frame[["date", variable]].copy()
        values[variable] = pd.to_numeric(values[variable], errors="coerce")
        grouped = values.groupby("date")[variable]
        maxes = grouped.max()
        counts = grouped.count()
        for day_key, high in maxes.items():
            if pd.isna(high):
                continue
            rows.append(
                {
                    "date": day_key,
                    "model": result.model,
                    "model_source": "open_meteo",
                    "model_id_used": result.model_id_used,
                    "lead_day": day,
                    "estimated_high_f": round(float(high), 1),
                    "source_hour_count": int(counts.loc[day_key]),
                }
            )
    return pd.DataFrame(rows)


def build_long_frame(
    actuals: pd.DataFrame,
    model_frames: list[pd.DataFrame],
    location: LocationConfig,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if model_frames:
        estimates = pd.concat(model_frames, ignore_index=True)
    else:
        estimates = pd.DataFrame()
    if estimates.empty:
        raise RuntimeError("No model estimate rows were available for aggregation")

    estimate_dates = set(estimates["date"].dropna().astype(str))
    actual_dates = set(actuals["date"].dropna().astype(str))
    missing_actual_dates = sorted(estimate_dates - actual_dates)
    if missing_actual_dates:
        LOGGER.warning(
            "Dropping model estimates for %s date(s) without NOAA actual_high_f: %s",
            len(missing_actual_dates),
            ", ".join(missing_actual_dates[:20]),
        )

    long_frame = estimates.merge(
        actuals[["date", "actual_high_f", "actual_station_id"]],
        on="date",
        how="inner",
    )
    if long_frame.empty:
        raise RuntimeError("No model estimate rows had matching NOAA actual_high_f values")
    if long_frame["actual_high_f"].isna().any():
        missing = sorted(long_frame.loc[long_frame["actual_high_f"].isna(), "date"].unique())
        raise RuntimeError(f"Final rows missing actual_high_f for dates: {missing[:20]}")

    long_frame.insert(1, "location", location.location)
    long_frame.insert(2, "latitude", location.latitude)
    long_frame.insert(3, "longitude", location.longitude)
    long_frame["error_f"] = (
        long_frame["estimated_high_f"].astype(float) - long_frame["actual_high_f"].astype(float)
    ).round(1)
    long_frame["abs_error_f"] = long_frame["error_f"].abs().round(1)
    long_frame["downloaded_start_date"] = start_date.isoformat()
    long_frame["downloaded_end_date"] = end_date.isoformat()

    ordered = [
        "date",
        "location",
        "latitude",
        "longitude",
        "actual_station_id",
        "actual_high_f",
        "model",
        "model_source",
        "model_id_used",
        "lead_day",
        "estimated_high_f",
        "error_f",
        "abs_error_f",
        "source_hour_count",
        "downloaded_start_date",
        "downloaded_end_date",
    ]
    long_frame = long_frame[ordered].sort_values(["date", "model", "lead_day"])
    bad_models = set(long_frame["model"].dropna()) & DISALLOWED_MODELS
    if bad_models:
        raise RuntimeError(f"Final output includes disallowed models: {sorted(bad_models)}")
    return long_frame


def build_wide_frame(long_frame: pd.DataFrame, actuals: pd.DataFrame, location: LocationConfig) -> pd.DataFrame:
    base = actuals[["date", "actual_high_f", "actual_station_id"]].copy()
    base.insert(1, "location", location.location)
    base.insert(2, "latitude", location.latitude)
    base.insert(3, "longitude", location.longitude)
    base = base[["date", "location", "latitude", "longitude", "actual_station_id", "actual_high_f"]]
    working = long_frame.copy()
    working["prefix"] = working["model"].astype(str) + "_day" + working["lead_day"].astype(str)

    metric_frames = []
    for value_column, suffix in (
        ("estimated_high_f", "high_f"),
        ("error_f", "error_f"),
        ("abs_error_f", "abs_error_f"),
    ):
        pivot_source = working[["date", "prefix", value_column]].copy()
        pivot_source["column"] = pivot_source["prefix"] + f"_{suffix}"
        pivot = pivot_source.pivot_table(
            index="date",
            columns="column",
            values=value_column,
            aggfunc="first",
        )
        metric_frames.append(pivot)

    metrics = pd.concat(metric_frames, axis=1) if metric_frames else pd.DataFrame()
    metrics = metrics.reset_index()
    wide = base.sort_values("date").merge(metrics, on="date", how="left")

    base_columns = ["date", "location", "latitude", "longitude", "actual_station_id", "actual_high_f"]
    prefixes = (
        working[["model", "lead_day"]]
        .drop_duplicates()
        .sort_values(["model", "lead_day"])
        .assign(prefix=lambda frame: frame["model"].astype(str) + "_day" + frame["lead_day"].astype(str))
    )
    metric_columns = [
        f"{prefix}_{suffix}"
        for prefix in prefixes["prefix"]
        for suffix in ("high_f", "error_f", "abs_error_f")
        if f"{prefix}_{suffix}" in wide.columns
    ]
    return wide[base_columns + metric_columns]


def build_coverage_report(
    long_frame: pd.DataFrame,
    actuals: pd.DataFrame,
    results: list[DownloadResult],
    requested_models: list[str],
    lead_days: list[int],
) -> pd.DataFrame:
    total_dates = actuals["date"].nunique()
    rows: list[dict[str, Any]] = []
    id_by_model = {
        result.model: result.model_id_used for result in results if result.model_id_used is not None
    }
    for model in requested_models:
        for day in lead_days:
            subset = long_frame[
                (long_frame["model"] == model)
                & (long_frame["lead_day"].astype(int) == int(day))
            ]
            dates_with = int(subset["date"].nunique()) if not subset.empty else 0
            rows.append(
                {
                    "model": model,
                    "model_id_used": id_by_model.get(model),
                    "lead_day": day,
                    "first_date_available": subset["date"].min() if not subset.empty else None,
                    "last_date_available": subset["date"].max() if not subset.empty else None,
                    "dates_with_estimate": dates_with,
                    "dates_missing_estimate": int(total_dates - dates_with),
                    "mean_error_f": round(float(subset["error_f"].mean()), 3)
                    if not subset.empty
                    else None,
                    "mean_abs_error_f": round(float(subset["abs_error_f"].mean()), 3)
                    if not subset.empty
                    else None,
                }
            )
    return pd.DataFrame(rows)


def write_skipped_models(results: list[DownloadResult], out_dir: Path) -> Path:
    rows = [
        {
            "model": result.model,
            "reason": result.error,
            "attempted_aliases": ",".join(result.attempted_aliases),
            "timestamp": utc_now_iso(),
        }
        for result in results
        if not result.succeeded
    ]
    path = out_dir / "skipped_models.csv"
    pd.DataFrame(rows, columns=["model", "reason", "attempted_aliases", "timestamp"]).to_csv(
        path,
        index=False,
    )
    return path


def verify_successful_raw_caches(results: list[DownloadResult]) -> None:
    missing = [str(result.raw_path) for result in results if result.succeeded and not result.raw_path.exists()]
    if missing:
        raise RuntimeError(f"Successful model downloads missing raw cache files: {missing}")


def write_outputs(
    long_frame: pd.DataFrame,
    wide_frame: pd.DataFrame,
    coverage: pd.DataFrame,
    out_dir: Path,
) -> tuple[Path, Path, Path]:
    long_path = out_dir / "klax_model_vs_actual_highs_long.csv"
    wide_path = out_dir / "klax_model_vs_actual_highs_wide.csv"
    coverage_path = out_dir / "coverage_report.csv"
    long_frame.to_csv(long_path, index=False)
    wide_frame.to_csv(wide_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    return long_path, wide_path, coverage_path


def print_summary(
    *,
    start_date: date,
    end_date: date,
    actuals: pd.DataFrame,
    requested_models: list[str],
    results: list[DownloadResult],
    long_frame: pd.DataFrame | None,
    wide_frame: pd.DataFrame | None,
    long_path: Path | None,
    wide_path: Path | None,
    coverage_path: Path | None,
    skipped_path: Path,
) -> None:
    succeeded = [result.model for result in results if result.succeeded]
    skipped = [result.model for result in results if not result.succeeded]
    lines = [
        "",
        "KLAX temperature history summary",
        "================================",
        f"Date range: {start_date} to {end_date}",
        f"Actual rows downloaded: {len(actuals)}",
        f"Models requested: {', '.join(requested_models)}",
        f"Models succeeded: {', '.join(succeeded) if succeeded else '-'}",
        f"Models skipped/failed: {', '.join(skipped) if skipped else '-'}",
        f"Final long row count: {len(long_frame) if long_frame is not None else 0}",
        f"Final wide row count: {len(wide_frame) if wide_frame is not None else 0}",
        f"Long output: {long_path if long_path else '-'}",
        f"Wide output: {wide_path if wide_path else '-'}",
        f"Coverage report: {coverage_path if coverage_path else '-'}",
        f"Skipped models: {skipped_path}",
    ]
    print("\n".join(lines))


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)

    location = resolve_location()
    start_date = parse_date_arg(args.start_date)
    end_date = parse_date_arg(args.end_date, default_yesterday_tz=location.timezone_name)
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out_dir)
    ensure_dirs(raw_dir, out_dir)

    lead_days = parse_lead_days(args.lead_days)
    requested_models = parse_model_override(args.models)
    if not requested_models:
        raise ValueError("No models requested after excluding disallowed feeds")

    LOGGER.info(
        "Using location %s lat=%s lon=%s tz=%s station=%s",
        location.location,
        location.latitude,
        location.longitude,
        location.timezone_name,
        location.station,
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

    results = [
        fetch_model_raw(
            session=session,
            location=location,
            model=model,
            lead_days=lead_days,
            start_date=start_date,
            end_date=end_date,
            raw_dir=raw_dir,
            refresh=args.refresh,
        )
        for model in requested_models
    ]
    verify_successful_raw_caches(results)

    skipped_path = write_skipped_models(results, out_dir)
    failed = [result for result in results if not result.succeeded]
    if failed and not args.allow_partial:
        actuals = actuals_to_frame(actuals_raw, location)
        print_summary(
            start_date=start_date,
            end_date=end_date,
            actuals=actuals,
            requested_models=requested_models,
            results=results,
            long_frame=None,
            wide_frame=None,
            long_path=None,
            wide_path=None,
            coverage_path=None,
            skipped_path=skipped_path,
        )
        raise SystemExit(
            "One or more requested model downloads failed. "
            "No merged outputs were created. Re-run with --allow-partial to aggregate successes."
        )

    actuals = actuals_to_frame(actuals_raw, location)
    model_frames = [
        model_raw_to_frame(result, lead_days, start_date, end_date)
        for result in results
        if result.succeeded
    ]
    long_frame = build_long_frame(actuals, model_frames, location, start_date, end_date)
    wide_frame = build_wide_frame(long_frame, actuals, location)
    coverage = build_coverage_report(
        long_frame=long_frame,
        actuals=actuals,
        results=results,
        requested_models=requested_models,
        lead_days=lead_days,
    )
    long_path, wide_path, coverage_path = write_outputs(long_frame, wide_frame, coverage, out_dir)
    print_summary(
        start_date=start_date,
        end_date=end_date,
        actuals=actuals,
        requested_models=requested_models,
        results=results,
        long_frame=long_frame,
        wide_frame=wide_frame,
        long_path=long_path,
        wide_path=wide_path,
        coverage_path=coverage_path,
        skipped_path=skipped_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
