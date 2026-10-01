from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

import klax_lab.incident_packet_amendment_v3 as packet_incident
from klax_lab.campaign_v3 import CHAMPION_RANKING_RULE
from klax_lab.provenance import canonical_hash, sha256_file, write_json


PROJECT = Path(__file__).resolve().parents[1]


def _copy_file(root: Path, relative: Path | str) -> Path:
    relative = Path(relative)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PROJECT / relative, target)
    return target


def _fixture(root: Path) -> dict[str, str]:
    shutil.copytree(
        PROJECT / packet_incident.FAILED_CAMPAIGN_PATH,
        root / packet_incident.FAILED_CAMPAIGN_PATH,
    )
    shutil.copytree(
        PROJECT / packet_incident.AUTHORIZATION_ARCHIVE_PATH.parent,
        root / packet_incident.AUTHORIZATION_ARCHIVE_PATH.parent,
    )
    _copy_file(root, packet_incident.FIRST_AMENDMENT_PATH)
    first = json.loads(
        (root / packet_incident.FIRST_AMENDMENT_PATH).read_text(
            encoding="utf-8"))
    diagnostic_relative = Path(
        first["diagnostic_quarantine"]["manifest"]["path"])
    _copy_file(root, diagnostic_relative)
    _copy_file(root, "configs/v3_goal.json")
    _copy_file(root, "schemas/research-plan-v3.schema.json")
    (root / "src/klax_lab").mkdir(parents=True)
    for path in sorted((PROJECT / "src/klax_lab").glob("*.py")):
        shutil.copy2(path, root / "src/klax_lab" / path.name)

    archive = json.loads(
        (root / packet_incident.AUTHORIZATION_ARCHIVE_PATH).read_text(
            encoding="utf-8"))
    archived = {row["original_path"]: row for row in archive["files"]}
    archived_readiness = json.loads((
        root / archived["data/manifests/v3_readiness.json"]["archived_path"]
    ).read_text(encoding="utf-8"))
    current_inventory = [{
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in sorted((root / "src/klax_lab").glob("*.py"))]
    code_sha = canonical_hash(current_inventory)
    write_json(root / packet_incident.CODE_INVENTORY_PATH, {
        "schema_version": 1,
        "component": packet_incident.CODE_INVENTORY_COMPONENT,
        "code_inventory": current_inventory,
        "code_sha256": code_sha,
        "network_used": False,
        "protected_final_read": False,
    })
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

    campaign_root = root / packet_incident.FAILED_CAMPAIGN_PATH
    artifact_manifest = json.loads(
        (campaign_root / "campaign-artifacts.json").read_text(encoding="utf-8"))
    summary = json.loads((campaign_root / "summary.json").read_text(encoding="utf-8"))
    failed = {
        "campaign_id": packet_incident.FAILED_CAMPAIGN_ID,
        "campaign_path": packet_incident.FAILED_CAMPAIGN_PATH.as_posix(),
        "completed_at_utc": summary["completed_at_utc"],
        "scientific_conclusion": "INSUFFICIENT_EVIDENCE",
        "stop_reason": packet_incident.STOP_REASON,
        "campaign_artifacts": {
            "path": (packet_incident.FAILED_CAMPAIGN_PATH /
                     "campaign-artifacts.json").as_posix(),
            "sha256": packet_incident.CAMPAIGN_ARTIFACTS_SHA256,
            "manifest_sha256": artifact_manifest["manifest_sha256"],
            "artifact_count": len(artifact_manifest["artifacts"]),
        },
        "summary": {
            "path": (packet_incident.FAILED_CAMPAIGN_PATH /
                     "summary.json").as_posix(),
            "sha256": packet_incident.SUMMARY_SHA256,
        },
        "recovery_state": {
            "path": (packet_incident.FAILED_CAMPAIGN_PATH /
                     "recovery-state.json").as_posix(),
            "sha256": packet_incident.RECOVERY_SHA256,
        },
        "search_coverage": {
            "path": (packet_incident.FAILED_CAMPAIGN_PATH /
                     "search-coverage.json").as_posix(),
            "sha256": packet_incident.COVERAGE_SHA256,
        },
        "archived_authorization": {
            "manifest_path": packet_incident.AUTHORIZATION_ARCHIVE_PATH.as_posix(),
            "manifest_sha256": packet_incident.AUTHORIZATION_ARCHIVE_SHA256,
            "readiness_path": archived[
                "data/manifests/v3_readiness.json"]["archived_path"],
            "readiness_sha256": archived[
                "data/manifests/v3_readiness.json"]["sha256"],
            "data_bundle_path": archived[
                "data/manifests/v3_data_bundle.json"]["archived_path"],
            "data_bundle_sha256": archived[
                "data/manifests/v3_data_bundle.json"]["sha256"],
            "ticket_path": archived[
                "runs/v3_offline_campaign_ticket.json"]["archived_path"],
            "ticket_sha256": archived[
                "runs/v3_offline_campaign_ticket.json"]["sha256"],
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
                "CLAIMED":
                    "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
                "RECOVERY_CONSUMED":
                    "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
                "COMMITTED":
                    "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
            }.items()],
        },
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    campaign = packet_incident._verify_campaign(root, failed)
    archive_verified = packet_incident._verify_archive(root, failed)
    packet_bytes, reproduced_packet = packet_incident._reproduce_packet(
        campaign, archive_verified)
    candidate_rows = packet_incident._candidate_import_records(campaign)
    observed = sorted(row["plan_sha256"] for row in candidate_rows)
    eligible = sorted(
        value for value in observed
        if value != packet_incident.DENIED_DIAGNOSTIC_PLAN_SHA256)
    diagnostic = first["diagnostic_quarantine"]["manifest"]
    import_body = {
        "schema_version": 1,
        "component": packet_incident.IMPORT_STATE_COMPONENT,
        "mode": packet_incident.CONTINUATION_MODE,
        "source_campaign_id": packet_incident.FAILED_CAMPAIGN_ID,
        "source_campaign_artifacts_sha256":
            packet_incident.CAMPAIGN_ARTIFACTS_SHA256,
        "source_recovery_sha256": packet_incident.RECOVERY_SHA256,
        "source_coverage_sha256": packet_incident.COVERAGE_SHA256,
        "candidates": candidate_rows,
        "epoch_2_allocation": campaign["coverage"]["allocation_decisions"],
        "epoch_2_queue_sha256": canonical_hash(campaign["queue"]),
        "epoch_2_queue_task_ids": [row["task_id"] for row in campaign["queue"]],
        "counters": {
            "current_epoch": 2, "admitted_candidates": 10,
            "executed_candidates": 10, "model_calls": 11,
            "model_context_tokens_reserved": 180_224, "elapsed_seconds": 167,
            "epoch_admissions": {"1": 10, "2": 0},
        },
        "next_queue_index": 0,
        "novelty_index_sha256": canonical_hash(
            campaign["engine"]["novelty_index"]),
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
            "denied_plan_sha256s": [
                packet_incident.DENIED_DIAGNOSTIC_PLAN_SHA256],
            "eligible_parent_and_ranking_plan_sha256s": eligible,
            "preserve_source_counters_and_spent_budget": True,
        },
        "failed_packet_sha256": canonical_hash(reproduced_packet),
        "no_epoch_2_response_or_evaluation": True,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    import_state = {
        **import_body, "state_sha256": canonical_hash(import_body),
    }
    write_json(root / packet_incident.IMPORT_STATE_PATH, import_state)

    replacement = {
        "mode": packet_incident.CONTINUATION_MODE,
        "campaign_id": "v3-offline-continuation-fixture",
        "readiness_path": "data/manifests/v3_readiness_continuation_fixture.json",
        "ticket_path": "runs/v3_campaign_continuation_fixture_ticket.json",
        "claim_path": "runs/v3_campaign_continuation_fixture_ticket.json.claimed.json",
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
        "component": packet_incident.AMENDMENT_COMPONENT,
        "amendment_id": "campaign2-packet-repair-fixture",
        "status": packet_incident.AMENDMENT_STATUS,
        "registered_at_utc": "2026-09-26T17:00:00+00:00",
        "first_amendment": {
            "path": packet_incident.FIRST_AMENDMENT_PATH.as_posix(),
            "sha256": packet_incident.FIRST_AMENDMENT_SHA256,
            "amendment_id": first["amendment_id"],
            "replacement_campaign_id": packet_incident.FAILED_CAMPAIGN_ID,
        },
        "failed_campaign": failed,
        "trigger_evidence": {
            "basis": "The first epoch-2 packet exceeded the pinned worker byte cap.",
            "task_id": "e02-deepen_suppo-01",
            "category": "deepen_supported", "worker_role": "synthesizer",
            "seed_count": 6,
            "reproduced_canonical_packet_bytes": packet_bytes,
            "reproduced_canonical_packet_sha256":
                canonical_hash(reproduced_packet),
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
        "unchanged_scientific_contract": first[
            "unchanged_scientific_contract"],
        "repair_scope": {
            "allowed_changes": sorted(row["path"] for row in changed),
            "changed_code_pre_post": changed,
            "post_repair_code_inventory": {
                "path": packet_incident.CODE_INVENTORY_PATH.as_posix(),
                "sha256": sha256_file(root / packet_incident.CODE_INVENTORY_PATH),
                "code_sha256": code_sha,
            },
            "scope_expansion": False,
            "repair_selected_only_from_packet_resource_evidence": True,
            "development_performance_used_to_select_repair": False,
        },
        "continuation_import_state": {
            "path": packet_incident.IMPORT_STATE_PATH.as_posix(),
            "sha256": sha256_file(root / packet_incident.IMPORT_STATE_PATH),
            "state_sha256": import_state["state_sha256"],
        },
        "observed_plan_policy": {
            "source_campaign_id": packet_incident.FAILED_CAMPAIGN_ID,
            "observed_plan_sha256s": observed,
            "denied_plan_sha256s": [
                packet_incident.DENIED_DIAGNOSTIC_PLAN_SHA256],
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
    write_json(root / packet_incident.AMENDMENT_PATH, amendment)
    return replacement


def _ranking_sha() -> str:
    return canonical_hash({"rule": CHAMPION_RANKING_RULE})


def test_valid_packet_incident_amendment_binds_continuation(
        tmp_path: Path) -> None:
    replacement = _fixture(tmp_path)
    result = packet_incident.verify_campaign2_packet_repair_amendment(
        tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha(),
        pre_issuance=True)
    assert result["replacement_campaign_id"] == replacement["campaign_id"]
    assert result["reproduced_packet_bytes"] in {12_254, 12_255}
    assert len(result["observed_plan_sha256s"]) == 10
    assert result["denied_plan_sha256s"] == [
        packet_incident.DENIED_DIAGNOSTIC_PLAN_SHA256]
    assert len(result["eligible_parent_and_ranking_plan_sha256s"]) == 9


def test_packet_incident_refuses_tampered_imported_counter(
        tmp_path: Path) -> None:
    _fixture(tmp_path)
    state_path = tmp_path / packet_incident.IMPORT_STATE_PATH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["counters"]["model_calls"] = 10
    state_body = {key: value for key, value in state.items() if key != "state_sha256"}
    state["state_sha256"] = canonical_hash(state_body)
    write_json(state_path, state)
    amendment_path = tmp_path / packet_incident.AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["continuation_import_state"]["sha256"] = sha256_file(state_path)
    amendment["continuation_import_state"]["state_sha256"] = state["state_sha256"]
    write_json(amendment_path, amendment)
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="import state differs"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha())


def test_packet_incident_refuses_tampered_observed_plan_policy(
        tmp_path: Path) -> None:
    _fixture(tmp_path)
    amendment_path = tmp_path / packet_incident.AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["observed_plan_policy"]["denied_plan_sha256s"] = []
    write_json(amendment_path, amendment)
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="Observed-plan policy differs"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha())


def test_packet_incident_refuses_archive_tamper(tmp_path: Path) -> None:
    _fixture(tmp_path)
    archive = json.loads((
        tmp_path / packet_incident.AUTHORIZATION_ARCHIVE_PATH
    ).read_text(encoding="utf-8"))
    archived_readiness = tmp_path / archive["files"][0]["archived_path"]
    archived_readiness.write_bytes(archived_readiness.read_bytes() + b" ")
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="archived file changed"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha())


def test_packet_incident_refuses_incomplete_code_diff(tmp_path: Path) -> None:
    _fixture(tmp_path)
    amendment_path = tmp_path / packet_incident.AMENDMENT_PATH
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment["repair_scope"]["allowed_changes"] = amendment[
        "repair_scope"]["allowed_changes"][:-1]
    write_json(amendment_path, amendment)
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="Allowed packet repair files differ"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha())


def test_packet_incident_refuses_wrong_ranking_binding(tmp_path: Path) -> None:
    _fixture(tmp_path)
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="Scientific contract differs"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256="0" * 64)


def test_packet_incident_refuses_recovery_review_semantic_mismatch(
        tmp_path: Path) -> None:
    _fixture(tmp_path)
    amendment = json.loads((
        tmp_path / packet_incident.AMENDMENT_PATH
    ).read_text(encoding="utf-8"))
    campaign = packet_incident._verify_campaign(
        tmp_path, amendment["failed_campaign"])
    candidate_id = sorted(campaign["candidates"])[0]
    campaign["candidates"][candidate_id]["promotion"] = {
        "tampered": "semantic mismatch",
    }
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="recovery promotion differs"):
        packet_incident._candidate_import_records(campaign)


def test_packet_incident_refuses_missing_amendment(tmp_path: Path) -> None:
    with pytest.raises(
            packet_incident.V3PacketIncidentAmendmentError,
            match="Missing or invalid"):
        packet_incident.verify_campaign2_packet_repair_amendment(
            tmp_path, expected_champion_ranking_rule_sha256=_ranking_sha())
