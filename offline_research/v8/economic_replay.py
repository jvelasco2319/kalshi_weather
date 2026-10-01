"""Freeze and score the fixed V8 probability repair on the exposed V7I replay.

The freeze phase fits the already-selected repair on exposed 2025 development
labels, then reconstructs all 2026 probabilities and strict orders without
reading a 2026 settlement.  The score phase is separate and reports the
previously exposed 2026 period as stability evidence, never confirmation.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

import numpy as np

from past7_replay.audited import _apply_arrival, _choose_no_order
from past7_replay.engine import (
    _decimal_text,
    _fee,
    _file_hash,
    _ordered_yes,
    _probability_rows,
    _read_json,
    _seal,
    _write_immutable_csv,
    _write_immutable_json,
)
from v7_shadow.daily import V5B_STRATEGY
from v7i_replay.engine import (
    OUTCOMES as V7I_OUTCOMES,
    TARGET_DATES,
    _load_books,
    _load_outcomes,
    _load_universe,
)
from v8 import probability_repair as repair


CONFIG = Path("configs/v8_campaign.json")
RUN = Path("runs/campaigns_v8/v8-offline-20260930T004226Z")
FREEZE = RUN / "probability-order-freeze.json"
SCORE = RUN / "score.json"
DAILY = RUN / "daily-results.csv"
FOLDS = RUN / "fold-results.csv"
REPORT = RUN / "REPORT.md"
V7I_FREEZE = Path("runs/replays/v7i-historical-20260730-20260927/prediction-order-freeze.json")
V7I_SCORE = Path("runs/replays/v7i-historical-20260730-20260927/scored-results.json")
PRIMARY_START = "2026-08-04"
LEADER_ID = "rolling_confusion-alpha-2-w-0.75"


class V8ReplayError(ValueError):
    pass


def _load_config(root: Path) -> dict[str, Any]:
    value = _read_json(root / CONFIG)
    if (
        value.get("schema_version") != "klax-v8-probability-repair-and-replay-v1"
        or value.get("campaign_id") != RUN.name
        or value.get("frozen_leader", {}).get("candidate_id") != LEADER_ID
        or value.get("safety") != {
            "network_allowed": False,
            "paper_orders_allowed": False,
            "live_orders_allowed": False,
            "current_or_live_market_feed_allowed": False,
            "claims_of_untouched_confirmation_allowed": False,
        }
    ):
        raise V8ReplayError("V8 campaign registration differs")
    for section, path_key, hash_key in (
        ("forecast_development", "probability_spec", "probability_spec_sha256"),
        ("forecast_development", "probability_result", "probability_result_sha256"),
        ("forward_stability_replay", "frozen_v7i_input", "frozen_v7i_input_sha256"),
    ):
        relative = Path(value[section][path_key])
        if _file_hash(root / relative) != str(value[section][hash_key]).lower():
            raise V8ReplayError(f"registered input hash differs: {relative}")
    return value


def _leader() -> repair.Candidate:
    candidates = {candidate.candidate_id: candidate for candidate in repair.catalog()}
    candidate = candidates.get(LEADER_ID)
    if candidate != repair.Candidate(
        LEADER_ID, "rolling_confusion", (("prior", 12.0), ("weight", 0.75))
    ):
        raise V8ReplayError("fixed V8 candidate differs")
    return candidate


def _fit_mapping(examples: Sequence[repair.Example]) -> tuple[list[list[float]], list[list[float]]]:
    counts = [[2.0] * 6 for _ in range(6)]
    for row in examples:
        mode = max(range(6), key=lambda index: (row.base[index], index))
        counts[mode][row.actual_index] += 1.0
    distributions = [[value / sum(row) for value in row] for row in counts]
    return counts, distributions


def _repaired(base: Sequence[float], distributions: Sequence[Sequence[float]]) -> list[float]:
    if len(base) != 6 or len(distributions) != 6:
        raise V8ReplayError("six-bracket repair inputs required")
    p = [float(value) for value in base]
    if any(not math.isfinite(value) or value < 0 for value in p) or not math.isclose(sum(p), 1.0, abs_tol=1e-8):
        raise V8ReplayError("invalid base probability vector")
    mode = max(range(6), key=lambda index: (p[index], index))
    output = [0.25 * value + 0.75 * float(other) for value, other in zip(p, distributions[mode], strict=True)]
    total = sum(output)
    result = [value / total for value in output]
    if min(result) <= 0 or not math.isclose(sum(result), 1.0, abs_tol=1e-12):
        raise V8ReplayError("repaired probability vector invalid")
    return result


def build_freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = _load_config(root)
    probability_result = _read_json(root / config["forecast_development"]["probability_result"])
    repair._verify_seal(probability_result)
    if (
        probability_result.get("status") != "COMPLETE_DEVELOPMENT_ONLY"
        or probability_result.get("leader", {}).get("candidate_id") != LEADER_ID
        or probability_result["leader"].get("passes_all_development_gates") is not True
    ):
        raise V8ReplayError("valid probability-development leader required")
    examples, training_bindings = repair._load_examples(root)
    if len(examples) != 359:
        raise V8ReplayError("V8 training population differs")
    counts, distributions = _fit_mapping(examples)
    candidate = _leader()

    v7i = _read_json(root / V7I_FREEZE, sealed=True)
    if (
        v7i.get("status") != "FROZEN_BEFORE_OUTCOME_READ"
        or v7i.get("outcomes_read") is not False
        or v7i.get("settlement_labels_read") is not False
        or tuple(v7i.get("target_dates", ())) != TARGET_DATES
    ):
        raise V8ReplayError("valid V7I pre-outcome freeze required")
    records = v7i["methods"]["v5b_causal_no"]["records"]
    if len(records) != 60:
        raise V8ReplayError("V7I record count differs")
    by_day, universe_bindings = _load_universe(root)
    strategy_freeze = _read_json(root / V5B_STRATEGY, sealed=True)
    parameters = strategy_freeze["parameters"]
    bindings = dict(v7i["input_bindings"])
    bindings.update(training_bindings)
    bindings.update(universe_bindings)
    for relative in (CONFIG, V7I_FREEZE, V5B_STRATEGY, Path(config["forecast_development"]["probability_result"]), repair.SPEC):
        bindings[relative.as_posix()] = _file_hash(root / relative)

    output_records: list[dict[str, Any]] = []
    for source_record in records:
        day = source_record["climate_date"]
        contracts = _ordered_yes(by_day[day])
        source_rows = sorted(source_record["probabilities"], key=lambda row: int(row["contract_order"]))
        tickers = [row["market_ticker"] for row in source_rows]
        if tickers != [row["market_ticker"] for row in contracts]:
            raise V8ReplayError(f"V7I bracket order differs: {day}")
        base = [float(row["yes_probability"]) for row in source_rows]
        probabilities = _repaired(base, distributions)
        # Cross-check the frozen mapping against the probability colony's exact transform.
        canonical = repair.transform(base, candidate, examples)
        if any(not math.isclose(left, right, abs_tol=1e-15) for left, right in zip(probabilities, canonical, strict=True)):
            raise V8ReplayError(f"repair implementation differs: {day}")
        mapped = dict(zip(tickers, probabilities, strict=True))
        snapshots, book_bindings, source = _load_books(root, day, contracts)
        bindings.update(book_bindings)
        decision = _choose_no_order(day, mapped, contracts, snapshots[0], parameters)
        order = _apply_arrival(decision, day, snapshots[5])
        output_records.append({
            "climate_date": day,
            "evaluation_partition": "diagnostic_overlap" if day < PRIMARY_START else "exposed_forward_stability_primary",
            "event_ticker": source_record["event_ticker"],
            "decision_at_utc": source_record["decision_at_utc"],
            "arrival_at_utc": source_record["arrival_at_utc"],
            "execution_source": source,
            "base_probabilities": _probability_rows(dict(zip(tickers, base, strict=True)), contracts),
            "repaired_probabilities": _probability_rows(mapped, contracts),
            "order": order,
        })
    if [row["climate_date"] for row in output_records] != list(TARGET_DATES):
        raise V8ReplayError("V8 replay date coverage differs")

    artifact = _seal({
        "schema_version": "klax-v8-probability-order-freeze-v1",
        "campaign_id": RUN.name,
        "status": "FROZEN_BEFORE_2026_OUTCOME_READ",
        "candidate_id": LEADER_ID,
        "fit_population": {
            "date_count": len(examples),
            "start": examples[0].climate_date,
            "end": examples[-1].climate_date,
            "status": "EXPOSED_2025_DEVELOPMENT",
        },
        "modal_confusion_counts_with_dirichlet_prior": counts,
        "conditional_distributions_by_base_modal_position": distributions,
        "base_weight": 0.25,
        "conditional_weight": 0.75,
        "target_dates": list(TARGET_DATES),
        "primary_start": PRIMARY_START,
        "records": output_records,
        "input_bindings": dict(sorted(bindings.items())),
        "2026_label_updates_used": False,
        "2026_outcomes_read": False,
        "statistically_untouched": False,
        "evidence_status": "PREVIOUSLY_EXPOSED_CAUSAL_STABILITY_REPLAY",
        "network_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "order_authorization_count": 0,
    })
    _write_immutable_json(root / FREEZE, artifact)
    return artifact


def _summary(rows: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    fills = [row for row in rows if row["execution_status"] == "FILLED"]
    outlay = sum((Decimal(str(row["entry_outlay_dollars"])) for row in fills), Decimal(0))
    profit = sum((Decimal(str(row["net_profit_dollars"])) for row in fills), Decimal(0))
    return {
        "period": name,
        "date_count": len(rows),
        "start_date": min(row["climate_date"] for row in rows),
        "end_date": max(row["climate_date"] for row in rows),
        "frozen_order_count": sum(row["decision_status"] == "ORDER_FROZEN" for row in rows),
        "filled_count": len(fills),
        "win_count": sum(bool(row["won"]) for row in fills),
        "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
        "mean_log_loss": mean(float(row["log_loss"]) for row in rows),
        "zero_true_probability_count": sum(float(row["true_bracket_probability"]) <= 0 for row in rows),
        "total_entry_outlay_dollars": _decimal_text(outlay),
        "total_net_profit_dollars": _decimal_text(profit),
        "aggregate_return": None if not outlay else _decimal_text(profit / outlay),
    }


def _periods(rows: Sequence[Mapping[str, Any]], dates: Sequence[str], width: int) -> list[dict[str, Any]]:
    output = []
    for start in range(0, len(dates), width):
        members = set(dates[start:start + width])
        output.append(_summary([row for row in rows if row["climate_date"] in members], f"fold_{start // width + 1}"))
    return output


def _bootstrap_lower(rows: Sequence[Mapping[str, Any]], primary_dates: Sequence[str], config: Mapping[str, Any]) -> float:
    by_day = {day: (0.0, 0.0) for day in primary_dates}
    for row in rows:
        if row["execution_status"] == "FILLED":
            by_day[row["climate_date"]] = (float(row["entry_outlay_dollars"]), float(row["net_profit_dollars"]))
    daily = [by_day[day] for day in primary_dates]
    rng = np.random.default_rng(int(config["bootstrap_seed"]))
    block = int(config["bootstrap_block_days"])
    returns = []
    for _ in range(int(config["bootstrap_draws"])):
        sample: list[tuple[float, float]] = []
        while len(sample) < len(daily):
            begin = int(rng.integers(0, len(daily)))
            sample.extend(daily[(begin + offset) % len(daily)] for offset in range(block))
        sample = sample[:len(daily)]
        outlay = sum(item[0] for item in sample)
        returns.append(sum(item[1] for item in sample) / outlay if outlay else 0.0)
    return float(np.quantile(np.asarray(returns), 0.05, method="linear"))


def score(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = _load_config(root)
    freeze = _read_json(root / FREEZE, sealed=True)
    if (
        freeze.get("status") != "FROZEN_BEFORE_2026_OUTCOME_READ"
        or freeze.get("2026_outcomes_read") is not False
        or freeze.get("candidate_id") != LEADER_ID
    ):
        raise V8ReplayError("valid V8 pre-outcome freeze required")
    for relative, expected in freeze["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V8ReplayError(f"frozen input changed: {relative}")
    ticker_to_day = {
        item["market_ticker"]: record["climate_date"]
        for record in freeze["records"]
        for item in record["repaired_probabilities"]
    }
    outcomes = _load_outcomes(root / V7I_OUTCOMES, set(ticker_to_day), ticker_to_day)
    rows: list[dict[str, Any]] = []
    for record in freeze["records"]:
        day = record["climate_date"]
        probabilities = {item["market_ticker"]: float(item["yes_probability"]) for item in record["repaired_probabilities"]}
        winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
        true_probability = probabilities[winner]
        order = record["order"]
        row: dict[str, Any] = {
            "climate_date": day,
            "evaluation_partition": record["evaluation_partition"],
            "winning_market_ticker": winner,
            "true_bracket_probability": true_probability,
            "multiclass_brier": sum((value - float(ticker == winner)) ** 2 for ticker, value in probabilities.items()),
            "log_loss": -math.log(max(true_probability, 1e-15)),
            "decision_status": order["decision_status"],
            "decision_reason": order.get("decision_reason"),
            "selected_market_ticker": order.get("market_ticker"),
            "contract_side": order.get("contract_side"),
            "execution_status": order["execution_status"],
            "execution_reason": order.get("execution_reason"),
            "fill_price_cents": order.get("fill_price_cents"),
            "won": None,
            "entry_outlay_dollars": None,
            "net_profit_dollars": None,
        }
        if order["execution_status"] == "FILLED":
            won = outcomes[day][order["market_ticker"]] == order["contract_side"]
            outlay = Decimal(order["fill_entry_outlay_dollars"])
            row.update({
                "won": won,
                "entry_outlay_dollars": _decimal_text(outlay),
                "net_profit_dollars": _decimal_text(Decimal(int(won)) - outlay),
            })
        rows.append(row)

    primary_dates = tuple(day for day in TARGET_DATES if day >= PRIMARY_START)
    primary_rows = [row for row in rows if row["climate_date"] >= PRIMARY_START]
    full = _summary(rows, "full_60_diagnostic")
    primary = _summary(primary_rows, "primary_55")
    folds = _periods(primary_rows, primary_dates, 11)
    fills = [row for row in primary_rows if row["execution_status"] == "FILLED"]
    best_removed = sorted(fills, key=lambda row: Decimal(row["net_profit_dollars"]), reverse=True)[1:]
    removed_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in best_removed), Decimal(0))
    removed_profit = sum((Decimal(row["net_profit_dollars"]) for row in best_removed), Decimal(0))
    stress_cents = int(config["economic_research_screen"]["adverse_entry_stress_cents"])
    stress_outlay = Decimal(0)
    stress_profit = Decimal(0)
    for row in fills:
        price = min(99, int(row["fill_price_cents"]) + stress_cents)
        outlay = Decimal(price) / Decimal(100) + _fee(row["climate_date"], price)
        stress_outlay += outlay
        stress_profit += Decimal(int(bool(row["won"]))) - outlay
    bootstrap_lower = _bootstrap_lower(primary_rows, primary_dates, config["economic_research_screen"])
    positive_folds = sum(item["aggregate_return"] is not None and Decimal(item["aggregate_return"]) > 0 for item in folds)
    gates = {
        "minimum_filled_days": primary["filled_count"] >= int(config["economic_research_screen"]["minimum_filled_days"]),
        "aggregate_return_at_least_10pct": primary["aggregate_return"] is not None and Decimal(primary["aggregate_return"]) >= Decimal(str(config["economic_research_screen"]["minimum_aggregate_return"])),
        "minimum_positive_folds": positive_folds >= int(config["economic_research_screen"]["minimum_positive_folds_of_five"]),
        "positive_bootstrap_lower_bound": bootstrap_lower > 0,
        "positive_two_cent_stress": stress_profit > 0,
        "positive_best_trade_removed": removed_profit > 0,
    }
    original = _read_json(root / V7I_SCORE, sealed=True)["summaries"]["primary_55"]
    result = _seal({
        "schema_version": "klax-v8-exposed-forward-stability-score-v1",
        "campaign_id": RUN.name,
        "status": "COMPLETE_EXPOSED_STABILITY_REPLAY",
        "candidate_id": LEADER_ID,
        "prediction_order_freeze_sha256": freeze["self_sha256"],
        "outcome_binding": {V7I_OUTCOMES.as_posix(): _file_hash(root / V7I_OUTCOMES)},
        "summaries": {"primary_55": primary, "full_60_diagnostic": full},
        "original_v5b_primary_comparator": original,
        "five_chronological_folds": folds,
        "robustness": {
            "positive_folds_of_five": positive_folds,
            "moving_block_bootstrap_one_sided_95_lower": bootstrap_lower,
            "two_cent_stress_return": None if not stress_outlay else _decimal_text(stress_profit / stress_outlay),
            "best_trade_removed_return": None if not removed_outlay else _decimal_text(removed_profit / removed_outlay),
            "gates": gates,
            "passes_all_economic_research_gates": all(gates.values()),
        },
        "statistically_untouched": False,
        "evidence_status": "PREVIOUSLY_EXPOSED_CAUSAL_STABILITY_REPLAY",
        "ten_percent_confirmed": False,
        "network_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })
    _write_immutable_csv(root / DAILY, rows, list(rows[0]))
    _write_immutable_csv(root / FOLDS, folds, list(folds[0]))
    _write_immutable_json(root / SCORE, result)
    _write_report(root / REPORT, result)
    return result


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.2f}%"


def _write_report(path: Path, result: Mapping[str, Any]) -> None:
    primary = result["summaries"]["primary_55"]
    original = result["original_v5b_primary_comparator"]
    robust = result["robustness"]
    failed = [name for name, passed in robust["gates"].items() if not passed]
    lines = [
        "# V8 repaired-probability strict Kalshi replay",
        "",
        "The fixed V8 repair was fit on 359 exposed 2025 dates, frozen, and applied without 2026 label updates to the existing V7I 60-day replay. The primary period contains 55 previously exposed dates from August 4 through September 27, 2026.",
        "",
        "| Metric | Original V5B | V8 repaired |",
        "| --- | ---: | ---: |",
        f"| Brier score | {float(original['mean_multiclass_brier']):.4f} | {float(primary['mean_multiclass_brier']):.4f} |",
        f"| Log loss | {float(original['mean_clipped_log_loss']):.4f} | {float(primary['mean_log_loss']):.4f} |",
        f"| Filled trades | {int(original['filled_count'])} | {int(primary['filled_count'])} |",
        f"| Wins | {int(original['win_count'])} | {int(primary['win_count'])} |",
        f"| Return after fees | {_pct(original['aggregate_realized_net_return'])} | {_pct(primary['aggregate_return'])} |",
        "",
        f"V8 produced **{primary['filled_count']} fills**, **{primary['win_count']} wins**, and **{_pct(primary['aggregate_return'])}** simulated return after fees. Its one-sided 95% moving-block-bootstrap lower bound was **{_pct(robust['moving_block_bootstrap_one_sided_95_lower'])}**. The two-cent stress return was **{_pct(robust['two_cent_stress_return'])}**, and removing the best trade left **{_pct(robust['best_trade_removed_return'])}**.",
        "",
        f"It passed all economic research gates: **{robust['passes_all_economic_research_gates']}**. Failed gates: {', '.join(failed) if failed else 'none'}.",
        "",
        "These dates and outcomes were already exposed in earlier research. The result is useful stability evidence, but it cannot confirm the 10% objective or authorize online betting. No paper or live orders were placed.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise V8ReplayError("immutable V8 report differs")
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "score", "run"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    if args.action in {"freeze", "run"}:
        value = build_freeze(args.project_root)
    if args.action in {"score", "run"}:
        value = score(args.project_root)
    print(json.dumps({
        "campaign_id": value["campaign_id"],
        "status": value["status"],
        "self_sha256": value["self_sha256"],
        "primary": value.get("summaries", {}).get("primary_55"),
        "robustness": value.get("robustness"),
    }, indent=2))


if __name__ == "__main__":
    main()
