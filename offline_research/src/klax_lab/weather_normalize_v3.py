"""Cache-only normalization and conservative as-of audit for V3 weather.

The GRIB decoder preserves nominal model-cycle and archived HTTP metadata but
does not turn either into historical publication proof.  This module applies
the separately registered availability policy: a row is usable from the later
of six hours after its nominal model cycle and the archived object's HTTP
``Last-Modified`` timestamp, when present.  The timestamp remains evidence
about the archived object, not proof of the original publication instant.

Only the fixed 12:00, 15:00, and 18:00 UTC decision schedules are admitted.
The driver reads the immutable local cache, writes one atomic normalized
partition per model and day, and resumes only from outputs whose hashes and
row-level coverage still verify.  It contains no network or acquisition path.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping

from .provenance import sha256_file, write_json
from .weather_decode_v3 import (
    KLAX_POINT,
    SpatialPoint,
    _write_parquet_atomic,
    decode_weather_plan,
)
from .weather_sources_v3 import build_revised_daily_plans


UTC = timezone.utc
LEGACY_POLICY_ID = "nominal_model_cycle_plus_6h_v1"
POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"
POLICY_CONFIG_PATH = Path("configs/v3_weather_availability_policy.json")
POLICY_CONFIG_SHA256 = "e523891139133deea2cee112dfe53b940a0bcb7d729d62b7c40c7fe9ec9fc9be"
POLICY_AMENDMENT_PATH = Path(
    "data/manifests/v3_weather_availability_policy_v2_amendment.json")
POLICY_AMENDMENT_SHA256 = "546e812644080b44c434ea8da6e772f298387dbbe6bd1ea6f5ebdf4d660d711a"
PRE_AMENDMENT_PROGRESS_PATH = Path(
    "data/manifests/v3_weather_normalization_progress.pre-availability-v2.json")
PRE_AMENDMENT_PROGRESS_SHA256 = (
    "819a6669502226807e1276b09b26d0963e288f60e0b22c77a334c45c554fd731")
AVAILABILITY_DELAY = timedelta(hours=6)
DECISION_HOURS_UTC = (12, 15, 18)
TRAINING_START = date(2024, 1, 1)
TRAINING_END = date(2024, 12, 31)
SELECTION_START = date(2025, 1, 5)
SELECTION_END = date(2025, 6, 30)

HRRR_LEADS = (2, 8, 14, 20)
HRRR_FIELDS = (
    "temperature_2m",
    "total_cloud_cover",
    "cloud_ceiling",
    "wind_u_10m",
    "wind_v_10m",
    "mean_sea_level_pressure",
)
GEFS_LEADS = (9, 12, 15, 18, 21, 24, 27, 30)
GEFS_MEMBERS = ("avg", "spr")


def _registered_partition(value: date) -> str:
    if TRAINING_START <= value <= TRAINING_END:
        return "weather_training"
    if SELECTION_START <= value <= SELECTION_END:
        return "selection"
    if value >= date(2025, 7, 1):
        raise ValueError("Protected-final weather data cannot be normalized")
    raise ValueError("Weather date is outside the registered V3 partitions")


def registered_weather_dates() -> tuple[date, ...]:
    training = tuple(
        TRAINING_START + timedelta(days=offset)
        for offset in range((TRAINING_END - TRAINING_START).days + 1)
    )
    selection = tuple(
        SELECTION_START + timedelta(days=offset)
        for offset in range((SELECTION_END - SELECTION_START).days + 1)
    )
    dates = training + selection
    if len(dates) != 543 or len(set(dates)) != 543:
        raise ValueError("Registered V3 weather calendar differs from 543 distinct days")
    return dates


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def verify_availability_policy_registration(root: Path) -> dict[str, Any]:
    """Verify the pre-campaign v2 registration and immutable failure snapshot."""
    root = _safe_root(root)
    records = (
        (POLICY_CONFIG_PATH, POLICY_CONFIG_SHA256, "availability policy config"),
        (POLICY_AMENDMENT_PATH, POLICY_AMENDMENT_SHA256, "availability amendment"),
        (PRE_AMENDMENT_PROGRESS_PATH, PRE_AMENDMENT_PROGRESS_SHA256,
         "pre-amendment normalization snapshot"),
    )
    for relative, expected_hash, label in records:
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ValueError(f"Registered {label} is missing or changed")
    config = json.loads((root / POLICY_CONFIG_PATH).read_text(encoding="utf-8-sig"))
    amendment = json.loads((root / POLICY_AMENDMENT_PATH).read_text(encoding="utf-8-sig"))
    snapshot = json.loads(
        (root / PRE_AMENDMENT_PROGRESS_PATH).read_text(encoding="utf-8-sig"))
    if (config.get("status") != "REGISTERED_PRE_CAMPAIGN"
            or config.get("policy_id") != POLICY_ID
            or config.get("effective_available_at")
            != "max(nominal_model_cycle_plus_6_hours, archived_HTTP_Last-Modified_if_present)"
            or config.get("admitted_decision_times_utc") != ["12:00", "15:00", "18:00"]
            or config.get("protected_final_read") is not False
            or config.get("amendment") != {
                "path": POLICY_AMENDMENT_PATH.as_posix(),
                "sha256": POLICY_AMENDMENT_SHA256,
            }
            or config.get("pre_amendment_progress_snapshot") != {
                "path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
                "sha256": PRE_AMENDMENT_PROGRESS_SHA256,
            }):
        raise ValueError("Registered availability policy config differs")
    if (amendment.get("status") != "REGISTERED_BEFORE_CODE_CHANGE_OR_CAMPAIGN"
            or amendment.get("amendment_id") != "v3_weather_availability_policy_v2"
            or amendment.get("amended_policy", {}).get("policy_id") != POLICY_ID
            or amendment.get("scientific_boundaries", {}).get("protected_final_read")
            is not False
            or amendment.get("scientific_boundaries", {}).get("campaign_started") is not False
            or amendment.get("scientific_boundaries", {}).get(
                "outcomes_or_returns_consulted_for_amendment") is not False
            or amendment.get("pre_amendment_progress_snapshot", {}).get("sha256")
            != PRE_AMENDMENT_PROGRESS_SHA256
            or amendment.get("evidence_summary", {}).get("affected_field_objects") != 16):
        raise ValueError("Registered availability amendment differs")
    if (snapshot.get("status") != "PARTIAL_CACHE_ONLY_NORMALIZATION"
            or snapshot.get("registered_days") != 543
            or snapshot.get("normalized_days") != 542
            or snapshot.get("integrity_failure_days") != 1
            or snapshot.get("protected_final_read") is not False):
        raise ValueError("Pre-amendment normalization snapshot differs")
    return availability_policy_record()


def apply_conservative_availability(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the registered max bound without claiming publication-time proof."""
    audited: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        if row.get("model") not in {"hrrr", "gefs"}:
            raise ValueError("Availability policy accepts only HRRR and GEFS rows")
        if row.get("historical_availability_proven") is not False:
            raise ValueError("Decoder must not claim historical publication-time proof")
        if row.get("as_of_validated") is not False:
            raise ValueError("Availability policy must receive unaudited decoder rows")
        climate_date = date.fromisoformat(str(row.get("climate_date")))
        _registered_partition(climate_date)
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        reference = _parse_utc(
            row.get("forecast_reference_time_utc"), "forecast reference time"
        )
        if nominal != reference:
            raise ValueError("Nominal issue time differs from the decoded model cycle")
        nominal_bound = nominal + AVAILABILITY_DELAY
        archived_http = row.get("source_last_modified_at_utc")
        archived_http_time: datetime | None = None
        if archived_http is not None:
            archived_http_time = _parse_utc(archived_http, "archived HTTP Last-Modified")
        conservative = max(
            value for value in (nominal_bound, archived_http_time) if value is not None)
        decisions = [datetime.combine(climate_date, time(hour), UTC)
                     for hour in DECISION_HOURS_UTC]
        eligible = [decision.strftime("%H:%M") for decision in decisions
                    if conservative <= decision]
        if eligible != ["12:00", "15:00", "18:00"]:
            raise ValueError("Weather row is unavailable for every admitted decision schedule")

        # Preserve decoder timing fields verbatim.  The new effective field is
        # policy-derived and does not rewrite archived HTTP evidence.
        row.update({
            "availability_policy_id": POLICY_ID,
            "availability_delay_hours": 6,
            "effective_information_available_at_utc": _iso(conservative),
            "effective_information_availability_basis": (
                "max_of_nominal_model_cycle_plus_6_hours_and_"
                "archived_HTTP_Last-Modified_if_present"
            ),
            "eligible_decision_times_utc": eligible,
            "historical_availability_proven": False,
            "as_of_validated": True,
            "as_of_validation_basis": "registered_conservative_bound_not_publication_proof",
        })
        audited.append(row)
    if not audited:
        raise ValueError("Availability audit requires at least one decoded row")
    return audited


def _expected_keys(model: str, point_ids: tuple[str, ...]) -> set[tuple[Any, ...]]:
    if model == "hrrr":
        return {
            (lead, None, field, point_id)
            for lead in HRRR_LEADS for field in HRRR_FIELDS for point_id in point_ids
        }
    if model == "gefs":
        return {
            (lead, member, "temperature_2m", point_id)
            for lead in GEFS_LEADS for member in GEFS_MEMBERS for point_id in point_ids
        }
    raise ValueError("Unsupported normalized weather model")


def validate_daily_coverage(rows: Iterable[dict[str, Any]], *, model: str,
                            target_date: date,
                            points: Iterable[SpatialPoint] = (KLAX_POINT,),
                            expected_policy_id: str = POLICY_ID) -> dict[str, Any]:
    """Validate the exact registered day/lead/field/point inventory."""
    values = list(rows)
    point_ids = tuple(point.point_id for point in points)
    if not point_ids or len(set(point_ids)) != len(point_ids):
        raise ValueError("Coverage audit requires unique registered points")
    expected = _expected_keys(model, point_ids)
    actual = [
        (row.get("lead_hours"), row.get("member_id"), row.get("field_id"),
         row.get("point_id"))
        for row in values
    ]
    if len(actual) != len(set(actual)):
        raise ValueError("Normalized weather day contains duplicate identities")
    if set(actual) != expected:
        missing = sorted(expected - set(actual), key=str)
        extra = sorted(set(actual) - expected, key=str)
        raise ValueError(f"Normalized {model} coverage differs; missing={missing}; extra={extra}")
    partition = _registered_partition(target_date)
    expected_times = ["12:00", "15:00", "18:00"]
    for row in values:
        if (row.get("model") != model
                or row.get("climate_date") != target_date.isoformat()
                or row.get("partition") != partition
                or row.get("availability_policy_id") != expected_policy_id
                or row.get("historical_availability_proven") is not False
                or row.get("as_of_validated") is not True
                or row.get("eligible_decision_times_utc") != expected_times):
            raise ValueError("Normalized weather row fails identity or as-of policy checks")
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        reference = _parse_utc(
            row.get("forecast_reference_time_utc"), "forecast reference time")
        effective = _parse_utc(
            row.get("effective_information_available_at_utc"), "effective availability")
        archived = row.get("source_last_modified_at_utc")
        archived_time = (_parse_utc(archived, "archived HTTP Last-Modified")
                         if archived is not None else None)
        nominal_bound = nominal + AVAILABILITY_DELAY
        if expected_policy_id == POLICY_ID:
            expected_available = max(
                value for value in (nominal_bound, archived_time) if value is not None)
        elif expected_policy_id == LEGACY_POLICY_ID:
            expected_available = nominal_bound
            if archived_time is not None and archived_time > expected_available:
                raise ValueError("Legacy normalized row contradicts its v1 availability policy")
        else:
            raise ValueError("Unsupported normalized weather availability policy")
        if (reference != nominal or effective != expected_available
                or row.get("availability_delay_hours") != 6):
            raise ValueError("Normalized weather row has inconsistent policy timing")
        if any(token in str(row.get("source_url", "")).casefold()
               for token in ("latest", "current", "recent")):
            raise ValueError("Normalized weather row names a current/latest endpoint")
        if row.get("is_missing") and row.get("field_id") != "cloud_ceiling":
            raise ValueError("Required normalized weather value is missing")
    return {
        "model": model,
        "climate_date": target_date.isoformat(),
        "partition": partition,
        "rows": len(values),
        "required_identity_count": len(expected),
        "coverage_complete": True,
        "decision_times_utc": expected_times,
    }


def _safe_root(project_root: Path) -> Path:
    root = Path(project_root).resolve()
    if "protected_final" in {part.casefold() for part in root.parts}:
        raise ValueError("Weather normalizer cannot run inside protected_final")
    return root


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    resolved.relative_to(root)
    if "protected_final" in {part.casefold() for part in resolved.parts}:
        raise ValueError("Protected-final paths are forbidden")
    return resolved.relative_to(root).as_posix()


def _output_record(root: Path, path: Path, model: str, rows: int) -> dict[str, Any]:
    return {
        "model": model,
        "path": _relative(root, path),
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _day_manifest_record(root: Path, target_date: date) -> dict[str, Any]:
    manifest = _day_paths(root, target_date)[2]
    return {
        "path": _relative(root, manifest),
        "bytes": manifest.stat().st_size,
        "sha256": sha256_file(manifest),
    }


def _day_paths(root: Path, target_date: date) -> tuple[Path, Path, Path]:
    partition = _registered_partition(target_date)
    folder = root / "data/normalized/v3_weather" / partition / f"date={target_date.isoformat()}"
    return (
        folder / "hrrr_points.parquet",
        folder / "gefs_summary_points.parquet",
        folder / "normalization_manifest.json",
    )


def _migration_paths(root: Path, target_date: date) -> tuple[Path, Path, Path]:
    folder = _day_paths(root, target_date)[0].parent
    return (
        folder,
        folder.with_name(folder.name + ".policy-v2-stage"),
        folder.with_name(folder.name + ".policy-v1-backup"),
    )


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq
    return pq.read_table(path).to_pylist()


def _verify_day_outputs(root: Path, target_date: date,
                        points: tuple[SpatialPoint, ...], manifest: Mapping[str, Any],
                        hrrr_path: Path, gefs_path: Path, *,
                        expected_policy_id: str,
                        records_name_final_paths: bool = True) -> dict[str, Any]:
    outputs = {record.get("model"): record for record in manifest.get("outputs", [])}
    if set(outputs) != {"hrrr", "gefs"}:
        raise ValueError("Normalized weather day output inventory differs")
    final_hrrr, final_gefs, _ = _day_paths(root, target_date)
    for model, path, final_path in (
            ("hrrr", hrrr_path, final_hrrr), ("gefs", gefs_path, final_gefs)):
        record = outputs[model]
        expected_path = final_path if records_name_final_paths else path
        if (record.get("path") != _relative(root, expected_path)
                or record.get("bytes") != path.stat().st_size
                or record.get("sha256") != sha256_file(path)):
            raise ValueError("Normalized weather day output hash differs")
        coverage = validate_daily_coverage(
            _read_parquet(path), model=model, target_date=target_date, points=points,
            expected_policy_id=expected_policy_id,
        )
        if record.get("rows") != coverage["rows"]:
            raise ValueError("Normalized weather day row count differs")
    return dict(manifest)


def _validate_day_manifest_identity(manifest: Mapping[str, Any], target_date: date,
                                    expected_policy_id: str) -> None:
    if (manifest.get("status") != "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
            or manifest.get("climate_date") != target_date.isoformat()
            or manifest.get("availability_policy", {}).get("policy_id")
            != expected_policy_id
            or manifest.get("protected_final_read") is not False
            or manifest.get("network_used") is not False):
        raise ValueError("Normalized weather day manifest differs from the registered policy")


def _validate_v2_lineage(root: Path, target_date: date,
                         manifest: Mapping[str, Any]) -> None:
    """Anchor every committed v2 day to the immutable pre-amendment inventory."""
    snapshot = json.loads(
        (root / PRE_AMENDMENT_PROGRESS_PATH).read_text(encoding="utf-8-sig"))
    prior_by_date = {item.get("date"): item for item in snapshot.get("days", [])}
    prior = prior_by_date.get(target_date.isoformat())
    migration = manifest.get("policy_migration")
    if prior is None:
        if (target_date != date(2025, 6, 28)
                or manifest.get("migrated_from_verified_v1_output") is not False
                or migration is not None):
            raise ValueError("Fresh v2 normalized day lineage differs")
        return
    if (manifest.get("migrated_from_verified_v1_output") is not True
            or not isinstance(migration, dict)
            or migration.get("source_policy_id") != LEGACY_POLICY_ID
            or migration.get("target_policy_id") != POLICY_ID
            or migration.get("transformed_without_grib_redecode") is not True
            or migration.get("amendment_path") != POLICY_AMENDMENT_PATH.as_posix()
            or migration.get("amendment_sha256") != POLICY_AMENDMENT_SHA256
            or migration.get("source_outputs") != prior.get("outputs")
            or manifest.get("partition") != prior.get("partition")):
        raise ValueError("Migrated v2 normalized day lineage differs")


def _validate_fresh_v2_decode(root: Path, target_date: date) -> None:
    snapshot = json.loads(
        (root / PRE_AMENDMENT_PROGRESS_PATH).read_text(encoding="utf-8-sig"))
    prior_dates = {item.get("date") for item in snapshot.get("days", [])}
    if target_date != date(2025, 6, 28) or target_date.isoformat() in prior_dates:
        raise ValueError("Only the registered June 28 gap may receive a fresh v2 decode")


def _stage_output_record(root: Path, staged_path: Path, final_path: Path,
                         model: str, rows: int) -> dict[str, Any]:
    return {
        "model": model,
        "path": _relative(root, final_path),
        "rows": rows,
        "bytes": staged_path.stat().st_size,
        "sha256": sha256_file(staged_path),
    }


def _verify_staged_migration(root: Path, target_date: date,
                             points: tuple[SpatialPoint, ...], stage: Path) -> dict[str, Any]:
    manifest_path = stage / "normalization_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _validate_day_manifest_identity(manifest, target_date, POLICY_ID)
    migration = manifest.get("policy_migration")
    if (not isinstance(migration, dict)
            or migration.get("source_policy_id") != LEGACY_POLICY_ID
            or migration.get("target_policy_id") != POLICY_ID
            or migration.get("transformed_without_grib_redecode") is not True
            or migration.get("amendment_sha256") != POLICY_AMENDMENT_SHA256):
        raise ValueError("Staged weather policy migration provenance differs")
    _validate_v2_lineage(root, target_date, manifest)
    return _verify_day_outputs(
        root, target_date, points, manifest,
        stage / "hrrr_points.parquet", stage / "gefs_summary_points.parquet",
        expected_policy_id=POLICY_ID,
    )


def _recover_interrupted_migration(root: Path, target_date: date,
                                   points: tuple[SpatialPoint, ...]) -> None:
    folder, stage, backup = _migration_paths(root, target_date)
    if backup.exists():
        if folder.exists():
            manifest_path = folder / "normalization_manifest.json"
            policy = None
            manifest: dict[str, Any] | None = None
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                policy = manifest.get("availability_policy", {}).get("policy_id")
            if policy != POLICY_ID:
                raise ValueError("Interrupted weather policy migration has ambiguous live output")
            _validate_day_manifest_identity(manifest or {}, target_date, POLICY_ID)
            _validate_v2_lineage(root, target_date, manifest or {})
            _verify_day_outputs(
                root, target_date, points, manifest or {},
                folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet",
                expected_policy_id=POLICY_ID,
            )
            if stage.exists():
                shutil.rmtree(stage)
            shutil.rmtree(backup)
            return
        if stage.exists():
            _verify_staged_migration(root, target_date, points, stage)
            stage.replace(folder)
            shutil.rmtree(backup)
            return
        backup.replace(folder)
        return
    if stage.exists():
        if not folder.exists():
            raise ValueError("Staged weather policy migration lacks its verified v1 source")
        manifest = json.loads(
            (folder / "normalization_manifest.json").read_text(encoding="utf-8"))
        policy = manifest.get("availability_policy", {}).get("policy_id")
        if policy == POLICY_ID:
            _validate_day_manifest_identity(manifest, target_date, POLICY_ID)
            _validate_v2_lineage(root, target_date, manifest)
            _verify_day_outputs(
                root, target_date, points, manifest,
                folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet",
                expected_policy_id=POLICY_ID,
            )
            shutil.rmtree(stage)
            return
        if policy != LEGACY_POLICY_ID:
            raise ValueError("Staged weather policy migration source policy differs")
        _validate_day_manifest_identity(manifest, target_date, LEGACY_POLICY_ID)
        _verify_day_outputs(
            root, target_date, points, manifest,
            folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet",
            expected_policy_id=LEGACY_POLICY_ID,
        )
        try:
            _verify_staged_migration(root, target_date, points, stage)
        except Exception:
            # A crash before the staged manifest commit leaves no authoritative
            # v2 artifact.  Once the live v1 day revalidates exactly, discarding
            # only that incomplete derived stage is safe and deterministic.
            shutil.rmtree(stage)
            return
        folder.replace(backup)
        stage.replace(folder)
        shutil.rmtree(backup)


def _migration_input_row(row: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(row)
    for key in (
        "availability_policy_id", "availability_delay_hours",
        "effective_information_available_at_utc",
        "effective_information_availability_basis", "eligible_decision_times_utc",
        "as_of_validation_basis",
    ):
        value.pop(key, None)
    value["historical_availability_proven"] = False
    value["as_of_validated"] = False
    return value


def _migrate_legacy_day(root: Path, target_date: date,
                        points: tuple[SpatialPoint, ...],
                        manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate v1 outputs and atomically replace the day directory with v2."""
    hrrr_path, gefs_path, _ = _day_paths(root, target_date)
    _validate_day_manifest_identity(manifest, target_date, LEGACY_POLICY_ID)
    verified = _verify_day_outputs(
        root, target_date, points, manifest, hrrr_path, gefs_path,
        expected_policy_id=LEGACY_POLICY_ID,
    )
    source_outputs = [dict(record) for record in verified["outputs"]]
    transformed: dict[str, list[dict[str, Any]]] = {}
    coverage: dict[str, dict[str, Any]] = {}
    for model, path in (("hrrr", hrrr_path), ("gefs", gefs_path)):
        rows = apply_conservative_availability(
            _migration_input_row(row) for row in _read_parquet(path))
        transformed[model] = rows
        coverage[model] = validate_daily_coverage(
            rows, model=model, target_date=target_date, points=points)

    folder, stage, backup = _migration_paths(root, target_date)
    if stage.exists() or backup.exists():
        raise ValueError("Weather policy migration workspace is not clean")
    stage.mkdir(parents=False)
    staged_hrrr = stage / "hrrr_points.parquet"
    staged_gefs = stage / "gefs_summary_points.parquet"
    _write_parquet_atomic(staged_hrrr, transformed["hrrr"])
    _write_parquet_atomic(staged_gefs, transformed["gefs"])
    outputs = [
        _stage_output_record(root, staged_hrrr, hrrr_path, "hrrr",
                             len(transformed["hrrr"])),
        _stage_output_record(root, staged_gefs, gefs_path, "gefs",
                             len(transformed["gefs"])),
    ]
    report = {
        **dict(manifest),
        "schema_version": 2,
        "resumed_from_verified_output": False,
        "migrated_from_verified_v1_output": True,
        "availability_policy": availability_policy_record(),
        "hrrr_coverage": coverage["hrrr"],
        "gefs_coverage": coverage["gefs"],
        "outputs": outputs,
        "policy_migration": {
            "source_policy_id": LEGACY_POLICY_ID,
            "target_policy_id": POLICY_ID,
            "transformed_without_grib_redecode": True,
            "source_outputs": source_outputs,
            "amendment_path": POLICY_AMENDMENT_PATH.as_posix(),
            "amendment_sha256": POLICY_AMENDMENT_SHA256,
        },
        "limitations": [
            "Archived HTTP Last-Modified is a conservative lower bound on the archived object state, not original publication-time proof",
            "The v1 decoded source rows were hash-verified and migrated without GRIB re-decode",
            "GEFS mean/spread does not preserve member-level skew or multimodality",
        ],
    }
    write_json(stage / "normalization_manifest.json", report)
    _verify_staged_migration(root, target_date, points, stage)
    folder.replace(backup)
    stage.replace(folder)
    committed = json.loads(
        (folder / "normalization_manifest.json").read_text(encoding="utf-8"))
    _verify_day_outputs(
        root, target_date, points, committed,
        folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet",
        expected_policy_id=POLICY_ID,
    )
    shutil.rmtree(backup)
    return {**report, "migration_performed_now": True}


def _verify_resumable_day(root: Path, target_date: date,
                          points: tuple[SpatialPoint, ...]) -> dict[str, Any] | None:
    _recover_interrupted_migration(root, target_date, points)
    hrrr_path, gefs_path, manifest_path = _day_paths(root, target_date)
    if not manifest_path.exists() and not hrrr_path.exists() and not gefs_path.exists():
        return None
    if not manifest_path.is_file() or not hrrr_path.is_file() or not gefs_path.is_file():
        # Derived outputs are reproducible from immutable raw fragments.  The
        # manifest is the commit marker, so a crash before all three files are
        # present is safely recovered by atomically regenerating the day.
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    policy = manifest.get("availability_policy", {}).get("policy_id")
    if policy == LEGACY_POLICY_ID:
        return _migrate_legacy_day(root, target_date, points, manifest)
    _validate_day_manifest_identity(manifest, target_date, POLICY_ID)
    _validate_v2_lineage(root, target_date, manifest)
    return _verify_day_outputs(
        root, target_date, points, manifest, hrrr_path, gefs_path,
        expected_policy_id=POLICY_ID,
    )


def normalize_weather_day(project_root: Path, target_date: date, *,
                          today: date | None = None,
                          points: Iterable[SpatialPoint] = (KLAX_POINT,)) -> dict[str, Any]:
    """Normalize one registered day from the local verified cache only."""
    root = _safe_root(project_root)
    _registered_partition(target_date)
    verify_availability_policy_registration(root)
    point_values = tuple(points)
    resumed = _verify_resumable_day(root, target_date, point_values)
    if resumed is not None:
        if resumed.get("migration_performed_now") is True:
            return resumed
        return {**resumed, "resumed_from_verified_output": True,
                "migration_performed_now": False}

    _validate_fresh_v2_decode(root, target_date)

    hrrr_plan, gefs_plan = build_revised_daily_plans(target_date, today=today)
    cache_root = root / "data/raw/weather_v3/compatibility"
    hrrr_rows = apply_conservative_availability(
        decode_weather_plan(hrrr_plan, cache_root, points=point_values)
    )
    gefs_rows = apply_conservative_availability(
        decode_weather_plan(gefs_plan, cache_root, points=point_values)
    )
    hrrr_coverage = validate_daily_coverage(
        hrrr_rows, model="hrrr", target_date=target_date, points=point_values,
    )
    gefs_coverage = validate_daily_coverage(
        gefs_rows, model="gefs", target_date=target_date, points=point_values,
    )
    hrrr_path, gefs_path, manifest_path = _day_paths(root, target_date)
    _write_parquet_atomic(hrrr_path, hrrr_rows)
    _write_parquet_atomic(gefs_path, gefs_rows)
    outputs = [
        _output_record(root, hrrr_path, "hrrr", len(hrrr_rows)),
        _output_record(root, gefs_path, "gefs", len(gefs_rows)),
    ]
    report = {
        "schema_version": 2,
        "component": "weather_normalized_day",
        "status": "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND",
        "climate_date": target_date.isoformat(),
        "partition": _registered_partition(target_date),
        "protected_final_read": False,
        "network_used": False,
        "resumed_from_verified_output": False,
        "migrated_from_verified_v1_output": False,
        "migration_performed_now": False,
        "availability_policy": availability_policy_record(),
        "hrrr_coverage": hrrr_coverage,
        "gefs_coverage": gefs_coverage,
        "outputs": outputs,
        "limitations": [
            "The max bound is a registered conservative assumption, not proof of publication time",
            "Archived HTTP Last-Modified is retained and can only delay effective availability",
            "GEFS mean/spread does not preserve member-level skew or multimodality",
        ],
    }
    write_json(manifest_path, report)
    return report


def availability_policy_record() -> dict[str, Any]:
    return {
        "policy_id": POLICY_ID,
        "effective_available_at": (
            "max(nominal_model_cycle_plus_6_hours, "
            "archived_HTTP_Last-Modified_if_present)"),
        "delay_hours": 6,
        "admitted_decision_times_utc": ["12:00", "15:00", "18:00"],
        "archived_http_last_modified_rule": "can_only_delay_effective_availability",
        "missing_archived_http_last_modified_fallback": "nominal_model_cycle_plus_6_hours",
        "historical_publication_time_proven": False,
        "policy_config": {
            "path": POLICY_CONFIG_PATH.as_posix(), "sha256": POLICY_CONFIG_SHA256},
        "amendment": {
            "path": POLICY_AMENDMENT_PATH.as_posix(), "sha256": POLICY_AMENDMENT_SHA256},
        "pre_amendment_progress_snapshot": {
            "path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
            "sha256": PRE_AMENDMENT_PROGRESS_SHA256,
        },
        "basis": [
            "GOALS.md requires a predeclared conservative delay when historical availability is uncertain",
            "The v2 amendment was registered after source-metadata contradiction and before code change or campaign",
            "The max rule never advances availability relative to nominal cycle plus six hours",
        ],
    }


def _audit_local_observations(root: Path,
                              dates_by_partition: Mapping[str, set[str]]) -> dict[str, Any]:
    """Independently verify complete KLAX as-of coverage for admitted schedules."""
    import pyarrow.parquet as pq

    manifest_path = root / "data/manifests/v3_local_observations_partial.json"
    if not manifest_path.is_file():
        return {"substantively_complete": False, "reason": "local observation manifest missing"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("protected_final_read") is not False
            or manifest.get("historical_receipt_time_proven") is not False
            or manifest.get("local_observation_decision_times_admitted")
            != ["12:00", "15:00", "18:00"]):
        return {"substantively_complete": False,
                "reason": "local observation manifest policy differs"}
    output_records = []
    partition_coverage: dict[str, Any] = {}
    required_columns = {
        "climate_date", "partition", "station", "available_at", "as_of_validated",
        "protected_final", "historical_receipt_time_proven", "source_sha256",
    }
    for partition, expected_dates in dates_by_partition.items():
        declared = manifest.get("partitions", {}).get(partition)
        if not isinstance(declared, dict):
            return {"substantively_complete": False,
                    "reason": f"local observation partition {partition} missing"}
        path = (root / str(declared.get("path"))).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return {"substantively_complete": False, "reason": "local observation path escapes root"}
        if ("protected_final" in {part.casefold() for part in path.parts}
                or not path.is_file()):
            return {"substantively_complete": False,
                    "reason": "local observation output missing or protected"}
        table = pq.read_table(path)
        if not required_columns <= set(table.column_names) or table.num_rows != declared.get("rows"):
            return {"substantively_complete": False,
                    "reason": "local observation schema or row count differs"}
        rows = table.select(sorted(required_columns)).to_pylist()
        if any(row["partition"] != partition or row["protected_final"] is not False
               or row["as_of_validated"] is not True
               or row["historical_receipt_time_proven"] is not False
               for row in rows):
            return {"substantively_complete": False,
                    "reason": "local observation row boundary or timing flags differ"}
        klax = [row for row in rows if row["station"] == "KLAX"
                and row["climate_date"] in expected_dates]
        coverage: dict[str, int] = {}
        for clock in ("12:00", "15:00", "18:00"):
            hour = int(clock[:2])
            covered = {
                row["climate_date"] for row in klax
                if _parse_utc(row["available_at"], "local observation availability")
                <= datetime.combine(date.fromisoformat(row["climate_date"]), time(hour), UTC)
            }
            coverage[clock] = len(covered)
            if covered != expected_dates:
                return {"substantively_complete": False,
                        "reason": f"KLAX {partition} coverage incomplete at {clock}",
                        "covered_days": len(covered), "expected_days": len(expected_dates)}
        output_records.append({
            "partition": partition,
            "path": _relative(root, path),
            "rows": table.num_rows,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
        partition_coverage[partition] = {
            "expected_days": len(expected_dates),
            "klax_as_of_days": coverage,
        }
    return {
        "substantively_complete": True,
        "source_manifest": {
            "path": _relative(root, manifest_path),
            "sha256": sha256_file(manifest_path),
        },
        "availability_basis": manifest.get("availability_basis"),
        "historical_receipt_time_proven": False,
        "partition_coverage": partition_coverage,
        "outputs": output_records,
        "nearby_stations_optional_and_not_required_for_complete_klax_schedule": True,
    }


def _publish_complete_components(root: Path, days: list[dict[str, Any]],
                                 dates_by_partition: Mapping[str, set[str]],
                                 local_audit: dict[str, Any]) -> list[str]:
    if not local_audit.get("substantively_complete"):
        raise ValueError("Complete HRRR component requires substantive local-observation coverage")
    hrrr_outputs, gefs_outputs = [], []
    for day in days:
        outputs = {record["model"]: record for record in day["outputs"]}
        hrrr_outputs.append(outputs["hrrr"])
        gefs_outputs.append(outputs["gefs"])
    common = {
        "schema_version": 1,
        "status": "COMPLETE_REGISTERED_COVERAGE_WITH_CONSERVATIVE_ASOF_BOUND",
        "readiness_component_pass": True,
        "protected_final_read": False,
        "network_used_for_normalization": False,
        "historical_publication_time_proven": False,
        "availability_policy": availability_policy_record(),
        "registered_days": len(days),
        "partitions": {key: {"days": len(value), "dates": sorted(value)}
                       for key, value in dates_by_partition.items()},
    }
    hrrr_manifest = {
        **common,
        "component": "hrrr_local_observations",
        "hrrr": {
            "coverage_complete": True,
            "required_rows_per_day": len(HRRR_LEADS) * len(HRRR_FIELDS),
            "rows": sum(record["rows"] for record in hrrr_outputs),
            "outputs": hrrr_outputs,
        },
        "local_observations": local_audit,
        "limitations": [
            "Historical publication and observation receipt times remain unproven",
            "Availability uses declared conservative proxies rather than original receipt logs",
            "Nearby-station coverage is diagnostic; complete KLAX coverage is the required core",
        ],
    }
    gefs_manifest = {
        **common,
        "component": "gefs",
        "coverage_complete": True,
        "uncertainty_representation": "archived_GEFS_30_member_mean_and_standard_deviation",
        "member_level_distribution_retained": False,
        "required_rows_per_day": len(GEFS_LEADS) * len(GEFS_MEMBERS),
        "rows": sum(record["rows"] for record in gefs_outputs),
        "outputs": gefs_outputs,
        "limitations": [
            "Mean and spread are native derived fields but do not retain member identity",
            "Skew, multimodality, and member trajectories cannot be reconstructed",
            "Historical publication time remains unproven; the conservative bound is an assumption",
        ],
    }
    hrrr_path = root / "data/manifests/v3_hrrr_local_observations.json"
    gefs_path = root / "data/manifests/v3_gefs.json"
    write_json(hrrr_path, hrrr_manifest)
    write_json(gefs_path, gefs_manifest)
    return [_relative(root, hrrr_path), _relative(root, gefs_path)]


def normalize_registered_weather_history(project_root: Path, *,
                                         today: date | None = None) -> dict[str, Any]:
    """Normalize every currently cached registered day and publish honest progress."""
    root = _safe_root(project_root)
    verify_availability_policy_registration(root)
    dates = registered_weather_dates()
    dates_by_partition: dict[str, set[str]] = defaultdict(set)
    for value in dates:
        dates_by_partition[_registered_partition(value)].add(value.isoformat())
    days: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    resumed_count = 0
    migrated_count = 0
    progress_path = root / "data/manifests/v3_weather_normalization_progress.json"
    for scanned, value in enumerate(dates, start=1):
        try:
            report = normalize_weather_day(root, value, today=today)
            days.append(report)
            resumed_count += int(report.get("resumed_from_verified_output") is True)
            migrated_count += int(report.get("migration_performed_now") is True)
        except FileNotFoundError as exc:
            missing.append({"date": value.isoformat(), "reason": str(exc)})
        except ValueError as exc:
            message = str(exc)
            # Concurrent acquisition can expose a source before its sidecar is
            # atomically present.  It remains incomplete, never admitted.
            if "lacks provenance" in message or "cache-only compatibility input missing" in message:
                missing.append({"date": value.isoformat(), "reason": message})
            else:
                failures.append({"date": value.isoformat(), "reason": message})
        write_json(progress_path, {
            "schema_version": 1,
            "component": "weather_normalization_progress",
            "status": "RUNNING_CACHE_ONLY_NORMALIZATION",
            "protected_final_read": False,
            "network_used": False,
            "registered_days": len(dates),
            "dates_scanned": scanned,
            "last_scanned_date": value.isoformat(),
            "normalized_days": len(days),
            "resumed_verified_days": resumed_count,
            "migrations_performed_this_run": migrated_count,
            "missing_raw_cache_days_seen": len(missing),
            "integrity_failure_days_seen": len(failures),
            "coverage_complete": False,
            "readiness_component_manifests_published": False,
            "availability_policy": availability_policy_record(),
            "limitations": [
                "This is an in-progress checkpoint and not V3 readiness evidence",
                "Complete component manifests remain withheld during the scan",
            ],
        })

    normalized_dates = {day["climate_date"] for day in days}
    expected_dates = {value.isoformat() for value in dates}
    complete = (normalized_dates == expected_dates and len(days) == len(dates)
                and not missing and not failures)
    local_audit = _audit_local_observations(root, dates_by_partition)
    published: list[str] = []
    final_paths = [
        root / "data/manifests/v3_hrrr_local_observations.json",
        root / "data/manifests/v3_gefs.json",
    ]
    if complete and local_audit.get("substantively_complete"):
        published = _publish_complete_components(root, days, dates_by_partition, local_audit)
    elif any(path.exists() for path in final_paths):
        raise ValueError("Existing complete weather component cannot be reconciled to current coverage")

    partition_counts = Counter(day["partition"] for day in days)
    durable_migrated_count = sum(
        day.get("migrated_from_verified_v1_output") is True for day in days)
    durable_fresh_dates = sorted(
        day["climate_date"] for day in days
        if day.get("migrated_from_verified_v1_output") is not True)
    report = {
        "schema_version": 1,
        "component": "weather_normalization_progress",
        "status": ("COMPLETE_COMPONENTS_PUBLISHED" if published
                   else "PARTIAL_CACHE_ONLY_NORMALIZATION"),
        "protected_final_read": False,
        "network_used": False,
        "registered_days": len(dates),
        "normalized_days": len(days),
        "newly_normalized_days": len(days) - resumed_count - migrated_count,
        "resumed_verified_days": resumed_count,
        "migrations_performed_this_run": migrated_count,
        "migrated_verified_v1_days": durable_migrated_count,
        "fresh_v2_normalized_dates": durable_fresh_dates,
        "missing_raw_cache_days": len(missing),
        "integrity_failure_days": len(failures),
        "coverage_complete": complete,
        "readiness_component_manifests_published": bool(published),
        "published_component_manifests": published,
        "partition_coverage": {
            key: {"normalized_days": partition_counts.get(key, 0),
                  "registered_days": len(value)}
            for key, value in dates_by_partition.items()
        },
        "availability_policy": availability_policy_record(),
        "local_observation_audit": local_audit,
        "days": [{
            "date": day["climate_date"],
            "partition": day["partition"],
            "resumed": day.get("resumed_from_verified_output", False),
            "migrated_from_v1": day.get("migrated_from_verified_v1_output", False),
            "manifest": _day_manifest_record(root, date.fromisoformat(day["climate_date"])),
            "outputs": day["outputs"],
        } for day in days],
        "missing": missing,
        "failures": failures,
        "limitations": [
            "Partial progress is not V3 readiness evidence",
            "The complete component manifests are withheld until all 543 registered days pass",
            "The max availability bound is conservative policy, not historical publication proof",
        ],
    }
    write_json(progress_path, report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    report = normalize_registered_weather_history(args.root)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"days", "missing", "failures"}},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
