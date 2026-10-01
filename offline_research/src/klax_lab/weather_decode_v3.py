"""Strict offline decoding for cached V3 HRRR and GEFS GRIB fragments.

The acquisition lane stores one exact GRIB message per registered field plus a
hash-bearing sidecar and the NOAA index used to select its byte range.  This
module is the offline boundary: it revalidates that provenance, decodes exactly
one message, checks model/field/time/ensemble semantics, and extracts named
points without importing an HTTP client.

Decoded rows deliberately do not claim a proven historical publication time.
The archived object's HTTP ``Last-Modified`` value is retained as an
availability candidate, while ``as_of_validated`` remains false until the
dataset availability policy admits it.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from math import isclose, isfinite
from pathlib import Path
from typing import Any, Iterable

from .dataset import _write_parquet
from .domain import climate_day_bounds
from .provenance import sha256_file, write_json
from .weather_sources_v3 import (
    ArchiveObjectPlan,
    HistoricalWeatherPlan,
    _safe_cache_path,
    _verified_cache_file,
    build_revised_daily_plans,
    select_index_ranges,
)


UTC = timezone.utc
KLAX = ("KLAX", 33.93816, -118.3866)


@dataclass(frozen=True)
class SpatialPoint:
    """A registered extraction target in decimal degrees."""

    point_id: str
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if (not isinstance(self.point_id, str) or not self.point_id
                or not self.point_id.replace("_", "").isalnum()):
            raise ValueError("Spatial point identifier is invalid")
        if (type(self.latitude) not in (int, float) or not isfinite(self.latitude)
                or not -90 <= self.latitude <= 90):
            raise ValueError("Spatial point latitude is invalid")
        if (type(self.longitude) not in (int, float) or not isfinite(self.longitude)
                or not -180 <= self.longitude <= 180):
            raise ValueError("Spatial point longitude is invalid")


KLAX_POINT = SpatialPoint(*KLAX)


@dataclass(frozen=True)
class FieldDefinition:
    short_name: str
    level_type: str
    level: float
    raw_units: str
    normalized_units: str
    minimum: float
    maximum: float
    allow_missing: bool = False


FIELD_DEFINITIONS = {
    "temperature_2m": FieldDefinition(
        "2t", "heightAboveGround", 2.0, "K", "degF", 180.0, 340.0,
    ),
    "total_cloud_cover": FieldDefinition(
        "tcc", "atmosphere", 0.0, "%", "percent", 0.0, 100.0,
    ),
    "cloud_ceiling": FieldDefinition(
        "gh", "cloudCeiling", 0.0, "gpm", "ft", -500.0, 30_000.0, True,
    ),
    "wind_u_10m": FieldDefinition(
        "10u", "heightAboveGround", 10.0, "m s**-1", "m_s", -150.0, 150.0,
    ),
    "wind_v_10m": FieldDefinition(
        "10v", "heightAboveGround", 10.0, "m s**-1", "m_s", -150.0, 150.0,
    ),
    "mean_sea_level_pressure": FieldDefinition(
        "mslma", "meanSea", 0.0, "Pa", "hPa", 70_000.0, 110_000.0,
    ),
}


def _registered_partition(value: date) -> str:
    if date(2024, 1, 1) <= value <= date(2024, 12, 31):
        return "weather_training"
    if date(2025, 1, 5) <= value <= date(2025, 6, 30):
        return "selection"
    if value >= date(2025, 7, 1):
        raise ValueError("Protected-final weather data cannot be decoded")
    raise ValueError("Weather decode date is outside the registered V3 partitions")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Provenance timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _http_timestamp(value: Any, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty HTTP timestamp or null")
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not a valid HTTP timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _grib_timestamp(day: int, clock: int, label: str) -> datetime:
    try:
        return datetime.strptime(f"{day:08d}{clock:04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"GRIB {label} is invalid") from exc


def _get(eccodes, handle, key: str) -> Any:
    try:
        return eccodes.codes_get(handle, key)
    except Exception as exc:
        raise ValueError(f"GRIB message lacks required key {key}") from exc


def _message_metadata(eccodes, handle) -> dict[str, Any]:
    initialized = _grib_timestamp(
        int(_get(eccodes, handle, "dataDate")),
        int(_get(eccodes, handle, "dataTime")),
        "reference time",
    )
    valid = _grib_timestamp(
        int(_get(eccodes, handle, "validityDate")),
        int(_get(eccodes, handle, "validityTime")),
        "valid time",
    )
    return {
        "edition": int(_get(eccodes, handle, "edition")),
        "discipline": int(_get(eccodes, handle, "discipline")),
        "parameter_category": int(_get(eccodes, handle, "parameterCategory")),
        "parameter_number": int(_get(eccodes, handle, "parameterNumber")),
        "short_name": str(_get(eccodes, handle, "shortName")),
        "parameter_name": str(_get(eccodes, handle, "name")),
        "level_type": str(_get(eccodes, handle, "typeOfLevel")),
        "level": float(_get(eccodes, handle, "level")),
        "raw_units": str(_get(eccodes, handle, "units")),
        "step_type": str(_get(eccodes, handle, "stepType")),
        "step_units": str(_get(eccodes, handle, "stepUnits")),
        "start_step": int(_get(eccodes, handle, "startStep")),
        "end_step": int(_get(eccodes, handle, "endStep")),
        "forecast_reference_time_utc": initialized,
        "valid_time_utc": valid,
        "grid_type": str(_get(eccodes, handle, "gridType")),
        "number_of_points": int(_get(eccodes, handle, "numberOfPoints")),
        "centre": str(_get(eccodes, handle, "centre")),
        "sub_centre": int(_get(eccodes, handle, "subCentre")),
        "generating_process_identifier": int(_get(eccodes, handle, "generatingProcessIdentifier")),
        "generating_process_type": int(_get(eccodes, handle, "typeOfGeneratingProcess")),
        "product_template": int(_get(eccodes, handle, "productDefinitionTemplateNumber")),
        "packing_type": str(_get(eccodes, handle, "packingType")),
        "bitmap_present": bool(_get(eccodes, handle, "bitmapPresent")),
        "missing_value": float(_get(eccodes, handle, "missingValue")),
        "message_length": int(_get(eccodes, handle, "totalLength")),
    }


def _validate_message(metadata: dict[str, Any], item: ArchiveObjectPlan,
                      field_id: str, target_date: date, eccodes, handle) -> dict[str, Any]:
    definition = FIELD_DEFINITIONS.get(field_id)
    if definition is None:
        raise ValueError(f"No decoder is registered for field {field_id}")
    expected_init = item.initialized_at.astimezone(UTC)
    expected_valid = expected_init + timedelta(hours=item.lead_hours)
    if metadata["edition"] != 2 or metadata["discipline"] != 0:
        raise ValueError("Weather fragment is not a GRIB2 meteorological message")
    if (metadata["short_name"] != definition.short_name
            or metadata["level_type"] != definition.level_type
            or not isclose(metadata["level"], definition.level, abs_tol=1e-9)
            or metadata["raw_units"] != definition.raw_units):
        raise ValueError("Decoded field identity or units differ from the registered selector")
    if (metadata["step_type"] != "instant"
            or metadata["step_units"] not in {"1", "h"}
            or metadata["start_step"] != item.lead_hours
            or metadata["end_step"] != item.lead_hours):
        raise ValueError("Decoded forecast step differs from the registered instantaneous lead")
    if (metadata["forecast_reference_time_utc"] != expected_init
            or metadata["valid_time_utc"] != expected_valid):
        raise ValueError("Decoded reference or valid time differs from the source plan")
    climate_start, climate_end = climate_day_bounds(target_date)
    if not climate_start <= expected_valid < climate_end:
        raise ValueError("Decoded field is outside the registered fixed-PST climate day")
    if (metadata["number_of_points"] <= 0 or not metadata["grid_type"]
            or metadata["centre"] != "kwbc"):
        raise ValueError("Decoded GRIB grid or producing centre is invalid")
    if item.model == "hrrr":
        if (item.member_id is not None or metadata["product_template"] != 0
                or metadata["generating_process_type"] != 2):
            raise ValueError("HRRR fragment does not identify a deterministic forecast")
        return {"ensemble_statistic": None, "ensemble_size": None,
                "derived_forecast_code": None}
    if item.model != "gefs" or item.member_id not in {"avg", "spr"}:
        raise ValueError("Decoder accepts only registered HRRR or GEFS mean/spread fragments")
    if field_id != "temperature_2m" or metadata["product_template"] != 2:
        raise ValueError("GEFS summary fragment has an unsupported field or product template")
    derived = int(_get(eccodes, handle, "derivedForecast"))
    ensemble_size = int(_get(eccodes, handle, "numberOfForecastsInEnsemble"))
    expected_derived = 0 if item.member_id == "avg" else 2
    if (metadata["generating_process_type"] != 4 or derived != expected_derived
            or ensemble_size != 30):
        raise ValueError("GEFS mean/spread ensemble semantics differ from the registered product")
    return {
        "ensemble_statistic": "mean" if item.member_id == "avg" else "standard_deviation",
        "ensemble_size": ensemble_size,
        "derived_forecast_code": derived,
    }


def _normalized_value(field_id: str, member_id: str | None, value: float) -> tuple[float, str]:
    definition = FIELD_DEFINITIONS[field_id]
    minimum, maximum = ((0.0, 30.0) if field_id == "temperature_2m" and member_id == "spr"
                        else (definition.minimum, definition.maximum))
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"Decoded {field_id} value is missing or physically implausible")
    if field_id == "temperature_2m":
        if member_id == "spr":
            return value * 1.8, "delta_degF"
        return (value - 273.15) * 1.8 + 32.0, "degF"
    if field_id == "cloud_ceiling":
        return value * 3.2808398950131, "ft"
    if field_id == "mean_sea_level_pressure":
        return value / 100.0, "hPa"
    return value, definition.normalized_units


def _decode_fragment(path: Path, *, item: ArchiveObjectPlan, field_id: str,
                     target_date: date, points: tuple[SpatialPoint, ...]) -> tuple[dict, list[dict]]:
    import eccodes

    size = path.stat().st_size
    if size < 16:
        raise ValueError("GRIB fragment is too short")
    with path.open("rb") as stream:
        if stream.read(4) != b"GRIB":
            raise ValueError("GRIB fragment lacks its opening marker")
        stream.seek(-4, 2)
        if stream.read(4) != b"7777":
            raise ValueError("GRIB fragment lacks its closing marker")
        stream.seek(0)
        handle = eccodes.codes_grib_new_from_file(stream)
        if handle is None:
            raise ValueError("GRIB fragment contains no decodable message")
        try:
            metadata = _message_metadata(eccodes, handle)
            if metadata["message_length"] != size:
                raise ValueError("GRIB message length differs from the exact cached byte range")
            ensemble = _validate_message(metadata, item, field_id, target_date, eccodes, handle)
            definition = FIELD_DEFINITIONS[field_id]
            rows = []
            for point in points:
                nearest = eccodes.codes_grib_find_nearest(
                    handle, float(point.latitude), float(point.longitude) % 360.0, npoints=1,
                )[0]
                raw = float(nearest["value"])
                missing = definition.allow_missing and isclose(
                    raw, metadata["missing_value"], rel_tol=0.0, abs_tol=1e-9,
                )
                if missing:
                    normalized, normalized_units = None, definition.normalized_units
                else:
                    normalized, normalized_units = _normalized_value(field_id, item.member_id, raw)
                distance = float(nearest["distance"])
                grid_latitude = float(nearest["lat"])
                grid_longitude = (float(nearest["lon"]) + 180.0) % 360.0 - 180.0
                if (not all(isfinite(value) for value in (distance, grid_latitude, grid_longitude))
                        or distance > 100.0):
                    raise ValueError("Selected grid point is invalid or over 100 km from its target")
                rows.append({
                    "point_id": point.point_id,
                    "target_latitude": float(point.latitude),
                    "target_longitude": float(point.longitude),
                    "grid_latitude": grid_latitude,
                    "grid_longitude": grid_longitude,
                    "grid_distance_km": distance,
                    "grid_index": int(nearest["index"]),
                    "raw_value": None if missing else raw,
                    "raw_units": metadata["raw_units"],
                    "value": normalized,
                    "units": normalized_units,
                    "is_missing": missing,
                })
        finally:
            eccodes.codes_release(handle)
        extra = eccodes.codes_grib_new_from_file(stream)
        if extra is not None:
            eccodes.codes_release(extra)
            raise ValueError("Exact weather range contains multiple GRIB messages")
    metadata.update(ensemble)
    return metadata, rows


def decode_weather_plan(plan: HistoricalWeatherPlan, cache_root: Path,
                        *, points: Iterable[SpatialPoint] = (KLAX_POINT,)) -> list[dict]:
    """Decode one finite cached plan, refusing missing or inconsistent fragments."""
    cache_root = Path(cache_root).resolve()
    point_values = tuple(points)
    if (not point_values or len({point.point_id for point in point_values}) != len(point_values)
            or any(not isinstance(point, SpatialPoint) for point in point_values)):
        raise ValueError("Extraction points must be a nonempty, unique SpatialPoint sequence")
    rows: list[dict] = []
    for item in plan.objects:
        target_date = item.initialized_at.date()
        partition = _registered_partition(target_date)
        folder = _safe_cache_path(cache_root, item.cache_key)
        index_path = _safe_cache_path(folder, "source.idx")
        index_meta = _verified_cache_file(
            index_path, url=item.index_url, start=None, end=None,
            maximum_bytes=item.index_max_bytes, require_grib=False,
        )
        index_retrieved = datetime.fromisoformat(index_meta["retrieved_at_utc"])
        index_retrieved_iso = _utc_iso(index_retrieved)
        index_last_modified = _http_timestamp(index_meta["last_modified"], "Index Last-Modified")
        if (index_last_modified is not None
                and index_last_modified > index_retrieved.astimezone(UTC)):
            raise ValueError("Archived index Last-Modified time follows its retrieval time")
        try:
            index_text = index_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("NOAA index is not valid UTF-8") from exc
        ranges = select_index_ranges(index_text, item)
        selectors = {field.field_id: field for field in item.fields}
        for selected in ranges:
            field_path = _safe_cache_path(folder, selected.field_id + ".grib2")
            field_meta = _verified_cache_file(
                field_path, url=item.url, start=selected.start, end=selected.end,
                maximum_bytes=selectors[selected.field_id].maximum_bytes, require_grib=True,
            )
            decoded, point_rows = _decode_fragment(
                field_path, item=item, field_id=selected.field_id,
                target_date=target_date, points=point_values,
            )
            retrieved = datetime.fromisoformat(field_meta["retrieved_at_utc"])
            retrieved_iso = _utc_iso(retrieved)
            last_modified = _http_timestamp(field_meta["last_modified"], "Last-Modified")
            if last_modified is not None and last_modified > retrieved.astimezone(UTC):
                raise ValueError("Archived object Last-Modified time follows its retrieval time")
            if retrieved.astimezone(UTC) < decoded["forecast_reference_time_utc"]:
                raise ValueError("Weather fragment retrieval precedes its forecast reference time")
            if (last_modified is not None
                    and last_modified < decoded["forecast_reference_time_utc"]):
                raise ValueError("Archived object Last-Modified precedes its forecast reference time")
            common = {
                "schema_version": 1,
                "climate_date": target_date.isoformat(),
                "partition": partition,
                "provider": item.provider,
                "model": item.model,
                "source_id": item.source_id,
                "member_id": item.member_id,
                "field_id": selected.field_id,
                "forecast_reference_time_utc": _utc_iso(decoded["forecast_reference_time_utc"]),
                "nominal_issue_time_utc": _utc_iso(decoded["forecast_reference_time_utc"]),
                "issue_time_basis": "GRIB_dataDate_dataTime_nominal_model_cycle",
                "cycle_hour_utc": decoded["forecast_reference_time_utc"].hour,
                "lead_hours": item.lead_hours,
                "valid_time_utc": _utc_iso(decoded["valid_time_utc"]),
                "ensemble_statistic": decoded["ensemble_statistic"],
                "ensemble_size": decoded["ensemble_size"],
                "derived_forecast_code": decoded["derived_forecast_code"],
                "parameter_name": decoded["parameter_name"],
                "short_name": decoded["short_name"],
                "level_type": decoded["level_type"],
                "level": decoded["level"],
                "grid_type": decoded["grid_type"],
                "number_of_grid_points": decoded["number_of_points"],
                "centre": decoded["centre"],
                "sub_centre": decoded["sub_centre"],
                "generating_process_identifier": decoded["generating_process_identifier"],
                "generating_process_type": decoded["generating_process_type"],
                "product_template": decoded["product_template"],
                "packing_type": decoded["packing_type"],
                "bitmap_present": decoded["bitmap_present"],
                "extraction_method": "ecCodes_geodesic_nearest_grid_point_no_interpolation",
                "source_path": field_path.as_posix(),
                "source_url": item.url,
                "source_bytes": field_meta["bytes"],
                "source_sha256": field_meta["sha256"],
                "source_range_start": selected.start,
                "source_range_end": selected.end,
                "source_etag": field_meta["etag"],
                "source_retrieved_at_utc": retrieved_iso,
                "source_last_modified_at_utc": (
                    last_modified.isoformat() if last_modified is not None else None
                ),
                "information_available_at_utc": (
                    last_modified.isoformat() if last_modified is not None else None
                ),
                "information_availability_basis": (
                    "archived_HTTP_Last-Modified_unverified_as_original_publication_time"
                    if last_modified is not None else "unavailable"
                ),
                "historical_availability_proven": False,
                "as_of_validated": False,
                "index_path": index_path.as_posix(),
                "index_url": item.index_url,
                "index_sha256": index_meta["sha256"],
                "index_retrieved_at_utc": index_retrieved_iso,
                "index_last_modified_at_utc": (
                    index_last_modified.isoformat() if index_last_modified is not None else None
                ),
                "index_line": selected.line,
            }
            rows.extend({**common, **point_row} for point_row in point_rows)
    expected = sum(len(item.fields) for item in plan.objects) * len(point_values)
    if len(rows) != expected:
        raise ValueError("Decoded weather row count differs from the complete planned inventory")
    return rows


def _write_parquet_atomic(path: Path, rows: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        _write_parquet(temporary, rows)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def decode_revised_weather_day(project_root: Path, target_date: date,
                               *, today: date | None = None,
                               points: Iterable[SpatialPoint] = (KLAX_POINT,)) -> dict:
    """Decode one complete registered day and publish clearly partial evidence."""
    root = Path(project_root).resolve()
    partition = _registered_partition(target_date)
    hrrr_plan, gefs_plan = build_revised_daily_plans(target_date, today=today)
    cache_root = root / "data/raw/weather_v3/compatibility"
    point_values = tuple(points)
    hrrr_rows = decode_weather_plan(hrrr_plan, cache_root, points=point_values)
    gefs_rows = decode_weather_plan(gefs_plan, cache_root, points=point_values)
    output_dir = root / "data/normalized/v3_weather" / partition / f"date={target_date.isoformat()}"
    if "protected_final" in {part.casefold() for part in output_dir.parts}:
        raise ValueError("Protected-final output path is forbidden")
    hrrr_path = output_dir / "hrrr_points.parquet"
    gefs_path = output_dir / "gefs_summary_points.parquet"
    _write_parquet_atomic(hrrr_path, hrrr_rows)
    _write_parquet_atomic(gefs_path, gefs_rows)
    outputs = [
        {"model": "hrrr", "path": hrrr_path.relative_to(root).as_posix(),
         "rows": len(hrrr_rows), "bytes": hrrr_path.stat().st_size,
         "sha256": sha256_file(hrrr_path)},
        {"model": "gefs", "path": gefs_path.relative_to(root).as_posix(),
         "rows": len(gefs_rows), "bytes": gefs_path.stat().st_size,
         "sha256": sha256_file(gefs_path)},
    ]
    report = {
        "schema_version": 1,
        "component": "weather_decode_partial_day",
        "status": "PARTIAL_DAY_DECODE_COMPLETE_NOT_READINESS_EVIDENCE",
        "climate_date": target_date.isoformat(),
        "partition": partition,
        "protected_final_read": False,
        "network_used": False,
        "coverage_complete_for_requested_day": True,
        "registered_history_coverage_complete": False,
        "readiness_component_pass": False,
        "points": [
            {"point_id": point.point_id, "latitude": point.latitude,
             "longitude": point.longitude} for point in point_values
        ],
        "raw_fragments_decoded": len(hrrr_rows) + len(gefs_rows),
        "hrrr_rows": len(hrrr_rows),
        "gefs_rows": len(gefs_rows),
        "outputs": outputs,
        "as_of_status": "NOT_ADMITTED_HISTORICAL_PUBLICATION_TIME_UNPROVEN",
        "limitations": [
            "This artifact covers one registered day and cannot satisfy a V3 readiness component",
            "Archived HTTP Last-Modified is retained but is not proven as original publication time",
            "GEFS mean/spread does not preserve member-level skew or multimodality",
        ],
    }
    write_json(output_dir / "decode_manifest.json", report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    report = decode_revised_weather_day(args.root, args.date)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
