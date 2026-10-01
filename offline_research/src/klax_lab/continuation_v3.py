"""Immutable continuation import for a terminal V3 campaign.

The helper does not resume a campaign or issue authorization.  It verifies a
completed source campaign, binds a separately issued successor readiness and
ticket, and records the exact state that a successor may import.  The failed
epoch-two queue remains immutable source evidence and is never executable in
the successor; the allocator must rebuild epoch two from the explicitly
eligible imported candidate pool.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from .provenance import canonical_hash, sha256_file, write_json
from .research_plan_v3 import ResearchPlanV3


CONTINUATION_IMPORT_VERSION = "klax-v3-continuation-import-v1"
SOURCE_MANIFEST_VERSION = "klax-v3-campaign-artifacts-v1"
SOURCE_RECOVERY_VERSION = "klax-v3-campaign-recovery-v2"
SOURCE_REPORT_VERSION = "klax-v3-campaign-report-v2"
SOURCE_TERMINAL_REASON = (
    "required_data_integrity_replication_critic_or_resource_boundary_failure")
DIAGNOSTIC_PLAN_SHA256 = (
    "f5f2fd485512dbcb45e0592766984bd5e17e00445f23378e663d39b6c10158a0")


class V3ContinuationError(ValueError):
    """A continuation import is incomplete, modified, or unsafe."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda item: (_ for _ in ()).throw(
                V3ContinuationError(f"Non-finite JSON in {label}: {item}")),
        )
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError) as exc:
        raise V3ContinuationError(f"Cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3ContinuationError(f"{label} must contain one JSON object")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise V3ContinuationError(f"Invalid {label}")
    return value


def _identifier(value: Any, label: str) -> str:
    if (not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value) is None):
        raise V3ContinuationError(f"Invalid {label}")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise V3ContinuationError(f"Missing {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3ContinuationError(f"Invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3ContinuationError(f"{label} must be timezone-aware")
    return value


def _project_file(root: Path, value: Path | str, label: str) -> tuple[str, Path]:
    if not isinstance(value, (str, Path)):
        raise V3ContinuationError(f"Invalid {label} path")
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise V3ContinuationError(f"{label} escapes the project") from exc
    lowered = {part.lower().replace("-", "_") for part in resolved.parts}
    if {"protected_final", "holdout"} & lowered:
        raise V3ContinuationError(f"{label} cannot use protected-final storage")
    if not resolved.is_file() or resolved.is_symlink():
        raise V3ContinuationError(f"Missing or symbolic {label}: {resolved}")
    return relative, resolved


def _artifact_inventory(directory: Path) -> list[dict[str, Any]]:
    manifest_path = directory / "campaign-artifacts.json"
    records: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise V3ContinuationError("Source campaign artifacts contain a symbolic link")
        if not path.is_file() or path == manifest_path:
            continue
        records.append({
            "path": path.relative_to(directory).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return records


def _verify_source_campaign(
    root: Path, source_campaign: Path | str,
) -> dict[str, Any]:
    source = Path(source_campaign)
    source = source.resolve() if source.is_absolute() else (root / source).resolve()
    expected_parent = (root / "runs" / "campaigns_v3").resolve()
    if source.parent != expected_parent or not source.is_dir() or source.is_symlink():
        raise V3ContinuationError(
            "Source must be one immutable runs/campaigns_v3/<campaign-id> directory")
    campaign_id = _identifier(source.name, "source campaign ID")

    manifest_path = source / "campaign-artifacts.json"
    manifest = _read_json(manifest_path, "source campaign artifact manifest")
    required_manifest = {
        "manifest_version", "campaign_id", "report_version", "artifacts",
        "network_used", "protected_final_read", "actual_orders_placed",
        "manifest_sha256",
    }
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    inventory = _artifact_inventory(source)
    if (set(manifest) != required_manifest
            or manifest.get("manifest_version") != SOURCE_MANIFEST_VERSION
            or manifest.get("campaign_id") != campaign_id
            or manifest.get("report_version") != SOURCE_REPORT_VERSION
            or manifest.get("network_used") is not False
            or manifest.get("protected_final_read") is not False
            or manifest.get("actual_orders_placed") is not False
            or manifest.get("manifest_sha256") != canonical_hash(body)
            or manifest.get("artifacts") != inventory):
        raise V3ContinuationError("Source campaign artifact manifest differs")

    by_path = {row["path"]: row for row in inventory}
    required_paths = {
        "recovery-state.json", "summary.json", "candidate-register.json",
        "search-coverage.json", "report.md",
    }
    if not required_paths <= set(by_path):
        raise V3ContinuationError("Source campaign terminal artifacts are incomplete")

    recovery = _read_json(source / "recovery-state.json", "source recovery state")
    summary = _read_json(source / "summary.json", "source campaign summary")
    register = json.loads((source / "candidate-register.json").read_text(encoding="utf-8"))
    coverage_file = _read_json(source / "search-coverage.json", "source search coverage")
    recovery_body = {key: value for key, value in recovery.items() if key != "state_sha256"}
    if (recovery.get("recovery_version") != SOURCE_RECOVERY_VERSION
            or recovery.get("campaign_id") != campaign_id
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("state_sha256") != canonical_hash(recovery_body)
            or recovery.get("protected_final_evaluated") is not False
            or recovery.get("actual_orders_placed") is not False):
        raise V3ContinuationError("Source recovery is not an immutable terminal state")
    started_at = _timestamp(recovery.get("started_at_utc"), "source start time")
    completed_at = _timestamp(recovery.get("completed_at_utc"), "source completion time")
    if datetime.fromisoformat(completed_at.replace("Z", "+00:00")) <= datetime.fromisoformat(
            started_at.replace("Z", "+00:00")):
        raise V3ContinuationError("Source completion does not follow its start")

    if (summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("campaign_id") != campaign_id
            or summary.get("report_version") != SOURCE_REPORT_VERSION
            or summary.get("started_at_utc") != started_at
            or summary.get("completed_at_utc") != completed_at
            or summary.get("stopped_reason") != SOURCE_TERMINAL_REASON
            or summary.get("protected_final_evaluated") is not False
            or summary.get("protected_final_authorization_issued") is not False
            or summary.get("actual_orders_placed") is not False
            or summary.get("ranked_candidates") != register
            or summary.get("coverage") != coverage_file
            or recovery.get("coverage") != coverage_file):
        raise V3ContinuationError("Source terminal artifacts disagree")

    engine = recovery.get("engine")
    queue = recovery.get("queue")
    if not isinstance(engine, dict) or not isinstance(queue, list) or not queue:
        raise V3ContinuationError("Source recovery engine or epoch-two queue is missing")
    integer_counters = {
        "elapsed_seconds", "current_epoch", "model_calls",
        "model_context_tokens_reserved", "admitted_candidates",
        "executed_candidates", "empty_epochs",
    }
    if any(type(engine.get(key)) is not int or engine[key] < 0 for key in integer_counters):
        raise V3ContinuationError("Source engine counters are invalid")
    if (engine["current_epoch"] != 2
            or engine.get("stopped_reason") != SOURCE_TERMINAL_REASON
            or engine.get("champion_candidate_id") is not None
            or engine.get("final_authorization") is not None
            or recovery.get("next_queue_index") != 0):
        raise V3ContinuationError("Source did not stop at the epoch-two dispatch boundary")

    task_ids: set[str] = set()
    for item in queue:
        if (not isinstance(item, dict) or item.get("status") != "PENDING"
                or item.get("candidate_id") is not None
                or not isinstance(item.get("task_id"), str)
                or not item["task_id"].startswith("e02-")
                or item["task_id"] in task_ids):
            raise V3ContinuationError(
                "Every source epoch-two queue item must be uniquely pending and unadmitted")
        task_ids.add(item["task_id"])
    prohibited_prefixes = tuple(
        prefix for task_id in task_ids
        for prefix in (f"tasks/{task_id}/", f"local-inference/{task_id}/"))
    if any(row["path"].startswith(prohibited_prefixes) for row in inventory):
        raise V3ContinuationError("Source epoch two contains a response or inference artifact")

    epochs = coverage_file.get("epochs")
    epoch_two = epochs.get("2") if isinstance(epochs, dict) else None
    admissions = engine.get("epoch_admissions")
    candidates = engine.get("candidates")
    if (not isinstance(epoch_two, dict)
            or epoch_two.get("planned") != len(queue)
            or epoch_two.get("reviewed") != 0
            or epoch_two.get("admitted") != 0
            or not isinstance(admissions, dict) or admissions.get("2") != 0
            or not isinstance(candidates, dict) or not candidates
            or engine["admitted_candidates"] != len(candidates)
            or engine["executed_candidates"] != len(candidates)):
        raise V3ContinuationError("Source epoch two is not a zero-admission boundary")

    parsed_candidates: dict[str, ResearchPlanV3] = {}
    for candidate_id, raw in candidates.items():
        if not isinstance(raw, dict):
            raise V3ContinuationError("Malformed imported candidate")
        try:
            plan = ResearchPlanV3.from_dict(raw["plan"])
        except (KeyError, ValueError) as exc:
            raise V3ContinuationError("Malformed imported research plan") from exc
        if (candidate_id != "v3-candidate-" + plan.identity[:20]
                or raw.get("candidate_id") != candidate_id
                or raw.get("epoch") != 1
                or raw.get("evaluation") is None
                or raw.get("promotion") is None
                or raw.get("replication") is None
                or raw.get("critic") is None):
            raise V3ContinuationError("Imported candidate identity or completed evidence differs")
        parsed_candidates[candidate_id] = plan
    if (not isinstance(register, list)
            or not all(isinstance(row, dict) for row in register)
            or len(register) != len(parsed_candidates)
            or set(row.get("candidate_id") for row in register)
            != set(parsed_candidates)):
        raise V3ContinuationError("Source candidate register differs from recovery candidates")
    expected_novelty = {
        plan.novelty_fingerprint: candidate_id
        for candidate_id, plan in parsed_candidates.items()}
    if (engine.get("novelty_index") != expected_novelty
            or admissions.get("1") != len(parsed_candidates)):
        raise V3ContinuationError("Source novelty or epoch-one admission state differs")
    diagnostic_ids = sorted(
        candidate_id for candidate_id, plan in parsed_candidates.items()
        if plan.identity == DIAGNOSTIC_PLAN_SHA256)
    if len(diagnostic_ids) != 1:
        raise V3ContinuationError("Source must retain exactly one diagnostic-plan candidate")

    response_paths = [
        row["path"] for row in inventory
        if re.fullmatch(r"tasks/e\d{2}-[^/]+/response\.json", row["path"])]
    if (len(response_paths) + 1 != engine["model_calls"]
            or any(not path.startswith("tasks/e01-") for path in response_paths)):
        raise V3ContinuationError(
            "Source call accounting does not prove one response-free epoch-two attempt")

    budget_used = summary.get("budget_used")
    if (not isinstance(budget_used, dict)
            or budget_used.get("local_model_calls") != engine["model_calls"]
            or budget_used.get("reserved_context_tokens")
            != engine["model_context_tokens_reserved"]
            or budget_used.get("admitted_candidates") != engine["admitted_candidates"]
            or budget_used.get("executed_candidates") != engine["executed_candidates"]
            or budget_used.get("epochs") != engine["current_epoch"]):
        raise V3ContinuationError("Source summary does not preserve spent budgets")

    return {
        "directory": source,
        "campaign_id": campaign_id,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "inventory": inventory,
        "recovery": recovery,
        "summary": summary,
        "candidate_register": register,
        "coverage": coverage_file,
        "parsed_candidates": parsed_candidates,
        "diagnostic_candidate_ids": diagnostic_ids,
    }


def _verify_successor_binding(
    root: Path, campaign_id: str,
    readiness_path: Path | str, ticket_path: Path | str,
    *, allow_existing_claim: bool = False,
) -> dict[str, Any]:
    campaign_id = _identifier(campaign_id, "successor campaign ID")
    readiness_relative, readiness_file = _project_file(
        root, readiness_path, "successor readiness")
    ticket_relative, ticket_file = _project_file(root, ticket_path, "successor ticket")
    readiness = _read_json(readiness_file, "successor readiness")
    ticket = _read_json(ticket_file, "successor ticket")
    readiness_sha = sha256_file(readiness_file)
    ticket_sha = sha256_file(ticket_file)
    if (readiness.get("ready_for_v3_campaign") is not True
            or readiness.get("v3_campaign_authorized") is not True
            or readiness.get("protected_final_access_authorized") is not False
            or readiness.get("holdout_access_denied") is not True
            or readiness.get("historical_only") is not True):
        raise V3ContinuationError("Successor readiness does not preserve the offline boundary")
    if (ticket.get("campaign_id") != campaign_id
            or ticket.get("status") != "ACTIVE"
            or ticket.get("one_use") is not True
            or ticket.get("readiness_path") != readiness_relative
            or ticket.get("readiness_sha256") != readiness_sha
            or ticket.get("protected_final_authorized") is not False
            or ticket.get("protected_final_evaluations_remaining") != 1):
        raise V3ContinuationError("Successor ticket identity or boundary differs")
    claim = ticket_file.with_name(ticket_file.name + ".claimed.json")
    if claim.exists():
        if not allow_existing_claim:
            raise V3ContinuationError("Successor ticket was already claimed")
        claim_value = _read_json(claim, "successor ticket claim")
        expected_claim = {
            "campaign_id": campaign_id,
            "claim_version": "klax-v3-ticket-claim-v1",
            "readiness_sha256": readiness_sha,
            "synthetic": ticket.get("synthetic"),
            "ticket_sha256": ticket_sha,
        }
        if claim_value != expected_claim:
            raise V3ContinuationError(
                "Successor ticket claim identity or boundary differs")
    return {
        "campaign_id": campaign_id,
        "readiness": {"path": readiness_relative, "sha256": readiness_sha},
        "ticket": {"path": ticket_relative, "sha256": ticket_sha},
        "claim_path": claim.relative_to(root).as_posix(),
    }


def build_continuation_import_manifest(
    *, root: Path | str, source_campaign: Path | str,
    successor_campaign_id: str, successor_readiness_path: Path | str,
    successor_ticket_path: Path | str, allow_existing_claim: bool = False,
) -> dict[str, Any]:
    """Verify source/successor identities and build an inert import manifest."""
    project = Path(root).resolve()
    source = _verify_source_campaign(project, source_campaign)
    successor = _verify_successor_binding(
        project, successor_campaign_id,
        successor_readiness_path, successor_ticket_path,
        allow_existing_claim=allow_existing_claim)
    if successor["campaign_id"] == source["campaign_id"]:
        raise V3ContinuationError("A continuation requires a fresh campaign identity")

    recovery = source["recovery"]
    engine = recovery["engine"]
    preserved_source_state = {
        "started_at_utc": recovery["started_at_utc"],
        "completed_at_utc": recovery["completed_at_utc"],
        "engine": deepcopy(engine),
        "coverage": deepcopy(source["coverage"]),
        "queue": deepcopy(recovery["queue"]),
        "next_queue_index": recovery["next_queue_index"],
    }
    imported_engine = deepcopy(engine)
    imported_engine["stopped_reason"] = None

    eligible_rows = []
    ineligible_rows = []
    ranked_candidate_ids = [
        row["candidate_id"] for row in source["candidate_register"]]
    for candidate_id in ranked_candidate_ids:
        plan = source["parsed_candidates"][candidate_id]
        raw = engine["candidates"][candidate_id]
        row = {
            "candidate_id": candidate_id,
            "plan_sha256": plan.identity,
            "novelty_fingerprint": plan.novelty_fingerprint,
            "evaluation_sha256": (raw.get("artifact_sha256s") or {}).get(
                "evaluation_sha256"),
            "promotion_sha256": canonical_hash(raw["promotion"]),
        }
        _sha(row["evaluation_sha256"], "imported candidate evaluation SHA-256")
        (ineligible_rows if plan.identity == DIAGNOSTIC_PLAN_SHA256
         else eligible_rows).append(row)

    epoch_two_decisions = [
        item for item in source["coverage"].get("allocation_decisions", [])
        if isinstance(item, dict) and item.get("epoch") == 2]
    if len(epoch_two_decisions) != 1:
        raise V3ContinuationError("Source must contain one superseded epoch-two allocation")

    source_binding = {
        "campaign_id": source["campaign_id"],
        "campaign_root": source["directory"].relative_to(project).as_posix(),
        "campaign_artifact_manifest": {
            "path": source["manifest_path"].relative_to(project).as_posix(),
            "sha256": sha256_file(source["manifest_path"]),
            "manifest_sha256": source["manifest"]["manifest_sha256"],
        },
        "recovery": {
            "path": (source["directory"] / "recovery-state.json").relative_to(
                project).as_posix(),
            "sha256": sha256_file(source["directory"] / "recovery-state.json"),
            "state_sha256": recovery["state_sha256"],
        },
        "summary": {
            "path": (source["directory"] / "summary.json").relative_to(project).as_posix(),
            "sha256": sha256_file(source["directory"] / "summary.json"),
        },
    }
    continuation = {
        "source_state_sha256": canonical_hash(preserved_source_state),
        "preserved_source_state": preserved_source_state,
        "successor_import_state": {
            "campaign_id": successor["campaign_id"],
            "readiness_sha256": successor["readiness"]["sha256"],
            "ticket_sha256": successor["ticket"]["sha256"],
            "phase": "REBUILD_EPOCH_ALLOCATION",
            "started_at_utc": recovery["started_at_utc"],
            "completed_at_utc": None,
            "engine": imported_engine,
            "coverage": deepcopy(source["coverage"]),
            "queue": [],
            "next_queue_index": 0,
        },
        "allowed_state_changes": [
            "campaign_id", "readiness_sha256", "ticket_sha256", "phase",
            "completed_at_utc", "engine.stopped_reason", "queue",
            "next_queue_index",
        ],
        "preserved_without_refund": [
            "started_at_utc", "engine.elapsed_seconds", "engine.current_epoch",
            "engine.model_calls", "engine.model_context_tokens_reserved",
            "engine.admitted_candidates", "engine.executed_candidates",
            "engine.empty_epochs", "engine.candidates", "engine.novelty_index",
            "engine.duplicate_proposals", "engine.nonproposal_responses",
            "engine.epoch_admissions", "engine.transient_retries", "coverage",
        ],
        "epoch_two_rebuild": {
            "epoch": 2,
            "required_before_dispatch": True,
            "begin_new_epoch_permitted": False,
            "source_queue_reused": False,
            "source_queue_sha256": canonical_hash(recovery["queue"]),
            "source_allocation_reused": False,
            "superseded_source_allocation_sha256": canonical_hash(
                epoch_two_decisions[0]),
            "allocator": "registered_deterministic_v3_allocator",
            "allocation_weights": deepcopy(source["coverage"]["allocation_weights"]),
            "eligible_imported_candidates": eligible_rows,
            "eligible_parent_pool_sha256": canonical_hash(eligible_rows),
            "ineligible_historical_candidates": ineligible_rows,
            "denied_plan_sha256s": [DIAGNOSTIC_PLAN_SHA256],
            "historical_candidate_records_retained": True,
            "failed_model_call_reservation_retained": True,
        },
    }
    continuation["successor_import_state_sha256"] = canonical_hash(
        continuation["successor_import_state"])
    body = {
        "manifest_version": CONTINUATION_IMPORT_VERSION,
        "source": source_binding,
        "successor": successor,
        "continuation": continuation,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "manifest_sha256": canonical_hash(body)}


def verify_continuation_import_manifest(
    *, root: Path | str, manifest: dict[str, Any],
    allow_existing_claim: bool = False,
) -> dict[str, Any]:
    """Rebuild the manifest from its bound paths and require exact equality."""
    if not isinstance(manifest, dict):
        raise V3ContinuationError("Continuation manifest must be one JSON object")
    required = {
        "manifest_version", "source", "successor", "continuation",
        "network_used", "protected_final_read", "actual_orders_placed",
        "manifest_sha256",
    }
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if (set(manifest) != required
            or manifest.get("manifest_version") != CONTINUATION_IMPORT_VERSION
            or manifest.get("manifest_sha256") != canonical_hash(body)
            or manifest.get("network_used") is not False
            or manifest.get("protected_final_read") is not False
            or manifest.get("actual_orders_placed") is not False):
        raise V3ContinuationError("Continuation manifest identity or boundary differs")
    source = manifest.get("source")
    successor = manifest.get("successor")
    if not isinstance(source, dict) or not isinstance(successor, dict):
        raise V3ContinuationError("Continuation source or successor binding is missing")
    rebuilt = build_continuation_import_manifest(
        root=root,
        source_campaign=source.get("campaign_root"),
        successor_campaign_id=successor.get("campaign_id"),
        successor_readiness_path=(successor.get("readiness") or {}).get("path"),
        successor_ticket_path=(successor.get("ticket") or {}).get("path"),
        allow_existing_claim=allow_existing_claim,
    )
    if rebuilt != manifest:
        raise V3ContinuationError("Continuation manifest differs from current bound evidence")
    return {
        "status": "PASS",
        "manifest_sha256": manifest["manifest_sha256"],
        "source_campaign_id": source["campaign_id"],
        "successor_campaign_id": successor["campaign_id"],
        "eligible_parent_count": len(
            manifest["continuation"]["epoch_two_rebuild"][
                "eligible_imported_candidates"]),
        "ineligible_parent_count": len(
            manifest["continuation"]["epoch_two_rebuild"][
                "ineligible_historical_candidates"]),
        "protected_final_read": False,
    }


def verify_epoch_two_rebuild(
    *, manifest: dict[str, Any], allocation_decision: dict[str, Any],
    queue: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate a successor's freshly rebuilt epoch-two allocation.

    The caller must first verify the continuation manifest against disk.  This
    check binds the allocator output to the eligible imported pool and refuses
    any queue or lineage reference to a quarantined exact plan identity.
    """
    try:
        continuation = manifest["continuation"]
        rebuild = continuation["epoch_two_rebuild"]
        expected_pool_sha = rebuild["eligible_parent_pool_sha256"]
        eligible_ids = [
            row["candidate_id"] for row in rebuild["eligible_imported_candidates"]]
        denied = set(rebuild["denied_plan_sha256s"])
    except (KeyError, TypeError) as exc:
        raise V3ContinuationError("Malformed continuation rebuild contract") from exc
    if (manifest.get("manifest_sha256") != canonical_hash({
            key: value for key, value in manifest.items() if key != "manifest_sha256"})):
        raise V3ContinuationError("Continuation manifest hash differs")
    if not isinstance(allocation_decision, dict):
        raise V3ContinuationError("Epoch-two allocation must be one object")
    decision_body = {
        key: value for key, value in allocation_decision.items()
        if key != "decision_sha256"}
    if (allocation_decision.get("epoch") != 2
            or allocation_decision.get("weights") != rebuild["allocation_weights"]
            or allocation_decision.get("eligible_parent_pool_sha256")
            != expected_pool_sha
            or allocation_decision.get("source_candidate_ids") != eligible_ids[:10]
            or allocation_decision.get("decision_sha256")
            != canonical_hash(decision_body)):
        raise V3ContinuationError(
            "Rebuilt epoch-two allocation differs from the eligible imported pool")
    if not isinstance(queue, list) or not queue:
        raise V3ContinuationError("Rebuilt epoch-two queue is empty or malformed")
    queue_plan_sha256s: list[str] = []
    seen_tasks: set[str] = set()
    for item in queue:
        if (not isinstance(item, dict) or item.get("status") != "PENDING"
                or item.get("candidate_id") is not None
                or not isinstance(item.get("task_id"), str)
                or not item["task_id"].startswith("e02-")
                or item["task_id"] in seen_tasks):
            raise V3ContinuationError("Malformed rebuilt epoch-two queue item")
        seen_tasks.add(item["task_id"])
        options = item.get("options")
        if not isinstance(options, list) or not options:
            raise V3ContinuationError("Rebuilt queue item has no finite seed options")
        try:
            primary = ResearchPlanV3.from_dict(item["plan"])
            plans = [ResearchPlanV3.from_dict(raw) for raw in options]
        except (KeyError, ValueError) as exc:
            raise V3ContinuationError("Malformed rebuilt epoch-two research plan") from exc
        if primary.to_dict() != plans[0].to_dict():
            raise V3ContinuationError("Rebuilt primary plan differs from its first option")
        for plan in plans:
            if plan.identity in denied or denied.intersection(plan.parent_plan_sha256s):
                raise V3ContinuationError(
                    "Rebuilt epoch two references a denied plan or parent")
        queue_plan_sha256s.append(primary.identity)
    if allocation_decision.get("candidate_plan_sha256s") != queue_plan_sha256s:
        raise V3ContinuationError("Rebuilt allocation and queue plan identities differ")
    return {
        "status": "PASS",
        "epoch": 2,
        "queue_items": len(queue),
        "eligible_parent_pool_sha256": expected_pool_sha,
        "rebuilt_queue_sha256": canonical_hash(queue),
        "protected_final_read": False,
    }


def write_continuation_import_manifest(
    *, destination: Path | str, root: Path | str,
    source_campaign: Path | str, successor_campaign_id: str,
    successor_readiness_path: Path | str, successor_ticket_path: Path | str,
) -> dict[str, Any]:
    """Atomically create an immutable continuation manifest."""
    project = Path(root).resolve()
    destination = Path(destination)
    destination = (destination.resolve() if destination.is_absolute()
                   else (project / destination).resolve())
    try:
        destination.relative_to(project)
    except ValueError as exc:
        raise V3ContinuationError(
            "Continuation import destination escapes the project") from exc
    if {part.lower().replace("-", "_") for part in destination.parts} & {
            "protected_final", "holdout"}:
        raise V3ContinuationError(
            "Continuation import destination enters protected storage")
    if destination.is_symlink():
        raise V3ContinuationError("Continuation import destination is symbolic")
    manifest = build_continuation_import_manifest(
        root=project, source_campaign=source_campaign,
        successor_campaign_id=successor_campaign_id,
        successor_readiness_path=successor_readiness_path,
        successor_ticket_path=successor_ticket_path,
    )
    if destination.exists():
        existing = _read_json(destination, "existing continuation import")
        if existing != manifest:
            raise V3ContinuationError("Refusing to overwrite a continuation import")
        return existing
    write_json(destination, manifest)
    return manifest
