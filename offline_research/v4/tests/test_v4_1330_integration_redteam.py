"""Independent fail-closed checks for the V4 13:30 integration.

These tests intentionally cross module boundaries.  A locally correct V4
plan or dataset is insufficient if the scheduler, evaluator, verifier, or
readiness path silently falls back to the frozen V3 12:00 data contract.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from klax_lab.campaign_v3 import (
    CandidateRecord, REPLICATION_CHECKS, REPLICATION_VERSION,
    V3CampaignAuthorization, V3CampaignBudget, V3CampaignError,
    validate_v3_replication,
)
from klax_lab.provenance import canonical_hash, sha256_file
from klax_lab.orchestrator_v3 import _independent_replication
from v4 import (
    dataset_v4, execution_coverage, orchestrator_v4, readiness_v4,
    verifier_v4,
)
from v4.research_plan_v4 import (
    DECISION_TIMES_UTC_V4, ResearchPlanV4, V4EvaluatorAdapterRequired,
    compile_plan_v4, exact_v3_evaluator_plan,
)
from v4.evaluator_v4 import OfflineCandidateEvaluatorV4
from v4.local_worker_v4 import (
    PROTOCOL_V4, parse_v4_worker_response, validate_v4_worker_packet,
)
from v4.orchestrator_v4 import V4CampaignEngine
from v4.verifier_v4 import (
    _self_test_scheduler, frozen_universe_v4, verify_scheduler_state_v4,
)


ROOT = Path(__file__).resolve().parents[2]
V4_TIMES = ("13:30", "15:00", "18:00")


def _bundle() -> dict:
    return json.loads((ROOT / dataset_v4.BUNDLE).read_text(encoding="utf-8"))


def _design():
    registration = execution_coverage.load_v4_execution_registration(ROOT)
    tracks = tuple(execution_coverage.ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    records = execution_coverage.load_ranked_v3_parent_records(ROOT, registration)
    parent, _ = execution_coverage.select_forecast_parent_v4(records)
    plans = execution_coverage.execution_factorial_plans_v4(parent, tracks)
    return registration, plans


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def test_every_effective_v4_layer_uses_only_the_amended_time_grid() -> None:
    amendment = json.loads((ROOT / dataset_v4.DECISION_TIME_AMENDMENT).read_text(
        encoding="utf-8"))
    bundle = _bundle()
    registration = execution_coverage.load_v4_execution_registration(ROOT)
    factorial = registration["execution_factorial"]

    assert DECISION_TIMES_UTC_V4 == V4_TIMES
    assert dataset_v4.DECISION_TIMES_UTC == V4_TIMES
    assert tuple(execution_coverage.DECISION_TIMES) == V4_TIMES
    assert tuple(factorial["decision_times_utc"]) == V4_TIMES
    assert tuple(bundle["decision_times_utc"]) == V4_TIMES
    assert tuple(amendment["prospective_decision_times_utc"]) == V4_TIMES
    assert "12:00" not in factorial["decision_times_utc"]
    assert amendment["later_retiming_permitted"] is False
    assert amendment["alternate_minute_fallback_permitted"] is False


def test_frozen_dataset_contains_real_1330_snapshots_without_relabeling() -> None:
    verified = dataset_v4.verify_v4_dataset(ROOT)
    bundle = _bundle()
    assert verified["dataset_id"] == bundle["dataset_id"]
    assert verified["decision_times_utc"] == list(V4_TIMES)

    frozen = ROOT / dataset_v4.DESTINATION
    names = (
        "weather_training_features.jsonl",
        "development_calibration_features.jsonl",
        "development_evaluation_features.jsonl",
    )
    counts = Counter()
    for name in names:
        for row in _iter_jsonl(frozen / name):
            decision_time = row["decision_time_utc"]
            assert decision_time in V4_TIMES
            assert decision_time != "12:00"
            decision = _stamp(row["decision_at"])
            assert decision.strftime("%H:%M") == decision_time
            counts[(name, decision_time)] += 1
            for observation in row.get("observations", ()):
                assert _stamp(observation["observed_at"]) <= decision
                assert _stamp(observation["available_at"]) <= decision
            for forecast in row.get("forecasts", ()):
                assert _stamp(forecast["initialized_at"]) <= decision
                assert _stamp(forecast["available_at"]) <= decision
            for contract in row.get("contracts", ()):
                market = contract["market"]
                candle = market["latest_completed_candle"]
                candle_at = datetime.fromtimestamp(
                    candle["end_period_ts"], timezone.utc)
                assert candle_at <= decision
                assert candle["period_minutes"] == 1
                assert candle["evidence_grade"] == "B_aggregated_quote"
                assert candle["historical_depth_available"] is False
                assert candle["hypothetical_fill_supported"] is False
                trades = market["public_trades_last_60_minutes"]
                assert _stamp(trades["window_end_inclusive"]) == decision
                assert _stamp(trades["window_start_exclusive"]) < decision
    for name in names:
        assert counts[(name, "13:30")] > 0
        assert counts[(name, "12:00")] == 0


def test_all_3072_scheduler_members_are_v4_typed_and_bundle_bound() -> None:
    registration, plans = _design()
    bundle = _bundle()
    assert len(plans) == 3072
    assert len({plan.identity for plan in plans}) == 3072
    assert all(type(plan) is ResearchPlanV4 for plan in plans)
    assert Counter(plan.decision_time_utc for plan in plans) == {
        time: 1024 for time in V4_TIMES}
    assert all(plan.data_bundle_version == bundle["dataset_id"] for plan in plans)
    assert all(plan.data_bundle_sha256 == bundle["bundle_sha256"] for plan in plans)
    assert all(plan.plan_version == "klax-research-plan-v4" for plan in plans)
    assert registration["execution_factorial"]["registered_plan_count"] == 3072

    universe = frozen_universe_v4(ROOT)
    assert len(universe.plan_by_id) == 3072
    assert set(universe.plan_by_id) == {plan.identity for plan in plans}
    assert all(row["plan_version"] == "klax-research-plan-v4"
               for row in universe.plan_by_id.values())
    assert all(row["data_bundle_sha256"] == bundle["bundle_sha256"]
               for row in universe.plan_by_id.values())


def test_1330_plan_identity_is_preserved_from_compiler_to_verifier() -> None:
    _, plans = _design()
    value = next(plan for plan in plans if plan.decision_time_utc == "13:30")
    compiled = compile_plan_v4(value)
    universe = frozen_universe_v4(ROOT)
    assert compiled.identity == value.identity
    assert compiled.execution_manifest["research_plan_sha256"] == value.identity
    assert compiled.execution_manifest["data_binding"]["asof_time_utc"] == "13:30"
    assert compiled.execution_manifest["decision_policy"][
        "decision_time_utc"] == "13:30"
    assert value.identity in universe.plan_by_id
    with pytest.raises(V4EvaluatorAdapterRequired, match="cannot represent 13:30"):
        exact_v3_evaluator_plan(value)


def test_production_evaluator_factory_cannot_open_the_v3_bundle() -> None:
    bundle = _bundle()
    assert orchestrator_v4.DATA_BUNDLE_PATH == dataset_v4.BUNDLE
    assert bundle["schema_version"] == "klax-v4-data-bundle-v1"
    assert bundle["bundle_sha256"] == canonical_hash({
        key: value for key, value in bundle.items() if key != "bundle_sha256"})
    assert sha256_file(ROOT / orchestrator_v4.DATA_BUNDLE_PATH) != sha256_file(
        ROOT / readiness_v4.V3_DATA_BUNDLE_PATH)

    factory_source = inspect.getsource(orchestrator_v4._production_evaluators_v4)
    assert "v3_data_bundle.json" not in factory_source
    assert "OfflineCandidateEvaluatorV3.from_bound_directory" not in factory_source
    assert "primary_fee_scenario" in factory_source


def test_production_admission_and_execution_never_compile_a_v4_plan_as_v3() -> None:
    boundaries = (
        orchestrator_v4.evaluate_candidate_v4,
        orchestrator_v4.ProductionV4Orchestrator._registered_plans,
        orchestrator_v4.ProductionV4Orchestrator._allocate_v4_queue,
        orchestrator_v4.ProductionV4Orchestrator._packet_with_digest,
        orchestrator_v4.ProductionV4Orchestrator._process_item,
        orchestrator_v4.ProductionV4Orchestrator._synthesize_if_due,
    )
    source = "\n".join(inspect.getsource(value) for value in boundaries)
    assert "ResearchPlanV3.from_dict" not in source
    assert "compile_plan_v3" not in source
    assert "exact_v3_evaluator_plan" not in source


def test_production_keeps_preregistered_candidate_and_synthesis_call_accounting() -> None:
    candidate_source = inspect.getsource(
        orchestrator_v4.ProductionV4Orchestrator._process_item)
    synthesis_source = inspect.getsource(
        orchestrator_v4.ProductionV4Orchestrator._synthesize_if_due)
    assert "call_journal" in candidate_source
    assert '"candidate"' in candidate_source
    assert '"retry"' in candidate_source
    assert '"model_inference_used": False' not in candidate_source
    assert "call_journal" in synthesis_source
    assert '"synthesis"' in synthesis_source
    assert '"retry"' in synthesis_source


def test_real_synthetic_epoch_charges_twelve_calls_and_due_synthesis(
        monkeypatch) -> None:
    """Exercise the production V4 nomination/synthesis boundary without scoring.

    Candidate evaluation is replaced with an outcome-blind review stub so this
    remains a fast protocol/accounting test.  Allocation, packet construction,
    parsing, model-call reservations, admission, synthesis, persistence, and
    next-digest lineage all use the production implementations.
    """
    bundle = _bundle()
    v3_goal = json.loads((ROOT / "configs/v3_goal.json").read_text(
        encoding="utf-8"))
    partitions = v3_goal["partitions"]
    campaign_parent = ROOT / "runs/campaigns_v4"
    campaign_parent.mkdir(parents=True, exist_ok=True)

    class FixtureWorker:
        def __init__(self, worker_id, engine, packets, active_counts):
            self.worker_id = worker_id
            self.engine = engine
            self.packets = packets
            self.active_counts = active_counts

        def respond(self, packet):
            checked = validate_v4_worker_packet(dict(packet))
            assert self.engine.active_model_worker == self.worker_id
            self.active_counts["current"] += 1
            self.active_counts["maximum"] = max(
                self.active_counts["maximum"], self.active_counts["current"])
            try:
                self.packets.append(checked)
                if checked["mode"] == "candidate_nomination":
                    response = {
                        "protocol": PROTOCOL_V4,
                        "task_id": checked["task_id"],
                        "action": "propose", "seed_index": 0,
                        "rationale": "Synthetic V4 protocol fixture nomination.",
                        "evidence_ids": [checked["evidence"][0]["evidence_id"]],
                        "limitations": [
                            "Protocol fixture only; it makes no performance claim."],
                        "synthesis": None,
                    }
                else:
                    response = {
                        "protocol": PROTOCOL_V4,
                        "task_id": checked["task_id"],
                        "action": "synthesize", "seed_index": None,
                        "rationale": "Synthetic V4 consolidation fixture.",
                        "evidence_ids": [checked["evidence"][0]["evidence_id"]],
                        "limitations": [
                            "Protocol fixture only; no candidate was scored."],
                        "synthesis": {
                            "summary": "Carry the hash-bound epoch evidence forward.",
                            "prioritized_candidate_ids": [],
                            "falsified_candidate_ids": [],
                            "principal_bottlenecks": ["unscored_fixture"],
                            "uncertainties": ["candidate_performance_unknown"],
                        },
                    }
                return parse_v4_worker_response(
                    json.dumps(response, sort_keys=True), checked)
            finally:
                self.active_counts["current"] -= 1

    def fixture_review(runner, item):
        # Keep the production nomination/admission path intact while avoiding
        # labels, fills, or return calculations in this protocol-only test.
        assert item["candidate_id"] in runner.engine.candidates
        item["status"] = "REVIEWED"
        runner.save_recovery()

    monkeypatch.setattr(
        orchestrator_v4.ProductionV4Orchestrator,
        "_execute_and_review", fixture_review)

    with TemporaryDirectory(prefix="v4-redteam-epoch-", dir=campaign_parent) as raw:
        output = Path(raw)
        campaign_id = output.name
        budget = V3CampaignBudget(
            maximum_epochs=5,
            maximum_distinct_executed_candidates=12,
            maximum_new_candidates_per_epoch=12,
            maximum_local_model_calls=13,
            local_reserved_context_tokens=13 * 16_384,
            maximum_local_inference_concurrency=1,
            maximum_wall_seconds=600,
            maximum_transient_retries_per_task=0,
            empty_epoch_patience=2,
            maximum_paid_api_dollars=0,
        )
        authorization = V3CampaignAuthorization(
            root=ROOT, campaign_id=campaign_id,
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
        engine = V4CampaignEngine(authorization)
        for expected_epoch in range(1, 5):
            assert engine.begin_epoch() == expected_epoch

        packets = []
        active_counts = {"current": 0, "maximum": 0}

        def worker_factory(worker_id, _role):
            return FixtureWorker(worker_id, engine, packets, active_counts)

        runner = orchestrator_v4.ProductionV4Orchestrator(
            engine, object(), object(), output,
            worker_factory=worker_factory)
        runner.queue = runner._allocate_v4_queue()
        assert len(runner.queue) == 12
        assert Counter(item["category"] for item in runner.queue) == (
            execution_coverage.EPOCH_QUOTAS)

        # The production resume boundary must restore the same injected V4
        # worker factory; silently dropping it would bypass every model call.
        runner.save_recovery()
        engine = V4CampaignEngine(authorization, claim_ticket=False)
        runner = orchestrator_v4.ProductionV4Orchestrator.recover(
            engine=engine, primary_evaluator=object(),
            replication_evaluator=object(), output_root=output,
            worker_factory=worker_factory)
        assert runner.worker_factory is worker_factory
        assert len(runner.queue) == 12

        for index, item in enumerate(runner.queue):
            runner.next_queue_index = index
            runner._process_item(item)

        candidate_journal = [
            row for row in runner.coverage["call_journal"]
            if row["call_type"] == "candidate"]
        assert len(candidate_journal) == 12
        assert engine.model_calls == 12
        assert [row["model_call_number"] for row in candidate_journal] == list(
            range(1, 13))
        assert all(row["status"] == "COMPLETED" for row in candidate_journal)
        assert all(item["status"] == "REVIEWED" for item in runner.queue)
        assert engine.active_model_worker is None
        assert active_counts == {"current": 0, "maximum": 1}

        assert runner.registration["cross_pollination"][
            "local_model_synthesis_every_epochs"] == 4
        runner._synthesize_if_due()
        synthesis_journal = [
            row for row in runner.coverage["call_journal"]
            if row["call_type"] == "synthesis"]
        assert len(synthesis_journal) == 1
        assert synthesis_journal[0]["status"] == "COMPLETED"
        assert synthesis_journal[0]["model_call_number"] == 13
        assert engine.model_calls == 13
        assert engine.active_model_worker is None
        assert active_counts == {"current": 0, "maximum": 1}

        assert len(packets) == 13
        assert all(packet["protocol"] == PROTOCOL_V4 for packet in packets)
        assert Counter(packet["mode"] for packet in packets) == {
            "candidate_nomination": 12,
            "cross_pollination_synthesis": 1,
        }
        assert all(packet["data_bundle_sha256"] == bundle["bundle_sha256"]
                   for packet in packets)
        assert all(
            ResearchPlanV4.from_dict(plan).decision_time_utc in V4_TIMES
            for packet in packets for plan in packet["seed_plans"])

        artifact = runner.coverage["synthesis_artifacts"][-1]
        previous_digest = runner.coverage["digests"][-1]
        assert artifact["response"]["synthesis"]["summary"] == (
            "Carry the hash-bound epoch evidence forward.")
        assert engine.begin_epoch() == 5
        runner._allocate_v4_queue()
        next_digest = runner.coverage["digests"][-1]
        assert next_digest["previous_digest_sha256"] == previous_digest[
            "digest_sha256"]
        assert next_digest["prior_synthesis_artifact_sha256"] == artifact[
            "artifact_sha256"]
        assert next_digest["prior_synthesis_artifact"] == artifact
        assert next_digest["prior_synthesis_artifact"]["response"][
            "synthesis"]["summary"] == (
                "Carry the hash-bound epoch evidence forward.")


def test_direct_unjournaled_admission_cannot_pass_scheduler_verification() -> None:
    state = _self_test_scheduler(ROOT)
    assert state["completed_plan_sha256s"]
    assert any(row.get("candidate_id") for epoch in state["allocation_history"]
               for row in epoch["allocations"])
    state["call_journal"] = []
    state["candidate_calls_used"] = 0
    state["synthesis_calls_used"] = 0
    state["transient_retry_calls_used"] = 0
    state["state_sha256"] = canonical_hash({
        key: value for key, value in state.items() if key != "state_sha256"})
    report = verify_scheduler_state_v4(ROOT, state)
    assert report["status"] == "FAIL"
    assert report["checks"]["call_budget_accounting"] is False
    assert "CALL_BUDGET_ACCOUNTING" in report["failures"]


def test_real_1330_candidate_runs_through_v4_evaluator_identity() -> None:
    _, plans = _design()
    plan = next(value for value in plans
                if value.decision_time_utc == "13:30")
    bundle = _bundle()
    v3_goal = json.loads((ROOT / "configs/v3_goal.json").read_text(
        encoding="utf-8"))
    partitions = v3_goal["partitions"]
    with TemporaryDirectory(prefix=".pytest-v4-1330-", dir=ROOT) as temporary:
        artifact_root = Path(temporary) / "primary"
        evaluator = OfflineCandidateEvaluatorV4.from_bound_directory(
            project_root=ROOT,
            dataset_root=ROOT / dataset_v4.DESTINATION,
            artifact_root=artifact_root,
            data_bundle_manifest=ROOT / dataset_v4.BUNDLE,
            expected_bundle_version=bundle["dataset_id"],
            expected_bundle_sha256=bundle["bundle_sha256"],
        )
        replication_evaluator = OfflineCandidateEvaluatorV4.from_bound_directory(
            project_root=ROOT,
            dataset_root=ROOT / dataset_v4.DESTINATION,
            artifact_root=Path(temporary) / "replication",
            data_bundle_manifest=ROOT / dataset_v4.BUNDLE,
            expected_bundle_version=bundle["dataset_id"],
            expected_bundle_sha256=bundle["bundle_sha256"],
        )
        budget = V3CampaignBudget(
            maximum_epochs=1, maximum_distinct_executed_candidates=1,
            maximum_new_candidates_per_epoch=1, maximum_local_model_calls=1,
            local_reserved_context_tokens=16_384,
            maximum_local_inference_concurrency=1,
            maximum_wall_seconds=600,
            maximum_transient_retries_per_task=0,
            empty_epoch_patience=2, maximum_paid_api_dollars=0,
        )
        authorization = V3CampaignAuthorization(
            root=ROOT, campaign_id="v4-redteam-1330",
            readiness_path=Path(temporary) / "readiness.json",
            ticket_path=Path(temporary) / "ticket.json",
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
        engine = V4CampaignEngine(authorization)
        engine.begin_epoch()
        compiled = compile_plan_v4(plan)
        admission = engine.admit_registered_plan_v4(
            plan, worker_id="v4-redteam-worker", task_id="v4-redteam-task")
        assert admission["execution_manifest"] == compiled.execution_manifest
        engine.execute_candidate(admission["candidate_id"], evaluator)
        evaluation = engine.candidates[admission["candidate_id"]].evaluation
        assert evaluation is not None
        candidate = evaluation["candidate"]
        assert candidate["research_plan_sha256"] == plan.identity
        assert candidate["dataset_id"] == bundle["dataset_id"]
        assert candidate["forecast_scores"]["scored_events"] == 145
        assert candidate["historical_assumed_fill"]["fee_scenario"] == {
            "name": "historical_proxy_not_verified_2025",
            "rate": "0.07", "rounding": "0.01",
            "provenance": "configs/evaluation.json: fee_scenario; historical schedule remains unverified",
            "historical_verified": False,
        }
        record = engine.candidates[admission["candidate_id"]]
        replication = _independent_replication(
            engine, record, replication_evaluator,
            artifact_root / plan.identity,
            Path(replication_evaluator.artifact_root) / plan.identity,
        )
        assert replication["status"] == "PASS"
        assert replication["candidate_plan_sha256"] == plan.identity
        assert validate_v3_replication(record, replication) is True
        saved = json.loads((artifact_root / plan.identity /
                            "compiled_manifest.json").read_text(encoding="utf-8"))
        assert saved["manifest_version"] == "klax-v4-execution-manifest-v1"
        assert saved["research_plan_sha256"] == plan.identity
        assert saved["data_binding"]["asof_time_utc"] == "13:30"
        assert saved["decision_policy"]["decision_time_utc"] == "13:30"


def test_v4_readiness_binds_new_bundle_and_rejects_v3_authorizations() -> None:
    checked = readiness_v4.validate_v4_preregistration(ROOT)
    bindings = readiness_v4._collect_bindings(ROOT, checked)
    bound = bindings["data_bundle"]
    bundle = _bundle()
    assert bound["path"] == dataset_v4.BUNDLE.as_posix()
    assert bound["version"] == bundle["dataset_id"]
    assert bound["sha256"] == bundle["bundle_sha256"]
    assert bound["manifest_sha256"] == sha256_file(ROOT / dataset_v4.BUNDLE)

    with pytest.raises(readiness_v4.V4ReadinessError):
        readiness_v4.load_v4_campaign_authorization(
            ROOT, Path("data/manifests/v3_readiness_r2.json"),
            Path("runs/v3_offline_campaign_ticket_r2.json"))


def test_independent_verifier_uses_the_v4_fold_contract() -> None:
    assert verifier_v4.FOLD_MANIFEST == dataset_v4.FOLD_COMPONENT
    dates, mapping, folds = verifier_v4._fold_contract(ROOT)
    bundle = _bundle()
    fold_component = json.loads((ROOT / dataset_v4.FOLD_COMPONENT).read_text(
        encoding="utf-8"))
    assert fold_component["dataset_id"] == bundle["dataset_id"]
    assert len(dates) == len(mapping) == 145
    assert len(folds) == 5


def test_replication_contract_rejects_any_plan_identity_substitution() -> None:
    _, plans = _design()
    value = next(plan for plan in plans if plan.decision_time_utc == "13:30")
    record = CandidateRecord(
        candidate_id="v4-redteam-candidate", plan=value,
        discovery_worker_id="v4-discovery-worker", epoch=1,
        artifact_sha256s={"evaluation_sha256": "a" * 64},
    )
    replication = {
        "contract_version": REPLICATION_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": value.identity,
        "novelty_fingerprint": value.novelty_fingerprint,
        "verifier_id": "v4-independent-replicator",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "source_artifact_sha256s": record.artifact_sha256s,
        "checks": {name: True for name in REPLICATION_CHECKS},
        "status": "PASS",
        "differences": [],
    }
    assert validate_v3_replication(record, replication) is True
    forged = dict(replication)
    forged["candidate_plan_sha256"] = "f" * 64
    with pytest.raises(V3CampaignError, match="different evidence"):
        validate_v3_replication(record, forged)


def test_unchanged_fees_and_promotion_gates_are_hash_bound() -> None:
    checked = readiness_v4.validate_v4_preregistration(ROOT)
    v3_gates = checked["v3_promotion_gates"]
    v4_gates = checked["goal"]["development_promotion_gates"]
    assert {key: v4_gates[key] for key in v3_gates} == v3_gates
    assert v4_gates["minimum_primary_capital_weighted_net_return"] == 0.10
    assert v4_gates[
        "minimum_estimated_net_expected_return_for_each_selected_trade"] == 0.10
    assert v4_gates["cost_stress"] == {
        "fee_rate": 0.1,
        "additional_adverse_price_per_contract_dollars": 0.02,
        "quantity": 1,
        "minimum_capital_weighted_return_exclusive": 0.0,
    }
    assert checked["promotion_gates_sha256"] == canonical_hash(v3_gates)
