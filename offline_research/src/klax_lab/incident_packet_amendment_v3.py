"""Fail-closed verifier for the V3 campaign-2 packet-resource incident.

The r1 campaign completed ten development evaluations, then stopped before
starting its first epoch-2 inference because the six-seed packet exceeded the
pinned local worker's byte envelope.  A successor may continue only from an
immutable import of that exact state and a pre-registered amendment.  This
module is read-only and never creates authorization or campaign artifacts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .provenance import canonical_hash, sha256_file
from .research_plan_v3 import ResearchPlanV3


AMENDMENT_PATH = Path("data/manifests/v3_campaign2_packet_repair_amendment.json")
CODE_INVENTORY_PATH = Path(
    "data/manifests/v3_campaign2_packet_repair_code_inventory.json")
IMPORT_STATE_PATH = Path(
    "data/manifests/v3_campaign2_continuation_import_state.json")
FIRST_AMENDMENT_PATH = Path(
    "data/manifests/v3_campaign1_integrity_repair_amendment.json")
FIRST_AMENDMENT_SHA256 = (
    "024e98093c155423f2e98ff303f380f185f785d763d6a94afd6746dbaaa1f7b2")

FAILED_CAMPAIGN_ID = "v3-offline-20260926T160500000Z-r1"
FAILED_CAMPAIGN_PATH = Path("runs/campaigns_v3") / FAILED_CAMPAIGN_ID
CAMPAIGN_ARTIFACTS_SHA256 = (
    "a7e854cecfd691a0a380f8f24331fa682eb7c0c262fddefbdbe9866c7a0e40d6")
SUMMARY_SHA256 = (
    "54b1efcbc0b1231c179d3b1b0fa34f00533fbee5d7092446bbb6e60ea714170e")
RECOVERY_SHA256 = (
    "68a5b40f49815ae7eb079c6f41649af4e81f0a6f8c6a1ad5ffc59f0190321018")
COVERAGE_SHA256 = (
    "12b056fe1e88fdbd3a7aecbbc78c12dcf51241869383d8fc8c5f6cd24c74ab4d")

AUTHORIZATION_ARCHIVE_PATH = Path(
    "runs/authorization_archive/v3-offline-20260926T160500000Z-r1-stale/"
    "archive-manifest.json")
AUTHORIZATION_ARCHIVE_SHA256 = (
    "cedbdeaa4e22d3a82c96f5aaed92888a7ec4156b683c851f7c49440ac7155211")

AMENDMENT_COMPONENT = "v3_campaign_packet_resource_incident_amendment"
AMENDMENT_STATUS = (
    "REGISTERED_AFTER_R1_PACKET_RESOURCE_STOP_BEFORE_SUCCESSOR_AUTHORIZATION")
CODE_INVENTORY_COMPONENT = "v3_campaign_packet_repair_code_inventory"
IMPORT_STATE_COMPONENT = "v3_campaign2_continuation_import_state"
CONTINUATION_MODE = "IMMUTABLE_BUDGET_PRESERVING_CONTINUATION"
STOP_REASON = "required_data_integrity_replication_critic_or_resource_boundary_failure"
EXPECTED_PACKET_BYTES = {12_254, 12_255}
LOCAL_MAX_PACKET_BYTES = 10_000
LOCAL_CONTEXT_TOKENS = 16_384
LOCAL_GENERATION_TOKENS = 768
DENIED_DIAGNOSTIC_PLAN_SHA256 = (
    "f5f2fd485512dbcb45e0592766984bd5e17e00445f23378e663d39b6c10158a0")

_SHA = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
_TOP_KEYS = {
    "schema_version", "component", "amendment_id", "status",
    "registered_at_utc", "first_amendment", "failed_campaign",
    "trigger_evidence", "unchanged_scientific_contract", "repair_scope",
    "continuation_import_state", "observed_plan_policy",
    "diagnostic_quarantine", "replacement_constraints", "network_used",
    "protected_final_read", "actual_orders_placed",
}
_SCIENTIFIC_KEYS = {
    "config_sha256", "schema_sha256", "data_bundle_version",
    "data_bundle_sha256", "evaluation_policy_sha256",
    "promotion_gates_sha256", "campaign_budget_sha256",
    "partition_contract_sha256", "champion_ranking_rule_sha256",
    "search_dsl_changed", "target_return_changed", "data_or_labels_changed",
    "gates_or_budget_changed",
}


class V3PacketIncidentAmendmentError(ValueError):
    """The packet-resource continuation registration is missing or changed."""


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise V3PacketIncidentAmendmentError(f"{label} fields differ")
    return value


def _read(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=lambda item: (_ for _ in ()).throw(
                V3PacketIncidentAmendmentError(
                    f"Non-finite JSON in {label}: {item}")),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V3PacketIncidentAmendmentError(
            f"Missing or invalid campaign-2 {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3PacketIncidentAmendmentError(f"{label} must contain one object")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise V3PacketIncidentAmendmentError(f"Invalid {label} SHA-256")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise V3PacketIncidentAmendmentError(f"Invalid {label}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise V3PacketIncidentAmendmentError(f"Invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3PacketIncidentAmendmentError(f"Invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3PacketIncidentAmendmentError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _relative(root: Path, value: Any, label: str) -> tuple[str, Path]:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise V3PacketIncidentAmendmentError(f"Invalid {label} path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise V3PacketIncidentAmendmentError(f"{label} path escapes the project")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise V3PacketIncidentAmendmentError(
            f"{label} path escapes the project") from exc
    if {part.casefold().replace("-", "_") for part in target.parts} & {
            "protected_final", "holdout"}:
        raise V3PacketIncidentAmendmentError(
            f"{label} path enters protected storage")
    return relative.as_posix(), target


def _file_record(root: Path, value: Any, label: str) -> tuple[str, Path]:
    record = _exact(value, {"path", "sha256"}, label)
    relative, path = _relative(root, record["path"], label)
    digest = _digest(record["sha256"], label)
    if not path.is_file() or sha256_file(path) != digest:
        raise V3PacketIncidentAmendmentError(f"{label} changed")
    return relative, path


def _verify_first_amendment(root: Path, record: Any) -> dict[str, Any]:
    record = _exact(record, {
        "path", "sha256", "amendment_id", "replacement_campaign_id",
    }, "first amendment binding")
    relative, path = _relative(root, record["path"], "first amendment")
    if (relative != FIRST_AMENDMENT_PATH.as_posix()
            or record["sha256"] != FIRST_AMENDMENT_SHA256
            or not path.is_file()
            or sha256_file(path) != FIRST_AMENDMENT_SHA256):
        raise V3PacketIncidentAmendmentError("First amendment identity differs")
    value = _read(path, "first amendment")
    if (value.get("schema_version") != 1
            or value.get("component")
            != "v3_campaign_integrity_incident_amendment"
            or value.get("status")
            != "REGISTERED_AFTER_FAILED_CAMPAIGN_AND_REPAIR_VALIDATION_BEFORE_REPLACEMENT_READINESS_OR_TICKET"
            or value.get("network_used") is not False
            or value.get("protected_final_read") is not False
            or value.get("actual_orders_placed") is not False
            or value.get("amendment_id") != record["amendment_id"]
            or value.get("replacement_constraints", {}).get("campaign_id")
            != FAILED_CAMPAIGN_ID
            or record["replacement_campaign_id"] != FAILED_CAMPAIGN_ID):
        raise V3PacketIncidentAmendmentError("First amendment semantics differ")
    _timestamp(value.get("registered_at_utc"), "first amendment registration")
    diagnostic = value.get("diagnostic_quarantine")
    if not isinstance(diagnostic, dict):
        raise V3PacketIncidentAmendmentError(
            "First amendment diagnostic quarantine is missing")
    return value


def _verify_archive(root: Path, failed: dict[str, Any]) -> dict[str, Any]:
    if AUTHORIZATION_ARCHIVE_SHA256 is None:
        raise V3PacketIncidentAmendmentError(
            "R1 authorization archive expected SHA-256 is not finalized")
    authorization = _exact(failed["archived_authorization"], {
        "manifest_path", "manifest_sha256", "readiness_path",
        "readiness_sha256", "data_bundle_path", "data_bundle_sha256",
        "ticket_path", "ticket_sha256", "claim_path", "claim_sha256",
        "bootstrap_chain",
    }, "r1 archived authorization")
    relative, path = _relative(
        root, authorization["manifest_path"], "r1 authorization archive")
    if (relative != AUTHORIZATION_ARCHIVE_PATH.as_posix()
            or authorization["manifest_sha256"] != AUTHORIZATION_ARCHIVE_SHA256
            or not path.is_file()
            or sha256_file(path) != AUTHORIZATION_ARCHIVE_SHA256):
        raise V3PacketIncidentAmendmentError(
            "R1 authorization archive identity differs")
    archive = _read(path, "r1 authorization archive")
    originals = {
        "data/manifests/v3_readiness.json",
        "data/manifests/v3_data_bundle.json",
        "runs/v3_offline_campaign_ticket.json",
        "runs/v3_offline_campaign_ticket.json.claimed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
    }
    rows = archive.get("files")
    if (archive.get("archive_version") != "klax-v3-stale-authorization-archive-v2"
            or archive.get("status")
            != "ARCHIVED_BYTE_FOR_BYTE_BEFORE_CONTINUATION_AUTHORIZATION"
            or archive.get("failed_campaign_id") != FAILED_CAMPAIGN_ID
            or archive.get("preserved_campaign_path")
            != FAILED_CAMPAIGN_PATH.as_posix()
            or archive.get("protected_final_read") is not False
            or archive.get("actual_orders_placed") is not False
            or not isinstance(rows, list)):
        raise V3PacketIncidentAmendmentError(
            "R1 authorization archive semantics differ")
    by_original: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = _exact(row, {
            "archived_path", "bytes", "original_path", "sha256",
        }, "r1 archived file")
        original = row["original_path"]
        if (not isinstance(original, str) or original in by_original
                or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise V3PacketIncidentAmendmentError(
                "R1 authorization archive inventory differs")
        _, target = _relative(root, row["archived_path"], "r1 archived file")
        if (not target.is_file() or target.stat().st_size != row["bytes"]
                or sha256_file(target) != _digest(
                    row["sha256"], "r1 archived file")):
            raise V3PacketIncidentAmendmentError(
                f"R1 archived file changed: {original}")
        by_original[original] = {**row, "target": target}
    if set(by_original) != originals:
        raise V3PacketIncidentAmendmentError(
            "R1 authorization archive file set differs")

    bindings = {
        "readiness": "data/manifests/v3_readiness.json",
        "data_bundle": "data/manifests/v3_data_bundle.json",
        "ticket": "runs/v3_offline_campaign_ticket.json",
        "claim": "runs/v3_offline_campaign_ticket.json.claimed.json",
    }
    for label, original in bindings.items():
        row = by_original[original]
        if (authorization[f"{label}_path"] != row["archived_path"]
                or authorization[f"{label}_sha256"] != row["sha256"]):
            raise V3PacketIncidentAmendmentError(
                f"R1 archived {label} binding differs")
    transition_originals = {
        "BASE": "runs/v3_offline_campaign_ticket.json.bootstrap.json",
        "CLAIMED": "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
        "RECOVERY_CONSUMED":
            "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
        "COMMITTED": "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
    }
    chain = authorization["bootstrap_chain"]
    if (not isinstance(chain, list)
            or [row.get("transition") for row in chain]
            != list(transition_originals)):
        raise V3PacketIncidentAmendmentError(
            "R1 archived bootstrap chain differs")
    for item in chain:
        item = _exact(item, {"transition", "path", "sha256"}, "bootstrap item")
        archived = by_original[transition_originals[item["transition"]]]
        if item["path"] != archived["archived_path"] or item["sha256"] != archived["sha256"]:
            raise V3PacketIncidentAmendmentError(
                "R1 archived bootstrap item differs")

    readiness = _read(by_original[bindings["readiness"]]["target"], "r1 readiness")
    bundle = _read(by_original[bindings["data_bundle"]]["target"], "r1 data bundle")
    ticket = _read(by_original[bindings["ticket"]]["target"], "r1 ticket")
    claim = _read(by_original[bindings["claim"]]["target"], "r1 claim")
    first = readiness.get("incident_amendment", {})
    if (ticket.get("campaign_id") != FAILED_CAMPAIGN_ID
            or ticket.get("one_use") is not True
            or ticket.get("synthetic") is not False
            or first.get("path") != FIRST_AMENDMENT_PATH.as_posix()
            or first.get("sha256") != FIRST_AMENDMENT_SHA256
            or first.get("replacement_campaign_id") != FAILED_CAMPAIGN_ID
            or claim.get("campaign_id") != FAILED_CAMPAIGN_ID
            or claim.get("ticket_sha256")
            != by_original[bindings["ticket"]]["sha256"]
            or claim.get("readiness_sha256")
            != by_original[bindings["readiness"]]["sha256"]):
        raise V3PacketIncidentAmendmentError(
            "R1 archived one-use authorization differs")
    return {
        "readiness": readiness, "bundle": bundle, "ticket": ticket,
        "by_original": by_original,
    }


def _verify_campaign(root: Path, failed: Any) -> dict[str, Any]:
    failed = _exact(failed, {
        "campaign_id", "campaign_path", "completed_at_utc",
        "scientific_conclusion", "stop_reason", "campaign_artifacts",
        "summary", "recovery_state", "search_coverage",
        "archived_authorization", "network_used", "protected_final_read",
        "actual_orders_placed",
    }, "r1 failed campaign")
    campaign_relative, campaign_path = _relative(
        root, failed["campaign_path"], "r1 campaign")
    if (failed["campaign_id"] != FAILED_CAMPAIGN_ID
            or campaign_relative != FAILED_CAMPAIGN_PATH.as_posix()
            or not campaign_path.is_dir()
            or failed["scientific_conclusion"] != "INSUFFICIENT_EVIDENCE"
            or failed["stop_reason"] != STOP_REASON
            or any(failed[key] is not False for key in (
                "network_used", "protected_final_read", "actual_orders_placed"))):
        raise V3PacketIncidentAmendmentError("R1 campaign identity differs")

    records = {
        "summary": ("summary.json", SUMMARY_SHA256),
        "recovery_state": ("recovery-state.json", RECOVERY_SHA256),
        "search_coverage": ("search-coverage.json", COVERAGE_SHA256),
    }
    paths: dict[str, Path] = {}
    for label, (name, expected_sha) in records.items():
        relative, path = _file_record(root, failed[label], f"r1 {label}")
        if (relative != (FAILED_CAMPAIGN_PATH / name).as_posix()
                or failed[label]["sha256"] != expected_sha):
            raise V3PacketIncidentAmendmentError(f"R1 {label} identity differs")
        paths[label] = path

    manifest_record = _exact(failed["campaign_artifacts"], {
        "path", "sha256", "manifest_sha256", "artifact_count",
    }, "r1 campaign artifact manifest")
    relative, manifest_path = _relative(
        root, manifest_record["path"], "r1 campaign artifact manifest")
    if (relative != (FAILED_CAMPAIGN_PATH / "campaign-artifacts.json").as_posix()
            or manifest_record["sha256"] != CAMPAIGN_ARTIFACTS_SHA256
            or not manifest_path.is_file()
            or sha256_file(manifest_path) != CAMPAIGN_ARTIFACTS_SHA256):
        raise V3PacketIncidentAmendmentError(
            "R1 campaign artifact identity differs")
    manifest = _read(manifest_path, "r1 campaign artifact manifest")
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    rows = manifest.get("artifacts")
    if (manifest.get("campaign_id") != FAILED_CAMPAIGN_ID
            or manifest.get("network_used") is not False
            or manifest.get("protected_final_read") is not False
            or manifest.get("actual_orders_placed") is not False
            or canonical_hash(body) != manifest.get("manifest_sha256")
            or manifest_record["manifest_sha256"] != manifest.get("manifest_sha256")
            or not isinstance(rows, list)
            or manifest_record["artifact_count"] != len(rows)):
        raise V3PacketIncidentAmendmentError(
            "R1 campaign artifact manifest differs")
    artifact_index: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = _exact(row, {"path", "bytes", "sha256"}, "r1 campaign artifact")
        artifact_relative = row["path"]
        if (not isinstance(artifact_relative, str)
                or artifact_relative in artifact_index
                or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise V3PacketIncidentAmendmentError("R1 artifact inventory differs")
        _, target = _relative(
            root, f"{campaign_relative}/{artifact_relative}", "r1 artifact")
        if (not target.is_file() or target.stat().st_size != row["bytes"]
                or sha256_file(target) != _digest(row["sha256"], "r1 artifact")):
            raise V3PacketIncidentAmendmentError(
                f"R1 campaign artifact changed: {artifact_relative}")
        artifact_index[artifact_relative] = row
    for label, (name, _) in records.items():
        if artifact_index.get(name, {}).get("sha256") != failed[label]["sha256"]:
            raise V3PacketIncidentAmendmentError(
                f"R1 artifact manifest does not bind {label}")

    summary = _read(paths["summary"], "r1 summary")
    recovery = _read(paths["recovery_state"], "r1 recovery")
    coverage = _read(paths["search_coverage"], "r1 search coverage")
    recovery_body = {key: value for key, value in recovery.items()
                     if key != "state_sha256"}
    engine = recovery.get("engine", {})
    queue = recovery.get("queue")
    if (summary.get("campaign_id") != FAILED_CAMPAIGN_ID
            or summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("scientific_conclusion") != "INSUFFICIENT_EVIDENCE"
            or summary.get("stopped_reason") != STOP_REASON
            or summary.get("protected_final_evaluated") is not False
            or summary.get("protected_final_authorization_issued") is not False
            or summary.get("actual_orders_placed") is not False
            or failed["completed_at_utc"] != summary.get("completed_at_utc")
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("protected_final_evaluated") is not False
            or recovery.get("actual_orders_placed") is not False
            or recovery.get("state_sha256") != canonical_hash(recovery_body)
            or recovery.get("coverage") != coverage
            or not isinstance(queue, list) or len(queue) != 10
            or recovery.get("next_queue_index") != 0
            or coverage.get("integrity_failures") != []):
        raise V3PacketIncidentAmendmentError("R1 terminal state differs")
    counters = {
        "current_epoch": 2, "admitted_candidates": 10,
        "executed_candidates": 10, "model_calls": 11,
        "model_context_tokens_reserved": 180_224, "elapsed_seconds": 167,
    }
    if (any(engine.get(key) != value for key, value in counters.items())
            or engine.get("stopped_reason") != STOP_REASON
            or engine.get("epoch_admissions") != {"1": 10, "2": 0}
            or any(item.get("status") != "PENDING" for item in queue)
            or queue[0].get("task_id") != "e02-deepen_suppo-01"):
        raise V3PacketIncidentAmendmentError("R1 continuation counters differ")
    candidates = engine.get("candidates")
    if not isinstance(candidates, dict) or len(candidates) != 10:
        raise V3PacketIncidentAmendmentError("R1 candidate set differs")
    if any(raw.get("epoch") != 1 for raw in candidates.values()):
        raise V3PacketIncidentAmendmentError("R1 contains an epoch-2 evaluation")
    if any(path.startswith(("local-inference/e02-", "tasks/e02-"))
           for path in artifact_index):
        raise V3PacketIncidentAmendmentError(
            "R1 contains an epoch-2 response artifact")
    return {
        "summary": summary, "recovery": recovery, "coverage": coverage,
        "engine": engine, "queue": queue, "candidates": candidates,
        "artifact_index": artifact_index, "campaign_path": campaign_path,
    }


def _plan_summary(raw: dict[str, Any]) -> dict[str, Any]:
    evaluation = raw.get("evaluation") or {}
    candidate = evaluation.get("candidate") or {}
    economics = candidate.get("historical_assumed_fill") or {}
    scores = candidate.get("forecast_scores") or {}
    reference = (evaluation.get("reference") or {}).get("forecast_scores") or {}
    promotion = dict(raw.get("promotion") or {})
    replication = raw.get("replication") or {}
    critic = raw.get("critic") or {}
    return {
        "candidate_id": raw["candidate_id"],
        "research_plan_sha256": ResearchPlanV3.from_dict(raw["plan"]).identity,
        "novelty_fingerprint": ResearchPlanV3.from_dict(raw["plan"]).novelty_fingerprint,
        "colony": raw["plan"]["colony"], "epoch": raw["epoch"],
        "capital_weighted_return": economics.get("capital_weighted_return"),
        "trade_count": economics.get("trade_count"),
        "bootstrap_lower_95": (economics.get("bootstrap") or {}).get("lower_95"),
        "brier": scores.get("brier"),
        "crps_f": scores.get("crps_f", scores.get("gaussian_crps_f")),
        "reference_brier": reference.get("brier"),
        "reference_crps_f": reference.get(
            "crps_f", reference.get("gaussian_crps_f")),
        "probability_conservation_passed": scores.get(
            "probability_conservation_passed"),
        "total_net_profit": economics.get("total_net_profit"),
        "total_entry_outlay": economics.get("total_entry_outlay"),
        "mean_trade_return": economics.get("mean_trade_return"),
        "capital_weighted_return_after_removing_most_profitable_day":
            economics.get("capital_weighted_return_after_removing_most_profitable_day"),
        "bootstrap": dict(economics.get("bootstrap") or {}),
        "fee_scenario": dict(economics.get("fee_scenario") or {}),
        "contribution_breakdowns": dict(
            economics.get("contribution_breakdowns") or {}),
        "folds": [{key: value.get(key) for key in (
            "fold", "trade_count", "total_net_profit", "total_entry_outlay",
            "capital_weighted_return", "mean_trade_return")}
            for value in evaluation.get("folds", [])],
        "cost_stress": dict(evaluation.get("stress_return") or {}),
        "promotion_passed": promotion.get("passed"),
        "rejection_reasons": list(promotion.get("reasons", [])),
        "promotion": promotion,
        "replication": {
            "status": replication.get("status"),
            "verifier_id": replication.get("verifier_id"),
            "checks": dict(replication.get("checks") or {}),
            "differences": list(replication.get("differences") or []),
        },
        "critic": {
            "decision": critic.get("decision"),
            "critic_id": critic.get("critic_id"),
            "checks": dict(critic.get("checks") or {}),
            "unresolved_defects": list(critic.get("unresolved_defects") or []),
        },
        "artifact_sha256s": dict(raw.get("artifact_sha256s") or {}),
    }


def _reproduce_packet(campaign: dict[str, Any], archive: dict[str, Any]) -> tuple[int, dict]:
    recovery = campaign["recovery"]
    engine = campaign["engine"]
    item = campaign["queue"][0]
    readiness = archive["readiness"]
    plan = ResearchPlanV3.from_dict(item["plan"])
    options = [ResearchPlanV3.from_dict(value).to_dict()
               for value in item["options"]]
    if len(options) != 6 or len(plan.parent_plan_sha256s) != 1:
        raise V3PacketIncidentAmendmentError(
            "R1 first epoch-2 packet options differ")
    parent = next((raw for raw in campaign["candidates"].values()
                   if ResearchPlanV3.from_dict(raw["plan"]).identity
                   == plan.parent_plan_sha256s[0]), None)
    if parent is None:
        raise V3PacketIncidentAmendmentError("R1 packet parent is missing")
    budget = readiness.get("campaign_budget")
    if not isinstance(budget, dict):
        raise V3PacketIncidentAmendmentError("R1 campaign budget is missing")
    reservation = (budget["local_reserved_context_tokens"]
                   // budget["maximum_local_model_calls"])
    before_calls = engine["model_calls"] - 1
    before_reserved = engine["model_context_tokens_reserved"] - reservation
    packet = {
        "protocol": "klax-research-proposal-v3",
        "task_id": item["task_id"], "campaign_id": FAILED_CAMPAIGN_ID,
        "worker_role": item["role"], "scope": "development_only",
        "synthetic": False,
        "question": (
            "Nominate exactly one supplied whole seed plan for deterministic offline "
            "testing. Propose means nominate an unverified hypothesis, not endorse or "
            "deploy it. Evaluation evidence is intentionally unavailable until after "
            "nomination. Differences among seeds, missing performance results, or "
            "uncertainty about which seed is best are not reasons to reject. Reject or "
            "abstain only when every supplied seed is structurally invalid, out of "
            "scope, or a known duplicate. Cite only supplied evidence and do not claim "
            "empirical support before deterministic evaluation."),
        "readiness_sha256": recovery["readiness_sha256"],
        "config_sha256": readiness["config_sha256"],
        "schema_sha256": readiness["schema_sha256"],
        "partition_contract_sha256": readiness["partition_contract_sha256"],
        "data_bundle_version": readiness["data_bundle"]["version"],
        "data_bundle_sha256": readiness["data_bundle"]["sha256"],
        "colony": plan.colony, "stage": plan.stage,
        "parent_hypothesis_ids": list(plan.parent_hypothesis_ids),
        "parent_plan_sha256s": list(plan.parent_plan_sha256s),
        "evidence": [{
            "evidence_id": "readiness-" + recovery["readiness_sha256"][:16],
            "scope": "weather_training",
            "summary": (
                "Readiness-bound offline data, evaluator, promotion gates and protected-final "
                "denial passed before this campaign ticket was issued."),
            "artifact_sha256": recovery["readiness_sha256"],
        }, {
            "evidence_id": "evaluation-" + parent["candidate_id"].removeprefix(
                "v3-candidate-"),
            "scope": "development_evaluation",
            "summary": json.dumps(
                _plan_summary(parent), sort_keys=True, allow_nan=False)[:1800],
            "artifact_sha256": parent["artifact_sha256s"]["evaluation_sha256"],
        }],
        "seed_plans": options,
        "budget_remaining": {
            "epoch": 2,
            "epochs_remaining": max(0, budget["maximum_epochs"] - 2),
            "candidate_slots_remaining": max(
                0, budget["maximum_distinct_executed_candidates"]
                - engine["admitted_candidates"]),
            "epoch_candidate_slots_remaining": budget["maximum_new_candidates_per_epoch"],
            "model_calls_remaining": max(
                0, budget["maximum_local_model_calls"] - before_calls),
            "reserved_context_tokens_remaining": max(
                0, budget["local_reserved_context_tokens"] - before_reserved),
            "paid_api_dollars_remaining": 0,
            "wall_seconds_remaining": max(
                0, budget["maximum_wall_seconds"] - engine["elapsed_seconds"]),
        },
    }
    encoded = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")
    return len(encoded), packet


def _verify_trigger(value: Any, campaign: dict[str, Any], archive: dict[str, Any]) -> int:
    trigger = _exact(value, {
        "basis", "task_id", "category", "worker_role", "seed_count",
        "reproduced_canonical_packet_bytes", "independent_observed_packet_bytes",
        "reproduced_canonical_packet_sha256",
        "worker_max_packet_bytes", "worker_context_tokens",
        "worker_generation_tokens", "exception_type", "exception_message",
        "inference_process_started", "epoch2_response_recorded",
        "epoch2_evaluation_recorded", "model_calls_before_attempt",
        "model_calls_after_attempt", "reserved_context_tokens_before_attempt",
        "reserved_context_tokens_after_attempt",
    }, "packet incident trigger")
    packet_bytes, packet = _reproduce_packet(campaign, archive)
    if (not isinstance(trigger["basis"], str) or not trigger["basis"].strip()
            or trigger["task_id"] != "e02-deepen_suppo-01"
            or trigger["category"] != "deepen_supported"
            or trigger["worker_role"] != "synthesizer"
            or trigger["seed_count"] != 6
            or trigger["reproduced_canonical_packet_bytes"] != packet_bytes
            or trigger["reproduced_canonical_packet_sha256"]
            != canonical_hash(packet)
            or trigger["independent_observed_packet_bytes"] != [12_254, 12_255]
            or packet_bytes not in EXPECTED_PACKET_BYTES
            or trigger["worker_max_packet_bytes"] != LOCAL_MAX_PACKET_BYTES
            or packet_bytes <= LOCAL_MAX_PACKET_BYTES
            or trigger["worker_context_tokens"] != LOCAL_CONTEXT_TOKENS
            or trigger["worker_generation_tokens"] != LOCAL_GENERATION_TOKENS
            or trigger["exception_type"] != "V3ResearchProtocolError"
            or trigger["exception_message"]
            != "V3 research packet exceeds byte limit"
            or trigger["inference_process_started"] is not False
            or trigger["epoch2_response_recorded"] is not False
            or trigger["epoch2_evaluation_recorded"] is not False
            or trigger["model_calls_before_attempt"] != 10
            or trigger["model_calls_after_attempt"] != 11
            or trigger["reserved_context_tokens_before_attempt"] != 163_840
            or trigger["reserved_context_tokens_after_attempt"] != 180_224
            or packet["task_id"] != campaign["queue"][0]["task_id"]):
        raise V3PacketIncidentAmendmentError(
            "Packet incident reproduction differs")
    return packet_bytes


def _current_code_inventory(root: Path) -> list[dict[str, Any]]:
    paths = sorted((root / "src/klax_lab").glob("*.py"))
    if not paths:
        raise V3PacketIncidentAmendmentError("Current source package is missing")
    return [{
        "path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in paths]


def _verify_repair_scope(
    root: Path, value: Any, archived_readiness: dict[str, Any],
) -> str:
    repair = _exact(value, {
        "allowed_changes", "changed_code_pre_post", "post_repair_code_inventory",
        "scope_expansion", "repair_selected_only_from_packet_resource_evidence",
        "development_performance_used_to_select_repair",
    }, "packet repair scope")
    if (repair["scope_expansion"] is not False
            or repair["repair_selected_only_from_packet_resource_evidence"] is not True
            or repair["development_performance_used_to_select_repair"] is not False):
        raise V3PacketIncidentAmendmentError("Packet repair independence differs")
    record = _exact(repair["post_repair_code_inventory"], {
        "path", "sha256", "code_sha256",
    }, "packet repair code inventory binding")
    relative, path = _relative(root, record["path"], "packet repair code inventory")
    if relative != CODE_INVENTORY_PATH.as_posix() or not path.is_file():
        raise V3PacketIncidentAmendmentError("Packet repair inventory path differs")
    if sha256_file(path) != _digest(record["sha256"], "packet repair inventory"):
        raise V3PacketIncidentAmendmentError("Packet repair inventory changed")
    body = _exact(_read(path, "packet repair code inventory"), {
        "schema_version", "component", "code_inventory", "code_sha256",
        "network_used", "protected_final_read",
    }, "packet repair code inventory")
    current = _current_code_inventory(root)
    aggregate = canonical_hash(current)
    if (body["schema_version"] != 1
            or body["component"] != CODE_INVENTORY_COMPONENT
            or body["code_inventory"] != current
            or body["code_sha256"] != aggregate
            or record["code_sha256"] != aggregate
            or body["network_used"] is not False
            or body["protected_final_read"] is not False):
        raise V3PacketIncidentAmendmentError("Packet repair inventory differs")
    old_rows = archived_readiness.get("code_inventory")
    if not isinstance(old_rows, list):
        raise V3PacketIncidentAmendmentError("R1 code inventory is missing")
    old = {row.get("path"): row for row in old_rows if isinstance(row, dict)}
    now = {row["path"]: row for row in current}
    if len(old) != len(old_rows) or set(old) - set(now):
        raise V3PacketIncidentAmendmentError("Packet repair removed source files")
    actual = sorted(
        key for key, row in now.items()
        if key not in old or old[key].get("sha256") != row["sha256"])
    allowed = repair["allowed_changes"]
    changes = repair["changed_code_pre_post"]
    if (not isinstance(allowed, list) or allowed != sorted(set(allowed))
            or not isinstance(changes, list)):
        raise V3PacketIncidentAmendmentError("Packet repair file list differs")
    seen: list[str] = []
    for change in changes:
        change = _exact(change, {"path", "before", "after"}, "packet code change")
        item = change["path"]
        if item in seen or item not in now or change["after"] != now[item]["sha256"]:
            raise V3PacketIncidentAmendmentError("Packet code change differs")
        if item in old:
            if change["before"] != old[item].get("sha256"):
                raise V3PacketIncidentAmendmentError("Packet pre-repair hash differs")
        elif change["before"] is not None:
            raise V3PacketIncidentAmendmentError("New packet module has a prior hash")
        if change["before"] == change["after"]:
            raise V3PacketIncidentAmendmentError("Packet code record did not change")
        _digest(change["after"], "packet post-repair code")
        seen.append(item)
    if sorted(seen) != allowed or allowed != actual:
        raise V3PacketIncidentAmendmentError("Allowed packet repair files differ")
    return aggregate


def _verify_science(
    root: Path, value: Any, archive: dict[str, Any], first: dict[str, Any],
    *, expected_champion_ranking_rule_sha256: str,
) -> None:
    contract = _exact(value, _SCIENTIFIC_KEYS, "unchanged scientific contract")
    if any(contract[key] is not False for key in (
            "search_dsl_changed", "target_return_changed", "data_or_labels_changed",
            "gates_or_budget_changed")):
        raise V3PacketIncidentAmendmentError("Scientific contract changed")
    for key in _SCIENTIFIC_KEYS - {
            "data_bundle_version", "search_dsl_changed", "target_return_changed",
            "data_or_labels_changed", "gates_or_budget_changed"}:
        _digest(contract[key], key)
    readiness = archive["readiness"]
    ticket = archive["ticket"]
    bundle = archive["bundle"]
    goal = _read(root / "configs/v3_goal.json", "current V3 goal")
    try:
        from .evaluator_v3 import evaluation_policy_sha256
        current_policy_sha = evaluation_policy_sha256()
    except (ImportError, AttributeError, ValueError) as exc:
        raise V3PacketIncidentAmendmentError(
            "V3 evaluation policy is unavailable") from exc
    ranking_sha = _digest(
        expected_champion_ranking_rule_sha256, "champion ranking rule")
    expected = {
        "config_sha256": sha256_file(root / "configs/v3_goal.json"),
        "schema_sha256": sha256_file(root / "schemas/research-plan-v3.schema.json"),
        "data_bundle_version": bundle.get("dataset_id"),
        "data_bundle_sha256": bundle.get("bundle_sha256"),
        "evaluation_policy_sha256": current_policy_sha,
        "promotion_gates_sha256": canonical_hash(goal["development_promotion_gates"]),
        "campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "partition_contract_sha256": canonical_hash(goal["partitions"]),
        "champion_ranking_rule_sha256": ranking_sha,
    }
    first_contract = first.get("unchanged_scientific_contract")
    if (any(contract[key] != expected[key] for key in expected)
            or any(readiness.get(key) != contract[key] for key in (
                "config_sha256", "schema_sha256", "evaluation_policy_sha256",
                "promotion_gates_sha256", "campaign_budget_sha256",
                "partition_contract_sha256", "champion_ranking_rule_sha256"))
            or readiness.get("data_bundle", {}).get("version")
            != contract["data_bundle_version"]
            or readiness.get("data_bundle", {}).get("sha256")
            != contract["data_bundle_sha256"]
            or any(ticket.get(key) != contract[key] for key in expected)
            or not isinstance(first_contract, dict)
            or any(first_contract.get(key) != contract[key] for key in expected)):
        raise V3PacketIncidentAmendmentError(
            "Scientific contract differs from r1")


def _candidate_import_records(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    index = campaign["artifact_index"]
    result = []
    for candidate_id, raw in sorted(campaign["candidates"].items()):
        plan = ResearchPlanV3.from_dict(raw["plan"])
        if candidate_id != "v3-candidate-" + plan.identity[:20]:
            raise V3PacketIncidentAmendmentError("R1 candidate identity differs")
        primary = raw.get("artifact_sha256s")
        if not isinstance(primary, dict):
            raise V3PacketIncidentAmendmentError("R1 candidate artifacts are missing")
        expected_primary = {
            "compiled_manifest_sha256": "compiled_manifest.json",
            "evaluation_sha256": "evaluation.json",
            "fold_metrics_sha256": "fold_metrics.json",
            "ledger_sha256": "ledger.json",
            "predictions_sha256": "predictions.json",
        }
        for key, name in expected_primary.items():
            path = f"candidates/primary/{plan.identity}/{name}"
            if index.get(path, {}).get("sha256") != primary.get(key):
                raise V3PacketIncidentAmendmentError(
                    "R1 primary candidate artifact identity differs")
        evaluation_path = (
            campaign["campaign_path"] / "candidates" / "primary" /
            plan.identity / "evaluation.json")
        if json.loads(evaluation_path.read_text(encoding="utf-8")) != raw.get(
                "evaluation"):
            raise V3PacketIncidentAmendmentError(
                "R1 recovery evaluation differs from its bound artifact")
        reviews = {
            label + "_sha256": index.get(
                f"reviews/{candidate_id}/{label}.json", {}).get("sha256")
            for label in ("promotion", "replication", "critic")
        }
        if any(_SHA.fullmatch(value or "") is None for value in reviews.values()):
            raise V3PacketIncidentAmendmentError("R1 review identity is missing")
        for label in ("promotion", "replication", "critic"):
            review_path = (
                campaign["campaign_path"] / "reviews" / candidate_id /
                f"{label}.json")
            if json.loads(review_path.read_text(encoding="utf-8")) != raw.get(label):
                raise V3PacketIncidentAmendmentError(
                    f"R1 recovery {label} differs from its bound artifact")
        result.append({
            "candidate_id": candidate_id,
            "plan_sha256": plan.identity,
            "novelty_fingerprint": plan.novelty_fingerprint,
            "epoch": raw["epoch"],
            "discovery_worker_id": raw["discovery_worker_id"],
            "primary_artifact_sha256s": primary,
            **reviews,
        })
    return result


def _verify_import_state(
    root: Path, record: Any, campaign: dict[str, Any], archive: dict[str, Any],
    first: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    record = _exact(record, {"path", "sha256", "state_sha256"},
                    "continuation import-state binding")
    relative, path = _relative(root, record["path"], "continuation import state")
    if relative != IMPORT_STATE_PATH.as_posix() or not path.is_file():
        raise V3PacketIncidentAmendmentError("Continuation import-state path differs")
    if sha256_file(path) != _digest(record["sha256"], "continuation import state"):
        raise V3PacketIncidentAmendmentError("Continuation import state changed")
    value = _exact(_read(path, "continuation import state"), {
        "schema_version", "component", "mode", "source_campaign_id",
        "source_campaign_artifacts_sha256", "source_recovery_sha256",
        "source_coverage_sha256", "candidates", "epoch_2_allocation",
        "epoch_2_queue_sha256", "epoch_2_queue_task_ids", "counters",
        "next_queue_index", "novelty_index_sha256", "diagnostic_quarantine",
        "continuation_rebuild", "failed_packet_sha256",
        "no_epoch_2_response_or_evaluation", "state_sha256", "network_used",
        "protected_final_read", "actual_orders_placed",
    }, "continuation import state")
    body = {key: item for key, item in value.items() if key != "state_sha256"}
    candidates = _candidate_import_records(campaign)
    allocation = campaign["coverage"]["allocation_decisions"]
    queue = campaign["queue"]
    engine = campaign["engine"]
    diagnostic = first["diagnostic_quarantine"]["manifest"]
    expected_counters = {
        "current_epoch": 2, "admitted_candidates": 10,
        "executed_candidates": 10, "model_calls": 11,
        "model_context_tokens_reserved": 180_224, "elapsed_seconds": 167,
        "epoch_admissions": {"1": 10, "2": 0},
    }
    expected_diagnostic = {
        "manifest": diagnostic,
        "excluded_from_continuation_parenting_and_ranking": True,
        "r1_campaign_results_remain_legitimate_imported_adaptive_evidence": True,
    }
    plans = [item["plan_sha256"] for item in candidates]
    eligible = sorted(
        item for item in plans if item != DENIED_DIAGNOSTIC_PLAN_SHA256)
    expected_rebuild = {
        "required": True,
        "source_allocation_and_queue_preserved_for_audit": True,
        "source_allocation_or_queue_reusable": False,
        "rebuild_from_eligible_imported_candidates": True,
        "denied_plan_sha256s": [DENIED_DIAGNOSTIC_PLAN_SHA256],
        "eligible_parent_and_ranking_plan_sha256s": eligible,
        "preserve_source_counters_and_spent_budget": True,
    }
    _, failed_packet = _reproduce_packet(campaign, archive)
    if (value["schema_version"] != 1
            or value["component"] != IMPORT_STATE_COMPONENT
            or value["mode"] != CONTINUATION_MODE
            or value["source_campaign_id"] != FAILED_CAMPAIGN_ID
            or value["source_campaign_artifacts_sha256"]
            != CAMPAIGN_ARTIFACTS_SHA256
            or value["source_recovery_sha256"] != RECOVERY_SHA256
            or value["source_coverage_sha256"] != COVERAGE_SHA256
            or value["candidates"] != candidates
            or value["epoch_2_allocation"] != allocation
            or value["epoch_2_queue_sha256"] != canonical_hash(queue)
            or value["epoch_2_queue_task_ids"]
            != [item["task_id"] for item in queue]
            or value["counters"] != expected_counters
            or value["next_queue_index"] != 0
            or value["novelty_index_sha256"]
            != canonical_hash(engine["novelty_index"])
            or value["diagnostic_quarantine"] != expected_diagnostic
            or value["continuation_rebuild"] != expected_rebuild
            or value["failed_packet_sha256"] != canonical_hash(failed_packet)
            or value["no_epoch_2_response_or_evaluation"] is not True
            or value["network_used"] is not False
            or value["protected_final_read"] is not False
            or value["actual_orders_placed"] is not False
            or value["state_sha256"] != canonical_hash(body)
            or record["state_sha256"] != value["state_sha256"]):
        raise V3PacketIncidentAmendmentError(
            "Continuation import state differs from immutable r1 evidence")
    _, diagnostic_path = _relative(root, diagnostic["path"], "diagnostic quarantine")
    if (not diagnostic_path.is_file()
            or sha256_file(diagnostic_path) != diagnostic["sha256"]):
        raise V3PacketIncidentAmendmentError("Diagnostic quarantine changed")
    return value, plans


def _verify_observed_policy(value: Any, plans: list[str]) -> None:
    policy = _exact(value, {
        "source_campaign_id", "observed_plan_sha256s",
        "denied_plan_sha256s", "eligible_parent_and_ranking_plan_sha256s",
        "continuation_imports_as_legitimate_adaptive_evidence",
        "fresh_restart_denylist_candidates_seeds_parents_and_ranking",
    }, "observed-plan policy")
    eligible = sorted(
        item for item in plans if item != DENIED_DIAGNOSTIC_PLAN_SHA256)
    if (policy["source_campaign_id"] != FAILED_CAMPAIGN_ID
            or policy["observed_plan_sha256s"] != sorted(plans)
            or len(plans) != 10 or len(set(plans)) != 10
            or DENIED_DIAGNOSTIC_PLAN_SHA256 not in plans
            or policy["denied_plan_sha256s"]
            != [DENIED_DIAGNOSTIC_PLAN_SHA256]
            or policy["eligible_parent_and_ranking_plan_sha256s"] != eligible
            or policy["continuation_imports_as_legitimate_adaptive_evidence"] is not True
            or policy["fresh_restart_denylist_candidates_seeds_parents_and_ranking"]
            is not True):
        raise V3PacketIncidentAmendmentError("Observed-plan policy differs")


def _verify_diagnostic(value: Any, first: dict[str, Any]) -> None:
    expected = {
        "manifest": first["diagnostic_quarantine"]["manifest"],
        "excluded_from_continuation_parenting_and_ranking": True,
        "excluded_from_promotion_or_protected_final_authorization": True,
        "performance_used_to_select_packet_repair": False,
    }
    if value != expected:
        raise V3PacketIncidentAmendmentError(
            "Earlier diagnostic quarantine policy differs")


def _verify_replacement(root: Path, value: Any, *, pre_issuance: bool) -> dict[str, str]:
    replacement = _exact(value, {
        "mode", "campaign_id", "readiness_path", "ticket_path", "claim_path",
        "must_differ_from_source_campaign_id", "must_not_mutate_source_campaign",
        "must_use_fresh_readiness_and_one_use_ticket",
        "must_bind_this_amendment_and_import_state",
        "must_preserve_exact_source_state_and_rebuild_epoch_2_queue",
        "original_epoch_2_allocation_or_queue_reusable",
        "claim_path_must_be_derived_from_ticket_path",
        "prelaunch_claim_path_must_not_exist", "source_ticket_or_claim_reusable",
    }, "successor constraints")
    campaign_id = _identifier(replacement["campaign_id"], "successor campaign_id")
    readiness_relative, readiness = _relative(
        root, replacement["readiness_path"], "successor readiness")
    ticket_relative, ticket = _relative(
        root, replacement["ticket_path"], "successor ticket")
    claim_relative, claim = _relative(
        root, replacement["claim_path"], "successor claim")
    true_fields = {
        "must_differ_from_source_campaign_id", "must_not_mutate_source_campaign",
        "must_use_fresh_readiness_and_one_use_ticket",
        "must_bind_this_amendment_and_import_state",
        "must_preserve_exact_source_state_and_rebuild_epoch_2_queue",
        "claim_path_must_be_derived_from_ticket_path",
        "prelaunch_claim_path_must_not_exist",
    }
    if (replacement["mode"] != CONTINUATION_MODE
            or campaign_id == FAILED_CAMPAIGN_ID
            or any(replacement[key] is not True for key in true_fields)
            or replacement["original_epoch_2_allocation_or_queue_reusable"] is not False
            or replacement["source_ticket_or_claim_reusable"] is not False
            or claim_relative != ticket_relative + ".claimed.json"):
        raise V3PacketIncidentAmendmentError("Successor constraints differ")
    if pre_issuance and (readiness.exists() or ticket.exists() or claim.exists()
            or (root / "runs/campaigns_v3" / campaign_id).exists()):
        raise V3PacketIncidentAmendmentError(
            "Successor authorization or campaign already exists")
    return {
        "campaign_id": campaign_id, "readiness_path": readiness_relative,
        "ticket_path": ticket_relative, "claim_path": claim_relative,
    }


def verify_campaign2_packet_repair_amendment(
    root: Path | str, *, expected_champion_ranking_rule_sha256: str,
    pre_issuance: bool = False,
) -> dict[str, Any]:
    """Verify the immutable r1 state and its budget-preserving successor."""
    root = Path(root).resolve()
    amendment_path = root / AMENDMENT_PATH
    amendment = _exact(
        _read(amendment_path, "packet repair amendment"), _TOP_KEYS,
        "packet repair amendment")
    registered = _timestamp(amendment["registered_at_utc"], "packet amendment registration")
    amendment_id = _identifier(amendment["amendment_id"], "packet amendment_id")
    if (amendment["schema_version"] != 1
            or amendment["component"] != AMENDMENT_COMPONENT
            or amendment["status"] != AMENDMENT_STATUS
            or amendment["network_used"] is not False
            or amendment["protected_final_read"] is not False
            or amendment["actual_orders_placed"] is not False):
        raise V3PacketIncidentAmendmentError("Packet repair amendment differs")
    first = _verify_first_amendment(root, amendment["first_amendment"])
    campaign = _verify_campaign(root, amendment["failed_campaign"])
    archive = _verify_archive(root, amendment["failed_campaign"])
    packet_bytes = _verify_trigger(amendment["trigger_evidence"], campaign, archive)
    _verify_science(
        root, amendment["unchanged_scientific_contract"], archive, first,
        expected_champion_ranking_rule_sha256=(
            expected_champion_ranking_rule_sha256),
    )
    code_sha = _verify_repair_scope(root, amendment["repair_scope"], archive["readiness"])
    import_state, plans = _verify_import_state(
        root, amendment["continuation_import_state"], campaign, archive, first)
    _verify_observed_policy(amendment["observed_plan_policy"], plans)
    _verify_diagnostic(amendment["diagnostic_quarantine"], first)
    replacement = _verify_replacement(
        root, amendment["replacement_constraints"], pre_issuance=pre_issuance)
    completed = _timestamp(
        amendment["failed_campaign"]["completed_at_utc"], "r1 completion")
    if not completed < registered:
        raise V3PacketIncidentAmendmentError(
            "Packet amendment was not registered after the r1 stop")
    digest = sha256_file(amendment_path)
    return {
        "path": AMENDMENT_PATH.as_posix(), "sha256": digest,
        "amendment_id": amendment_id, "registered_at_utc": registered.isoformat(),
        "source_campaign_id": FAILED_CAMPAIGN_ID,
        "source_campaign_artifacts_sha256": CAMPAIGN_ARTIFACTS_SHA256,
        "continuation_import_state_path": IMPORT_STATE_PATH.as_posix(),
        "continuation_import_state_sha256":
            amendment["continuation_import_state"]["sha256"],
        "continuation_state_sha256": import_state["state_sha256"],
        "replacement_campaign_id": replacement["campaign_id"],
        "replacement_readiness_path": replacement["readiness_path"],
        "replacement_ticket_path": replacement["ticket_path"],
        "replacement_claim_path": replacement["claim_path"],
        "observed_plan_sha256s": sorted(plans),
        "denied_plan_sha256s": [DENIED_DIAGNOSTIC_PLAN_SHA256],
        "eligible_parent_and_ranking_plan_sha256s": sorted(
            item for item in plans if item != DENIED_DIAGNOSTIC_PLAN_SHA256),
        "reproduced_packet_bytes": packet_bytes,
        "post_repair_code_sha256": code_sha,
    }
