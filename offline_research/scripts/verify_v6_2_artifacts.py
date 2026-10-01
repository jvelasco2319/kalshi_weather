"""Verify the active corrected V6.2 registration and first wave."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v6_1.common import checked
from v6_2.campaign import locate, verify


def run(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    first = checked(locate(root) / "first-wave.json")
    checks = {
        "campaign_identity": first["campaign_id"] == state["campaign_id"],
        "finite_catalog_27": len(registration["catalog_ids"]) == 27,
        "first_wave_8": len(first["evaluated_candidate_ids"]) == 8,
        "corrected_market_gate_computed": isinstance(
            first["market_results"]["M01_MARKET_PLUS_CALIBRATED_WEATHER"]["market_gate_passed"], bool),
        "confirmation_denied": not registration["confirmation_authorized"],
        "protected_labels_unread": not state["protected_labels_accessed"],
        "network_unused": not state["network_used_by_experiment"],
        "zero_orders": state["orders"] == 0,
    }
    result = {"schema_version": "klax-v6.2-verification-v1",
              "campaign_id": state["campaign_id"],
              "status": "PASS" if all(checks.values()) else "FAIL",
              "checks": checks, "failed_checks": [key for key, value in checks.items() if not value]}
    if result["status"] != "PASS":
        raise ValueError(json.dumps(result, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print(json.dumps(run(args.project_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

