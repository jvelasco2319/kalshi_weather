"""Record objective preregistration and baseline defects before experimental launch."""
from __future__ import annotations

import argparse
from pathlib import Path

from .campaign import locate, verify
from .common import checked, filehash, seal, stamp, write


def run(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    if state["development_budget_started"]:
        raise ValueError("Gate audit must precede the experiment clock")
    baseline = checked(root / "data/development/v6_1/market_baseline_v2.json")
    config_path = root / "configs/v6_1_campaign.json"
    import json
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fold_capacity = math_floor = len(checked(root / "data/manifests/v6_1_readiness.json")["development_dates"]) // config["outer_folds"]
    defects = {
        "v1_market_probability_order_misaligned": True,
        "v1_mixed_grade_b_proxy_with_paid_books": True,
        "minimum_100_grade_a_days_exceeds_64_date_development_cohort": config["minimum_confirmation_grade_a_days"] > 64,
        "minimum_15_per_fold_exceeds_smallest_development_fold": config["minimum_selected_days_per_fold"] > fold_capacity,
        "only_7_grade_a_development_days": True,
    }
    result = seal({
        "schema_version": "klax-v6.1-gate-impossibility-audit-v1",
        "campaign_id": state["campaign_id"], "created_at": stamp(),
        "status": "EXPERIMENTAL_LAUNCH_BLOCKED_BY_PREREGISTRATION_DEFECT",
        "defects": defects,
        "corrected_market_baseline": baseline["self_sha256"],
        "development_date_count": 64, "smallest_theoretical_fold_capacity": fold_capacity,
        "grade_a_development_date_count": 7,
        "interpretation": "The 100-day and 15-per-fold gates are valid future confirmation requirements but cannot be development gates on this cohort.",
        "required_action": "Preserve V6.1 and register a corrected successor before starting an experimental clock.",
        "protected_labels_accessed": False, "orders": 0,
        "bindings": {
            "configs/v6_1_campaign.json": filehash(config_path),
            "data/development/v6_1/market_baseline_v2.json": filehash(root / "data/development/v6_1/market_baseline_v2.json"),
            "v6_1/gate_audit.py": filehash(root / "v6_1/gate_audit.py"),
        },
    })
    directory = locate(root)
    write(directory / "gate-impossibility-audit.json", result)
    colony = checked(directory / "colony-status.json")
    updated = seal({**{k: v for k, v in colony.items() if k != "self_sha256"},
                    "updated_at": stamp(), "cross_pollination_enabled": False,
                    "launch_blocker": result["status"]})
    write(directory / "colony-status.json", updated)
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = run(args.project_root)
    print({key: result[key] for key in ("campaign_id", "status", "defects", "required_action")})


if __name__ == "__main__":
    main()

