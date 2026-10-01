"""Run the registered V5 four-colony offline evidence campaign."""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from .common import (
    V5IntegrityError, atomic_write_json, file_record, hash_bound, iso_utc,
    load_object, safety_record, utc_now, verify_hash_bound,
)
from .readiness import READINESS_PATH, READY_STATUS, TICKET_PATH
from .verifier import MANIFEST_VERSION, prelabel_conclusion, verify_campaign


RUNNER_VERSION = "klax-v5-four-colony-orchestrator-v1"
SUMMARY_VERSION = "klax-v5-offline-campaign-summary-v1"
RECOVERY_VERSION = "klax-v5-campaign-recovery-v1"
OUTPUT_PARENT = Path("runs/campaigns_v5")


def _load_authorization(root: Path, *, resume: bool) -> tuple[
    dict[str, Any], dict[str, Any], Path
]:
    readiness = load_object(root / READINESS_PATH)
    verify_hash_bound(readiness, "readiness_sha256")
    if (
        readiness.get("status") != READY_STATUS
        or readiness.get("maximum_wall_seconds") != 43_200
        or readiness.get("maximum_concurrent_agent_slots") != 4
        or readiness.get("promotable_candidate_identity_limit") != 1
        or readiness.get("protected_confirmation_labels_read") is not False
        or readiness.get("live_or_paper_orders_authorized") is not False
        or readiness.get("actual_orders_placed") is not False
    ):
        raise V5IntegrityError("V5 readiness safety or budget differs")

    ticket_path = root / TICKET_PATH
    claim_path = ticket_path.with_name(ticket_path.name + ".claimed.json")
    if resume:
        claim = load_object(claim_path)
        verify_hash_bound(claim, "ticket_sha256")
        if claim.get("status") != "CLAIMED":
            raise V5IntegrityError("V5 claimed ticket status differs")
        return readiness, claim, claim_path

    ticket = load_object(ticket_path)
    verify_hash_bound(ticket, "ticket_sha256")
    if (
        ticket.get("status") != "UNCLAIMED"
        or ticket.get("readiness_sha256") != readiness["readiness_sha256"]
        or ticket.get("maximum_wall_seconds") != 43_200
        or claim_path.exists()
    ):
        raise V5IntegrityError("V5 one-use ticket is unavailable or differs")
    started = utc_now()
    claim_body = {
        **{key: value for key, value in ticket.items()
           if key != "ticket_sha256"},
        "status": "CLAIMED",
        "issued_ticket_sha256": ticket["ticket_sha256"],
        "claimed_at_utc": iso_utc(started),
        "started_at_utc": iso_utc(started),
        "absolute_deadline_at_utc": iso_utc(
            started + timedelta(seconds=43_200)),
    }
    claim = hash_bound(claim_body, "ticket_sha256")
    issued_path = ticket_path.with_name(ticket_path.name + ".issued.json")
    ticket_path.replace(issued_path)
    atomic_write_json(claim_path, claim)
    return readiness, claim, claim_path


def _task_register() -> list[dict[str, Any]]:
    colonies = (
        "execution_evidence",
        "fee_and_settlement_integrity",
        "frozen_probability_validation",
        "sample_and_regime_robustness",
    )
    stages = (
        "source_capability",
        "development_reproduction",
        "outcome_blind_transfer_readiness",
        "cross_review",
        "terminal_synthesis",
    )
    return [
        {
            "task_id": f"v5-e{epoch:02d}-c{index + 1}",
            "epoch": epoch,
            "colony": colony,
            "stage": stages[epoch - 1],
            "status": "COMPLETED" if epoch == 1
            else "NOT_RUN_REGISTERED_TERMINAL",
            "model_calls_charged": 0,
            "protected_confirmation_labels_read": False,
            "actual_orders_placed": False,
        }
        for epoch in range(1, 6)
        for index, colony in enumerate(colonies)
    ]


def _write_colony_artifacts(
    output: Path, readiness: Mapping[str, Any],
) -> list[Path]:
    artifacts: list[Path] = []
    for name, result in readiness["colony_results"].items():
        path = output / "colonies" / name / "summary.json"
        atomic_write_json(path, result)
        artifacts.append(path)
    for reviewer, review in readiness["cross_reviews"].items():
        path = output / "cross-reviews" / f"{reviewer}.json"
        atomic_write_json(path, review)
        artifacts.append(path)
    return artifacts


def _report_markdown(summary: Mapping[str, Any]) -> str:
    blockers = summary["prelabel_blockers"]
    lines = [
        "# V5 Four-Colony Offline Verification Campaign",
        "",
        f"Campaign: {summary['campaign_id']}",
        "",
        f"Conclusion: {summary['scientific_conclusion']}",
        "",
        "The campaign stopped at the registered outcome-blind evidence barrier. "
        "No protected confirmation label was read and no live or paper order "
        "was authorized or placed.",
        "",
        "## Four colony results",
        "",
    ]
    for name, row in summary["colony_statuses"].items():
        lines.append(
            f"- **{name}**: {row['status']} "
            f"(promotion ready: {str(row['promotion_ready']).lower()})")
    lines.extend(["", "## Pre-label blockers", ""])
    if blockers:
        for row in blockers:
            reasons = ", ".join(row.get("failure_reasons") or ["unspecified"])
            lines.append(
                f"- **{row['colony']}** — {row['status']}: {reasons}")
    else:
        lines.append("- None.")
    lines.extend([
        "",
        "## Safety",
        "",
        "- Protected confirmation labels read: false",
        "- Live or paper orders authorized: false",
        "- Actual orders placed: false",
        "- Network used by experiment runner: false",
        "",
        "This is a historical research conclusion. It does not establish or "
        "guarantee future profitability.",
        "",
    ])
    return "\n".join(lines)


def _artifact_manifest(
    root: Path, campaign_id: str, paths: list[Path], conclusion: str,
) -> dict[str, Any]:
    records = [
        file_record(root, path.relative_to(root))
        for path in sorted(set(paths), key=lambda item: item.as_posix())
    ]
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "campaign_id": campaign_id,
        "scientific_conclusion": conclusion,
        "artifacts": records,
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
    }
    return hash_bound(manifest, "manifest_sha256")


def run(root: Path, *, resume: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    readiness, claim, _ = _load_authorization(root, resume=resume)
    campaign_id = str(claim["campaign_id"])
    output = root / OUTPUT_PARENT / campaign_id
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".campaign-execution.lock"
    if lock_path.exists() and not resume:
        raise V5IntegrityError("V5 campaign execution lease already exists")
    if not lock_path.exists():
        lock_path.write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at_utc": claim["started_at_utc"],
            }) + "\n",
            encoding="utf-8",
        )

    summary_path = output / "summary.json"
    if summary_path.exists():
        raise V5IntegrityError("V5 campaign already has a terminal summary")

    started_monotonic = time.monotonic()
    conclusion = prelabel_conclusion(readiness["colony_results"])
    if conclusion is None:
        raise V5IntegrityError(
            "confirmation-eligible V5 readiness requires the separate "
            "one-shot confirmation evaluator")

    artifacts = _write_colony_artifacts(output, readiness)
    registration = hash_bound({
        "registration_version": "klax-v5-campaign-registration-v1",
        "campaign_id": campaign_id,
        "runner_version": RUNNER_VERSION,
        "readiness_sha256": readiness["readiness_sha256"],
        "goal_config": readiness["goal_config"],
        "source_candidate": readiness["source_candidate"],
        "maximum_wall_seconds": 43_200,
        "maximum_concurrent_agent_slots": 4,
        "promotable_candidate_identity_limit": 1,
        **safety_record(),
    }, "registration_sha256")
    registration_path = output / "registration.json"
    atomic_write_json(registration_path, registration)
    artifacts.append(registration_path)

    tasks = _task_register()
    task_register_path = output / "task-register.json"
    atomic_write_json(task_register_path, {
        "task_register_version": "klax-v5-task-register-v1",
        "campaign_id": campaign_id,
        "tasks": tasks,
        "core_task_count": 20,
        "completed_source_capability_tasks": 4,
        "not_run_after_terminal_count": 16,
        **safety_record(),
    })
    artifacts.append(task_register_path)

    scheduler_path = output / "scheduler-state.json"
    atomic_write_json(scheduler_path, {
        "scheduler_version": "klax-v5-scheduler-state-v1",
        "campaign_id": campaign_id,
        "current_epoch": 1,
        "maximum_epochs": 5,
        "core_tasks": 20,
        "model_calls_consumed": 0,
        "maximum_model_calls": 60,
        "maximum_concurrent_agent_slots": 4,
        "stopped_reason": "registered_prelabel_terminal_conclusion",
        **safety_record(),
    })
    artifacts.append(scheduler_path)

    completed = utc_now()
    summary = hash_bound({
        "summary_version": SUMMARY_VERSION,
        "status": "COMPLETE",
        "campaign_id": campaign_id,
        "readiness_sha256": readiness["readiness_sha256"],
        "started_at_utc": claim["started_at_utc"],
        "absolute_deadline_at_utc": claim["absolute_deadline_at_utc"],
        "completed_at_utc": iso_utc(completed),
        "elapsed_wall_seconds": round(
            time.monotonic() - started_monotonic, 6),
        "maximum_wall_seconds": 43_200,
        "stopped_reason": "registered_prelabel_terminal_conclusion",
        "scientific_conclusion": conclusion,
        "confirmation_eligible": False,
        "protected_confirmation_remained_sealed": True,
        "colony_statuses": {
            name: {
                "status": row["status"],
                "promotion_ready": row["promotion_ready"],
            }
            for name, row in readiness["colony_results"].items()
        },
        "prelabel_blockers": readiness["prelabel_blockers"],
        "maximum_concurrent_agent_slots": 4,
        "promotable_candidate_identity_limit": 1,
        "registered_core_tasks": 20,
        "completed_core_tasks": 4,
        "local_model_calls_consumed": 0,
        **safety_record(),
    }, "summary_sha256")
    atomic_write_json(summary_path, summary)
    artifacts.append(summary_path)

    recovery = hash_bound({
        "recovery_version": RECOVERY_VERSION,
        "campaign_id": campaign_id,
        "phase": "COMPLETE",
        "started_at_utc": claim["started_at_utc"],
        "absolute_deadline_at_utc": claim["absolute_deadline_at_utc"],
        "completed_at_utc": summary["completed_at_utc"],
        "readiness_sha256": readiness["readiness_sha256"],
        "scientific_conclusion": conclusion,
        "registered_core_tasks": 20,
        "completed_core_tasks": 4,
        "model_calls_consumed": 0,
        "elapsed_wall_seconds": summary["elapsed_wall_seconds"],
        **safety_record(),
    }, "state_sha256")
    recovery_path = output / "recovery-state.json"
    atomic_write_json(recovery_path, recovery)
    artifacts.append(recovery_path)

    report_path = output / "report.md"
    report_path.write_text(_report_markdown(summary), encoding="utf-8")
    artifacts.append(report_path)

    manifest = _artifact_manifest(root, campaign_id, artifacts, conclusion)
    manifest_path = output / "campaign-artifacts.json"
    atomic_write_json(manifest_path, manifest)

    verification = verify_campaign(root, campaign_id)
    verification_path = output / "scheduler-verification.json"
    atomic_write_json(verification_path, verification)
    if verification["status"] != "PASS":
        raise V5IntegrityError(
            "V5 independent terminal verification failed: "
            + ", ".join(verification["errors"]))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "resume"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    value = run(args.root, resume=args.action == "resume")
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
