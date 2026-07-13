from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenMeteoForecastResult:
    """Open-Meteo forecast payload with per-model diagnostics."""

    frame: pd.DataFrame
    successful_models: list[str]
    failed_models: dict[str, str]
    fallback_used: bool
    model_maxes_f: dict[str, float]
    raw_columns: list[str]
    feature_summary: dict[str, float | None] = field(default_factory=dict)
    failed_variable_requests: dict[str, str] = field(default_factory=dict)


class OpenMeteoClient:
    def __init__(self, endpoint: str = "https://api.open-meteo.com/v1/gfs") -> None:
        self.endpoint = endpoint
        self.session = requests.Session()

    def forecast_hourly(
        self,
        latitude: float,
        longitude: float,
        models: list[str],
        variables: list[str],
        timezone_name: str = "America/Los_Angeles",
        forecast_days: int = 1,
    ) -> pd.DataFrame:
        """Return the best available hourly forecast frame.

        This remains for backwards compatibility. New Phase 2 code should call
        `forecast_hourly_by_model` so model diagnostics are not lost.
        """
        return self.forecast_hourly_by_model(
            latitude=latitude,
            longitude=longitude,
            models=models,
            variables=variables,
            timezone_name=timezone_name,
            forecast_days=forecast_days,
        ).frame

    def forecast_hourly_by_model(
        self,
        latitude: float,
        longitude: float,
        models: list[str],
        variables: list[str],
        timezone_name: str = "America/Los_Angeles",
        forecast_days: int = 1,
        asof_local: datetime | None = None,
        end_local: datetime | None = None,
        log_model_failures: bool = False,
    ) -> OpenMeteoForecastResult:
        """Fetch Open-Meteo data one model at a time, with a generic fallback."""
        frames: list[pd.DataFrame] = []
        successful_models: list[str] = []
        failed_models: dict[str, str] = {}
        failed_variable_requests: dict[str, str] = {}

        for model in models:
            try:
                frame, variable_error = self._forecast_hourly_frame_with_variable_fallback(
                    latitude=latitude,
                    longitude=longitude,
                    variables=variables,
                    timezone_name=timezone_name,
                    forecast_days=forecast_days,
                    model=None if model == "best_match" else model,
                )
                if variable_error:
                    failed_variable_requests[model] = variable_error
                if frame.empty:
                    failed_models[model] = "empty forecast response"
                    continue
                frames.append(_rename_model_columns(frame, model))
                successful_models.append(model)
            except Exception as exc:  # noqa: BLE001 - record provider failure per model
                failed_models[model] = str(exc)
                if log_model_failures:
                    LOGGER.warning("Open-Meteo model request failed model=%s error=%s", model, exc)

        fallback_used = False
        if frames:
            merged = _merge_hourly_frames(frames)
        else:
            fallback_used = True
            fallback_frame, variable_error = self._forecast_hourly_frame_with_variable_fallback(
                latitude=latitude,
                longitude=longitude,
                variables=variables,
                timezone_name=timezone_name,
                forecast_days=forecast_days,
                model=None,
            )
            if variable_error:
                failed_variable_requests["best_match"] = variable_error
            merged = _rename_model_columns(fallback_frame, "best_match")

        maxes = model_future_maxes_f(merged, asof_local=asof_local, end_local=end_local)
        feature_summary = future_feature_summary(merged, asof_local=asof_local, end_local=end_local)
        return OpenMeteoForecastResult(
            frame=merged,
            successful_models=successful_models,
            failed_models=failed_models,
            fallback_used=fallback_used,
            model_maxes_f=maxes,
            raw_columns=list(merged.columns),
            feature_summary=feature_summary,
            failed_variable_requests=failed_variable_requests,
        )

    def probe_models(
        self,
        latitude: float,
        longitude: float,
        candidate_models: list[str],
        timezone_name: str = "America/Los_Angeles",
        forecast_days: int = 1,
        asof_local: datetime | None = None,
        end_local: datetime | None = None,
    ) -> list[dict[str, object]]:
        """Probe candidate Open-Meteo model ids with a minimal temperature request."""
        results: list[dict[str, object]] = []
        for model in candidate_models:
            try:
                frame = self._forecast_hourly_frame(
                    latitude=latitude,
                    longitude=longitude,
                    variables=["temperature_2m"],
                    timezone_name=timezone_name,
                    forecast_days=forecast_days,
                    model=None if model == "best_match" else model,
                )
                renamed = _rename_model_columns(frame, model)
                maxes = model_future_maxes_f(renamed, asof_local=asof_local, end_local=end_local)
                results.append(
                    {
                        "model_id": model,
                        "success": True,
                        "response_columns": list(renamed.columns),
                        "future_max": next(iter(maxes.values()), None),
                        "error": None,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - probe reports provider errors by candidate
                results.append(
                    {
                        "model_id": model,
                        "success": False,
                        "response_columns": [],
                        "future_max": None,
                        "error": str(exc),
                    }
                )
        return sorted(results, key=lambda row: (not bool(row["success"]), str(row["model_id"])))

    def _forecast_hourly_frame_with_variable_fallback(
        self,
        latitude: float,
        longitude: float,
        variables: list[str],
        timezone_name: str,
        forecast_days: int,
        model: str | None,
    ) -> tuple[pd.DataFrame, str | None]:
        try:
            return (
                self._forecast_hourly_frame(
                    latitude=latitude,
                    longitude=longitude,
                    variables=variables,
                    timezone_name=timezone_name,
                    forecast_days=forecast_days,
                    model=model,
                ),
                None,
            )
        except Exception as exc:
            if variables == ["temperature_2m"] or "temperature_2m" not in variables:
                raise
            fallback = self._forecast_hourly_frame(
                latitude=latitude,
                longitude=longitude,
                variables=["temperature_2m"],
                timezone_name=timezone_name,
                forecast_days=forecast_days,
                model=model,
            )
            return fallback, str(exc)

    def _forecast_hourly_frame(
        self,
        latitude: float,
        longitude: float,
        variables: list[str],
        timezone_name: str,
        forecast_days: int,
        model: str | None,
    ) -> pd.DataFrame:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone_name,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "forecast_days": forecast_days,
            "hourly": ",".join(variables),
        }
        if model is not None:
            params["models"] = model
        response = self.session.get(self.endpoint, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        hourly = data.get("hourly", {})
        frame = pd.DataFrame(hourly)
        if "time" in frame.columns:
            frame["time"] = pd.to_datetime(frame["time"])
        return frame


def _rename_model_columns(frame: pd.DataFrame, model: str) -> pd.DataFrame:
    renamed = frame.copy()
    rename_map = {
        col: f"{col}__{model}"
        for col in renamed.columns
        if col != "time" and "__" not in col
    }
    return renamed.rename(columns=rename_map)


def _merge_hourly_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for frame in frames[1:]:
        if "time" in merged.columns and "time" in frame.columns:
            merged = merged.merge(frame, on="time", how="outer")
        else:
            merged = pd.concat([merged, frame], axis=1)
    return merged.sort_values("time") if "time" in merged.columns else merged


def temperature_columns(frame: pd.DataFrame) -> list[str]:
    return [c for c in frame.columns if c.startswith("temperature_2m")]


def model_future_maxes_f(
    frame: pd.DataFrame,
    asof_local: datetime | None = None,
    end_local: datetime | None = None,
) -> dict[str, float]:
    if "time" in frame.columns and (asof_local is not None or end_local is not None):
        times = pd.to_datetime(frame["time"])
        mask = pd.Series(True, index=frame.index)
        if asof_local is not None:
            mask &= times >= pd.Timestamp(asof_local).tz_localize(None)
        if end_local is not None:
            mask &= times < pd.Timestamp(end_local).tz_localize(None)
        frame = frame.loc[mask]

    result: dict[str, float] = {}
    for col in temperature_columns(frame):
        series = pd.to_numeric(frame[col], errors="coerce").dropna()
        if not series.empty:
            result[col] = float(series.max())
    return result


def future_feature_summary(
    frame: pd.DataFrame,
    asof_local: datetime | None = None,
    end_local: datetime | None = None,
) -> dict[str, float | None]:
    if "time" in frame.columns and (asof_local is not None or end_local is not None):
        times = pd.to_datetime(frame["time"])
        mask = pd.Series(True, index=frame.index)
        if asof_local is not None:
            mask &= times >= pd.Timestamp(asof_local).tz_localize(None)
        if end_local is not None:
            mask &= times < pd.Timestamp(end_local).tz_localize(None)
        frame = frame.loc[mask]

    summary: dict[str, float | None] = {}
    for base in [
        "cloud_cover_low",
        "shortwave_radiation",
        "direct_radiation",
        "wind_speed_10m",
        "wind_gusts_10m",
        "apparent_temperature",
    ]:
        cols = [col for col in frame.columns if col.startswith(f"{base}__")]
        values = pd.concat([pd.to_numeric(frame[col], errors="coerce") for col in cols], ignore_index=True) if cols else pd.Series(dtype=float)
        values = values.dropna()
        summary[f"{base}_max"] = float(values.max()) if not values.empty else None
        summary[f"{base}_mean"] = float(values.mean()) if not values.empty else None

    wind_dirs = [
        pd.to_numeric(frame[col], errors="coerce")
        for col in frame.columns
        if col.startswith("wind_direction_10m__")
    ]
    if wind_dirs:
        values = pd.concat(wind_dirs, ignore_index=True).dropna()
        summary["wind_direction_10m_mean"] = float(values.mean()) if not values.empty else None
    else:
        summary["wind_direction_10m_mean"] = None
    return summary


OPEN_METEO_MODEL_CANDIDATES = [
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
