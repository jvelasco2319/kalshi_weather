"""Regression checks for recovery transitions and duplicate repair exclusion."""
from datetime import date
import json
import socket

import pytest

from scripts import acquire_v7y_weather_history as backfill


def fixture_state(tmp_path):
    missing = {"2025-08-15", "2025-12-18", "2025-12-19"}
    value = {
        "schema_version": "v7y-weather-backfill-v1",
        "campaign_id": "v7y-calendar-2025",
        "date_start": "2025-07-01", "date_end": "2025-12-31",
        "target_date_count": 184, "status": "INCOMPLETE",
        "completed_dates": [day for day in backfill.target_dates() if day not in missing],
        "unavailable_dates": [],
        "failed_dates": {day: {"type": "ValueError", "message": "prior failure"} for day in missing},
        "network_used": True, "protected_labels_read": False,
        "paper_orders_placed": 0, "live_orders_placed": 0,
        "started_at_utc": "2026-09-29T00:04:18+00:00",
        "transferred_bytes_this_process": 6778891797,
        "cumulative_request_count": 2468,
    }
    backfill.write_state(tmp_path / backfill.STATE, value)
    config = tmp_path / backfill.POLICY_CONFIG_PATH
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    snapshot = {"prior_recovery_state": dict(value)}
    snapshot["snapshot_sha256"] = backfill.artifact_hash(snapshot, "snapshot_sha256")
    (tmp_path / "runs/v7y_weather_backfill/repair-prestate.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return value


def fixture_registration(root):
    snapshot = json.loads((root / "runs/v7y_weather_backfill/repair-prestate.json").read_text())
    return {"policy_sha256": "a" * 64, "policy": {
        "repair_prestate_path": "runs/v7y_weather_backfill/repair-prestate.json",
        "repair_prestate_sha256": snapshot["snapshot_sha256"],
    }}


def test_cache_only_repair_clears_three_failures_without_refunding_counters(tmp_path, monkeypatch):
    original = fixture_state(tmp_path)
    monkeypatch.setattr(backfill, "verify_policy_registration", fixture_registration)
    normalized = []
    def normalize(root, target):
        normalized.append(target.isoformat())
        return {"manifest_sha256": target.isoformat()}
    monkeypatch.setattr(backfill, "_normalized_day", normalize)
    monkeypatch.setattr(backfill, "validate_normalized_day", lambda *args: None)
    touched = []

    def cached(root, target):
        touched.append(target.isoformat())
        return {"date": target.isoformat(), "network_requests": 0, "transferred_bytes": 0}

    monkeypatch.setattr(backfill, "cached_one", cached)

    def forbidden(*args, **kwargs):
        raise AssertionError("cache-only repair must not create a downloader")

    monkeypatch.setattr(backfill, "RateLimitedSession", forbidden)
    monkeypatch.setattr(backfill, "acquire_one", forbidden)
    result = backfill.run(tmp_path, 2, cache_only=True)
    assert sorted(touched) == ["2025-08-15", "2025-12-18", "2025-12-19"]
    assert sorted(normalized) == backfill.target_dates()
    assert result["status"] == "COMPLETE"
    assert result["completed_dates"] == backfill.target_dates()
    assert result["normalization_completed_dates"] == backfill.target_dates()
    assert result["failed_dates"] == {}
    assert result["unavailable_dates"] == []
    assert result["transferred_bytes_this_process"] == original["transferred_bytes_this_process"]
    assert result["cumulative_request_count"] == original["cumulative_request_count"]
    assert result["started_at_utc"] == original["started_at_utc"]
    assert result["transferred_bytes_last_repair"] == 0
    assert result["availability_repair_parent_recovery_sha256"] == original["recovery_sha256"]
    assert backfill.load_state(tmp_path / backfill.STATE)["recovery_sha256"] == result["recovery_sha256"]


def test_live_normalization_lock_refuses_duplicate_without_state_change(tmp_path, monkeypatch):
    fixture_state(tmp_path)
    state_path = tmp_path / backfill.STATE
    before = state_path.read_bytes()
    lock = state_path.parent / "normalization.lock"
    lock.write_text(json.dumps({"process_id": 999999}), encoding="utf-8")
    monkeypatch.setattr(backfill, "process_exists", lambda pid: True)
    with pytest.raises(RuntimeError, match="another process"):
        backfill.run(tmp_path, 1, cache_only=True)
    assert state_path.read_bytes() == before


def test_normalization_failure_does_not_mark_missing_date_complete(tmp_path, monkeypatch):
    fixture_state(tmp_path)
    monkeypatch.setattr(backfill, "verify_policy_registration", fixture_registration)
    def fail(*args, **kwargs):
        raise ValueError("availability bound follows 18:00")
    monkeypatch.setattr(backfill, "_normalized_day", fail)
    with pytest.raises(ValueError, match="18:00"):
        backfill.run(tmp_path, 1, cache_only=True)
    result = backfill.load_state(tmp_path / backfill.STATE)
    assert len(result["completed_dates"]) == 181
    assert set(result["failed_dates"]) == {"2025-08-15", "2025-12-18", "2025-12-19"}
    assert not (state_path := tmp_path / "runs/v7y_weather_backfill/normalization.lock").exists()


def test_resealed_initial_state_with_counter_change_is_rejected(tmp_path, monkeypatch):
    value = fixture_state(tmp_path)
    value["cumulative_request_count"] = 0
    backfill.write_state(tmp_path / backfill.STATE, value)
    monkeypatch.setattr(backfill, "verify_policy_registration", fixture_registration)
    with pytest.raises(ValueError, match="registered prestate"):
        backfill.run(tmp_path, 1, cache_only=True)
    assert len(backfill.load_state(tmp_path / backfill.STATE)["completed_dates"]) == 181


def test_resume_cannot_refund_preserved_counter(tmp_path, monkeypatch):
    value = fixture_state(tmp_path)
    value["availability_registration"] = {"path": backfill.POLICY_CONFIG_PATH.as_posix(),
        "sha256": backfill.file_hash(tmp_path / backfill.POLICY_CONFIG_PATH), "policy_sha256": "a" * 64}
    value["availability_repair_parent_recovery_sha256"] = value["recovery_sha256"]
    value["cumulative_request_count"] = 0
    backfill.write_state(tmp_path / backfill.STATE, value)
    monkeypatch.setattr(backfill, "verify_policy_registration", fixture_registration)
    with pytest.raises(ValueError, match="preserved counter"):
        backfill.run(tmp_path, 1, cache_only=True)


def test_resume_cannot_clear_historical_network_use(tmp_path):
    value = fixture_state(tmp_path)
    registration = fixture_registration(tmp_path)
    value["availability_registration"] = {"policy_sha256": "a" * 64}
    value["availability_repair_parent_recovery_sha256"] = value["recovery_sha256"]
    value["network_used"] = False
    backfill.write_state(tmp_path / backfill.STATE, value)
    with pytest.raises(ValueError, match="network_used"):
        backfill.validate_repair_parent(tmp_path, value, registration)


def test_cache_only_programmatic_run_denies_and_restores_sockets(tmp_path, monkeypatch):
    originals = (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)
    def probe(root, workers, *, cache_only):
        with pytest.raises(RuntimeError, match="denies network"):
            socket.create_connection(("example.invalid", 80))
        with socket.socket() as stream:
            with pytest.raises(RuntimeError, match="denies network"):
                stream.connect(("127.0.0.1", 80))
            with pytest.raises(RuntimeError, match="denies network"):
                stream.connect_ex(("127.0.0.1", 80))
        return {"status": "probe"}
    monkeypatch.setattr(backfill, "_run", probe)
    assert backfill.run(tmp_path, 1, cache_only=True)["status"] == "probe"
    assert originals == (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)


def test_valid_repair_can_resume_after_interrupted_existing_conversion(tmp_path, monkeypatch):
    original = fixture_state(tmp_path)
    monkeypatch.setattr(backfill, "verify_policy_registration", fixture_registration)
    monkeypatch.setattr(backfill, "validate_normalized_day", lambda *args: None)
    def stop(*args):
        raise RuntimeError("interrupted conversion")
    monkeypatch.setattr(backfill, "_normalized_day", stop)
    with pytest.raises(RuntimeError, match="interrupted"):
        backfill.run(tmp_path, 1, cache_only=True)
    monkeypatch.setattr(backfill, "_normalized_day", lambda root, target: {"manifest_sha256": target.isoformat()})
    touched = []
    def cached(root, target):
        touched.append(target.isoformat())
        return {"network_requests": 0}
    monkeypatch.setattr(backfill, "cached_one", cached)
    result = backfill.run(tmp_path, 1, cache_only=True)
    assert result["status"] == "COMPLETE"
    assert len(touched) == 3
    assert result["availability_repair_parent_recovery_sha256"] == original["recovery_sha256"]
    assert result["cumulative_request_count"] == original["cumulative_request_count"]


def test_cache_verifier_cannot_report_live_or_out_of_scope_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(backfill, "verify_cache_only", lambda *args: {
        "status": "CACHE_COMPATIBILITY_PASS", "network_used": True,
        "protected_final_read": False, "objects_verified": 4,
    })
    with pytest.raises(ValueError, match="scope differs"):
        backfill.cached_one(tmp_path, date(2025, 8, 15))
