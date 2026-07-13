from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from kalshi_weather.schemas import WeatherSnapshot
from kalshi_weather.time_utils import climate_day_utc, fixed_standard_time, local_wall_time, standard_market_date

LAX_STATION_ID = "KLAX"
LAX_LATITUDE = 33.93816
LAX_LONGITUDE = -118.3866
LAX_TIMEZONE = "America/Los_Angeles"
PACIFIC_STANDARD_UTC_OFFSET_HOURS = -8

OPEN_METEO_MODELS = ["gfs_seamless", "gfs013", "gfs_global", "best_match"]
OPEN_METEO_MODEL_WEIGHTS = {
    "gfs_seamless": 1.0,
    "gfs013": 0.75,
    "gfs_global": 0.75,
    "best_match": 1.0,
}
OPEN_METEO_PROBE_MODELS = [
    "best_match",
    "gfs_seamless",
    "gfs_global",
    "gfs_global025",
    "gfs_global016",
    "gfs025",
    "gfs013",
    "hrrr",
    "hrrr_conus",
    "nbm",
    "nbm_conus",
    "nam",
    "nam_conus",
    "graphcast",
    "graphcast025",
    "gfs_graphcast",
    "gfs_graphcast025",
    "aigfs",
    "aigfs025",
    "hgefs",
    "hgefs025",
]
OPEN_METEO_VARIABLES = [
    "temperature_2m",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "sunshine_duration",
    "apparent_temperature",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "relative_humidity_2m",
    "dew_point_2m",
]


def lax_climate_day_utc(market_date: date) -> tuple[datetime, datetime]:
    return climate_day_utc(market_date, PACIFIC_STANDARD_UTC_OFFSET_HOURS)


def current_lax_market_date(now_utc: datetime | None = None) -> date:
    now = now_utc or datetime.now(timezone.utc)
    return standard_market_date(now, PACIFIC_STANDARD_UTC_OFFSET_HOURS)


def latest_settled_lax_market_date(
    now_utc: datetime | None = None,
    settlement_buffer_hours: int = 4,
) -> date:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    candidate = current_lax_market_date(now)
    while True:
        _start_utc, end_utc = lax_climate_day_utc(candidate)
        if now >= end_utc + timedelta(hours=settlement_buffer_hours):
            return candidate
        candidate = candidate - timedelta(days=1)


def is_lax_market_date_settled(
    market_date: date,
    now_utc: datetime | None = None,
    settlement_buffer_hours: int = 4,
) -> bool:
    return market_date <= latest_settled_lax_market_date(now_utc, settlement_buffer_hours)


def remaining_lax_day_local(now_utc: datetime | None = None) -> tuple[datetime, datetime]:
    now = now_utc or datetime.now(timezone.utc)
    market_date = current_lax_market_date(now)
    _start_utc, end_utc = lax_climate_day_utc(market_date)
    return local_wall_time(now, LAX_TIMEZONE), end_utc.astimezone(ZoneInfo(LAX_TIMEZONE)).replace(tzinfo=None)


def lax_time_debug_payload(station: str = LAX_STATION_ID, now_utc: datetime | None = None) -> dict[str, object]:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    market_date = current_lax_market_date(now)
    start_utc, end_utc = lax_climate_day_utc(market_date)
    remaining_start, remaining_end = remaining_lax_day_local(now)
    la_zone = ZoneInfo(LAX_TIMEZONE)
    return {
        "station": station,
        "now_utc": now,
        "local_wall_time": now.astimezone(la_zone),
        "fixed_local_standard_time": fixed_standard_time(now, PACIFIC_STANDARD_UTC_OFFSET_HOURS),
        "market_date": market_date,
        "climate_day_start_utc": start_utc,
        "climate_day_end_utc": end_utc,
        "climate_day_start_local_wall": start_utc.astimezone(la_zone),
        "climate_day_end_local_wall": end_utc.astimezone(la_zone),
        "remaining_window_start_local": remaining_start,
        "remaining_window_end_local": remaining_end,
    }


def weather_snapshot_from_frames(
    station_id: str,
    observations: pd.DataFrame,
    model_maxes: dict[str, float],
    model_details: dict[str, object] | None = None,
) -> WeatherSnapshot:
    if observations.empty:
        observed_high = float("nan")
        latest = None
    else:
        observed_high = float(pd.to_numeric(observations["temp_f"], errors="coerce").max())
        latest_value = observations["timestamp_utc"].max()
        latest = latest_value.to_pydatetime() if hasattr(latest_value, "to_pydatetime") else latest_value

    details = dict(model_details or {})
    selected = details.get("selected_future_high_f") or details.get("future_max_selected")
    if selected is None:
        selected, components = weighted_future_high(model_maxes, OPEN_METEO_MODEL_WEIGHTS)
        if components:
            details.setdefault("selected_model_components", components)
            details.setdefault("weights_used", OPEN_METEO_MODEL_WEIGHTS)
    model_future_high = float(selected) if selected is not None else None
    return WeatherSnapshot(
        station_id=station_id,
        timestamp_utc=datetime.now(timezone.utc),
        observed_high_so_far_f=observed_high,
        latest_observation_utc=latest,
        observation_count=len(observations),
        model_future_high_f=model_future_high,
        model_details=details or model_maxes,
    )


def weighted_future_high(
    model_maxes: dict[str, float],
    weights: dict[str, float] | None = None,
) -> tuple[float | None, list[dict[str, float | str]]]:
    weights = weights or OPEN_METEO_MODEL_WEIGHTS
    components: list[dict[str, float | str]] = []
    weighted_sum = 0.0
    weight_sum = 0.0
    for column, value in model_maxes.items():
        model_id = _model_id_from_temperature_column(column)
        weight = float(weights.get(model_id, weights.get(column, 1.0)))
        if weight <= 0:
            continue
        value_f = float(value)
        components.append(
            {
                "column": column,
                "model_id": model_id,
                "future_high_f": value_f,
                "weight": weight,
            }
        )
        weighted_sum += value_f * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None, components
    return weighted_sum / weight_sum, components


def _model_id_from_temperature_column(column: str) -> str:
    if "__" not in column:
        return column
    return column.split("__", 1)[1]
