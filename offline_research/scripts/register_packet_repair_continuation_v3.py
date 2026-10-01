"""Register the immutable campaign-2 packet repair and r1 continuation evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import klax_lab.incident_packet_amendment_v3 as incident
from klax_lab.campaign_v3 import CHAMPION_RANKING_RULE
from klax_lab.provenance import canonical_hash, sha256_file, write_json


ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR_ID = "v3-offline-20260926T164700000Z-r2"
SUCCESSOR_READINESS = "data/manifests/v3_readiness_r2.json"
SUCCESSOR_TICKET = "runs/v3_offline_campaign_ticket_r2.json"


def _exclusive_json(path: Path, value: object) -> None:
    if path.exists():
        raise RuntimeError(f"Refusing to replace registered evidence: {path}")
    write_json(path, value)


def main() -> None:
    first = json.loads((ROOT / incident.FIRST_AMENDMENT_PATH).read_text(
        encoding="utf-8"))
    archive_manifest = json.loads((
        ROOT / incident.AUTHORIZATION_ARCHIVE_PATH).read_text(encoding="utf-8"))
    archived = {row["original_path"]: row for row in archive_manifest["files"]}
    archived_readiness = json.loads((ROOT / archived[
        "data/manifests/v3_readiness.json"]["archived_path"]).read_text(
            encoding="utf-8"))

    current_inventory = [{
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in sorted((ROOT / "src/klax_lab").glob("*.py"))]
    code_sha = canonical_hash(current_inventory)
    old = {row["path"]: row for row in archived_readiness["code_inventory"]}
    changed = []
    for row in current_inventory:
        prior = old.get(row["path"])
        if prior is None or prior["sha256"] != row["sha256"]:
            changed.append({
                "path": row["path"],
                "before": None if prior is None else prior["sha256"],
                "after": row["sha256"],
            })
    code_inventory = {
        "schema_version": 1,
        "component": incident.CODE_INVENTORY_COMPONENT,
        "code_inventory": current_inventory,
        "code_sha256": code_sha,
        "network_used": False,
        "protected_final_read": False,
    }
    _exclusive_json(ROOT / incident.CODE_INVENTORY_PATH, code_inventory)

    campaign_root = ROOT / incident.FAILED_CAMPAIGN_PATH
    artifact_manifest = json.loads((
        campaign_root / "campaign-artifacts.json").read_text(encoding="utf-8"))
    summary = json.loads((campaign_root / "summary.json").read_text(
        encoding="utf-8"))
    archived_authorization = {
        "manifest_path": incident.AUTHORIZATION_ARCHIVE_PATH.as_posix(),
        "manifest_sha256": incident.AUTHORIZATION_ARCHIVE_SHA256,
        "readiness_path": archived["data/manifests/v3_readiness.json"][
            "archived_path"],
        "readiness_sha256": archived["data/manifests/v3_readiness.json"]["sha256"],
        "data_bundle_path": archived["data/manifests/v3_data_bundle.json"][
            "archived_path"],
        "data_bundle_sha256": archived[
            "data/manifests/v3_data_bundle.json"]["sha256"],
        "ticket_path": archived["runs/v3_offline_campaign_ticket.json"][
            "archived_path"],
        "ticket_sha256": archived["runs/v3_offline_campaign_ticket.json"]["sha256"],
        "claim_path": archived[
            "runs/v3_offline_campaign_ticket.json.claimed.json"]["archived_path"],
        "claim_sha256": archived[
            "runs/v3_offline_campaign_ticket.json.claimed.json"]["sha256"],
        "bootstrap_chain": [{
            "transition": transition,
            "path": archived[original]["archived_path"],
            "sha256": archived[original]["sha256"],
        } for transition, original in {
            "BASE": "runs/v3_offline_campaign_ticket.json.bootstrap.json",
            "CLAIMED": "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
            "RECOVERY_CONSUMED":
                "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
            "COMMITTED":
                "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
        }.items()],
    }
    failed = {
        "campaign_id": incident.FAILED_CAMPAIGN_ID,
        "campaign_path": incident.FAILED_CAMPAIGN_PATH.as_posix(),
        "completed_at_utc": summary["completed_at_utc"],
        "scientific_conclusion": "INSUFFICIENT_EVIDENCE",
        "stop_reason": incident.STOP_REASON,
        "campaign_artifacts": {
            "path": (incident.FAILED_CAMPAIGN_PATH /
                     "campaign-artifacts.json").as_posix(),
            "sha256": incident.CAMPAIGN_ARTIFACTS_SHA256,
            "manifest_sha256": artifact_manifest["manifest_sha256"],
            "artifact_count": len(artifact_manifest["artifacts"]),
        },
        "summary": {
            "path": (incident.FAILED_CAMPAIGN_PATH / "summary.json").as_posix(),
            "sha256": incident.SUMMARY_SHA256,
        },
        "recovery_state": {
            "path": (incident.FAILED_CAMPAIGN_PATH /
                     "recovery-state.json").as_posix(),
            "sha256": incident.RECOVERY_SHA256,
        },
        "search_coverage": {
            "path": (incident.FAILED_CAMPAIGN_PATH /
                     "search-coverage.json").as_posix(),
            "sha256": incident.COVERAGE_SHA256,
        },
        "archived_authorization": archived_authorization,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    campaign = incident._verify_campaign(ROOT, failed)
    archive = incident._verify_archive(ROOT, failed)
    packet_bytes, packet = incident._reproduce_packet(campaign, archive)
    candidate_rows = incident._candidate_import_records(campaign)
    observed = sorted(row["plan_sha256"] for row in candidate_rows)
    eligible = sorted(
        value for value in observed
        if value != incident.DENIED_DIAGNOSTIC_PLAN_SHA256)
    diagnostic = first["diagnostic_quarantine"]["manifest"]
    import_body = {
        "schema_version": 1,
        "component": incident.IMPORT_STATE_COMPONENT,
        "mode": incident.CONTINUATION_MODE,
        "source_campaign_id": incident.FAILED_CAMPAIGN_ID,
        "source_campaign_artifacts_sha256": incident.CAMPAIGN_ARTIFACTS_SHA256,
        "source_recovery_sha256": incident.RECOVERY_SHA256,
        "source_coverage_sha256": incident.COVERAGE_SHA256,
        "candidates": candidate_rows,
        "epoch_2_allocation": campaign["coverage"]["allocation_decisions"],
        "epoch_2_queue_sha256": canonical_hash(campaign["queue"]),
        "epoch_2_queue_task_ids": [row["task_id"] for row in campaign["queue"]],
        "counters": {
            "current_epoch": 2,
            "admitted_candidates": 10,
            "executed_candidates": 10,
            "model_calls": 11,
            "model_context_tokens_reserved": 180_224,
            "elapsed_seconds": 167,
            "epoch_admissions": {"1": 10, "2": 0},
        },
        "next_queue_index": 0,
        "novelty_index_sha256": canonical_hash(campaign["engine"]["novelty_index"]),
        "diagnostic_quarantine": {
            "manifest": diagnostic,
            "excluded_from_continuation_parenting_and_ranking": True,
            "r1_campaign_results_remain_legitimate_imported_adaptive_evidence": True,
        },
        "continuation_rebuild": {
            "required": True,
            "source_allocation_and_queue_preserved_for_audit": True,
            "source_allocation_or_queue_reusable": False,
            "rebuild_from_eligible_imported_candidates": True,
            "denied_plan_sha256s": [incident.DENIED_DIAGNOSTIC_PLAN_SHA256],
            "eligible_parent_and_ranking_plan_sha256s": eligible,
            "preserve_source_counters_and_spent_budget": True,
        },
        "failed_packet_sha256": canonical_hash(packet),
        "no_epoch_2_response_or_evaluation": True,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    import_state = {**import_body, "state_sha256": canonical_hash(import_body)}
    _exclusive_json(ROOT / incident.IMPORT_STATE_PATH, import_state)

    replacement = {
        "mode": incident.CONTINUATION_MODE,
        "campaign_id": SUCCESSOR_ID,
        "readiness_path": SUCCESSOR_READINESS,
        "ticket_path": SUCCESSOR_TICKET,
        "claim_path": SUCCESSOR_TICKET + ".claimed.json",
        "must_differ_from_source_campaign_id": True,
        "must_not_mutate_source_campaign": True,
        "must_use_fresh_readiness_and_one_use_ticket": True,
        "must_bind_this_amendment_and_import_state": True,
        "must_preserve_exact_source_state_and_rebuild_epoch_2_queue": True,
        "original_epoch_2_allocation_or_queue_reusable": False,
        "claim_path_must_be_derived_from_ticket_path": True,
        "prelaunch_claim_path_must_not_exist": True,
        "source_ticket_or_claim_reusable": False,
    }
    amendment = {
        "schema_version": 1,
        "component": incident.AMENDMENT_COMPONENT,
        "amendment_id": "campaign2-packet-repair-and-budget-preserving-continuation",
        "status": incident.AMENDMENT_STATUS,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_amendment": {
            "path": incident.FIRST_AMENDMENT_PATH.as_posix(),
            "sha256": incident.FIRST_AMENDMENT_SHA256,
            "amendment_id": first["amendment_id"],
            "replacement_campaign_id": incident.FAILED_CAMPAIGN_ID,
        },
        "failed_campaign": failed,
        "trigger_evidence": {
            "basis": (
                "The first epoch-2 packet exceeded the pinned worker byte cap "
                "before inference, response creation, or evaluation."),
            "task_id": "e02-deepen_suppo-01",
            "category": "deepen_supported",
            "worker_role": "synthesizer",
            "seed_count": 6,
            "reproduced_canonical_packet_bytes": packet_bytes,
            "reproduced_canonical_packet_sha256": canonical_hash(packet),
            "independent_observed_packet_bytes": [12_254, 12_255],
            "worker_max_packet_bytes": 10_000,
            "worker_context_tokens": 16_384,
            "worker_generation_tokens": 768,
            "exception_type": "V3ResearchProtocolError",
            "exception_message": "V3 research packet exceeds byte limit",
            "inference_process_started": False,
            "epoch2_response_recorded": False,
            "epoch2_evaluation_recorded": False,
            "model_calls_before_attempt": 10,
            "model_calls_after_attempt": 11,
            "reserved_context_tokens_before_attempt": 163_840,
            "reserved_context_tokens_after_attempt": 180_224,
        },
        "unchanged_scientific_contract": first["unchanged_scientific_contract"],
        "repair_scope": {
            "allowed_changes": sorted(row["path"] for row in changed),
            "changed_code_pre_post": changed,
            "post_repair_code_inventory": {
                "path": incident.CODE_INVENTORY_PATH.as_posix(),
                "sha256": sha256_file(ROOT / incident.CODE_INVENTORY_PATH),
                "code_sha256": code_sha,
            },
            "scope_expansion": False,
            "repair_selected_only_from_packet_resource_evidence": True,
            "development_performance_used_to_select_repair": False,
        },
        "continuation_import_state": {
            "path": incident.IMPORT_STATE_PATH.as_posix(),
            "sha256": sha256_file(ROOT / incident.IMPORT_STATE_PATH),
            "state_sha256": import_state["state_sha256"],
        },
        "observed_plan_policy": {
            "source_campaign_id": incident.FAILED_CAMPAIGN_ID,
            "observed_plan_sha256s": observed,
            "denied_plan_sha256s": [incident.DENIED_DIAGNOSTIC_PLAN_SHA256],
            "eligible_parent_and_ranking_plan_sha256s": eligible,
            "continuation_imports_as_legitimate_adaptive_evidence": True,
            "fresh_restart_denylist_candidates_seeds_parents_and_ranking": True,
        },
        "diagnostic_quarantine": {
            "manifest": diagnostic,
            "excluded_from_continuation_parenting_and_ranking": True,
            "excluded_from_promotion_or_protected_final_authorization": True,
            "performance_used_to_select_packet_repair": False,
        },
        "replacement_constraints": replacement,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    _exclusive_json(ROOT / incident.AMENDMENT_PATH, amendment)
    ranking_sha = canonical_hash({"rule": CHAMPION_RANKING_RULE})
    result = incident.verify_campaign2_packet_repair_amendment(
        ROOT, expected_champion_ranking_rule_sha256=ranking_sha,
        pre_issuance=True)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
