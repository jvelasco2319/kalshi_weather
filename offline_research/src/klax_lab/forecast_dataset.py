"""Build separate forecast partitions with auditable point-in-time assumptions."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from math import isfinite, isclose
from pathlib import Path
import re

from .dataset import _write_parquet, partition
from .domain import climate_day_bounds, require_as_of
from .grib_reader import extract_temperature, kelvin_to_fahrenheit, validate_metadata
from .provenance import sha256_file, write_json

EXPECTED_LEADS = [8, 9, 12, 15, 18, 21, 24, 27, 30, 31]
MODELS = ("gfs", "nbm")
AUDIT_STRIDE_DAYS = 30


class AvailabilityContradiction(ValueError):
    def __init__(self, audit: dict):
        super().__init__("Source object Last-Modified contradicts assumed historical availability")
        self.audit = audit


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Forecast timestamp lacks a UTC offset")
    return result.astimezone(timezone.utc)


def _raw_relative(model: str, initialized: datetime, lead: int) -> Path:
    return Path("data/raw/weather") / model / initialized.strftime("%Y%m%d") / "t00z" / f"f{lead:03d}.2m_temperature.grib2"


def _source_url(model: str, initialized: datetime, lead: int) -> str:
    # Deliberately offline; importing the acquisition module is forbidden here.
    stamp = initialized.strftime("%Y%m%d")
    if model == "gfs":
        return f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{stamp}/00/atmos/gfs.t00z.pgrb2.0p25.f{lead:03d}"
    if model == "nbm":
        return f"https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.{stamp}/00/core/blend.t00z.core.f{lead:03d}.co.grib2"
    raise ValueError("Unexpected weather model")


def validate_feature(row: dict, policy: dict) -> None:
    """Validate a feature's semantics; byte lineage is a separate mandatory gate."""
    day = date.fromisoformat(row["target_date"])
    start, end = climate_day_bounds(day)
    issued = _timestamp(row["initialization"])
    available = _timestamp(row["assumed_available_at"])
    cutoff = datetime(day.year, day.month, day.day, policy["decision_hour_utc"], tzinfo=timezone.utc)
    require_as_of(issued, available, cutoff, issued_at=issued)
    if policy["forecast_initialization_hour_utc"] != 0 or issued != datetime(day.year, day.month, day.day, 0, tzinfo=timezone.utc):
        raise ValueError("Unexpected model initialization")
    if policy["availability_delay_hours"] != 6 or available != issued + timedelta(hours=6):
        raise ValueError("Forecast availability must equal the registered initialization plus six hours")
    if row["model"] not in MODELS:
        raise ValueError("Unexpected weather model")
    if _timestamp(row["climate_day_start"]) != start or _timestamp(row["climate_day_end_exclusive"]) != end:
        raise ValueError("Feature climate-day boundaries disagree with policy")
    if row["planned_leads"] != EXPECTED_LEADS or row["sample_count"] != 10 or len(row["samples"]) != 10:
        raise ValueError("Incomplete sampled forecast")
    if row.get("historical_availability_proven") is not False:
        raise ValueError("Availability assumption changed without policy update")
    maximum = _finite(row["sampled_max_temperature_f"], "Sampled maximum")
    values, valid_times = [], []
    for sample in row["samples"]:
        lead = sample["end_step"]
        if isinstance(lead, bool) or not isinstance(lead, int) or lead not in EXPECTED_LEADS:
            raise ValueError("Unexpected forecast lead")
        validate_metadata(sample, issued, lead, day)
        valid = _timestamp(sample["valid_time"])
        if not start <= valid < end:
            raise ValueError("Sample outside fixed-PST climate day")
        for axis in ("latitude", "longitude"):
            measured = _finite(sample["station_" + axis], "Station coordinate")
            if abs(measured - policy["station"][axis]) > 1e-8:
                raise ValueError("Forecast sample uses the wrong station")
        distance = _finite(sample["grid_distance_km"], "Grid distance")
        if not 0 <= distance <= 100:
            raise ValueError("Grid distance outside supported station radius")
        latitude = _finite(sample["grid_latitude"], "Grid latitude")
        longitude = _finite(sample["grid_longitude"], "Grid longitude")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("Invalid grid coordinates")
        grid_index = sample["grid_index"]
        if isinstance(grid_index, bool) or not isinstance(grid_index, int) or not 0 <= grid_index < sample["number_of_points"]:
            raise ValueError("Invalid source grid index")
        temperature = _finite(sample["temperature_f"], "Fahrenheit temperature")
        converted = kelvin_to_fahrenheit(_finite(sample["temperature_k"], "Kelvin temperature"), sample["units"])
        if abs(temperature - converted) > 1e-8:
            raise ValueError("Temperature conversion is inconsistent")
        if Path(sample["source_path"]).as_posix() != _raw_relative(row["model"], issued, lead).as_posix():
            raise ValueError("Sample source identity disagrees with its model, initialization or lead")
        if not re.fullmatch(r"[0-9a-f]{64}", sample["source_sha256"]):
            raise ValueError("Invalid sample source hash")
        values.append(temperature)
        valid_times.append(valid)
    if sorted(valid_times) != [issued + timedelta(hours=i) for i in EXPECTED_LEADS]:
        raise ValueError("Forecast lead mismatch")
    if abs(max(values) - maximum) > 1e-8:
        raise ValueError("Sampled maximum mismatch")


def acquisition_records(root: Path) -> tuple[dict, list]:
    """Index immutable successful per-day manifests, without importing fetch code."""
    result = defaultdict(list)
    errors = []
    for path in sorted((root / "data/manifests/weather").glob("*.json")):
        try:
            report = _read_json(path)
            for model, record in report.get("models", {}).items():
                if model in MODELS and record.get("status") == "COMPLETE_SAMPLED_PROXY":
                    result[(report["target_date"], model)].append({"path": path, "sha256": sha256_file(path),
                                                                "report": report, "record": record})
        except (ValueError, KeyError, TypeError) as error:
            errors.append({"file": path.relative_to(root).as_posix(), "reason": str(error)})
    return result, errors


def _check_index(index_text: str, source: dict, initialized: datetime, lead: int) -> None:
    lines = index_text.splitlines()
    chosen = source["index_line"]
    if lines.count(chosen) != 1:
        raise ValueError("Selected source index line is missing or ambiguous")
    fields = chosen.split(":")
    if len(fields) < 6 or fields[2:6] != ["d=" + initialized.strftime("%Y%m%d%H"), "TMP", "2 m above ground", f"{lead} hour fcst"] or any(x.strip() for x in fields[6:]):
        raise ValueError("Selected source index describes the wrong field")
    offsets = [int(line.split(":")[1]) for line in lines]
    if offsets != sorted(offsets):
        raise ValueError("Source index offsets are unordered")
    begin = int(fields[1])
    later = [offset for offset in offsets if offset > begin]
    if not later or source["field"]["range_start"] != begin or source["field"]["range_end"] != min(later) - 1:
        raise ValueError("Raw field byte range disagrees with the source index")


def _check_source(root: Path, path: Path, metadata: dict, expected_url: str) -> None:
    if not path.resolve().is_relative_to((root / "data/raw/weather").resolve()):
        raise ValueError("Source file escapes the weather cache")
    if not path.is_file() or not path.with_suffix(path.suffix + ".json").is_file():
        raise ValueError("Raw source or provenance sidecar is missing")
    sidecar = _read_json(path.with_suffix(path.suffix + ".json"))
    if sidecar != metadata or metadata["url"] != expected_url:
        raise ValueError("Acquisition source identity disagrees with its raw sidecar")
    if path.stat().st_size != metadata["bytes"] or sha256_file(path) != metadata["sha256"]:
        raise ValueError("Raw source hash or byte count mismatch")


def audit_training_day(day: date, policy: dict) -> bool:
    first, last = (date.fromisoformat(x) for x in policy["weather_training"])
    return first <= day <= last and (day - first).days % AUDIT_STRIDE_DAYS == 0


def _compare_extraction(sample: dict, decoded: dict) -> None:
    # Runtime version and method-description strings may change without changing
    # the physical field. Compare every decoded semantic/physical value instead.
    for key, value in sample.items():
        if key in ("source_path", "extraction_method", "eccodes_version"):
            continue
        if key not in decoded:
            raise ValueError("Independent GRIB decode lacks a cached sample field")
        other = decoded[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not isinstance(other, (int, float)) or not isfinite(other) or not isclose(value, other, rel_tol=1e-10, abs_tol=1e-7):
                raise ValueError("Independent GRIB decode differs from the point cache")
        elif value != other:
            raise ValueError("Independent GRIB metadata differs from the point cache")


def _object_state(metadata: dict, kind: str, lead: int, available: datetime, decision: datetime) -> dict:
    raw_time = metadata.get("last_modified")
    modified = None
    if raw_time:
        modified = parsedate_to_datetime(raw_time)
        if modified.tzinfo is None or modified.utcoffset() is None:
            raise ValueError("Source Last-Modified lacks a UTC offset")
        modified = modified.astimezone(timezone.utc)
    return {"kind": kind, "lead": lead, "url": metadata["url"], "sha256": metadata["sha256"],
            "etag": metadata.get("etag"), "retrieved_at": metadata.get("retrieved_at"),
            "last_modified_original": raw_time,
            "last_modified_utc": modified.isoformat() if modified else None,
            "after_assumed_availability": modified > available if modified else None,
            "after_decision": modified > decision if modified else None,
            "evidence_role": "object_state_timestamp_not_public_availability_proof"}


def _availability_audit(states: list[dict]) -> dict:
    return {"source_objects": len(states),
            "last_modified_known": sum(r["last_modified_utc"] is not None for r in states),
            "last_modified_missing": sum(r["last_modified_utc"] is None for r in states),
            "last_modified_after_assumed": sum(r["after_assumed_availability"] is True for r in states),
            "last_modified_after_decision": sum(r["after_decision"] is True for r in states),
            "historical_availability_proven": False, "source_object_states": states}


def validate_lineage(root: Path, row: dict, records: dict, policy: dict, *, decoder=None) -> dict:
    """Bind feature -> acquisition manifest -> point cache -> raw/index bytes.

    Every source hash is checked. Independent re-decoding is intentionally a
    deterministic training-only sample, not a claim to re-decode the full archive.
    """
    candidates = records.get((row["target_date"], row["model"]), [])
    if not candidates:
        raise ValueError("No complete acquisition manifest exists for the model day")
    if any(candidate["record"].get("daily_feature") != row for candidate in candidates):
        raise ValueError("Feature differs from an immutable acquisition manifest")
    chosen = candidates[0]
    report, record = chosen["report"], chosen["record"]
    if record["samples"] != row["samples"] or report["planned_leads"] != EXPECTED_LEADS or report["requested_leads"] != EXPECTED_LEADS:
        raise ValueError("Acquisition manifest samples or requested leads disagree")
    if report["historical_availability_proven"] is not False or _timestamp(report["assumed_available_at"]) != _timestamp(row["assumed_available_at"]):
        raise ValueError("Acquisition availability assumption disagrees")
    sources = record["sources"]
    if len(sources) != len(EXPECTED_LEADS):
        raise ValueError("Incomplete acquisition source references")
    indexed = {source["field"]["url"]: source for source in sources}
    if len(indexed) != len(sources):
        raise ValueError("Duplicate acquisition source identity")
    initialized = _timestamp(row["initialization"])
    day = date.fromisoformat(row["target_date"])
    decode = decoder or extract_temperature
    should_decode = audit_training_day(day, policy)
    decoded_count = 0
    states, checked_samples = [], []
    available = _timestamp(row["assumed_available_at"])
    decision = datetime(day.year, day.month, day.day, policy["decision_hour_utc"], tzinfo=timezone.utc)
    for sample in row["samples"]:
        lead = sample["end_step"]
        raw = root / _raw_relative(row["model"], initialized, lead)
        index = raw.parent / f"f{lead:03d}.idx"
        point = raw.with_suffix(".point.json")
        url = _source_url(row["model"], initialized, lead)
        source = indexed.get(url)
        if source is None:
            raise ValueError("Expected model source absent from acquisition manifest")
        _check_source(root, raw, source["field"], url)
        _check_source(root, index, source["index"], url + ".idx")
        if source["index"]["range_start"] is not None or source["index"]["range_end"] is not None:
            raise ValueError("Source index must be preserved in full")
        if source["field"]["bytes"] != source["field"]["range_end"] - source["field"]["range_start"] + 1:
            raise ValueError("Selected GRIB size disagrees with its byte range")
        if source["field"]["full_source_bytes"] <= source["field"]["range_end"]:
            raise ValueError("Selected byte range exceeds the original GRIB object")
        if sample["source_sha256"] != source["field"]["sha256"]:
            raise ValueError("Sample source hash disagrees with acquisition provenance")
        if not point.is_file() or _read_json(point) != sample:
            raise ValueError("Feature sample differs from immutable point cache")
        _check_index(index.read_text(encoding="utf-8"), source, initialized, lead)
        states.extend(_object_state(source[kind], kind, lead, available, decision) for kind in ("field", "index"))
        checked_samples.append((sample, raw, lead))
    availability = _availability_audit(states)
    if availability["last_modified_after_assumed"]:
        raise AvailabilityContradiction(availability)
    if should_decode:
        for sample, raw, lead in checked_samples:
            decoded = decode(raw, expected_init=initialized, expected_lead=lead, target_day=day,
                             latitude=policy["station"]["latitude"], longitude=policy["station"]["longitude"])
            _compare_extraction(sample, decoded)
            decoded_count += 1
    return {"manifest_path": chosen["path"].relative_to(root).as_posix(), "manifest_sha256": chosen["sha256"],
            "raw_fields_hashed": len(row["samples"]), "raw_indices_hashed": len(row["samples"]),
            "independently_redecoded_fields": decoded_count,
            "source_availability_audit": availability,
            "audit_scope": "training_first_day_then_every_30th_day_all_planned_leads"}


def normalize_forecasts(root: Path, policy: dict) -> dict:
    root = Path(root).resolve()
    days = defaultdict(dict)
    errors = []
    records, manifest_errors = acquisition_records(root)
    lineage_audit = []
    source_state_records = []
    for path in sorted((root / "data/normalized/weather").glob("*_sampled_temperature.json")):
        try:
            row = _read_json(path)
            split = partition(row["target_date"], policy)
            if split == "excluded":
                continue
            validate_feature(row, policy)
            if path.name != f"{row['model']}_{row['target_date']}_sampled_temperature.json":
                raise ValueError("Feature filename disagrees with model/date identity")
            lineage = validate_lineage(root, row, records, policy)
        except AvailabilityContradiction as error:
            errors.append({"file": str(path.relative_to(root)), "reason": str(error),
                           "last_modified_after_assumed": error.audit["last_modified_after_assumed"],
                           "last_modified_after_decision": error.audit["last_modified_after_decision"]})
            source_state_records.append({"target_date": row["target_date"], "model": row["model"],
                                         "status": "quarantined_availability_contradiction", **error.audit})
            continue
        except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
            errors.append({"file": str(path.relative_to(root)), "reason": str(error)})
            continue
        model = row["model"]
        source_state_records.append({"target_date": row["target_date"], "model": model,
                                     "status": "no_timestamp_contradiction_not_proof", **lineage.pop("source_availability_audit")})
        if model in days[row["target_date"]]:
            raise ValueError("Duplicate model-day feature")
        days[row["target_date"]][model] = {"row": row, "sha256": sha256_file(path),
                                         "path": str(path.relative_to(root)), "lineage": lineage}
        # Only date, counts and source hashes are exposed; never label values.
        lineage_audit.append({"target_date": row["target_date"], "model": model, "partition": split, **lineage})
    combined = []
    incomplete = 0
    for day, features in sorted(days.items()):
        if set(features) != {"gfs", "nbm"}:
            incomplete += 1
            continue
        combined.append({"climate_date": day, "partition": partition(day, policy),
                         "gfs": features["gfs"]["row"]["sampled_max_temperature_f"],
                         "nbm": features["nbm"]["row"]["sampled_max_temperature_f"],
                         "available_at": max(_timestamp(features[m]["row"]["assumed_available_at"]) for m in features).isoformat(),
                         "gfs_feature_sha256": features["gfs"]["sha256"], "nbm_feature_sha256": features["nbm"]["sha256"],
                         "gfs_acquisition_manifest_sha256": features["gfs"]["lineage"]["manifest_sha256"],
                         "nbm_acquisition_manifest_sha256": features["nbm"]["lineage"]["manifest_sha256"],
                         "historical_availability_proven": False,
                         "feature_role": "sampled_temperature_proxy_not_continuous_daily_high"})
    counts = {}
    for split in ("weather_training", "selection", "protected_final"):
        rows = [r for r in combined if r["partition"] == split]
        counts[split] = len(rows)
        _write_parquet(root / "data/normalized" / split / "features/forecasts.parquet", rows)
    expected_counts = {split: (date.fromisoformat(policy[split][1]) - date.fromisoformat(policy[split][0])).days + 1 for split in counts}
    source_state_path = root / "data/manifests/forecast_source_state.json"
    write_json(source_state_path, {"historical_availability_proven": False,
                                  "interpretation": "S3 object state may contradict the assumption; timestamps do not prove original public availability",
                                  "model_days": source_state_records})
    report = {"complete_model_pairs_by_partition": counts, "incomplete_days": incomplete,
              "expected_model_pairs_by_partition": expected_counts,
              "coverage_fraction_by_partition": {s: counts[s] / expected_counts[s] for s in counts},
              "full_range_coverage": counts == expected_counts,
              "readiness_policy": "Coverage thresholds and terminal-acquisition requirement are enforced by the separate readiness gate",
              "invalid_feature_files": errors, "invalid_acquisition_manifests": manifest_errors,
              "source_availability_verified": False,
              "source_object_state_manifest": source_state_path.relative_to(root).as_posix(),
              "source_object_state_manifest_sha256": sha256_file(source_state_path),
              "last_modified_audit": {key: sum(row[key] for row in source_state_records) for key in (
                  "source_objects", "last_modified_known", "last_modified_missing", "last_modified_after_assumed", "last_modified_after_decision")},
              "quarantined_availability_model_days": sum(r["status"] == "quarantined_availability_contradiction" for r in source_state_records),
              "source_lineage_validated_model_days": len(lineage_audit),
              "raw_fields_hashed": sum(r["raw_fields_hashed"] for r in lineage_audit),
              "raw_indices_hashed": sum(r["raw_indices_hashed"] for r in lineage_audit),
              "independent_decode_audit": {
                  "scope": "training_first_day_then_every_30th_day_all_planned_leads_both_models",
                  "protected_data_redecoded": False,
                  "fields_redecoded": sum(r["independently_redecoded_fields"] for r in lineage_audit),
                  "training_model_days_redecoded": sum(r["independently_redecoded_fields"] > 0 for r in lineage_audit),
                  "limitation": "Full archive raw/index hashes are checked; extraction is independently repeated only for the stated deterministic training subset"},
              "lineage": lineage_audit,
              "status": "FULL_RANGE_FEATURES_READY" if counts == expected_counts and not errors and not manifest_errors else "PARTIAL_DOWNLOAD"}
    write_json(root / "data/manifests/forecast_normalization.json", report)
    return report
