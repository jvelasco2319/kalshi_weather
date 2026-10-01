from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd
import pytest

from klax_lab.provenance import sha256_file
from scripts import v7y_weather_cache as cache
from v5.probability_cache_readiness import (
    PARTITION as LEGACY_PARTITION,
    POLICY_ID as LEGACY_POLICY_ID,
    VERSION as LEGACY_VERSION,
    _coverage as legacy_coverage,
    apply_frozen_availability,
)


TARGET = date(2025, 12, 18)
HRRR_FIELDS = (
    "temperature_2m",
    "total_cloud_cover",
    "cloud_ceiling",
    "wind_u_10m",
    "wind_v_10m",
    "mean_sea_level_pressure",
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _raw_row(*, model: str = "hrrr", lead: int = 2, member: str | None = None,
             field: str = "temperature_2m", modified: str = "2025-12-18T11:00:00+00:00") -> dict:
    cycle = datetime.combine(TARGET, datetime.min.time(), UTC) + timedelta(
        hours=6 if model == "hrrr" else 0
    )
    source_id = f"{model}-{TARGET:%Y%m%d}-{member or 'none'}-{lead}-{field}"
    return {
        "schema_version": 1,
        "climate_date": TARGET.isoformat(),
        "partition": LEGACY_PARTITION,
        "provider": "NOAA_NODD_AWS",
        "model": model,
        "source_id": source_id,
        "member_id": member,
        "field_id": field,
        "forecast_reference_time_utc": cycle.isoformat(),
        "nominal_issue_time_utc": cycle.isoformat(),
        "cycle_hour_utc": cycle.hour,
        "lead_hours": lead,
        "valid_time_utc": (cycle + timedelta(hours=lead)).isoformat(),
        "point_id": "KLAX",
        "units": (
            "delta_degF" if model == "gefs" and member == "spr"
            else "degF" if field == "temperature_2m"
            else "unit"
        ),
        "value": float(lead),
        "is_missing": False,
        "source_path": f"data/raw/v5p/weather/{source_id}/{field}.grib2",
        "source_sha256": _digest("source:" + source_id),
        "source_last_modified_at_utc": modified,
        "index_path": f"data/raw/v5p/weather/{source_id}/source.idx",
        "index_sha256": _digest("index:" + source_id),
        "historical_availability_proven": False,
        "as_of_validated": False,
        "contains_settlement_label": False,
    }


def _raw_rows(model: str, modified: str = "2025-12-18T11:00:00+00:00") -> list[dict]:
    if model == "hrrr":
        return [
            _raw_row(model=model, lead=lead, field=field, modified=modified)
            for lead in (2, 8, 14, 20)
            for field in HRRR_FIELDS
        ]
    return [
        _raw_row(model=model, lead=lead, member=member, modified=modified)
        for lead in (9, 12, 15, 18, 21, 24, 27, 30)
        for member in ("avg", "spr")
    ]


def _write_raw_inputs(root: Path) -> None:
    for row in _raw_rows("hrrr") + _raw_rows("gefs"):
        for field, body in (
            ("source_path", "source:" + row["source_id"]),
            ("index_path", "index:" + row["source_id"]),
        ):
            path = root / row[field]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body.encode("utf-8"))


def _write_policy(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    shared_relative = "fixture/shared_weather_helper.py"
    shared_path = root / shared_relative
    shared_path.parent.mkdir(parents=True, exist_ok=True)
    shared_path.write_text("# registered helper\n", encoding="utf-8")
    monkeypatch.setattr(cache, "PRESERVED_SHARED_PATHS", (shared_relative,))
    preserved: dict[str, dict] = {}
    candidates = [shared_path]
    candidates.extend(
        path for folder in (root / cache.LEGACY_OUTPUT_ROOT, root / cache.RAW_CACHE_ROOT)
        if folder.exists()
        for path in folder.rglob("*")
        if path.is_file()
    )
    for artifact in candidates:
        preserved[artifact.relative_to(root).as_posix()] = {
            "bytes": artifact.stat().st_size,
            "sha256": sha256_file(artifact),
        }
    failed = {"2025-08-15", "2025-12-18", "2025-12-19"}
    completed = []
    cursor = cache.START
    while cursor <= cache.END:
        if cursor.isoformat() not in failed:
            completed.append(cursor.isoformat())
        cursor += timedelta(days=1)
    prior = {
        "schema_version": "v7y-weather-backfill-v1",
        "campaign_id": "v7y-calendar-2025",
        "date_start": cache.START.isoformat(),
        "date_end": cache.END.isoformat(),
        "target_date_count": 184,
        "status": "INCOMPLETE",
        "completed_dates": completed,
        "failed_dates": {day: {"type": "ValueError", "message": "blocked"} for day in failed},
        "unavailable_dates": [],
        "protected_labels_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }
    prior["recovery_sha256"] = cache._canonical_hash(prior)
    prestate = {
        "schema_version": "v7y-availability-repair-prestate-v1",
        "registered_at_utc": "2026-09-29T23:48:06+00:00",
        "authorization": "User requested apply the fix on 2026-09-29",
        "prior_recovery_state": prior,
        "prior_recovery_file_sha256": _digest("prior recovery"),
        "preserved_artifacts": preserved,
        "protected_labels_read": False,
        "network_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }
    prestate["snapshot_sha256"] = cache._canonical_hash(prestate)
    monkeypatch.setattr(cache, "REGISTERED_PRESTATE_SHA256", prestate["snapshot_sha256"])
    prestate_path = root / cache.REPAIR_PRESTATE_PATH
    prestate_path.parent.mkdir(parents=True, exist_ok=True)
    prestate_path.write_text(
        json.dumps(prestate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    value = {
        "schema_version": "v7y-weather-availability-policy-v1",
        "status": "REGISTERED_BEFORE_V7Y_FREEZE",
        "policy_id": cache.POLICY_ID,
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
        "new_output_root": cache.OUTPUT_ROOT.as_posix(),
        "preserved_legacy_normalized_data": True,
        "eligible_decision_times": (
            "Preserve actual eligible 12:00, 15:00, and 18:00 schedules for each row"
        ),
        "registered_at_utc": "2026-09-29T23:48:07+00:00",
        "scope": "All V7Y calendar-2025 HRRR/GEFS rows at the existing 18:00 UTC decision",
        "repair_prestate_path": cache.REPAIR_PRESTATE_PATH.as_posix(),
        "repair_prestate_sha256": prestate["snapshot_sha256"],
        "reason": (
            "Shared V5 all-schedule eligibility incorrectly rejected December files "
            "available before V7Y's registered 18:00 decision"
        ),
    }
    value["policy_sha256"] = cache._canonical_hash(value)
    monkeypatch.setattr(cache, "REGISTERED_POLICY_SHA256", value["policy_sha256"])
    path = root / cache.POLICY_CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return value


def _fake_raw_verification(plan, _root: Path) -> dict:
    count = 4 if plan == "hrrr-plan" else 16
    return {
        "status": "CACHE_COMPATIBILITY_PASS",
        "objects_verified": count,
        "cached_bytes_verified": count * 100,
        "network_used": False,
        "protected_final_read": False,
    }


def _patch_raw_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "build_v5_daily_weather_plans", lambda target: ("hrrr-plan", "gefs-plan"))
    monkeypatch.setattr(cache, "verify_cache_only", _fake_raw_verification)
    monkeypatch.setattr(
        cache,
        "_decode_plan",
        lambda plan, _root: _raw_rows("hrrr" if plan == "hrrr-plan" else "gefs"),
    )


@pytest.mark.parametrize(
    ("modified", "eligible"),
    [
        ("2025-12-18T17:59:59+00:00", ["18:00"]),
        ("2025-12-18T18:00:00+00:00", ["18:00"]),
    ],
)
def test_v7y_18utc_boundary_is_inclusive(modified: str, eligible: list[str]) -> None:
    result = cache.apply_v7y_availability([_raw_row(modified=modified)])[0]
    assert result["effective_information_available_at_utc"] == modified
    assert result["eligible_decision_times_utc"] == eligible


def test_v7y_rejects_one_second_after_decision() -> None:
    with pytest.raises(cache.V7YWeatherCacheError, match="not available"):
        cache.apply_v7y_availability([
            _raw_row(modified="2025-12-18T18:00:01+00:00")
        ])


def test_v7y_preserves_real_eligibility_and_conservative_max() -> None:
    early = cache.apply_v7y_availability([
        _raw_row(modified="2025-12-18T11:00:00+00:00")
    ])[0]
    assert early["effective_information_available_at_utc"] == "2025-12-18T12:00:00+00:00"
    assert early["eligible_decision_times_utc"] == ["12:00", "15:00", "18:00"]

    afternoon = cache.apply_v7y_availability([
        _raw_row(modified="2025-12-18T14:30:00+00:00")
    ])[0]
    assert afternoon["eligible_decision_times_utc"] == ["15:00", "18:00"]
    assert "12:00" not in afternoon["eligible_decision_times_utc"]


def test_existing_v5_policy_stays_strict_while_v7y_accepts_18utc_safe_row() -> None:
    row = _raw_row(modified="2025-12-18T13:25:00+00:00")
    with pytest.raises(ValueError, match="decision-time"):
        apply_frozen_availability([row])
    assert cache.apply_v7y_availability([row])[0]["eligible_decision_times_utc"] == [
        "15:00",
        "18:00",
    ]


def test_policy_registration_rejects_resealed_semantic_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _write_policy(tmp_path, monkeypatch)
    value["decision_time_utc"] = "18:01:00"
    value["policy_sha256"] = cache._canonical_hash(value, "policy_sha256")
    path = tmp_path / cache.POLICY_CONFIG_PATH
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(cache.V7YWeatherCacheError, match="registration differs"):
        cache.verify_policy_registration(tmp_path)


def test_normalized_manifest_and_resealed_timestamp_mutation_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_inputs(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    _patch_raw_day(monkeypatch)
    manifest = cache.normalized_day(tmp_path, TARGET)
    cache.validate_normalized_day(tmp_path, TARGET, manifest)

    changed_manifest = json.loads(json.dumps(manifest))
    changed_manifest["decision_time_utc"] = "18:01"
    changed_manifest["manifest_sha256"] = cache._canonical_hash(
        changed_manifest, "manifest_sha256"
    )
    with pytest.raises(cache.V7YWeatherCacheError, match="manifest differs"):
        cache.validate_normalized_day(tmp_path, TARGET, changed_manifest)

    h_path = tmp_path / manifest["outputs"][0]["path"]
    frame = pd.read_parquet(h_path)
    frame.loc[0, "effective_information_available_at_utc"] = "2025-12-18T12:00:01+00:00"
    frame.to_parquet(h_path, index=False)
    changed_rows = json.loads(json.dumps(manifest))
    changed_rows["outputs"][0]["bytes"] = h_path.stat().st_size
    changed_rows["outputs"][0]["sha256"] = sha256_file(h_path)
    changed_rows["manifest_sha256"] = cache._canonical_hash(changed_rows, "manifest_sha256")
    with pytest.raises(cache.V7YWeatherCacheError, match="row boundary"):
        cache.validate_normalized_day(tmp_path, TARGET, changed_rows)


def _write_legacy_day(root: Path) -> tuple[list[dict], list[dict]]:
    h_rows = apply_frozen_availability(_raw_rows("hrrr"))
    g_rows = apply_frozen_availability(_raw_rows("gefs"))
    folder = root / cache.LEGACY_OUTPUT_ROOT / f"date={TARGET.isoformat()}"
    folder.mkdir(parents=True)
    h_path = folder / "hrrr_points.parquet"
    g_path = folder / "gefs_summary_points.parquet"
    pd.DataFrame(h_rows).to_parquet(h_path, index=False)
    pd.DataFrame(g_rows).to_parquet(g_path, index=False)
    outputs = [
        {
            "model": "hrrr",
            "path": h_path.relative_to(root).as_posix(),
            "rows": 24,
            "bytes": h_path.stat().st_size,
            "sha256": sha256_file(h_path),
        },
        {
            "model": "gefs",
            "path": g_path.relative_to(root).as_posix(),
            "rows": 16,
            "bytes": g_path.stat().st_size,
            "sha256": sha256_file(g_path),
        },
    ]
    manifest = {
        "version": LEGACY_VERSION,
        "status": "NORMALIZED_FEATURES_ONLY",
        "climate_date": TARGET.isoformat(),
        "partition": LEGACY_PARTITION,
        "availability_policy_id": LEGACY_POLICY_ID,
        "decision_time_utc": "18:00",
        "coverage": {
            "hrrr": legacy_coverage(h_rows, "hrrr", TARGET),
            "gefs": legacy_coverage(g_rows, "gefs", TARGET),
        },
        "outputs": outputs,
        "network_used": False,
        "refit_performed": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    manifest["manifest_sha256"] = cache._canonical_hash(manifest)
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return h_rows, g_rows


def test_normalized_day_reuses_verified_legacy_rows_without_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_h, original_g = _write_legacy_day(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    monkeypatch.setattr(cache, "build_v5_daily_weather_plans", lambda target: ("hrrr-plan", "gefs-plan"))
    monkeypatch.setattr(cache, "verify_cache_only", _fake_raw_verification)
    monkeypatch.setattr(
        cache,
        "_decode_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy rows must be reused")),
    )

    manifest = cache.normalized_day(tmp_path, TARGET)
    assert manifest["migration_source"]["kind"] == "verified_legacy_v5p_normalized"
    h_path = tmp_path / manifest["outputs"][0]["path"]
    g_path = tmp_path / manifest["outputs"][1]["path"]
    migrated_h = pd.read_parquet(h_path).to_dict(orient="records")
    migrated_g = pd.read_parquet(g_path).to_dict(orient="records")
    for before, after in zip(original_h + original_g, migrated_h + migrated_g, strict=True):
        assert after["value"] == before["value"]
        assert after["source_sha256"] == before["source_sha256"]
        assert after["index_sha256"] == before["index_sha256"]
        assert after["source_path"] == before["source_path"]
        assert after["valid_time_utc"] == before["valid_time_utc"]
        assert after["partition"] == cache.PARTITION
        assert after["availability_policy_id"] == cache.POLICY_ID


def test_resealed_prestate_replacement_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_policy(tmp_path, monkeypatch)
    prestate_path = tmp_path / cache.REPAIR_PRESTATE_PATH
    prestate = json.loads(prestate_path.read_text(encoding="utf-8"))
    prestate["registered_at_utc"] = "2026-09-30T00:00:00+00:00"
    prestate["snapshot_sha256"] = cache._canonical_hash(prestate, "snapshot_sha256")
    prestate_path.write_text(
        json.dumps(prestate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(cache.V7YWeatherCacheError, match="prestate registration"):
        cache.verify_policy_registration(tmp_path)


def test_registered_shared_source_tamper_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_policy(tmp_path, monkeypatch)
    (tmp_path / cache.PRESERVED_SHARED_PATHS[0]).write_text(
        "# changed after registration\n", encoding="utf-8"
    )
    with pytest.raises(cache.V7YWeatherCacheError, match="preserved artifact binding"):
        cache.verify_policy_registration(tmp_path)


@pytest.mark.parametrize("crash_state", ["empty_directory", "one_parquet"])
def test_manifest_absent_partial_output_is_deterministically_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_state: str
) -> None:
    _write_raw_inputs(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    _patch_raw_day(monkeypatch)
    h_path, g_path, manifest_path = cache._day_paths(tmp_path, TARGET)
    h_path.parent.mkdir(parents=True)
    if crash_state == "one_parquet":
        h_path.write_bytes(b"interrupted parquet write")

    manifest = cache.normalized_day(tmp_path, TARGET)
    cache.validate_normalized_day(tmp_path, TARGET, manifest)
    assert h_path.is_file() and g_path.is_file() and manifest_path.is_file()
    if crash_state == "one_parquet":
        assert h_path.read_bytes() != b"interrupted parquet write"


def test_raw_decoder_absolute_provenance_paths_bind_to_registered_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_inputs(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    _patch_raw_day(monkeypatch)

    def decode_absolute(plan, _root):
        rows = _raw_rows("hrrr" if plan == "hrrr-plan" else "gefs")
        for row in rows:
            row["source_path"] = (tmp_path / row["source_path"]).resolve().as_posix()
            row["index_path"] = (tmp_path / row["index_path"]).resolve().as_posix()
        return rows

    monkeypatch.setattr(cache, "_decode_plan", decode_absolute)
    manifest = cache.normalized_day(tmp_path, TARGET)
    cache.validate_normalized_day(tmp_path, TARGET, manifest)
    assert manifest["migration_source"]["kind"] == "decoded_from_verified_raw_cache"


def test_committed_manifest_with_missing_output_fails_without_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_inputs(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    _patch_raw_day(monkeypatch)
    manifest = cache.normalized_day(tmp_path, TARGET)
    h_path = tmp_path / manifest["outputs"][0]["path"]
    manifest_path = tmp_path / cache._day_paths(tmp_path, TARGET)[2].relative_to(tmp_path)
    before = manifest_path.read_bytes()
    h_path.unlink()

    with pytest.raises(cache.V7YWeatherCacheError, match="partial V7Y"):
        cache.normalized_day(tmp_path, TARGET)
    assert manifest_path.read_bytes() == before
    assert not h_path.exists()


def test_validation_rechecks_registered_raw_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_raw_inputs(tmp_path)
    _write_policy(tmp_path, monkeypatch)
    _patch_raw_day(monkeypatch)
    manifest = cache.normalized_day(tmp_path, TARGET)
    source_path = tmp_path / _raw_rows("hrrr")[0]["source_path"]
    source_path.write_bytes(b"changed after normalization")

    with pytest.raises(cache.V7YWeatherCacheError, match="preserved artifact binding"):
        cache.validate_normalized_day(tmp_path, TARGET, manifest)
