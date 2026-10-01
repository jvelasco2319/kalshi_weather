"""Independent structural and hash verifier for a registered V6.1 campaign."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v6_1.campaign import locate, verify
from v6_1.common import checked


def run(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    directory = locate(root)
    ledger = checked(root / "data/manifests/v6_1_global_exposure_ledger.json")
    readiness = checked(root / "data/manifests/v6_1_readiness.json")
    launch = checked(directory / "launch-certificate.json") if (directory / "launch-certificate.json").is_file() else None
    colony = checked(directory / "colony-status.json") if (directory / "colony-status.json").is_file() else None
    checks = {
        "campaign_identity": registration["campaign_id"] == state["campaign_id"],
        "old_v6_preserved": registration["predecessor_v6_preserved"],
        "all_current_dates_blocked_from_final_confirmation": ledger["confirmation_eligible_date_count"] == 0,
        "development_only": registration["launch_mode"] == "DEVELOPMENT_ONLY",
        "confirmation_denied": not registration["confirmation_authorized"],
        "protected_labels_unread_by_v6_1": not state["protected_labels_accessed"],
        "network_unused_by_experiment": not state["network_used_by_experiment"],
        "zero_orders": state["orders"] == 0,
        "four_colonies": len(registration["colonies"]) == 4,
        "finite_catalog": len(registration["candidate_catalog_ids"]) == 27,
        "readiness_development_only": readiness["status"] == "READY_FOR_DEVELOPMENT_PREREQUISITES_ONLY",
        "launch_certificate_present": launch is not None,
        "colony_status_present": colony is not None,
        "development_clock_not_refunded_or_started_early": not state["development_budget_started"],
    }
    result = {
        "schema_version": "klax-v6.1-artifact-verification-v1",
        "campaign_id": state["campaign_id"],
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
    }
    if result["status"] != "PASS":
        raise ValueError(json.dumps(result, indent=2))
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(json.dumps(run(args.project_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

