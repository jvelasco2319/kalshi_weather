"""Build a hash-bound V6.1 launch-readiness record without reading new labels."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from .common import checked, filehash, read, seal, stamp, write
from .date_ledger import OUTPUT as LEDGER_PATH, build as build_ledger


CONFIG = Path("configs/v6_1_campaign.json")
OUTPUT = Path("data/manifests/v6_1_readiness.json")
V5A_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
V5B_UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
V5B_RESULT = Path("data/manifests/v5b_untouched_confirmation_result.json")


def _grade_a_dates(records: list[dict]) -> set[str]:
    return {row["climate_date"] for row in records if row.get("execution_evidence_grade") == "A"}


def build(root: Path) -> dict:
    root = Path(root).resolve()
    config = read(root / CONFIG)
    if any((config["allow_network_during_experiment"], config["allow_orders"],
            config["allow_paper_orders"], config["allow_confirmation_labels"])):
        raise ValueError("V6.1 unsafe configuration")
    ledger = build_ledger(root)
    checked(root / LEDGER_PATH)
    v5a = read(root / V5A_UNIVERSE)
    v5b = read(root / V5B_UNIVERSE)
    result = read(root / V5B_RESULT)

    development_dates = sorted({
        row["climate_date"] for row in v5a["records"]
        if row.get("partition") == "development"
    })
    development_a = _grade_a_dates([
        row for row in v5a["records"] if row.get("partition") == "development"
    ])
    all_a = _grade_a_dates(v5a["records"] + v5b["records"])
    opened = {trade["climate_date"] for trade in result["trades"]}
    all_outcome_unopened_a = sorted(all_a - opened)
    outcome_unopened_a = sorted(all_a - opened - set(development_dates))
    current_weather_dates = sorted(
        path.name.removeprefix("date=")
        for path in (root / "data/normalized/v5p_probability_features").glob("date=20??-??-??")
        if path.is_dir()
    )
    observation_2026 = list((root / "data/normalized/v6_1_observations_2026").glob("**/*.parquet"))

    gates = {
        "completed_v6_preserved": (root / "runs/campaigns_v6/v6-development-20260927T214208879719Z/recovery-state.json").is_file(),
        "global_exposure_ledger_valid": ledger["confirmation_eligible_date_count"] == 0,
        "offline_experiment_enforced": not config["allow_network_during_experiment"],
        "orders_disabled": not config["allow_orders"] and not config["allow_paper_orders"],
        "confirmation_labels_disabled": not config["allow_confirmation_labels"],
        "development_cohort_ready": len(development_dates) == 64,
        "forecast_cache_ready": len(current_weather_dates) == 142,
        "exact_event_rules_ready": bool(v5a.get("source_bindings")),
        "historical_fee_binding_ready": (root / "data/raw/v5b_untouched/economics/manifest.json").is_file(),
        "observations_2026_ready": bool(observation_2026),
        "coherent_market_projection_implemented": (root / "v6_1/market_projection.py").is_file(),
        "minimum_confirmation_grade_a_days": ledger["confirmation_eligible_date_count"] >= config["minimum_confirmation_grade_a_days"],
    }
    blocking = [key for key, value in gates.items() if not value]
    status = "READY_FOR_DEVELOPMENT_PREREQUISITES_ONLY" if all(
        gates[key] for key in (
            "completed_v6_preserved", "global_exposure_ledger_valid",
            "offline_experiment_enforced", "orders_disabled",
            "confirmation_labels_disabled", "development_cohort_ready",
            "forecast_cache_ready", "exact_event_rules_ready",
            "historical_fee_binding_ready")
    ) else "NOT_READY"
    grade_counts = Counter(row.get("execution_evidence_grade") for row in v5a["records"] + v5b["records"])
    bindings = [CONFIG, LEDGER_PATH, V5A_UNIVERSE, V5B_UNIVERSE, V5B_RESULT,
                Path("docs/V6_1_SETTLEMENT_ALIGNED_CAMPAIGN_GOAL.md")]
    document = seal({
        "schema_version": "klax-v6.1-readiness-v1",
        "created_at": stamp(),
        "status": status,
        "launch_mode": "DEVELOPMENT_ONLY",
        "phase": "PREREQUISITE_AND_MARKET_BASELINE_BUILD",
        "development_dates": development_dates,
        "development_date_count": len(development_dates),
        "development_grade_a_date_count": len(development_a),
        "weather_feature_date_count": len(current_weather_dates),
        "weather_feature_first_date": current_weather_dates[0] if current_weather_dates else None,
        "weather_feature_last_date": current_weather_dates[-1] if current_weather_dates else None,
        "execution_grade_record_counts": dict(sorted(grade_counts.items())),
        "v5b_outcome_exposed_dates": sorted(opened),
        "all_outcome_unopened_grade_a_dates_including_development": all_outcome_unopened_a,
        "all_outcome_unopened_grade_a_date_count_including_development": len(all_outcome_unopened_a),
        "outcome_unopened_grade_a_dates": outcome_unopened_a,
        "outcome_unopened_grade_a_date_count": len(outcome_unopened_a),
        "current_confirmation_eligible_grade_a_date_count": 0,
        "minimum_confirmation_grade_a_days": config["minimum_confirmation_grade_a_days"],
        "confirmation_shortfall_days": config["minimum_confirmation_grade_a_days"],
        "confirmation_policy": "All current May-September candidate pools are selection-exposed; use a new outcome-blind cohort.",
        "gates": gates,
        "blocking_gates": blocking,
        "colonies": {
            "settlement_forecasting": {
                "status": "PARTIAL_INPUT_READY",
                "blocker": "2026 as-of local observations and numeric Weather Company target are unavailable"
            },
            "market_residuals": {
                "status": "READY_FOR_IMPLEMENTATION",
                "blocker": "coherent bid/ask probability projection is not yet implemented"
            },
            "execution_economics": {
                "status": "DIAGNOSTIC_ONLY",
                "blocker": f"only {len(development_a)} Grade-A development days"
            },
            "adversarial_validation": {
                "status": "ACTIVE_GOVERNANCE",
                "blocker": None
            }
        },
        "next_actions": [
            "Acquire and freeze 2026 as-of observations for KLAX, KHHR, KLGB, KSMO, and KTOA.",
            "Implement and test the preregistered coherent Kalshi market-probability projection.",
            "Build source-specific market-only and market-plus-weather walk-forward baselines.",
            "Acquire at least 100 new, outcome-blind Grade-A confirmation days before any one-shot confirmation."
        ],
        "protected_labels_read_by_v6_1": False,
        "network_used_by_readiness": False,
        "orders": 0,
        "bindings": {path.as_posix(): filehash(root / path) for path in bindings},
    })
    write(root / OUTPUT, document)
    return document


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = build(args.project_root)
    print({key: result[key] for key in (
        "status", "phase", "development_date_count", "development_grade_a_date_count",
        "outcome_unopened_grade_a_date_count", "current_confirmation_eligible_grade_a_date_count",
        "blocking_gates")})


if __name__ == "__main__":
    main()

