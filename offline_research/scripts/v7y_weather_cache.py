"""Cache-only V7Y HRRR/GEFS normalization at the fixed 18:00 UTC decision.

The V3/V5 weather policy remains frozen and unchanged.  This module creates a
separate V7Y feature tree.  It reuses fully verified V5P normalized rows when
they exist and otherwise decodes only the already verified local raw cache.
It has no acquisition client or outcome-reading path.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import pandas as pd

from klax_lab.provenance import sha256_file, write_json
from klax_lab.weather_decode_v3 import _write_parquet_atomic
from klax_lab.weather_sources_v3 import verify_cache_only
from v5.acquire_probability_evidence import build_v5_daily_weather_plans
from v5.probability_cache_readiness import (
    PARTITION as LEGACY_PARTITION,
    POLICY_ID as LEGACY_POLICY_ID,
    VERSION as LEGACY_VERSION,
    _coverage as legacy_coverage,
    _decode_plan,
)


OUTPUT_ROOT = Path("data/normalized/v7y_weather_features")
POLICY_CONFIG_PATH = Path("configs/v7y_hrrr_gefs_availability_policy.json")
POLICY_ID = "v7y_max_nominal_plus_6h_archived_last_modified_18z_v1"
PARTITION = "v7y_calendar_features"

# These seals were written by register_v7y_availability_repair.py before the
# repair implementation or any V7Y study output existed.  A self-consistent
# replacement JSON is therefore insufficient: it must retain these registered
# identities as well.
REGISTERED_POLICY_SHA256 = "6d052ac852014edfe6f957441d2cfa5e80ad803eb5779bac9b5604c953bb6f86"
REGISTERED_PRESTATE_SHA256 = "612ab85dac97b1acbf44c8966f2718ba5b5e4b1b28521f022462dfaab548feba"
REPAIR_PRESTATE_PATH = Path("runs/v7y_weather_backfill/repair-prestate.json")
PRESERVED_SHARED_PATHS = (
    "src/klax_lab/weather_decode_v3.py",
    "src/klax_lab/weather_normalize_v3.py",
    "src/klax_lab/weather_sources_v3.py",
    "v5/acquire_probability_evidence.py",
    "v5/probability_cache_readiness.py",
)

VERSION = "klax-v7y-weather-cache-v1"
LEGACY_OUTPUT_ROOT = Path("data/normalized/v5p_probability_features")
RAW_CACHE_ROOT = Path("data/raw/v5p/weather")
START = date(2025, 7, 1)
END = date(2025, 12, 31)
AVAILABILITY_DELAY = timedelta(hours=6)
DECISION_HOURS_UTC = (12, 15, 18)
EFFECTIVE_BASIS = (
    "max_of_nominal_model_cycle_plus_6_hours_and_archived_HTTP_Last-Modified_if_present"
)
VALIDATION_BASIS = "frozen_conservative_bound_not_publication_proof"


class V7YWeatherCacheError(ValueError):
    """A cached input, policy registration, or normalized binding is invalid."""


def _canonical_hash(value: Mapping[str, Any], field: str | None = None) -> str:
    body = dict(value)
    if field is not None:
        body.pop(field, None)
    payload = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V7YWeatherCacheError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V7YWeatherCacheError(f"JSON object required: {path}")
    return value


def _safe_relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise V7YWeatherCacheError("V7Y weather path escapes the project root") from exc
    forbidden = {"labels", "outcomes", "protected_final", "settlement_targets"}
    if forbidden & {part.casefold().replace("-", "_") for part in relative.parts}:
        raise V7YWeatherCacheError("V7Y weather cache cannot access protected outcomes")
    return relative.as_posix()


def _target_date(value: Any) -> date:
    try:
        target = value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise V7YWeatherCacheError("invalid V7Y climate date") from exc
    if not START <= target <= END:
        raise V7YWeatherCacheError("date is outside the V7Y weather window")
    return target


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise V7YWeatherCacheError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V7YWeatherCacheError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V7YWeatherCacheError(f"{field} must include an offset")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V7YWeatherCacheError("availability timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _string_list(value: Any, field: str) -> list[str]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise V7YWeatherCacheError(f"{field} must be a string list")
    return list(value)


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise V7YWeatherCacheError(f"invalid {field}")
    return value


def _availability(row: Mapping[str, Any]) -> tuple[datetime, list[str]]:
    nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
    reference = _parse_utc(row.get("forecast_reference_time_utc"), "reference time")
    if nominal != reference:
        raise V7YWeatherCacheError("nominal and decoded model cycles differ")
    archived = row.get("source_last_modified_at_utc")
    archived_time = _parse_utc(archived, "Last-Modified") if archived else None
    available = max(value for value in (nominal + AVAILABILITY_DELAY, archived_time) if value)
    target = _target_date(row.get("climate_date"))
    eligible = [
        f"{hour:02d}:00"
        for hour in DECISION_HOURS_UTC
        if available <= datetime.combine(target, time(hour), UTC)
    ]
    return available, eligible


def _policy_binding(registration: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": registration["path"],
        "bytes": registration["bytes"],
        "sha256": registration["sha256"],
        "policy_sha256": registration["policy_sha256"],
        "repair_prestate_path": registration["repair_prestate_path"],
        "repair_prestate_bytes": registration["repair_prestate_bytes"],
        "repair_prestate_file_sha256": registration["repair_prestate_file_sha256"],
        "repair_prestate_sha256": registration["repair_prestate_sha256"],
    }


def _artifact_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if _safe_relative(root, path) != relative or not path.is_file():
        raise V7YWeatherCacheError(f"preserved artifact missing: {relative}")
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _verify_preserved_artifact(
    root: Path, preserved: Mapping[str, Any], relative: str
) -> dict[str, Any]:
    registered = preserved.get(relative)
    current = _artifact_record(root, relative)
    if (
        not isinstance(registered, Mapping)
        or set(registered) != {"bytes", "sha256"}
        or registered.get("bytes") != current["bytes"]
        or registered.get("sha256") != current["sha256"]
    ):
        raise V7YWeatherCacheError(f"preserved artifact binding differs: {relative}")
    _digest(registered.get("sha256"), "preserved artifact SHA-256")
    return current


def _verify_prestate(root: Path, policy: Mapping[str, Any]) -> dict[str, Any]:
    relative = policy.get("repair_prestate_path")
    if relative != REPAIR_PRESTATE_PATH.as_posix():
        raise V7YWeatherCacheError("V7Y repair prestate path differs")
    path = root / REPAIR_PRESTATE_PATH
    value = _load_object(path)
    if (
        policy.get("repair_prestate_sha256") != REGISTERED_PRESTATE_SHA256
        or value.get("snapshot_sha256") != REGISTERED_PRESTATE_SHA256
        or value.get("snapshot_sha256") != _canonical_hash(value, "snapshot_sha256")
        or value.get("schema_version") != "v7y-availability-repair-prestate-v1"
        or value.get("authorization") != "User requested apply the fix on 2026-09-29"
        or value.get("protected_labels_read") is not False
        or value.get("network_used") is not False
        or value.get("paper_orders_placed") != 0
        or value.get("live_orders_placed") != 0
    ):
        raise V7YWeatherCacheError("V7Y repair prestate registration differs")
    _parse_utc(value.get("registered_at_utc"), "prestate registration time")
    prior = value.get("prior_recovery_state")
    if not isinstance(prior, Mapping):
        raise V7YWeatherCacheError("V7Y repair prior recovery state missing")
    expected_failures = {"2025-08-15", "2025-12-18", "2025-12-19"}
    expected_completed = {
        (START + timedelta(days=offset)).isoformat()
        for offset in range((END - START).days + 1)
    } - expected_failures
    completed = prior.get("completed_dates")
    failures = prior.get("failed_dates")
    if (
        prior.get("schema_version") != "v7y-weather-backfill-v1"
        or prior.get("campaign_id") != "v7y-calendar-2025"
        or prior.get("date_start") != START.isoformat()
        or prior.get("date_end") != END.isoformat()
        or prior.get("target_date_count") != 184
        or prior.get("status") != "INCOMPLETE"
        or not isinstance(completed, list)
        or len(completed) != 181
        or set(completed) != expected_completed
        or not isinstance(failures, Mapping)
        or set(failures) != expected_failures
        or set(completed) & expected_failures
        or prior.get("unavailable_dates") != []
        or prior.get("protected_labels_read") is not False
        or prior.get("paper_orders_placed") != 0
        or prior.get("live_orders_placed") != 0
        or prior.get("recovery_sha256") != _canonical_hash(prior, "recovery_sha256")
    ):
        raise V7YWeatherCacheError("V7Y repair recovery identity differs")
    _digest(value.get("prior_recovery_file_sha256"), "prior recovery file SHA-256")
    preserved = value.get("preserved_artifacts")
    if not isinstance(preserved, Mapping):
        raise V7YWeatherCacheError("V7Y preserved artifact inventory missing")
    for shared in PRESERVED_SHARED_PATHS:
        _verify_preserved_artifact(root, preserved, shared)
    return value


def verify_policy_registration(root: Path) -> dict[str, Any]:
    """Verify and inventory the outcome-blind V7Y availability registration."""
    root = Path(root).resolve()
    path = root / POLICY_CONFIG_PATH
    value = _load_object(path)
    expected = {
        "schema_version": "v7y-weather-availability-policy-v1",
        "status": "REGISTERED_BEFORE_V7Y_FREEZE",
        "policy_id": POLICY_ID,
        "decision_time_utc": "18:00:00",
        "availability_delay_hours": 6,
        "effective_available_at": (
            "max(nominal_model_cycle_plus_6_hours, archived_HTTP_Last-Modified_if_present)"
        ),
        "row_admission_rule": (
            "effective_information_available_at_utc_must_be_at_or_before_18:00:00_UTC"
        ),
        "historical_publication_time_proven": False,
        "fixed_decision_time_changed": False,
        "protected_labels_read": False,
        "new_output_root": OUTPUT_ROOT.as_posix(),
        "preserved_legacy_normalized_data": True,
        "eligible_decision_times": (
            "Preserve actual eligible 12:00, 15:00, and 18:00 schedules for each row"
        ),
        "repair_prestate_path": REPAIR_PRESTATE_PATH.as_posix(),
        "repair_prestate_sha256": REGISTERED_PRESTATE_SHA256,
        "scope": (
            "All V7Y calendar-2025 HRRR/GEFS rows at the existing 18:00 UTC decision"
        ),
        "reason": (
            "Shared V5 all-schedule eligibility incorrectly rejected December files "
            "available before V7Y's registered 18:00 decision"
        ),
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise V7YWeatherCacheError("V7Y availability policy registration differs")
    if (
        value.get("policy_sha256") != REGISTERED_POLICY_SHA256
        or value.get("policy_sha256") != _canonical_hash(value, "policy_sha256")
    ):
        raise V7YWeatherCacheError("V7Y availability policy seal mismatch")
    _parse_utc(value.get("registered_at_utc"), "policy registration time")
    prestate = _verify_prestate(root, value)
    return {
        "path": POLICY_CONFIG_PATH.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "policy_sha256": value["policy_sha256"],
        "repair_prestate_path": REPAIR_PRESTATE_PATH.as_posix(),
        "repair_prestate_bytes": (root / REPAIR_PRESTATE_PATH).stat().st_size,
        "repair_prestate_file_sha256": sha256_file(root / REPAIR_PRESTATE_PATH),
        "repair_prestate_sha256": prestate["snapshot_sha256"],
        "repair_prestate": prestate,
        "policy": value,
    }


def _verify_legacy_availability(row: Mapping[str, Any]) -> None:
    available, eligible = _availability(row)
    if (
        row.get("partition") != LEGACY_PARTITION
        or row.get("availability_policy_id") != LEGACY_POLICY_ID
        or row.get("availability_delay_hours") != 6
        or row.get("effective_information_available_at_utc") != _iso(available)
        or row.get("effective_information_availability_basis") != EFFECTIVE_BASIS
        or _string_list(row.get("eligible_decision_times_utc"), "legacy eligible times") != eligible
        or eligible != ["12:00", "15:00", "18:00"]
        or row.get("historical_availability_proven") is not False
        or row.get("as_of_validated") is not True
        or row.get("as_of_validation_basis") != VALIDATION_BASIS
        or row.get("contains_settlement_label") is not False
    ):
        raise V7YWeatherCacheError("legacy V5P availability binding differs")


def apply_v7y_availability(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the conservative formula and admit only rows available by 18:00 UTC."""
    audited: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        target = _target_date(row.get("climate_date"))
        model = row.get("model")
        if model not in {"hrrr", "gefs"} or row.get("partition") != LEGACY_PARTITION:
            raise V7YWeatherCacheError("weather row identity differs")
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        expected_cycle = 6 if model == "hrrr" else 0
        if nominal.date() != target or nominal.hour != expected_cycle or nominal.minute != 0 or nominal.second != 0:
            raise V7YWeatherCacheError("weather row model cycle differs")
        if row.get("as_of_validated") is True:
            _verify_legacy_availability(row)
        elif (
            row.get("as_of_validated") is not False
            or row.get("historical_availability_proven") is not False
            or row.get("contains_settlement_label", False) is not False
        ):
            raise V7YWeatherCacheError("decoded weather timing flags differ")
        available, eligible = _availability(row)
        if "18:00" not in eligible:
            raise V7YWeatherCacheError("row was not available by the fixed 18:00 UTC decision")
        row.update({
            "partition": PARTITION,
            "availability_policy_id": POLICY_ID,
            "availability_delay_hours": 6,
            "effective_information_available_at_utc": _iso(available),
            "effective_information_availability_basis": EFFECTIVE_BASIS,
            "eligible_decision_times_utc": eligible,
            "historical_availability_proven": False,
            "as_of_validated": True,
            "as_of_validation_basis": VALIDATION_BASIS,
            "contains_settlement_label": False,
        })
        audited.append(row)
    if not audited:
        raise V7YWeatherCacheError("availability audit requires weather rows")
    return audited


def _expected_tuples(model: str) -> set[tuple[Any, ...]]:
    if model == "hrrr":
        return {
            (lead, None, field, "KLAX")
            for lead in (2, 8, 14, 20)
            for field in (
                "temperature_2m",
                "total_cloud_cover",
                "cloud_ceiling",
                "wind_u_10m",
                "wind_v_10m",
                "mean_sea_level_pressure",
            )
        }
    if model == "gefs":
        return {
            (lead, member, "temperature_2m", "KLAX")
            for lead in (9, 12, 15, 18, 21, 24, 27, 30)
            for member in ("avg", "spr")
        }
    raise V7YWeatherCacheError("unsupported V7Y weather model")


def _verify_v7y_row(row: Mapping[str, Any], model: str, target: date) -> None:
    available, eligible = _availability(row)
    if (
        row.get("model") != model
        or row.get("climate_date") != target.isoformat()
        or row.get("partition") != PARTITION
        or row.get("availability_policy_id") != POLICY_ID
        or row.get("availability_delay_hours") != 6
        or row.get("effective_information_available_at_utc") != _iso(available)
        or row.get("effective_information_availability_basis") != EFFECTIVE_BASIS
        or _string_list(row.get("eligible_decision_times_utc"), "eligible decision times") != eligible
        or "18:00" not in eligible
        or row.get("historical_availability_proven") is not False
        or row.get("as_of_validated") is not True
        or row.get("as_of_validation_basis") != VALIDATION_BASIS
        or row.get("contains_settlement_label") is not False
    ):
        raise V7YWeatherCacheError(f"{model} row boundary differs")
    nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
    expected_cycle = 6 if model == "hrrr" else 0
    if nominal.date() != target or nominal.hour != expected_cycle or nominal.minute != 0 or nominal.second != 0:
        raise V7YWeatherCacheError(f"{model} model cycle differs")
    _digest(row.get("source_sha256"), "source SHA-256")
    _digest(row.get("index_sha256"), "index SHA-256")
    if row.get("field_id") == "temperature_2m":
        try:
            numeric = float(row.get("value"))
        except (TypeError, ValueError) as exc:
            raise V7YWeatherCacheError(f"{model} temperature is not numeric") from exc
        if (
            row.get("is_missing") is not False
            or row.get("units") not in {"degF", "delta_degF"}
            or not math.isfinite(numeric)
        ):
            raise V7YWeatherCacheError(f"{model} temperature value differs")


def coverage(rows: list[dict[str, Any]], model: str, target: date) -> dict[str, Any]:
    """Validate the exact frozen V7Y tuple inventory for one model-day."""
    target = _target_date(target)
    expected = _expected_tuples(model)
    actual = {
        (row.get("lead_hours"), row.get("member_id"), row.get("field_id"), row.get("point_id"))
        for row in rows
    }
    if actual != expected or len(rows) != len(expected):
        raise V7YWeatherCacheError(f"{model} normalized coverage differs")
    for row in rows:
        _verify_v7y_row(row, model, target)
    return {"model": model, "rows": len(rows), "complete": True}


def _day_paths(root: Path, target: date) -> tuple[Path, Path, Path]:
    folder = root / OUTPUT_ROOT / f"date={target.isoformat()}"
    return (
        folder / "hrrr_points.parquet",
        folder / "gefs_summary_points.parquet",
        folder / "manifest.json",
    )


def _legacy_paths(root: Path, target: date) -> tuple[Path, Path, Path]:
    folder = root / LEGACY_OUTPUT_ROOT / f"date={target.isoformat()}"
    return (
        folder / "hrrr_points.parquet",
        folder / "gefs_summary_points.parquet",
        folder / "manifest.json",
    )


def _output_record(root: Path, path: Path, model: str, rows: int) -> dict[str, Any]:
    return {
        "model": model,
        "path": _safe_relative(root, path),
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _read_bound_output(root: Path, record: Mapping[str, Any], expected_path: Path,
                       expected_model: str, expected_rows: int) -> list[dict[str, Any]]:
    if (
        record.get("model") != expected_model
        or record.get("path") != _safe_relative(root, expected_path)
        or record.get("rows") != expected_rows
        or not expected_path.is_file()
        or record.get("bytes") != expected_path.stat().st_size
        or record.get("sha256") != sha256_file(expected_path)
    ):
        raise V7YWeatherCacheError(f"{expected_model} output binding differs")
    try:
        rows = pd.read_parquet(expected_path).to_dict(orient="records")
    except Exception as exc:
        raise V7YWeatherCacheError(f"invalid {expected_model} Parquet") from exc
    if len(rows) != expected_rows:
        raise V7YWeatherCacheError(f"{expected_model} Parquet row count differs")
    return rows


def _legacy_day(
    root: Path, target: date, registration: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]] | None:
    h_path, g_path, manifest_path = _legacy_paths(root, target)
    exists = (h_path.is_file(), g_path.is_file(), manifest_path.is_file())
    if not any(exists):
        return None
    if not all(exists):
        raise V7YWeatherCacheError("partial legacy V5P normalized day")
    preserved = registration["repair_prestate"].get("preserved_artifacts", {})
    preserved_inputs = [
        _verify_preserved_artifact(root, preserved, _safe_relative(root, path))
        for path in (h_path, g_path, manifest_path)
    ]
    manifest = _load_object(manifest_path)
    if (
        manifest.get("manifest_sha256") != _canonical_hash(manifest, "manifest_sha256")
        or manifest.get("version") != LEGACY_VERSION
        or manifest.get("status") != "NORMALIZED_FEATURES_ONLY"
        or manifest.get("climate_date") != target.isoformat()
        or manifest.get("partition") != LEGACY_PARTITION
        or manifest.get("availability_policy_id") != LEGACY_POLICY_ID
        or manifest.get("decision_time_utc") != "18:00"
        or manifest.get("network_used") is not False
        or manifest.get("refit_performed") is not False
        or manifest.get("protected_confirmation_labels_read") is not False
        or manifest.get("actual_orders_placed") is not False
    ):
        raise V7YWeatherCacheError("legacy V5P normalized manifest differs")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 2:
        raise V7YWeatherCacheError("legacy V5P output inventory differs")
    by_model = {record.get("model"): record for record in outputs if isinstance(record, dict)}
    if set(by_model) != {"hrrr", "gefs"}:
        raise V7YWeatherCacheError("legacy V5P output models differ")
    h_rows = _read_bound_output(root, by_model["hrrr"], h_path, "hrrr", 24)
    g_rows = _read_bound_output(root, by_model["gefs"], g_path, "gefs", 16)
    h_cover = legacy_coverage(h_rows, "hrrr", target)
    g_cover = legacy_coverage(g_rows, "gefs", target)
    if manifest.get("coverage") != {"hrrr": h_cover, "gefs": g_cover}:
        raise V7YWeatherCacheError("legacy V5P coverage binding differs")
    for row in h_rows + g_rows:
        _verify_legacy_availability(row)
    inventory = {
        "kind": "verified_legacy_v5p_normalized",
        "legacy_manifest": {
            "path": _safe_relative(root, manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": sha256_file(manifest_path),
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "legacy_outputs": [dict(by_model[model]) for model in ("hrrr", "gefs")],
        "preserved_input_artifacts": sorted(
            preserved_inputs, key=lambda record: record["path"]
        ),
    }
    return h_rows, g_rows, inventory


def _raw_cache_summary(result: Mapping[str, Any], expected_objects: int, model: str) -> dict[str, Any]:
    if (
        result.get("status") != "CACHE_COMPATIBILITY_PASS"
        or result.get("objects_verified") != expected_objects
        or result.get("network_used") is not False
        or result.get("protected_final_read") is not False
    ):
        raise V7YWeatherCacheError(f"{model} raw cache verification differs")
    return {
        "status": result["status"],
        "objects_verified": result["objects_verified"],
        "cached_bytes_verified": result["cached_bytes_verified"],
        "network_used": False,
        "protected_final_read": False,
    }


def _sha_inventory(rows: Iterable[Mapping[str, Any]], field: str) -> list[str]:
    return sorted({_digest(row.get(field), field) for row in rows})


def _raw_prestate_inventory(
    root: Path,
    target: date,
    registration: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    preserved = registration["repair_prestate"].get("preserved_artifacts", {})
    prefix = RAW_CACHE_ROOT.as_posix() + "/"
    marker = f"-{target:%Y%m%d}-"
    relative_paths = sorted(
        relative
        for relative in preserved
        if isinstance(relative, str)
        and relative.startswith(prefix)
        and marker in relative[len(prefix):].split("/", 1)[0]
    )
    if not relative_paths:
        raise V7YWeatherCacheError("raw day is absent from the registered repair prestate")
    records = [
        _verify_preserved_artifact(root, preserved, relative)
        for relative in relative_paths
    ]
    row_inputs: set[str] = set()
    for row in rows:
        for field, digest_field in (
            ("source_path", "source_sha256"),
            ("index_path", "index_sha256"),
        ):
            recorded = row.get(field)
            if not isinstance(recorded, str):
                raise V7YWeatherCacheError(f"decoded weather {field} missing")
            recorded_path = Path(recorded)
            relative = _safe_relative(
                root, recorded_path if recorded_path.is_absolute() else root / recorded_path
            )
            registered = preserved.get(relative)
            if (
                not isinstance(registered, Mapping)
                or row.get(digest_field) != registered.get("sha256")
            ):
                raise V7YWeatherCacheError(
                    f"decoded weather {digest_field} differs from repair prestate"
                )
            row_inputs.add(relative)
    if not row_inputs <= set(relative_paths):
        raise V7YWeatherCacheError("decoded raw inputs are absent from the repair prestate")
    return records


def normalized_day(root: Path, target: date) -> dict[str, Any]:
    """Create or validate one V7Y day using local, hash-verified inputs only."""
    root = Path(root).resolve()
    target = _target_date(target)
    registration = verify_policy_registration(root)
    h_path, g_path, manifest_path = _day_paths(root, target)
    current = (h_path.is_file(), g_path.is_file(), manifest_path.is_file())
    if current[2]:
        if not all(current):
            raise V7YWeatherCacheError("partial V7Y normalized day")
        manifest = _load_object(manifest_path)
        validate_normalized_day(root, target, manifest)
        return manifest

    h_plan, g_plan = build_v5_daily_weather_plans(target)
    cache = root / RAW_CACHE_ROOT
    h_raw = _raw_cache_summary(verify_cache_only(h_plan, cache), 4, "hrrr")
    g_raw = _raw_cache_summary(verify_cache_only(g_plan, cache), 16, "gefs")
    legacy = _legacy_day(root, target, registration)
    if legacy is None:
        h_rows = _decode_plan(h_plan, cache)
        g_rows = _decode_plan(g_plan, cache)
        migration: dict[str, Any] = {"kind": "decoded_from_verified_raw_cache"}
    else:
        h_rows, g_rows, migration = legacy
    h_rows = apply_v7y_availability(h_rows)
    g_rows = apply_v7y_availability(g_rows)
    h_cover = coverage(h_rows, "hrrr", target)
    g_cover = coverage(g_rows, "gefs", target)
    all_rows = h_rows + g_rows
    if migration["kind"] == "decoded_from_verified_raw_cache":
        migration["preserved_input_artifacts"] = _raw_prestate_inventory(
            root, target, registration, all_rows
        )
    migration.update({
        "raw_cache": {"hrrr": h_raw, "gefs": g_raw},
        "source_sha256_inventory": _sha_inventory(all_rows, "source_sha256"),
        "index_sha256_inventory": _sha_inventory(all_rows, "index_sha256"),
    })

    # The manifest is the commit marker.  If a prior attempt stopped after
    # creating the directory or either derived Parquet, verified inputs are
    # deterministically rewritten before the manifest is published.
    h_path.parent.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(h_path, h_rows)
    _write_parquet_atomic(g_path, g_rows)
    outputs = [
        _output_record(root, h_path, "hrrr", len(h_rows)),
        _output_record(root, g_path, "gefs", len(g_rows)),
    ]
    manifest = {
        "version": VERSION,
        "status": "NORMALIZED_FEATURES_ONLY",
        "climate_date": target.isoformat(),
        "partition": PARTITION,
        "availability_policy_id": POLICY_ID,
        "decision_time_utc": "18:00",
        "availability_policy_registration": _policy_binding(registration),
        "coverage": {"hrrr": h_cover, "gefs": g_cover},
        "outputs": outputs,
        "migration_source": migration,
        "migration_source_sha256": _canonical_hash(migration),
        "network_used": False,
        "refit_performed": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    write_json(manifest_path, manifest)
    validate_normalized_day(root, target, manifest)
    return manifest


def _verify_migration_source(
    root: Path,
    target: date,
    migration: Mapping[str, Any],
    rows: list[dict[str, Any]],
    registration: Mapping[str, Any],
) -> None:
    if migration.get("kind") not in {
        "verified_legacy_v5p_normalized",
        "decoded_from_verified_raw_cache",
    }:
        raise V7YWeatherCacheError("V7Y migration source kind differs")
    raw = migration.get("raw_cache")
    if not isinstance(raw, dict):
        raise V7YWeatherCacheError("V7Y raw cache inventory missing")
    _raw_cache_summary(raw.get("hrrr", {}), 4, "hrrr")
    _raw_cache_summary(raw.get("gefs", {}), 16, "gefs")
    if migration.get("source_sha256_inventory") != _sha_inventory(rows, "source_sha256"):
        raise V7YWeatherCacheError("V7Y source SHA-256 inventory differs")
    if migration.get("index_sha256_inventory") != _sha_inventory(rows, "index_sha256"):
        raise V7YWeatherCacheError("V7Y index SHA-256 inventory differs")
    registered_inputs = migration.get("preserved_input_artifacts")
    if not isinstance(registered_inputs, list):
        raise V7YWeatherCacheError("V7Y preserved input inventory missing")
    if migration.get("kind") == "decoded_from_verified_raw_cache":
        if "legacy_manifest" in migration or "legacy_outputs" in migration:
            raise V7YWeatherCacheError("raw-decoded migration unexpectedly names legacy outputs")
        expected_inputs = _raw_prestate_inventory(root, target, registration, rows)
        if registered_inputs != expected_inputs:
            raise V7YWeatherCacheError("raw prestate input inventory differs")
        return
    legacy_manifest = migration.get("legacy_manifest")
    legacy_outputs = migration.get("legacy_outputs")
    if not isinstance(legacy_manifest, dict) or not isinstance(legacy_outputs, list):
        raise V7YWeatherCacheError("legacy migration inventory missing")
    preserved = registration["repair_prestate"].get("preserved_artifacts", {})
    expected_inputs = sorted(
        [
            _verify_preserved_artifact(root, preserved, _safe_relative(root, path))
            for path in _legacy_paths(root, target)
        ],
        key=lambda record: record["path"],
    )
    if registered_inputs != expected_inputs:
        raise V7YWeatherCacheError("legacy prestate input inventory differs")
    manifest_path = root / str(legacy_manifest.get("path"))
    if (
        _safe_relative(root, manifest_path) != legacy_manifest.get("path")
        or not manifest_path.is_file()
        or legacy_manifest.get("bytes") != manifest_path.stat().st_size
        or legacy_manifest.get("sha256") != sha256_file(manifest_path)
    ):
        raise V7YWeatherCacheError("legacy migration manifest binding differs")
    legacy = _load_object(manifest_path)
    if (
        legacy.get("manifest_sha256") != legacy_manifest.get("manifest_sha256")
        or legacy.get("manifest_sha256") != _canonical_hash(legacy, "manifest_sha256")
    ):
        raise V7YWeatherCacheError("legacy migration manifest seal differs")
    if legacy.get("outputs") != legacy_outputs:
        raise V7YWeatherCacheError("legacy migration output inventory differs")
    for record in legacy_outputs:
        path = root / str(record.get("path"))
        if (
            _safe_relative(root, path) != record.get("path")
            or not path.is_file()
            or record.get("bytes") != path.stat().st_size
            or record.get("sha256") != sha256_file(path)
        ):
            raise V7YWeatherCacheError("legacy migration output binding differs")


def validate_normalized_day(root: Path, target: date, manifest: Mapping[str, Any]) -> None:
    """Recompute hashes, coverage, and every 18:00 availability decision."""
    root = Path(root).resolve()
    target = _target_date(target)
    registration = verify_policy_registration(root)
    if not isinstance(manifest, Mapping):
        raise V7YWeatherCacheError("V7Y normalized manifest must be an object")
    if (
        manifest.get("manifest_sha256") != _canonical_hash(manifest, "manifest_sha256")
        or manifest.get("version") != VERSION
        or manifest.get("status") != "NORMALIZED_FEATURES_ONLY"
        or manifest.get("climate_date") != target.isoformat()
        or manifest.get("partition") != PARTITION
        or manifest.get("availability_policy_id") != POLICY_ID
        or manifest.get("decision_time_utc") != "18:00"
        or manifest.get("availability_policy_registration") != _policy_binding(registration)
        or manifest.get("network_used") is not False
        or manifest.get("refit_performed") is not False
        or manifest.get("protected_confirmation_labels_read") is not False
        or manifest.get("actual_orders_placed") is not False
    ):
        raise V7YWeatherCacheError("V7Y normalized manifest differs")
    migration = manifest.get("migration_source")
    if not isinstance(migration, Mapping) or manifest.get("migration_source_sha256") != _canonical_hash(migration):
        raise V7YWeatherCacheError("V7Y migration source seal differs")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 2:
        raise V7YWeatherCacheError("V7Y output inventory differs")
    by_model = {record.get("model"): record for record in outputs if isinstance(record, Mapping)}
    if set(by_model) != {"hrrr", "gefs"}:
        raise V7YWeatherCacheError("V7Y output models differ")
    h_path, g_path, _ = _day_paths(root, target)
    h_rows = _read_bound_output(root, by_model["hrrr"], h_path, "hrrr", 24)
    g_rows = _read_bound_output(root, by_model["gefs"], g_path, "gefs", 16)
    actual_coverage = {
        "hrrr": coverage(h_rows, "hrrr", target),
        "gefs": coverage(g_rows, "gefs", target),
    }
    if manifest.get("coverage") != actual_coverage:
        raise V7YWeatherCacheError("V7Y manifest coverage differs")
    _verify_migration_source(
        root, target, migration, h_rows + g_rows, registration
    )
