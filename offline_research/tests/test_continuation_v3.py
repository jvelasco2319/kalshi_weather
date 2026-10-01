from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from klax_lab.continuation_v3 import (
    DIAGNOSTIC_PLAN_SHA256, V3ContinuationError,
    build_continuation_import_manifest, verify_continuation_import_manifest,
    verify_epoch_two_rebuild, write_continuation_import_manifest,
)
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.research_plan_v3 import ResearchPlanV3


DIAGNOSTIC_PLAN = {
    "abstention_operator": "fixed_uncertainty_buffer",
    "allowed_sides": "BOTH",
    "calibration_operator": "isotonic_bracket",
    "colony": "local_weather",
    "contract_mapping": "kalshi_interval_bounds_v1",
    "data_bundle_sha256": "c24f483a93ae8766edc51047b8f3dbf7c9c3d5de8ca1b4781f2b2549bbf41824",
    "data_bundle_version": "872161299f101a830f60cd0e546732b20d7e490b18c140bb17eb9fca5912ec0c",
    "decision_time_utc": "12:00",
    "entry_price_ceiling_cents": 90,
    "entry_price_floor_cents": 15,
    "entry_threshold": 0.1,
    "feature_set": "temperature_only",
    "fill_rule": "observed_ask_or_conservative_proxy",
    "forecast_source_set": "hrrr_gefs_summary",
    "lineage_operator": "seed",
    "market_residual_model": "none",
    "market_snapshot_rule": "last_completed_one_minute_candle_at_or_before_decision",
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
        **body, "manifest_sha256": canonical_hash(body)})


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


def _fixture(tmp_path: Path):
    root = tmp_path / "project"
    campaign_id = "source-campaign"
    source = root / "runs/campaigns_v3" / campaign_id
    source.mkdir(parents=True)
    diagnostic = ResearchPlanV3.from_dict(DIAGNOSTIC_PLAN)
    assert diagnostic.identity == DIAGNOSTIC_PLAN_SHA256
    eligible = ResearchPlanV3.from_dict({**DIAGNOSTIC_PLAN, "entry_threshold": 0.15})
    records = [_candidate(diagnostic), _candidate(eligible)]
    candidates = {row["candidate_id"]: row for row in records}
    novelty = {
        ResearchPlanV3.from_dict(row["plan"]).novelty_fingerprint: row["candidate_id"]
        for row in records
    }
    queue = [{
        "task_id": f"e02-deepen_suppo-{index:02d}",
        "category": "deepen_supported", "role": "synthesizer",
        "plan": eligible.to_dict(), "options": [eligible.to_dict()],
        "status": "PENDING", "candidate_id": None,
    } for index in (1, 2)]
    allocation = {
        "epoch": 2,
        "weights": {
            "deepen_supported": 0.5, "cross_colony_combinations": 0.2,
            "independent_alternatives": 0.15, "adversarial_replication": 0.15,
        },
        "requested_counts": {"deepen_supported": 1},
        "selected_counts": {"deepen_supported": 2},
        "candidate_plan_sha256s": [eligible.identity, diagnostic.identity],
        "source_candidate_ids": sorted(candidates),
    }
    allocation["decision_sha256"] = canonical_hash(allocation)
    coverage = {
        "colonies": {},
        "epochs": {
            "1": {"planned": 2, "reviewed": 2, "admitted": 2,
                  "allocation": "initial_independent"},
            "2": {"planned": 2, "reviewed": 0, "admitted": 0,
                  "allocation": allocation["weights"]},
        },
        "allocation_weights": allocation["weights"],
        "allocation_decisions": [allocation],
        "duplicate_proposals": 0,
        "nonproposal_responses": 0,
        "integrity_failures": [],
    }
    engine = {
        "elapsed_seconds": 167,
        "current_epoch": 2,
        "model_calls": 3,
        "model_context_tokens_reserved": 3 * 16384,
        "admitted_candidates": 2,
        "executed_candidates": 2,
        "empty_epochs": 0,
        "stopped_reason": "required_data_integrity_replication_critic_or_resource_boundary_failure",
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
        "campaign_id": campaign_id,
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
        **recovery_body, "state_sha256": canonical_hash(recovery_body)})
    register = [{"candidate_id": row["candidate_id"]} for row in records]
    write_json(source / "candidate-register.json", register)
    write_json(source / "search-coverage.json", coverage)
    summary = {
        "status": "OFFLINE_CAMPAIGN_COMPLETE",
        "campaign_id": campaign_id,
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
            "local_model_calls": 3, "reserved_context_tokens": 3 * 16384,
            "admitted_candidates": 2, "executed_candidates": 2, "epochs": 2,
        },
    }
    write_json(source / "summary.json", summary)
    (source / "report.md").write_text("terminal source\n", encoding="utf-8")
    for index in (1, 2):
        task = source / "tasks" / f"e01-initial_inde-{index:02d}"
        write_json(task / "response.json", {"action": "propose"})
    _publish(source)

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
        "status": "ACTIVE", "one_use": True,
        "readiness_path": readiness.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(readiness),
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 1,
    })
    return root, source, readiness, ticket


def _build(root: Path, source: Path, readiness: Path, ticket: Path):
    return build_continuation_import_manifest(
        root=root, source_campaign=source,
        successor_campaign_id="successor-campaign",
        successor_readiness_path=readiness,
        successor_ticket_path=ticket,
    )


def test_continuation_preserves_spent_state_and_excludes_only_diagnostic_parent(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    manifest = _build(root, source, readiness, ticket)
    continuation = manifest["continuation"]
    preserved = continuation["preserved_source_state"]
    imported = continuation["successor_import_state"]
    rebuild = continuation["epoch_two_rebuild"]

    assert preserved["engine"]["model_calls"] == 3
    assert preserved["engine"]["model_context_tokens_reserved"] == 3 * 16384
    assert preserved["engine"]["candidates"] == imported["engine"]["candidates"]
    assert preserved["coverage"] == imported["coverage"]
    assert imported["engine"]["stopped_reason"] is None
    assert imported["completed_at_utc"] is None
    assert imported["phase"] == "REBUILD_EPOCH_ALLOCATION"
    assert imported["queue"] == []
    assert imported["engine"]["current_epoch"] == 2
    assert rebuild["begin_new_epoch_permitted"] is False
    assert rebuild["source_queue_reused"] is False
    assert rebuild["source_allocation_reused"] is False
    assert rebuild["denied_plan_sha256s"] == [DIAGNOSTIC_PLAN_SHA256]
    assert len(rebuild["ineligible_historical_candidates"]) == 1
    assert len(rebuild["eligible_imported_candidates"]) == 1
    diagnostic_id = rebuild["ineligible_historical_candidates"][0]["candidate_id"]
    assert diagnostic_id in preserved["engine"]["candidates"]
    assert diagnostic_id in imported["engine"]["candidates"]
    assert verify_continuation_import_manifest(root=root, manifest=manifest)["status"] == "PASS"


def test_continuation_refuses_source_artifact_mutation(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    (source / "report.md").write_text("modified\n", encoding="utf-8")
    with pytest.raises(V3ContinuationError, match="artifact manifest differs"):
        _build(root, source, readiness, ticket)


def test_continuation_refuses_any_epoch_two_response_even_if_republished(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    write_json(source / "tasks/e02-deepen_suppo-01/response.json", {"action": "reject"})
    _publish(source)
    with pytest.raises(V3ContinuationError, match="response or inference"):
        _build(root, source, readiness, ticket)


def test_continuation_refuses_nonpending_epoch_two_queue(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    recovery_path = source / "recovery-state.json"
    recovery = json.loads(recovery_path.read_text())
    recovery["queue"][0]["status"] = "NONPROPOSAL"
    recovery["state_sha256"] = canonical_hash({
        key: value for key, value in recovery.items() if key != "state_sha256"})
    write_json(recovery_path, recovery)
    _publish(source)
    with pytest.raises(V3ContinuationError, match="uniquely pending"):
        _build(root, source, readiness, ticket)


def test_continuation_refuses_spent_or_differently_bound_successor_ticket(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    write_json(ticket.with_name(ticket.name + ".claimed.json"), {"claimed": True})
    with pytest.raises(V3ContinuationError, match="already claimed"):
        _build(root, source, readiness, ticket)


def test_continuation_recovery_accepts_only_the_exact_existing_claim(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    manifest = _build(root, source, readiness, ticket)
    claim = ticket.with_name(ticket.name + ".claimed.json")
    write_json(claim, {
        "campaign_id": "successor-campaign",
        "claim_version": "klax-v3-ticket-claim-v1",
        "readiness_sha256": sha256_file(readiness),
        "synthetic": None,
        "ticket_sha256": sha256_file(ticket),
    })
    assert verify_continuation_import_manifest(
        root=root, manifest=manifest, allow_existing_claim=True)["status"] == "PASS"
    with pytest.raises(V3ContinuationError, match="already claimed"):
        verify_continuation_import_manifest(root=root, manifest=manifest)

    claim_value = json.loads(claim.read_text(encoding="utf-8"))
    claim_value["ticket_sha256"] = "0" * 64
    write_json(claim, claim_value)
    with pytest.raises(V3ContinuationError, match="claim identity"):
        verify_continuation_import_manifest(
            root=root, manifest=manifest, allow_existing_claim=True)


def test_continuation_manifest_write_is_immutable(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    destination = root / "data/manifests/continuation.json"
    first = write_continuation_import_manifest(
        destination=destination, root=root, source_campaign=source,
        successor_campaign_id="successor-campaign",
        successor_readiness_path=readiness, successor_ticket_path=ticket)
    second = write_continuation_import_manifest(
        destination=destination, root=root, source_campaign=source,
        successor_campaign_id="successor-campaign",
        successor_readiness_path=readiness, successor_ticket_path=ticket)
    assert first == second

    modified = deepcopy(first)
    modified["successor"]["claim_path"] = "runs/different.claimed.json"
    write_json(destination, modified)
    with pytest.raises(V3ContinuationError, match="Refusing to overwrite"):
        write_continuation_import_manifest(
            destination=destination, root=root, source_campaign=source,
            successor_campaign_id="successor-campaign",
            successor_readiness_path=readiness, successor_ticket_path=ticket)


def test_rebuilt_epoch_two_must_use_only_eligible_parent_pool(tmp_path):
    root, source, readiness, ticket = _fixture(tmp_path)
    manifest = _build(root, source, readiness, ticket)
    rebuild = manifest["continuation"]["epoch_two_rebuild"]
    eligible_id = rebuild["eligible_imported_candidates"][0]["candidate_id"]
    plan = deepcopy(
        manifest["continuation"]["preserved_source_state"]["engine"]
        ["candidates"][eligible_id]["plan"])
    queue = [{
        "task_id": "e02-deepen_suppo-01", "status": "PENDING",
        "candidate_id": None, "plan": plan, "options": [plan],
    }]
    decision = {
        "epoch": 2,
        "weights": rebuild["allocation_weights"],
        "eligible_parent_pool_sha256": rebuild["eligible_parent_pool_sha256"],
        "source_candidate_ids": [eligible_id],
        "candidate_plan_sha256s": [ResearchPlanV3.from_dict(plan).identity],
    }
    decision["decision_sha256"] = canonical_hash(decision)
    assert verify_epoch_two_rebuild(
        manifest=manifest, allocation_decision=decision, queue=queue)["status"] == "PASS"

    denied_plan = deepcopy(DIAGNOSTIC_PLAN)
    queue[0]["plan"] = denied_plan
    queue[0]["options"] = [denied_plan]
    decision["candidate_plan_sha256s"] = [DIAGNOSTIC_PLAN_SHA256]
    decision["decision_sha256"] = canonical_hash({
        key: value for key, value in decision.items() if key != "decision_sha256"})
    with pytest.raises(V3ContinuationError, match="denied plan or parent"):
        verify_epoch_two_rebuild(
            manifest=manifest, allocation_decision=decision, queue=queue)
