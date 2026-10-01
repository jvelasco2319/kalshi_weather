from __future__ import annotations

import json
from pathlib import Path

import pytest

import klax_lab.orchestrator_v3 as orchestrator_module
from klax_lab.campaign_v3 import (
    V3CampaignAuthorization,
    V3CampaignBudget,
    V3CampaignEngine,
    V3IntegrityStop,
    V3ReadinessRefusal,
)
from klax_lab.continuation_v3 import (
    DIAGNOSTIC_PLAN_SHA256,
    build_continuation_import_manifest,
)
from klax_lab.orchestrator_v3 import BoundedV3Orchestrator
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.research_plan_v3 import ResearchPlanV3


DIAGNOSTIC_PLAN = {
    "abstention_operator": "fixed_uncertainty_buffer",
    "allowed_sides": "BOTH",
    "calibration_operator": "isotonic_bracket",
    "colony": "local_weather",
    "contract_mapping": "kalshi_interval_bounds_v1",
    "data_bundle_sha256":
        "c24f483a93ae8766edc51047b8f3dbf7c9c3d5de8ca1b4781f2b2549bbf41824",
    "data_bundle_version":
        "872161299f101a830f60cd0e546732b20d7e490b18c140bb17eb9fca5912ec0c",
    "decision_time_utc": "12:00",
    "entry_price_ceiling_cents": 90,
    "entry_price_floor_cents": 15,
    "entry_threshold": 0.1,
    "feature_set": "temperature_only",
    "fill_rule": "observed_ask_or_conservative_proxy",
    "forecast_source_set": "hrrr_gefs_summary",
    "lineage_operator": "seed",
    "market_residual_model": "none",
    "market_snapshot_rule":
        "last_completed_one_minute_candle_at_or_before_decision",
    "maximum_interval_width_f": 4.0,
    "maximum_positions_per_event": 1,
    "maximum_price_age_minutes": 1,
    "maximum_spread_cents": 25,
    "minimum_candle_volume": 0,
    "minimum_regime_training_days": 30,
    "parent_hypothesis_ids": [],
    "parent_plan_sha256s": [],
    "plan_version": "klax-research-plan-v3",
    "probability_family": "quantile_brackets",
    "regime_model": "pooled",
    "settlement_source": "nws_clilax_final",
    "settlement_target": "daily_max_integer_f",
    "settlement_timezone": "America/Los_Angeles",
    "stage": "forecast_skill",
    "uncertainty_buffer": 0.0,
}


def _inventory(source: Path) -> list[dict]:
    return [{
        "path": path.relative_to(source).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in sorted(source.rglob("*"))
        if path.is_file() and path.name != "campaign-artifacts.json"]


def _publish(source: Path) -> None:
    body = {
        "manifest_version": "klax-v3-campaign-artifacts-v1",
        "campaign_id": source.name,
        "report_version": "klax-v3-campaign-report-v2",
        "artifacts": _inventory(source),
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    write_json(source / "campaign-artifacts.json", {
        **body, "manifest_sha256": canonical_hash(body),
    })


def _candidate(plan: ResearchPlanV3) -> dict:
    candidate_id = "v3-candidate-" + plan.identity[:20]
    return {
        "candidate_id": candidate_id,
        "plan": plan.to_dict(),
        "discovery_worker_id": "explorer-local_weather-e01",
        "epoch": 1,
        "artifact_sha256s": {
            "compiled_manifest_sha256": "1" * 64,
            "predictions_sha256": "2" * 64,
            "ledger_sha256": "3" * 64,
            "fold_metrics_sha256": "4" * 64,
            "evaluation_sha256": "5" * 64,
        },
        "evaluation": {"candidate": {"historical_assumed_fill": {}}},
        "promotion": {"passed": False, "reasons": ["return_gate_failed"]},
        "replication": {"status": "PASS"},
        "critic": {"decision": "NONREJECT"},
    }


def _source_campaign(root: Path) -> Path:
    source = root / "runs/campaigns_v3/source-campaign"
    source.mkdir(parents=True)
    diagnostic = ResearchPlanV3.from_dict(DIAGNOSTIC_PLAN)
    assert diagnostic.identity == DIAGNOSTIC_PLAN_SHA256
    eligible = ResearchPlanV3.from_dict({
        **DIAGNOSTIC_PLAN, "entry_threshold": 0.15,
    })
    records = [_candidate(diagnostic), _candidate(eligible)]
    candidates = {row["candidate_id"]: row for row in records}
    novelty = {
        ResearchPlanV3.from_dict(row["plan"]).novelty_fingerprint:
            row["candidate_id"]
        for row in records
    }
    queue = [{
        "task_id": f"e02-deepen_suppo-{index:02d}",
        "category": "deepen_supported",
        "role": "synthesizer",
        "plan": eligible.to_dict(),
        "options": [eligible.to_dict()],
        "status": "PENDING",
        "candidate_id": None,
    } for index in (1, 2)]
    weights = {
        "deepen_supported": 0.5,
        "cross_colony_combinations": 0.2,
        "independent_alternatives": 0.15,
        "adversarial_replication": 0.15,
    }
    allocation = {
        "epoch": 2,
        "weights": weights,
        "requested_counts": {"deepen_supported": 1},
        "selected_counts": {"deepen_supported": 2},
        "candidate_plan_sha256s": [eligible.identity, diagnostic.identity],
        "source_candidate_ids": sorted(candidates),
    }
    allocation["decision_sha256"] = canonical_hash(allocation)
    coverage = {
        "colonies": {},
        "epochs": {
            "1": {
                "planned": 2, "reviewed": 2, "admitted": 2,
                "allocation": "initial_independent",
            },
            "2": {
                "planned": 2, "reviewed": 0, "admitted": 0,
                "allocation": weights,
            },
        },
        "allocation_weights": weights,
        "allocation_decisions": [allocation],
        "duplicate_proposals": 0,
        "nonproposal_responses": 0,
        "integrity_failures": [],
    }
    engine = {
        "elapsed_seconds": 167,
        "current_epoch": 2,
        "model_calls": 3,
        "model_context_tokens_reserved": 3 * 16_384,
        "admitted_candidates": 2,
        "executed_candidates": 2,
        "empty_epochs": 0,
        "stopped_reason":
            "required_data_integrity_replication_critic_or_resource_boundary_failure",
        "champion_candidate_id": None,
        "final_authorization": None,
        "novelty_index": novelty,
        "duplicate_proposals": [],
        "nonproposal_responses": [],
        "epoch_admissions": {"1": 2, "2": 0},
        "transient_retries": {},
        "candidates": candidates,
    }
    recovery_body = {
        "recovery_version": "klax-v3-campaign-recovery-v2",
        "orchestrator_version": "klax-v3-bounded-orchestrator-v1",
        "campaign_id": source.name,
        "readiness_sha256": "a" * 64,
        "ticket_sha256": "b" * 64,
        "phase": "COMPLETE",
        "started_at_utc": "2026-09-26T16:29:51+00:00",
        "completed_at_utc": "2026-09-26T16:32:38+00:00",
        "queue": queue,
        "next_queue_index": 0,
        "coverage": coverage,
        "engine": engine,
        "protected_final_evaluated": False,
        "actual_orders_placed": False,
    }
    write_json(source / "recovery-state.json", {
        **recovery_body, "state_sha256": canonical_hash(recovery_body),
    })
    register = [{"candidate_id": row["candidate_id"]} for row in records]
    write_json(source / "candidate-register.json", register)
    write_json(source / "search-coverage.json", coverage)
    write_json(source / "summary.json", {
        "status": "OFFLINE_CAMPAIGN_COMPLETE",
        "campaign_id": source.name,
        "report_version": "klax-v3-campaign-report-v2",
        "started_at_utc": recovery_body["started_at_utc"],
        "completed_at_utc": recovery_body["completed_at_utc"],
        "stopped_reason": engine["stopped_reason"],
        "protected_final_evaluated": False,
        "protected_final_authorization_issued": False,
        "actual_orders_placed": False,
        "ranked_candidates": register,
        "coverage": coverage,
        "budget_used": {
            "local_model_calls": 3,
            "reserved_context_tokens": 3 * 16_384,
            "admitted_candidates": 2,
            "executed_candidates": 2,
            "epochs": 2,
        },
    })
    (source / "report.md").write_text("terminal source\n", encoding="utf-8")
    for index in (1, 2):
        write_json(
            source / "tasks" / f"e01-initial_inde-{index:02d}" / "response.json",
            {"action": "propose"},
        )
    _publish(source)
    return source


def _fixture(tmp_path: Path):
    root = tmp_path / "project"
    source = _source_campaign(root)
    readiness = root / "data/manifests/successor-readiness.json"
    write_json(readiness, {
        "ready_for_v3_campaign": True,
        "v3_campaign_authorized": True,
        "protected_final_access_authorized": False,
        "holdout_access_denied": True,
        "historical_only": True,
    })
    ticket = root / "runs/successor-ticket.json"
    write_json(ticket, {
        "campaign_id": "successor-campaign",
        "status": "ACTIVE",
        "synthetic": False,
        "one_use": True,
        "readiness_path": readiness.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(readiness),
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 1,
    })
    manifest = build_continuation_import_manifest(
        root=root,
        source_campaign=source,
        successor_campaign_id="successor-campaign",
        successor_readiness_path=readiness,
        successor_ticket_path=ticket,
    )
    output = root / "runs/campaigns_v3/successor-campaign"
    write_json(output / orchestrator_module.CONTINUATION_IMPORT_FILENAME, manifest)
    layer_a = root / "data/manifests/source-import-state.json"
    write_json(layer_a, {"fixture": True})
    budget = V3CampaignBudget(
        maximum_epochs=3,
        maximum_distinct_executed_candidates=6,
        maximum_new_candidates_per_epoch=2,
        maximum_local_model_calls=10,
        local_reserved_context_tokens=10 * 16_384,
        maximum_local_inference_concurrency=1,
        maximum_wall_seconds=3_600,
        maximum_transient_retries_per_task=2,
        empty_epoch_patience=2,
        maximum_paid_api_dollars=0,
    )
    auth = V3CampaignAuthorization(
        root=root,
        campaign_id="successor-campaign",
        readiness_path=readiness,
        ticket_path=ticket,
        readiness_sha256=sha256_file(readiness),
        ticket_sha256=sha256_file(ticket),
        config_sha256="a" * 64,
        schema_sha256="b" * 64,
        data_bundle_version=DIAGNOSTIC_PLAN["data_bundle_version"],
        data_bundle_sha256=DIAGNOSTIC_PLAN["data_bundle_sha256"],
        code_sha256="c" * 64,
        evaluation_policy_sha256="d" * 64,
        promotion_gates_sha256="e" * 64,
        campaign_budget_sha256="f" * 64,
        partition_contract_sha256="1" * 64,
        partition_contract={},
        champion_ranking_rule="fixture ranking rule",
        champion_ranking_rule_sha256="2" * 64,
        budget=budget,
        protected_final_roots=(root / "data/protected_final",),
        synthetic=False,
        denied_plan_sha256s=(DIAGNOSTIC_PLAN_SHA256,),
        continuation_import_state_path=layer_a,
        continuation_import_state_sha256=sha256_file(layer_a),
        source_campaign_id=source.name,
    )
    write_json(ticket.with_name(ticket.name + ".claimed.json"), {
        "campaign_id": auth.campaign_id,
        "claim_version": "klax-v3-ticket-claim-v1",
        "readiness_sha256": auth.readiness_sha256,
        "synthetic": False,
        "ticket_sha256": auth.ticket_sha256,
    })
    orchestrator_module._write_initial_continuation_recovery(
        auth, output, manifest)
    engine = V3CampaignEngine(auth, claim_ticket=False)
    orchestrator = BoundedV3Orchestrator.recover(
        engine=engine,
        primary_evaluator=None,
        replication_evaluator=None,
        output_root=output,
    )
    orchestrator_module._verify_continuation_recovery_floor(
        orchestrator, manifest)
    return auth, output, manifest, orchestrator


def test_clean_import_rebuilds_epoch_two_without_refunding_budget(tmp_path):
    _auth, output, manifest, orchestrator = _fixture(tmp_path)
    assert orchestrator.phase == "REBUILD_EPOCH_ALLOCATION"
    assert orchestrator.engine.current_epoch == 2
    assert orchestrator.engine.model_calls == 3
    assert orchestrator.engine.model_context_tokens_reserved == 3 * 16_384

    orchestrator._rebuild_continuation_epoch()

    assert orchestrator.phase == "EXECUTING_EPOCH"
    assert orchestrator.engine.current_epoch == 2
    assert orchestrator.engine.model_calls == 3
    assert orchestrator.engine.model_context_tokens_reserved == 3 * 16_384
    assert orchestrator.queue
    assert orchestrator.next_queue_index == 0
    assert (output / "epoch-02-rebuild.json").is_file()
    journal = json.loads((
        output / "epoch-02-rebuild.json").read_text(encoding="utf-8"))
    assert journal["continuation_manifest_sha256"] == manifest["manifest_sha256"]
    assert len(journal["packet_preflight"]) == len(orchestrator.queue)
    assert all(row["seed_count_after_preflight"] >= 1
               for row in journal["packet_preflight"])
    assert all(
        DIAGNOSTIC_PLAN_SHA256
        not in ResearchPlanV3.from_dict(option).parent_plan_sha256s
        and ResearchPlanV3.from_dict(option).identity
        != DIAGNOSTIC_PLAN_SHA256
        for item in orchestrator.queue for option in item["options"]
    )
    orchestrator_module._verify_continuation_recovery_floor(
        orchestrator, manifest)


def test_continuation_finalization_refuses_missing_executable_manifest(tmp_path):
    _auth, output, _manifest, orchestrator = _fixture(tmp_path)
    (output / orchestrator_module.CONTINUATION_IMPORT_FILENAME).unlink()
    with pytest.raises(V3IntegrityStop, match="manifest is missing"):
        orchestrator._finalize()
    assert not (output / "protected-final-authorization.json").exists()


@pytest.mark.parametrize("phase", ["BETWEEN_EPOCHS", "COMPLETE"])
def test_post_rebuild_phase_without_journal_is_refused(tmp_path, phase):
    _auth, _output, manifest, orchestrator = _fixture(tmp_path)
    orchestrator.phase = phase
    with pytest.raises(
            V3ReadinessRefusal,
            match="recovery violates its import floor") as caught:
        orchestrator_module._verify_continuation_recovery_floor(
            orchestrator, manifest)
    assert "lacks its journal" in str(caught.value.__cause__)


def test_campaign_execution_lease_excludes_second_holder(tmp_path):
    output = tmp_path / "project/runs/campaigns_v3/successor-campaign"
    with orchestrator_module._CampaignExecutionLease(output):
        with pytest.raises(
                V3ReadinessRefusal,
                match="Another process already holds"):
            with orchestrator_module._CampaignExecutionLease(output):
                pytest.fail("second campaign lease unexpectedly acquired")
