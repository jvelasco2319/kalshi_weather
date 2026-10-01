from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import shutil

import pytest

from klax_lab.campaign_v3 import (
    CANDIDATE_ARTIFACTS, CHAMPION_RANKING_RULE, CRITIC_CHECKS, CRITIC_VERSION,
    REPLICATION_CHECKS, REPLICATION_VERSION, CandidateEvaluationBundle,
    DeterministicV3FixtureWorker, ProtectedFinalRefusal, V3BudgetStop,
    V3CampaignEngine, V3CampaignError, V3IntegrityStop, V3ReadinessRefusal,
    load_v3_campaign_authorization,
)
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.research_plan_v3 import make_plan_v3
from klax_lab.research_protocol_v3 import PROTOCOL_V3
from klax_lab.v3_readiness import REQUIRED_COMPONENT_MANIFESTS


PROJECT = Path(__file__).resolve().parents[1]


def build_authorized_fixture(tmp_path: Path):
    root = tmp_path / "fixture-project"
    (root / "configs").mkdir(parents=True)
    (root / "schemas").mkdir(parents=True)
    shutil.copy2(PROJECT / "configs/v3_goal.json", root / "configs/v3_goal.json")
    shutil.copy2(PROJECT / "schemas/research-plan-v3.schema.json",
                 root / "schemas/research-plan-v3.schema.json")
    goal = json.loads((root / "configs/v3_goal.json").read_text())
    components = []
    for requirement in REQUIRED_COMPONENT_MANIFESTS:
        path = root / requirement.path
        write_json(path, {
            "status": "PASS", "synthetic": True, "protected_final_read": False,
            "component": requirement.component,
        })
        components.append({
            "component": requirement.component,
            "path": requirement.path,
            "sha256": sha256_file(path),
            "status": "PASS",
        })
    dataset_path = root / "data/manifests/v3_dataset.json"
    dataset_manifest_sha = sha256_file(dataset_path)
    readiness_path = root / "runs/fixtures/v3-readiness.json"
    readiness = {
        "readiness_version": "klax-v3-readiness-v1",
        "status": "READY_FOR_V3_OFFLINE_CAMPAIGN",
        "architecture_version": 3,
        "synthetic": True,
        "ready_for_v3_campaign": True,
        "v3_campaign_authorized": True,
        "protected_final_access_authorized": False,
        "offline_verified": True,
        "holdout_access_denied": True,
        "historical_only": True,
        "config_path": "configs/v3_goal.json",
        "config_sha256": sha256_file(root / "configs/v3_goal.json"),
        "schema_path": "schemas/research-plan-v3.schema.json",
        "schema_sha256": sha256_file(root / "schemas/research-plan-v3.schema.json"),
        "data_bundle": {
            "version": "synthetic-v3-data",
            "sha256": "a" * 64,
            "manifest_path": "data/manifests/v3_dataset.json",
            "manifest_sha256": dataset_manifest_sha,
            "scope": "weather_training_calibration_and_scored_development_only",
        },
        "code_sha256": "b" * 64,
        "evaluation_policy_sha256": "c" * 64,
        "promotion_gates_sha256": canonical_hash(goal["development_promotion_gates"]),
        "campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "campaign_budget": goal["campaign_budget"],
        "partition_contract": goal["partitions"],
        "partition_contract_sha256": canonical_hash(goal["partitions"]),
        "champion_ranking_rule": CHAMPION_RANKING_RULE,
        "champion_ranking_rule_sha256": canonical_hash(
            {"rule": CHAMPION_RANKING_RULE}),
        "validated_component_manifests": components,
        "protected_final_roots": ["data/protected_final"],
        "created_at_utc": "2026-09-25T00:00:00+00:00",
    }
    write_json(readiness_path, readiness)
    ticket_path = root / "runs/fixtures/v3-ticket.json"
    ticket = {
        "ticket_version": "klax-v3-offline-campaign-ticket-v1",
        "status": "ACTIVE",
        "synthetic": True,
        "one_use": True,
        "campaign_id": "synthetic-v3-campaign",
        "readiness_path": readiness_path.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(readiness_path),
        "config_sha256": readiness["config_sha256"],
        "schema_sha256": readiness["schema_sha256"],
        "data_bundle_version": readiness["data_bundle"]["version"],
        "data_bundle_sha256": readiness["data_bundle"]["sha256"],
        "code_sha256": readiness["code_sha256"],
        "evaluation_policy_sha256": readiness["evaluation_policy_sha256"],
        "promotion_gates_sha256": readiness["promotion_gates_sha256"],
        "campaign_budget_sha256": readiness["campaign_budget_sha256"],
        "partition_contract_sha256": readiness["partition_contract_sha256"],
        "champion_ranking_rule_sha256": readiness["champion_ranking_rule_sha256"],
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 1,
        "issued_at_utc": "2026-09-25T00:01:00+00:00",
    }
    write_json(ticket_path, ticket)
    authorization = load_v3_campaign_authorization(
        root, readiness_path, ticket_path, allow_synthetic=True)
    return root, readiness_path, ticket_path, authorization


def make_plan(authorization, **overrides):
    values = {
        "colony": "local_weather", "stage": "forecast_skill",
        "data_bundle_version": authorization.data_bundle_version,
        "data_bundle_sha256": authorization.data_bundle_sha256,
    }
    values.update(overrides)
    return make_plan_v3(**values)


def packet(engine: V3CampaignEngine, plan, task_id="task-1"):
    return {
        "protocol": PROTOCOL_V3,
        "task_id": task_id,
        "campaign_id": engine.authorization.campaign_id,
        "worker_role": "explorer",
        "scope": "synthetic_only",
        "synthetic": True,
        "question": "Propose one finite synthetic candidate.",
        "readiness_sha256": engine.authorization.readiness_sha256,
        "config_sha256": engine.authorization.config_sha256,
        "schema_sha256": engine.authorization.schema_sha256,
        "partition_contract_sha256": engine.authorization.partition_contract_sha256,
        "data_bundle_version": engine.authorization.data_bundle_version,
        "data_bundle_sha256": engine.authorization.data_bundle_sha256,
        "colony": plan.colony,
        "stage": plan.stage,
        "parent_hypothesis_ids": list(plan.parent_hypothesis_ids),
        "parent_plan_sha256s": list(plan.parent_plan_sha256s),
        "evidence": [{
            "evidence_id": "synthetic-evidence", "scope": "synthetic",
            "summary": "Synthetic invariant evidence; no market performance.",
            "artifact_sha256": "d" * 64,
        }],
        "seed_plans": [plan.to_dict()],
        "budget_remaining": engine.budget_remaining(),
    }


def response(plan, task_id="task-1"):
    return {
        "protocol": PROTOCOL_V3,
        "task_id": task_id,
        "action": "propose",
        "seed_index": 0,
        "rationale": "Finite synthetic hypothesis.",
        "evidence_ids": ["synthetic-evidence"],
        "limitations": ["Engineering fixture only."],
        "requested_checks": [],
    }


def abstain_response(task_id):
    return {
        "protocol": PROTOCOL_V3,
        "task_id": task_id,
        "action": "abstain",
        "seed_index": None,
        "rationale": "No new executable synthetic structure.",
        "evidence_ids": ["synthetic-evidence"],
        "limitations": ["Engineering fixture only."],
        "requested_checks": [],
    }


def structurally_distinct_plans(authorization, count):
    base = make_plan(authorization)
    plans = []
    for threshold in (.10, .15, .20, .30):
        for buffer in (0.0, .02, .05, .10):
            for width in (4.0, 6.0, 8.0, 12.0):
                plans.append(replace(
                    base, entry_threshold=threshold, uncertainty_buffer=buffer,
                    maximum_interval_width_f=width))
                if len(plans) == count:
                    return plans
    raise AssertionError("Fixture requested more structures than its finite grid")


def passing_evidence(engine):
    count = 30
    start = date(2025, 2, 4)
    days = [(start + timedelta(days=index)).isoformat() for index in range(count)]
    candidate = {
        "partition_audit": {
            "partition_contract_sha256": engine.authorization.partition_contract_sha256,
            "weather_model_fit_source": "weather_training_through_2024_12_31",
            "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
            "calibration_prefix_scored": False,
            "scored_outcomes_used_for_fit_or_thresholds": False,
        },
        "forecast_scores": {
            "brier": 0.10, "gaussian_crps_f": 1.0,
            "probability_conservation_passed": True,
        },
        "historical_assumed_fill": {
            "capital_weighted_return": 0.12,
            "trade_count": count,
            "bootstrap": {
                "lower_95": 0.01, "resamples": 10000,
                "unit": "independent_settlement_day",
                "stratified_by_development_fold": True, "seed": 20260925,
                "one_sided_confidence": 0.95, "undefined_resamples": 0,
            },
            "selected_trade_expected_net_returns": [0.11] * count,
            "selected_event_ids": [f"event-{index}" for index in range(count)],
            "selected_settlement_days": days,
            "capital_weighted_return_after_removing_most_profitable_day": 0.08,
            "contribution_breakdowns": {
                "calendar_month": {"2025-01": 1},
                "weather_regime": {"ordinary_sea_breeze": 1},
                "entry_price_band": {"20-39": 1},
                "purchase_side": {"YES": 1},
                "decision_time": {"15:00": 1},
            },
        },
    }
    reference = {"forecast_scores": {"brier": 0.11, "gaussian_crps_f": 1.1}}
    folds = [
        {"fold": index + 1, "capital_weighted_return": value, "trade_count": 6,
         "selected_settlement_days": days[index * 6:(index + 1) * 6]}
        for index, value in enumerate((0.08, 0.12, 0.15, 0.04, -0.01))
    ]
    stress = {
        "capital_weighted_return": 0.01,
        "fee_rate": 0.10,
        "additional_adverse_price_per_contract_dollars": 0.02,
        "quantity": 1,
        "unavailable_entry_treatment": "abstain",
    }
    return candidate, reference, folds, stress


def artifacts(candidate, reference, folds, stress):
    payload = {
        "candidate": candidate, "reference": reference, "folds": folds,
        "stress_return": stress,
    }
    return {
        "compiled_manifest_sha256": "1" * 64,
        "predictions_sha256": "2" * 64,
        "ledger_sha256": "3" * 64,
        "fold_metrics_sha256": "4" * 64,
        "evaluation_sha256": canonical_hash(payload),
    }


def review_records(record):
    replication = {
        "contract_version": REPLICATION_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "verifier_id": "independent-replicator",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "source_artifact_sha256s": dict(record.artifact_sha256s),
        "checks": {key: True for key in REPLICATION_CHECKS},
        "status": "PASS",
        "differences": [],
    }
    critic = {
        "contract_version": CRITIC_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "critic_id": "independent-critic",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "evidence_sha256s": dict(record.artifact_sha256s),
        "checks": {key: True for key in CRITIC_CHECKS},
        "decision": "NONREJECT",
        "unresolved_defects": [],
    }
    return replication, critic


def admitted_engine(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    plan = make_plan(authorization)
    admitted = engine.process_worker_response(
        "discovery-worker", response(plan), packet(engine, plan))
    return engine, admitted["candidate_id"], plan


def test_real_campaign_loader_rejects_synthetic_ticket_by_default(tmp_path):
    root, readiness, ticket, _ = build_authorized_fixture(tmp_path)
    with pytest.raises(V3ReadinessRefusal, match="Synthetic"):
        load_v3_campaign_authorization(root, readiness, ticket)


def test_ticket_refuses_incomplete_or_mutated_component(tmp_path):
    root, readiness, ticket, _ = build_authorized_fixture(tmp_path)
    component = root / REQUIRED_COMPONENT_MANIFESTS[0].path
    component.write_text('{"status":"IN_PROGRESS"}', encoding="utf-8")
    with pytest.raises(V3ReadinessRefusal, match="hash differs"):
        load_v3_campaign_authorization(root, readiness, ticket, allow_synthetic=True)


def test_cross_colony_cosmetic_duplicate_consumes_no_experiment_slot(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    first = make_plan(authorization)
    accepted = DeterministicV3FixtureWorker("worker-a").propose(
        engine, packet(engine, first), first)
    routed = replace(first, colony="market_behavior", stage="economic_simulation")
    duplicate = DeterministicV3FixtureWorker("worker-b").propose(
        engine, packet(engine, routed, "task-2"), routed)
    assert accepted["status"] == "ADMITTED"
    assert duplicate["status"] == "DUPLICATE_STRUCTURE"
    assert duplicate["experiment_slot_consumed"] is False
    assert engine.admitted_candidates == 1
    assert engine.model_calls == 2


def test_authorization_denylist_rejects_exact_seed_before_charging_call(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    plan = make_plan(authorization)
    authorization = replace(
        authorization, denied_plan_sha256s=(plan.identity,))
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()

    with pytest.raises(V3IntegrityStop, match="worker seed options"):
        engine.process_worker_response(
            "discovery-worker", response(plan), packet(engine, plan))

    assert engine.model_calls == 0
    assert engine.admitted_candidates == 0
    assert engine.candidates == {}
    assert engine.stopped_reason == (
        "required_data_integrity_replication_critic_or_resource_boundary_failure")
    assert engine.snapshot()["denied_plan_sha256s"] == [plan.identity]


def test_authorization_denylist_is_canonical_and_exact(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    first, second = structurally_distinct_plans(authorization, 2)
    denied = tuple(sorted((first.identity, second.identity)))
    checked = replace(authorization, denied_plan_sha256s=denied)
    assert checked.denied_plan_sha256s == denied

    with pytest.raises(V3CampaignError, match="sorted and unique"):
        replace(authorization, denied_plan_sha256s=(denied[1], denied[0]))
    with pytest.raises(V3CampaignError, match="Invalid"):
        replace(authorization, denied_plan_sha256s=("not-a-sha",))


def test_admission_backstop_rejects_denied_plan_even_after_packet_precheck(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    plan = make_plan(authorization)
    authorization = replace(
        authorization, denied_plan_sha256s=(plan.identity,))
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    proposal = type("Proposal", (), {"plan": plan, "task_id": "task-1"})()

    with pytest.raises(V3IntegrityStop, match="candidate admission"):
        engine._admit_plan(proposal, "discovery-worker")

    assert engine.admitted_candidates == 0
    assert engine.candidates == {}


def test_one_inference_process_and_model_calls_are_deterministically_charged(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    with engine.model_call("worker-a"):
        with pytest.raises(V3CampaignError, match="exactly one"):
            with engine.model_call("worker-b"):
                pass
    assert engine.model_calls == 1


def test_one_use_ticket_cannot_start_a_second_engine(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    V3CampaignEngine(authorization)
    with pytest.raises(V3ReadinessRefusal, match="already been claimed"):
        V3CampaignEngine(authorization)


def test_two_empty_epochs_stop_without_searching_for_a_positive_result(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    assert engine.finish_epoch()["stopped_reason"] is None
    engine.begin_epoch()
    assert engine.finish_epoch()["stopped_reason"] == "two_consecutive_empty_epochs"
    with pytest.raises(V3BudgetStop, match="stopped"):
        engine.begin_epoch()


def test_per_epoch_candidate_ceiling_is_exact_and_duplicates_do_not_hide_it(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    plans = structurally_distinct_plans(authorization, 11)
    for index, plan in enumerate(plans[:10], 1):
        task_id = f"task-{index}"
        admitted = engine.process_worker_response(
            f"worker-{index}", response(plan, task_id),
            packet(engine, plan, task_id))
        assert admitted["status"] == "ADMITTED"
    plan = plans[10]
    with pytest.raises(V3BudgetStop, match="per-epoch"):
        engine.process_worker_response(
            "worker-11", response(plan, "task-11"),
            packet(engine, plan, "task-11"))
    assert engine.admitted_candidates == 10
    assert engine.model_calls == 11


def test_model_call_ceiling_stops_at_exact_registered_count(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    plan = make_plan(authorization)
    for index in range(engine.budget.maximum_local_model_calls):
        task_id = f"abstain-{index}"
        result = engine.process_worker_response(
            f"worker-{index}", abstain_response(task_id),
            packet(engine, plan, task_id))
        assert result["status"] == "NO_EXECUTABLE_PLAN"
    with pytest.raises(V3BudgetStop, match="model-call"):
        engine.process_worker_response(
            "worker-over", abstain_response("abstain-over"),
            packet(engine, plan, "abstain-over"))
    assert engine.model_calls == 180
    assert engine.model_context_tokens_reserved == 2_949_120
    assert engine.budget_remaining()["reserved_context_tokens_remaining"] == 0
    assert engine.stopped_reason == "model_call_budget_exhausted"


def test_transient_retries_are_charged_and_stop_after_two(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    assert engine.register_transient_failure("task-a") == 1
    assert engine.register_transient_failure("task-a") == 2
    with pytest.raises(V3IntegrityStop, match="retry ceiling"):
        engine.register_transient_failure("task-a")
    assert engine.transient_retries == {"task-a": 2}
    assert engine.stopped_reason == (
        "required_data_integrity_replication_critic_or_resource_boundary_failure")


def test_sixth_productive_epoch_stops_at_registered_epoch_ceiling(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    for index, plan in enumerate(structurally_distinct_plans(authorization, 6), 1):
        engine.begin_epoch()
        task_id = f"epoch-{index}"
        engine.process_worker_response(
            f"worker-{index}", response(plan, task_id),
            packet(engine, plan, task_id))
        result = engine.finish_epoch()
    assert result["stopped_reason"] == "epoch_budget_exhausted"
    assert engine.current_epoch == 6


def test_wall_clock_budget_fails_closed(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    now = [100.0]
    engine = V3CampaignEngine(authorization, clock=lambda: now[0])
    now[0] += engine.budget.maximum_wall_seconds
    with pytest.raises(V3BudgetStop, match="wall-time"):
        engine.begin_epoch()
    assert engine.stopped_reason == "wall_time_budget_exhausted"


def test_packet_binding_failure_is_an_integrity_stop(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    engine = V3CampaignEngine(authorization)
    engine.begin_epoch()
    plan = make_plan(authorization)
    bad_packet = packet(engine, plan)
    bad_packet["readiness_sha256"] = "0" * 64
    with pytest.raises(V3IntegrityStop, match="differs"):
        engine.process_worker_response("worker-a", response(plan), bad_packet)
    assert engine.stopped_reason == (
        "required_data_integrity_replication_critic_or_resource_boundary_failure")


def test_calibration_prefix_can_fit_market_layers_but_cannot_enter_scored_returns(tmp_path):
    engine, candidate_id, _ = admitted_engine(tmp_path)
    candidate, reference, folds, stress = passing_evidence(engine)
    candidate["historical_assumed_fill"]["selected_settlement_days"][0] = "2025-01-05"
    folds[0]["selected_settlement_days"][0] = "2025-01-05"
    hashes = artifacts(candidate, reference, folds, stress)
    with pytest.raises(V3IntegrityStop, match="Calibration-prefix"):
        engine.record_candidate_evaluation(
            candidate_id, candidate=candidate, reference=reference, folds=folds,
            stress_return=stress, artifact_sha256s=hashes)
    assert engine.stopped_reason == (
        "required_data_integrity_replication_critic_or_resource_boundary_failure")


def test_fixed_candidate_evaluator_interface_is_offline_and_partition_bound(tmp_path):
    engine, candidate_id, plan = admitted_engine(tmp_path)
    candidate, reference, folds, stress = passing_evidence(engine)
    hashes = artifacts(candidate, reference, folds, stress)

    class LocalFixedEvaluator:
        def evaluate(self, *, plan, execution_manifest, context):
            assert plan.identity == execution_manifest["research_plan_sha256"]
            assert context.scope == "synthetic_only"
            assert context.network_permitted is False
            assert context.protected_final_permitted is False
            assert context.partition_contract_sha256 == (
                engine.authorization.partition_contract_sha256)
            return CandidateEvaluationBundle(
                candidate, reference, folds, stress, hashes)

    evaluation_hash = engine.execute_candidate(candidate_id, LocalFixedEvaluator())
    assert evaluation_hash == hashes["evaluation_sha256"]
    assert engine.candidates[candidate_id].plan == plan


def test_independent_replication_and_critic_are_required_and_identity_bound(tmp_path):
    engine, candidate_id, _ = admitted_engine(tmp_path)
    candidate, reference, folds, stress = passing_evidence(engine)
    hashes = artifacts(candidate, reference, folds, stress)
    engine.record_candidate_evaluation(
        candidate_id, candidate=candidate, reference=reference, folds=folds,
        stress_return=stress, artifact_sha256s=hashes)
    record = engine.candidates[candidate_id]
    replication, critic = review_records(record)
    replication["verifier_id"] = record.discovery_worker_id
    with pytest.raises(V3IntegrityStop, match="cannot independently replicate"):
        engine.review_candidate(candidate_id, replication, critic)
    assert engine.champion_candidate_id is None


def test_every_gate_plus_independent_reviews_can_nominate_one_synthetic_champion(tmp_path):
    engine, candidate_id, _ = admitted_engine(tmp_path)
    candidate, reference, folds, stress = passing_evidence(engine)
    hashes = artifacts(candidate, reference, folds, stress)
    engine.record_candidate_evaluation(
        candidate_id, candidate=candidate, reference=reference, folds=folds,
        stress_return=stress, artifact_sha256s=hashes)
    replication, critic = review_records(engine.candidates[candidate_id])
    gate = engine.review_candidate(candidate_id, replication, critic)
    assert gate["passed"] is True
    assert engine.champion_candidate_id == candidate_id
    assert engine.stopped_reason == "candidate_passes_every_development_promotion_gate"
    with pytest.raises(ProtectedFinalRefusal, match="Synthetic"):
        engine.authorize_protected_final()
    assert engine.snapshot()["protected_final_sealed"] is True
    assert engine.snapshot()["profitability_claimed"] is False


def test_missing_registered_promotion_detail_rejects_candidate(tmp_path):
    engine, candidate_id, _ = admitted_engine(tmp_path)
    candidate, reference, folds, stress = passing_evidence(engine)
    del candidate["historical_assumed_fill"]["selected_trade_expected_net_returns"]
    hashes = artifacts(candidate, reference, folds, stress)
    engine.record_candidate_evaluation(
        candidate_id, candidate=candidate, reference=reference, folds=folds,
        stress_return=stress, artifact_sha256s=hashes)
    replication, critic = review_records(engine.candidates[candidate_id])
    gate = engine.review_candidate(candidate_id, replication, critic)
    assert gate["passed"] is False
    assert "selected_trade_expected_return_gate_failed" in gate["reasons"]
    assert engine.champion_candidate_id is None
    with pytest.raises(ProtectedFinalRefusal, match="without a development champion"):
        engine.authorize_protected_final()


def test_development_artifact_guard_rejects_protected_paths(tmp_path):
    _, _, _, authorization = build_authorized_fixture(tmp_path)
    with pytest.raises(ProtectedFinalRefusal, match="protected-final"):
        authorization.assert_development_path("data/protected_final/labels.parquet")


def test_snapshot_is_synthetic_and_contains_no_profit_claim(tmp_path):
    engine, _, _ = admitted_engine(tmp_path)
    snapshot = engine.save_snapshot(
        engine.authorization.root / "runs/fixtures/campaign-state.json")
    assert snapshot["synthetic"] is True
    assert snapshot["actual_orders_placed"] is False
    assert snapshot["profitability_claimed"] is False
    assert snapshot["protected_final_sealed"] is True
