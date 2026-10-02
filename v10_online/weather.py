"""Bounded public weather inputs for the unchanged V10 probability chain.

The HTTP transport is injected. This module never opens a socket, reads an
outcome, writes an archive, substitutes a model family, or changes fitted state.
Late captures may produce an explicitly excluded preview; they cannot become a
prospective observation merely because their nominal model cycle was earlier.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import io
import json
from math import isclose, isfinite
from typing import Any, Callable, Mapping
from urllib.parse import urlencode

from klax_lab.weather_decode_v3 import (
    FIELD_DEFINITIONS, KLAX_POINT, _message_metadata, _normalized_value,
    _validate_message,
)
from klax_lab.weather_sources_v3 import (
    ArchiveObjectPlan, FieldSelector, GEFS_TEMPERATURE_FIELD_MAX_BYTES,
    HRRR_FIELD_MAX_BYTES, HRRR_FIELDS, INDEX_MAX_BYTES,
    REVISED_GEFS_LEADS, REVISED_HRRR_FIELDS, REVISED_HRRR_LEADS,
    gefs_url, hrrr_url, select_index_ranges,
)

UTC = timezone.utc
OBS_ENDPOINT = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OBS_FIELDS = (
    "tmpf", "dwpf", "drct", "sknt", "alti", "mslp", "vsby",
    "skyc1", "skyl1", "skyc2", "skyl2", "skyc3", "skyl3", "metar",
)
STATIONS = {"LAX": "KLAX", "DAG": "KDAG"}
OBS_MAX_BYTES = 1_000_000
Fetch = Callable[..., Mapping[str, Any]]


class WeatherInputError(ValueError):
    """A required input is unavailable, inconsistent, or outside its bound."""


def _utc(value: str | datetime, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise WeatherInputError(f"Invalid {label}") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeatherInputError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _identity(day: str | date, decision_at: str | datetime) -> tuple[date, datetime]:
    try:
        target = date.fromisoformat(day) if isinstance(day, str) else day
    except ValueError as exc:
        raise WeatherInputError("Invalid target date") from exc
    if not isinstance(target, date) or isinstance(target, datetime):
        raise WeatherInputError("Target must be a date")
    decision = _utc(decision_at, "decision time")
    if decision != datetime.combine(target, time(18), UTC):
        raise WeatherInputError("Unchanged V10 requires the target-date 18:00 UTC cutoff")
    return target, decision


def forecast_plan(day: str | date) -> tuple[ArchiveObjectPlan, ...]:
    """The exact inherited cycles and leads, rather than the newest run."""
    target = date.fromisoformat(day) if isinstance(day, str) else day
    if not isinstance(target, date) or isinstance(target, datetime):
        raise WeatherInputError("Target must be a date")
    objects = []
    h_init = datetime.combine(target, time(6), UTC)
    h_fields = tuple(FieldSelector(name, *HRRR_FIELDS[name], HRRR_FIELD_MAX_BYTES)
                     for name in REVISED_HRRR_FIELDS)
    for lead in REVISED_HRRR_LEADS:
        url = hrrr_url(h_init, lead)
        objects.append(ArchiveObjectPlan(
            f"hrrr-{target:%Y%m%d}-t06z-f{lead:02d}", "NOAA_NODD_AWS", "hrrr",
            h_init, lead, None, url, url + ".idx", h_fields,
        ))
    g_init = datetime.combine(target, time(0), UTC)
    for member, suffix in (("avg", "ens mean"), ("spr", "ens std dev")):
        fields = (FieldSelector("temperature_2m", "TMP", "2 m above ground",
                                GEFS_TEMPERATURE_FIELD_MAX_BYTES, (suffix,)),)
        for lead in REVISED_GEFS_LEADS:
            url = gefs_url(g_init, member, lead)
            objects.append(ArchiveObjectPlan(
                f"gefs-{target:%Y%m%d}-t00z-{member}-f{lead:03d}", "NOAA_NODD_AWS",
                "gefs", g_init, lead, member, url, url + ".idx", fields,
            ))
    return tuple(objects)


def _reply(fetch: Fetch, url: str, maximum: int, *, headers: dict[str, str] | None = None) -> dict:
    try:
        supplied = fetch(url, maximum_bytes=maximum, headers=headers)
    except Exception as exc:
        raise WeatherInputError(f"Required public input could not be captured: {url}") from exc
    if not isinstance(supplied, Mapping):
        raise WeatherInputError("Weather transport response must be a mapping")
    body = supplied.get("body")
    if not isinstance(body, bytes) or not body or len(body) > maximum:
        raise WeatherInputError("Weather response is empty or exceeds its byte limit")
    if supplied.get("sha256") != sha256(body).hexdigest():
        raise WeatherInputError("Weather response digest differs")
    received = _utc(supplied.get("retrieved_at_utc"), "capture completion time")
    response_headers = supplied.get("headers")
    if not isinstance(response_headers, Mapping):
        raise WeatherInputError("Weather response lacks source headers")
    normalized = {str(key).lower(): str(value) for key, value in response_headers.items()}
    if headers and "Range" in headers:
        expected = headers["Range"].removeprefix("bytes=")
        actual = normalized.get("content-range", "")
        if supplied.get("status_code") != 206 or not actual.startswith(f"bytes {expected}/"):
            raise WeatherInputError("Source did not honor the exact bounded byte range")
        start, end = (int(value) for value in expected.split("-"))
        if len(body) != end - start + 1:
            raise WeatherInputError("Source byte range is truncated")
    elif supplied.get("status_code") != 200:
        raise WeatherInputError("Required public input did not return HTTP 200")
    modified = None
    if normalized.get("last-modified"):
        try:
            modified = parsedate_to_datetime(normalized["last-modified"])
        except (ValueError, TypeError) as exc:
            raise WeatherInputError("Malformed source Last-Modified time") from exc
        modified = _utc(modified, "source modification time")
        if modified > received:
            raise WeatherInputError("Source modification time exceeds capture completion")
    return {**dict(supplied), "body": body, "headers": normalized,
            "received": received, "modified": modified}


def _decode_fragment(body: bytes, item: ArchiveObjectPlan, field_id: str, day: date) -> dict:
    """Use the same ecCodes identity and nearest-point checks as the old chain."""
    import eccodes

    if (len(body) < 16 or not body.startswith(b"GRIB") or not body.endswith(b"7777")
            or int.from_bytes(body[8:16], "big") != len(body)):
        raise WeatherInputError("Range must contain exactly one complete GRIB2 message")
    handle = eccodes.codes_new_from_message(body)
    if handle is None:
        raise WeatherInputError("GRIB2 message cannot be decoded")
    try:
        metadata = _message_metadata(eccodes, handle)
        ensemble = _validate_message(metadata, item, field_id, day, eccodes, handle)
        nearest = eccodes.codes_grib_find_nearest(
            handle, KLAX_POINT.latitude, KLAX_POINT.longitude % 360.0, npoints=1,
        )[0]
        raw = float(nearest["value"])
        definition = FIELD_DEFINITIONS[field_id]
        missing = definition.allow_missing and isclose(raw, metadata["missing_value"], rel_tol=0.0, abs_tol=1e-9)
        value, units = ((None, definition.normalized_units) if missing else
                        _normalized_value(field_id, item.member_id, raw))
        distance = float(nearest["distance"])
        lat = float(nearest["lat"])
        lon = (float(nearest["lon"]) + 180.0) % 360.0 - 180.0
        if not all(isfinite(number) for number in (distance, lat, lon)) or not 0 <= distance <= 100:
            raise WeatherInputError("Nearest grid point is invalid or more than 100 km from KLAX")
        return {
            "value": value, "units": units, "raw_value": None if missing else raw,
            "raw_units": metadata["raw_units"], "is_missing": missing,
            "point_id": "KLAX", "target_latitude": KLAX_POINT.latitude,
            "target_longitude": KLAX_POINT.longitude, "grid_latitude": lat,
            "grid_longitude": lon, "grid_distance_km": distance,
            "grid_index": int(nearest["index"]), "grid_type": metadata["grid_type"],
            "number_of_grid_points": metadata["number_of_points"],
            "extraction_method": "ecCodes_geodesic_nearest_grid_point_no_interpolation",
            "short_name": metadata["short_name"], "level_type": metadata["level_type"],
            "level": metadata["level"], "product_template": metadata["product_template"],
            **ensemble,
        }
    except WeatherInputError:
        raise
    except Exception as exc:
        raise WeatherInputError(f"Exact GRIB field identity/timing/extraction failed: {item.source_id}/{field_id}") from exc
    finally:
        eccodes.codes_release(handle)


def collect_forecasts(day: str | date, decision_at: str | datetime, fetch: Fetch) -> dict:
    target, decision = _identity(day, decision_at)
    rows, records = [], []
    for item in forecast_plan(target):
        index = _reply(fetch, item.index_url, item.index_max_bytes)
        try:
            selections = select_index_ranges(index["body"].decode("utf-8"), item)
        except (ValueError, UnicodeError) as exc:
            raise WeatherInputError(f"Required forecast index differs: {item.source_id}") from exc
        if index["received"] < item.initialized_at:
            raise WeatherInputError("Index receipt precedes model initialization")
        records.append({"url": item.index_url, "sha256": index["sha256"],
                        "bytes": len(index["body"]), "retrieved_at_utc": index["received"].isoformat()})
        field_limits = {field.field_id: field.maximum_bytes for field in item.fields}
        for selection in selections:
            length = selection.end - selection.start + 1
            if length <= 0 or length > field_limits[selection.field_id]:
                raise WeatherInputError("Forecast field exceeds its registered range limit")
            captured = _reply(fetch, item.url, length,
                              headers={"Range": f"bytes={selection.start}-{selection.end}"})
            if captured["received"] < item.initialized_at:
                raise WeatherInputError("Forecast capture precedes initialization")
            if captured["modified"] is not None and captured["modified"] < item.initialized_at:
                raise WeatherInputError("Forecast modification precedes initialization")
            available = max(item.initialized_at + timedelta(hours=6),
                            captured["modified"] or item.initialized_at)
            if available > decision:
                raise WeatherInputError("Required forecast was unavailable under the fixed six-hour bound")
            decoded = _decode_fragment(captured["body"], item, selection.field_id, target)
            rows.append({
                "schema_version": 1, "climate_date": target.isoformat(),
                "model": item.model, "provider": item.provider,
                "source_id": item.source_id, "member_id": item.member_id,
                "field_id": selection.field_id, "lead_hours": item.lead_hours,
                "cycle_hour_utc": item.initialized_at.hour,
                "forecast_reference_time_utc": item.initialized_at.isoformat(),
                "nominal_issue_time_utc": item.initialized_at.isoformat(),
                "valid_time_utc": (item.initialized_at + timedelta(hours=item.lead_hours)).isoformat(),
                "information_available_at_utc": available.isoformat(),
                "effective_information_available_at_utc": available.isoformat(),
                "information_availability_basis": "max(nominal_model_cycle_plus_6_hours,HTTP_Last-Modified)",
                "as_of_validated": True, "historical_availability_proven": False,
                "source_retrieved_at_utc": captured["received"].isoformat(),
                "source_last_modified_at_utc": None if captured["modified"] is None else captured["modified"].isoformat(),
                "source_url": item.url, "source_sha256": captured["sha256"],
                "source_bytes": len(captured["body"]), "source_range_start": selection.start,
                "source_range_end": selection.end, "index_url": item.index_url,
                "index_sha256": index["sha256"], "index_line": selection.line,
                "index_retrieved_at_utc": index["received"].isoformat(), **decoded,
            })
            records.append({"url": item.url, "sha256": captured["sha256"],
                            "bytes": len(captured["body"]), "range_start": selection.start,
                            "range_end": selection.end, "retrieved_at_utc": captured["received"].isoformat()})
    if len(rows) != 40 or len(records) != 60:
        raise WeatherInputError("Required fixed 40-row forecast inventory is incomplete")
    latest = max(_utc(record["retrieved_at_utc"], "capture completion") for record in records)
    return {
        "climate_date": target.isoformat(), "decision_at_utc": decision.isoformat(),
        "forecasts": rows, "forecast_sources": records,
        "forecast_capture_completed_at_utc": latest.isoformat(),
        "prospective_eligible": latest <= decision, "nominal_asof_validated": True,
        "forecast_request_count": len(records),
        "forecast_bytes": sum(record["bytes"] for record in records),
        "forecast_cycles_utc": {"hrrr": "06:00", "gefs": "00:00"},
        "limitations": [
            "Forecast availability retains the inherited six-hour conservative bound; original publication time is unproven.",
            "GEFS retains the native 30-member mean and standard deviation, rather than member trajectories.",
            *([] if latest <= decision else ["At least one source was captured after cutoff; this preview is excluded from prospective results."]),
        ],
    }


def observation_url(day: str | date) -> str:
    target = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(target, time(12), UTC)
    end = datetime.combine(target, time(18), UTC)
    # Current IEM identifiers are 3=routine, 4=special. The old 1/2 values
    # must not silently replace the original routine/special population.
    parameters = [*(('station', station) for station in STATIONS),
                  *(('data', field) for field in OBS_FIELDS),
                  ("sts", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("ets", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"),
                  ("latlon", "no"), ("elev", "no"), ("missing", "empty"),
                  ("trace", "empty"), ("direct", "no"),
                  ("report_type", "3"), ("report_type", "4")]
    return OBS_ENDPOINT + "?" + urlencode(parameters)


def _number(row: Mapping[str, str], name: str) -> float | None:
    text = (row.get(name) or "").strip()
    if not text or text.upper() in {"M", "NULL", "NAN"}:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise WeatherInputError(f"Invalid observation {name}") from exc
    if not isfinite(number):
        raise WeatherInputError(f"Nonfinite observation {name}")
    return number


def parse_observations(body: bytes, day: str | date, decision_at: str | datetime,
                       *, retrieved_at_utc: str | datetime) -> list[dict]:
    target, decision = _identity(day, decision_at)
    receipt = _utc(retrieved_at_utc, "observation capture completion")
    try:
        lines = [line for line in body.decode("utf-8-sig").splitlines() if line and not line.startswith("#")]
    except UnicodeError as exc:
        raise WeatherInputError("Observation response is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    if reader.fieldnames is None or not {"station", "valid", *OBS_FIELDS} <= set(reader.fieldnames):
        raise WeatherInputError("Observation source schema differs")
    best = {}
    start = datetime.combine(target, time(12), UTC)
    digest = sha256(body).hexdigest()
    for raw_row in reader:
        station = STATIONS.get(raw_row.get("station", ""))
        if station is None:
            raise WeatherInputError("Observation source returned an unrequested station")
        raw = (raw_row.get("metar") or "").strip()
        if raw.endswith(" MADISHF"):
            continue
        if raw.endswith(" IEM_GHCNH"):
            raw = raw.removesuffix(" IEM_GHCNH").rstrip()
        if not raw.startswith(("METAR ", "SPECI ", station + " ")):
            continue
        try:
            observed = datetime.strptime(raw_row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except ValueError as exc:
            raise WeatherInputError("Observation timestamp differs") from exc
        available = observed + timedelta(minutes=15)
        if observed < start or available > decision or observed > receipt:
            continue
        temperature = _number(raw_row, "tmpf")
        if temperature is None:
            continue
        if not -100 <= temperature <= 160:
            raise WeatherInputError("Observation temperature is implausible")
        pressure = _number(raw_row, "mslp")
        pressure_basis = "provider_mean_sea_level_pressure"
        if pressure is None:
            altimeter = _number(raw_row, "alti")
            pressure = None if altimeter is None else altimeter * 33.8638866667
            pressure_basis = "altimeter_times_33.8638866667" if pressure is not None else "missing"
        if pressure is not None and not 700 <= pressure <= 1100:
            raise WeatherInputError("Observation pressure is implausible")
        layers = []
        for index in (1, 2, 3):
            cover = (raw_row.get(f"skyc{index}") or "").strip().upper()
            height = _number(raw_row, f"skyl{index}")
            if cover:
                layers.append({"cover": cover, "height_ft": height})
        ceiling = [layer["height_ft"] for layer in layers
                   if layer["cover"] in {"BKN", "OVC", "VV"} and layer["height_ft"] is not None]
        record = {
            "station": station, "climate_date": target.isoformat(),
            "observed_at": observed.isoformat(), "available_at": available.isoformat(),
            "temperature_f": temperature, "dewpoint_f": _number(raw_row, "dwpf"),
            "wind_direction_degrees": _number(raw_row, "drct"),
            "wind_speed_kt": _number(raw_row, "sknt"), "pressure_hpa": pressure,
            "pressure_basis": pressure_basis, "cloud_ceiling_ft": min(ceiling) if ceiling else None,
            "broken_or_overcast": any(layer["cover"] in {"BKN", "OVC", "VV"} for layer in layers),
            "sky_layers_json": json.dumps(layers, sort_keys=True), "raw_metar": raw,
            "source_sha256": digest, "source_retrieved_at_utc": receipt.isoformat(),
            "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
            "as_of_validated": True,
        }
        completeness = sum(record[key] is not None for key in (
            "dewpoint_f", "wind_direction_degrees", "wind_speed_kt", "pressure_hpa", "cloud_ceiling_ft"))
        key = (station, observed)
        if key not in best or completeness > best[key][0]:
            best[key] = (completeness, record)
    return sorted((value[1] for value in best.values()), key=lambda row: (row["observed_at"], row["station"]))


def pressure_state(observations: list[dict]) -> dict:
    coast = sorted((row for row in observations if row["station"] == "KLAX"), key=lambda row: row["observed_at"])
    inland = sorted((row for row in observations if row["station"] == "KDAG"), key=lambda row: row["observed_at"])
    latest_coast = coast[-1] if coast else None
    valid_inland = [row for row in inland if row["pressure_hpa"] is not None]
    latest_inland = valid_inland[-1] if valid_inland else None
    gradient = None
    if latest_coast is not None and latest_coast["pressure_hpa"] is not None and latest_inland is not None:
        gradient = float(latest_coast["pressure_hpa"]) - float(latest_inland["pressure_hpa"])
    flow = "offshore" if gradient is not None and gradient <= -2 else "onshore" if gradient is not None and gradient >= 2 else "neutral"
    return {
        "pressure_and_flow": flow, "observed_pressure_gradient_hpa": gradient,
        "pressure_inputs_complete": gradient is not None,
        "klax_asof_report_count": len(coast), "kdag_asof_report_count": len(inland),
        "pressure_selected_observations": {"KLAX": latest_coast, "KDAG": latest_inland},
    }


def collect_observations(day: str | date, decision_at: str | datetime, fetch: Fetch) -> dict:
    target, decision = _identity(day, decision_at)
    url = observation_url(target)
    captured = _reply(fetch, url, OBS_MAX_BYTES)
    observations = parse_observations(captured["body"], target, decision,
                                      retrieved_at_utc=captured["received"])
    state = pressure_state(observations)
    timely = captured["received"] <= decision
    return {
        "climate_date": target.isoformat(), "decision_at_utc": decision.isoformat(),
        "observations": observations, **state,
        "observation_capture_completed_at_utc": captured["received"].isoformat(),
        "observation_source": {"url": url, "sha256": captured["sha256"],
                               "bytes": len(captured["body"]), "retrieved_at_utc": captured["received"].isoformat()},
        "prospective_eligible": timely,
        "observation_request_count": 1, "observation_bytes": len(captured["body"]),
        "limitations": [
            "IEM archives ingest asynchronously; the inherited observation-time-plus-15-minute availability bound remains a proxy.",
            "Altimeter fallback preserves V10 semantics and is identified per row; it is not measured sea-level pressure.",
            *([] if timely else ["Observation capture completed after cutoff; this preview is excluded from prospective results."]),
            *([] if state["pressure_inputs_complete"] else ["Original V10 neutral-posterior fallback applies; no complete observed gradient."]),
        ],
    }


def collect_weather(day: str | date, decision_at: str | datetime, fetch: Fetch) -> dict:
    forecasts = collect_forecasts(day, decision_at, fetch)
    observations = collect_observations(day, decision_at, fetch)
    return {
        **forecasts, **observations,
        "prospective_eligible": forecasts["prospective_eligible"] and observations["prospective_eligible"],
        "limitations": forecasts["limitations"] + observations["limitations"],
        "request_count": forecasts["forecast_request_count"] + observations["observation_request_count"],
        "bytes": forecasts["forecast_bytes"] + observations["observation_bytes"],
    }
