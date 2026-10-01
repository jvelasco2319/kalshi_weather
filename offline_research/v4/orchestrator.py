"""Durable prepare/start/resume/status path for the V4 offline campaign.

The scheduler emits typed V3 plans for an external offline evaluator.  It does
not read the protected-final partition, fetch data, or place orders.  Every
state transition is atomic and hash-bound so a restart reissues the same queue.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from klax_lab.provenance import canonical_hash
from klax_lab.research_plan_v3 import ResearchPlanV3

from .execution_coverage import (
    EPOCH_SIZE, ModelTrackV4, V4CoverageError, adaptive_followup_packet_v4,
    allocate_epoch_v4, build_cross_pollination_digest_v4,
    build_v4_execution_manifest, execution_factorial_plans_v4,
    load_ranked_v3_parent_records, load_v4_execution_registration,
    robust_positive_milestone_v4, select_forecast_parent_v4,
)


STATE_VERSION = "klax-v4-orchestrator-state-v1"
TERMINAL_STATUSES = {"CHAMPION", "ROBUST_POSITIVE_REVIEW", "BUDGET_COMPLETE", "FAILED"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise V4CoverageError("V4 timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _assert_nonprotected_path(path: Path) -> None:
    if "protected_final" in {part.casefold().replace("-", "_") for part in path.parts}:
        raise V4CoverageError("V4 state cannot enter protected-final storage")


def _state_hash(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body.pop("state_sha256", None)
    return canonical_hash(body)


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    _assert_nonprotected_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body["state_sha256"] = _state_hash(body)
    temporary = path.with_name(path.name + ".pending")
    temporary.write_text(
        json.dumps(body, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def load_state_v4(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    _assert_nonprotected_path(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V4CoverageError(f"Cannot read V4 state: {path}") from exc
    if (not isinstance(state, dict) or state.get("state_version") != STATE_VERSION
            or state.get("state_sha256") != _state_hash(state)
            or state.get("protected_final_read") is not False
            or state.get("orders_authorized") is not False):
        raise V4CoverageError("V4 state identity or safety fields differ")
    return state


def _components(root: Path):
    registration = load_v4_execution_registration(root)
    tracks = tuple(ModelTrackV4.from_dict(row)
                   for row in registration["execution_factorial"]["model_tracks"])
    records = load_ranked_v3_parent_records(root, registration)
    parent, parent_audit = select_forecast_parent_v4(records)
    plans = execution_factorial_plans_v4(parent, tracks)
    manifest = build_v4_execution_manifest(root)
    return registration, records, parent, parent_audit, plans, manifest


def _compact_record(record: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": record.get("candidate_id"),
        "plan": deepcopy(record.get("plan")),
        "evaluation": deepcopy(record.get("evaluation")),
        "promotion": deepcopy(record.get("promotion")),
        "replication": deepcopy(record.get("replication")),
        "critic": deepcopy(record.get("critic")),
        "ledger": deepcopy(dict(ledger)),
    }


def _source_evidence(root: Path, registration: Mapping[str, Any],
                     records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    campaign_root = root / registration["source_campaign"]["campaign_root"]
    continuation = json.loads(
        (campaign_root / "continuation-import.json").read_text(encoding="utf-8"))
    predecessor_id = (continuation.get("source") or {}).get("campaign_id")
    predecessor_root = (root / "runs/campaigns_v3" / predecessor_id
                        if isinstance(predecessor_id, str) else None)
    output = []
    for record in records:
        plan = ResearchPlanV3.from_dict(dict(record["plan"]))
        ledger_path = campaign_root / "candidates" / "primary" / plan.identity / "ledger.json"
        if not ledger_path.is_file() and predecessor_root is not None:
            ledger_path = predecessor_root / "candidates" / "primary" / plan.identity / "ledger.json"
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise V4CoverageError(f"Missing V3 source ledger: {plan.identity}") from exc
        decisions = ledger.get("decisions")
        if not isinstance(decisions, list):
            raise V4CoverageError("V3 source ledger decisions are invalid")
        output.append(_compact_record(record, ledger))
    return tuple(output)


def prepare_v4(root: Path, state_path: Path) -> dict[str, Any]:
    root, state_path = Path(root).resolve(), Path(state_path).resolve()
    _assert_nonprotected_path(state_path)
    if state_path.exists():
        raise V4CoverageError("V4 state already exists; use resume or status")
    registration, _, _, parent_audit, plans, manifest = _components(root)
    state = {
        "state_version": STATE_VERSION,
        "status": "PREPARED",
        "root": str(root),
        "manifest": manifest,
        "registration_sha256": manifest["registration_sha256"],
        "registered_plan_set_sha256": manifest["registered_plan_set_sha256"],
        "source_parent": parent_audit,
        "budget": dict(registration["budget"]),
        "current_epoch": 0,
        "completed_plan_sha256s": [],
        "candidate_calls_used": 0,
        "synthesis_calls_used": 0,
        "transient_retry_calls_used": 0,
        "active_queue": [],
        "current_digest": None,
        "last_completed_digest_sha256": None,
        "started_at_utc": None,
        "deadline_utc": None,
        "completed_at_utc": None,
        "stopped_reason": None,
        "protected_final_read": False,
        "protected_final_authorized": False,
        "orders_authorized": False,
        "actual_profit_claim": False,
        "registered_plan_count": len(plans),
    }
    _atomic_write(state_path, state)
    return load_state_v4(state_path)


def _verify_runtime_binding(root: Path, state: Mapping[str, Any]):
    registration, records, parent, _, plans, manifest = _components(root)
    if (manifest["manifest_sha256"] != state["manifest"]["manifest_sha256"]
            or manifest["registration_sha256"] != state["registration_sha256"]
            or manifest["registered_plan_set_sha256"]
            != state["registered_plan_set_sha256"]):
        raise V4CoverageError("V4 runtime code, registration, or plan set changed")
    return registration, records, parent, plans, manifest


def _queue_rows(
    allocations, digest: Mapping[str, Any], registration: Mapping[str, Any], epoch: int,
) -> list[dict[str, Any]]:
    rows = []
    for slot, allocation in enumerate(allocations, 1):
        packet = adaptive_followup_packet_v4(allocation, digest, registration)
        rows.append({
            "task_id": f"v4-e{epoch:03d}-s{slot:02d}",
            "epoch": epoch,
            "slot": slot,
            "category": allocation.category,
            "colony": allocation.colony,
            "plan_sha256": allocation.plan.identity,
            "plan": allocation.plan.to_dict(),
            "followup_packet": packet,
            "status": "PENDING",
        })
    return rows


def start_v4(root: Path, state_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    root, state_path = Path(root).resolve(), Path(state_path).resolve()
    state = load_state_v4(state_path)
    if state["status"] != "PREPARED":
        raise V4CoverageError("Only a prepared V4 campaign can start")
    registration, records, _, plans, _ = _verify_runtime_binding(root, state)
    evidence = _source_evidence(root, registration, records)
    digest = build_cross_pollination_digest_v4(
        1, evidence, previous_digest_sha256=None)
    allocations = allocate_epoch_v4(
        plans, (), evidence, cross_pollination_digest=digest)
    started = (now or _utc_now()).astimezone(timezone.utc)
    state.update({
        "status": "RUNNING",
        "current_epoch": 1,
        "active_queue": _queue_rows(allocations, digest, registration, 1),
        "current_digest": digest,
        "candidate_calls_used": EPOCH_SIZE,
        "started_at_utc": _timestamp(started),
        "deadline_utc": _timestamp(
            started + timedelta(seconds=registration["budget"]["maximum_wall_seconds"])),
    })
    _atomic_write(state_path, state)
    return load_state_v4(state_path)


def resume_v4(root: Path, state_path: Path) -> dict[str, Any]:
    root, state_path = Path(root).resolve(), Path(state_path).resolve()
    state = load_state_v4(state_path)
    _verify_runtime_binding(root, state)
    if state["status"] == "PREPARED":
        return start_v4(root, state_path)
    if state["status"] == "RUNNING" and len(state["active_queue"]) != EPOCH_SIZE:
        raise V4CoverageError("Running V4 state has an incomplete active queue")
    return state


def _validate_epoch_results(state: Mapping[str, Any], results: Iterable[Mapping[str, Any]]):
    rows = tuple(results)
    if len(rows) != EPOCH_SIZE:
        raise V4CoverageError("V4 epoch result count differs from its queue")
    expected = {row["plan_sha256"] for row in state["active_queue"]}
    observed = set()
    compact = []
    for row in rows:
        if not isinstance(row.get("plan"), Mapping):
            raise V4CoverageError("V4 result omits its typed plan")
        plan = ResearchPlanV3.from_dict(dict(row["plan"]))
        if plan.identity not in expected or plan.identity in observed:
            raise V4CoverageError("V4 result identity is missing, duplicated, or unexpected")
        if (row.get("replication", {}).get("status") != "PASS"
                or row.get("replication", {}).get("protected_final_evaluated") is not False
                or row.get("critic", {}).get("decision") != "NONREJECT"
                or row.get("critic", {}).get("protected_final_evaluated") is not False):
            raise V4CoverageError("V4 result lacks independent replication or critic approval")
        observed.add(plan.identity)
        if not isinstance(row.get("ledger"), Mapping):
            raise V4CoverageError("V4 result lacks its evaluator ledger")
        compact.append(_compact_record(row, row["ledger"]))
    if observed != expected:
        raise V4CoverageError("V4 epoch results do not cover the active queue exactly")
    return tuple(compact)


def complete_epoch_v4(
    root: Path,
    state_path: Path,
    results: Iterable[Mapping[str, Any]],
    *,
    synthesis_artifact: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reject external result injection.

    V4 completion is owned by :mod:`v4.orchestrator_v4`, which invokes the
    fixed evaluator, independent numerical replay, critic, and observed-fill
    verifier itself. Accepting a caller-provided result mapping here would let
    forged promotion flags influence stopping.
    """
    raise V4CoverageError(
        "External V4 epoch results are forbidden; use the production runner")
    # Kept below as an inert state-transition specification for recovery-file
    # compatibility and code review. It is deliberately unreachable.
    root, state_path = Path(root).resolve(), Path(state_path).resolve()
    state = load_state_v4(state_path)
    if state["status"] != "RUNNING":
        raise V4CoverageError("Only a running V4 epoch can complete")
    registration, _, _, plans, _ = _verify_runtime_binding(root, state)
    evidence = _validate_epoch_results(state, results)
    epoch = state["current_epoch"]
    synthesis_due = epoch % registration["cross_pollination"][
        "local_model_synthesis_every_epochs"] == 0
    if synthesis_due:
        if (not isinstance(synthesis_artifact, Mapping)
                or synthesis_artifact.get("source_digest_sha256")
                != state["current_digest"]["digest_sha256"]
                or not isinstance(synthesis_artifact.get("artifact_sha256"), str)):
            raise V4CoverageError("Scheduled V4 synthesis artifact is missing or unbound")
        state["synthesis_calls_used"] += 1
    elif synthesis_artifact is not None:
        raise V4CoverageError("Unscheduled synthesis would exceed the registered cadence")

    completed = set(state["completed_plan_sha256s"])
    completed.update(row["plan_sha256"] for row in state["active_queue"])
    state["completed_plan_sha256s"] = sorted(completed)
    state["last_completed_digest_sha256"] = state["current_digest"]["digest_sha256"]
    moment = (now or _utc_now()).astimezone(timezone.utc)

    champion = next((row for row in evidence if row.get("promotion", {}).get("passed") is True), None)
    robust = next((row for row in evidence if robust_positive_milestone_v4(row)), None)
    budget = registration["budget"]
    stop_reason = None
    status = "RUNNING"
    if champion is not None:
        status, stop_reason = "CHAMPION", "candidate_passed_every_development_gate"
    elif robust is not None:
        status, stop_reason = "ROBUST_POSITIVE_REVIEW", "robust_positive_nonchampion_review"
    elif moment >= _parse_timestamp(state["deadline_utc"]):
        status, stop_reason = "BUDGET_COMPLETE", "wall_time_budget_exhausted"
    elif epoch >= budget["maximum_epochs"] or len(completed) >= budget["maximum_distinct_candidates"]:
        status, stop_reason = "BUDGET_COMPLETE", "registered_candidate_or_epoch_budget_exhausted"

    if status in TERMINAL_STATUSES:
        state.update({
            "status": status, "stopped_reason": stop_reason, "active_queue": [],
            "current_digest": None, "completed_at_utc": _timestamp(moment),
        })
        _atomic_write(state_path, state)
        return load_state_v4(state_path)

    next_epoch = epoch + 1
    digest = build_cross_pollination_digest_v4(
        next_epoch, evidence,
        previous_digest_sha256=state["last_completed_digest_sha256"])
    allocations = allocate_epoch_v4(
        plans, completed, evidence, cross_pollination_digest=digest)
    if (state["candidate_calls_used"] + EPOCH_SIZE
            + state["synthesis_calls_used"] + state["transient_retry_calls_used"]
            > budget["maximum_local_model_calls"]):
        state.update({
            "status": "BUDGET_COMPLETE", "stopped_reason": "model_call_budget_exhausted",
            "active_queue": [], "current_digest": None,
            "completed_at_utc": _timestamp(moment),
        })
    else:
        state.update({
            "current_epoch": next_epoch,
            "active_queue": _queue_rows(
                allocations, digest, registration, next_epoch),
            "current_digest": digest,
            "candidate_calls_used": state["candidate_calls_used"] + EPOCH_SIZE,
        })
    _atomic_write(state_path, state)
    return load_state_v4(state_path)


def status_v4(root: Path, state_path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    state = load_state_v4(state_path)
    _verify_runtime_binding(Path(root).resolve(), state)
    moment = (now or _utc_now()).astimezone(timezone.utc)
    seconds_remaining = None
    if state["deadline_utc"] is not None:
        seconds_remaining = max(
            0, int((_parse_timestamp(state["deadline_utc"]) - moment).total_seconds()))
    return {
        "status": state["status"],
        "current_epoch": state["current_epoch"],
        "active_queue_items": len(state["active_queue"]),
        "completed_candidates": len(state["completed_plan_sha256s"]),
        "registered_candidates": state["registered_plan_count"],
        "candidate_calls_used": state["candidate_calls_used"],
        "synthesis_calls_used": state["synthesis_calls_used"],
        "transient_retry_calls_used": state["transient_retry_calls_used"],
        "seconds_remaining": seconds_remaining,
        "stopped_reason": state["stopped_reason"],
        "current_digest_sha256": (
            state["current_digest"]["digest_sha256"]
            if state["current_digest"] is not None else None),
        "protected_final_read": False,
        "orders_authorized": False,
        "state_sha256": state["state_sha256"],
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="V4 offline KLAX scheduler")
    parser.add_argument("command", choices=("prepare", "start", "resume", "status"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        value = prepare_v4(args.root, args.state)
    elif args.command == "start":
        value = start_v4(args.root, args.state)
    elif args.command == "resume":
        value = resume_v4(args.root, args.state)
    else:
        value = status_v4(args.root, args.state)
    print(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
