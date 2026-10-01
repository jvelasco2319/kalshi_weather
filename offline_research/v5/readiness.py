"""Build and verify the one-use V5 evidence-campaign authorization."""
from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .common import (
    V5IntegrityError, atomic_write_json, canonical_hash, file_record,
    file_sha256, hash_bound, iso_utc, load_object, safety_record, utc_now,
)
from .sample_regime import audit_sample_regime


READINESS_VERSION = "klax-v5-four-colony-readiness-v1"
TICKET_VERSION = "klax-v5-offline-campaign-ticket-v1"
READY_STATUS = "READY_FOR_V5_OFFLINE_EVIDENCE_CAMPAIGN"
READINESS_PATH = Path("data/manifests/v5_readiness.json")
TICKET_PATH = Path("runs/v5_offline_campaign_ticket.json")
GOAL_CONFIG_PATH = Path("configs/v5_four_colony_verification_campaign.json")
GOAL_DOCUMENT_PATH = Path("docs/V5_FOUR_COLONY_VERIFICATION_CAMPAIGN_GOAL.md")
SOURCE_CANDIDATE_PATH = Path(
    "runs/campaigns_v4/v4-offline-20260926T212122609Z/candidates/primary/"
    "a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461/"
    "compiled_manifest.json"
)


def _colony_auditors() -> tuple[tuple[str, Callable[..., dict[str, Any]]], ...]:
    # Imports are delayed so each colony can be developed and tested separately.
    from .economics import audit_fee_settlement
    from .execution import audit_execution_evidence
    from .probability import audit_frozen_probability

    return (
        ("execution_evidence", audit_execution_evidence),
        ("fee_and_settlement_integrity", audit_fee_settlement),
        ("frozen_probability_validation", audit_frozen_probability),
        ("sample_and_regime_robustness", audit_sample_regime),
    )


def _validate_config(root: Path) -> dict[str, Any]:
    config = load_object(root / GOAL_CONFIG_PATH)
    if (
        config.get("schema_version") != "klax-v5-four-colony-goal-v1"
        or config.get("status")
        != "GOAL_REGISTERED_PENDING_IMPLEMENTATION_AND_READINESS"
        or config.get("offline_only") is not True
        or config.get("live_or_paper_orders_authorized") is not False
        or config.get("prospective_live_collection_authorized") is not False
        or config.get("agent_system", {}).get("maximum_concurrent_agent_slots") != 4
        or config.get("budgets", {}).get("wall_clock_seconds_after_readiness")
        != 43_200
        or config.get("frozen_probability_leader", {}).get(
            "research_plan_sha256")
        != "a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461"
        or config.get("frozen_probability_leader", {}).get(
            "transfer_requirements", {}).get("promotable_candidate_identity_limit")
        != 1
        or config.get("protected_data_policy", {}).get(
            "confirmation_label_read_authorized_before_all_readiness_gates")
        is not False
    ):
        raise V5IntegrityError("V5 goal configuration differs")
    goal_path = root / GOAL_DOCUMENT_PATH
    if (
        not goal_path.is_file()
        or file_sha256(goal_path) != config.get("goal_document_sha256")
    ):
        raise V5IntegrityError("V5 goal document differs")
    source = load_object(root / SOURCE_CANDIDATE_PATH)
    leader = config["frozen_probability_leader"]
    if (
        source.get("research_plan_sha256") != leader["research_plan_sha256"]
        or source.get("fitted_model_sha256") != leader["fitted_model_sha256"]
        or source.get("fitted_model_state", {}).get("state_sha256")
        != leader["model_state_sha256"]
        or source.get("protected_final_used_for_fit") is not False
    ):
        raise V5IntegrityError("frozen probability leader identity differs")
    return config


def _verify_colony_result(
    name: str, result: Mapping[str, Any],
) -> dict[str, Any]:
    hash_fields = [
        field for field in ("result_sha256", "self_sha256", "audit_sha256")
        if field in result
    ]
    field = hash_fields[0] if len(hash_fields) == 1 else None
    claimed = result.get(field) if field is not None else None
    body = {key: value for key, value in result.items() if key != field}
    checks = {
        "object": isinstance(result, Mapping),
        "colony": result.get("colony") == name,
        "status": isinstance(result.get("status"), str)
        and bool(result.get("status")),
        "promotion_ready_boolean": type(result.get("promotion_ready")) is bool,
        "failure_reasons": isinstance(result.get("failure_reasons"), list),
        "one_self_hash": len(hash_fields) == 1,
        "self_hash": isinstance(claimed, str)
        and claimed == canonical_hash(body),
        "no_protected_labels": result.get(
            "protected_confirmation_labels_read") is False,
        "no_orders_authorized": result.get(
            "live_or_paper_orders_authorized") is False,
        "no_orders_placed": result.get("actual_orders_placed") is False,
        "no_network": result.get("network_used") is False,
    }
    return {
        "review_version": "klax-v5-colony-cross-review-v1",
        "target_colony": name,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        **safety_record(),
    }


def build_readiness(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(root).resolve()
    config = _validate_config(root)
    started = utc_now()
    colony_results: dict[str, dict[str, Any]] = {}
    for name, auditor in _colony_auditors():
        result = auditor(root, config)
        if not isinstance(result, dict):
            raise V5IntegrityError(f"{name} audit did not return an object")
        colony_results[name] = result

    rotation = {
        "execution_evidence": "frozen_probability_validation",
        "frozen_probability_validation": "sample_and_regime_robustness",
        "sample_and_regime_robustness": "fee_and_settlement_integrity",
        "fee_and_settlement_integrity": "execution_evidence",
    }
    cross_reviews = {
        reviewer: {
            **_verify_colony_result(target, colony_results[target]),
            "reviewer_colony": reviewer,
        }
        for reviewer, target in rotation.items()
    }
    if any(row["status"] != "PASS" for row in cross_reviews.values()):
        raise V5IntegrityError("V5 colony cross-review failed")

    code_paths = sorted(
        path for path in (root / "v5").glob("*.py") if path.is_file())
    code_inventory = [
        file_record(root, path.relative_to(root)) for path in code_paths]
    required_names = {
        "__init__.py", "common.py", "execution.py", "economics.py",
        "probability.py", "sample_regime.py", "readiness.py",
        "orchestrator.py", "verifier.py",
    }
    if not required_names.issubset({path.name for path in code_paths}):
        missing = sorted(required_names - {path.name for path in code_paths})
        raise V5IntegrityError(f"V5 implementation incomplete: {missing}")

    confirmation_eligible = all(
        row["promotion_ready"] for row in colony_results.values())
    blockers = [
        {
            "colony": name,
            "status": row["status"],
            "failure_reasons": row["failure_reasons"],
        }
        for name, row in colony_results.items()
        if not row["promotion_ready"]
    ]
    readiness: dict[str, Any] = {
        "readiness_version": READINESS_VERSION,
        "status": READY_STATUS,
        "prepared_at_utc": iso_utc(started),
        "goal_config": file_record(root, GOAL_CONFIG_PATH),
        "goal_document": file_record(root, GOAL_DOCUMENT_PATH),
        "source_candidate": file_record(root, SOURCE_CANDIDATE_PATH),
        "code_inventory": code_inventory,
        "code_inventory_sha256": canonical_hash(code_inventory),
        "colony_results": colony_results,
        "cross_reviews": cross_reviews,
        "confirmation_eligible": confirmation_eligible,
        "prelabel_blockers": blockers,
        "maximum_wall_seconds": 43_200,
        "maximum_concurrent_agent_slots": 4,
        "promotable_candidate_identity_limit": 1,
        "selected_trade_set_frozen_before_labels": True,
        **safety_record(),
    }
    readiness = hash_bound(readiness, "readiness_sha256")
    campaign_id = (
        "v5-offline-" + started.strftime("%Y%m%dT%H%M%S%fZ"))
    ticket: dict[str, Any] = {
        "ticket_version": TICKET_VERSION,
        "status": "UNCLAIMED",
        "campaign_id": campaign_id,
        "issued_at_utc": iso_utc(started),
        "maximum_wall_seconds": 43_200,
        "readiness_path": READINESS_PATH.as_posix(),
        "readiness_sha256": readiness["readiness_sha256"],
        "goal_config_sha256": readiness["goal_config"]["sha256"],
        "code_inventory_sha256": readiness["code_inventory_sha256"],
        "confirmation_eligible_at_launch": confirmation_eligible,
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
    }
    ticket = hash_bound(ticket, "ticket_sha256")
    return readiness, ticket


def prepare(root: Path, *, replace: bool = False) -> dict[str, Any]:
    root = Path(root).resolve()
    readiness_path = root / READINESS_PATH
    ticket_path = root / TICKET_PATH
    claimed = ticket_path.with_name(ticket_path.name + ".claimed.json")
    if not replace and (
        readiness_path.exists() or ticket_path.exists() or claimed.exists()
    ):
        raise V5IntegrityError(
            "V5 readiness or ticket already exists; replacement refused")
    readiness, ticket = build_readiness(root)
    atomic_write_json(readiness_path, readiness)
    atomic_write_json(ticket_path, ticket)
    return {
        "status": readiness["status"],
        "campaign_id": ticket["campaign_id"],
        "confirmation_eligible": readiness["confirmation_eligible"],
        "prelabel_blockers": readiness["prelabel_blockers"],
        "readiness_path": READINESS_PATH.as_posix(),
        "readiness_file_sha256": file_sha256(readiness_path),
        "ticket_path": TICKET_PATH.as_posix(),
        "ticket_file_sha256": file_sha256(ticket_path),
        "maximum_wall_seconds": ticket["maximum_wall_seconds"],
        **safety_record(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "inspect"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.action == "prepare":
        value = prepare(root, replace=args.replace)
    else:
        value = {
            "readiness": load_object(root / READINESS_PATH),
            "ticket": load_object(root / TICKET_PATH),
        }
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
