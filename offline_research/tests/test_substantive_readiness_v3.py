from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import shutil

import pytest

import klax_lab.campaign_v3 as campaign
import klax_lab.incident_amendment_v3 as incident
import klax_lab.incident_packet_amendment_v3 as packet_incident
import klax_lab.substantive_readiness_v3 as readiness
from klax_lab.evaluator_v3 import evaluation_policy_sha256
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.v3_readiness import REQUIRED_COMPONENT_MANIFESTS


PROJECT = Path(__file__).resolve().parents[1]


def _copy_weather_policy_registration(root: Path) -> None:
    for relative in (
            readiness.POLICY_CONFIG_PATH, readiness.POLICY_AMENDMENT_PATH,
            readiness.PRE_AMENDMENT_PROGRESS_PATH):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((PROJECT / relative).read_bytes())


def test_capability_probes_reexecute_and_reject_tampering(tmp_path: Path) -> None:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "src", root / "src")
    published = readiness.publish_v3_capability_probes(
        root, include_synthetic_worker_fixture=True)
    assert set(published) == {
        "independent_replication", "critic_contract", "worker_capability"}
    assert readiness.verify_v3_capability_probes(
        root, allow_synthetic_worker=True)["status"] == "PASS"
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="actual local GPT-OSS protocol probe"):
        readiness.verify_v3_capability_probes(root)
    assert all(body["network_used"] is False for body in published.values())
    assert all(body["protected_final_read"] is False for body in published.values())
    path = root / readiness.REPLICATION_PATH
    body = json.loads(path.read_text(encoding="utf-8"))
    body["independent_result"]["fees"] = "999"
    write_json(path, body)
    with pytest.raises(readiness.V3SubstantiveReadinessError, match="stale or modified"):
        readiness.verify_v3_capability_probes(root, allow_synthetic_worker=True)


def _actual_worker_probe_fixture(root: Path, monkeypatch) -> tuple[Path, Path]:
    from klax_lab.local_backend import BACKEND
    from klax_lab.research_protocol_v3 import PROTOCOL_V3

    for name in ("local_backend.py", "research_plan_v3.py", "research_protocol_v3.py"):
        target = root / "src/klax_lab" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / "src/klax_lab" / name, target)
    runtime_spec = root / "data/models/runtime_spec.json"
    write_json(runtime_spec, {
        "protocol": "klax-local-runtime-v1", "backend": BACKEND,
        "executable": {"path": "runtime/llama-completion.exe", "sha256": "1" * 64},
        "model": {"path": "runtime/model.gguf", "sha256": "2" * 64},
        "help_file": {"path": "runtime/help.txt", "sha256": "3" * 64},
        "support_files": [],
    })
    monkeypatch.setattr(
        "klax_lab.local_backend.verify_runtime",
        lambda _spec: {"runtime_sha256": "a" * 64},
    )
    probe = root / "runs/local_worker_probe/fixture/v3-protocol-report.json"
    write_json(probe, {
        "status": "PASS", "synthetic": True, "protocol": PROTOCOL_V3,
        "backend": BACKEND, "runtime_sha256": "a" * 64,
        "backend_code_sha256": sha256_file(root / "src/klax_lab/local_backend.py"),
        "actual_v3_protocol_probe_passed": True,
        "validated_research_plan_sha256": "b" * 64,
        "tool_catalog": [], "error": None,
        "scope": "Actual local-model V3 structured-output compatibility",
    })
    return runtime_spec, probe


def test_actual_worker_component_binds_existing_probe_without_inference(
        tmp_path: Path, monkeypatch) -> None:
    runtime_spec, probe = _actual_worker_probe_fixture(tmp_path, monkeypatch)
    write_json(tmp_path / "data/manifests/local_worker_probe.json", {
        "status": "PASS",
        "v3_protocol_probe_path": probe.relative_to(tmp_path).as_posix(),
        "v3_protocol_probe_sha256": sha256_file(probe),
    })
    published = readiness.publish_actual_v3_worker_probe(
        tmp_path, runtime_spec_path=runtime_spec)
    assert published["local_model_inference_executed"] is True
    assert published["actual_v3_protocol_probe_passed"] is True
    assert published["tool_catalog"] == []
    assert published["runtime_spec_sha256"] == sha256_file(runtime_spec)
    assert published["v3_protocol_probe_sha256"] == sha256_file(probe)
    assert readiness.verify_actual_v3_worker_probe(tmp_path) == published


def test_actual_worker_component_rejects_tamper_missing_and_synthetic_only(
        tmp_path: Path, monkeypatch) -> None:
    runtime_spec, probe = _actual_worker_probe_fixture(tmp_path, monkeypatch)
    readiness.publish_actual_v3_worker_probe(
        tmp_path, runtime_spec_path=runtime_spec, v3_protocol_probe_path=probe)
    report = json.loads(probe.read_text(encoding="utf-8"))
    report["tool_catalog"] = ["shell"]
    write_json(probe, report)
    with pytest.raises(readiness.V3SubstantiveReadinessError, match="did not pass or changed"):
        readiness.verify_actual_v3_worker_probe(tmp_path)

    probe.unlink()
    with pytest.raises(readiness.V3SubstantiveReadinessError, match="Missing V3 protocol probe"):
        readiness.publish_actual_v3_worker_probe(
            tmp_path, runtime_spec_path=runtime_spec, v3_protocol_probe_path=probe)

    write_json(tmp_path / readiness.WORKER_PATH, readiness.build_worker_probe(tmp_path))
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="actual local GPT-OSS protocol probe"):
        readiness.verify_actual_v3_worker_probe(tmp_path)


def _weather_component_fixture(root: Path, monkeypatch) -> None:
    _copy_weather_policy_registration(root)
    dates = {
        "weather_training": ["2024-01-01"],
        "selection": ["2025-01-05"],
    }
    monkeypatch.setattr(readiness, "EXPECTED_WEATHER_DAYS", 2)
    monkeypatch.setattr(readiness, "_expected_dates", lambda: dates)
    outputs = {}
    for model, rows in (("hrrr", 24), ("gefs", 16)):
        records = []
        filename = "hrrr_points.parquet" if model == "hrrr" else "gefs_summary_points.parquet"
        for partition, values in dates.items():
            path = (root / "data/normalized/v3_weather" / partition
                    / f"date={values[0]}" / filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((model + values[0]).encode())
            records.append({
                "model": model, "path": path.relative_to(root).as_posix(),
                "rows": rows, "bytes": path.stat().st_size, "sha256": sha256_file(path),
            })
        outputs[model] = records
    local_source = root / "data/manifests/local.json"
    write_json(local_source, {"protected_final_read": False})
    local_outputs = []
    for index, partition in enumerate(dates):
        path = root / f"data/normalized/local-{index}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(partition.encode())
        local_outputs.append({
            "partition": partition, "path": path.relative_to(root).as_posix(),
            "rows": 1, "bytes": path.stat().st_size, "sha256": sha256_file(path),
        })
    common = {
        "schema_version": 1,
        "status": readiness.WEATHER_COMPLETE_STATUS,
        "readiness_component_pass": True, "protected_final_read": False,
        "network_used_for_normalization": False,
        "historical_publication_time_proven": False, "registered_days": 2,
        "availability_policy": readiness.availability_policy_record(),
        "partitions": {name: {"days": len(values), "dates": values}
                       for name, values in dates.items()},
    }
    write_json(root / "data/manifests/v3_hrrr_local_observations.json", {
        **common, "component": "hrrr_local_observations",
        "hrrr": {"coverage_complete": True, "required_rows_per_day": 24,
                 "rows": 48, "outputs": outputs["hrrr"]},
        "local_observations": {
            "substantively_complete": True, "historical_receipt_time_proven": False,
            "nearby_stations_optional_and_not_required_for_complete_klax_schedule": True,
            "source_manifest": {"path": local_source.relative_to(root).as_posix(),
                                "sha256": sha256_file(local_source)},
            "outputs": local_outputs,
        },
    })
    write_json(root / "data/manifests/v3_gefs.json", {
        **common, "component": "gefs", "coverage_complete": True,
        "uncertainty_representation": "archived_GEFS_30_member_mean_and_standard_deviation",
        "member_level_distribution_retained": False, "required_rows_per_day": 16,
        "rows": 32, "outputs": outputs["gefs"],
    })


def test_weather_components_require_exact_hashes_and_partition_coverage(
        tmp_path: Path, monkeypatch) -> None:
    _weather_component_fixture(tmp_path, monkeypatch)
    result = readiness._verify_weather_component(tmp_path, "hrrr_local_observations")
    assert result["hrrr"]["coverage_complete"] is True
    first = tmp_path / result["hrrr"]["outputs"][0]["path"]
    first.write_bytes(b"changed")
    with pytest.raises(readiness.V3SubstantiveReadinessError, match="missing or changed"):
        readiness._verify_weather_component(tmp_path, "hrrr_local_observations")


def test_weather_component_rejects_availability_amendment_tamper(
        tmp_path: Path, monkeypatch) -> None:
    _weather_component_fixture(tmp_path, monkeypatch)
    amendment = tmp_path / readiness.POLICY_AMENDMENT_PATH
    amendment.write_bytes(amendment.read_bytes() + b" ")
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="availability registration differs"):
        readiness._verify_weather_component(tmp_path, "gefs")


def _full_days() -> list[dict]:
    days = []
    for start, end in ((date(2024, 1, 1), date(2024, 12, 31)),
                       (date(2025, 1, 5), date(2025, 6, 30))):
        for index in range((end - start).days + 1):
            days.append({
                "date": (start + timedelta(days=index)).isoformat(),
                "status": "RAW_RANGES_CACHE_VERIFIED", "network_requests": 60,
                "cache_hits": 0, "new_transfer_bytes": 10,
                "hrrr_objects": 4, "gefs_objects": 16,
            })
    return days


def _source_completion_fixture(root: Path) -> None:
    _copy_weather_policy_registration(root)
    write_json(root / readiness.SOURCE_PATH, {
        "schema_version": 1, "component": "source_feasibility",
        "status": "REVISED_FINITE_BULK_PLAN_ADMITTED_NOT_COMPLETE",
        "protected_final_read": False,
        "revised_bulk_plan": {"training_and_development_days": 543,
                              "transfer_cap_bytes": 35_000_000_000},
        "pilot": {"preserved": True},
    })
    days = _full_days()
    normalization_days = []
    for item in days:
        partition = "weather_training" if item["date"] < "2025-01-01" else "selection"
        daily_path = (root / "data/normalized/v3_weather" / partition
                      / f"date={item['date']}" / "normalization_manifest.json")
        write_json(daily_path, {"protected_final_read": False, "date": item["date"]})
        normalization_days.append({
            "date": item["date"], "partition": partition,
            "migrated_from_v1": item["date"] != "2025-06-28",
            "manifest": {
                "path": daily_path.relative_to(root).as_posix(),
                "bytes": daily_path.stat().st_size,
                "sha256": sha256_file(daily_path),
            },
        })
    write_json(root / readiness.BULK_PROGRESS_PATH, {
        "schema_version": 1, "component": "revised_weather_raw_history",
        "status": "FULL_RAW_RANGE_ACQUISITION_COMPLETE",
        "partitions": {
            "weather_training": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
            "selection": {"start_date": "2025-01-05", "end_date": "2025-06-30"},
        },
        "unregistered_gap_dates_acquired": False, "days_attempted": 543,
        "days_complete": 543, "days_unavailable": 0, "protected_final_read": False,
        "coverage_complete": True, "network_used_for_acquisition": True,
        "total_source_bytes_after": 5430, "max_total_source_bytes": 35_000_000_000,
        "days": days,
    })
    write_json(root / readiness.NORMALIZATION_PROGRESS_PATH, {
        "status": "COMPLETE_COMPONENTS_PUBLISHED", "coverage_complete": True,
        "normalized_days": 543, "missing_raw_cache_days": 0, "integrity_failure_days": 0,
        "network_used": False, "protected_final_read": False,
        "availability_policy": readiness.availability_policy_record(),
        "migrated_verified_v1_days": 542,
        "fresh_v2_normalized_dates": ["2025-06-28"],
        "days": normalization_days,
        "readiness_component_manifests_published": True,
        "published_component_manifests": [
            "data/manifests/v3_hrrr_local_observations.json",
            "data/manifests/v3_gefs.json",
        ],
    })
    stdout = root / "runs/weather_v3_bulk.stdout.log"
    stderr = root / "runs/weather_v3_bulk.stderr.log"
    stdout.parent.mkdir(parents=True, exist_ok=True)
    stdout.write_text("complete 543-day fixture\n", encoding="utf-8")
    stderr.write_text("", encoding="utf-8")
    write_json(root / readiness.ACQUISITION_ATTEMPTS_PATH, {
        "schema_version": 2,
        "component": "registered_weather_acquisition_attempt_journal",
        "registered_scope": {
            "start_date": "2024-01-01", "end_date": "2025-06-30",
            "registered_days": 543, "max_total_source_bytes": 35_000_000_000,
        },
        "recovery_policy": {
            "maximum_process_attempts": 3,
            "restartable_failures": [
                "requests.exceptions.ReadTimeout", "requests.exceptions.ConnectTimeout",
                "requests.exceptions.ConnectionError",
                "requests.exceptions.ChunkedEncodingError",
                "urllib3.exceptions.ReadTimeoutError", "urllib3.exceptions.ProtocolError",
                "urllib3.exceptions.NewConnectionError",
            ],
            "nontransient_failures_are_terminal": True,
            "scope_change_allowed": False, "byte_cap_change_allowed": False,
            "protected_final_read_allowed": False,
        },
        "attempts": [{
            "attempt": 1, "pid": 12345,
            "terminal_status": "COMPLETE_543_DAYS_CACHE_VERIFIED",
            "committed_days": 543, "last_committed_date": "2025-06-30",
            "network_requests": 543 * 60,
            "new_transfer_bytes": 543 * 10,
            "stdout_path": stdout.relative_to(root).as_posix(),
            "stdout_sha256": sha256_file(stdout),
            "stderr_path": stderr.relative_to(root).as_posix(),
            "stderr_sha256": sha256_file(stderr),
        }],
        "protected_final_read": False,
    })


def _register_completion_metrics_correction(root: Path) -> Path:
    journal_path = root / readiness.ACQUISITION_ATTEMPTS_PATH
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["updated_at_utc"] = "2026-09-25T20:02:00.100000Z"
    journal["attempts"][-1]["ended_at_utc"] = "2026-09-25T20:02:00.000000Z"
    journal["attempts"][-1]["new_transfer_bytes"] = 0
    snapshot_path = (
        root / readiness.PRE_COMPLETION_CORRECTION_ACQUISITION_ATTEMPTS_PATH)
    write_json(snapshot_path, journal)

    progress_path = root / readiness.BULK_PROGRESS_PATH
    correction_path = root / readiness.COMPLETION_METRICS_CORRECTION_PATH
    request_sum = 543 * 60
    byte_sum = 543 * 10
    write_json(correction_path, {
        "schema_version": 1,
        "component": "v3_weather_acquisition_attempt4_completion_metrics_correction",
        "status": "REGISTERED_POST_COMPLETION_FROM_IMMUTABLE_DAILY_PROGRESS",
        "registered_at_utc": "2026-09-25T20:03:00.000000Z",
        "attempt": 1,
        "pid": 12345,
        "pre_correction_journal": {
            "path": readiness.PRE_COMPLETION_CORRECTION_ACQUISITION_ATTEMPTS_PATH.as_posix(),
            "sha256": sha256_file(snapshot_path),
        },
        "authoritative_final_progress": {
            "path": readiness.BULK_PROGRESS_PATH.as_posix(),
            "sha256": sha256_file(progress_path),
        },
        "prior_metrics": {
            "network_requests": request_sum,
            "new_transfer_bytes": 0,
        },
        "corrected_metrics": {
            "network_requests": request_sum,
            "new_transfer_bytes": byte_sum,
        },
        "daily_records": {
            "rows": 543,
            "network_requests_sum": request_sum,
            "new_transfer_bytes_sum": byte_sum,
        },
        "reason": readiness.COMPLETION_METRICS_CORRECTION_REASON,
        "scope_changed": False,
        "scientific_or_evaluation_policy_changed": False,
        "gate_changed": False,
        "protected_final_read": False,
    })
    journal["attempts"][-1]["new_transfer_bytes"] = byte_sum
    journal["completion_metrics_correction"] = {
        "path": readiness.COMPLETION_METRICS_CORRECTION_PATH.as_posix(),
        "sha256": sha256_file(correction_path),
    }
    write_json(journal_path, journal)
    return correction_path


def _rewrite_completion_correction_and_bind(root: Path, correction: dict) -> None:
    correction_path = root / readiness.COMPLETION_METRICS_CORRECTION_PATH
    write_json(correction_path, correction)
    journal_path = root / readiness.ACQUISITION_ATTEMPTS_PATH
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["completion_metrics_correction"]["sha256"] = sha256_file(
        correction_path)
    write_json(journal_path, journal)


def test_source_finalizer_requires_exact_543_day_terminal_state(
        tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    monkeypatch.setattr(readiness, "_verify_weather_component", lambda root, component: (
        {"hrrr": {"rows": 13032}} if component == "hrrr_local_observations"
        else {"rows": 8688}))
    for name in ("v3_hrrr_local_observations.json", "v3_gefs.json"):
        write_json(tmp_path / "data/manifests" / name, {"component": name})
    result = readiness.finalize_v3_source_feasibility(tmp_path)
    assert result["status"] == readiness.SOURCE_COMPLETE_STATUS
    assert result["completion_audit"]["days_complete"] == 543
    assert result["completion_audit"]["network_requests"] == 543 * 60
    assert result["completion_audit"]["acquisition_process_attempt_count"] == 1
    assert result["completion_audit"]["acquisition_process_attempt_statuses"] == [
        "COMPLETE_543_DAYS_CACHE_VERIFIED"]
    assert result["pilot"] == {"preserved": True}
    assert readiness.finalize_v3_source_feasibility(tmp_path) == result


def test_source_finalizer_requires_hash_bound_weather_policy_registration(
        tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    monkeypatch.setattr(readiness, "_verify_weather_component", lambda root, component: (
        {"hrrr": {"rows": 13032}} if component == "hrrr_local_observations"
        else {"rows": 8688}))
    for name in ("v3_hrrr_local_observations.json", "v3_gefs.json"):
        write_json(tmp_path / "data/manifests" / name, {"component": name})
    config = tmp_path / readiness.POLICY_CONFIG_PATH
    config.write_bytes(config.read_bytes() + b" ")
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="availability registration is missing or changed"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_requires_exact_v1_migration_lineage(
        tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    monkeypatch.setattr(readiness, "_verify_weather_component", lambda root, component: (
        {"hrrr": {"rows": 13032}} if component == "hrrr_local_observations"
        else {"rows": 8688}))
    for name in ("v3_hrrr_local_observations.json", "v3_gefs.json"):
        write_json(tmp_path / "data/manifests" / name, {"component": name})
    progress_path = tmp_path / readiness.NORMALIZATION_PROGRESS_PATH
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    by_date = {item["date"]: item for item in progress["days"]}
    by_date["2025-06-27"]["migrated_from_v1"] = False
    by_date["2025-06-28"]["migrated_from_v1"] = True
    write_json(progress_path, progress)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="normalization is not complete and offline"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_attempt_journal_accepts_hash_bound_completion_metrics_correction(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    _register_completion_metrics_correction(tmp_path)
    progress = json.loads(
        (tmp_path / readiness.BULK_PROGRESS_PATH).read_text(encoding="utf-8"))
    result = readiness._verify_acquisition_attempt_journal(tmp_path, progress)
    assert result["attempts"][-1]["new_transfer_bytes"] == 543 * 10


def test_attempt_journal_rejects_terminal_metric_not_equal_to_daily_sum(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    path = tmp_path / readiness.ACQUISITION_ATTEMPTS_PATH
    journal = json.loads(path.read_text(encoding="utf-8"))
    journal["attempts"][-1]["network_requests"] += 1
    write_json(path, journal)
    progress = json.loads(
        (tmp_path / readiness.BULK_PROGRESS_PATH).read_text(encoding="utf-8"))
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="Final acquisition process attempt is not complete"):
        readiness._verify_acquisition_attempt_journal(tmp_path, progress)


def test_attempt_journal_rejects_completion_correction_tamper(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    correction_path = _register_completion_metrics_correction(tmp_path)
    correction = json.loads(correction_path.read_text(encoding="utf-8"))
    correction["daily_records"]["new_transfer_bytes_sum"] += 1
    _rewrite_completion_correction_and_bind(tmp_path, correction)
    progress = json.loads(
        (tmp_path / readiness.BULK_PROGRESS_PATH).read_text(encoding="utf-8"))
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="completion-metrics correction provenance differs"):
        readiness._verify_acquisition_attempt_journal(tmp_path, progress)


def test_attempt_journal_rejects_pre_correction_snapshot_tamper(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    _register_completion_metrics_correction(tmp_path)
    snapshot = (
        tmp_path / readiness.PRE_COMPLETION_CORRECTION_ACQUISITION_ATTEMPTS_PATH)
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    progress = json.loads(
        (tmp_path / readiness.BULK_PROGRESS_PATH).read_text(encoding="utf-8"))
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="Pre-correction acquisition journal is missing or changed"):
        readiness._verify_acquisition_attempt_journal(tmp_path, progress)


def test_source_finalizer_refuses_one_missing_day(tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    progress_path = tmp_path / readiness.BULK_PROGRESS_PATH
    progress = json.loads(progress_path.read_text())
    progress["days"][-1]["status"] = "SOURCE_UNAVAILABLE"
    progress["days_complete"] = 542
    progress["days_unavailable"] = 1
    progress["coverage_complete"] = False
    write_json(progress_path, progress)
    with pytest.raises(readiness.V3SubstantiveReadinessError, match="543-day coverage"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_rejects_attempt_log_tamper(
        tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    monkeypatch.setattr(readiness, "_verify_weather_component", lambda root, component: (
        {"hrrr": {"rows": 13032}} if component == "hrrr_local_observations"
        else {"rows": 8688}))
    for name in ("v3_hrrr_local_observations.json", "v3_gefs.json"):
        write_json(tmp_path / "data/manifests" / name, {"component": name})
    readiness.finalize_v3_source_feasibility(tmp_path)
    (tmp_path / "runs/weather_v3_bulk.stdout.log").write_text(
        "tampered\n", encoding="utf-8")
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="process-attempt log is missing or changed"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_rejects_attempt_ceiling_violation(tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    path = tmp_path / readiness.ACQUISITION_ATTEMPTS_PATH
    body = json.loads(path.read_text(encoding="utf-8"))
    final = body["attempts"][0]
    body["attempts"] = [
        {
            **final, "attempt": number, "pid": 12340 + number,
            "terminal_status": (
                "COMPLETE_543_DAYS_CACHE_VERIFIED" if number == 4
                else "TRANSIENT_NETWORK_FAILURE"),
        }
        for number in range(1, 5)
    ]
    write_json(path, body)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="process-attempt inventory differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def _register_pretransfer_environment_replacement(root: Path) -> Path:
    path = root / readiness.ACQUISITION_ATTEMPTS_PATH
    body = json.loads(path.read_text(encoding="utf-8"))
    final = body["attempts"][0]
    stamps = [
        ("2026-09-25T18:00:00.000000Z", "2026-09-25T19:00:00.000000Z"),
        ("2026-09-25T19:01:00.000000Z", "2026-09-25T20:00:00.000000Z"),
        ("2026-09-25T20:01:00.000000Z", "2026-09-25T20:02:00.000000Z"),
    ]
    pre_attempts = [
        {
            **final, "attempt": number, "pid": 12340 + number,
            "started_at_utc": started, "ended_at_utc": ended,
            "terminal_status": (
                "TRANSIENT_READ_TIMEOUT" if number == 1
                else "TRANSIENT_NETWORK_FAILURE"),
            "committed_days": 6, "last_committed_date": "2025-01-10",
            **({
                "failure_class": "PermissionError.WinError10013",
                "failure_context": "local_socket_permission_denied",
                "recovery_exhausted": True,
                "recovery_exhaustion_reason":
                    "registered_process_attempt_ceiling_reached",
            } if number == 3 else {}),
        }
        for number, (started, ended) in enumerate(stamps, start=1)
    ]
    snapshot_path = root / readiness.PRE_AMENDMENT_ACQUISITION_ATTEMPTS_PATH
    write_json(snapshot_path, {
        "schema_version": 2,
        "component": body["component"],
        "registered_scope": body["registered_scope"],
        "attempts": pre_attempts,
        "protected_final_read": False,
        "updated_at_utc": "2026-09-25T20:02:00.100000+00:00",
        "recovery_policy": body["recovery_policy"],
    })
    amendment_path = root / readiness.PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH
    write_json(amendment_path, {
        "schema_version": 1,
        "component": "v3_weather_acquisition_pretransfer_environment_amendment",
        "status": "REGISTERED_BEFORE_REPLACEMENT_LAUNCH",
        "registered_at_utc": "2026-09-25T20:03:00.000000Z",
        "failed_attempt": 3,
        "failed_pid": pre_attempts[2]["pid"],
        "failure_class": "PermissionError.WinError10013",
        "failure_context": "local_socket_permission_denied",
        "pre_amendment_journal_sha256": sha256_file(snapshot_path),
        "failed_stdout_sha256": pre_attempts[2]["stdout_sha256"],
        "failed_stderr_sha256": pre_attempts[2]["stderr_sha256"],
        "eligibility": {
            "network_requests": 0,
            "new_transfer_bytes": 0,
            "failure_before_first_new_network_request": True,
        },
        "maximum_replacement_launches": 1,
        "scope_changed": False,
        "byte_cap_changed": False,
        "protected_final_read": False,
        "scientific_or_evaluation_policy_changed": False,
        "rationale": "Fixture environment replacement.",
    })
    body["schema_version"] = 3
    body["recovery_policy"].update({
        "maximum_pretransfer_environment_replacement_launches": 1,
        "maximum_total_process_launches": 4,
    })
    body["administrative_replacement_amendment"] = {
        "path": amendment_path.relative_to(root).as_posix(),
        "sha256": sha256_file(amendment_path),
    }
    archived_stdout = root / "runs/weather_v3_bulk.attempt-3.stdout.log"
    archived_stderr = root / "runs/weather_v3_bulk.attempt-3.stderr.log"
    shutil.copy2(root / final["stdout_path"], archived_stdout)
    shutil.copy2(root / final["stderr_path"], archived_stderr)
    current_failed = {
        **pre_attempts[2], "network_requests": 0, "new_transfer_bytes": 0,
        "administrative_replacement_eligible": True,
        "stdout_path": archived_stdout.relative_to(root).as_posix(),
        "stderr_path": archived_stderr.relative_to(root).as_posix(),
    }
    body["attempts"] = pre_attempts[:2] + [current_failed, {
        **final, "attempt": 4, "pid": 12344,
        "started_at_utc": "2026-09-25T20:04:00.000000Z",
        "terminal_status": "COMPLETE_543_DAYS_CACHE_VERIFIED",
        "administrative_replacement": True, "replacement_for_attempt": 3,
    }]
    write_json(path, body)
    return path


def test_source_finalizer_accepts_one_hash_bound_pretransfer_environment_replacement(
        tmp_path: Path, monkeypatch) -> None:
    _source_completion_fixture(tmp_path)
    _register_pretransfer_environment_replacement(tmp_path)
    monkeypatch.setattr(readiness, "_verify_weather_component", lambda root, component: (
        {"hrrr": {"rows": 13032}} if component == "hrrr_local_observations"
        else {"rows": 8688}))
    for name in ("v3_hrrr_local_observations.json", "v3_gefs.json"):
        write_json(tmp_path / "data/manifests" / name, {"component": name})
    result = readiness.finalize_v3_source_feasibility(tmp_path)
    assert result["completion_audit"]["acquisition_process_attempt_count"] == 4


def test_source_finalizer_rejects_environment_replacement_after_network_request(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    path = _register_pretransfer_environment_replacement(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["attempts"][2]["network_requests"] = 1
    write_json(path, body)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="environment replacement provenance differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_rejects_pre_amendment_journal_tamper(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    _register_pretransfer_environment_replacement(tmp_path)
    snapshot = tmp_path / readiness.PRE_AMENDMENT_ACQUISITION_ATTEMPTS_PATH
    snapshot.write_bytes(snapshot.read_bytes() + b" ")
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="Pre-amendment acquisition journal is missing or changed"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def _rewrite_amendment_and_bind(root: Path, amendment: dict) -> None:
    amendment_path = root / readiness.PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH
    write_json(amendment_path, amendment)
    journal_path = root / readiness.ACQUISITION_ATTEMPTS_PATH
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["administrative_replacement_amendment"]["sha256"] = sha256_file(
        amendment_path)
    write_json(journal_path, journal)


@pytest.mark.parametrize("field,value", [
    ("failed_pid", 99999),
    ("failed_stdout_sha256", "a" * 64),
    ("failed_stderr_sha256", "b" * 64),
])
def test_source_finalizer_rejects_unbound_failed_attempt_evidence(
        tmp_path: Path, field: str, value) -> None:
    _source_completion_fixture(tmp_path)
    _register_pretransfer_environment_replacement(tmp_path)
    amendment_path = tmp_path / readiness.PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment[field] = value
    _rewrite_amendment_and_bind(tmp_path, amendment)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="environment replacement provenance differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_rejects_amendment_registered_after_replacement(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    _register_pretransfer_environment_replacement(tmp_path)
    amendment_path = tmp_path / readiness.PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["registered_at_utc"] = "2026-09-25T20:05:00.000000Z"
    _rewrite_amendment_and_bind(tmp_path, amendment)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="environment replacement ordering differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def test_source_finalizer_rejects_changed_eligibility_object(
        tmp_path: Path) -> None:
    _source_completion_fixture(tmp_path)
    _register_pretransfer_environment_replacement(tmp_path)
    amendment_path = tmp_path / readiness.PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["eligibility"]["failure_before_first_new_network_request"] = False
    _rewrite_amendment_and_bind(tmp_path, amendment)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="environment amendment differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


@pytest.mark.parametrize("attempt_index,field,value,error", [
    (0, "committed_days", 7,
     "Pre-amendment acquisition attempts changed after registration"),
    (1, "started_at_utc", "2026-09-25T19:02:00.000000Z",
     "Pre-amendment acquisition attempts changed after registration"),
    (2, "ended_at_utc", "2026-09-25T20:02:01.000000Z",
     "Acquisition environment replacement provenance differs"),
    (0, "unexpected_field", "not-registered",
     "Pre-amendment acquisition attempts changed after registration"),
    (2, "unexpected_field", "not-registered",
     "Acquisition environment replacement provenance differs"),
])
def test_source_finalizer_rejects_any_unregistered_attempt_mutation(
        tmp_path: Path, attempt_index: int, field: str, value, error: str) -> None:
    _source_completion_fixture(tmp_path)
    path = _register_pretransfer_environment_replacement(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["attempts"][attempt_index][field] = value
    write_json(path, body)
    with pytest.raises(readiness.V3SubstantiveReadinessError, match=error):
        readiness.finalize_v3_source_feasibility(tmp_path)


@pytest.mark.parametrize("field,value", [
    ("stdout_path", "runs/weather_v3_bulk.stdout.log"),
    ("stderr_path", "runs/weather_v3_bulk.stderr.log"),
])
def test_source_finalizer_requires_documented_attempt_three_log_archival(
        tmp_path: Path, field: str, value: str) -> None:
    _source_completion_fixture(tmp_path)
    path = _register_pretransfer_environment_replacement(tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["attempts"][2][field] = value
    write_json(path, body)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="environment replacement provenance differs"):
        readiness.finalize_v3_source_feasibility(tmp_path)


def _issuance_fixture(root: Path, monkeypatch) -> list[dict[str, str]]:
    (root / "configs").mkdir(parents=True)
    (root / "schemas").mkdir(parents=True)
    shutil.copy2(PROJECT / "configs/v3_goal.json", root / "configs/v3_goal.json")
    shutil.copy2(PROJECT / "schemas/research-plan-v3.schema.json",
                 root / "schemas/research-plan-v3.schema.json")
    (root / "src/klax_lab").mkdir(parents=True)
    (root / "src/klax_lab/probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    frozen_manifest = root / "data/frozen/v3/manifest.json"
    frozen_fold = root / "data/frozen/v3/folds.json"
    write_json(frozen_manifest, {"dataset_id": "d" * 64})
    write_json(frozen_fold, {"folds_id": "f" * 64})
    records = []
    for item in REQUIRED_COMPONENT_MANIFESTS:
        body = {"component": item.component, "status": "PASS",
                "protected_final_read": False}
        if item.component == "frozen_dataset":
            body.update({"dataset_id": "d" * 64,
                         "dataset_manifest_path": "data/frozen/v3/manifest.json"})
        if item.component == "five_fold_split":
            body.update({"folds_id": "f" * 64,
                         "fold_path": "data/frozen/v3/folds.json"})
        write_json(root / item.path, body)
        records.append({"component": item.component, "path": item.path,
                        "sha256": sha256_file(root / item.path), "status": "PASS"})
    monkeypatch.setattr(readiness, "validate_all_v3_components", lambda root: records)
    return records


def _register_campaign1_incident_fixture(
    root: Path,
    monkeypatch,
    *,
    repair_variant: str | None = None,
) -> dict[str, str]:
    """Create a complete isolated real-amendment fixture under ``root``."""
    goal = json.loads((root / "configs/v3_goal.json").read_text(encoding="utf-8"))
    data_binding = readiness._data_bundle(root)
    ranking_sha = canonical_hash({"rule": campaign.CHAMPION_RANKING_RULE})
    scientific = {
        "config_sha256": sha256_file(root / "configs/v3_goal.json"),
        "schema_sha256": sha256_file(root / "schemas/research-plan-v3.schema.json"),
        "data_bundle_version": data_binding["version"],
        "data_bundle_sha256": data_binding["sha256"],
        "evaluation_policy_sha256": evaluation_policy_sha256(),
        "promotion_gates_sha256": canonical_hash(goal["development_promotion_gates"]),
        "campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "partition_contract_sha256": canonical_hash(goal["partitions"]),
        "champion_ranking_rule_sha256": ranking_sha,
        "search_dsl_changed": False,
        "target_return_changed": False,
        "data_or_labels_changed": False,
        "gates_or_budget_changed": False,
    }

    campaign_root = root / incident.FAILED_CAMPAIGN_PATH
    completed = "2026-09-26T15:21:03.971455+00:00"
    summary = {
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "status": "OFFLINE_CAMPAIGN_COMPLETE",
        "scientific_conclusion": incident.FAILED_CONCLUSION,
        "stopped_reason": incident.FAILED_STOP_REASON,
        "budget_used": {"executed_candidates": 0},
        "completed_at_utc": completed,
        "protected_final_evaluated": False,
        "protected_final_authorization_issued": False,
        "actual_orders_placed": False,
    }
    write_json(campaign_root / "summary.json", summary)
    queue = []
    response_hashes = []
    for index in range(1, 8):
        task_id = f"e01-initial_inde-{index:02d}"
        response_path = campaign_root / "tasks" / task_id / "response.json"
        write_json(response_path, {"task_id": task_id, "action": "reject"})
        response_hashes.append(sha256_file(response_path))
        queue.append({"task_id": task_id, "status": "NONPROPOSAL"})
    queue.append({"task_id": "e01-initial_inde-08", "status": "ADMITTED"})
    state_body = {
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "phase": "COMPLETE",
        "engine": {"stopped_reason": incident.FAILED_STOP_REASON},
        "queue": queue,
        "protected_final_evaluated": False,
        "actual_orders_placed": False,
    }
    write_json(campaign_root / "recovery-state.json", {
        **state_body, "state_sha256": canonical_hash(state_body)})
    write_json(campaign_root / "search-coverage.json", {
        "integrity_failures": [{
            "stage": "primary_evaluation", "exception_type": "ValueError"}]})
    write_json(campaign_root / "candidate-register.json", [{
        "candidate_id": "v3-candidate-f5f2fd485512dbcb45e0"}])
    (campaign_root / "report.md").write_text(
        "# Immutable failed campaign fixture\n", encoding="utf-8")
    artifact_rows = []
    for path in sorted(item for item in campaign_root.rglob("*") if item.is_file()):
        artifact_rows.append({
            "path": path.relative_to(campaign_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    artifact_body = {
        "actual_orders_placed": False,
        "artifacts": artifact_rows,
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "manifest_version": "fixture-v1",
        "network_used": False,
        "protected_final_read": False,
        "report_version": "fixture-v1",
    }
    write_json(campaign_root / "campaign-artifacts.json", {
        **artifact_body, "manifest_sha256": canonical_hash(artifact_body)})

    probe_path = root / "src/klax_lab/probe.py"
    probe_after = sha256_file(probe_path)
    probe_before = "1" * 64
    old_code_inventory = [{
        "path": "src/klax_lab/probe.py", "bytes": probe_path.stat().st_size,
        "sha256": probe_before,
    }]
    auxiliary_path = root / "src/klax_lab/auxiliary.py"
    if repair_variant == "modified_unlisted":
        auxiliary_path.write_text("VALUE = 'old'\n", encoding="utf-8")
        old_code_inventory.append({
            "path": "src/klax_lab/auxiliary.py",
            "bytes": auxiliary_path.stat().st_size,
            "sha256": sha256_file(auxiliary_path),
        })
        old_code_inventory.sort(key=lambda row: row["path"])
    elif repair_variant == "removed_unlisted":
        old_code_inventory.append({
            "path": "src/klax_lab/removed.py", "bytes": 14,
            "sha256": "2" * 64,
        })
        old_code_inventory.sort(key=lambda row: row["path"])
    archived_readiness = {
        **{key: scientific[key] for key in (
            "config_sha256", "schema_sha256", "evaluation_policy_sha256",
            "promotion_gates_sha256", "campaign_budget_sha256",
            "partition_contract_sha256", "champion_ranking_rule_sha256")},
        "data_bundle": {
            "version": scientific["data_bundle_version"],
            "sha256": scientific["data_bundle_sha256"],
        },
        "code_inventory": old_code_inventory,
        "code_sha256": canonical_hash(old_code_inventory),
    }
    archived_ticket = {
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "one_use": True,
        "synthetic": False,
        **{key: scientific[key] for key in (
            "config_sha256", "schema_sha256", "data_bundle_version",
            "data_bundle_sha256", "evaluation_policy_sha256",
            "promotion_gates_sha256", "campaign_budget_sha256",
            "partition_contract_sha256", "champion_ranking_rule_sha256")},
    }
    archive_base = root / incident.ARCHIVE_MANIFEST_PATH.parent
    archived_sources = {
        "data/manifests/v3_readiness.json": archived_readiness,
        "data/manifests/v3_data_bundle.json": json.loads(
            (root / "data/manifests/v3_data_bundle.json").read_text(encoding="utf-8")),
        "runs/v3_offline_campaign_ticket.json": archived_ticket,
    }
    archive_rows = []
    archived_by_original = {}
    for original, body in archived_sources.items():
        path = archive_base / original
        write_json(path, body)
        archived_by_original[original] = path
    archived_readiness_sha = sha256_file(
        archived_by_original["data/manifests/v3_readiness.json"])
    archived_ticket_sha = sha256_file(
        archived_by_original["runs/v3_offline_campaign_ticket.json"])
    claim = {
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "ticket_sha256": archived_ticket_sha,
        "readiness_sha256": archived_readiness_sha,
    }
    claim_original = "runs/v3_offline_campaign_ticket.json.claimed.json"
    claim_path = archive_base / claim_original
    write_json(claim_path, claim)
    archived_by_original[claim_original] = claim_path
    transition_names = {
        "BASE": "runs/v3_offline_campaign_ticket.json.bootstrap.json",
        "CLAIMED": "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
        "RECOVERY_CONSUMED":
            "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
        "COMMITTED": "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
    }
    for transition, original in transition_names.items():
        path = archive_base / original
        write_json(path, {
            "campaign_id": incident.FAILED_CAMPAIGN_ID,
            "status": "PREPARED" if transition == "BASE" else transition,
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        })
        archived_by_original[original] = path
    campaign_id_original = "runs/weather_v3_campaign.id"
    campaign_id_path = archive_base / campaign_id_original
    campaign_id_path.parent.mkdir(parents=True, exist_ok=True)
    campaign_id_path.write_text(incident.FAILED_CAMPAIGN_ID, encoding="utf-8")
    archived_by_original[campaign_id_original] = campaign_id_path
    for original, path in sorted(archived_by_original.items()):
        archive_rows.append({
            "archived_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "original_path": original,
            "sha256": sha256_file(path),
        })
    archive_manifest = {
        "archive_version": "klax-v3-stale-authorization-archive-v1",
        "archived_at_utc": "2026-09-26T15:54:18+00:00",
        "failed_campaign_id": incident.FAILED_CAMPAIGN_ID,
        "files": archive_rows,
        "preserved_campaign_path": incident.FAILED_CAMPAIGN_PATH.as_posix(),
        "protected_final_read": False,
        "status": "ARCHIVED_BYTE_FOR_BYTE_BEFORE_REPLACEMENT_AUTHORIZATION",
    }
    write_json(root / incident.ARCHIVE_MANIFEST_PATH, archive_manifest)
    archive_manifest_sha = sha256_file(root / incident.ARCHIVE_MANIFEST_PATH)
    monkeypatch.setattr(incident, "ARCHIVE_MANIFEST_SHA256", archive_manifest_sha)

    diagnostic_root = root / incident.DIAGNOSTIC_MANIFEST_PATH.parent
    diagnostic_artifact = diagnostic_root / "mechanical-check.json"
    write_json(diagnostic_artifact, {"mechanical_execution": "PASS"})
    diagnostic_binding = {
        "src/klax_lab/probe.py": probe_after,
    }
    monkeypatch.setattr(
        incident, "EXPECTED_DIAGNOSTIC_CODE_BINDING", diagnostic_binding)
    diagnostic = {
        "actual_orders_placed": False,
        "artifacts": [{
            "path": diagnostic_artifact.relative_to(root).as_posix(),
            "sha256": sha256_file(diagnostic_artifact),
        }],
        "candidate_id": "v3-candidate-f5f2fd485512dbcb45e0",
        "candidate_plan_sha256":
            "f5f2fd485512dbcb45e0592766984bd5e17e00445f23378e663d39b6c10158a0",
        "component": incident.DIAGNOSTIC_COMPONENT,
        "contains_development_results": True,
        "diagnostic_code_binding": [
            {"path": path, "sha256": digest}
            for path, digest in sorted(diagnostic_binding.items())],
        "diagnostic_completed_at_utc": "2026-09-26T15:34:28+00:00",
        "eligible_as_replacement_campaign_evidence": False,
        "eligible_as_seed_parent_or_ranking_input": False,
        "eligible_for_promotion_or_protected_final_authorization": False,
        "network_used": False,
        "performance_metrics_consulted_to_select_or_modify_scientific_parameters": False,
        "protected_final_read": False,
        "purpose": "mechanical evaluator execution verification after repair",
        "quarantine_registered_at_utc": "2026-09-26T15:55:04+00:00",
        "schema_version": 1,
        "status": incident.DIAGNOSTIC_STATUS,
    }
    write_json(root / incident.DIAGNOSTIC_MANIFEST_PATH, diagnostic)
    diagnostic_sha = sha256_file(root / incident.DIAGNOSTIC_MANIFEST_PATH)
    monkeypatch.setattr(incident, "DIAGNOSTIC_MANIFEST_SHA256", diagnostic_sha)

    if repair_variant == "new_unlisted":
        auxiliary_path.write_text("VALUE = 'new'\n", encoding="utf-8")
    elif repair_variant == "modified_unlisted":
        auxiliary_path.write_text("VALUE = 'changed'\n", encoding="utf-8")

    current_inventory = [{
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in sorted((root / "src/klax_lab").glob("*.py"))]
    code_sha = canonical_hash(current_inventory)
    write_json(root / incident.CODE_INVENTORY_PATH, {
        "schema_version": 1,
        "component": incident.CODE_INVENTORY_COMPONENT,
        "code_inventory": current_inventory,
        "code_sha256": code_sha,
        "protected_final_read": False,
        "network_used": False,
    })

    failed_files = {
        key: {
            "path": (incident.FAILED_CAMPAIGN_PATH / name).as_posix(),
            "sha256": sha256_file(campaign_root / name),
        } for key, name in {
            "summary": "summary.json", "recovery_state": "recovery-state.json",
            "search_coverage": "search-coverage.json", "report": "report.md",
            "candidate_register": "candidate-register.json",
        }.items()
    }
    campaign_artifacts = json.loads(
        (campaign_root / "campaign-artifacts.json").read_text(encoding="utf-8"))
    archive_index = {row["original_path"]: row for row in archive_rows}
    bootstrap_chain = [{
        "transition": transition,
        "path": archive_index[original]["archived_path"],
        "sha256": archive_index[original]["sha256"],
    } for transition, original in transition_names.items()]
    replacement = {
        "campaign_id": "v3-offline-replacement-fixture",
        "readiness_path": readiness.READINESS_PATH.as_posix(),
        "ticket_path": readiness.TICKET_PATH.as_posix(),
        "claim_path": readiness.TICKET_PATH.as_posix() + ".claimed.json",
    }
    amendment = {
        "schema_version": 1,
        "component": incident.AMENDMENT_COMPONENT,
        "amendment_id": "campaign1-integrity-repair-fixture",
        "status": incident.AMENDMENT_STATUS,
        "registered_at_utc": "2026-09-26T16:00:00+00:00",
        "failed_campaign": {
            "campaign_id": incident.FAILED_CAMPAIGN_ID,
            "campaign_path": incident.FAILED_CAMPAIGN_PATH.as_posix(),
            "completed_at_utc": completed,
            "scientific_conclusion": incident.FAILED_CONCLUSION,
            "stop_reason": incident.FAILED_STOP_REASON,
            "executed_candidates": 0,
            "campaign_artifacts": {
                "path": (incident.FAILED_CAMPAIGN_PATH / "campaign-artifacts.json").as_posix(),
                "sha256": sha256_file(campaign_root / "campaign-artifacts.json"),
                "manifest_sha256": campaign_artifacts["manifest_sha256"],
                "artifact_count": len(campaign_artifacts["artifacts"]),
            },
            **failed_files,
            "archived_authorization": {
                "manifest_path": incident.ARCHIVE_MANIFEST_PATH.as_posix(),
                "manifest_sha256": archive_manifest_sha,
                "ticket_path": archive_index[
                    "runs/v3_offline_campaign_ticket.json"]["archived_path"],
                "ticket_sha256": archive_index[
                    "runs/v3_offline_campaign_ticket.json"]["sha256"],
                "claim_path": archive_index[claim_original]["archived_path"],
                "claim_sha256": archive_index[claim_original]["sha256"],
                "bootstrap_chain": bootstrap_chain,
            },
            "network_used": False,
            "protected_final_read": False,
            "actual_orders_placed": False,
        },
        "trigger_evidence": {
            "basis": "preserved campaign integrity and protocol defects",
            "failed_campaign_performance_metrics_available": False,
            "proposal_protocol_defect": {
                "accepted_nonproposal_responses": 7,
                "admitted_proposals": 1,
                "finding": "Seven valid nonproposal responses preceded one admission.",
                "response_sha256s": response_hashes,
            },
            "primary_evaluator_integrity_defect": {
                "exception_type": "ValueError",
                "finding": "A boundary quote reached the ROI primitive before screening.",
                "reproduced_case": {
                    "settlement_date": "2025-02-07",
                    "ticker": "KXHIGHLAX-25FEB07-T56",
                    "side": "NO",
                    "observed_at_utc": "2025-02-07T07:00:00+00:00",
                    "yes_bid_dollars": "0.0000",
                    "yes_ask_dollars": "0.0300",
                    "derived_entry_price_dollars": "1.0000",
                    "exception_type": "ValueError",
                    "exception_message": "entry price must be strictly between $0 and $1",
                    "required_disposition": "ENTRY_PRICE_OUTSIDE_CONTROL_BAND",
                },
            },
        },
        "unchanged_scientific_contract": scientific,
        "repair_scope": {
            "allowed_changes": ["src/klax_lab/probe.py"],
            "changed_code_pre_post": [{
                "path": "src/klax_lab/probe.py",
                "before": probe_before,
                "after": probe_after,
            }],
            "post_repair_code_inventory": {
                "path": incident.CODE_INVENTORY_PATH.as_posix(),
                "sha256": sha256_file(root / incident.CODE_INVENTORY_PATH),
                "code_sha256": code_sha,
            },
            "scope_expansion": False,
        },
        "diagnostic_quarantine": {
            "manifest": {
                "path": incident.DIAGNOSTIC_MANIFEST_PATH.as_posix(),
                "sha256": diagnostic_sha,
            },
            "diagnostic_completed_at_utc": diagnostic["diagnostic_completed_at_utc"],
            "quarantine_registered_at_utc": diagnostic["quarantine_registered_at_utc"],
            "purpose": diagnostic["purpose"],
            "contains_development_results": True,
            "eligible_as_replacement_evidence": False,
            "eligible_as_seed_parent_or_ranking_input": False,
            "eligible_for_promotion_or_protected_final_authorization": False,
            "performance_metrics_consulted_to_select_or_modify_scientific_parameters": False,
            "must_remain_outside_data_bundle_and_campaign_evidence": True,
        },
        "scientific_independence": {
            "repairs_triggered_only_by_integrity_or_protocol_defects": True,
            "evaluator_repair_fixed_before_diagnostic_run": True,
            "diagnostic_results_existed_before_later_protocol_edits": True,
            "later_protocol_edits_derived_from_saved_worker_and_schema_evidence": True,
            "diagnostic_performance_used_to_select_or_modify_scientific_parameters": False,
            "diagnostic_evidence_used_for_candidate_generation": False,
            "diagnostic_evidence_used_for_candidate_ranking": False,
            "diagnostic_evidence_used_for_seed_or_parent_selection": False,
            "development_or_protected_results_used_to_change_scientific_contract": False,
            "protected_final_results_observed": False,
            "owner_attestation": incident.OWNER_ATTESTATION,
        },
        "replacement_constraints": {
            **replacement,
            "must_differ_from_failed_campaign_id": True,
            "must_not_resume_failed_campaign": True,
            "must_not_overwrite_or_remove_failed_ticket_or_claim": True,
            "must_use_fresh_readiness_and_one_use_ticket": True,
            "ticket_must_bind_this_amendment_sha256": True,
            "readiness_must_bind_this_amendment_sha256": True,
            "claim_path_must_be_derived_from_new_ticket_path": True,
            "prelaunch_claim_path_must_not_exist": True,
            "old_ticket_or_claim_reusable": False,
        },
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    write_json(root / incident.AMENDMENT_PATH, amendment)
    return replacement


def test_issuer_creates_loader_compatible_hash_bound_one_use_pair(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    replacement = _register_campaign1_incident_fixture(tmp_path, monkeypatch)
    # Production starts from the byte-for-byte authorization archive, where
    # the old active bundle no longer exists. Issuance must reconstruct it
    # deterministically before it verifies the unchanged scientific contract.
    (tmp_path / "data/manifests/v3_data_bundle.json").unlink()
    stamp = datetime(2026, 9, 26, 16, 1, tzinfo=timezone.utc)
    ready, ticket = readiness.issue_v3_campaign_readiness(
        tmp_path, replacement["campaign_id"],
        readiness_path=Path(replacement["readiness_path"]),
        ticket_path=Path(replacement["ticket_path"]),
        now=lambda: stamp)
    assert ready["synthetic"] is False and ticket["one_use"] is True
    assert ready["protected_final_access_authorized"] is False
    assert ready["incident_amendment"] == {
        "path": incident.AMENDMENT_PATH.as_posix(),
        "sha256": sha256_file(tmp_path / incident.AMENDMENT_PATH),
        "amendment_id": "campaign1-integrity-repair-fixture",
        "failed_campaign_id": incident.FAILED_CAMPAIGN_ID,
        "replacement_campaign_id": replacement["campaign_id"],
    }
    assert ticket["incident_amendment_id"] == ready["incident_amendment"]["amendment_id"]
    assert ticket["incident_amendment_sha256"] == ready["incident_amendment"]["sha256"]
    bundle = json.loads((tmp_path / "data/manifests/v3_data_bundle.json").read_text())
    assert bundle["bundle_sha256"] == ready["data_bundle"]["sha256"]
    monkeypatch.setattr(campaign, "verify_component_fixtures", lambda root: {
        "status": "PASS", "protected_final_read": False, "campaign_authorized": False})
    authorization = campaign.load_v3_campaign_authorization(
        tmp_path, Path(replacement["readiness_path"]), Path(replacement["ticket_path"]))
    assert authorization.campaign_id == replacement["campaign_id"]
    assert authorization.incident_amendment_path == (
        tmp_path / incident.AMENDMENT_PATH).resolve()
    assert authorization.incident_amendment_sha256 == ready["incident_amendment"]["sha256"]
    assert authorization.incident_amendment_id == "campaign1-integrity-repair-fixture"
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="incident amendment"):
        readiness.issue_v3_campaign_readiness(
            tmp_path, replacement["campaign_id"],
            readiness_path=Path(replacement["readiness_path"]),
            ticket_path=Path(replacement["ticket_path"]))


def test_issuer_selects_packet_repair_amendment_when_registered(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    amendment_path = tmp_path / packet_incident.AMENDMENT_PATH
    write_json(amendment_path, {"registered": True})
    replacement = {
        "campaign_id": "v3-offline-continuation-fixture",
        "readiness_path": "data/manifests/v3-readiness-continuation.json",
        "ticket_path": "runs/v3-continuation-ticket.json",
        "claim_path": "runs/v3-continuation-ticket.json.claimed.json",
    }
    verified = {
        "path": packet_incident.AMENDMENT_PATH.as_posix(),
        "sha256": "9" * 64,
        "amendment_id": "campaign2-packet-repair-fixture",
        "registered_at_utc": "2026-09-26T17:00:00+00:00",
        "source_campaign_id": packet_incident.FAILED_CAMPAIGN_ID,
        "replacement_campaign_id": replacement["campaign_id"],
        "replacement_readiness_path": replacement["readiness_path"],
        "replacement_ticket_path": replacement["ticket_path"],
        "replacement_claim_path": replacement["claim_path"],
        "continuation_import_state_sha256": "8" * 64,
        "denied_plan_sha256s": [packet_incident.DENIED_DIAGNOSTIC_PLAN_SHA256],
    }
    calls = []

    def verify_packet(root, *, expected_champion_ranking_rule_sha256,
                      pre_issuance=False):
        calls.append((root, expected_champion_ranking_rule_sha256, pre_issuance))
        return verified

    monkeypatch.setattr(
        readiness, "verify_campaign2_packet_repair_amendment", verify_packet)
    monkeypatch.setattr(
        readiness, "verify_campaign1_integrity_repair_amendment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("first amendment fallback must not run")))

    ready, ticket = readiness.issue_v3_campaign_readiness(
        tmp_path, replacement["campaign_id"],
        readiness_path=Path(replacement["readiness_path"]),
        ticket_path=Path(replacement["ticket_path"]),
        now=lambda: datetime(2026, 9, 26, 17, 1, tzinfo=timezone.utc))

    assert len(calls) == 1 and calls[0][2] is True
    assert ready["incident_amendment"] == {
        "path": verified["path"],
        "sha256": verified["sha256"],
        "amendment_id": verified["amendment_id"],
        "source_campaign_id": verified["source_campaign_id"],
        "replacement_campaign_id": verified["replacement_campaign_id"],
        "continuation_import_state_sha256": verified[
            "continuation_import_state_sha256"],
    }
    assert ticket["incident_amendment_id"] == verified["amendment_id"]
    assert ticket["incident_amendment_sha256"] == verified["sha256"]


def test_issuer_does_not_fallback_when_packet_repair_manifest_is_invalid(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    write_json(tmp_path / packet_incident.AMENDMENT_PATH, {"invalid": True})
    first_called = False

    def invalid_packet(*args, **kwargs):
        raise packet_incident.V3PacketIncidentAmendmentError(
            "invalid packet amendment fixture")

    def first_fallback(*args, **kwargs):
        nonlocal first_called
        first_called = True
        return {}

    monkeypatch.setattr(
        readiness, "verify_campaign2_packet_repair_amendment", invalid_packet)
    monkeypatch.setattr(
        readiness, "verify_campaign1_integrity_repair_amendment", first_fallback)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="incident amendment"):
        readiness.issue_v3_campaign_readiness(
            tmp_path, "v3-offline-continuation-fixture",
            readiness_path=Path("data/manifests/v3-readiness-continuation.json"),
            ticket_path=Path("runs/v3-continuation-ticket.json"))
    assert first_called is False


def test_loader_detects_code_changed_after_ticket_issuance(tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    replacement = _register_campaign1_incident_fixture(tmp_path, monkeypatch)
    readiness.issue_v3_campaign_readiness(
        tmp_path, replacement["campaign_id"],
        readiness_path=Path(replacement["readiness_path"]),
        ticket_path=Path(replacement["ticket_path"]),
        now=lambda: datetime(2026, 9, 26, 16, 1, tzinfo=timezone.utc))
    (tmp_path / "src/klax_lab/probe.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(campaign, "verify_component_fixtures", lambda root: {
        "status": "PASS", "protected_final_read": False, "campaign_authorized": False})
    with pytest.raises(campaign.V3ReadinessRefusal, match="incident amendment"):
        campaign.load_v3_campaign_authorization(
            tmp_path, Path(replacement["readiness_path"]),
            Path(replacement["ticket_path"]))


def test_real_issuer_refuses_missing_incident_amendment(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    with pytest.raises(
            readiness.V3SubstantiveReadinessError,
            match="incident amendment"):
        readiness.issue_v3_campaign_readiness(
            tmp_path, "v3-offline-replacement-fixture",
            readiness_path=readiness.READINESS_PATH,
            ticket_path=readiness.TICKET_PATH)


def test_loader_refuses_tampered_incident_amendment(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    replacement = _register_campaign1_incident_fixture(tmp_path, monkeypatch)
    readiness.issue_v3_campaign_readiness(
        tmp_path, replacement["campaign_id"],
        readiness_path=Path(replacement["readiness_path"]),
        ticket_path=Path(replacement["ticket_path"]),
        now=lambda: datetime(2026, 9, 26, 16, 1, tzinfo=timezone.utc))
    amendment_path = tmp_path / incident.AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["network_used"] = True
    write_json(amendment_path, amendment)
    monkeypatch.setattr(campaign, "verify_component_fixtures", lambda root: {
        "status": "PASS", "protected_final_read": False, "campaign_authorized": False})
    with pytest.raises(campaign.V3ReadinessRefusal, match="incident amendment"):
        campaign.load_v3_campaign_authorization(
            tmp_path, Path(replacement["readiness_path"]),
            Path(replacement["ticket_path"]))


def _verify_incident_fixture(root: Path) -> None:
    incident.verify_campaign1_integrity_repair_amendment(
        root,
        expected_champion_ranking_rule_sha256=canonical_hash({
            "rule": campaign.CHAMPION_RANKING_RULE,
        }),
    )


def test_incident_scope_refuses_unlisted_new_source(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    _register_campaign1_incident_fixture(
        tmp_path, monkeypatch, repair_variant="new_unlisted")
    with pytest.raises(
            incident.V3IncidentAmendmentError,
            match="Allowed repair files differ"):
        _verify_incident_fixture(tmp_path)


def test_incident_scope_refuses_unlisted_modified_source(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    _register_campaign1_incident_fixture(
        tmp_path, monkeypatch, repair_variant="modified_unlisted")
    with pytest.raises(
            incident.V3IncidentAmendmentError,
            match="Allowed repair files differ"):
        _verify_incident_fixture(tmp_path)


def test_incident_scope_refuses_removed_archived_source(
        tmp_path: Path, monkeypatch) -> None:
    _issuance_fixture(tmp_path, monkeypatch)
    _register_campaign1_incident_fixture(
        tmp_path, monkeypatch, repair_variant="removed_unlisted")
    with pytest.raises(
            incident.V3IncidentAmendmentError,
            match="removed files"):
        _verify_incident_fixture(tmp_path)
