"""Conservative availability and resumable cache-only normalization tests."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import klax_lab.weather_normalize_v3 as normalizer
from klax_lab.weather_normalize_v3 import (
    HRRR_FIELDS,
    HRRR_LEADS,
    GEFS_LEADS,
    GEFS_MEMBERS,
    LEGACY_POLICY_ID,
    POLICY_ID,
    POLICY_AMENDMENT_PATH,
    POLICY_CONFIG_PATH,
    PRE_AMENDMENT_PROGRESS_PATH,
    _audit_local_observations,
    apply_conservative_availability,
    normalize_registered_weather_history,
    normalize_weather_day,
    registered_weather_dates,
)
from klax_lab.provenance import sha256_file, write_json


DAY = date(2025, 1, 5)
NOW = date(2026, 9, 25)
PROJECT = Path(__file__).resolve().parents[1]


def _copy_policy_registration(root: Path) -> None:
    for relative in (
            POLICY_CONFIG_PATH, POLICY_AMENDMENT_PATH, PRE_AMENDMENT_PROGRESS_PATH):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((PROJECT / relative).read_bytes())


def _row(*, model: str, lead: int, field: str = "temperature_2m",
         member: str | None = None, last_modified: str | None = None) -> dict:
    nominal = "2025-01-05T06:00:00+00:00" if model == "hrrr" else "2025-01-05T00:00:00+00:00"
    return {
        "schema_version": 1,
        "climate_date": DAY.isoformat(),
        "partition": "selection",
        "provider": "NOAA_NODD_AWS",
        "model": model,
        "member_id": member,
        "field_id": field,
        "forecast_reference_time_utc": nominal,
        "nominal_issue_time_utc": nominal,
        "lead_hours": lead,
        "point_id": "KLAX",
        "source_url": f"https://archive.example/{model}/{lead}/{field}/{member}",
        "source_last_modified_at_utc": last_modified,
        "information_available_at_utc": last_modified,
        "information_availability_basis": "archived_HTTP_Last-Modified_unverified_as_original_publication_time",
        "historical_availability_proven": False,
        "as_of_validated": False,
        "is_missing": False,
        "value": 50.0,
    }


def _complete_rows(model: str) -> list[dict]:
    if model == "hrrr":
        return [_row(model=model, lead=lead, field=field,
                     last_modified="2025-01-05T07:20:00+00:00")
                for lead in HRRR_LEADS for field in HRRR_FIELDS]
    return [_row(model=model, lead=lead, member=member,
                 last_modified="2025-01-05T04:05:00+00:00")
            for member in GEFS_MEMBERS for lead in GEFS_LEADS]


def test_conservative_bound_preserves_http_evidence_and_admits_only_registered_times():
    source = _row(model="hrrr", lead=2,
                  last_modified="2025-01-05T07:20:00+00:00")
    audited = apply_conservative_availability([source])[0]
    assert audited["information_available_at_utc"] == source["information_available_at_utc"]
    assert audited["source_last_modified_at_utc"] == source["source_last_modified_at_utc"]
    assert audited["effective_information_available_at_utc"] == "2025-01-05T12:00:00+00:00"
    assert audited["eligible_decision_times_utc"] == ["12:00", "15:00", "18:00"]
    assert audited["availability_policy_id"] == POLICY_ID
    assert audited["as_of_validated"] is True
    assert audited["historical_availability_proven"] is False

    delayed = _row(model="gefs", lead=9, member="avg",
                   last_modified="2025-01-05T10:49:41+00:00")
    delayed_audit = apply_conservative_availability([delayed])[0]
    assert delayed_audit["effective_information_available_at_utc"] == (
        "2025-01-05T10:49:41+00:00")
    assert delayed_audit["eligible_decision_times_utc"] == ["12:00", "15:00", "18:00"]

    boundary = _row(model="gefs", lead=9, member="avg",
                    last_modified="2025-01-05T12:00:00+00:00")
    assert apply_conservative_availability([boundary])[0][
        "effective_information_available_at_utc"] == "2025-01-05T12:00:00+00:00"

    too_late = _row(model="gefs", lead=9, member="avg",
                    last_modified="2025-01-05T12:00:01+00:00")
    with pytest.raises(ValueError, match="unavailable"):
        apply_conservative_availability([too_late])

    missing = _row(model="gefs", lead=9, member="avg", last_modified=None)
    assert apply_conservative_availability([missing])[0][
        "effective_information_available_at_utc"] == "2025-01-05T06:00:00+00:00"


def test_registered_weather_calendar_is_exact_and_excludes_protected_final():
    dates = registered_weather_dates()
    assert len(dates) == 543 and len(set(dates)) == 543
    assert dates[0] == date(2024, 1, 1)
    assert date(2024, 12, 31) in dates
    assert date(2025, 1, 1) not in dates and date(2025, 1, 4) not in dates
    assert dates[-1] == date(2025, 6, 30)
    assert all(value < date(2025, 7, 1) for value in dates)


def test_fresh_v2_decode_is_limited_to_registered_pre_amendment_gap(
        tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)
    monkeypatch.setattr(
        normalizer, "decode_weather_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unregistered fresh v2 day reached GRIB decode")),
    )
    with pytest.raises(ValueError, match="June 28 gap"):
        normalize_weather_day(tmp_path, DAY, today=NOW)


def test_day_normalization_is_cache_only_atomic_and_hash_verified_on_resume(tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)
    monkeypatch.setattr(normalizer, "_validate_v2_lineage", lambda *args: None)
    monkeypatch.setattr(normalizer, "_validate_fresh_v2_decode", lambda *args: None)
    calls = []

    def fake_decode(plan, cache_root, *, points):
        calls.append((plan.purpose, Path(cache_root), tuple(points)))
        return _complete_rows("hrrr" if "hrrr" in plan.purpose else "gefs")

    monkeypatch.setattr(normalizer, "decode_weather_plan", fake_decode)
    first = normalize_weather_day(tmp_path, DAY, today=NOW)
    assert first["status"] == "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
    assert first["network_used"] is False and first["protected_final_read"] is False
    assert first["hrrr_coverage"]["rows"] == 24
    assert first["gefs_coverage"]["rows"] == 16
    assert len(calls) == 2
    for output in first["outputs"]:
        path = tmp_path / output["path"]
        assert path.is_file() and output["bytes"] == path.stat().st_size
        assert not path.with_name(path.name + ".tmp").exists()

    second = normalize_weather_day(tmp_path, DAY, today=NOW)
    assert second["resumed_from_verified_output"] is True
    assert len(calls) == 2

    output = tmp_path / first["outputs"][0]["path"]
    output.write_bytes(output.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="hash differs"):
        normalize_weather_day(tmp_path, DAY, today=NOW)


def _legacy_rows(model: str) -> list[dict]:
    result = []
    for source in _complete_rows(model):
        row = dict(source)
        nominal = datetime.fromisoformat(row["nominal_issue_time_utc"])
        row.update({
            "availability_policy_id": LEGACY_POLICY_ID,
            "availability_delay_hours": 6,
            "effective_information_available_at_utc": (nominal + timedelta(hours=6)).isoformat(),
            "effective_information_availability_basis": (
                "conservative_bound_nominal_model_cycle_plus_6_hours;"
                "archived_HTTP_Last-Modified_checked_if_present"),
            "eligible_decision_times_utc": ["12:00", "15:00", "18:00"],
            "historical_availability_proven": False,
            "as_of_validated": True,
            "as_of_validation_basis": "registered_conservative_bound_not_publication_proof",
        })
        result.append(row)
    return result


def _write_legacy_day(root: Path) -> tuple[Path, dict[str, str]]:
    folder = root / "data/normalized/v3_weather/selection/date=2025-01-05"
    folder.mkdir(parents=True)
    paths = {
        "hrrr": folder / "hrrr_points.parquet",
        "gefs": folder / "gefs_summary_points.parquet",
    }
    rows = {model: _legacy_rows(model) for model in paths}
    for model, path in paths.items():
        pq.write_table(pa.Table.from_pylist(rows[model]), path)
    outputs = [{
        "model": model,
        "path": path.relative_to(root).as_posix(),
        "rows": len(rows[model]),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for model, path in paths.items()]
    write_json(folder / "normalization_manifest.json", {
        "schema_version": 1,
        "component": "weather_normalized_day",
        "status": "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND",
        "climate_date": DAY.isoformat(),
        "partition": "selection",
        "protected_final_read": False,
        "network_used": False,
        "availability_policy": {"policy_id": LEGACY_POLICY_ID},
        "outputs": outputs,
    })
    source_hashes = {record["model"]: record["sha256"] for record in outputs}
    return folder, source_hashes


def _install_unit_lineage_snapshot(root: Path, folder: Path, monkeypatch) -> None:
    manifest = json.loads((folder / "normalization_manifest.json").read_text())
    relative = Path("data/manifests/unit_pre_amendment_progress.json")
    write_json(root / relative, {
        "status": "PARTIAL_CACHE_ONLY_NORMALIZATION",
        "registered_days": 543,
        "normalized_days": 542,
        "integrity_failure_days": 1,
        "protected_final_read": False,
        "days": [{
            "date": DAY.isoformat(), "partition": "selection",
            "outputs": manifest["outputs"],
        }],
    })
    monkeypatch.setattr(normalizer, "PRE_AMENDMENT_PROGRESS_PATH", relative)
    monkeypatch.setattr(
        normalizer, "verify_availability_policy_registration",
        lambda root: normalizer.availability_policy_record())


def test_verified_v1_day_migrates_without_grib_decode_and_records_provenance(
        tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)
    folder, source_hashes = _write_legacy_day(tmp_path)
    _install_unit_lineage_snapshot(tmp_path, folder, monkeypatch)

    def no_decode(*args, **kwargs):
        raise AssertionError("verified v1 migration must not decode GRIB")

    monkeypatch.setattr(normalizer, "decode_weather_plan", no_decode)
    migrated = normalize_weather_day(tmp_path, DAY, today=NOW)
    assert migrated["migration_performed_now"] is True
    assert migrated["availability_policy"]["policy_id"] == POLICY_ID
    assert migrated["policy_migration"]["transformed_without_grib_redecode"] is True
    assert {record["model"]: record["sha256"] for record in
            migrated["policy_migration"]["source_outputs"]} == source_hashes
    assert not folder.with_name(folder.name + ".policy-v2-stage").exists()
    assert not folder.with_name(folder.name + ".policy-v1-backup").exists()

    resumed = normalize_weather_day(tmp_path, DAY, today=NOW)
    assert resumed["resumed_from_verified_output"] is True
    assert resumed["migration_performed_now"] is False

    committed_path = folder / "normalization_manifest.json"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    committed.pop("policy_migration")
    write_json(committed_path, committed)
    with pytest.raises(ValueError, match="lineage differs"):
        normalize_weather_day(tmp_path, DAY, today=NOW)


def test_partial_v2_stage_is_discarded_only_after_exact_v1_revalidation(
        tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)
    folder, _ = _write_legacy_day(tmp_path)
    _install_unit_lineage_snapshot(tmp_path, folder, monkeypatch)
    stage = folder.with_name(folder.name + ".policy-v2-stage")
    stage.mkdir()
    (stage / "hrrr_points.parquet").write_bytes(b"interrupted-stage")

    monkeypatch.setattr(
        normalizer, "decode_weather_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("partial-stage recovery must migrate verified v1 rows")),
    )
    migrated = normalize_weather_day(tmp_path, DAY, today=NOW)
    assert migrated["migration_performed_now"] is True
    assert migrated["availability_policy"]["policy_id"] == POLICY_ID
    assert not stage.exists()


def test_partial_v2_stage_does_not_mask_changed_v1_source(tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)
    folder, _ = _write_legacy_day(tmp_path)
    _install_unit_lineage_snapshot(tmp_path, folder, monkeypatch)
    stage = folder.with_name(folder.name + ".policy-v2-stage")
    stage.mkdir()
    (stage / "hrrr_points.parquet").write_bytes(b"interrupted-stage")
    source = folder / "hrrr_points.parquet"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        normalize_weather_day(tmp_path, DAY, today=NOW)
    assert stage.exists()


def _write_local_fixture(root: Path, partition: str, dates: set[str], *, omit_12=False) -> dict:
    rows = []
    for value in sorted(dates):
        clocks = (15, 18) if omit_12 else (12, 15, 18)
        for hour in clocks:
            rows.append({
                "climate_date": value,
                "partition": partition,
                "station": "KLAX",
                "available_at": f"{value}T{hour:02d}:00:00+00:00",
                "as_of_validated": True,
                "protected_final": False,
                "historical_receipt_time_proven": False,
                "source_sha256": "a" * 64,
            })
    path = root / "data/normalized/v3_observations" / partition / "features/local_observations.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return {"path": path.relative_to(root).as_posix(), "rows": len(rows)}


def test_local_observation_combination_requires_every_klax_asof_day(tmp_path):
    dates = {"weather_training": {"2024-01-01"}, "selection": {"2025-01-05"}}
    partitions = {
        key: _write_local_fixture(tmp_path, key, value)
        for key, value in dates.items()
    }
    manifest_path = tmp_path / "data/manifests/v3_local_observations_partial.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "protected_final_read": False,
        "historical_receipt_time_proven": False,
        "local_observation_decision_times_admitted": ["12:00", "15:00", "18:00"],
        "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
        "partitions": partitions,
    }), encoding="utf-8")
    audit = _audit_local_observations(tmp_path.resolve(), dates)
    assert audit["substantively_complete"] is True
    assert {row["partition"] for row in audit["outputs"]} == set(dates)

    partitions["selection"] = _write_local_fixture(
        tmp_path, "selection", dates["selection"], omit_12=True,
    )
    manifest_path.write_text(json.dumps({
        "protected_final_read": False,
        "historical_receipt_time_proven": False,
        "local_observation_decision_times_admitted": ["12:00", "15:00", "18:00"],
        "availability_basis": "observed_time_plus_15_minutes_conservative_proxy",
        "partitions": partitions,
    }), encoding="utf-8")
    audit = _audit_local_observations(tmp_path.resolve(), dates)
    assert audit["substantively_complete"] is False
    assert "12:00" in audit["reason"]


def test_bulk_driver_withholds_components_while_any_registered_day_is_missing(tmp_path, monkeypatch):
    _copy_policy_registration(tmp_path)

    def missing(*args, **kwargs):
        raise FileNotFoundError("cache-only compatibility input missing")

    monkeypatch.setattr(normalizer, "normalize_weather_day", missing)
    report = normalize_registered_weather_history(tmp_path, today=NOW)
    assert report["registered_days"] == 543
    assert report["normalized_days"] == 0
    assert report["missing_raw_cache_days"] == 543
    assert report["readiness_component_manifests_published"] is False
    assert not (tmp_path / "data/manifests/v3_hrrr_local_observations.json").exists()
    assert not (tmp_path / "data/manifests/v3_gefs.json").exists()
    progress = json.loads(
        (tmp_path / "data/manifests/v3_weather_normalization_progress.json").read_text()
    )
    assert progress["status"] == "PARTIAL_CACHE_ONLY_NORMALIZATION"
    assert progress["network_used"] is False and progress["protected_final_read"] is False


def test_protected_final_date_and_root_fail_before_any_decode(tmp_path):
    with pytest.raises(ValueError, match="Protected-final"):
        normalize_weather_day(tmp_path, date(2025, 7, 1), today=NOW)
    protected = tmp_path / "protected_final"
    protected.mkdir()
    with pytest.raises(ValueError, match="protected_final"):
        normalize_registered_weather_history(protected, today=NOW)
