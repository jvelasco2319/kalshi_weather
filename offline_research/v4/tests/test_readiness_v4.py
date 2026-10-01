from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from v4 import readiness_v4 as readiness
from v4 import local_worker_v4


ROOT = Path(__file__).resolve().parents[2]


def _copy_registration(root: Path) -> None:
    for relative in (
        readiness.CONFIG_PATH,
        readiness.CONTRACT_PATH,
        readiness.EXECUTION_CONFIG_PATH,
        readiness.V3_GOAL_PATH,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)


def _copy_stage0_binding(root: Path) -> Path:
    registration = json.loads(
        (ROOT / readiness.STAGE0_PARENT_PATH).read_text(encoding="utf-8"))
    for relative in (
        readiness.STAGE0_PARENT_PATH,
        readiness.STAGE0_FUNNEL_PATH,
        Path(registration["compiled_manifest_path"]),
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return root / readiness.STAGE0_PARENT_PATH


def _copy_decision_time_amendment(root: Path) -> Path:
    for relative in (
        readiness.DECISION_TIME_AMENDMENT_PATH,
        readiness.DECISION_TIME_AUDIT_PATH,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return root / readiness.DECISION_TIME_AMENDMENT_PATH


def _rewrite_stage0_registration(path: Path, value: dict) -> None:
    body = {key: item for key, item in value.items()
            if key != "registration_sha256"}
    value["registration_sha256"] = readiness.canonical_hash(body)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _record(path: str, token: str) -> dict:
    return {"path": path, "bytes": 1, "sha256": token * 64}


def _bindings() -> dict:
    return {
        "config": _record(readiness.CONFIG_PATH.as_posix(), "1"),
        "contract": _record(readiness.CONTRACT_PATH.as_posix(), "2"),
        "execution_config": _record(readiness.EXECUTION_CONFIG_PATH.as_posix(), "3"),
        "stage0_training_only_parent": {
            "registration": _record(
                readiness.STAGE0_PARENT_PATH.as_posix(), "3"),
            "registration_sha256": (
                readiness.EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256),
            "registration_semantic_sha256": "3" * 64,
            "stage0_funnel_code": _record(
                readiness.STAGE0_FUNNEL_PATH.as_posix(), "3"),
            "compiled_manifest": _record(
                "runs/campaigns_v3/fixture/compiled_manifest.json", "3"),
            "source_campaign_id": readiness.V3_CAMPAIGN_ID,
            "source_plan_sha256": "3" * 64,
            "forbidden_operators": [
                "calibration", "market_residual", "conformal", "abstention",
                "fit", "scoring"],
            "runtime_reference_sha256": "3" * 64,
            "settlement_labels_read": False,
            "profit_calculated": False,
            "protected_final_read": False,
        },
        "v4_code_inventory": [_record("v4/readiness_v4.py", "4")],
        "v4_code_sha256": "5" * 64,
        "v3_goal": _record(readiness.V3_GOAL_PATH.as_posix(), "6"),
        "v3_schema": _record(readiness.V3_SCHEMA_PATH.as_posix(), "7"),
        "v3_code_inventory": {
            "path": readiness.V3_CODE_INVENTORY_PATH.as_posix(),
            "sha256": "8" * 64,
            "code_sha256": readiness.EXPECTED_V3_CODE_SHA256,
            "file_count": 69,
        },
        "v3_science_code_inventory": [
            _record("src/klax_lab/evaluator_v3.py", "9")],
        "v3_science_code_sha256": "a" * 64,
        "v3_campaign": {
            "path": readiness.V3_CAMPAIGN_MANIFEST_PATH.as_posix(),
            "sha256": readiness.EXPECTED_V3_CAMPAIGN_FILE_SHA256,
            "manifest_sha256": readiness.EXPECTED_V3_CAMPAIGN_MANIFEST_SHA256,
            "artifact_count": 1007,
            "campaign_id": readiness.V3_CAMPAIGN_ID,
            "protected_final_read": False,
            "actual_orders_placed": False,
        },
        "data_bundle": {
            "path": readiness.V4_DATA_BUNDLE_PATH.as_posix(),
            "manifest_sha256": readiness.EXPECTED_V4_DATA_BUNDLE_FILE_SHA256,
            "version": readiness.EXPECTED_V4_DATASET_ID,
            "sha256": readiness.EXPECTED_V4_DATA_BUNDLE_SHA256,
            "folds_id": readiness.EXPECTED_V4_FOLDS_ID,
            "decision_times_utc": list(readiness.EXPECTED_DECISION_TIMES_V4),
            "scope": "weather_training_calibration_and_scored_development_only",
            "network_used": False,
            "protected_final_read": False,
        },
        "runtime_interfaces": {
            "runner": _record("v4/orchestrator_v4.py", "b"),
            "runner_signatures": {
                "start_v4_campaign": ["root", "readiness_path", "ticket_path"],
                "resume_v4_campaign": ["root", "readiness_path", "ticket_path"],
                "campaign_status_v4": ["root", "ticket_path"],
                "main": ["argv"],
            },
            "science_interfaces": ["OfflineCandidateEvaluatorV3"],
            "production_runnable": True,
        },
        "worker_probe": {
            "path": readiness.WORKER_PROBE_PATH.as_posix(),
            "sha256": "c" * 64,
            "evidence_sha256": "d" * 64,
            "runtime_sha256": "e" * 64,
            "network_used": False,
            "protected_final_read": False,
        },
        "runtime_spec": {
            "path": "data/models/runtime_spec.json", "sha256": "f" * 64},
        "protocol_probe": {
            "path": "runs/local_worker_probe/probe/v3-protocol-report.json",
            "sha256": "0" * 64,
        },
        "v4_worker_protocol": {
            "code": _record(readiness.V4_WORKER_CODE_PATH.as_posix(), "b"),
            "self_test": {"status": "PASS"},
            "actual_runtime_probe": {
                "manifest": _record(
                    readiness.V4_WORKER_PROBE_PATH.as_posix(), "c"),
                "probe_sha256": readiness.EXPECTED_V4_WORKER_PROBE_SHA256,
                "artifact_inventory_sha256": (
                    readiness.EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256),
            },
            "protocol": "klax-research-proposal-v4",
            "network_used": False,
            "protected_final_read": False,
        },
    }


def test_registration_preserves_v3_gates_and_twelve_hour_boundary() -> None:
    checked = readiness.validate_v4_preregistration(ROOT)
    executable = readiness._verify_execution_registration(ROOT, checked)
    assert checked["promotion_gates_sha256"] == (
        "455672358d874be5ad90b171f3baaedafa7ef3714f6cd9dbbc1c941a6741bb56")
    budget = readiness._compatibility_budget(checked["goal"])
    assert budget.maximum_wall_seconds == 43_200
    assert budget.maximum_distinct_executed_candidates == 3072
    assert budget.maximum_local_inference_concurrency == 1
    assert budget.empty_epoch_patience == 257
    assert executable["execution_factorial"]["registered_plan_count"] == 3072
    registered_slots = checked["goal"]["search_space_coverage"]["per_epoch_slots"]
    assert {key: executable["epoch_allocation"][key]
            for key in registered_slots} == registered_slots


def test_registration_rejects_any_v3_gate_change(tmp_path: Path) -> None:
    _copy_registration(tmp_path)
    path = tmp_path / readiness.CONFIG_PATH
    goal = json.loads(path.read_text(encoding="utf-8"))
    goal["development_promotion_gates"][
        "minimum_primary_capital_weighted_net_return"] = .09
    path.write_text(json.dumps(goal), encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="preregistration|promotion gate"):
        readiness.validate_v4_preregistration(tmp_path)


def test_registration_rejects_contract_byte_drift(tmp_path: Path) -> None:
    _copy_registration(tmp_path)
    contract = tmp_path / readiness.CONTRACT_PATH
    contract.write_bytes(contract.read_bytes() + b"\n")
    with pytest.raises(readiness.V4ReadinessError, match="campaign contract changed"):
        readiness.validate_v4_preregistration(tmp_path)


def test_real_stage0_training_only_parent_is_exactly_bound() -> None:
    binding = readiness._verify_stage0_parent_binding(ROOT)
    assert binding["registration"]["sha256"] == (
        readiness.EXPECTED_STAGE0_PARENT_FILE_SHA256)
    assert binding["registration_sha256"] == (
        readiness.EXPECTED_STAGE0_PARENT_REGISTRATION_SHA256)
    assert binding["stage0_funnel_code"]["sha256"] == (
        readiness.EXPECTED_STAGE0_FUNNEL_CODE_SHA256)
    assert binding["forbidden_operators"] == [
        "calibration", "market_residual", "conformal", "abstention", "fit",
        "scoring",
    ]
    assert binding["settlement_labels_read"] is False
    assert binding["profit_calculated"] is False
    assert binding["protected_final_read"] is False


@pytest.mark.parametrize(("field", "mutated"), (
    ("forbidden_operators", [
        "calibration", "market_residual", "conformal", "abstention", "fit"]),
    ("settlement_labels_read", True),
    ("profit_calculated", True),
    ("protected_final_read", True),
))
def test_stage0_parent_rejects_forbidden_operator_flag_mutation(
        tmp_path: Path, field: str, mutated: object) -> None:
    path = _copy_stage0_binding(tmp_path)
    registration = json.loads(path.read_text(encoding="utf-8"))
    registration[field] = mutated
    _rewrite_stage0_registration(path, registration)
    with pytest.raises(readiness.V4ReadinessError, match="forbidden|safety"):
        readiness._verify_stage0_parent_binding(tmp_path)


def test_stage0_parent_rejects_source_parent_mutation(tmp_path: Path) -> None:
    path = _copy_stage0_binding(tmp_path)
    registration = json.loads(path.read_text(encoding="utf-8"))
    registration["source_campaign_id"] = "v3-forged-parent"
    _rewrite_stage0_registration(path, registration)
    with pytest.raises(readiness.V4ReadinessError, match="campaign binding"):
        readiness._verify_stage0_parent_binding(tmp_path)


def test_stage0_parent_rejects_funnel_code_mutation(tmp_path: Path) -> None:
    _copy_stage0_binding(tmp_path)
    code = tmp_path / readiness.STAGE0_FUNNEL_PATH
    code.write_bytes(code.read_bytes() + b"\n")
    with pytest.raises(readiness.V4ReadinessError, match="funnel code changed"):
        readiness._verify_stage0_parent_binding(tmp_path)


def test_real_v4_decision_time_amendment_and_dataset_are_exactly_bound() -> None:
    amendment = readiness._verify_decision_time_amendment(ROOT)
    assert amendment["registration"]["sha256"] == (
        readiness.EXPECTED_DECISION_AMENDMENT_FILE_SHA256)
    assert amendment["registration_sha256"] == (
        readiness.EXPECTED_DECISION_AMENDMENT_REGISTRATION_SHA256)
    assert amendment["decision_times_utc"] == ["13:30", "15:00", "18:00"]
    assert amendment["settlement_labels_read"] is False
    bundle = readiness._verify_v4_data_bundle(ROOT)
    assert bundle["version"] == readiness.EXPECTED_V4_DATASET_ID
    assert bundle["sha256"] == readiness.EXPECTED_V4_DATA_BUNDLE_SHA256
    assert bundle["folds_id"] == readiness.EXPECTED_V4_FOLDS_ID
    assert bundle["decision_times_utc"] == ["13:30", "15:00", "18:00"]
    assert bundle["protected_final_read"] is False


@pytest.mark.parametrize(("field", "mutated"), (
    ("prospective_decision_times_utc", ["12:00", "15:00", "18:00"]),
    ("alternate_minute_fallback_permitted", True),
    ("later_retiming_permitted", True),
    ("settlement_labels_read", True),
    ("profit_calculated", True),
    ("protected_final_read", True),
))
def test_decision_time_amendment_rejects_mutation(
        tmp_path: Path, field: str, mutated: object) -> None:
    path = _copy_decision_time_amendment(tmp_path)
    registration = json.loads(path.read_text(encoding="utf-8"))
    registration[field] = mutated
    body = {key: value for key, value in registration.items()
            if key != "registration_sha256"}
    registration["registration_sha256"] = readiness.canonical_hash(body)
    path.write_text(json.dumps(registration, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="identity|safety"):
        readiness._verify_decision_time_amendment(tmp_path)


def test_issue_load_mutation_and_one_use_are_fail_closed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _copy_registration(tmp_path)
    current = {"value": _bindings()}
    monkeypatch.setattr(
        readiness, "_collect_bindings",
        lambda _root, _validated: deepcopy(current["value"]))
    campaign_id = "v4-fixture-20260926T200000Z"
    ready, ticket = readiness.issue_v4_campaign_readiness(
        tmp_path, campaign_id,
        now=lambda: datetime(2026, 9, 26, 20, tzinfo=timezone.utc))
    ready_path = tmp_path / readiness.READINESS_PATH
    ticket_path = tmp_path / readiness.TICKET_PATH
    assert ready["protected_final_evaluations_remaining"] == 0
    assert ready["actual_orders_authorized"] is False
    assert ticket["maximum_wall_seconds"] == 43_200
    assert ticket["v4_worker_probe_sha256"] == (
        readiness.EXPECTED_V4_WORKER_PROBE_SHA256)
    assert ticket["v4_worker_probe_artifacts_sha256"] == (
        readiness.EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256)

    authorization = readiness.load_v4_campaign_authorization(
        tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)
    assert authorization.campaign_id == campaign_id
    assert authorization.ticket_path == ticket_path
    assert authorization.budget.maximum_wall_seconds == 43_200
    assert authorization.config_sha256 == current["value"]["v3_goal"]["sha256"]
    assert authorization.data_bundle_version == readiness.EXPECTED_V4_DATASET_ID
    assert authorization.data_bundle_sha256 == (
        readiness.EXPECTED_V4_DATA_BUNDLE_SHA256)

    stale_v1_readiness = deepcopy(ready)
    stale_v1_readiness["readiness_version"] = "klax-v4-readiness-v1"
    ready_path.write_bytes((
        json.dumps(stale_v1_readiness, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8"))
    with pytest.raises(readiness.V4ReadinessError, match="offline V4 development"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)
    ready_path.write_bytes((
        json.dumps(ready, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8"))

    stale_v3_ticket = deepcopy(ticket)
    stale_v3_ticket["data_bundle_version"] = readiness.EXPECTED_V3_DATASET_ID
    stale_v3_ticket["data_bundle_sha256"] = (
        readiness.EXPECTED_V3_DATA_BUNDLE_SHA256)
    ticket_path.write_text(
        json.dumps(stale_v3_ticket, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="ticket binding"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)

    stale_v1_ticket = deepcopy(ticket)
    stale_v1_ticket["ticket_version"] = "klax-v4-offline-campaign-ticket-v1"
    ticket_path.write_text(
        json.dumps(stale_v1_ticket, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="ticket binding"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)

    mutated = json.loads(ticket_path.read_text(encoding="utf-8"))
    mutated = deepcopy(ticket)
    mutated["actual_orders_authorized"] = True
    ticket_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="ticket binding"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)

    ticket_path.write_text(json.dumps(ticket, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    current["value"]["v4_code_sha256"] = "0" * 64
    with pytest.raises(readiness.V4ReadinessError, match="artifact changed"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)

    current["value"] = _bindings()
    with pytest.raises(readiness.V4ReadinessError, match="resume requires"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH,
            allow_existing_claim=True)
    claim_path = ticket_path.with_name(ticket_path.name + ".claimed.json")
    claim_path.write_text("{}", encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="already been claimed"):
        readiness.load_v4_campaign_authorization(
            tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH)
    claim_path.write_text(json.dumps({
        "claim_version": "klax-v3-ticket-claim-v1",
        "campaign_id": campaign_id,
        "ticket_sha256": readiness.sha256_file(ticket_path),
        "readiness_sha256": readiness.sha256_file(ready_path),
        "synthetic": False,
    }), encoding="utf-8")
    resumed = readiness.load_v4_campaign_authorization(
        tmp_path, readiness.READINESS_PATH, readiness.TICKET_PATH,
        allow_existing_claim=True)
    assert resumed.campaign_id == campaign_id

    with pytest.raises(readiness.V4ReadinessError, match="already issued or claimed"):
        readiness.issue_v4_campaign_readiness(tmp_path, campaign_id)


def test_only_controller_bound_paths_are_accepted(tmp_path: Path) -> None:
    with pytest.raises(readiness.V4ReadinessError, match="controller-bound"):
        readiness._authorization_path(
            tmp_path, "v4-fixture", "ticket", "runs/v3_offline_campaign_ticket.json")


def test_runtime_interfaces_refuse_missing_production_runner(tmp_path: Path) -> None:
    (tmp_path / "v4").mkdir()
    with pytest.raises(readiness.V4ReadinessError, match="not implemented"):
        readiness._verify_runtime_interfaces(tmp_path)


def test_real_production_interfaces_and_negative_self_tests_pass() -> None:
    result = readiness._verify_runtime_interfaces(ROOT)
    assert result["production_runnable"] is True
    assert result["readiness_self_test"]["status"] == "PASS"
    assert result["readiness_self_test"]["checks"]["forged_flags_rejected"] is True
    assert result["readiness_self_test"]["checks"]["stale_quote_rejected"] is True
    assert result["readiness_self_test"]["checks"][
        "v3_assumed_fill_limitation_detected"] is True
    assert result["production_integration_self_test"]["status"] == "PASS"
    assert all(result["production_integration_self_test"]["checks"].values())
    assert result["production_integration_self_test"]["checks"][
        "candidate_verifier_failure_blocks_promotion"] is True
    assert result["production_integration_self_test"]["checks"][
        "scheduler_verifier_failure_forces_insufficient_evidence"] is True
    assert result["production_integration_self_test"]["checks"][
        "report_markdown_matches_summary_conclusion"] is True
    assert {
        "frozen_universe_v4", "audit_v3_raw_ledger_limitations",
        "verify_candidate_v4", "verify_scheduler_state_v4",
        "run_v4_readiness_self_test",
    } <= set(result["verifier_signatures"])


def test_v4_worker_protocol_self_test_is_hash_bound_and_passes() -> None:
    result = readiness._verify_worker(ROOT)["v4_worker_protocol"]
    assert result["code"]["path"] == readiness.V4_WORKER_CODE_PATH.as_posix()
    assert result["protocol"] == "klax-research-proposal-v4"
    assert result["self_test"]["status"] == "PASS"
    assert result["self_test"]["checks"] == {
        "research_plan_v4_preserved": True,
        "exact_1330_admitted": True,
        "nomination_seed_bound": True,
        "synthesis_content_typed": True,
        "protected_final_denied": True,
    }
    assert result["network_used"] is False
    assert result["protected_final_read"] is False
    actual = result["actual_runtime_probe"]
    assert actual["manifest"]["sha256"] == (
        readiness.EXPECTED_V4_WORKER_PROBE_FILE_SHA256)
    assert actual["probe_sha256"] == readiness.EXPECTED_V4_WORKER_PROBE_SHA256
    assert actual["artifact_count"] == 14
    assert actual["artifact_inventory_sha256"] == (
        readiness.EXPECTED_V4_WORKER_PROBE_ARTIFACTS_SHA256)
    assert actual["actual_nomination_probe_passed"] is True
    assert actual["actual_synthesis_probe_passed"] is True
    assert actual["tool_catalog"] == []
    assert actual["experiment_executed"] is False
    assert actual["profit_claimed"] is False


def test_v4_worker_protocol_self_test_rejects_forged_pass(
        monkeypatch: pytest.MonkeyPatch) -> None:
    original = local_worker_v4.run_v4_worker_protocol_self_test(ROOT)
    forged = deepcopy(original)
    forged["checks"]["exact_1330_admitted"] = False
    body = {key: value for key, value in forged.items()
            if key != "self_test_sha256"}
    forged["self_test_sha256"] = readiness.canonical_hash(body)
    def forged_self_test(root):
        del root
        return deepcopy(forged)

    monkeypatch.setattr(
        local_worker_v4, "run_v4_worker_protocol_self_test", forged_self_test)
    with pytest.raises(readiness.V4ReadinessError, match="fail-closed fixtures"):
        readiness._verify_worker(ROOT)


def test_actual_v4_worker_probe_artifact_mutation_is_rejected(
        tmp_path: Path) -> None:
    source = ROOT / readiness.V4_WORKER_PROBE_ARTIFACT_ROOT
    destination = tmp_path / readiness.V4_WORKER_PROBE_ARTIFACT_ROOT
    shutil.copytree(source, destination)
    packet = destination / "v4-probe-nomination/attempt-1/packet.json"
    packet.write_bytes(packet.read_bytes() + b"\n")
    with pytest.raises(readiness.V4ReadinessError, match="inventory changed"):
        readiness._v4_worker_probe_inventory(tmp_path)


def test_actual_v4_worker_probe_manifest_mutation_is_rejected(
        tmp_path: Path) -> None:
    destination = tmp_path / readiness.V4_WORKER_PROBE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / readiness.V4_WORKER_PROBE_PATH, destination)
    value = json.loads(destination.read_text(encoding="utf-8"))
    value["actual_synthesis_probe_passed"] = False
    destination.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(readiness.V4ReadinessError, match="manifest changed"):
        readiness._verify_actual_v4_worker_probe(tmp_path, {})
