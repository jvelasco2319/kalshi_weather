"""Corrected paid-book-only V6.1 market baseline with explicit ID alignment."""
from __future__ import annotations

import argparse
from datetime import datetime
import math
from pathlib import Path
from statistics import mean

from v5a.development_search import _contains
from v5b.evaluation import load_development

from .common import filehash, read, seal, stamp, write
from .market_projection import coherent_market_probabilities


EXECUTION = Path("data/manifests/v5a_paid_execution_outcome_blind.json")
OUTPUT = Path("data/development/v6_1/market_baseline_v2.json")


def _scores(probabilities: list[float], winners: list[int]) -> tuple[float, float]:
    winner = winners.index(1)
    return (sum((probability - outcome) ** 2
                for probability, outcome in zip(probabilities, winners)),
            -math.log(max(probabilities[winner], 1e-12)))


def _paid_interval(rows: list[dict]) -> tuple[float, float]:
    lower, upper = [0.0], [1.0]
    for row in rows:
        side = row["contract_side"]
        bid, ask = row.get("best_bid_cents"), row.get("best_ask_cents")
        if side == "YES":
            if bid is not None:
                lower.append(float(bid) / 100)
            if ask is not None:
                upper.append(float(ask) / 100)
        else:
            if ask is not None:
                lower.append(1 - float(ask) / 100)
            if bid is not None:
                upper.append(1 - float(bid) / 100)
    lo, hi = max(lower), min(upper)
    if lo > hi:
        lo = hi = (lo + hi) / 2
    return lo, hi


def build(root: Path) -> dict:
    root = Path(root).resolve()
    context = load_development(root)
    execution = read(root / EXECUTION)
    paid = {}
    for row in execution["records"]:
        if row["climate_date"] not in set(context["dates"]):
            continue
        if row["evidence_grade"] not in {"A", "B_PLUS"}:
            continue
        if row["book_state"] != "VERIFIED" or not 0 <= float(row["quote_age_ms"]) <= 60000:
            continue
        quote = datetime.fromisoformat(row["quote_at_utc"].replace("Z", "+00:00"))
        arrival = datetime.fromisoformat(row["arrival_at_utc"].replace("Z", "+00:00"))
        if quote > arrival:
            raise ValueError("Post-arrival quote rejected")
        paid.setdefault((row["climate_date"], row["market_ticker"]), []).append(row)

    records, omitted = [], []
    for date in context["dates"]:
        universe = context["rows_by_date"][date]
        yes_rows = sorted([row for row in universe if row["contract_side"] == "YES"],
                          key=lambda row: (-10000 if row["floor_strike"] is None
                                           else float(row["floor_strike"]), row["market_ticker"]))
        tickers = [row["market_ticker"] for row in yes_rows]
        if any((date, ticker) not in paid for ticker in tickers):
            omitted.append({"climate_date": date, "reason": "incomplete_paid_book_vector"})
            continue
        intervals = []
        grades = []
        for ticker in tickers:
            rows = paid[date, ticker]
            lo, hi = _paid_interval(rows)
            intervals.append({"bracket_id": ticker, "lower": lo, "upper": hi})
            grades.extend(row["evidence_grade"] for row in rows)
        projection = coherent_market_probabilities(intervals)
        probability_by_id = {row["bracket_id"]: row["probability"]
                             for row in projection["probabilities"]}
        market = [probability_by_id[ticker] for ticker in tickers]
        weather = [context["probabilities"][(date, ticker)] for ticker in tickers]
        blend = [(market_value + weather_value) / 2
                 for market_value, weather_value in zip(market, weather)]
        actual = context["labels"][date]
        winners = [int(_contains(row, actual)) for row in yes_rows]
        if sum(winners) != 1:
            raise ValueError("Bracket universe is not exhaustive")
        market_brier, market_log = _scores(market, winners)
        weather_brier, weather_log = _scores(weather, winners)
        blend_brier, blend_log = _scores(blend, winners)
        records.append({
            "climate_date": date, "bracket_ids_in_settlement_order": tickers,
            "winning_bracket_id": tickers[winners.index(1)],
            "market_probabilities": market, "weather_probabilities": weather,
            "fixed_half_blend_probabilities": blend,
            "market_brier": market_brier, "market_log_loss": market_log,
            "weather_brier": weather_brier, "weather_log_loss": weather_log,
            "fixed_half_blend_brier": blend_brier, "fixed_half_blend_log_loss": blend_log,
            "market_intervals_relaxed": projection["intervals_relaxed"],
            "paid_quote_grade_counts": {grade: grades.count(grade) for grade in ("A", "B_PLUS")},
        })
    if not records:
        raise ValueError("No complete paid-book probability vectors")
    metrics = {
        "market_only": {"multiclass_brier": mean(row["market_brier"] for row in records),
                        "clipped_log_loss": mean(row["market_log_loss"] for row in records)},
        "frozen_weather_only": {"multiclass_brier": mean(row["weather_brier"] for row in records),
                                "clipped_log_loss": mean(row["weather_log_loss"] for row in records)},
        "fixed_half_market_weather_diagnostic": {
            "multiclass_brier": mean(row["fixed_half_blend_brier"] for row in records),
            "clipped_log_loss": mean(row["fixed_half_blend_log_loss"] for row in records)},
    }
    folds = []
    for fold in range(5):
        rows = [row for index, row in enumerate(records) if min(4, index * 5 // len(records)) == fold]
        folds.append({"fold": fold + 1, "date_count": len(rows),
                      "market_brier": mean(row["market_brier"] for row in rows),
                      "weather_brier": mean(row["weather_brier"] for row in rows),
                      "blend_brier": mean(row["fixed_half_blend_brier"] for row in rows),
                      "market_log_loss": mean(row["market_log_loss"] for row in rows),
                      "weather_log_loss": mean(row["weather_log_loss"] for row in rows),
                      "blend_log_loss": mean(row["fixed_half_blend_log_loss"] for row in rows)})
    bindings = [EXECUTION.as_posix(), "data/manifests/v5a_outcome_blind_universe.json",
                "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json",
                "data/normalized/v5a/frozen_leader_probabilities.parquet",
                "data/development/v5a/development_labels.json", "v6_1/market_projection.py",
                "v6_1/baseline_v2.py"]
    result = seal({
        "schema_version": "klax-v6.1-market-baseline-v2",
        "created_at": stamp(), "status": "CORRECTED_DEVELOPMENT_BASELINE_COMPLETE",
        "supersedes": "data/development/v6_1/market_baseline.json",
        "superseded_reason": "V1 mixed proxy evidence and discarded projection bracket IDs, misaligning probabilities with outcomes.",
        "decision_time_utc": "18:00", "registered_arrival_delay_seconds": 5,
        "maximum_paid_quote_age_ms": 60000, "grade_a_primary": False,
        "evidence_scope": "A and B+ paid full-book quote vectors for forecast diagnostics only",
        "complete_market_vector_date_count": len(records), "omitted_date_count": len(omitted),
        "omitted_dates": omitted, "aggregate_metrics": metrics,
        "fixed_half_blend_beats_market_on_both_metrics": (
            metrics["fixed_half_market_weather_diagnostic"]["multiclass_brier"] < metrics["market_only"]["multiclass_brier"]
            and metrics["fixed_half_market_weather_diagnostic"]["clipped_log_loss"] < metrics["market_only"]["clipped_log_loss"]),
        "fixed_half_blend_is_promotable": False, "folds": folds, "records": records,
        "protected_confirmation_labels_read": False, "network_used": False, "orders": 0,
        "bindings": {path: filehash(root / path) for path in bindings},
    })
    write(root / OUTPUT, result)
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = build(args.project_root)
    print({key: result[key] for key in ("status", "complete_market_vector_date_count",
                                       "omitted_date_count", "aggregate_metrics",
                                       "fixed_half_blend_beats_market_on_both_metrics")})


if __name__ == "__main__":
    main()

