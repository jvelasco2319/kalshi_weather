"""Record prerequisite progress without starting or refunding the development clock."""
from __future__ import annotations

import argparse
from pathlib import Path

from .campaign import locate, verify
from .common import checked, filehash, seal, stamp, write


def record(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    if state["status"] != "ACTIVE_PREREQUISITE_PHASE" or state["development_budget_started"]:
        raise ValueError("V6.1 prerequisite progress cannot modify the active development run")
    observations = checked(root / "data/manifests/v6_1_observations_2026.json")
    baseline = checked(root / "data/development/v6_1/market_baseline.json")
    if observations["calendar_dates_with_any_observation"] != 142:
        raise ValueError("V6.1 observation window is incomplete")
    if baseline["complete_market_vector_date_count"] <= 0:
        raise ValueError("V6.1 market baseline has no scored dates")
    directory = locate(root)
    colony = seal({
        "schema_version": "klax-v6.1-colony-status-v1",
        "campaign_id": state["campaign_id"],
        "updated_at": stamp(),
        "colonies": {
            "settlement_forecasting": {
                "status": "READY_FOR_PRECURSOR_EVALUATION",
                "evidence": "142 dates and 20,073 as-of reports across five stations",
                "remaining_blocker": "Weather Company numeric settlement temperatures remain unavailable"
            },
            "market_residuals": {
                "status": "MARKET_BASELINE_COMPLETE",
                "evidence": f"{baseline['complete_market_vector_date_count']} coherent market vectors",
                "remaining_blocker": "Nested walk-forward and paired bootstrap have not run"
            },
            "execution_economics": {
                "status": "DIAGNOSTIC_ONLY",
                "evidence": "7 Grade-A dates in the exposed development cohort",
                "remaining_blocker": "100 new Grade-A confirmation days are required"
            },
            "adversarial_validation": {
                "status": "ACTIVE_GOVERNANCE",
                "evidence": "Global exposure ledger and negative-control catalog registered",
                "remaining_blocker": None
            }
        },
        "cross_pollination_enabled": False,
        "cross_pollination_condition": "Nested settlement and market precursor gates must pass",
        "confirmation_authorized": False,
    })
    write(directory / "colony-status.json", colony)
    inputs = [
        "data/manifests/v6_1_observations_2026.json",
        "data/normalized/v6_1_observations_2026/features/local_observations.parquet",
        "data/development/v6_1/market_baseline.json",
        "v6_1/acquire_observations.py", "v6_1/baseline.py", "v6_1/progress.py",
    ]
    progress = seal({
        "schema_version": "klax-v6.1-prerequisite-progress-v1",
        "campaign_id": state["campaign_id"],
        "recorded_at": stamp(),
        "status": "PREREQUISITE_DATA_AND_BASELINE_COMPLETE",
        "observations_2026_ready": True,
        "coherent_market_baseline_ready": True,
        "market_baseline_date_count": baseline["complete_market_vector_date_count"],
        "diagnostic_half_blend_beats_market_on_both_metrics": baseline[
            "fixed_half_blend_beats_market_on_both_metrics"],
        "diagnostic_only": True,
        "development_clock_started": False,
        "next_gate": "NESTED_WALK_FORWARD_PRECURSOR_EVALUATION",
        "confirmation_authorized": False,
        "protected_labels_accessed": False,
        "orders": 0,
        "bindings": {path: filehash(root / path) for path in inputs},
    })
    write(directory / "prerequisite-progress.json", progress)
    return progress


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = record(args.project_root)
    print({key: result[key] for key in (
        "campaign_id", "status", "market_baseline_date_count",
        "diagnostic_half_blend_beats_market_on_both_metrics",
        "development_clock_started", "next_gate")})


if __name__ == "__main__":
    main()

