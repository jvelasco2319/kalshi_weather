"""Leakage-safe feature extraction for frozen V3 weather rows.

The extractor accepts only the already joined, as-of feature row emitted by the
V3 dataset builder.  It never reads labels, files, clocks, or network sources.
Missing-value medians and constant-feature removal are fitted on caller-supplied
training rows and then frozen for later transforms.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import cos, isfinite, pi, sin, sqrt
from statistics import median
from typing import Any, Mapping, Sequence

from .regimes_v3 import WEATHER_REGIMES


STATIONS = ("KLAX", "KSMO", "KHHR", "KTOA", "KLGB")
LABEL_KEYS = {"reported_high_f", "yes_outcome", "settlement_label", "outcome"}

_SOURCE_MODELS = {
    "gfs_nbm": ("gfs", "nbm"),
    "gfs_nbm_hrrr": ("gfs", "nbm", "hrrr"),
    "gefs": ("gefs",),
    "gfs_nbm_hrrr_gefs": ("gfs", "nbm", "hrrr", "gefs"),
    "gefs_summary": ("gefs",),
    "hrrr_gefs_summary": ("hrrr", "gefs"),
    "gfs_nbm_hrrr_gefs_summary": ("gfs", "nbm", "hrrr", "gefs"),
}
_FEATURE_SETS = {"temperature_only", "intraday_station", "marine_layer", "full_local_weather"}
_OBSERVATION_FIELDS = (
    "temperature_f", "dewpoint_f", "wind_direction_degrees", "wind_speed_kt",
    "pressure_hpa", "cloud_ceiling_ft", "visibility_miles",
)


def _assert_no_labels(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in LABEL_KEYS:
                raise ValueError("Settlement labels cannot enter V3 feature extraction")
            _assert_no_labels(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_labels(child)


def _number(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be finite numeric or null")
    return float(value)


def _statistics(values: Sequence[float | None], prefix: str) -> dict[str, float | None]:
    present = [value for value in values if value is not None]
    return {
        prefix + ".mean": sum(present) / len(present) if present else None,
        prefix + ".min": min(present) if present else None,
        prefix + ".max": max(present) if present else None,
    }


def _field(records: Sequence[Mapping[str, Any]], model: str, field_id: str,
           *, member_id: str | None = None) -> list[float | None]:
    values = []
    for row in records:
        if row.get("model") != model or row.get("field_id") != field_id:
            continue
        if member_id is not None and row.get("member_id") != member_id:
            continue
        if row.get("as_of_validated") is not True:
            raise ValueError("Forecast rows must pass the registered as-of audit before feature use")
        missing = row.get("is_missing")
        value = _number(row.get("value"), f"{model}.{field_id}.value")
        if missing is True and value is not None:
            raise ValueError("Forecast missingness flag contradicts its value")
        if missing is False and value is None:
            raise ValueError("Forecast nonmissing flag lacks a value")
        values.append(value)
    return values


def _observation_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        station = row.get("station")
        if station not in STATIONS:
            continue
        if row.get("as_of_validated") is not True:
            raise ValueError("Observation rows must pass the registered as-of audit")
        if station in result:
            raise ValueError("Frozen feature row contains duplicate station snapshots")
        result[station] = row
    return result


def raw_feature_map(
    row: Mapping[str, Any], *, feature_set: str, forecast_source_set: str,
) -> dict[str, float | None]:
    """Extract a fixed named feature surface from one frozen as-of row."""
    if feature_set not in _FEATURE_SETS or forecast_source_set not in _SOURCE_MODELS:
        raise ValueError("Unsupported V3 feature or forecast source set")
    _assert_no_labels(row)
    if row.get("contains_settlement_label") is not False or row.get("as_of_join_validated") is not True:
        raise ValueError("V3 feature row is not a validated label-free as-of join")
    try:
        day = date.fromisoformat(str(row["climate_date"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("V3 feature row lacks a valid climate date") from exc
    angle = 2 * pi * (day.timetuple().tm_yday - 1) / (366 if day.year % 4 == 0 else 365)
    result: dict[str, float | None] = {
        "calendar.sin_day": sin(angle),
        "calendar.cos_day": cos(angle),
    }
    forecasts = row.get("forecasts")
    observations = row.get("observations")
    if not isinstance(forecasts, list) or not isinstance(observations, list):
        raise ValueError("V3 feature row lacks forecast or observation snapshots")

    for model in _SOURCE_MODELS[forecast_source_set]:
        if model == "gefs" and "summary" in forecast_source_set:
            result.update(_statistics(
                _field(forecasts, "gefs", "temperature_2m", member_id="avg"),
                "gefs.temperature_mean_f",
            ))
            spread = _field(forecasts, "gefs", "temperature_2m", member_id="spr")
            result.update(_statistics(spread, "gefs.temperature_spread_f"))
        else:
            values = _field(forecasts, model, "temperature_2m")
            result.update(_statistics(values, f"{model}.temperature_f"))

    observed = _observation_map(observations)
    if feature_set != "temperature_only":
        klax = observed.get("KLAX")
        if klax is None:
            raise ValueError("Observation-dependent feature set requires a KLAX snapshot")
        for name in ("temperature_f", "dewpoint_f", "wind_speed_kt", "pressure_hpa"):
            result[f"obs.KLAX.{name}"] = _number(klax.get(name), f"KLAX {name}")

    if feature_set in {"marine_layer", "full_local_weather"}:
        klax = observed["KLAX"]
        for name in ("cloud_ceiling_ft", "visibility_miles", "wind_direction_degrees"):
            result[f"obs.KLAX.{name}"] = _number(klax.get(name), f"KLAX {name}")
        for field_id, label in (
            ("total_cloud_cover", "cloud_percent"),
            ("cloud_ceiling", "ceiling_ft"),
            ("wind_u_10m", "wind_u_m_s"),
            ("wind_v_10m", "wind_v_m_s"),
            ("mean_sea_level_pressure", "pressure_hpa"),
        ):
            result.update(_statistics(
                _field(forecasts, "hrrr", field_id), f"hrrr.{label}",
            ))

    if feature_set == "full_local_weather":
        for station in STATIONS:
            station_row = observed.get(station)
            for name in _OBSERVATION_FIELDS:
                result[f"obs.{station}.{name}"] = (
                    _number(station_row.get(name), f"{station} {name}")
                    if station_row is not None else None
                )
        klax_pressure = result.get("obs.KLAX.pressure_hpa")
        for station in STATIONS[1:]:
            other = result.get(f"obs.{station}.pressure_hpa")
            result[f"gradient.KLAX_minus_{station}.pressure_hpa"] = (
                klax_pressure - other
                if klax_pressure is not None and other is not None else None
            )
    return result


@dataclass(frozen=True)
class FittedFeatureEncoder:
    feature_set: str
    forecast_source_set: str
    raw_feature_names: tuple[str, ...]
    medians: tuple[float, ...]
    feature_names: tuple[str, ...]
    dropped_constant_features: tuple[str, ...]
    training_rows: int

    def __post_init__(self) -> None:
        if len(self.raw_feature_names) != len(self.medians) or self.training_rows < 1:
            raise ValueError("Invalid V3 feature encoder metadata")
        if len(set(self.raw_feature_names)) != len(self.raw_feature_names):
            raise ValueError("V3 raw feature names must be unique")

    def transform(self, rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[float, ...], ...]:
        retained = set(self.feature_names)
        result = []
        for row in rows:
            raw = raw_feature_map(
                row, feature_set=self.feature_set,
                forecast_source_set=self.forecast_source_set,
            )
            if tuple(sorted(raw)) != self.raw_feature_names:
                raise ValueError("V3 feature surface changed after encoder fitting")
            expanded: dict[str, float] = {}
            for name, center in zip(self.raw_feature_names, self.medians):
                value = raw[name]
                expanded[name] = center if value is None else value
                expanded[name + "__missing"] = float(value is None)
            result.append(tuple(expanded[name] for name in self.feature_names if name in retained))
        return tuple(result)


def fit_feature_encoder(
    rows: Sequence[Mapping[str, Any]], *, feature_set: str,
    forecast_source_set: str, minimum_rows: int = 30,
) -> FittedFeatureEncoder:
    if len(rows) < minimum_rows:
        raise ValueError("V3 feature encoder has insufficient training rows")
    maps = [raw_feature_map(
        row, feature_set=feature_set, forecast_source_set=forecast_source_set,
    ) for row in rows]
    names = tuple(sorted(maps[0]))
    if any(tuple(sorted(item)) != names for item in maps):
        raise ValueError("V3 raw feature surfaces are inconsistent")
    medians = tuple(
        median(present) if (present := [item[name] for item in maps if item[name] is not None]) else 0.0
        for name in names
    )
    expanded_rows = []
    expanded_names = tuple(
        child for name in names for child in (name, name + "__missing")
    )
    for item in maps:
        expanded = {}
        for name, center in zip(names, medians):
            expanded[name] = center if item[name] is None else item[name]
            expanded[name + "__missing"] = float(item[name] is None)
        expanded_rows.append(expanded)
    retained, dropped = [], []
    for name in expanded_names:
        values = [item[name] for item in expanded_rows]
        if max(values) - min(values) <= 1e-12:
            dropped.append(name)
        else:
            retained.append(name)
    if len(retained) < 2:
        raise ValueError("V3 feature encoder retained fewer than two varying features")
    return FittedFeatureEncoder(
        feature_set, forecast_source_set, names, medians,
        tuple(retained), tuple(dropped), len(rows),
    )


def diagnostic_regime_seed(features: Mapping[str, float | None]) -> str:
    """Assign a transparent six-regime seed label from as-of features.

    This is a deterministic research label, not an observed meteorological
    truth.  A trained classifier may use it only when every regime has enough
    registered training support; otherwise the evaluator must use its pooled
    fallback.
    """
    ceiling = features.get("obs.KLAX.cloud_ceiling_ft")
    visibility = features.get("obs.KLAX.visibility_miles")
    wind_speed = features.get("obs.KLAX.wind_speed_kt") or 0.0
    wind_direction = features.get("obs.KLAX.wind_direction_degrees")
    pressure = features.get("obs.KLAX.pressure_hpa")
    temperature = features.get("obs.KLAX.temperature_f")
    dewpoint = features.get("obs.KLAX.dewpoint_f")
    cloud = features.get("hrrr.cloud_percent.mean")
    hrrr_high = features.get("hrrr.temperature_f.max")
    if pressure is not None and pressure < 1008 and wind_speed >= 12 and (cloud or 0) >= 60:
        return "frontal_or_precipitation"
    if wind_direction is not None and 20 <= wind_direction <= 120 and wind_speed >= 8:
        return "offshore_or_santa_ana_flow"
    if ceiling is not None and ceiling <= 1200 and (visibility is None or visibility <= 7):
        if (cloud or 100) >= 70:
            return "persistent_marine_layer"
        return "delayed_marine_layer_burnoff"
    if ((hrrr_high is not None and hrrr_high >= 85)
            or (temperature is not None and temperature >= 78)) and wind_speed <= 8:
        return "heat_or_weak_gradient"
    # A low cloud forecast without a current low ceiling represents a burnoff
    # transition rather than persistent observed stratus.
    if (cloud or 0) >= 55 and dewpoint is not None and temperature is not None and temperature - dewpoint <= 8:
        return "delayed_marine_layer_burnoff"
    result = "ordinary_sea_breeze"
    if result not in WEATHER_REGIMES:
        raise AssertionError("Unregistered diagnostic regime")
    return result
