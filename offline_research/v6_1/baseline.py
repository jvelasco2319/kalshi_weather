"""Build the first V6.1 market-only and fixed-blend forecast baselines."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
from statistics import mean

from v5a.development_search import _contains
from v5b.evaluation import load_development

from .common import filehash, seal, stamp, write
from .market_projection import coherent_market_probabilities


OUTPUT = Path("data/development/v6_1/market_baseline.json")


def quote_interval(yes: dict, no: dict) -> tuple[float, float, bool]:
    """Return an outcome-blind YES-probability interval from both book sides."""
    lower, upper = [0.0], [1.0]
    if yes.get("bid_price_cents") is not None:
        lower.append(float(yes["bid_price_cents"]) / 100)
    if yes.get("execution_price_cents") is not None:
        upper.append(float(yes["execution_price_cents"]) / 100)
    if no.get("execution_price_cents") is not None:
        lower.append(1 - float(no["execution_price_cents"]) / 100)
    if no.get("bid_price_cents") is not None:
        upper.append(1 - float(no["bid_price_cents"]) / 100)
    lo, hi = max(lower), min(upper)
    crossed = lo > hi
    if crossed:
        # Fixed before label access: asynchronous/complement-crossed intervals collapse
        # to the midpoint of the conflicting bounds.
        lo = hi = min(1.0, max(0.0, (lo + hi) / 2))
    return lo, hi, crossed


def _scores(probabilities: list[float], winners: list[int]) -> tuple[float, float]:
    winner = winners.index(1)
    return (sum((probability - outcome) ** 2
                for probability, outcome in zip(probabilities, winners)),
            -math.log(max(probabilities[winner], 1e-12)))


def build(root: Path) -> dict:
    root = Path(root).resolve()
    context = load_development(root)
    records, omitted = [], []
    for date in context["dates"]:
        rows = context["rows_by_date"][date]
        by_ticker = {}
        for row in rows:
            by_ticker.setdefault(row["market_ticker"], {})[row["contract_side"]] = row
        ordered = sorted(by_ticker, key=lambda ticker: (
            -10000 if by_ticker[ticker]["YES"]["floor_strike"] is None
            else float(by_ticker[ticker]["YES"]["floor_strike"]), ticker))
        if any("YES" not in by_ticker[ticker] or "NO" not in by_ticker[ticker] for ticker in ordered):
            omitted.append({"climate_date": date, "reason": "missing_contract_side"})
            continue
        intervals, crossed = [], 0
        for ticker in ordered:
            yes, no = by_ticker[ticker]["YES"], by_ticker[ticker]["NO"]
            quote_values = [yes.get("bid_price_cents"), yes.get("execution_price_cents"),
                            no.get("bid_price_cents"), no.get("execution_price_cents")]
            if all(value is None for value in quote_values):
                break
            lo, hi, was_crossed = quote_interval(yes, no)
            intervals.append({"bracket_id": ticker, "lower": lo, "upper": hi})
            crossed += int(was_crossed)
        if len(intervals) != len(ordered):
            omitted.append({"climate_date": date, "reason": "incomplete_quote_vector"})
            continue
        market = coherent_market_probabilities(intervals)
        market_probabilities = [item["probability"] for item in market["probabilities"]]
        weather_probabilities = [context["probabilities"][(date, ticker)] for ticker in ordered]
        if abs(sum(weather_probabilities) - 1) > 1e-8:
            raise ValueError("Frozen weather probability vector is incoherent")
        blend = [(market_value + weather_value) / 2
                 for market_value, weather_value in zip(market_probabilities, weather_probabilities)]
        actual = context["labels"][date]
        winners = [int(_contains(by_ticker[ticker]["YES"], actual)) for ticker in ordered]
        if sum(winners) != 1:
            raise ValueError("Development outcome is not mutually exclusive and exhaustive")
        market_brier, market_log = _scores(market_probabilities, winners)
        weather_brier, weather_log = _scores(weather_probabilities, winners)
        blend_brier, blend_log = _scores(blend, winners)
        source = (by_ticker[ordered[0]]["YES"].get("settlement_sources") or [{}])[0].get("name")
        records.append({
            "climate_date": date,
            "settlement_source": source,
            "reported_high_f": actual,
            "bracket_ids": ordered,
            "winning_bracket_id": ordered[winners.index(1)],
            "market_probabilities": market_probabilities,
            "weather_probabilities": weather_probabilities,
            "fixed_half_blend_probabilities": blend,
            "market_brier": market_brier,
            "market_log_loss": market_log,
            "weather_brier": weather_brier,
            "weather_log_loss": weather_log,
            "fixed_half_blend_brier": blend_brier,
            "fixed_half_blend_log_loss": blend_log,
            "market_intervals_relaxed": market["intervals_relaxed"],
            "crossed_contract_intervals": crossed,
        })
    if not records:
        raise ValueError("No complete contemporaneous market vectors")

    folds = []
    for fold in range(5):
        selected = [row for index, row in enumerate(records)
                    if min(4, index * 5 // len(records)) == fold]
        folds.append({
            "fold": fold + 1,
            "date_count": len(selected),
            "market_brier": mean(row["market_brier"] for row in selected),
            "market_log_loss": mean(row["market_log_loss"] for row in selected),
            "weather_brier": mean(row["weather_brier"] for row in selected),
            "weather_log_loss": mean(row["weather_log_loss"] for row in selected),
            "fixed_half_blend_brier": mean(row["fixed_half_blend_brier"] for row in selected),
            "fixed_half_blend_log_loss": mean(row["fixed_half_blend_log_loss"] for row in selected),
        })
    bindings = [
        "data/manifests/v5a_outcome_blind_universe.json",
        "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json",
        "data/normalized/v5a/frozen_leader_probabilities.parquet",
        "data/development/v5a/development_labels.json",
        "data/manifests/v6_1_observations_2026.json",
        "v6_1/market_projection.py",
        "v6_1/baseline.py",
    ]
    market_brier = mean(row["market_brier"] for row in records)
    market_log = mean(row["market_log_loss"] for row in records)
    weather_brier = mean(row["weather_brier"] for row in records)
    weather_log = mean(row["weather_log_loss"] for row in records)
    blend_brier = mean(row["fixed_half_blend_brier"] for row in records)
    blend_log = mean(row["fixed_half_blend_log_loss"] for row in records)
    document = seal({
        "schema_version": "klax-v6.1-market-baseline-v1",
        "created_at": stamp(),
        "status": "DEVELOPMENT_BASELINE_COMPLETE",
        "development_only": True,
        "decision_time_utc": "18:00",
        "complete_market_vector_date_count": len(records),
        "omitted_date_count": len(omitted),
        "omitted_dates": omitted,
        "settlement_source_counts": {
            source: sum(row["settlement_source"] == source for row in records)
            for source in sorted({row["settlement_source"] for row in records})
        },
        "aggregate_metrics": {
            "market_only": {"multiclass_brier": market_brier, "clipped_log_loss": market_log},
            "frozen_weather_only": {"multiclass_brier": weather_brier, "clipped_log_loss": weather_log},
            "fixed_half_market_weather_diagnostic": {"multiclass_brier": blend_brier, "clipped_log_loss": blend_log},
        },
        "fixed_half_blend_beats_market_on_both_metrics": blend_brier < market_brier and blend_log < market_log,
        "fixed_half_blend_is_promotable": False,
        "reason_not_promotable": "Diagnostic fixed blend has not passed nested walk-forward, bootstrap, or multiple-testing gates.",
        "folds": folds,
        "records": records,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "orders": 0,
        "bindings": {path: filehash(root / path) for path in bindings},
    })
    write(root / OUTPUT, document)
    return document


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = build(args.project_root)
    print({
        "status": result["status"],
        "complete_market_vector_date_count": result["complete_market_vector_date_count"],
        "omitted_date_count": result["omitted_date_count"],
        "aggregate_metrics": result["aggregate_metrics"],
        "fixed_half_blend_beats_market_on_both_metrics": result["fixed_half_blend_beats_market_on_both_metrics"],
    })


if __name__ == "__main__":
    main()

