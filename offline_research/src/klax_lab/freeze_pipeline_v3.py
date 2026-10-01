"""Freeze the complete manifest-bound V3 development dataset offline.

This entry point is deliberately downstream of finite weather acquisition,
cache-only normalization, and source finalization.  It discovers forecast and
observation artifacts only through their complete component manifests, checks
their exact bytes and registered daily inventory, adapts the normalizer's
field names to :mod:`klax_lab.dataset_v3`, and publishes the two canonical
readiness components.  It has no acquisition path and never reads the
protected-final tree.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .dataset_v3 import (
    build_frozen_development_dataset,
    normalized_input_manifest,
    publish_frozen_dataset_component_manifests,
    verify_frozen_development_dataset,
)
from .provenance import canonical_hash, sha256_file
from .substantive_readiness_v3 import SOURCE_COMPLETE_STATUS, WEATHER_COMPLETE_STATUS
from .weather_normalize_v3 import (
    AVAILABILITY_DELAY,
    GEFS_LEADS,
    GEFS_MEMBERS,
    HRRR_FIELDS,
    HRRR_LEADS,
    POLICY_AMENDMENT_PATH,
    POLICY_AMENDMENT_SHA256,
    POLICY_CONFIG_PATH,
    POLICY_CONFIG_SHA256,
    POLICY_ID,
    PRE_AMENDMENT_PROGRESS_PATH,
    PRE_AMENDMENT_PROGRESS_SHA256,
    registered_weather_dates,
    validate_daily_coverage,
    verify_availability_policy_registration,
)


EXPECTED_WEATHER_DAYS = 543
DECISION_TIMES_UTC = ("12:00", "15:00", "18:00")
REQUIRED_STATIONS = ("KLAX",)
OPTIONAL_STATIONS = ("KHHR", "KLGB", "KSMO", "KTOA")
SHA256 = re.compile(r"[0-9a-f]{64}")

SOURCE_MANIFEST = Path("data/manifests/v3_source_feasibility.json")
NORMALIZATION_PROGRESS = Path("data/manifests/v3_weather_normalization_progress.json")
HRRR_MANIFEST = Path("data/manifests/v3_hrrr_local_observations.json")
GEFS_MANIFEST = Path("data/manifests/v3_gefs.json")
TRAINING_TARGET_MANIFEST = Path("data/manifests/v3_weather_training_targets.json")
SETTLEMENT_MANIFEST = Path("data/manifests/v3_settlement_reconciliation.json")
MARKET_MANIFEST = Path("data/manifests/v3_market_normalization.json")
MINUTE_SOURCE_MANIFEST = Path("data/manifests/v3_minute_kalshi.json")
LOCAL_OBSERVATION_MANIFEST = Path("data/manifests/v3_local_observations_partial.json")
DESTINATION = Path("data/frozen/v3_development")

TRAINING_TARGET = Path(
    "data/normalized/v3_development/weather_training/labels/settlement_targets.parquet"
)
DEVELOPMENT_TARGET = Path(
    "data/normalized/v3_development/selection/labels/settlement_targets.parquet"
)
TRAINING_EXCLUSIONS = Path(
    "data/normalized/v3_development/weather_training/labels/settlement_target_exclusions.json"
)
DEVELOPMENT_EXCLUSIONS = Path(
    "data/normalized/v3_development/selection/labels/settlement_target_exclusions.json"
)
MARKET_PATHS = (
    Path("data/normalized/selection/features/candles_1m.parquet"),
    Path("data/normalized/selection/features/trades.parquet"),
)
OBSERVATION_PATHS = {
    "weather_training": Path(
        "data/normalized/v3_observations/weather_training/features/local_observations.parquet"
    ),
    "selection": Path(
        "data/normalized/v3_observations/selection/features/local_observations.parquet"
    ),
}


class V3FreezePipelineError(ValueError):
    """A required finalized input is absent, partial, stale, or out of scope."""


def _safe_root(value: Path) -> Path:
    root = Path(value).resolve()
    if "protected_final" in {part.casefold().replace("-", "_") for part in root.parts}:
        raise V3FreezePipelineError("The development freeze cannot run in protected-final storage")
    return root


def _inside(root: Path, value: Path | str, label: str) -> Path:
    supplied = Path(value)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise V3FreezePipelineError(f"{label} must be a project-relative path")
    path = (root / supplied).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise V3FreezePipelineError(f"{label} escapes the project root") from exc
    if "protected_final" in {
        part.casefold().replace("-", "_") for part in relative.parts
    }:
        raise V3FreezePipelineError(f"{label} points into protected-final storage")
    return path


def _read_json(root: Path, relative: Path, label: str) -> dict[str, Any]:
    path = _inside(root, relative, label)
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                V3FreezePipelineError(f"Non-finite JSON in {label}: {token}")
            ),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V3FreezePipelineError(f"Missing or invalid {label}: {relative.as_posix()}") from exc
    if not isinstance(value, dict):
        raise V3FreezePipelineError(f"{label} must contain a JSON object")
    if value.get("protected_final_read") is not False:
        raise V3FreezePipelineError(f"{label} does not prove protected-final denial")
    return value


def _manifest_envelope(root: Path, relative: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    path = _inside(root, relative, "source manifest")
    return {
        "path": relative.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "manifest": dict(body),
    }


def _expected_dates() -> dict[str, list[str]]:
    result = {"weather_training": [], "selection": []}
    for value in registered_weather_dates():
        partition = "weather_training" if value.year == 2024 else "selection"
        result[partition].append(value.isoformat())
    if sum(map(len, result.values())) != EXPECTED_WEATHER_DAYS:
        raise AssertionError("Registered V3 weather calendar changed")
    return result


def _require_finalized_source(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = _read_json(root, SOURCE_MANIFEST, "finalized source feasibility")
    audit = source.get("completion_audit")
    if (source.get("status") != SOURCE_COMPLETE_STATUS
            or source.get("readiness_component_pass") is not True
            or not isinstance(audit, dict)
            or audit.get("days_complete") != EXPECTED_WEATHER_DAYS
            or audit.get("days_unavailable") != 0
            or audit.get("offline_normalization_verified") is not True
            or audit.get("protected_final_read") is not False):
        raise V3FreezePipelineError("Weather source finalization is absent or incomplete")
    try:
        registered_policy = verify_availability_policy_registration(root)
    except ValueError as exc:
        raise V3FreezePipelineError(
            "Weather availability registration is missing or changed") from exc
    expected_policy_bindings = {
        "weather_availability_policy_config_path": POLICY_CONFIG_PATH.as_posix(),
        "weather_availability_policy_config_sha256": POLICY_CONFIG_SHA256,
        "weather_availability_amendment_path": POLICY_AMENDMENT_PATH.as_posix(),
        "weather_availability_amendment_sha256": POLICY_AMENDMENT_SHA256,
        "pre_amendment_normalization_snapshot_path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
        "pre_amendment_normalization_snapshot_sha256": PRE_AMENDMENT_PROGRESS_SHA256,
    }
    if any(audit.get(key) != value for key, value in expected_policy_bindings.items()):
        raise V3FreezePipelineError(
            "Finalized source has stale weather availability policy bindings")
    progress = _read_json(root, NORMALIZATION_PROGRESS, "weather normalization progress")
    hrrr = _read_json(root, HRRR_MANIFEST, "complete HRRR/local-observation component")
    gefs = _read_json(root, GEFS_MANIFEST, "complete GEFS component")
    expected_flat = _expected_dates()["weather_training"] + _expected_dates()["selection"]
    progress_days = progress.get("days")
    migration_identity = ([{
        "date": item.get("date"), "migrated_from_v1": item.get("migrated_from_v1"),
        "manifest_path": (item.get("manifest") or {}).get("path"),
        "manifest_sha256": (item.get("manifest") or {}).get("sha256"),
    } for item in progress_days] if isinstance(progress_days, list) else [])
    if (progress.get("status") != "COMPLETE_COMPONENTS_PUBLISHED"
            or progress.get("coverage_complete") is not True
            or progress.get("normalized_days") != EXPECTED_WEATHER_DAYS
            or progress.get("missing_raw_cache_days") != 0
            or progress.get("integrity_failure_days") != 0
            or progress.get("network_used") is not False
            or progress.get("availability_policy") != registered_policy
            or progress.get("migrated_verified_v1_days") != 542
            or progress.get("fresh_v2_normalized_dates") != ["2025-06-28"]
            or len(migration_identity) != EXPECTED_WEATHER_DAYS
            or [item["date"] for item in migration_identity] != expected_flat
            or sum(item["migrated_from_v1"] is True for item in migration_identity) != 542
            or [item["date"] for item in migration_identity
                if item["migrated_from_v1"] is not True] != ["2025-06-28"]
            or audit.get("migrated_verified_v1_days") != 542
            or audit.get("fresh_v2_normalized_dates") != ["2025-06-28"]
            or audit.get("weather_policy_migration_identity_sha256")
            != canonical_hash(migration_identity)
            or progress.get("readiness_component_manifests_published") is not True):
        raise V3FreezePipelineError("Weather normalization is not exact complete offline coverage")
    for item in progress_days:
        record = item.get("manifest")
        expected_manifest = (
            f"data/normalized/v3_weather/{item.get('partition')}/"
            f"date={item.get('date')}/normalization_manifest.json")
        if not isinstance(record, dict) or record.get("path") != expected_manifest:
            raise V3FreezePipelineError("Weather daily manifest inventory differs")
        path = _inside(root, Path(expected_manifest), "weather daily manifest")
        if (not path.is_file() or record.get("bytes") != path.stat().st_size
                or record.get("sha256") != sha256_file(path)):
            raise V3FreezePipelineError("Weather daily manifest hash differs")
    expected_hashes = {
        "hrrr_component_sha256": sha256_file(_inside(root, HRRR_MANIFEST, "HRRR manifest")),
        "gefs_component_sha256": sha256_file(_inside(root, GEFS_MANIFEST, "GEFS manifest")),
        "normalization_progress_sha256": sha256_file(
            _inside(root, NORMALIZATION_PROGRESS, "normalization progress")
        ),
    }
    for key, digest in expected_hashes.items():
        if audit.get(key) != digest:
            raise V3FreezePipelineError(f"Finalized source has a stale {key} binding")
    for component, body in (("hrrr_local_observations", hrrr), ("gefs", gefs)):
        if (body.get("component") != component
                or body.get("status") != WEATHER_COMPLETE_STATUS
                or body.get("readiness_component_pass") is not True
                or body.get("availability_policy") != registered_policy
                or body.get("registered_days") != EXPECTED_WEATHER_DAYS):
            raise V3FreezePipelineError(f"{component} is not a complete weather component")
    return source, hrrr, gefs


def _verify_parquet_record(root: Path, record: Mapping[str, Any], *,
                           expected_path: str, expected_model: str | None = None,
                           expected_rows: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import pyarrow.parquet as pq

    if not isinstance(record, Mapping) or record.get("path") != expected_path:
        raise V3FreezePipelineError(f"Manifest does not bind expected artifact {expected_path}")
    if expected_model is not None and record.get("model") != expected_model:
        raise V3FreezePipelineError(f"Wrong model binding for {expected_path}")
    digest = record.get("sha256")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise V3FreezePipelineError(f"Invalid SHA-256 binding for {expected_path}")
    path = _inside(root, Path(expected_path), "normalized artifact")
    if not path.is_file() or sha256_file(path) != digest:
        raise V3FreezePipelineError(f"Normalized artifact is missing or changed: {expected_path}")
    if "bytes" in record and record.get("bytes") != path.stat().st_size:
        raise V3FreezePipelineError(f"Normalized artifact byte count changed: {expected_path}")
    table = pq.read_table(path)
    rows = table.to_pylist()
    declared_rows = record.get("rows", expected_rows)
    if (type(declared_rows) is not int or declared_rows != len(rows)
            or (expected_rows is not None and len(rows) != expected_rows)):
        raise V3FreezePipelineError(f"Normalized artifact row count changed: {expected_path}")
    artifact = {
        "path": expected_path,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "rows": len(rows),
    }
    return rows, artifact


def _weather_output_map(body: Mapping[str, Any], model: str) -> dict[str, Mapping[str, Any]]:
    records = body.get("hrrr", {}).get("outputs") if model == "hrrr" else body.get("outputs")
    if not isinstance(records, list) or len(records) != EXPECTED_WEATHER_DAYS:
        raise V3FreezePipelineError(f"Complete {model} output inventory must contain 543 days")
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise V3FreezePipelineError(f"Malformed {model} output record")
        path = str(record["path"])
        if path in result:
            raise V3FreezePipelineError(f"Duplicate {model} output path")
        result[path] = record
    return result


def _adapt_forecast_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    aliases = {
        "initialized_at": row.get("forecast_reference_time_utc"),
        "available_at": row.get("effective_information_available_at_utc"),
        "valid_at": row.get("valid_time_utc"),
        "location_id": row.get("point_id"),
    }
    if any(not isinstance(value, str) or not value for value in aliases.values()):
        raise V3FreezePipelineError("Normalized forecast row lacks a dataset as-of alias")
    result.update(aliases)
    return result


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise V3FreezePipelineError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3FreezePipelineError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3FreezePipelineError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _validate_cycle_contract(rows: Sequence[Mapping[str, Any]], *,
                             model: str, target_date: date) -> None:
    expected_hour = 6 if model == "hrrr" else 0
    expected_cycle = datetime.combine(
        target_date, datetime.min.time(), timezone.utc
    ).replace(hour=expected_hour)
    for row in rows:
        reference = _parse_utc(row.get("forecast_reference_time_utc"), "forecast cycle")
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        available = _parse_utc(
            row.get("effective_information_available_at_utc"), "effective availability"
        )
        archived_value = row.get("source_last_modified_at_utc")
        archived = (_parse_utc(archived_value, "archived HTTP Last-Modified")
                    if archived_value is not None else None)
        nominal_bound = expected_cycle + AVAILABILITY_DELAY
        expected_available = max(
            value for value in (nominal_bound, archived) if value is not None)
        first_decision = datetime.combine(
            target_date, datetime.min.time(), timezone.utc).replace(hour=12)
        valid = _parse_utc(row.get("valid_time_utc"), "forecast valid time")
        lead = row.get("lead_hours")
        if (reference != expected_cycle or nominal != expected_cycle
                or row.get("cycle_hour_utc") != expected_hour
                or row.get("availability_policy_id") != POLICY_ID
                or available != expected_available or available > first_decision
                or row.get("eligible_decision_times_utc")
                != ["12:00", "15:00", "18:00"]
                or type(lead) is not int or valid != expected_cycle + timedelta(hours=lead)):
            raise V3FreezePipelineError(
                f"Normalized {model} cycle/timing contract differs on {target_date.isoformat()}"
            )


def _load_weather_rows(root: Path, hrrr: Mapping[str, Any], gefs: Mapping[str, Any]) -> tuple[
        dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    expected = _expected_dates()
    records = {"hrrr": _weather_output_map(hrrr, "hrrr"),
               "gefs": _weather_output_map(gefs, "gefs")}
    expected_rows = {"hrrr": len(HRRR_LEADS) * len(HRRR_FIELDS),
                     "gefs": len(GEFS_LEADS) * len(GEFS_MEMBERS)}
    filenames = {"hrrr": "hrrr_points.parquet", "gefs": "gefs_summary_points.parquet"}
    rows_by_partition = {key: [] for key in expected}
    artifacts_by_partition = {key: [] for key in expected}
    expected_paths: dict[str, set[str]] = {"hrrr": set(), "gefs": set()}
    for partition, dates in expected.items():
        for day_text in dates:
            day = date.fromisoformat(day_text)
            for model in ("hrrr", "gefs"):
                relative = (
                    f"data/normalized/v3_weather/{partition}/date={day_text}/"
                    f"{filenames[model]}"
                )
                expected_paths[model].add(relative)
                record = records[model].get(relative)
                if record is None:
                    raise V3FreezePipelineError(f"Complete {model} manifest omits {day_text}")
                values, artifact = _verify_parquet_record(
                    root, record, expected_path=relative, expected_model=model,
                    expected_rows=expected_rows[model],
                )
                try:
                    validate_daily_coverage(values, model=model, target_date=day)
                except ValueError as exc:
                    raise V3FreezePipelineError(
                        f"Normalized {model} coverage failed for {day_text}: {exc}"
                    ) from exc
                _validate_cycle_contract(values, model=model, target_date=day)
                rows_by_partition[partition].extend(_adapt_forecast_row(row) for row in values)
                artifacts_by_partition[partition].append(artifact)
    for model in ("hrrr", "gefs"):
        if set(records[model]) != expected_paths[model]:
            raise V3FreezePipelineError(f"Complete {model} manifest contains unregistered outputs")
    return rows_by_partition, artifacts_by_partition


def _load_exact_parquet(root: Path, relative: Path, *, expected_sha256: str,
                        expected_rows: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _verify_parquet_record(
        root, {"path": relative.as_posix(), "sha256": expected_sha256,
               **({"rows": expected_rows} if expected_rows is not None else {})},
        expected_path=relative.as_posix(), expected_rows=expected_rows,
    )


def _load_exclusions(root: Path, relative: Path, partition: str) -> dict[str, str]:
    path = _inside(root, relative, "settlement exclusions")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V3FreezePipelineError(f"Missing or invalid exclusions: {relative.as_posix()}") from exc
    if not isinstance(values, list):
        raise V3FreezePipelineError("Settlement exclusions must contain a JSON list")
    result: dict[str, str] = {}
    for row in values:
        if (not isinstance(row, dict) or row.get("partition") != partition
                or not isinstance(row.get("climate_date"), str)
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise V3FreezePipelineError("Malformed settlement exclusion")
        if row["climate_date"] in result:
            raise V3FreezePipelineError("Duplicate settlement exclusion date")
        result[row["climate_date"]] = row["reason"].strip()
    return result


def _binding(component: str, rows: Sequence[Mapping[str, Any]], *,
             artifacts: Sequence[Mapping[str, Any]],
             manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return normalized_input_manifest(
        component, rows,
        upstream_manifests=({
            "normalized_artifacts": list(artifacts),
            "source_manifests": list(manifests),
        },),
    )


def freeze_completed_v3_development(root: Path, *,
                                    destination: Path = DESTINATION) -> dict[str, Any]:
    """Freeze and publish V3 training/calibration/evaluation components.

    All validation and table loading occurs before immutable output files are
    created.  Missing or partial finalized weather evidence therefore cannot
    leave a dataset manifest behind.
    """
    root = _safe_root(root)
    output = _inside(root, destination, "frozen dataset destination")
    source, hrrr, gefs = _require_finalized_source(root)
    forecast_rows, forecast_artifacts = _load_weather_rows(root, hrrr, gefs)

    training_target_manifest = _read_json(
        root, TRAINING_TARGET_MANIFEST, "weather-training target manifest"
    )
    settlement_manifest = _read_json(root, SETTLEMENT_MANIFEST, "settlement manifest")
    market_manifest = _read_json(root, MARKET_MANIFEST, "market normalization manifest")
    minute_manifest = _read_json(root, MINUTE_SOURCE_MANIFEST, "minute-market source manifest")
    local_manifest = _read_json(root, LOCAL_OBSERVATION_MANIFEST, "local-observation manifest")

    if market_manifest.get("source_audit_sha256") != sha256_file(
            _inside(root, MINUTE_SOURCE_MANIFEST, "minute-market source manifest")):
        raise V3FreezePipelineError("Minute-market normalization has a stale source-audit binding")
    download_manifest_path = Path("data/manifests/kalshi_downloads.json")
    if market_manifest.get("download_manifest_sha256") != sha256_file(
            _inside(root, download_manifest_path, "Kalshi download manifest")):
        raise V3FreezePipelineError("Minute-market normalization has a stale download binding")

    if (training_target_manifest.get("status")
            != "TRAINING_TARGETS_COMPLETE_WITH_EXPLICIT_EXCLUSIONS"
            or training_target_manifest.get("target_path") != TRAINING_TARGET.as_posix()
            or training_target_manifest.get("target_count") != 365):
        raise V3FreezePipelineError("Weather-training targets are incomplete or moved")
    training_targets, training_target_artifact = _load_exact_parquet(
        root, TRAINING_TARGET,
        expected_sha256=str(training_target_manifest.get("target_sha256")), expected_rows=365,
    )
    selection = settlement_manifest.get("partitions", {}).get("selection", {})
    if (settlement_manifest.get("status") != "DEVELOPMENT_COMPLETE"
            or selection.get("target_path") != DEVELOPMENT_TARGET.as_posix()
            or selection.get("eligible_targets") != 175):
        raise V3FreezePipelineError("Development settlement targets are incomplete or moved")
    development_targets, development_target_artifact = _load_exact_parquet(
        root, DEVELOPMENT_TARGET, expected_sha256=str(selection.get("target_sha256")),
        expected_rows=175,
    )

    market_output = {
        item.get("path"): item for item in market_manifest.get("outputs", [])
        if isinstance(item, dict)
    }
    if (market_manifest.get("status") != "DEVELOPMENT_ONLY_COMPLETE"
            or set(market_output) != {path.as_posix() for path in MARKET_PATHS}):
        raise V3FreezePipelineError("Normalized minute-market output inventory differs")
    market_rows: list[dict[str, Any]] = []
    market_artifacts = []
    expected_market_counts = {
        MARKET_PATHS[0].as_posix(): market_manifest.get("candle_rows"),
        MARKET_PATHS[1].as_posix(): market_manifest.get("trade_rows"),
    }
    for relative in MARKET_PATHS:
        record = market_output[relative.as_posix()]
        values, artifact = _load_exact_parquet(
            root, relative, expected_sha256=str(record.get("sha256")),
            expected_rows=expected_market_counts[relative.as_posix()],
        )
        market_rows.extend(values)
        market_artifacts.append(artifact)

    local_audit = hrrr.get("local_observations")
    if not isinstance(local_audit, dict) or local_audit.get("substantively_complete") is not True:
        raise V3FreezePipelineError("Complete HRRR component lacks local-observation coverage")
    local_source = local_audit.get("source_manifest")
    if (not isinstance(local_source, dict)
            or local_source.get("path") != LOCAL_OBSERVATION_MANIFEST.as_posix()
            or local_source.get("sha256") != sha256_file(
                _inside(root, LOCAL_OBSERVATION_MANIFEST, "local-observation manifest")
            )):
        raise V3FreezePipelineError("Complete HRRR component has a stale observation binding")
    local_records = {
        item.get("partition"): item for item in local_audit.get("outputs", [])
        if isinstance(item, dict)
    }
    if set(local_records) != set(OBSERVATION_PATHS):
        raise V3FreezePipelineError("Local-observation partition inventory differs")
    observation_rows: dict[str, list[dict[str, Any]]] = {}
    observation_artifacts: dict[str, list[dict[str, Any]]] = {}
    for partition, relative in OBSERVATION_PATHS.items():
        record = local_records[partition]
        values, artifact = _verify_parquet_record(
            root, record, expected_path=relative.as_posix(),
            expected_rows=record.get("rows"),
        )
        allowed_stations = set(REQUIRED_STATIONS) | set(OPTIONAL_STATIONS)
        if any(row.get("partition") != partition or row.get("station") not in allowed_stations
               for row in values):
            raise V3FreezePipelineError("Local-observation rows differ from the registered stations")
        observation_rows[partition] = values
        observation_artifacts[partition] = [artifact]

    training_exclusions = _load_exclusions(root, TRAINING_EXCLUSIONS, "weather_training")
    development_exclusions = _load_exclusions(root, DEVELOPMENT_EXCLUSIONS, "selection")
    if (sha256_file(_inside(root, TRAINING_EXCLUSIONS, "training exclusions"))
            != training_target_manifest.get("exclusions_sha256")
            or sha256_file(_inside(root, DEVELOPMENT_EXCLUSIONS, "development exclusions"))
            != selection.get("exclusions_sha256")):
        raise V3FreezePipelineError("Settlement exclusion artifact changed")

    source_record = _manifest_envelope(root, SOURCE_MANIFEST, source)
    hrrr_record = _manifest_envelope(root, HRRR_MANIFEST, hrrr)
    gefs_record = _manifest_envelope(root, GEFS_MANIFEST, gefs)
    training_target_record = _manifest_envelope(
        root, TRAINING_TARGET_MANIFEST, training_target_manifest
    )
    settlement_record = _manifest_envelope(root, SETTLEMENT_MANIFEST, settlement_manifest)
    market_record = _manifest_envelope(root, MARKET_MANIFEST, market_manifest)
    minute_record = _manifest_envelope(root, MINUTE_SOURCE_MANIFEST, minute_manifest)
    local_record = _manifest_envelope(root, LOCAL_OBSERVATION_MANIFEST, local_manifest)

    tables = {
        "training_settlement": training_targets,
        "training_observation": observation_rows["weather_training"],
        "training_forecast": forecast_rows["weather_training"],
        "settlement": development_targets,
        "market": market_rows,
        "observation": observation_rows["selection"],
        "forecast": forecast_rows["selection"],
    }
    bindings = {
        "training_settlement": _binding(
            "training_settlement", training_targets,
            artifacts=[training_target_artifact], manifests=[training_target_record],
        ),
        "training_observation": _binding(
            "training_observation", observation_rows["weather_training"],
            artifacts=observation_artifacts["weather_training"],
            manifests=[hrrr_record, local_record],
        ),
        "training_forecast": _binding(
            "training_forecast", forecast_rows["weather_training"],
            artifacts=forecast_artifacts["weather_training"],
            manifests=[source_record, hrrr_record, gefs_record],
        ),
        "settlement": _binding(
            "settlement", development_targets,
            artifacts=[development_target_artifact], manifests=[settlement_record],
        ),
        "market": _binding(
            "market", market_rows, artifacts=market_artifacts,
            manifests=[market_record, minute_record],
        ),
        "observation": _binding(
            "observation", observation_rows["selection"],
            artifacts=observation_artifacts["selection"],
            manifests=[hrrr_record, local_record],
        ),
        "forecast": _binding(
            "forecast", forecast_rows["selection"],
            artifacts=forecast_artifacts["selection"],
            manifests=[source_record, hrrr_record, gefs_record],
        ),
    }

    manifest = build_frozen_development_dataset(
        output,
        training_settlement_rows=tables["training_settlement"],
        training_settlement_manifest=bindings["training_settlement"],
        training_observation_rows=tables["training_observation"],
        training_observation_manifest=bindings["training_observation"],
        training_forecast_rows=tables["training_forecast"],
        training_forecast_manifest=bindings["training_forecast"],
        settlement_rows=tables["settlement"], settlement_manifest=bindings["settlement"],
        market_rows=tables["market"], market_manifest=bindings["market"],
        observation_rows=tables["observation"], observation_manifest=bindings["observation"],
        forecast_rows=tables["forecast"], forecast_manifest=bindings["forecast"],
        decision_times_utc=DECISION_TIMES_UTC,
        required_stations=REQUIRED_STATIONS,
        optional_observation_stations=OPTIONAL_STATIONS,
        required_forecast_models=("hrrr", "gefs"),
        minimum_forecast_records_per_model={"hrrr": 24, "gefs": 16},
        required_forecast_fields_by_model={
            "hrrr": HRRR_FIELDS, "gefs": ("temperature_2m",),
        },
        required_forecast_members_by_model={
            "hrrr": (None,), "gefs": GEFS_MEMBERS,
        },
        training_exclusions=training_exclusions,
        development_exclusions=development_exclusions,
    )
    verified = verify_frozen_development_dataset(output)
    components = publish_frozen_dataset_component_manifests(root, output)
    if (components["frozen_dataset"].get("dataset_id") != manifest.get("dataset_id")
            or components["five_fold_split"].get("folds_id") != verified.get("folds_id")):
        raise V3FreezePipelineError("Published component identities differ from the verified freeze")
    return {
        "status": "V3_DEVELOPMENT_DATASET_FROZEN_AND_VERIFIED",
        "dataset_id": verified["dataset_id"],
        "folds_id": verified["folds_id"],
        "destination": output.relative_to(root).as_posix(),
        "weather_training_feature_rows": verified["weather_training_feature_rows"],
        "calibration_feature_rows": verified["calibration_feature_rows"],
        "evaluation_feature_rows": verified["evaluation_feature_rows"],
        "required_stations": list(REQUIRED_STATIONS),
        "optional_stations_preserved_when_available": list(OPTIONAL_STATIONS),
        "network_used": False,
        "protected_final_read": False,
        "campaign_or_readiness_ticket_issued": False,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    args = parser.parse_args(argv)
    print(json.dumps(
        freeze_completed_v3_development(args.root, destination=args.destination),
        indent=2, sort_keys=True,
    ))


if __name__ == "__main__":
    main()
