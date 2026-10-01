"""Independent adversarial checks for the V4.1 incident continuation.

These tests bind the repair to the archived failure that triggered it.  Model
format repair may preserve an unambiguous singleton nonproposal, but it may
never manufacture a proposal, select a plan, refund resources, or rewrite the
source campaign.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import v4.orchestrator_v4_1 as orchestrator_v4_1
from klax_lab.campaign_v3 import (
    V3BudgetStop, V3CampaignAuthorization, V3CampaignBudget, V3IntegrityStop,
)
from klax_lab.provenance import canonical_hash, sha256_file
from v4.continuation_v4_1 import (
    ABSOLUTE_DEADLINE_AT_UTC,
    SOURCE_CLAIM_PATH,
    SOURCE_READINESS_PATH,
    SOURCE_STDERR_PATH,
    SOURCE_TICKET_PATH,
    V4_1ContinuationError,
    audit_source_campaign_v4_1,
    build_continuation_import_record_v4_1,
    reconcile_failed_nomination_v4_1,
)
from v4.local_worker_v4 import V4WorkerProtocolError, parse_v4_worker_response
from v4.local_worker_v4_1 import (
    NORMALIZATION_RULE,
    V4_1CandidateResponseProtocolError,
    extract_archived_completion_body_v4_1,
    normalize_singleton_nonproposal_v4_1,
    parse_v4_1_worker_response,
)
from v4.research_plan_v4 import ResearchPlanV4
from v4.orchestrator_v4_1 import (
    ProductionV4_1Orchestrator, V4_1CampaignEngine, campaign_status_v4_1,
    _hydrate_engine_from_source, _reconciled_source_state,
    verify_scheduler_state_v4_1,
)
from v4.orchestrator_v4 import ProductionV4Orchestrator
from v4.verifier_v4 import verify_scheduler_state_v4


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "v4-offline-20260926T212122609Z"
SOURCE = ROOT / "runs/campaigns_v4" / SOURCE_ID
FAILED_TASK = "v4-e007-s11"
FAILED_PLAN_SHA256 = (
    "568bf0bb0164850f246724f70f4df899848435dff2ebfa782ce114df5733b744")
SOURCE_RECOVERY_FILE_SHA256 = (
    "d631a281a9004c1ded5fb3ac7b504a16224307492ea276d0dcc516efd41f89d0")
SOURCE_RECOVERY_STATE_SHA256 = (
    "b4e1576fbb9ba5822182f877239e8657636c3f49e07f8e4b8005185ee2fa987b")
PACKET_SHA256 = (
    "cf9c667dd71c0393ed54570305153ee429afb0b1ec6ff1a6f0db7248499ce737")
STDOUT_SHA256 = (
    "d3ecbc80e9887626fb366e25c6f94daae7173e0921ae6f80d1e129e08e44a9b5")
PROCESS_SHA256S = (
    "02efaea5ce4885aa8a3552c21cf42f14e745cd75a791920c085d83d566d721a4",
    "cd0f7e3eac26991315f5e0f125cbc69383131247a18f4b40438edad7c9001eb4",
    "10378c14c0df8261f7e2b3835ca978025e7a8f4fe7b5ad3398e6728be81ddf6b",
)


def _attempt(index: int) -> Path:
    return SOURCE / "local-inference-v4" / FAILED_TASK / f"attempt-{index}"


def _packet() -> dict:
    return json.loads((_attempt(1) / "packet.json").read_text(encoding="utf-8"))


def _raw_object(index: int = 1) -> tuple[bytes, dict]:
    body = extract_archived_completion_body_v4_1(
        (_attempt(index) / "stdout.bin").read_bytes())
    return body, json.loads(body)


def _source_recovery() -> dict:
    return json.loads((SOURCE / "recovery-state.json").read_text(encoding="utf-8"))


def _authorization(output: Path) -> V3CampaignAuthorization:
    bundle = json.loads((
        ROOT / "data/manifests/v4_data_bundle_1330_1500_1800.json"
    ).read_text(encoding="utf-8"))
    partitions = json.loads((ROOT / "configs/v3_goal.json").read_text(
        encoding="utf-8"))["partitions"]
    budget = V3CampaignBudget(
        maximum_epochs=256,
        maximum_distinct_executed_candidates=3_072,
        maximum_new_candidates_per_epoch=12,
        maximum_local_model_calls=3_300,
        local_reserved_context_tokens=54_067_200,
        maximum_local_inference_concurrency=1,
        maximum_wall_seconds=43_200,
        maximum_transient_retries_per_task=2,
        empty_epoch_patience=2,
        maximum_paid_api_dollars=0,
    )
    return V3CampaignAuthorization(
        root=ROOT, campaign_id=output.name,
        readiness_path=output / "readiness.json",
        ticket_path=output / "ticket.json",
        readiness_sha256="1" * 64, ticket_sha256="2" * 64,
        config_sha256="3" * 64, schema_sha256="4" * 64,
        data_bundle_version=bundle["dataset_id"],
        data_bundle_sha256=bundle["bundle_sha256"],
        code_sha256="5" * 64, evaluation_policy_sha256="6" * 64,
        promotion_gates_sha256="7" * 64,
        campaign_budget_sha256="8" * 64,
        partition_contract_sha256=canonical_hash(partitions),
        partition_contract=partitions,
        champion_ranking_rule="registered_test_order",
        champion_ranking_rule_sha256="9" * 64,
        budget=budget,
        protected_final_roots=(ROOT / "data/protected_final",),
        synthetic=False,
    )


def _imported_runner(output: Path, *, claim_ticket: bool = True):
    authorization = _authorization(output)
    fixed_now = datetime(2026, 9, 26, 23, 45, tzinfo=timezone.utc)
    engine = V4_1CampaignEngine(
        authorization,
        absolute_deadline_at_utc="2026-09-27T09:23:25.261434+00:00",
        utc_clock=lambda: fixed_now,
        claim_ticket=claim_ticket,
    )
    source, record = _reconciled_source_state(ROOT)
    _hydrate_engine_from_source(engine, source)
    runner = ProductionV4_1Orchestrator(
        engine, object(), object(), output,
        worker_factory=lambda *_args: None,
        phase=source["phase"], queue=deepcopy(source["queue"]),
        next_queue_index=source["next_queue_index"],
        coverage=deepcopy(source["coverage"]),
        started_at_utc="2026-09-26T21:23:25.261434+00:00",
        completed_at_utc=None, continuation_record=record,
        source_output_root=SOURCE,
        absolute_deadline_at_utc="2026-09-27T09:23:25.261434+00:00",
    )
    return authorization, engine, runner, fixed_now


def _finish_review_without_data(runner, item) -> None:
    record = runner.engine.candidates[item["candidate_id"]]
    if record.evaluation is None:
        record.evaluation = {"redteam_fixture": True}
        record.artifact_sha256s = {"redteam_fixture": "a" * 64}
        record.promotion = {"passed": False}
        runner.engine.executed_candidates += 1
    item["status"] = "REVIEWED"


def _scheduler_state(runner) -> dict:
    history = runner.coverage["allocation_history"]
    completed = [row for row in history if row.get("completed") is True]
    active = next(row for row in reversed(history)
                  if row.get("completed") is not True)
    journal = runner.coverage["call_journal"]
    registration = json.loads((ROOT / "v4/config/execution_coverage.json").read_text(
        encoding="utf-8"))
    state = {
        "state_version": "klax-v4-orchestrator-state-v1",
        "campaign_id": runner.engine.authorization.campaign_id,
        "status": "COMPLETE",
        "current_epoch": runner.engine.current_epoch,
        "allocation_history": history,
        "active_queue": active["allocations"],
        "current_digest": active["digest"],
        "last_completed_digest_sha256": completed[-1]["digest"]["digest_sha256"],
        "completed_plan_sha256s": sorted(
            item["plan_sha256"] for epoch in history if epoch["completed"]
            for item in epoch["allocations"]),
        "call_journal": journal,
        "candidate_calls_used": sum(
            row["call_type"] == "candidate" for row in journal),
        "synthesis_calls_used": sum(
            row["call_type"] == "synthesis" for row in journal),
        "transient_retry_calls_used": sum(
            row["call_type"] == "retry" for row in journal),
        "model_calls_reserved": runner.engine.model_calls,
        "model_context_tokens_reserved": (
            runner.engine.model_context_tokens_reserved),
        "model_nomination_failures_v4_1": deepcopy(
            runner.coverage.get("v4_1_model_nomination_failures", [])),
        "resume_history": runner.coverage["resume_history"],
        "budget": registration["budget"],
        "started_at_utc": "2026-09-26T21:23:25.261434+00:00",
        "deadline_utc": "2026-09-27T09:23:25.261434+00:00",
        "completed_at_utc": "2026-09-26T23:45:00+00:00",
        "stopped_reason": "wall_time_budget_exhausted",
        "registered_plan_count": runner.coverage["manifest"][
            "registered_plan_count"],
        "manifest": runner.coverage["manifest"],
        "protected_final_read": False,
        "protected_final_authorized": False,
        "orders_authorized": False,
        "actual_profit_claim": False,
    }
    state["state_sha256"] = canonical_hash(state)
    return state


def _v4_1_scheduler_state(runner) -> dict:
    state = _scheduler_state(runner)
    state["continuation_v4_1"] = {
        "continuation_version": "4.1",
        "source_campaign_id": SOURCE_ID,
        "source_recovery_state_sha256": runner.continuation_record[
            "source_recovery_state_sha256"],
        "continuation_import_sha256": canonical_hash(
            runner.continuation_record),
        "candidate_imports_sha256": runner.continuation_record[
            "candidate_imports_sha256"],
        "incident_reconciliation": runner.coverage[
            "v4_1_incident_reconciliation"],
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "consumed_candidates_floor": 82,
        "consumed_model_calls_floor": 86,
        "consumed_context_tokens_floor": 1_409_024,
        "consumed_wall_seconds_floor": 1_669,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    state.pop("state_sha256", None)
    state["state_sha256"] = canonical_hash(state)
    return state


class _InvalidResponseWorker:
    """Write production-shaped artifacts, then raise a typed parse error."""

    def __init__(self, worker_id: str, artifact_root: Path) -> None:
        self.worker_id = worker_id
        self.artifact_root = artifact_root
        self.calls = 0

    def respond(self, packet: dict) -> dict:
        self.calls += 1
        attempt_number = 1
        while (self.artifact_root / packet["task_id"]
               / f"attempt-{attempt_number}").exists():
            attempt_number += 1
        attempt = (self.artifact_root / packet["task_id"]
                   / f"attempt-{attempt_number}")
        attempt.mkdir(parents=True)
        (attempt / "packet.json").write_text(
            json.dumps(packet, sort_keys=True), encoding="utf-8")
        (attempt / "stdout.bin").write_bytes(
            f"malformed-{self.calls}".encode("utf-8"))
        process = {
            "exit_code": 0,
            "timed_out": False,
            "output_limit_exceeded": False,
            "cleanup_failed": False,
            "reader_errors": [],
        }
        (attempt / "process.json").write_text(
            json.dumps(process, sort_keys=True), encoding="utf-8")
        audit = {
            "error_version": "klax-v4.1-candidate-response-error-v1",
            "task_id": packet["task_id"],
            "packet_sha256": canonical_hash(packet),
            "raw_stdout_sha256": sha256_file(attempt / "stdout.bin"),
            "attempt_directory": attempt.relative_to(ROOT).as_posix(),
            "completion_body_sha256": None,
            "process_sha256": sha256_file(attempt / "process.json"),
            "error_class": "V4WorkerProtocolError",
            "error_message": "synthetic malformed response",
            "model_response_accepted": False,
            "scientific_evidence_created": False,
            "protected_final_read": False,
        }
        (attempt / "response-error.json").write_text(
            json.dumps(audit, sort_keys=True), encoding="utf-8")
        raise V4_1CandidateResponseProtocolError(
            "synthetic malformed response", audit)


def _write_proposal_artifact(
    output: Path, packet: dict, attempt_number: int, response: dict,
) -> None:
    attempt = (output / "local-inference-v4-1" / packet["task_id"]
               / f"attempt-{attempt_number}")
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "proposal.json").write_text(
        json.dumps({"response": response}, sort_keys=True), encoding="utf-8")


@pytest.fixture(scope="module")
def continuation_record() -> dict:
    return build_continuation_import_record_v4_1(ROOT)


def test_archived_three_attempts_are_exact_and_reproduce_the_parser_incident() -> None:
    recovery = _source_recovery()
    assert sha256_file(SOURCE / "recovery-state.json") == SOURCE_RECOVERY_FILE_SHA256
    assert recovery["state_sha256"] == SOURCE_RECOVERY_STATE_SHA256
    assert recovery["state_sha256"] == canonical_hash({
        key: value for key, value in recovery.items() if key != "state_sha256"})

    packet = _packet()
    assert sha256_file(_attempt(1) / "packet.json") == PACKET_SHA256
    assert ResearchPlanV4.from_dict(packet["seed_plans"][0]).identity == (
        FAILED_PLAN_SHA256)
    for index, process_sha256 in enumerate(PROCESS_SHA256S, 1):
        attempt = _attempt(index)
        assert sha256_file(attempt / "packet.json") == PACKET_SHA256
        assert sha256_file(attempt / "stdout.bin") == STDOUT_SHA256
        assert sha256_file(attempt / "process.json") == process_sha256
        process = json.loads((attempt / "process.json").read_text(encoding="utf-8"))
        assert process == {
            "cleanup_failed": False,
            "elapsed_seconds": process["elapsed_seconds"],
            "exit_code": 0,
            "output_limit_exceeded": False,
            "reader_errors": [],
            "timed_out": False,
        }
        body, raw = _raw_object(index)
        assert raw["action"] == "abstain"
        assert raw["seed_index"] == 0
        with pytest.raises(V4WorkerProtocolError, match="Only V4 propose"):
            parse_v4_worker_response(body, packet)
        repaired = parse_v4_1_worker_response(body, packet)
        assert repaired["action"] == "abstain"
        assert repaired["seed_index"] is None
        assert repaired["task_id"] == FAILED_TASK


@pytest.mark.parametrize("action", ["reject", "abstain"])
def test_singleton_nonproposal_repair_preserves_action_and_never_proposes(
        action: str) -> None:
    packet = _packet()
    _, raw = _raw_object()
    raw["action"] = action
    normalized, audit = normalize_singleton_nonproposal_v4_1(raw, packet)
    assert normalized == {**raw, "seed_index": None}
    assert audit is not None
    assert audit["rule"] == NORMALIZATION_RULE
    assert audit["semantic_action_preserved"] == action
    assert audit["proposal_created"] is False
    assert audit["singleton_plan_sha256"] == FAILED_PLAN_SHA256
    assert audit["raw_response_sha256"] == canonical_hash(raw)
    assert audit["normalized_response_sha256"] == canonical_hash(normalized)
    assert audit["normalization_sha256"] == canonical_hash({
        key: value for key, value in audit.items()
        if key != "normalization_sha256"})
    assert parse_v4_1_worker_response(
        json.dumps(raw), packet)["action"] == action


def test_valid_proposal_is_not_rewritten_and_other_malformed_cases_fail_closed() -> None:
    packet = _packet()
    _, raw = _raw_object()
    proposal = {**raw, "action": "propose"}
    unchanged, audit = normalize_singleton_nonproposal_v4_1(proposal, packet)
    assert unchanged == proposal
    assert audit is None
    assert parse_v4_1_worker_response(
        json.dumps(proposal), packet)["action"] == "propose"

    wrong_task = {**raw, "task_id": "v4-e007-forged"}
    with pytest.raises(V4WorkerProtocolError, match="binding differs"):
        parse_v4_1_worker_response(json.dumps(wrong_task), packet)

    wrong_protocol = {**raw, "protocol": "forged-protocol"}
    with pytest.raises(V4WorkerProtocolError, match="binding differs"):
        parse_v4_1_worker_response(json.dumps(wrong_protocol), packet)

    recovery = _source_recovery()
    second_plan = recovery["queue"][11]["plan"]
    multi_packet = deepcopy(packet)
    multi_packet["seed_plans"].append(second_plan)
    assert len({ResearchPlanV4.from_dict(row).identity
                for row in multi_packet["seed_plans"]}) == 2
    normalized, audit = normalize_singleton_nonproposal_v4_1(raw, multi_packet)
    assert normalized == raw
    assert audit is None
    with pytest.raises(V4WorkerProtocolError, match="Only V4 propose"):
        parse_v4_1_worker_response(json.dumps(raw), multi_packet)

    synthesis_packet = deepcopy(packet)
    synthesis_packet["mode"] = "cross_pollination_synthesis"
    synthesis_packet["seed_plans"] = []
    with pytest.raises(V4WorkerProtocolError, match="action differs"):
        parse_v4_1_worker_response(json.dumps(raw), synthesis_packet)


def test_import_is_deterministic_read_only_and_preserves_every_source_byte(
        continuation_record: dict) -> None:
    before = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted(SOURCE.rglob("*")) if path.is_file()
    }
    outside_before = {
        path.as_posix(): sha256_file(ROOT / path)
        for path in (SOURCE_READINESS_PATH, SOURCE_TICKET_PATH,
                     SOURCE_CLAIM_PATH, SOURCE_STDERR_PATH)
    }
    audit = audit_source_campaign_v4_1(ROOT)
    rebuilt = build_continuation_import_record_v4_1(ROOT)
    reconciliation = reconcile_failed_nomination_v4_1(ROOT, rebuilt)
    repeated = reconcile_failed_nomination_v4_1(ROOT, rebuilt)
    after = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted(SOURCE.rglob("*")) if path.is_file()
    }
    outside_after = {
        path.as_posix(): sha256_file(ROOT / path)
        for path in (SOURCE_READINESS_PATH, SOURCE_TICKET_PATH,
                     SOURCE_CLAIM_PATH, SOURCE_STDERR_PATH)
    }

    assert continuation_record == rebuilt
    assert reconciliation == repeated
    assert audit["status"] == "PASS"
    assert audit["source_artifact_count"] == len(before) == 1751
    assert audit["source_artifact_inventory_sha256"] == canonical_hash(
        continuation_record["source_artifact_inventory"])
    assert before == after
    assert outside_before == outside_after
    assert reconciliation["status"] == "HOST_EXACT_PLAN_FALLBACK"
    assert reconciliation["plan_sha256"] == FAILED_PLAN_SHA256
    assert reconciliation["model_calls_before"] == 86
    assert reconciliation["model_calls_charged"] == 0
    assert reconciliation["model_calls_after"] == 86
    assert reconciliation["model_nomination_succeeded"] is False
    assert reconciliation["model_response_fabricated"] is False
    assert reconciliation["scientific_evidence_created"] is False


def test_import_preserves_original_deadline_and_all_cumulative_budget_floors(
        continuation_record: dict) -> None:
    budget = continuation_record["cumulative_budget"]
    assert continuation_record["original_started_at_utc"] == (
        "2026-09-26T21:23:25.261434+00:00")
    assert ABSOLUTE_DEADLINE_AT_UTC == "2026-09-27T09:23:25.261434+00:00"
    assert continuation_record["absolute_deadline_at_utc"] == (
        ABSOLUTE_DEADLINE_AT_UTC)
    assert budget == {
        "completed_epochs": 6,
        "consumed_candidates": 82,
        "consumed_context_tokens": 1_409_024,
        "consumed_model_calls": 86,
        "consumed_retry_calls": 2,
        "consumed_wall_seconds_floor": 1_669,
        "current_epoch": 7,
        "maximum_candidates": 3_072,
        "maximum_context_tokens": 54_067_200,
        "maximum_epochs": 256,
        "maximum_model_calls": 3_300,
        "maximum_wall_seconds": 43_200,
        "remaining_candidates": 2_990,
        "remaining_context_tokens": 52_658_176,
        "remaining_model_calls": 3_214,
        "remaining_nomination_call_reservation": 2_989,
        "remaining_retry_call_reservation": 162,
        "remaining_synthesis_call_reservation": 63,
    }
    assert continuation_record["continuation_position"] == {
        "epoch": 7,
        "failed_task_fallback_completed": True,
        "imported_candidate_count": 82,
        "next_queue_index": 11,
        "next_task_id": "v4-e007-s12",
        "phase": "EXECUTING_EPOCH",
    }


def test_fallback_is_exact_registered_plan_and_not_scientific_evidence(
        continuation_record: dict) -> None:
    incident = continuation_record["incident"]
    fallback = incident["fallback"]
    assert incident["status"] == "MODEL_NOMINATION_FAILED"
    assert incident["task_id"] == FAILED_TASK
    assert incident["plan_sha256"] == FAILED_PLAN_SHA256
    assert incident["attempts_charged"] == 3
    assert incident["model_call_numbers"] == [84, 85, 86]
    assert incident["model_nomination_succeeded"] is False
    assert incident["scientific_evidence_created"] is False
    assert fallback == {
        "authorization_basis": (
            "exact preallocated singleton after primary plus two charged "
            "schema-invalid nonproposal retries"),
        "authorized_plan_sha256": FAILED_PLAN_SHA256,
        "candidate_result_claimed": False,
        "disposition": "HOST_EXACT_PLAN_FALLBACK",
        "model_calls_charged": 0,
        "model_response_fabricated": False,
    }
    imported_ids = {
        row["research_plan_sha256"] for row in continuation_record[
            "candidate_imports"]}
    assert len(imported_ids) == 82
    assert FAILED_PLAN_SHA256 not in imported_ids
    assert {row["independent_verifier_status"] for row in continuation_record[
        "candidate_imports"]} == {"FAIL"}
    assert {row["independent_promotion_eligible"] for row in continuation_record[
        "candidate_imports"]} == {False}
    assert continuation_record["safety"] == {
        "actual_orders_placed": False,
        "fallback_creates_model_response": False,
        "fixed_universe_changed": False,
        "model_nomination_failure_is_scientific_evidence": False,
        "network_permitted": False,
        "promotion_gates_changed": False,
        "protected_final_read": False,
        "source_campaign_mutation_permitted": False,
    }


def test_tampered_import_or_fallback_identity_is_rejected(
        continuation_record: dict) -> None:
    tampered = deepcopy(continuation_record)
    tampered["incident"]["fallback"]["authorized_plan_sha256"] = "f" * 64
    with pytest.raises(V4_1ContinuationError, match="record differs"):
        reconcile_failed_nomination_v4_1(ROOT, tampered)

    tampered = deepcopy(continuation_record)
    tampered["cumulative_budget"]["consumed_model_calls"] = 85
    with pytest.raises(V4_1ContinuationError, match="record differs"):
        reconcile_failed_nomination_v4_1(ROOT, tampered)

    tampered = deepcopy(continuation_record)
    tampered["absolute_deadline_at_utc"] = "2026-09-27T09:23:26.261434+00:00"
    with pytest.raises(V4_1ContinuationError, match="record differs"):
        reconcile_failed_nomination_v4_1(ROOT, tampered)


def test_production_fallback_consumes_exact_plan_without_an_inference_call(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, engine, runner, _now = _imported_runner(output)
        source_before = sha256_file(SOURCE / "recovery-state.json")
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))

        result = runner.apply_incident_fallback_v4_1()
        candidate_id = "v4-candidate-" + FAILED_PLAN_SHA256[:20]
        assert result["status"] == "HOST_EXACT_PLAN_FALLBACK"
        assert result["plan_sha256"] == FAILED_PLAN_SHA256
        assert result["model_calls_charged"] == 0
        assert engine.model_calls == 86
        assert engine.model_context_tokens_reserved == 1_409_024
        assert engine.admitted_candidates == engine.executed_candidates == 83
        assert engine.candidates[candidate_id].plan.identity == FAILED_PLAN_SHA256
        assert runner.queue[10]["candidate_id"] == candidate_id
        assert runner.queue[10]["status"] == "REVIEWED"
        assert runner.next_queue_index == 11
        assert (output / "incident-reconciliation.json").is_file()
        assert sha256_file(SOURCE / "recovery-state.json") == source_before

        # Replaying the transition observes the durable result.  It cannot
        # allocate another candidate or charge another inference.
        repeated = runner.apply_incident_fallback_v4_1()
        assert repeated == result
        assert engine.model_calls == 86
        assert engine.admitted_candidates == engine.executed_candidates == 83
        assert len(engine.candidates) == 83


def test_fallback_recovery_is_idempotent_after_crash_between_admit_and_review(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization, engine, runner, fixed_now = _imported_runner(output)

        def simulated_crash(_item) -> None:
            raise RuntimeError("simulated crash after durable admission")

        monkeypatch.setattr(runner, "_execute_and_review", simulated_crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            runner.apply_incident_fallback_v4_1()

        saved = json.loads((output / "recovery-state.json").read_text(
            encoding="utf-8"))
        assert saved["next_queue_index"] == 10
        assert saved["queue"][10]["status"] == "ADMITTED"
        assert saved["engine"]["model_calls"] == 86
        assert saved["engine"]["admitted_candidates"] == 83
        assert saved["engine"]["executed_candidates"] == 82

        recovered_engine = V4_1CampaignEngine(
            authorization,
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
            utc_clock=lambda: fixed_now,
            claim_ticket=False,
        )
        recovered = ProductionV4_1Orchestrator.recover_v4_1(
            engine=recovered_engine, primary_evaluator=object(),
            replication_evaluator=object(), output_root=output,
            continuation_record=runner.continuation_record,
            worker_factory=lambda *_args: None)
        monkeypatch.setattr(
            recovered, "_execute_and_review",
            lambda item: _finish_review_without_data(recovered, item))
        result = recovered.apply_incident_fallback_v4_1()
        assert result["status"] == "HOST_EXACT_PLAN_FALLBACK"
        assert recovered.next_queue_index == 11
        assert recovered.queue[10]["status"] == "REVIEWED"
        assert recovered_engine.model_calls == 86
        assert recovered_engine.admitted_candidates == 83
        assert recovered_engine.executed_candidates == 83

        # A second recovery transition is also an observation, not work.
        recovered.apply_incident_fallback_v4_1()
        assert recovered_engine.model_calls == 86
        assert len(recovered_engine.candidates) == 83


def test_original_deadline_blocks_all_new_campaign_work() -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization = _authorization(output)
        deadline = datetime.fromisoformat(ABSOLUTE_DEADLINE_AT_UTC)
        engine = V4_1CampaignEngine(
            authorization,
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
            utc_clock=lambda: deadline,
        )
        with pytest.raises(V3BudgetStop, match="absolute deadline"):
            engine.begin_epoch()
        assert engine.stopped_reason == "wall_time_budget_exhausted"


def test_v4_1_scheduler_verifier_binds_import_fallback_and_budget_lineage(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, _engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        state = _v4_1_scheduler_state(runner)
        report = verify_scheduler_state_v4_1(
            ROOT, state, runner.continuation_record)
        assert report["status"] == "PASS", report

        def rejected(mutator) -> dict:
            forged = deepcopy(state)
            mutator(forged)
            forged.pop("state_sha256", None)
            forged["state_sha256"] = canonical_hash(forged)
            return verify_scheduler_state_v4_1(
                ROOT, forged, runner.continuation_record)

        assert rejected(lambda value: value["continuation_v4_1"][
            "incident_reconciliation"].update({"plan_sha256": "f" * 64}))[
                "status"] == "FAIL"
        assert rejected(lambda value: value.update({
            "deadline_utc": (datetime.fromisoformat(ABSOLUTE_DEADLINE_AT_UTC)
                              + timedelta(seconds=1)).isoformat()}))[
                "status"] == "FAIL"
        assert rejected(lambda value: value.update({
            "candidate_calls_used": value["candidate_calls_used"] - 1}))[
                "status"] == "FAIL"
        assert rejected(lambda value: value["call_journal"][-1].update({
            "completed_at_utc": None}))[
                "status"] == "FAIL"
        assert rejected(lambda value: value["continuation_v4_1"].update({
            "continuation_import_sha256": "0" * 64}))[
                "status"] == "FAIL"
        assert rejected(lambda value: value.update({
            "model_context_tokens_reserved": 0}))["status"] == "FAIL"
        assert rejected(lambda value: value.update({
            "campaign_id": "../../forged-cross-campaign"}))["status"] == "FAIL"


def test_recovery_rejects_refunded_counters_and_extended_deadline(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization, _engine, runner, fixed_now = _imported_runner(output)
        runner.save_recovery()
        path = output / "recovery-state.json"
        original = path.read_text(encoding="utf-8")

        def attempt(mutator) -> None:
            state = json.loads(original)
            mutator(state)
            state.pop("state_sha256", None)
            state["state_sha256"] = canonical_hash(state)
            path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            engine = V4_1CampaignEngine(
                authorization,
                absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
                utc_clock=lambda: fixed_now, claim_ticket=False)
            with pytest.raises(V3IntegrityStop):
                ProductionV4_1Orchestrator.recover_v4_1(
                    engine=engine, primary_evaluator=object(),
                    replication_evaluator=object(), output_root=output,
                    continuation_record=runner.continuation_record,
                    worker_factory=lambda *_args: None)

        attempt(lambda value: value["engine"].update({"model_calls": 85}))
        attempt(lambda value: value["engine"].update({
            "model_context_tokens_reserved": 0}))
        attempt(lambda value: value.update({
            "absolute_deadline_at_utc": (
                datetime.fromisoformat(ABSOLUTE_DEADLINE_AT_UTC)
                + timedelta(seconds=1)).isoformat()}))


def test_terminalization_never_leaves_a_base_v4_summary_on_verifier_crash(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A process death between base and V4.1 finalization must be recoverable."""
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, _engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()

        def simulated_base_finalizer(self) -> dict:
            scheduler = _scheduler_state(self)
            (self.output_root / "scheduler-state.json").write_text(
                json.dumps(scheduler, sort_keys=True), encoding="utf-8")
            report = {
                "report_version": "klax-v4-offline-campaign-report-v1",
                "campaign_id": self.engine.authorization.campaign_id,
                "scientific_conclusion": "NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET",
                "evaluated_candidates": self.engine.executed_candidates,
                "local_model_calls": self.engine.model_calls,
            }
            (self.output_root / "summary.json").write_text(
                json.dumps(report, sort_keys=True), encoding="utf-8")
            return {"path": str(self.output_root), **report}

        monkeypatch.setattr(
            ProductionV4Orchestrator, "_finalize_v4", simulated_base_finalizer)

        def simulated_verifier_crash(*_args, **_kwargs):
            raise RuntimeError("simulated V4.1 verifier crash")

        monkeypatch.setattr(
            orchestrator_v4_1, "verify_scheduler_state_v4_1",
            simulated_verifier_crash)
        try:
            runner._finalize_v4()
        except RuntimeError as exc:
            assert "simulated V4.1 verifier crash" in str(exc)

        summary_path = output / "summary.json"
        if summary_path.exists():
            # A terminal artifact may remain only if it is explicitly V4.1
            # fail-closed and accompanied by the independent result.
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert summary.get("report_version") == (
                "klax-v4.1-offline-campaign-report-v1")
            assert summary.get("scientific_conclusion") == "INSUFFICIENT_EVIDENCE"
            verification = json.loads((
                output / "scheduler-verification.json").read_text(
                    encoding="utf-8"))
            assert verification.get("status") == "FAIL"


def test_status_never_calls_a_lone_base_v4_summary_complete() -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        ticket = output.parent / f".{output.name}.redteam-ticket.json"
        try:
            ticket.write_text(json.dumps({"campaign_id": output.name}),
                              encoding="utf-8")
            (output / "summary.json").write_text(json.dumps({
                "report_version": "klax-v4-offline-campaign-report-v1",
                "campaign_id": output.name,
            }), encoding="utf-8")
            status = campaign_status_v4_1(ROOT, ticket)
            assert status["status"] != "COMPLETE"
        finally:
            ticket.unlink(missing_ok=True)


def test_future_protocol_exhaustion_uses_exact_plan_but_infrastructure_does_not(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        worker_box: dict[str, _InvalidResponseWorker] = {}

        def invalid_factory(worker_id: str, _role: str):
            worker = _InvalidResponseWorker(
                worker_id, output / "local-inference-v4-1")
            worker_box["worker"] = worker
            return worker

        runner.worker_factory = invalid_factory
        item = runner.queue[11]
        expected_plan = ResearchPlanV4.from_dict(item["plan"]).identity
        digest_before = deepcopy(runner.coverage["digests"][-1])
        runner._process_item(item)
        assert worker_box["worker"].calls == 3
        assert engine.model_calls == 89
        assert engine.model_context_tokens_reserved == 1_458_176
        assert item["status"] == "REVIEWED"
        candidate = engine.candidates[item["candidate_id"]]
        assert candidate.plan.identity == expected_plan
        failure = runner.coverage["v4_1_model_nomination_failures"][-1]
        assert failure["status"] == "MODEL_NOMINATION_FAILED"
        assert failure["task_id"] == item["task_id"]
        assert failure["plan_sha256"] == expected_plan
        assert failure["model_call_numbers"] == [87, 88, 89]
        assert failure["fallback"] == {
            "status": "HOST_EXACT_PLAN_FALLBACK",
            "model_calls_charged": 0,
        }
        assert failure["scientific_evidence_created"] is False
        assert runner.coverage["digests"][-1] == digest_before

        scheduler = _v4_1_scheduler_state(runner)
        verified = verify_scheduler_state_v4_1(
            ROOT, scheduler, runner.continuation_record)
        assert verified["status"] == "PASS", verified

        missing = deepcopy(scheduler)
        missing["model_nomination_failures_v4_1"] = []
        missing.pop("state_sha256", None)
        missing["state_sha256"] = canonical_hash(missing)
        assert verify_scheduler_state_v4_1(
            ROOT, missing, runner.continuation_record)["status"] == "FAIL"

        forged = deepcopy(scheduler)
        forged["model_nomination_failures_v4_1"][0][
            "plan_sha256"] = "f" * 64
        forged.pop("state_sha256", None)
        forged["state_sha256"] = canonical_hash(forged)
        assert verify_scheduler_state_v4_1(
            ROOT, forged, runner.continuation_record)["status"] == "FAIL"

    class InfrastructureFailureWorker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id
            self.calls = 0

        def respond(self, _packet: dict) -> dict:
            self.calls += 1
            raise OSError("synthetic runtime boundary failure")

    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        worker_box: dict[str, InfrastructureFailureWorker] = {}

        def infrastructure_factory(worker_id: str, _role: str):
            worker = InfrastructureFailureWorker(worker_id)
            worker_box["worker"] = worker
            return worker

        runner.worker_factory = infrastructure_factory
        item = runner.queue[11]
        with pytest.raises(OSError, match="runtime boundary"):
            runner._process_item(item)
        assert worker_box["worker"].calls == 3
        assert engine.model_calls == 89
        assert item["status"] == "PENDING"
        assert len(engine.candidates) == 83
        assert not runner.coverage.get("v4_1_model_nomination_failures")


@pytest.mark.parametrize("action", ["reject", "abstain"])
def test_valid_nonproposal_consumes_all_three_registered_attempts(
        monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)

    class NonproposalWorker:
        def __init__(self, worker_id: str, output: Path) -> None:
            self.worker_id = worker_id
            self.output = output
            self.calls = 0

        def respond(self, packet: dict) -> dict:
            self.calls += 1
            response = {
                "protocol": "klax-research-proposal-v4",
                "task_id": packet["task_id"],
                "action": action,
                "seed_index": None,
                "rationale": "Bounded red-team nonproposal.",
                "evidence_ids": [packet["evidence"][0]["evidence_id"]],
                "limitations": ["No scientific claim."],
                "synthesis": None,
            }
            _write_proposal_artifact(
                self.output, packet, self.calls, response)
            return response

    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        box: dict[str, NonproposalWorker] = {}

        def factory(worker_id: str, _role: str):
            worker = NonproposalWorker(worker_id, output)
            box["worker"] = worker
            return worker

        runner.worker_factory = factory
        item = runner.queue[11]
        expected_plan = ResearchPlanV4.from_dict(item["plan"]).identity
        runner._process_item(item)
        assert box["worker"].calls == 3
        assert engine.model_calls == 89
        assert engine.model_context_tokens_reserved == 1_458_176
        assert item["status"] == "REVIEWED"
        assert engine.candidates[item["candidate_id"]].plan.identity == expected_plan
        assert not runner.coverage.get("v4_1_model_nomination_failures")
        saved = json.loads((
            output / "tasks" / item["task_id"] / "response.json"
        ).read_text(encoding="utf-8"))
        assert saved["host_disposition"] == (
            "ADMIT_EXACT_REGISTERED_PLAN_AFTER_CHARGED_NONPROPOSAL")


def test_parser_failure_followed_by_valid_proposal_has_no_failure_fallback(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)

    class MixedWorker:
        def __init__(self, worker_id: str, output: Path) -> None:
            self.worker_id = worker_id
            self.output = output
            self.calls = 0
            self.invalid = _InvalidResponseWorker(
                worker_id, output / "local-inference-v4-1")

        def respond(self, packet: dict) -> dict:
            self.calls += 1
            if self.calls == 1:
                return self.invalid.respond(packet)
            response = {
                "protocol": "klax-research-proposal-v4",
                "task_id": packet["task_id"],
                "action": "propose", "seed_index": 0,
                "rationale": "Valid second-attempt nomination.",
                "evidence_ids": [packet["evidence"][0]["evidence_id"]],
                "limitations": ["Red-team fixture."], "synthesis": None,
            }
            _write_proposal_artifact(self.output, packet, 2, response)
            return response

    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        _authorization_value, engine, runner, _now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        box: dict[str, MixedWorker] = {}

        def factory(worker_id: str, _role: str):
            worker = MixedWorker(worker_id, output)
            box["worker"] = worker
            return worker

        runner.worker_factory = factory
        item = runner.queue[11]
        runner._process_item(item)
        rows = [row for row in runner.coverage["call_journal"]
                if row.get("task_id") == item["task_id"]]
        assert box["worker"].calls == 2
        assert engine.model_calls == 88
        assert [row["model_call_number"] for row in rows] == [87, 88]
        assert rows[0]["failure_class"] == "MODEL_NOMINATION_PROTOCOL_ERROR"
        assert rows[1]["status"] == "COMPLETED"
        assert item["status"] == "REVIEWED"
        assert not runner.coverage.get("v4_1_model_nomination_failures")
        saved = json.loads((
            output / "tasks" / item["task_id"] / "response.json"
        ).read_text(encoding="utf-8"))
        assert saved["model_nomination_failure"] is None
        assert saved["host_disposition"] == (
            "ADMIT_WORKER_NOMINATED_EXACT_REGISTERED_PLAN")


@pytest.mark.parametrize("failure_count", [1, 3])
def test_future_protocol_retry_resumes_saved_failure_prefix_without_replay(
        monkeypatch: pytest.MonkeyPatch, failure_count: int) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization, engine, runner, fixed_now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        runner.worker_factory = lambda worker_id, _role: _InvalidResponseWorker(
            worker_id, output / "local-inference-v4-1")
        task_id = runner.queue[11]["task_id"]
        original_save = runner.save_recovery
        crashed = False

        def crash_after_first_durable_parse_failure():
            nonlocal crashed
            value = original_save()
            journal = runner.coverage["call_journal"]
            saved_failures = [
                row for row in journal if row.get("task_id") == task_id
                and row.get("failure_class")
                == "MODEL_NOMINATION_PROTOCOL_ERROR"]
            if not crashed and len(saved_failures) == failure_count:
                crashed = True
                raise RuntimeError("simulated crash after saved parser failure")
            return value

        monkeypatch.setattr(runner, "save_recovery",
                            crash_after_first_durable_parse_failure)
        with pytest.raises(RuntimeError, match="saved parser failure"):
            runner._process_item(runner.queue[11])
        assert engine.model_calls == 86 + failure_count

        recovered_engine = V4_1CampaignEngine(
            authorization,
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
            utc_clock=lambda: fixed_now,
            claim_ticket=False,
        )
        recovered = ProductionV4_1Orchestrator.recover_v4_1(
            engine=recovered_engine, primary_evaluator=object(),
            replication_evaluator=object(), output_root=output,
            continuation_record=runner.continuation_record,
            worker_factory=lambda worker_id, _role: _InvalidResponseWorker(
                worker_id, output / "local-inference-v4-1"))
        monkeypatch.setattr(
            recovered, "_execute_and_review",
            lambda item: _finish_review_without_data(recovered, item))
        recovered._process_item(recovered.queue[11])
        failure = recovered.coverage["v4_1_model_nomination_failures"][-1]
        assert recovered_engine.model_calls == 89
        assert failure["model_call_numbers"] == [87, 88, 89]
        assert recovered.queue[11]["status"] == "REVIEWED"


@pytest.mark.parametrize("artifacts_written", [False, True])
def test_reserved_call_crash_never_replays_or_refunds(
        monkeypatch: pytest.MonkeyPatch, artifacts_written: bool) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)

    class ProcessCrashWorker:
        def __init__(self, worker_id: str, output: Path) -> None:
            self.worker_id = worker_id
            self.output = output

        def respond(self, packet: dict) -> dict:
            if artifacts_written:
                attempt = (self.output / "local-inference-v4-1"
                           / packet["task_id"] / "attempt-1")
                attempt.mkdir(parents=True)
                (attempt / "packet.json").write_text(
                    json.dumps(packet, sort_keys=True), encoding="utf-8")
                (attempt / "stdout.bin").write_bytes(b"partial")
                (attempt / "process.json").write_text(json.dumps({
                    "exit_code": 0, "timed_out": False,
                    "output_limit_exceeded": False, "cleanup_failed": False,
                    "reader_errors": [],
                }, sort_keys=True), encoding="utf-8")
            raise KeyboardInterrupt("simulated process-boundary crash")

    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization, engine, runner, fixed_now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        runner.worker_factory = lambda worker_id, _role: ProcessCrashWorker(
            worker_id, output)
        with pytest.raises(KeyboardInterrupt, match="process-boundary"):
            runner._process_item(runner.queue[11])
        assert engine.model_calls == 87

        recovered_engine = V4_1CampaignEngine(
            authorization,
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
            utc_clock=lambda: fixed_now,
            claim_ticket=False,
        )
        class ProposalWorker:
            def __init__(self, worker_id: str, output: Path) -> None:
                self.worker_id = worker_id
                self.output = output
                self.calls = 0

            def respond(self, packet: dict) -> dict:
                self.calls += 1
                response = {
                    "protocol": "klax-research-proposal-v4",
                    "task_id": packet["task_id"],
                    "action": "propose", "seed_index": 0,
                    "rationale": "Resume without replaying the reserved call.",
                    "evidence_ids": [packet["evidence"][0]["evidence_id"]],
                    "limitations": ["Red-team fixture."], "synthesis": None,
                }
                _write_proposal_artifact(self.output, packet, 2, response)
                return response

        worker_box: dict[str, ProposalWorker] = {}

        def continue_once(worker_id: str, _role: str):
            worker = ProposalWorker(worker_id, output)
            worker_box["worker"] = worker
            return worker

        recovered = ProductionV4_1Orchestrator.recover_v4_1(
            engine=recovered_engine, primary_evaluator=object(),
            replication_evaluator=object(), output_root=output,
            continuation_record=runner.continuation_record,
            worker_factory=continue_once)
        monkeypatch.setattr(
            recovered, "_execute_and_review",
            lambda item: _finish_review_without_data(recovered, item))
        recovered._process_item(recovered.queue[11])
        rows = [row for row in recovered.coverage["call_journal"]
                if row.get("task_id") == recovered.queue[11]["task_id"]]
        assert worker_box["worker"].calls == 1
        assert [row["model_call_number"] for row in rows] == [87, 88]
        assert rows[0]["status"] == "FAILED"
        assert rows[0]["failure_class"] == (
            "PROCESS_OUTCOME_UNAVAILABLE_AFTER_CRASH")
        assert rows[0]["crash_reconciled"] is True
        assert recovered_engine.model_calls == 88
        assert recovered_engine.model_context_tokens_reserved == (
            88 * 16_384)
        assert recovered.queue[11]["status"] == "REVIEWED"


def test_crash_before_retry_registration_is_fail_closed_without_replay(
        monkeypatch: pytest.MonkeyPatch) -> None:
    parent = ROOT / "runs/campaigns_v4_1"
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="v4-1-offline-redteam-", dir=parent) as raw:
        output = Path(raw)
        authorization, engine, runner, fixed_now = _imported_runner(output)
        monkeypatch.setattr(
            runner, "_execute_and_review",
            lambda item: _finish_review_without_data(runner, item))
        runner.apply_incident_fallback_v4_1()
        runner.worker_factory = lambda worker_id, _role: _InvalidResponseWorker(
            worker_id, output / "local-inference-v4-1")

        def crash_before_registration(_task_id: str) -> None:
            raise RuntimeError("simulated crash before retry registration")

        monkeypatch.setattr(
            engine, "register_transient_failure", crash_before_registration)
        with pytest.raises(RuntimeError, match="before retry registration"):
            runner._process_item(runner.queue[11])
        # The last durable checkpoint is the already charged RESERVED call.
        saved = json.loads((output / "recovery-state.json").read_text(
            encoding="utf-8"))
        assert saved["coverage"]["call_journal"][-1]["status"] == "RESERVED"
        assert saved["engine"]["model_calls"] == 87

        recovered_engine = V4_1CampaignEngine(
            authorization,
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
            utc_clock=lambda: fixed_now,
            claim_ticket=False,
        )
        recovered = ProductionV4_1Orchestrator.recover_v4_1(
            engine=recovered_engine, primary_evaluator=object(),
            replication_evaluator=object(), output_root=output,
            continuation_record=runner.continuation_record,
            worker_factory=lambda worker_id, _role: _InvalidResponseWorker(
                worker_id, output / "local-inference-v4-1"))
        monkeypatch.setattr(
            recovered, "_execute_and_review",
            lambda item: _finish_review_without_data(recovered, item))
        recovered._process_item(recovered.queue[11])
        rows = [row for row in recovered.coverage["call_journal"]
                if row.get("task_id") == recovered.queue[11]["task_id"]]
        assert [row["model_call_number"] for row in rows] == [87, 88, 89]
        assert rows[0]["crash_reconciled"] is True
        assert rows[0]["failure_class"] == "MODEL_NOMINATION_PROTOCOL_ERROR"
        assert recovered_engine.model_calls == 89
        assert recovered.coverage["v4_1_model_nomination_failures"][-1][
            "model_call_numbers"] == [87, 88, 89]
