"""Independent structural verifier for V5 terminal campaign artifacts."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

from .common import (
    V5IntegrityError, canonical_hash, file_sha256, load_object,
    project_path, verify_hash_bound,
)
from .readiness import READINESS_PATH, TICKET_PATH


VERIFIER_VERSION = "klax-v5-independent-campaign-verifier-v1"
MANIFEST_VERSION = "klax-v5-campaign-artifacts-v1"


def prelabel_conclusion(colonies: Mapping[str, Mapping[str, Any]]) -> str | None:
    order = (
        ("execution_evidence", "INSUFFICIENT_EXECUTION_EVIDENCE"),
        ("fee_and_settlement_integrity",
         "FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE"),
        ("frozen_probability_validation", "PROBABILITY_VALIDATION_FAILED"),
        ("sample_and_regime_robustness",
         "INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ"),
    )
    for name, conclusion in order:
        row = colonies.get(name)
        if not isinstance(row, Mapping):
            return "INTEGRITY_FAILURE"
        if row.get("promotion_ready") is not True:
            return conclusion
    return None


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise V5IntegrityError("timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V5IntegrityError("timestamp lacks timezone")
    return parsed


def verify_campaign(root: Path, campaign_id: str) -> dict[str, Any]:
    root = Path(root).resolve()
    campaign_root = project_path(root, Path("runs/campaigns_v5") / campaign_id)
    checks: dict[str, bool] = {}
    errors: list[str] = []

    def check(name: str, condition: bool) -> None:
        checks[name] = bool(condition)
        if not condition:
            errors.append(name)

    try:
        readiness = load_object(root / READINESS_PATH)
        claim_path = root / TICKET_PATH.with_name(
            TICKET_PATH.name + ".claimed.json")
        claim = load_object(claim_path)
        summary = load_object(campaign_root / "summary.json")
        recovery = load_object(campaign_root / "recovery-state.json")
        manifest = load_object(campaign_root / "campaign-artifacts.json")
        verify_hash_bound(readiness, "readiness_sha256")
        verify_hash_bound(claim, "ticket_sha256")
        verify_hash_bound(summary, "summary_sha256")
        verify_hash_bound(recovery, "state_sha256")
        verify_hash_bound(manifest, "manifest_sha256")

        check("campaign_identity", all(
            item.get("campaign_id") == campaign_id
            for item in (claim, summary, recovery, manifest)))
        check("readiness_binding",
              claim.get("readiness_sha256") == readiness.get("readiness_sha256")
              == summary.get("readiness_sha256"))
        check("terminal_phase",
              recovery.get("phase") == "COMPLETE"
              and summary.get("status") == "COMPLETE")
        started = _parse_time(summary.get("started_at_utc"))
        deadline = _parse_time(summary.get("absolute_deadline_at_utc"))
        completed = _parse_time(summary.get("completed_at_utc"))
        check("wall_budget_exact",
              int((deadline - started).total_seconds()) == 43_200)
        check("completed_not_after_deadline", completed <= deadline)
        check("early_stop_registered",
              summary.get("stopped_reason")
              in {"registered_prelabel_terminal_conclusion",
                  "wall_time_budget_exhausted"})

        colonies = readiness.get("colony_results")
        check("four_colonies",
              isinstance(colonies, Mapping) and len(colonies) == 4)
        expected = prelabel_conclusion(colonies or {})
        check("terminal_conclusion",
              expected is not None
              and summary.get("scientific_conclusion") == expected)
        check("protected_labels_sealed",
              readiness.get("protected_confirmation_labels_read") is False
              and summary.get("protected_confirmation_labels_read") is False
              and recovery.get("protected_confirmation_labels_read") is False)
        check("zero_order_authorization",
              readiness.get("live_or_paper_orders_authorized") is False
              and summary.get("live_or_paper_orders_authorized") is False
              and recovery.get("live_or_paper_orders_authorized") is False)
        check("zero_orders",
              readiness.get("actual_orders_placed") is False
              and summary.get("actual_orders_placed") is False
              and recovery.get("actual_orders_placed") is False)
        check("single_candidate",
              summary.get("promotable_candidate_identity_limit") == 1)
        check("four_agent_slots",
              summary.get("maximum_concurrent_agent_slots") == 4)

        artifacts = manifest.get("artifacts")
        check("artifact_manifest_list",
              isinstance(artifacts, list) and bool(artifacts))
        if isinstance(artifacts, list):
            seen: set[str] = set()
            for index, row in enumerate(artifacts):
                label = f"artifact_{index}"
                if not isinstance(row, Mapping):
                    check(label, False)
                    continue
                relative = row.get("path")
                try:
                    path = project_path(root, str(relative))
                    valid = (
                        relative not in seen
                        and path.is_file()
                        and path.stat().st_size == row.get("bytes")
                        and file_sha256(path) == row.get("sha256")
                    )
                except V5IntegrityError:
                    valid = False
                check(label, valid)
                if isinstance(relative, str):
                    seen.add(relative)
            required = {
                f"runs/campaigns_v5/{campaign_id}/summary.json",
                f"runs/campaigns_v5/{campaign_id}/recovery-state.json",
                f"runs/campaigns_v5/{campaign_id}/report.md",
            }
            check("required_artifacts", required.issubset(seen))
    except (OSError, ValueError, V5IntegrityError) as exc:
        errors.append(f"exception:{type(exc).__name__}:{exc}")

    status = "PASS" if not errors and all(checks.values()) else "FAIL"
    body = {
        "verifier_version": VERIFIER_VERSION,
        "campaign_id": campaign_id,
        "status": status,
        "checks": checks,
        "errors": errors,
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
    }
    body["verification_sha256"] = canonical_hash(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id", required=True)
    args = parser.parse_args(argv)
    value = verify_campaign(args.root, args.campaign_id)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
