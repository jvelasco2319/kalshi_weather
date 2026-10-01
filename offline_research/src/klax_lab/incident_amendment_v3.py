"""Fail-closed registration for the first V3 campaign integrity repair.

The first real V3 campaign stopped before producing performance evidence when
an endpoint market quote reached the ordinary ROI primitive.  A replacement
campaign is allowed only through the immutable, pre-registered amendment
verified here.  This module is intentionally read-only: it never creates an
amendment, readiness artifact, ticket, claim, or campaign output.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .provenance import canonical_hash, sha256_file


AMENDMENT_PATH = Path(
    "data/manifests/v3_campaign1_integrity_repair_amendment.json")
CODE_INVENTORY_PATH = Path(
    "data/manifests/v3_campaign1_integrity_repair_code_inventory.json")
FAILED_CAMPAIGN_ID = "v3-offline-20260926T151559358Z"
FAILED_CAMPAIGN_PATH = Path("runs/campaigns_v3") / FAILED_CAMPAIGN_ID
ARCHIVE_MANIFEST_PATH = Path(
    "runs/authorization_archive/v3-offline-20260926T151559358Z-stale/"
    "archive-manifest.json")
ARCHIVE_MANIFEST_SHA256 = (
    "8aaae35b42dad565497913459dba222752c024aa663aeca0670fce1183e06ffa")
DIAGNOSTIC_MANIFEST_PATH = Path(
    "runs/diagnostics/v3-offline-20260926T151559358Z-repair-verification/"
    "diagnostic-manifest.json")
DIAGNOSTIC_MANIFEST_SHA256 = (
    "7b4b1a2281917fb3dcc8cb31157a8433763a668a89be4651da8a77dd815e44dc")

AMENDMENT_COMPONENT = "v3_campaign_integrity_incident_amendment"
AMENDMENT_STATUS = (
    "REGISTERED_AFTER_FAILED_CAMPAIGN_AND_REPAIR_VALIDATION_BEFORE_"
    "REPLACEMENT_READINESS_OR_TICKET")
CODE_INVENTORY_COMPONENT = "v3_campaign_integrity_repair_code_inventory"
FAILED_CONCLUSION = "INSUFFICIENT_EVIDENCE"
FAILED_STOP_REASON = (
    "required_data_integrity_replication_critic_or_resource_boundary_failure")
DIAGNOSTIC_COMPONENT = "v3_integrity_repair_diagnostic_quarantine"
DIAGNOSTIC_STATUS = "QUARANTINED_DIAGNOSTIC_NOT_CAMPAIGN_EVIDENCE"
OWNER_ATTESTATION = (
    "Repairs were selected from the preserved evaluator exception and "
    "worker-response/schema evidence. The quarantined diagnostic was used "
    "only to verify mechanical evaluator execution and was not used to select "
    "or modify scientific parameters, candidates, rankings, seeds, parents, "
    "gates, partitions, budgets, or protected-final policy.")

EXPECTED_DIAGNOSTIC_CODE_BINDING = {
    "src/klax_lab/campaign_v3.py":
        "21387dc990efce82edd174b54388670289c38d2500e72c51ef28d714e6b471d7",
    "src/klax_lab/evaluator_v3.py":
        "8e9632c8bdcb34fd69dca910736a61ec55d919a1d3ebb22bfbdb8c3a0ad67967",
    "src/klax_lab/market_policy_v3.py":
        "9cb6d12ecfcb4ceb28ec3c8469f6cce5daac912b13a62feb6d67cc9a3a4eddc3",
    "src/klax_lab/research_plan_v3.py":
        "37de8aaf7659bb7e6c5ab49636a9242db6a5144bc598b4534a71d432f8d71bcb",
}

_SHA = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
_TOP_KEYS = {
    "schema_version", "component", "amendment_id", "status",
    "registered_at_utc", "failed_campaign", "trigger_evidence",
    "unchanged_scientific_contract", "repair_scope",
    "diagnostic_quarantine", "scientific_independence",
    "replacement_constraints", "network_used", "protected_final_read",
    "actual_orders_placed",
}
_FAILED_KEYS = {
    "campaign_id", "campaign_path", "completed_at_utc",
    "scientific_conclusion", "stop_reason", "executed_candidates",
    "campaign_artifacts", "summary", "recovery_state", "search_coverage",
    "report", "candidate_register", "archived_authorization",
    "network_used", "protected_final_read", "actual_orders_placed",
}
_SCIENTIFIC_KEYS = {
    "config_sha256", "schema_sha256", "data_bundle_version",
    "data_bundle_sha256", "evaluation_policy_sha256",
    "promotion_gates_sha256", "campaign_budget_sha256",
    "partition_contract_sha256", "champion_ranking_rule_sha256",
    "search_dsl_changed", "target_return_changed", "data_or_labels_changed",
    "gates_or_budget_changed",
}
_INDEPENDENCE_KEYS = {
    "repairs_triggered_only_by_integrity_or_protocol_defects",
    "evaluator_repair_fixed_before_diagnostic_run",
    "diagnostic_results_existed_before_later_protocol_edits",
    "later_protocol_edits_derived_from_saved_worker_and_schema_evidence",
    "diagnostic_performance_used_to_select_or_modify_scientific_parameters",
    "diagnostic_evidence_used_for_candidate_generation",
    "diagnostic_evidence_used_for_candidate_ranking",
    "diagnostic_evidence_used_for_seed_or_parent_selection",
    "development_or_protected_results_used_to_change_scientific_contract",
    "protected_final_results_observed", "owner_attestation",
}


class V3IncidentAmendmentError(ValueError):
    """The replacement-campaign incident registration is missing or changed."""


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise V3IncidentAmendmentError(f"{label} fields differ")
    return value


def _read(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=lambda item: (_ for _ in ()).throw(
                V3IncidentAmendmentError(
                    f"Non-finite JSON in {label}: {item}")),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V3IncidentAmendmentError(
            f"Missing or invalid V3 campaign incident {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3IncidentAmendmentError(f"{label} must contain one object")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise V3IncidentAmendmentError(f"Invalid {label} SHA-256")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise V3IncidentAmendmentError(f"Invalid {label}")
    return value


def _text(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 4000
            or any(ord(char) < 32 and char not in "\n\t" for char in value)):
        raise V3IncidentAmendmentError(f"Invalid {label}")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise V3IncidentAmendmentError(f"Invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3IncidentAmendmentError(f"Invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3IncidentAmendmentError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _relative(root: Path, value: Any, label: str) -> tuple[str, Path]:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value):
        raise V3IncidentAmendmentError(f"Invalid {label} path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise V3IncidentAmendmentError(f"{label} path escapes the project")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise V3IncidentAmendmentError(
            f"{label} path escapes the project") from exc
    if {part.casefold().replace("-", "_") for part in target.parts} & {
            "protected_final", "holdout"}:
        raise V3IncidentAmendmentError(
            f"{label} path enters protected storage")
    return relative.as_posix(), target


def _file_record(root: Path, value: Any, label: str) -> tuple[str, Path]:
    record = _exact(value, {"path", "sha256"}, label)
    relative, path = _relative(root, record["path"], label)
    digest = _sha(record["sha256"], label)
    if not path.is_file() or sha256_file(path) != digest:
        raise V3IncidentAmendmentError(f"{label} changed")
    return relative, path


def _verify_campaign_artifacts(root: Path, failed: dict[str, Any]) -> dict[str, Any]:
    campaign_path, campaign = _relative(
        root, failed["campaign_path"], "failed campaign")
    if (failed["campaign_id"] != FAILED_CAMPAIGN_ID
            or campaign_path != FAILED_CAMPAIGN_PATH.as_posix()
            or not campaign.is_dir()):
        raise V3IncidentAmendmentError("Failed campaign identity differs")

    artifacts_record = _exact(failed["campaign_artifacts"], {
        "path", "sha256", "manifest_sha256", "artifact_count",
    }, "failed campaign artifact manifest")
    expected_artifacts_path = (FAILED_CAMPAIGN_PATH / "campaign-artifacts.json").as_posix()
    relative, artifacts_path = _relative(
        root, artifacts_record["path"], "failed campaign artifact manifest")
    if relative != expected_artifacts_path or not artifacts_path.is_file():
        raise V3IncidentAmendmentError("Failed campaign artifact path differs")
    if sha256_file(artifacts_path) != _sha(
            artifacts_record["sha256"], "failed campaign artifact file"):
        raise V3IncidentAmendmentError("Failed campaign artifact file changed")
    artifacts = _read(artifacts_path, "campaign artifact manifest")
    manifest_body = {key: value for key, value in artifacts.items()
                     if key != "manifest_sha256"}
    rows = artifacts.get("artifacts")
    if (artifacts.get("campaign_id") != FAILED_CAMPAIGN_ID
            or artifacts.get("network_used") is not False
            or artifacts.get("protected_final_read") is not False
            or artifacts.get("actual_orders_placed") is not False
            or canonical_hash(manifest_body) != artifacts.get("manifest_sha256")
            or artifacts.get("manifest_sha256") != _sha(
                artifacts_record["manifest_sha256"],
                "failed campaign artifact manifest")
            or not isinstance(rows, list)
            or type(artifacts_record["artifact_count"]) is not int
            or artifacts_record["artifact_count"] != len(rows)):
        raise V3IncidentAmendmentError("Failed campaign artifact manifest differs")
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = _exact(row, {"path", "bytes", "sha256"}, "campaign artifact")
        relative_path = row["path"]
        if (not isinstance(relative_path, str) or relative_path in indexed
                or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise V3IncidentAmendmentError("Malformed campaign artifact inventory")
        _, target = _relative(
            root, f"{campaign_path}/{relative_path}", "campaign artifact")
        digest = _sha(row["sha256"], "campaign artifact")
        if (not target.is_file() or target.stat().st_size != row["bytes"]
                or sha256_file(target) != digest):
            raise V3IncidentAmendmentError(
                f"Failed campaign artifact changed: {relative_path}")
        indexed[relative_path] = row

    expected_files = {
        "summary": "summary.json", "recovery_state": "recovery-state.json",
        "search_coverage": "search-coverage.json", "report": "report.md",
        "candidate_register": "candidate-register.json",
    }
    paths: dict[str, Path] = {}
    for key, name in expected_files.items():
        relative_path, target = _file_record(root, failed[key], f"failed {key}")
        expected = (FAILED_CAMPAIGN_PATH / name).as_posix()
        if relative_path != expected or indexed.get(name, {}).get("sha256") != failed[key]["sha256"]:
            raise V3IncidentAmendmentError(f"Failed campaign {key} binding differs")
        paths[key] = target

    summary = _read(paths["summary"], "failed campaign summary")
    recovery = _read(paths["recovery_state"], "failed campaign recovery state")
    coverage = _read(paths["search_coverage"], "failed campaign search coverage")
    candidates = json.loads(paths["candidate_register"].read_text(encoding="utf-8"))
    if (summary.get("campaign_id") != FAILED_CAMPAIGN_ID
            or summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("scientific_conclusion") != FAILED_CONCLUSION
            or summary.get("stopped_reason") != FAILED_STOP_REASON
            or summary.get("budget_used", {}).get("executed_candidates") != 0
            or summary.get("protected_final_evaluated") is not False
            or summary.get("actual_orders_placed") is not False
            or summary.get("protected_final_authorization_issued") is not False
            or failed["completed_at_utc"] != summary.get("completed_at_utc")
            or failed["scientific_conclusion"] != FAILED_CONCLUSION
            or failed["stop_reason"] != FAILED_STOP_REASON
            or failed["executed_candidates"] != 0):
        raise V3IncidentAmendmentError("Failed campaign summary differs")
    state_body = {key: value for key, value in recovery.items()
                  if key != "state_sha256"}
    if (recovery.get("campaign_id") != FAILED_CAMPAIGN_ID
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("engine", {}).get("stopped_reason") != FAILED_STOP_REASON
            or recovery.get("protected_final_evaluated") is not False
            or recovery.get("actual_orders_placed") is not False
            or canonical_hash(state_body) != recovery.get("state_sha256")):
        raise V3IncidentAmendmentError("Failed campaign recovery state differs")
    if (coverage.get("integrity_failures") is None
            or len(coverage.get("integrity_failures", [])) != 1
            or coverage["integrity_failures"][0].get("stage") != "primary_evaluation"
            or coverage["integrity_failures"][0].get("exception_type") != "ValueError"):
        raise V3IncidentAmendmentError("Failed campaign integrity evidence differs")
    if (not isinstance(candidates, list) or len(candidates) != 1
            or candidates[0].get("candidate_id")
            != "v3-candidate-f5f2fd485512dbcb45e0"):
        raise V3IncidentAmendmentError("Failed campaign candidate register differs")
    return {
        "summary": summary, "recovery": recovery, "coverage": coverage,
        "artifact_index": indexed,
    }


def _verify_archive(root: Path, failed: dict[str, Any]) -> dict[str, Any]:
    authorization = _exact(failed["archived_authorization"], {
        "manifest_path", "manifest_sha256", "ticket_path", "ticket_sha256",
        "claim_path", "claim_sha256", "bootstrap_chain",
    }, "archived authorization")
    manifest_relative, manifest_path = _relative(
        root, authorization["manifest_path"], "authorization archive manifest")
    if (manifest_relative != ARCHIVE_MANIFEST_PATH.as_posix()
            or authorization["manifest_sha256"] != ARCHIVE_MANIFEST_SHA256
            or not manifest_path.is_file()
            or sha256_file(manifest_path) != ARCHIVE_MANIFEST_SHA256):
        raise V3IncidentAmendmentError("Authorization archive manifest differs")
    archive = _read(manifest_path, "authorization archive manifest")
    required_originals = {
        "data/manifests/v3_readiness.json",
        "data/manifests/v3_data_bundle.json",
        "runs/v3_offline_campaign_ticket.json",
        "runs/v3_offline_campaign_ticket.json.claimed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
        "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
        "runs/weather_v3_campaign.id",
    }
    rows = archive.get("files")
    if (archive.get("archive_version") != "klax-v3-stale-authorization-archive-v1"
            or archive.get("failed_campaign_id") != FAILED_CAMPAIGN_ID
            or archive.get("preserved_campaign_path") != FAILED_CAMPAIGN_PATH.as_posix()
            or archive.get("status")
            != "ARCHIVED_BYTE_FOR_BYTE_BEFORE_REPLACEMENT_AUTHORIZATION"
            or archive.get("protected_final_read") is not False
            or not isinstance(rows, list)):
        raise V3IncidentAmendmentError("Authorization archive semantics differ")
    by_original: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = _exact(
            row, {"archived_path", "bytes", "original_path", "sha256"},
            "authorization archive file")
        original = row["original_path"]
        if (not isinstance(original, str) or original in by_original
                or type(row["bytes"]) is not int or row["bytes"] < 0):
            raise V3IncidentAmendmentError("Authorization archive inventory differs")
        _, target = _relative(root, row["archived_path"], "archived authorization")
        digest = _sha(row["sha256"], "archived authorization")
        if (not target.is_file() or target.stat().st_size != row["bytes"]
                or sha256_file(target) != digest):
            raise V3IncidentAmendmentError(
                f"Archived authorization file changed: {original}")
        by_original[original] = {**row, "target": target}
    if set(by_original) != required_originals:
        raise V3IncidentAmendmentError("Authorization archive file set differs")

    ticket_row = by_original["runs/v3_offline_campaign_ticket.json"]
    claim_row = by_original["runs/v3_offline_campaign_ticket.json.claimed.json"]
    if (authorization["ticket_path"] != ticket_row["archived_path"]
            or authorization["ticket_sha256"] != ticket_row["sha256"]
            or authorization["claim_path"] != claim_row["archived_path"]
            or authorization["claim_sha256"] != claim_row["sha256"]):
        raise V3IncidentAmendmentError("Archived ticket or claim binding differs")

    transition_originals = {
        "BASE": "runs/v3_offline_campaign_ticket.json.bootstrap.json",
        "CLAIMED": "runs/v3_offline_campaign_ticket.json.bootstrap.claimed.json",
        "RECOVERY_CONSUMED":
            "runs/v3_offline_campaign_ticket.json.bootstrap.recovery-consumed.json",
        "COMMITTED": "runs/v3_offline_campaign_ticket.json.bootstrap.committed.json",
    }
    chain = authorization["bootstrap_chain"]
    if not isinstance(chain, list) or [row.get("transition") for row in chain] != list(
            transition_originals):
        raise V3IncidentAmendmentError("Archived bootstrap transition order differs")
    for row in chain:
        row = _exact(row, {"transition", "path", "sha256"}, "bootstrap transition")
        archived = by_original[transition_originals[row["transition"]]]
        if row["path"] != archived["archived_path"] or row["sha256"] != archived["sha256"]:
            raise V3IncidentAmendmentError("Archived bootstrap binding differs")
        body = _read(archived["target"], "archived bootstrap transition")
        expected_status = "PREPARED" if row["transition"] == "BASE" else row["transition"]
        if (body.get("campaign_id") != FAILED_CAMPAIGN_ID
                or body.get("status") != expected_status
                or body.get("protected_final_evaluated") is not False
                or body.get("actual_orders_placed") is not False):
            raise V3IncidentAmendmentError("Archived bootstrap semantics differ")

    readiness = _read(
        by_original["data/manifests/v3_readiness.json"]["target"],
        "archived readiness")
    bundle = _read(
        by_original["data/manifests/v3_data_bundle.json"]["target"],
        "archived data bundle")
    ticket = _read(ticket_row["target"], "archived ticket")
    claim = _read(claim_row["target"], "archived claim")
    if (ticket.get("campaign_id") != FAILED_CAMPAIGN_ID
            or ticket.get("one_use") is not True
            or ticket.get("synthetic") is not False
            or claim.get("campaign_id") != FAILED_CAMPAIGN_ID
            or claim.get("ticket_sha256") != ticket_row["sha256"]
            or claim.get("readiness_sha256")
            != by_original["data/manifests/v3_readiness.json"]["sha256"]):
        raise V3IncidentAmendmentError("Archived one-use authorization differs")
    return {"readiness": readiness, "bundle": bundle, "ticket": ticket,
            "by_original": by_original}


def _verify_trigger(root: Path, trigger: Any, campaign: dict[str, Any]) -> None:
    trigger = _exact(trigger, {
        "basis", "failed_campaign_performance_metrics_available",
        "proposal_protocol_defect", "primary_evaluator_integrity_defect",
    }, "incident trigger evidence")
    _text(trigger["basis"], "incident basis")
    proposal = _exact(trigger["proposal_protocol_defect"], {
        "accepted_nonproposal_responses", "admitted_proposals", "finding",
        "response_sha256s",
    }, "proposal protocol defect")
    if (trigger["failed_campaign_performance_metrics_available"] is not False
            or proposal["accepted_nonproposal_responses"] != 7
            or proposal["admitted_proposals"] != 1):
        raise V3IncidentAmendmentError("Incident trigger counts differ")
    _text(proposal["finding"], "proposal protocol finding")
    responses = proposal["response_sha256s"]
    queue = campaign["recovery"].get("queue", [])
    expected = []
    for row in queue:
        if row.get("status") == "NONPROPOSAL":
            response = root / FAILED_CAMPAIGN_PATH / "tasks" / row["task_id"] / "response.json"
            if not response.is_file():
                raise V3IncidentAmendmentError("Failed worker response is missing")
            expected.append(sha256_file(response))
    if (not isinstance(responses, list) or len(responses) != len(set(responses))
            or any(_SHA.fullmatch(value) is None for value in responses
                   if isinstance(value, str))
            or any(not isinstance(value, str) for value in responses)
            or sorted(responses) != sorted(expected)):
        raise V3IncidentAmendmentError("Failed worker response hashes differ")

    primary = _exact(trigger["primary_evaluator_integrity_defect"], {
        "exception_type", "finding", "reproduced_case",
    }, "primary evaluator integrity defect")
    _text(primary["finding"], "primary evaluator finding")
    reproduced = _exact(primary["reproduced_case"], {
        "settlement_date", "ticker", "side", "observed_at_utc",
        "yes_bid_dollars", "yes_ask_dollars", "derived_entry_price_dollars",
        "exception_type", "exception_message", "required_disposition",
    }, "primary evaluator reproduced case")
    expected_case = {
        "settlement_date": "2025-02-07",
        "ticker": "KXHIGHLAX-25FEB07-T56",
        "side": "NO",
        "observed_at_utc": "2025-02-07T07:00:00+00:00",
        "yes_bid_dollars": "0.0000",
        "yes_ask_dollars": "0.0300",
        "derived_entry_price_dollars": "1.0000",
        "exception_type": "ValueError",
        "exception_message": "entry price must be strictly between $0 and $1",
        "required_disposition": "ENTRY_PRICE_OUTSIDE_CONTROL_BAND",
    }
    if primary["exception_type"] != "ValueError" or reproduced != expected_case:
        raise V3IncidentAmendmentError("Primary evaluator reproduced case differs")


def _current_code_inventory(root: Path) -> list[dict[str, Any]]:
    paths = sorted((root / "src/klax_lab").glob("*.py"))
    if not paths:
        raise V3IncidentAmendmentError("Current source package is missing")
    return [{
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in paths]


def _verify_code_inventory(root: Path, record: Any) -> tuple[list[dict[str, Any]], str]:
    record = _exact(
        record, {"path", "sha256", "code_sha256"},
        "post-repair code inventory binding")
    relative, path = _relative(
        root, record["path"], "post-repair code inventory")
    if relative != CODE_INVENTORY_PATH.as_posix() or not path.is_file():
        raise V3IncidentAmendmentError("Post-repair code inventory path differs")
    file_sha = _sha(record["sha256"], "post-repair code inventory")
    if sha256_file(path) != file_sha:
        raise V3IncidentAmendmentError("Post-repair code inventory changed")
    body = _read(path, "post-repair code inventory")
    body = _exact(body, {
        "schema_version", "component", "code_inventory", "code_sha256",
        "protected_final_read", "network_used",
    }, "post-repair code inventory")
    current = _current_code_inventory(root)
    aggregate = canonical_hash(current)
    if (body["schema_version"] != 1
            or body["component"] != CODE_INVENTORY_COMPONENT
            or body["code_inventory"] != current
            or body["code_sha256"] != aggregate
            or record["code_sha256"] != aggregate
            or body["protected_final_read"] is not False
            or body["network_used"] is not False):
        raise V3IncidentAmendmentError("Post-repair code inventory differs")
    return current, aggregate


def _verify_scientific_contract(
    root: Path,
    value: Any,
    archive: dict[str, Any],
    *,
    expected_champion_ranking_rule_sha256: str,
) -> None:
    contract = _exact(value, _SCIENTIFIC_KEYS, "unchanged scientific contract")
    for key in _SCIENTIFIC_KEYS - {
            "data_bundle_version", "search_dsl_changed", "target_return_changed",
            "data_or_labels_changed", "gates_or_budget_changed"}:
        _sha(contract[key], key)
    if any(contract[key] is not False for key in (
            "search_dsl_changed", "target_return_changed", "data_or_labels_changed",
            "gates_or_budget_changed")):
        raise V3IncidentAmendmentError("Scientific contract change is not permitted")

    readiness = archive["readiness"]
    ticket = archive["ticket"]
    bundle = archive["bundle"]
    config_path = root / "configs/v3_goal.json"
    schema_path = root / "schemas/research-plan-v3.schema.json"
    current_bundle_path = root / "data/manifests/v3_data_bundle.json"
    goal = _read(config_path, "current V3 goal")
    current_bundle = _read(current_bundle_path, "current V3 data bundle")
    try:
        from .evaluator_v3 import evaluation_policy_sha256
        evaluator_policy = evaluation_policy_sha256()
    except (ImportError, AttributeError, ValueError) as exc:
        raise V3IncidentAmendmentError(
            "Current V3 evaluation policy is unavailable") from exc
    expected = {
        "config_sha256": sha256_file(config_path),
        "schema_sha256": sha256_file(schema_path),
        "data_bundle_version": current_bundle.get("dataset_id"),
        "data_bundle_sha256": current_bundle.get("bundle_sha256"),
        "evaluation_policy_sha256": evaluator_policy,
        "promotion_gates_sha256": canonical_hash(goal["development_promotion_gates"]),
        "campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "partition_contract_sha256": canonical_hash(goal["partitions"]),
        "champion_ranking_rule_sha256": expected_champion_ranking_rule_sha256,
    }
    if any(contract[key] != expected[key] for key in expected):
        raise V3IncidentAmendmentError("Current scientific contract differs")
    archived_expected = {
        "config_sha256": readiness.get("config_sha256"),
        "schema_sha256": readiness.get("schema_sha256"),
        "data_bundle_version": readiness.get("data_bundle", {}).get("version"),
        "data_bundle_sha256": readiness.get("data_bundle", {}).get("sha256"),
        "evaluation_policy_sha256": readiness.get("evaluation_policy_sha256"),
        "promotion_gates_sha256": readiness.get("promotion_gates_sha256"),
        "campaign_budget_sha256": readiness.get("campaign_budget_sha256"),
        "partition_contract_sha256": readiness.get("partition_contract_sha256"),
        "champion_ranking_rule_sha256": readiness.get(
            "champion_ranking_rule_sha256"),
    }
    if (any(contract[key] != archived_expected[key] for key in archived_expected)
            or bundle.get("dataset_id") != contract["data_bundle_version"]
            or bundle.get("bundle_sha256") != contract["data_bundle_sha256"]
            or any(ticket.get(key) != contract[key] for key in (
                "config_sha256", "schema_sha256", "data_bundle_version",
                "data_bundle_sha256", "evaluation_policy_sha256",
                "promotion_gates_sha256", "campaign_budget_sha256",
                "partition_contract_sha256", "champion_ranking_rule_sha256"))):
        raise V3IncidentAmendmentError("Archived scientific contract differs")


def _verify_diagnostic(root: Path, value: Any, registered: datetime) -> datetime:
    quarantine = _exact(value, {
        "manifest", "diagnostic_completed_at_utc", "quarantine_registered_at_utc",
        "purpose", "contains_development_results",
        "eligible_as_replacement_evidence", "eligible_as_seed_parent_or_ranking_input",
        "eligible_for_promotion_or_protected_final_authorization",
        "performance_metrics_consulted_to_select_or_modify_scientific_parameters",
        "must_remain_outside_data_bundle_and_campaign_evidence",
    }, "diagnostic quarantine")
    manifest_relative, manifest_path = _file_record(
        root, quarantine["manifest"], "diagnostic quarantine manifest")
    if (manifest_relative != DIAGNOSTIC_MANIFEST_PATH.as_posix()
            or quarantine["manifest"]["sha256"] != DIAGNOSTIC_MANIFEST_SHA256):
        raise V3IncidentAmendmentError("Diagnostic quarantine manifest differs")
    manifest = _read(manifest_path, "diagnostic quarantine manifest")
    expected_keys = {
        "actual_orders_placed", "artifacts", "candidate_id",
        "candidate_plan_sha256", "component", "contains_development_results",
        "diagnostic_code_binding", "diagnostic_completed_at_utc",
        "eligible_as_replacement_campaign_evidence",
        "eligible_as_seed_parent_or_ranking_input",
        "eligible_for_promotion_or_protected_final_authorization", "network_used",
        "performance_metrics_consulted_to_select_or_modify_scientific_parameters",
        "protected_final_read", "purpose", "quarantine_registered_at_utc",
        "schema_version", "status",
    }
    _exact(manifest, expected_keys, "diagnostic quarantine manifest")
    completed = _timestamp(
        manifest["diagnostic_completed_at_utc"], "diagnostic completion time")
    quarantined = _timestamp(
        manifest["quarantine_registered_at_utc"], "quarantine registration time")
    if (not completed < quarantined < registered
            or quarantine["diagnostic_completed_at_utc"]
            != manifest["diagnostic_completed_at_utc"]
            or quarantine["quarantine_registered_at_utc"]
            != manifest["quarantine_registered_at_utc"]
            or manifest["schema_version"] != 1
            or manifest["component"] != DIAGNOSTIC_COMPONENT
            or manifest["status"] != DIAGNOSTIC_STATUS
            or manifest["candidate_id"] != "v3-candidate-f5f2fd485512dbcb45e0"
            or manifest["candidate_plan_sha256"]
            != "f5f2fd485512dbcb45e0592766984bd5e17e00445f23378e663d39b6c10158a0"
            or manifest["contains_development_results"] is not True
            or manifest["eligible_as_replacement_campaign_evidence"] is not False
            or manifest["eligible_as_seed_parent_or_ranking_input"] is not False
            or manifest["eligible_for_promotion_or_protected_final_authorization"] is not False
            or manifest["performance_metrics_consulted_to_select_or_modify_scientific_parameters"] is not False
            or manifest["network_used"] is not False
            or manifest["protected_final_read"] is not False
            or manifest["actual_orders_placed"] is not False
            or quarantine["purpose"] != manifest["purpose"]
            or quarantine["contains_development_results"] is not True
            or quarantine["eligible_as_replacement_evidence"] is not False
            or quarantine["eligible_as_seed_parent_or_ranking_input"] is not False
            or quarantine["eligible_for_promotion_or_protected_final_authorization"] is not False
            or quarantine["performance_metrics_consulted_to_select_or_modify_scientific_parameters"] is not False
            or quarantine["must_remain_outside_data_bundle_and_campaign_evidence"] is not True):
        raise V3IncidentAmendmentError("Diagnostic quarantine semantics differ")
    binding = manifest["diagnostic_code_binding"]
    if (not isinstance(binding, list)
            or binding != [{"path": path, "sha256": digest}
                            for path, digest in sorted(
                                EXPECTED_DIAGNOSTIC_CODE_BINDING.items())]):
        raise V3IncidentAmendmentError("Diagnostic-time code binding differs")
    rows = manifest["artifacts"]
    if not isinstance(rows, list) or not rows:
        raise V3IncidentAmendmentError("Diagnostic artifact inventory is empty")
    seen: set[str] = set()
    for row in rows:
        relative, path = _file_record(root, row, "diagnostic artifact")
        if (relative in seen
                or not relative.startswith(DIAGNOSTIC_MANIFEST_PATH.parent.as_posix() + "/")):
            raise V3IncidentAmendmentError("Diagnostic artifact path differs")
        seen.add(relative)
    bundle_text = (root / "data/manifests/v3_data_bundle.json").read_text(
        encoding="utf-8-sig")
    campaign_manifest_text = (root / FAILED_CAMPAIGN_PATH / "campaign-artifacts.json").read_text(
        encoding="utf-8-sig")
    if manifest_relative in bundle_text or manifest_relative in campaign_manifest_text:
        raise V3IncidentAmendmentError("Quarantined diagnostic entered campaign evidence")
    return completed


def _verify_repair_scope(
    root: Path, value: Any, archive: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    repair = _exact(value, {
        "allowed_changes", "changed_code_pre_post", "post_repair_code_inventory",
        "scope_expansion",
    }, "incident repair scope")
    allowed = repair["allowed_changes"]
    changes = repair["changed_code_pre_post"]
    if (not isinstance(allowed, list) or not allowed
            or len(allowed) != len(set(allowed)) or not isinstance(changes, list)
            or repair["scope_expansion"] is not False):
        raise V3IncidentAmendmentError("Incident repair scope differs")
    current, aggregate = _verify_code_inventory(
        root, repair["post_repair_code_inventory"])
    current_by_path = {row["path"]: row for row in current}
    old_inventory = archive["readiness"].get("code_inventory")
    if not isinstance(old_inventory, list):
        raise V3IncidentAmendmentError("Archived code inventory is missing")
    old_by_path = {row.get("path"): row for row in old_inventory
                   if isinstance(row, dict)}
    if any(not isinstance(path, str) for path in old_by_path):
        raise V3IncidentAmendmentError("Archived code inventory is malformed")
    removed_paths = sorted(set(old_by_path) - set(current_by_path))
    if removed_paths:
        raise V3IncidentAmendmentError(
            "Repair removed files from the archived source package")
    actual_changed_paths = sorted(
        path for path, current_row in current_by_path.items()
        if path not in old_by_path
        or old_by_path[path].get("sha256") != current_row["sha256"])
    changed_paths: list[str] = []
    for row in changes:
        row = _exact(row, {"path", "before", "after"}, "changed code record")
        path = row["path"]
        if (not isinstance(path, str) or path in changed_paths
                or not path.startswith("src/klax_lab/") or not path.endswith(".py")
                or path not in current_by_path
                or row["after"] != current_by_path[path]["sha256"]):
            raise V3IncidentAmendmentError("Changed code record differs")
        old = old_by_path.get(path)
        if old is None:
            if row["before"] is not None:
                raise V3IncidentAmendmentError("New repair module has a prior hash")
        elif row["before"] != old.get("sha256"):
            raise V3IncidentAmendmentError("Pre-repair code hash differs")
        if row["before"] == row["after"]:
            raise V3IncidentAmendmentError("Repair code record did not change")
        _sha(row["after"], "post-repair code")
        changed_paths.append(path)
    if (allowed != sorted(allowed) or sorted(changed_paths) != allowed
            or allowed != actual_changed_paths):
        raise V3IncidentAmendmentError("Allowed repair files differ")
    return current, aggregate


def _verify_independence(value: Any) -> None:
    independence = _exact(value, _INDEPENDENCE_KEYS, "scientific independence")
    expected_true = {
        "repairs_triggered_only_by_integrity_or_protocol_defects",
        "evaluator_repair_fixed_before_diagnostic_run",
        "diagnostic_results_existed_before_later_protocol_edits",
        "later_protocol_edits_derived_from_saved_worker_and_schema_evidence",
    }
    expected_false = _INDEPENDENCE_KEYS - expected_true - {"owner_attestation"}
    if (any(independence[key] is not True for key in expected_true)
            or any(independence[key] is not False for key in expected_false)
            or independence["owner_attestation"] != OWNER_ATTESTATION):
        raise V3IncidentAmendmentError("Scientific independence declaration differs")


def _verify_replacement(
    root: Path,
    value: Any,
    *,
    pre_issuance: bool,
) -> dict[str, str]:
    replacement = _exact(value, {
        "campaign_id", "readiness_path", "ticket_path", "claim_path",
        "must_differ_from_failed_campaign_id", "must_not_resume_failed_campaign",
        "must_not_overwrite_or_remove_failed_ticket_or_claim",
        "must_use_fresh_readiness_and_one_use_ticket",
        "ticket_must_bind_this_amendment_sha256",
        "readiness_must_bind_this_amendment_sha256",
        "claim_path_must_be_derived_from_new_ticket_path",
        "prelaunch_claim_path_must_not_exist", "old_ticket_or_claim_reusable",
    }, "replacement campaign constraints")
    campaign_id = _identifier(replacement["campaign_id"], "replacement campaign_id")
    readiness_relative, readiness_path = _relative(
        root, replacement["readiness_path"], "replacement readiness")
    ticket_relative, ticket_path = _relative(
        root, replacement["ticket_path"], "replacement ticket")
    claim_relative, claim_path = _relative(
        root, replacement["claim_path"], "replacement claim")
    required_true = {
        "must_differ_from_failed_campaign_id", "must_not_resume_failed_campaign",
        "must_not_overwrite_or_remove_failed_ticket_or_claim",
        "must_use_fresh_readiness_and_one_use_ticket",
        "ticket_must_bind_this_amendment_sha256",
        "readiness_must_bind_this_amendment_sha256",
        "claim_path_must_be_derived_from_new_ticket_path",
        "prelaunch_claim_path_must_not_exist",
    }
    if (campaign_id == FAILED_CAMPAIGN_ID
            or any(replacement[key] is not True for key in required_true)
            or replacement["old_ticket_or_claim_reusable"] is not False
            or claim_relative != ticket_relative + ".claimed.json"
            or len({readiness_relative, ticket_relative, claim_relative}) != 3):
        raise V3IncidentAmendmentError("Replacement campaign constraints differ")
    if pre_issuance and (any(path.exists() for path in (
            readiness_path, ticket_path, claim_path))
            or (root / "runs/campaigns_v3" / campaign_id).exists()):
        raise V3IncidentAmendmentError(
            "Replacement authorization path already exists before issuance")
    return {
        "campaign_id": campaign_id,
        "readiness_path": readiness_relative,
        "ticket_path": ticket_relative,
        "claim_path": claim_relative,
    }


def verify_campaign1_integrity_repair_amendment(
    root: Path | str,
    *,
    expected_champion_ranking_rule_sha256: str,
    pre_issuance: bool = False,
) -> dict[str, Any]:
    """Verify and summarize the immutable real-campaign replacement amendment."""
    root = Path(root).resolve()
    amendment_path = (root / AMENDMENT_PATH).resolve()
    amendment = _exact(
        _read(amendment_path, "amendment"), _TOP_KEYS,
        "V3 campaign incident amendment")
    registered = _timestamp(amendment["registered_at_utc"], "amendment registration time")
    amendment_id = _identifier(amendment["amendment_id"], "amendment_id")
    if (amendment["schema_version"] != 1
            or amendment["component"] != AMENDMENT_COMPONENT
            or amendment["status"] != AMENDMENT_STATUS
            or amendment["network_used"] is not False
            or amendment["protected_final_read"] is not False
            or amendment["actual_orders_placed"] is not False):
        raise V3IncidentAmendmentError("V3 campaign incident amendment differs")
    failed = _exact(amendment["failed_campaign"], _FAILED_KEYS, "failed campaign")
    if (failed["network_used"] is not False
            or failed["protected_final_read"] is not False
            or failed["actual_orders_placed"] is not False):
        raise V3IncidentAmendmentError("Failed campaign boundary differs")
    completed = _timestamp(failed["completed_at_utc"], "failed campaign completion")
    campaign = _verify_campaign_artifacts(root, failed)
    archive = _verify_archive(root, failed)
    _verify_trigger(root, amendment["trigger_evidence"], campaign)
    _verify_scientific_contract(
        root, amendment["unchanged_scientific_contract"], archive,
        expected_champion_ranking_rule_sha256=(
            _sha(expected_champion_ranking_rule_sha256, "ranking rule")),
    )
    _verify_repair_scope(root, amendment["repair_scope"], archive)
    diagnostic_completed = _verify_diagnostic(
        root, amendment["diagnostic_quarantine"], registered)
    _verify_independence(amendment["scientific_independence"])
    replacement = _verify_replacement(
        root, amendment["replacement_constraints"], pre_issuance=pre_issuance)
    if not completed < diagnostic_completed < registered:
        raise V3IncidentAmendmentError("Incident registration ordering differs")
    digest = sha256_file(amendment_path)
    return {
        "path": AMENDMENT_PATH.as_posix(),
        "sha256": digest,
        "amendment_id": amendment_id,
        "registered_at_utc": registered.isoformat(),
        "failed_campaign_id": FAILED_CAMPAIGN_ID,
        "replacement_campaign_id": replacement["campaign_id"],
        "replacement_readiness_path": replacement["readiness_path"],
        "replacement_ticket_path": replacement["ticket_path"],
        "replacement_claim_path": replacement["claim_path"],
    }
