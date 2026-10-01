from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import hashlib
from itertools import islice
import json
from pathlib import Path
import shutil

import pytest

import klax_lab.orchestrator_v3 as orchestrator_module

from klax_lab.campaign_v3 import (
    CandidateEvaluationBundle, CandidateRecord, V3CampaignAuthorization,
    V3CampaignBudget, V3CampaignEngine, V3IntegrityStop, V3ReadinessRefusal,
)
from klax_lab.orchestrator_v3 import (
    ALLOCATION_WEIGHTS, BoundedV3Orchestrator, OfflineLocalLLMWorkerV3,
    _finite_plan_stream, _first_novel_plan, _mutated_parent_plan, _packet,
    _with_lineage, allocation_counts,
    run_v3_campaign,
    verify_v3_campaign_artifacts,
)
from klax_lab.local_backend import WorkerLimits, build_any_prompt
from klax_lab.provenance import canonical_hash, sha256_file
from klax_lab.research_plan_v3 import ResearchPlanV3
from klax_lab.research_protocol_v3 import (
    V3ResearchProtocolError, validate_v3_worker_packet,
)


PROJECT = Path(__file__).resolve().parents[1]


def _canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _write(path: Path, value) -> str:
    raw = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _authorization(tmp_path: Path) -> V3CampaignAuthorization:
    root = tmp_path / "p"
    (root / "configs").mkdir(parents=True)
    shutil.copy2(PROJECT / "configs/v3_goal.json", root / "configs/v3_goal.json")
    goal = json.loads((root / "configs/v3_goal.json").read_text(encoding="utf-8"))
    ticket = root / "runs/v3-ticket.json"
    ticket.parent.mkdir(parents=True)
    ticket.write_text("{}", encoding="utf-8")
    readiness = root / "data/manifests/v3-readiness.json"
    readiness.parent.mkdir(parents=True)
    readiness.write_text("{}", encoding="utf-8")
    budget = V3CampaignBudget(
        maximum_epochs=1,
        maximum_distinct_executed_candidates=6,
        maximum_new_candidates_per_epoch=10,
        maximum_local_model_calls=10,
        local_reserved_context_tokens=163840,
        maximum_local_inference_concurrency=1,
        maximum_wall_seconds=3600,
        maximum_transient_retries_per_task=2,
        empty_epoch_patience=2,
        maximum_paid_api_dollars=0,
    )
    return V3CampaignAuthorization(
        root=root,
        campaign_id="c",
        readiness_path=readiness,
        ticket_path=ticket,
        readiness_sha256="a" * 64,
        ticket_sha256="b" * 64,
        config_sha256=sha256_file(root / "configs/v3_goal.json"),
        schema_sha256="c" * 64,
        data_bundle_version="d" * 64,
        data_bundle_sha256="e" * 64,
        code_sha256="f" * 64,
        evaluation_policy_sha256="1" * 64,
        promotion_gates_sha256=canonical_hash(goal["development_promotion_gates"]),
        campaign_budget_sha256=canonical_hash(asdict_for_test(budget)),
        partition_contract_sha256=canonical_hash(goal["partitions"]),
        partition_contract=dict(goal["partitions"]),
        champion_ranking_rule=(
            "first_candidate_in_deterministic_execution_order_to_pass_every_registered_gate"),
        champion_ranking_rule_sha256="2" * 64,
        budget=budget,
        protected_final_roots=(root / "data/protected_final",),
        synthetic=False,
    )


def asdict_for_test(budget: V3CampaignBudget) -> dict:
    return {name: getattr(budget, name) for name in budget.__dataclass_fields__}


class RejectingArtifactEvaluator:
    """Writes complete deterministic artifacts whose evidence fails promotion."""

    def __init__(self, artifact_root: Path):
        self.artifact_root = Path(artifact_root)
        self.inputs = object()
        self.fee_scenario = object()

    def evaluate(self, *, plan, execution_manifest, context):
        audit = {
            "partition_contract_sha256": context.partition_contract_sha256,
            "weather_model_fit_source": "weather_training_through_2024_12_31",
            "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
            "calibration_prefix_scored": False,
            "scored_outcomes_used_for_fit_or_thresholds": False,
        }
        economics = {
            "capital_weighted_return": None,
            "trade_count": 0,
            "bootstrap": {
                "lower_95": None, "resamples": 10000,
                "unit": "independent_settlement_day",
                "stratified_by_development_fold": True,
                "seed": 20260925, "one_sided_confidence": 0.95,
                "undefined_resamples": 10000,
            },
            "selected_trade_expected_net_returns": [],
            "selected_event_ids": [],
            "selected_settlement_days": [],
            "capital_weighted_return_after_removing_most_profitable_day": None,
            "contribution_breakdowns": {
                "calendar_month": {}, "weather_regime": {},
                "entry_price_band": {}, "purchase_side": {}, "decision_time": {},
            },
            "assumed_fill": True,
            "actual_orders_placed": False,
            "actual_account_gains_measured": False,
        }
        candidate = {
            "research_plan_sha256": plan.identity,
            "dataset_id": context.data_bundle_version,
            "partition_audit": audit,
            "forecast_scores": {
                "brier": 0.30, "crps_f": 2.0,
                "probability_conservation_passed": True,
            },
            "historical_assumed_fill": economics,
        }
        reference = {"forecast_scores": {"brier": 0.20, "crps_f": 1.5}}
        folds = [{
            "fold": index, "capital_weighted_return": None,
            "trade_count": 0, "selected_settlement_days": [],
        } for index in range(1, 6)]
        stress = {
            "capital_weighted_return": None,
            "fee_rate": 0.10,
            "additional_adverse_price_per_contract_dollars": 0.02,
            "quantity": 1,
            "unavailable_entry_treatment": "abstain",
        }
        payload = {
            "candidate": candidate, "reference": reference,
            "folds": folds, "stress_return": stress,
        }
        directory = self.artifact_root / plan.identity
        hashes = {
            "compiled_manifest_sha256": _write(
                directory / "compiled_manifest.json", execution_manifest),
            "predictions_sha256": _write(directory / "predictions.json", {
                "version": "test", "dataset_id": context.data_bundle_version,
                "research_plan_sha256": plan.identity, "rows": [],
                "contains_scored_labels": False,
            }),
            "ledger_sha256": _write(directory / "ledger.json", {
                "version": "test", "dataset_id": context.data_bundle_version,
                "research_plan_sha256": plan.identity, "decisions": [],
                "settlements": [], "historical_assumed_fill_only": True,
                "actual_orders_placed": False,
            }),
            "fold_metrics_sha256": _write(directory / "fold_metrics.json", folds),
            "evaluation_sha256": _write(directory / "evaluation.json", payload),
        }
        return CandidateEvaluationBundle(candidate, reference, folds, stress, hashes)


def test_registered_allocation_rounding_and_real_source_modes_only():
    assert ALLOCATION_WEIGHTS == {
        "deepen_supported": 0.50,
        "cross_colony_combinations": 0.20,
        "independent_alternatives": 0.15,
        "adversarial_replication": 0.15,
    }
    assert allocation_counts(10) == {
        "deepen_supported": 5,
        "cross_colony_combinations": 2,
        "independent_alternatives": 2,
        "adversarial_replication": 1,
    }
    plans = list(plan for _, plan in zip(range(100), _finite_plan_stream(
        colony="local_weather", stage="forecast_skill",
        data_bundle_version="d" * 64, data_bundle_sha256="e" * 64)))
    assert plans
    assert {plan.forecast_source_set for plan in plans} <= {
        "hrrr_gefs_summary", "gefs_summary"}
    assert not any("gfs_nbm" in plan.forecast_source_set for plan in plans)


def test_cross_colony_allocation_uses_distinct_parent_colonies(tmp_path):
    authorization = _authorization(tmp_path)
    engine = V3CampaignEngine(authorization)
    plans = list(plan for _, plan in zip(range(3), _finite_plan_stream(
        colony="local_weather", stage="forecast_skill",
        data_bundle_version=authorization.data_bundle_version,
        data_bundle_sha256=authorization.data_bundle_sha256)))
    plans[2] = replace(
        plans[2], colony="market_behavior", stage="market_information")
    records = [
        CandidateRecord(
            candidate_id=f"candidate-{index}", plan=plan,
            discovery_worker_id=f"worker-{index}", epoch=1,
            evaluation={}, promotion={"passed": False},
        )
        for index, plan in enumerate(plans)
    ]
    engine.candidates = {record.candidate_id: record for record in records}
    engine.novelty_index = {
        record.plan.novelty_fingerprint: record.candidate_id for record in records}
    engine.admitted_candidates = len(records)
    engine.current_epoch = 2
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    evaluator = RejectingArtifactEvaluator(output / "unused")
    orchestrator = BoundedV3Orchestrator(
        engine, evaluator, evaluator, output)

    queue = orchestrator._allocated_queue()
    combinations = [
        ResearchPlanV3.from_dict(item["plan"])
        for item in queue if item["category"] == "cross_colony_combinations"]
    colony_by_plan = {record.plan.identity: record.plan.colony for record in records}

    assert combinations
    for plan in combinations:
        assert len(plan.parent_plan_sha256s) == 2
        assert len({colony_by_plan[parent] for parent in plan.parent_plan_sha256s}) == 2


def test_optional_local_worker_rejects_network_or_paid_runner():
    with pytest.raises(Exception, match="offline and zero-paid"):
        OfflineLocalLLMWorkerV3(
            "worker", lambda packet: packet, "a" * 64,
            network_permitted=True)
    with pytest.raises(Exception, match="offline and zero-paid"):
        OfflineLocalLLMWorkerV3(
            "worker", lambda packet: packet, "a" * 64,
            paid_api_dollars=1)


def test_campaign_refuses_to_start_without_substantive_readiness(tmp_path):
    with pytest.raises(V3ReadinessRefusal):
        run_v3_campaign(
            tmp_path, "data/manifests/v3_readiness.json",
            "runs/v3_offline_campaign_ticket.json")
    assert not (tmp_path / "runs/campaigns_v3").exists()


def test_worker_can_choose_nonfirst_finite_option(tmp_path):
    authorization = _authorization(tmp_path)
    engine = V3CampaignEngine(authorization)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    evaluator = RejectingArtifactEvaluator(output / "unused")
    orchestrator = BoundedV3Orchestrator(engine, evaluator, evaluator, output)
    engine.begin_epoch()
    item = orchestrator._initial_queue()[0]
    options = [ResearchPlanV3.from_dict(value) for value in item["options"]]
    assert len(options) == 6
    packet = _packet(
        engine, task_id=item["task_id"], role=item["role"], plans=options)
    assert "Nominate exactly one supplied whole seed plan" in packet["question"]
    assert "Evaluation evidence is intentionally unavailable" in packet["question"]
    assert "missing performance results" in packet["question"]
    assert build_any_prompt(packet)

    def choose_second(checked):
        return {
            "protocol": checked["protocol"], "task_id": checked["task_id"],
            "action": "propose", "seed_index": 1,
            "rationale": "Choose the second finite option.",
            "evidence_ids": [], "limitations": ["Synthetic test."],
            "requested_checks": [],
        }

    admitted = engine.dispatch_worker("explorer-local_weather-e01", packet, choose_second)
    assert admitted["status"] == "ADMITTED"
    assert engine.candidates[admitted["candidate_id"]].plan == options[1]


def test_later_epoch_packet_compacts_observed_12254_byte_case(tmp_path):
    authorization = _authorization(tmp_path)
    production_budget = V3CampaignBudget(
        maximum_epochs=6,
        maximum_distinct_executed_candidates=60,
        maximum_new_candidates_per_epoch=10,
        maximum_local_model_calls=180,
        local_reserved_context_tokens=2_949_120,
        maximum_local_inference_concurrency=1,
        maximum_wall_seconds=28_800,
        maximum_transient_retries_per_task=2,
        empty_epoch_patience=2,
        maximum_paid_api_dollars=0,
    )
    authorization = replace(
        authorization, campaign_id="campaign10", budget=production_budget)
    engine = V3CampaignEngine(authorization)
    parent_plan = next(_finite_plan_stream(
        colony="local_weather", stage="forecast_skill",
        data_bundle_version=authorization.data_bundle_version,
        data_bundle_sha256=authorization.data_bundle_sha256))
    evaluation = {
        "candidate": {
            "historical_assumed_fill": {
                "capital_weighted_return": None,
                "trade_count": 0,
                "bootstrap": {"lower_95": None},
                "contribution_breakdowns": {
                    "calendar_month": {}, "decision_time": {},
                    "entry_price_band": {}, "purchase_side": {},
                    "weather_regime": {},
                },
                "total_net_profit": "0", "total_entry_outlay": "0",
                "mean_trade_return": None,
                "capital_weighted_return_after_removing_most_profitable_day": None,
            },
            "forecast_scores": {
                "brier": 0.8226837628273114,
                "crps_f": 0.6594683883625514,
                "probability_conservation_passed": True,
            },
        },
        "reference": {"forecast_scores": {
            "brier": 1.1019490232365527, "crps_f": 0.9833535954859873,
        }},
        "folds": [{
            "fold": index, "capital_weighted_return": None,
            "mean_trade_return": None, "total_entry_outlay": "0",
            "total_net_profit": "0", "trade_count": 0,
        } for index in range(1, 6)],
        "stress_return": {"capital_weighted_return": None},
    }
    parent = CandidateRecord(
        candidate_id="v3-candidate-20c5e1b7c7274bc24e80",
        plan=parent_plan,
        discovery_worker_id="explorer-local_weather-e01",
        epoch=1,
        evaluation=evaluation,
        artifact_sha256s={"evaluation_sha256": "4" * 64},
        promotion={"passed": False},
        replication={"status": "PASS"},
        critic={"decision": "NONREJECT"},
    )
    engine.candidates = {parent.candidate_id: parent}
    engine.novelty_index = {
        parent.plan.novelty_fingerprint: parent.candidate_id}
    engine.admitted_candidates = 1
    engine.executed_candidates = 1
    engine.current_epoch = 2
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    orchestrator = BoundedV3Orchestrator(engine, None, None, output)
    reserved: set[str] = set()
    primary = _mutated_parent_plan(engine, parent, reserved)
    assert primary is not None
    options = orchestrator._expand_options(primary, reserved)
    assert len(options) == 6

    compacted = _packet(
        engine, task_id="e02-deepen_suppo-01", role="synthesizer",
        plans=options)
    uncompacted = validate_v3_worker_packet({
        **compacted, "seed_plans": [plan.to_dict() for plan in options],
    })
    assert len(_canonical(uncompacted)) == 12_254
    assert len(_canonical(compacted)) == 9_564
    assert len(compacted["seed_plans"]) == 4
    assert compacted["seed_plans"] == uncompacted["seed_plans"][:4]
    assert len(_canonical(compacted)) <= WorkerLimits().max_packet_bytes
    assert build_any_prompt(compacted)


def test_two_parent_packet_preflights_transport_and_prompt_envelopes(tmp_path):
    authorization = _authorization(tmp_path)
    authorization = replace(
        authorization,
        campaign_id="campaign10",
        budget=V3CampaignBudget(
            maximum_epochs=6,
            maximum_distinct_executed_candidates=60,
            maximum_new_candidates_per_epoch=10,
            maximum_local_model_calls=180,
            local_reserved_context_tokens=2_949_120,
            maximum_local_inference_concurrency=1,
            maximum_wall_seconds=28_800,
            maximum_transient_retries_per_task=2,
            empty_epoch_patience=2,
            maximum_paid_api_dollars=0,
        ),
    )
    engine = V3CampaignEngine(authorization)
    first_plan = next(_finite_plan_stream(
        colony="local_weather", stage="forecast_skill",
        data_bundle_version=authorization.data_bundle_version,
        data_bundle_sha256=authorization.data_bundle_sha256))
    second_plan = replace(
        first_plan, colony="market_behavior", stage="market_information",
        entry_threshold=0.15)
    economics = {
        "capital_weighted_return": None,
        "trade_count": 0,
        "bootstrap": {"lower_95": None},
        "contribution_breakdowns": {},
        # Prompt construction escapes angle brackets after transport validation.
        # Two parent summaries make a three-seed packet fit 10 kB transport but
        # exceed the fixed 16,384-token conservative prompt envelope.
        "fee_scenario": {"name": "<" * 350},
        "total_net_profit": "0", "total_entry_outlay": "0",
        "mean_trade_return": None,
        "capital_weighted_return_after_removing_most_profitable_day": None,
    }
    evaluation = {
        "candidate": {
            "historical_assumed_fill": economics,
            "forecast_scores": {
                "brier": 0.8, "crps_f": 0.6,
                "probability_conservation_passed": True,
            },
        },
        "reference": {"forecast_scores": {"brier": 1.1, "crps_f": 0.9}},
        "folds": [], "stress_return": {},
    }
    parents = tuple(CandidateRecord(
        candidate_id=f"candidate-{index}",
        plan=plan,
        discovery_worker_id=f"worker-{index}",
        epoch=1,
        evaluation=evaluation,
        artifact_sha256s={"evaluation_sha256": str(index + 4) * 64},
        promotion={"passed": False},
        replication={"status": "PASS"},
        critic={"decision": "NONREJECT"},
    ) for index, plan in enumerate((first_plan, second_plan)))
    engine.candidates = {row.candidate_id: row for row in parents}
    engine.novelty_index = {
        row.plan.novelty_fingerprint: row.candidate_id for row in parents}
    engine.admitted_candidates = 2
    engine.executed_candidates = 2
    engine.current_epoch = 2
    bases = islice(_finite_plan_stream(
        colony="ensemble_probability", stage="probability_calibration",
        data_bundle_version=authorization.data_bundle_version,
        data_bundle_sha256=authorization.data_bundle_sha256), 6)
    options = [
        _with_lineage(plan, operator="combination", parents=parents)
        for plan in bases
    ]
    assert len(options) == 6

    compacted = _packet(
        engine, task_id="e02-cross_colony-01", role="synthesizer",
        plans=options)
    transport_only = {
        **compacted, "seed_plans": [plan.to_dict() for plan in options[:3]],
    }
    limits = WorkerLimits()
    validate_v3_worker_packet(
        transport_only, max_bytes=limits.max_packet_bytes)
    assert 9_900 < len(_canonical(transport_only)) <= limits.max_packet_bytes
    with pytest.raises(
            V3ResearchProtocolError, match="prompt exceeds context reservation"):
        build_any_prompt(transport_only, limits)

    assert len(compacted["seed_plans"]) == 2
    assert compacted["seed_plans"] == transport_only["seed_plans"][:2]
    assert len(_canonical(compacted)) <= limits.max_packet_bytes
    assert build_any_prompt(compacted, limits)


def test_queue_generation_filters_denied_plans_and_ranked_parents(tmp_path):
    authorization = _authorization(tmp_path)
    first, second = list(islice(_finite_plan_stream(
        colony="local_weather", stage="forecast_skill",
        data_bundle_version=authorization.data_bundle_version,
        data_bundle_sha256=authorization.data_bundle_sha256), 2))
    denied = first.identity
    authorization = replace(
        authorization, denied_plan_sha256s=(denied,))
    engine = V3CampaignEngine(authorization)
    reserved: set[str] = set()
    generated = _first_novel_plan(
        engine, "local_weather", "forecast_skill", reserved)
    assert generated is not None
    assert generated.identity != denied
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    orchestrator = BoundedV3Orchestrator(engine, None, None, output)
    options = orchestrator._expand_options(generated, reserved)
    assert options
    assert all(not engine.plan_is_denied(plan) for plan in options)

    allowed = replace(
        second, colony="market_behavior", stage="market_information")
    denied_record = CandidateRecord(
        "denied-candidate", first, "worker-denied", 1,
        evaluation={}, promotion={"passed": False})
    allowed_record = CandidateRecord(
        "allowed-candidate", allowed, "worker-allowed", 1,
        evaluation={}, promotion={"passed": False})
    engine.candidates = {
        denied_record.candidate_id: denied_record,
        allowed_record.candidate_id: allowed_record,
    }
    engine.novelty_index = {
        row.plan.novelty_fingerprint: row.candidate_id
        for row in engine.candidates.values()
    }
    engine.admitted_candidates = 2
    engine.current_epoch = 1
    queue = orchestrator._allocated_queue()
    assert queue
    decision = orchestrator.coverage["allocation_decisions"][-1]
    assert decision["source_candidate_ids"] == ["allowed-candidate"]
    assert all(
        denied not in option["parent_plan_sha256s"]
        and ResearchPlanV3.from_dict(option).identity != denied
        for item in queue for option in item["options"])


def test_direct_packet_size_integrity_stop_is_recorded_in_coverage(tmp_path):
    authorization = _authorization(tmp_path)
    engine = V3CampaignEngine(authorization)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    evaluator = RejectingArtifactEvaluator(output / "unused")

    def fail_on_packet_size(_packet):
        raise V3ResearchProtocolError("V3 research packet exceeds byte limit")

    def worker_factory(worker_id, _role):
        return OfflineLocalLLMWorkerV3(
            worker_id, fail_on_packet_size, "9" * 64)

    result = BoundedV3Orchestrator(
        engine, evaluator, evaluator, output,
        worker_factory=worker_factory).run()
    expected = [{
        "stage": "worker_dispatch",
        "task_id": "e01-initial_inde-01",
        "exception_type": "V3ResearchProtocolError",
        "exception_message": "V3 research packet exceeds byte limit",
    }]
    assert result["scientific_conclusion"] == "INSUFFICIENT_EVIDENCE"
    assert result["coverage"]["integrity_failures"] == expected
    assert json.loads(
        (output / "search-coverage.json").read_text(encoding="utf-8"))[
            "integrity_failures"] == expected
    assert result["protected_final_evaluated"] is False
    assert result["actual_orders_placed"] is False


def test_bounded_six_colony_campaign_reports_negative_evidence_and_recovers(
        tmp_path, monkeypatch):
    authorization = _authorization(tmp_path)
    engine = V3CampaignEngine(authorization)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    primary = RejectingArtifactEvaluator(output / "candidates/primary")
    replica = RejectingArtifactEvaluator(output / "candidates/replication")
    def fixture_verify(directory, **_kwargs):
        directory = Path(directory)
        return {
            "status": "PASS", "protected_final_read": False,
            "network_used": False,
            "artifact_sha256s": {
                key: sha256_file(directory / name) for key, name in {
                    "compiled_manifest_sha256": "compiled_manifest.json",
                    "predictions_sha256": "predictions.json",
                    "ledger_sha256": "ledger.json",
                    "fold_metrics_sha256": "fold_metrics.json",
                    "evaluation_sha256": "evaluation.json",
                }.items()},
        }
    monkeypatch.setattr(
        "klax_lab.orchestrator_v3.verify_candidate_evaluation_artifacts",
        fixture_verify)
    def fixture_recompute(**kwargs):
        artifact = Path(kwargs["output_directory"]) / "recomputed-evidence.json"
        digest = _write(artifact, {"fixture": True})
        return {
            "checks": {
                "compiled_identity": True, "selected_opportunities": True,
                "entry_outlay": True, "fees": True, "payouts": True,
                "fold_returns": True, "bootstrap_lower_bound": True,
                "crps": True, "brier": True,
                "partition_roles_and_nonoverlap": True,
            },
            "status": "PASS", "differences": [],
            "recomputation_artifact_path": artifact.as_posix(),
            "recomputation_artifact_sha256": digest,
        }
    monkeypatch.setattr(
        "klax_lab.orchestrator_v3.independently_recompute_candidate",
        fixture_recompute)
    orchestrator = BoundedV3Orchestrator(engine, primary, replica, output)
    result = orchestrator.run()

    assert result["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert datetime.fromisoformat(result["started_at_utc"]).tzinfo is not None
    assert datetime.fromisoformat(result["completed_at_utc"]).tzinfo is not None
    assert result["scientific_conclusion"] == "NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET"
    assert result["stopped_reason"] == "distinct_candidate_budget_exhausted"
    assert result["budget_used"]["executed_candidates"] == 6
    assert result["protected_final_evaluated"] is False
    assert result["protected_final_authorization_issued"] is False
    assert result["actual_orders_placed"] is False
    assert all(result["coverage"]["colonies"][name]["proposed"] == 1
               for name in result["six_registered_colonies"])
    assert all(row["promotion_passed"] is False
               for row in result["ranked_candidates"])
    assert (output / "report.md").is_file()
    assert result["execution_evidence_grade"] == {
        "minute_candles": "B_aggregated_historical_quote_evidence",
        "public_trades": "C_execution_proxy_without_depth_or_queue_position",
        "fill_interpretation": "historical_assumed_fill_only",
    }
    candidate_register = json.loads(
        (output / "candidate-register.json").read_text(encoding="utf-8"))
    assert len(candidate_register) == 6
    assert all(set(row["contribution_breakdowns"]) == {
        "calendar_month", "weather_regime", "entry_price_band",
        "purchase_side", "decision_time",
    } for row in candidate_register)
    assert all(len(row["folds"]) == 5 for row in candidate_register)
    assert all(row["promotion"]["passed"] is False for row in candidate_register)
    assert all(row["replication"]["status"] == "PASS" for row in candidate_register)
    assert all(row["critic"]["decision"] == "NONREJECT" for row in candidate_register)
    rendered = (output / "report.md").read_text(encoding="utf-8")
    for heading in ("Complete promotion evidence", "Chronological folds", "Cost stress",
                    "Concentration tables", "Independent review"):
        assert heading in rendered
    verified_campaign = verify_v3_campaign_artifacts(output)
    assert verified_campaign["status"] == "PASS"
    assert verified_campaign["campaign_id"] == authorization.campaign_id
    report_bytes = (output / "report.md").read_bytes()
    (output / "report.md").write_bytes(report_bytes + b"tampered\n")
    with pytest.raises(V3IntegrityStop, match="inventory changed"):
        verify_v3_campaign_artifacts(output)
    (output / "report.md").write_bytes(report_bytes)
    disguised = output / "tasks/extra/campaign-artifacts.json"
    disguised.parent.mkdir(parents=True, exist_ok=True)
    disguised.write_text("{}", encoding="utf-8")
    with pytest.raises(V3IntegrityStop, match="inventory changed"):
        verify_v3_campaign_artifacts(output)
    disguised.unlink()

    recovered_engine = V3CampaignEngine(authorization, claim_ticket=False)
    recovered = BoundedV3Orchestrator.recover(
        engine=recovered_engine, primary_evaluator=primary,
        replication_evaluator=replica, output_root=output)
    assert recovered.phase == "COMPLETE"
    assert recovered.engine.executed_candidates == 6
    assert recovered.engine.novelty_index == engine.novelty_index

    # A crash can occur after the terminal recovery save but before report
    # rendering. Reconstructing the same campaign may finish that transaction
    # without another proposal or candidate evaluation.
    for name in ("summary.json", "report.md", "candidate-register.json", "search-coverage.json"):
        (output / name).unlink()
    terminal = json.loads((output / "recovery-state.json").read_text(encoding="utf-8"))
    terminal["completed_at_utc"] = None
    terminal_body = {key: value for key, value in terminal.items() if key != "state_sha256"}
    terminal["state_sha256"] = canonical_hash(terminal_body)
    (output / "recovery-state.json").write_text(
        json.dumps(terminal, sort_keys=True), encoding="utf-8")
    report_recovery_engine = V3CampaignEngine(authorization, claim_ticket=False)
    report_recovery = BoundedV3Orchestrator.recover(
        engine=report_recovery_engine, primary_evaluator=primary,
        replication_evaluator=replica, output_root=output)
    rebuilt = report_recovery._finalize()
    assert rebuilt["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert (output / "summary.json").is_file()
    assert report_recovery_engine.model_calls == engine.model_calls
    assert report_recovery_engine.executed_candidates == engine.executed_candidates

    state_path = output / "recovery-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["engine"]["model_calls"] = 0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    another_engine = V3CampaignEngine(authorization, claim_ticket=False)
    with pytest.raises(V3IntegrityStop, match="modified"):
        BoundedV3Orchestrator.recover(
            engine=another_engine, primary_evaluator=primary,
            replication_evaluator=replica, output_root=output)


def test_independent_review_failure_stops_with_comprehensive_report(
        tmp_path, monkeypatch):
    authorization = _authorization(tmp_path)
    engine = V3CampaignEngine(authorization)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    primary = RejectingArtifactEvaluator(output / "candidates/primary")
    replica = RejectingArtifactEvaluator(output / "candidates/replication")

    def corrupted_review(*_args, **_kwargs):
        raise ValueError("deliberately corrupted independent evidence")

    monkeypatch.setattr(
        "klax_lab.orchestrator_v3._independent_replication", corrupted_review)
    result = BoundedV3Orchestrator(
        engine, primary, replica, output).run()
    assert result["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert result["scientific_conclusion"] == "INSUFFICIENT_EVIDENCE"
    assert result["stopped_reason"] == (
        "required_data_integrity_replication_critic_or_resource_boundary_failure")
    assert result["coverage"]["integrity_failures"] == [{
        "stage": "independent_review",
        "candidate_id": next(iter(engine.candidates)),
        "exception_type": "ValueError",
        "exception_message": "deliberately corrupted independent evidence",
    }]
    candidate = next(iter(engine.candidates.values()))
    assert result["coverage"]["colonies"][candidate.plan.colony]["proposed"] == 1
    assert result["coverage"]["epochs"]["1"]["admitted"] == 1
    report_text = (output / "report.md").read_text(encoding="utf-8")
    assert "Every plan passed through" not in report_text
    assert "missing stage evidence fails the corresponding gate" in report_text
    assert "registered later-epoch portfolio assigns" in report_text
    assert "Every admitted candidate is shown below" in report_text
    assert (output / "summary.json").is_file()


def test_campaign_dependency_failure_does_not_consume_one_use_ticket(
        tmp_path, monkeypatch):
    authorization = replace(_authorization(tmp_path), synthetic=True)
    monkeypatch.setattr(
        "klax_lab.orchestrator_v3.load_v3_campaign_authorization",
        lambda *_args, **_kwargs: authorization)

    def fail_before_claim(*_args, **_kwargs):
        raise RuntimeError("production evaluator setup failed")

    monkeypatch.setattr(
        "klax_lab.orchestrator_v3._production_evaluators", fail_before_claim)
    with pytest.raises(RuntimeError, match="evaluator setup failed"):
        run_v3_campaign(
            authorization.root, authorization.readiness_path,
            authorization.ticket_path, allow_synthetic=True)
    claim = authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".claimed.json")
    assert not claim.exists()
    assert not authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.json").exists()


def test_campaign_start_recovers_exact_claim_without_initial_recovery_state(
        tmp_path, monkeypatch):
    authorization = replace(_authorization(tmp_path), synthetic=True)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    primary = RejectingArtifactEvaluator(output / "candidates/primary")
    replica = RejectingArtifactEvaluator(output / "candidates/replication")
    monkeypatch.setattr(
        "klax_lab.orchestrator_v3.load_v3_campaign_authorization",
        lambda *_args, **_kwargs: authorization)
    monkeypatch.setattr(
        "klax_lab.orchestrator_v3._production_evaluators",
        lambda *_args, **_kwargs: (primary, replica))

    original_save = BoundedV3Orchestrator.save_recovery

    def lose_process_before_first_recovery(_self):
        raise RuntimeError("simulated loss after durable claim")

    monkeypatch.setattr(
        BoundedV3Orchestrator, "save_recovery", lose_process_before_first_recovery)
    with pytest.raises(RuntimeError, match="loss after durable claim"):
        run_v3_campaign(
            authorization.root, authorization.readiness_path,
            authorization.ticket_path, allow_synthetic=True)
    assert not (output / "recovery-state.json").exists()
    bootstrap_path = authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.json")
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    assert bootstrap["status"] == "PREPARED"
    assert authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.claimed.json").is_file()

    monkeypatch.setattr(BoundedV3Orchestrator, "save_recovery", original_save)
    result = run_v3_campaign(
        authorization.root, authorization.readiness_path,
        authorization.ticket_path, allow_synthetic=True)
    assert result["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert (output / "recovery-state.json").is_file()
    assert authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.committed.json").is_file()
    recovery_consumed = authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.recovery-consumed.json")
    assert recovery_consumed.is_file()

    # Removing the derived campaign directory and even the last terminal marker
    # cannot reset the ticket or budget: the independent consumption tombstone
    # remains authoritative.
    shutil.rmtree(output)
    authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.committed.json").unlink()
    with pytest.raises(V3ReadinessRefusal, match="already been consumed"):
        run_v3_campaign(
            authorization.root, authorization.readiness_path,
            authorization.ticket_path, allow_synthetic=True)


def test_campaign_bootstrap_recovery_transition_has_one_atomic_winner(tmp_path):
    authorization = replace(_authorization(tmp_path), synthetic=True)
    output = authorization.root / "runs/campaigns_v3" / authorization.campaign_id
    prepared = orchestrator_module._prepare_campaign_bootstrap(
        authorization, output)
    claimed = orchestrator_module._transition_campaign_bootstrap(
        authorization, output,
        orchestrator_module._read_campaign_bootstrap(
            authorization, output,
            orchestrator_module._campaign_bootstrap_path(authorization)),
        "CLAIMED")

    def compete():
        try:
            return orchestrator_module._transition_campaign_bootstrap(
                authorization, output, claimed, "RECOVERY_CONSUMED")
        except V3ReadinessRefusal as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _item: compete(), range(2)))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, V3ReadinessRefusal) for item in outcomes) == 1
    state = orchestrator_module._read_campaign_bootstrap(
        authorization, output,
        orchestrator_module._campaign_bootstrap_path(authorization))
    assert state["status"] == "RECOVERY_CONSUMED"
    assert prepared["status"] == "PREPARED"
