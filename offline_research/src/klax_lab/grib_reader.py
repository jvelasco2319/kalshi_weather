"""Offline-only ecCodes extraction for archived deterministic 2-m temperature.

The external collector snapshot is not imported: its import path requires pygrib
and creates data directories. This adaptation uses its nearest-grid-point idea,
with ecCodes geodesic nearest-neighbour selection and explicit GRIB validation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path

from .domain import climate_day_bounds

KLAX_LATITUDE = 33.93816
KLAX_LONGITUDE = -118.3866
UTC = timezone.utc


def kelvin_to_fahrenheit(value: float, units: str) -> float:
    if units != "K":
        raise ValueError(f"expected GRIB temperature units K, received {units!r}")
    if not isfinite(value) or not 150 <= value <= 350:
        raise ValueError("missing or implausible 2-m temperature in Kelvin")
    return (value - 273.15) * 1.8 + 32


def _timestamp(day: int, clock: int) -> datetime:
    return datetime.strptime(f"{day:08d}{clock:04d}", "%Y%m%d%H%M").replace(tzinfo=UTC)


def validate_metadata(metadata: dict, expected_init: datetime, expected_lead: int, target_day: date) -> None:
    """Validate a decoded instantaneous field, including reference/valid times."""
    if expected_init.tzinfo is None or expected_init.utcoffset() is None:
        raise ValueError("expected initialization must have a timezone")
    if metadata["edition"] != 2:
        raise ValueError("only GRIB edition 2 is supported")
    if metadata["short_name"] not in ("2t", "t") or metadata["level_type"] != "heightAboveGround" or metadata["level"] != 2:
        raise ValueError("field is not a 2-m above-ground temperature")
    if metadata["step_type"] != "instant" or metadata["start_step"] != expected_lead or metadata["end_step"] != expected_lead:
        raise ValueError("expected an instantaneous field at exactly the requested lead")
    if metadata["step_units"] not in ("h", "1") or metadata["units"] != "K":
        raise ValueError("unsupported forecast step or temperature units")
    init = expected_init.astimezone(UTC)
    if datetime.fromisoformat(metadata["initialization"]) != init:
        raise ValueError("GRIB initialization does not match archived source plan")
    valid = datetime.fromisoformat(metadata["valid_time"])
    if valid != init + timedelta(hours=expected_lead):
        raise ValueError("GRIB validity does not match initialization plus lead")
    start, end = climate_day_bounds(target_day)
    if not start <= valid < end:
        raise ValueError("field valid time lies outside the fixed-PST climate day")
    if metadata["number_of_points"] <= 0 or not metadata["grid_type"]:
        raise ValueError("GRIB grid is missing")
    if metadata.get("product_template", 0) != 0:
        raise ValueError("pilot accepts plain deterministic forecast product template 0 only")


def extract_temperature(
    path: Path,
    *,
    expected_init: datetime,
    expected_lead: int,
    target_day: date,
    latitude: float = KLAX_LATITUDE,
    longitude: float = KLAX_LONGITUDE,
) -> dict:
    """Decode exactly one selected local GRIB message, with no fetching fallback."""
    import eccodes

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"offline GRIB input missing: {path}")
    with path.open("rb") as stream:
        handle = eccodes.codes_grib_new_from_file(stream)
        if handle is None:
            raise ValueError("file contains no GRIB message")
        try:
            get = lambda key: eccodes.codes_get(handle, key)
            initialization = _timestamp(int(get("dataDate")), int(get("dataTime")))
            valid = _timestamp(int(get("validityDate")), int(get("validityTime")))
            metadata = {
                "edition": int(get("edition")),
                "short_name": str(get("shortName")),
                "level_type": str(get("typeOfLevel")),
                "level": float(get("level")),
                "step_type": str(get("stepType")),
                "step_units": eccodes.codes_get_string(handle, "stepUnits"),
                "start_step": int(get("startStep")),
                "end_step": int(get("endStep")),
                "units": str(get("units")),
                "initialization": initialization.isoformat(),
                "valid_time": valid.isoformat(),
                "grid_type": str(get("gridType")),
                "number_of_points": int(get("numberOfPoints")),
                "centre": str(get("centre")),
                "sub_centre": int(get("subCentre")),
                "generating_process": int(get("generatingProcessIdentifier")),
                "product_template": int(get("productDefinitionTemplateNumber")),
            }
            validate_metadata(metadata, expected_init, expected_lead, target_day)
            nearest = eccodes.codes_grib_find_nearest(handle, latitude, longitude % 360, npoints=1)[0]
            temperature = kelvin_to_fahrenheit(float(nearest["value"]), metadata["units"])
            grid_lon = (float(nearest["lon"]) + 180) % 360 - 180
            if not isfinite(nearest["distance"]) or nearest["distance"] > 100:
                raise ValueError("nearest model grid point is over 100 km from KLAX")
            metadata.update({
                "temperature_f": temperature,
                "temperature_k": float(nearest["value"]),
                "station_latitude": latitude,
                "station_longitude": longitude,
                "grid_latitude": float(nearest["lat"]),
                "grid_longitude": grid_lon,
                "grid_distance_km": float(nearest["distance"]),
                "grid_index": int(nearest["index"]),
                "extraction_method": "ecCodes geodesic nearest grid point; no interpolation",
                "eccodes_version": eccodes.codes_get_api_version(),
                "source_path": str(path),
                "source_sha256": sha256(path.read_bytes()).hexdigest(),
            })
        finally:
            eccodes.codes_release(handle)
        extra = eccodes.codes_grib_new_from_file(stream)
        if extra is not None:
            eccodes.codes_release(extra)
            raise ValueError("selected range unexpectedly contains multiple GRIB messages")
    return metadata


def sampled_daily_feature(samples: list[dict], target_day: date, expected_leads: list[int]) -> dict:
    """A sampled maximum proxy, explicitly not a continuous daily maximum."""
    if not samples or sorted(item["end_step"] for item in samples) != sorted(expected_leads):
        raise ValueError("daily feature requires exactly the planned distinct forecast leads")
    if len(set(expected_leads)) != len(expected_leads):
        raise ValueError("duplicate forecast lead in daily plan")
    initializations = {item["initialization"] for item in samples}
    if len(initializations) != 1:
        raise ValueError("sampled daily feature cannot mix model initialization times")
    start, end = climate_day_bounds(target_day)
    for item in samples:
        if not start <= datetime.fromisoformat(item["valid_time"]) < end:
            raise ValueError("sample is outside the target climate day")
    return {
        "target_date": target_day.isoformat(),
        "climate_day_start": start.isoformat(),
        "climate_day_end_exclusive": end.isoformat(),
        "initialization": next(iter(initializations)),
        "sampled_max_temperature_f": max(item["temperature_f"] for item in samples),
        "feature_definition": "maximum of planned instantaneous 2-m temperature samples; not continuous daily high",
        "planned_leads": expected_leads,
        "sample_count": len(samples),
        "samples": samples,
    }
