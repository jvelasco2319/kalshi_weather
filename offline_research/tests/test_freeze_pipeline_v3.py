"""Boundary and provenance tests for the real-data V3 freeze entry point."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from klax_lab.freeze_pipeline_v3 import (
    V3FreezePipelineError,
    _adapt_forecast_row,
    _inside,
    _require_finalized_source,
    _validate_cycle_contract,
    _weather_output_map,
    freeze_completed_v3_development,
)
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.weather_normalize_v3 import (
    POLICY_AMENDMENT_PATH,
    POLICY_CONFIG_PATH,
    POLICY_ID,
    PRE_AMENDMENT_PROGRESS_PATH,
    availability_policy_record,
    registered_weather_dates,
)


PROJECT = Path(__file__).resolve().parents[1]


def test_partial_source_fails_before_any_frozen_output_is_created(tmp_path: Path):
    manifests = tmp_path / "data/manifests"
    manifests.mkdir(parents=True)
    (manifests / "v3_source_feasibility.json").write_text(json.dumps({
        "component": "source_feasibility",
        "status": "REVISED_FINITE_BULK_PLAN_ADMITTED_NOT_COMPLETE",
        "protected_final_read": False,
    }), encoding="utf-8")
    with pytest.raises(V3FreezePipelineError, match="finalization is absent or incomplete"):
        freeze_completed_v3_development(tmp_path)
    assert not (tmp_path / "data/frozen/v3_development").exists()
    assert not (tmp_path / "data/manifests/v3_dataset.json").exists()


def _finalized_source_policy_fixture(root: Path) -> None:
    for relative in (
            POLICY_CONFIG_PATH, POLICY_AMENDMENT_PATH, PRE_AMENDMENT_PROGRESS_PATH):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((PROJECT / relative).read_bytes())
    policy = availability_policy_record()
    migration_days = []
    for value in registered_weather_dates():
        day_text = value.isoformat()
        partition = "weather_training" if value.year == 2024 else "selection"
        daily = (root / "data/normalized/v3_weather" / partition
                 / f"date={day_text}" / "normalization_manifest.json")
        write_json(daily, {"protected_final_read": False, "date": day_text})
        migration_days.append({
            "date": day_text, "partition": partition,
            "migrated_from_v1": day_text != "2025-06-28",
            "manifest": {
                "path": daily.relative_to(root).as_posix(),
                "bytes": daily.stat().st_size,
                "sha256": sha256_file(daily),
            },
        })
    progress = {
        "protected_final_read": False,
        "status": "COMPLETE_COMPONENTS_PUBLISHED",
        "coverage_complete": True,
        "normalized_days": 543,
        "missing_raw_cache_days": 0,
        "integrity_failure_days": 0,
        "network_used": False,
        "availability_policy": policy,
        "migrated_verified_v1_days": 542,
        "fresh_v2_normalized_dates": ["2025-06-28"],
        "days": migration_days,
        "readiness_component_manifests_published": True,
    }
    write_json(root / "data/manifests/v3_weather_normalization_progress.json", progress)
    common = {
        "protected_final_read": False,
        "status": "COMPLETE_REGISTERED_COVERAGE_WITH_CONSERVATIVE_ASOF_BOUND",
        "readiness_component_pass": True,
        "registered_days": 543,
        "availability_policy": policy,
    }
    write_json(root / "data/manifests/v3_hrrr_local_observations.json", {
        **common, "component": "hrrr_local_observations"})
    write_json(root / "data/manifests/v3_gefs.json", {**common, "component": "gefs"})
    audit = {
        "days_complete": 543,
        "days_unavailable": 0,
        "offline_normalization_verified": True,
        "protected_final_read": False,
        "normalization_progress_sha256": sha256_file(
            root / "data/manifests/v3_weather_normalization_progress.json"),
        "hrrr_component_sha256": sha256_file(
            root / "data/manifests/v3_hrrr_local_observations.json"),
        "gefs_component_sha256": sha256_file(root / "data/manifests/v3_gefs.json"),
        "weather_availability_policy_config_path": POLICY_CONFIG_PATH.as_posix(),
        "weather_availability_policy_config_sha256": policy["policy_config"]["sha256"],
        "weather_availability_amendment_path": POLICY_AMENDMENT_PATH.as_posix(),
        "weather_availability_amendment_sha256": policy["amendment"]["sha256"],
        "pre_amendment_normalization_snapshot_path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
        "pre_amendment_normalization_snapshot_sha256": policy[
            "pre_amendment_progress_snapshot"]["sha256"],
        "migrated_verified_v1_days": 542,
        "fresh_v2_normalized_dates": ["2025-06-28"],
        "weather_policy_migration_identity_sha256": canonical_hash([{
            "date": item["date"],
            "migrated_from_v1": item["migrated_from_v1"],
            "manifest_path": item["manifest"]["path"],
            "manifest_sha256": item["manifest"]["sha256"],
        } for item in migration_days]),
    }
    write_json(root / "data/manifests/v3_source_feasibility.json", {
        "protected_final_read": False,
        "status": "COMPLETE_FINITE_BULK_AND_NORMALIZED_COVERAGE_AUDITED",
        "readiness_component_pass": True,
        "completion_audit": audit,
    })


def test_freeze_revalidates_hash_bound_weather_policy_after_source_finalization(
        tmp_path: Path):
    _finalized_source_policy_fixture(tmp_path)
    _require_finalized_source(tmp_path)
    amendment = tmp_path / POLICY_AMENDMENT_PATH
    amendment.write_bytes(amendment.read_bytes() + b" ")
    with pytest.raises(V3FreezePipelineError, match="registration is missing or changed"):
        _require_finalized_source(tmp_path)


def test_freeze_rejects_stale_policy_binding_inside_source_audit(tmp_path: Path):
    _finalized_source_policy_fixture(tmp_path)
    source_path = tmp_path / "data/manifests/v3_source_feasibility.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["completion_audit"]["weather_availability_amendment_sha256"] = "0" * 64
    write_json(source_path, source)
    with pytest.raises(V3FreezePipelineError, match="stale weather availability"):
        _require_finalized_source(tmp_path)


def test_protected_final_paths_are_rejected_before_read():
    root = Path("C:/research").resolve()
    with pytest.raises(V3FreezePipelineError, match="protected-final"):
        _inside(root, Path("data/protected_final/secret.parquet"), "fixture")


def test_weather_inventory_rejects_duplicate_or_partial_manifest():
    record = {"path": "data/normalized/v3_weather/x.parquet"}
    with pytest.raises(V3FreezePipelineError, match="543 days"):
        _weather_output_map({"outputs": [record]}, "gefs")
    with pytest.raises(V3FreezePipelineError, match="543 days"):
        _weather_output_map({"hrrr": {"outputs": [record, record]}}, "hrrr")


def test_forecast_aliases_are_explicit_and_missing_alias_fails():
    row = {
        "forecast_reference_time_utc": "2025-01-05T06:00:00+00:00",
        "effective_information_available_at_utc": "2025-01-05T12:00:00+00:00",
        "valid_time_utc": "2025-01-05T14:00:00+00:00",
        "point_id": "KLAX",
    }
    adapted = _adapt_forecast_row(row)
    assert adapted["initialized_at"] == row["forecast_reference_time_utc"]
    assert adapted["available_at"] == row["effective_information_available_at_utc"]
    assert adapted["valid_at"] == row["valid_time_utc"]
    assert adapted["location_id"] == "KLAX"
    broken = dict(row)
    broken.pop("point_id")
    with pytest.raises(V3FreezePipelineError, match="as-of alias"):
        _adapt_forecast_row(broken)


def test_wrong_weather_cycle_is_rejected():
    row = {
        "forecast_reference_time_utc": "2025-01-05T06:00:00+00:00",
        "nominal_issue_time_utc": "2025-01-05T06:00:00+00:00",
        "effective_information_available_at_utc": "2025-01-05T12:00:00+00:00",
        "source_last_modified_at_utc": "2025-01-05T07:20:00+00:00",
        "availability_policy_id": POLICY_ID,
        "eligible_decision_times_utc": ["12:00", "15:00", "18:00"],
        "valid_time_utc": "2025-01-05T08:00:00+00:00",
        "cycle_hour_utc": 6,
        "lead_hours": 2,
    }
    _validate_cycle_contract([row], model="hrrr", target_date=date(2025, 1, 5))
    wrong = dict(row, cycle_hour_utc=7)
    with pytest.raises(V3FreezePipelineError, match="cycle/timing contract"):
        _validate_cycle_contract([wrong], model="hrrr", target_date=date(2025, 1, 5))


def test_gefs_archived_last_modified_can_delay_bound_but_not_cross_noon():
    row = {
        "forecast_reference_time_utc": "2025-01-05T00:00:00+00:00",
        "nominal_issue_time_utc": "2025-01-05T00:00:00+00:00",
        "source_last_modified_at_utc": "2025-01-05T10:49:41+00:00",
        "effective_information_available_at_utc": "2025-01-05T10:49:41+00:00",
        "availability_policy_id": POLICY_ID,
        "eligible_decision_times_utc": ["12:00", "15:00", "18:00"],
        "valid_time_utc": "2025-01-05T09:00:00+00:00",
        "cycle_hour_utc": 0,
        "lead_hours": 9,
    }
    _validate_cycle_contract([row], model="gefs", target_date=date(2025, 1, 5))
    late = dict(
        row,
        source_last_modified_at_utc="2025-01-05T12:00:01+00:00",
        effective_information_available_at_utc="2025-01-05T12:00:01+00:00",
    )
    with pytest.raises(V3FreezePipelineError, match="cycle/timing contract"):
        _validate_cycle_contract([late], model="gefs", target_date=date(2025, 1, 5))
