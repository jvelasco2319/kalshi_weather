"""Build the cross-campaign exposure ledger that supersedes stale holdout seals."""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from .common import filehash, read, seal, stamp, write


V5A_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
V5B_UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
V5B_RESULT = Path("data/manifests/v5b_untouched_confirmation_result.json")
OUTPUT = Path("data/manifests/v6_1_global_exposure_ledger.json")


def _access(campaign: str, role: str, *, features: bool = False, prices: bool = False,
            labels: bool = False, outcomes: bool = False,
            candidate_performance_seen: bool = False) -> dict:
    return {
        "campaign": campaign,
        "role": role,
        "weather_features_accessed": features,
        "market_prices_accessed": prices,
        "labels_accessed": labels,
        "outcomes_accessed": outcomes,
        "candidate_specific_performance_seen": candidate_performance_seen,
    }


def build(root: Path) -> dict:
    root = Path(root).resolve()
    sources = [V5A_UNIVERSE, V5B_UNIVERSE, V5B_RESULT]
    for relative in sources:
        if not (root / relative).is_file():
            raise FileNotFoundError(relative)
    v5a, v5b, result = (read(root / relative) for relative in sources)
    outcome_dates = {trade["climate_date"] for trade in result["trades"]}
    outcome_tickers = {trade["market_ticker"] for trade in result["trades"]}

    contracts: dict[tuple[str, str], dict] = {}
    provenance = defaultdict(set)
    for campaign, universe in (("v5a", v5a), ("v5b_untouched", v5b)):
        for row in universe["records"]:
            key = (row["climate_date"], row["market_ticker"])
            contract = contracts.setdefault(key, {
                "climate_date": row["climate_date"],
                "market_ticker": row["market_ticker"],
                "event_ticker": row.get("event_ticker"),
                "settlement_source": (row.get("settlement_sources") or [{}])[0].get("name"),
                "accesses": [],
            })
            provenance[key].add(campaign)
            date = row["climate_date"]
            if campaign == "v5a":
                if row.get("partition") == "development":
                    access = _access("v5a", "development", features=True, prices=True,
                                     labels=True, outcomes=True,
                                     candidate_performance_seen=True)
                    contract["accesses"].append(access)
                    contract["accesses"].append(_access(
                        "v6", "reused_development", features=True, prices=True,
                        labels=True, outcomes=True, candidate_performance_seen=True))
                else:
                    contract["accesses"].append(_access(
                        "v5a", "holdout_universe", features=True, prices=True))
            else:
                contract["accesses"].append(_access(
                    "v5b_untouched", "candidate_selection_pool", features=True,
                    prices=True, candidate_performance_seen=True))
            if date in outcome_dates:
                contract["accesses"].append(_access(
                    "v5b_untouched_confirmation", "one_shot_confirmation",
                    labels=True, outcomes=True, candidate_performance_seen=True))

    records = []
    for key in sorted(contracts):
        record = contracts[key]
        accesses = record["accesses"]
        outcome_exposed = any(item["outcomes_accessed"] for item in accesses)
        selection_exposed = any(
            item["role"] in {"development", "reused_development", "candidate_selection_pool"}
            for item in accesses
        )
        record.update({
            "source_universes": sorted(provenance[key]),
            "weather_features_exposed": any(item["weather_features_accessed"] for item in accesses),
            "market_prices_exposed": any(item["market_prices_accessed"] for item in accesses),
            "outcome_exposed": outcome_exposed,
            "selection_exposed": selection_exposed,
            "selected_confirmation_ticker": record["market_ticker"] in outcome_tickers,
            "final_confirmation_eligible": not (outcome_exposed or selection_exposed),
            "final_eligibility": (
                "PERMANENTLY_OUTCOME_EXPOSED" if outcome_exposed else "DEVELOPMENT_ONLY_SELECTION_EXPOSED"
            ),
        })
        records.append(record)

    date_summary = {}
    for record in records:
        date = record["climate_date"]
        summary = date_summary.setdefault(date, {
            "contract_count": 0,
            "outcome_exposed": False,
            "selection_exposed": False,
            "final_confirmation_eligible": True,
        })
        summary["contract_count"] += 1
        summary["outcome_exposed"] |= record["outcome_exposed"]
        summary["selection_exposed"] |= record["selection_exposed"]
        summary["final_confirmation_eligible"] &= record["final_confirmation_eligible"]

    document = seal({
        "schema_version": "klax-v6.1-global-exposure-ledger-v1",
        "created_at": stamp(),
        "policy": "Any feature/price use in candidate selection blocks final confirmation; any opened daily outcome permanently exposes every bracket for that date.",
        "stale_seal_override": "data/manifests/v5a_holdout_seal.json cannot grant eligibility after V5B confirmation",
        "source_bindings": {relative.as_posix(): filehash(root / relative) for relative in sources},
        "record_count": len(records),
        "date_count": len(date_summary),
        "outcome_exposed_date_count": sum(item["outcome_exposed"] for item in date_summary.values()),
        "selection_exposed_date_count": sum(item["selection_exposed"] for item in date_summary.values()),
        "confirmation_eligible_date_count": sum(
            item["final_confirmation_eligible"] for item in date_summary.values()),
        "date_summary": date_summary,
        "records": records,
    })
    write(root / OUTPUT, document)
    return document


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = build(args.project_root)
    print({key: result[key] for key in (
        "date_count", "record_count", "outcome_exposed_date_count",
        "selection_exposed_date_count", "confirmation_eligible_date_count")})


if __name__ == "__main__":
    main()

