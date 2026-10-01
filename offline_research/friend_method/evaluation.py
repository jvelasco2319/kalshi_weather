"""Chronological offline evaluation of the supplied method's testable core.

No network, order, credential, or protected-holdout entry point exists here.
The exact requested GFS/GFS-Seamless/NAM/NBM history is unavailable, so this
module uses the frozen HRRR/GEFS development features declared in the protocol.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

import numpy as np

from v5a.development_search import _contains, _fee
from v5b_next.weather_evaluation import load_development


HRRR = "hrrr_temperature_f_max"
GEFS = "gefs_mean_temperature_f_max"
GEFS_SPREAD = "gefs_spread_delta_f_max"


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def _number(row: dict, key: str) -> float:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid numeric feature: {key}")
    return float(value)


def fit_location(rows: list[dict], outcomes: list[float], current: dict) -> tuple[float, dict]:
    """Bias-correct two sources and fit a nonnegative sum-to-one weight."""
    if len(rows) != len(outcomes) or len(rows) < 2:
        raise ValueError("at least two aligned training rows required")
    h = np.asarray([_number(row, HRRR) for row in rows], dtype=float)
    g = np.asarray([_number(row, GEFS) for row in rows], dtype=float)
    y = np.asarray(outcomes, dtype=float)
    bias_h = float(np.mean(h - y))
    bias_g = float(np.mean(g - y))
    hc, gc = h - bias_h, g - bias_g
    delta = hc - gc
    denominator = float(delta @ delta)
    weight_h = 0.5 if denominator <= 1e-12 else float(np.clip(delta @ (y - gc) / denominator, 0.0, 1.0))
    weight_g = 1.0 - weight_h
    current_h = _number(current, HRRR) - bias_h
    current_g = _number(current, GEFS) - bias_g
    location = weight_h * current_h + weight_g * current_g
    between_source_variance = (
        weight_h * (current_h - location) ** 2 + weight_g * (current_g - location) ** 2
    )
    return float(location), {
        "bias_hrrr_f": bias_h,
        "bias_gefs_f": bias_g,
        "weight_hrrr": weight_h,
        "weight_gefs": weight_g,
        "between_source_variance_f2": float(between_source_variance),
    }


def _prequential_records(rows: list[dict], outcomes: list[float], minimum_history: int = 10) -> list[dict]:
    records = []
    for index in range(minimum_history, len(rows)):
        prediction, metadata = fit_location(rows[:index], outcomes[:index], rows[index])
        records.append({
            "error_f": float(outcomes[index] - prediction),
            "spread_variance_f2": metadata["between_source_variance_f2"],
        })
    return records


def fit_variance(records: list[dict], current_spread_variance: float, sigma_floor_f: float = 1.0) -> tuple[float, dict]:
    """Fit nonnegative disagreement contribution to prior one-step squared errors."""
    if len(records) < 10:
        raise ValueError("ten prior prequential errors required")
    x = np.asarray([row["spread_variance_f2"] for row in records], dtype=float)
    y = np.asarray([row["error_f"] ** 2 for row in records], dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
    slope = max(0.0, float(slope))
    intercept = max(0.0, float(np.mean(y - slope * x)))
    variance = max(sigma_floor_f**2, intercept + slope * current_spread_variance)
    return math.sqrt(variance), {
        "variance_intercept_f2": intercept,
        "spread_variance_coefficient": slope,
        "sigma_floor_f": sigma_floor_f,
        "prequential_error_count": len(records),
    }


def normal_cdf(value: float, location: float, sigma: float) -> float:
    if sigma <= 0 or not math.isfinite(sigma):
        raise ValueError("positive finite sigma required")
    return 0.5 * (1.0 + math.erf((value - location) / (sigma * math.sqrt(2.0))))


def bracket_probabilities(location: float, sigma: float, contracts: list[dict]) -> dict[str, float]:
    """Map a Gaussian latent temperature to exhaustive rounded-integer buckets."""
    values: dict[str, float] = {}
    intervals = []
    for row in contracts:
        strike = row["strike_type"]
        if strike == "less":
            lo, hi = -math.inf, int(row["cap_strike"]) - 1
            probability = normal_cdf(int(row["cap_strike"]) - 0.5, location, sigma)
        elif strike == "greater":
            lo, hi = int(row["floor_strike"]) + 1, math.inf
            probability = 1.0 - normal_cdf(int(row["floor_strike"]) + 0.5, location, sigma)
        elif strike == "between":
            lo, hi = int(row["floor_strike"]), int(row["cap_strike"])
            probability = normal_cdf(hi + 0.5, location, sigma) - normal_cdf(lo - 0.5, location, sigma)
        else:
            raise ValueError("unsupported strike type")
        intervals.append((lo, hi))
        ticker = row["market_ticker"]
        if ticker in values:
            raise ValueError("duplicate ticker")
        values[ticker] = max(0.0, float(probability))
    intervals.sort()
    if (
        len(intervals) < 2
        or intervals[0][0] != -math.inf
        or intervals[-1][1] != math.inf
        or any(right[0] != left[1] + 1 for left, right in zip(intervals, intervals[1:]))
    ):
        raise ValueError("contracts are not an exhaustive integer partition")
    total = sum(values.values())
    if abs(total - 1.0) > 1e-8:
        raise ValueError("probability mass does not sum to one")
    return values


def _date_seed(base_seed: int, date: str) -> int:
    digest = hashlib.sha256(f"{base_seed}|{date}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def forecast_sequence(context: dict, config: dict) -> list[dict]:
    features = context["weather_features"]
    labels_by_date = {row["climate_date"]: row for row in context["label_records"]}
    if [row["climate_date"] for row in features] != context["dates"]:
        raise ValueError("feature chronology differs")
    output = []
    for index, current in enumerate(features):
        date = current["climate_date"]
        decision = _utc(current["decision_at"])
        prior = [
            row
            for row in features[:index]
            if _utc(labels_by_date[row["climate_date"]]["issued_at"]) < decision
        ]
        if index < config["evaluation_scope"]["warmup_dates"]:
            continue
        if len(prior) < 20:
            raise ValueError("insufficient prior published labels")
        outcomes = [float(labels_by_date[row["climate_date"]]["reported_high_f"]) for row in prior]
        location, location_fit = fit_location(prior, outcomes, current)
        records = _prequential_records(prior, outcomes)
        sigma, variance_fit = fit_variance(
            records,
            location_fit["between_source_variance_f2"],
            float(config["forecast"]["sigma_floor_f"]),
        )
        yes = sorted(
            [row for row in context["rows_by_date"][date] if row["contract_side"] == "YES"],
            key=lambda row: (-10000 if row["floor_strike"] is None else row["floor_strike"], row["market_ticker"]),
        )
        central = bracket_probabilities(location, sigma, yes)
        scenario_values = {ticker: [] for ticker in central}
        rng = np.random.default_rng(_date_seed(int(config["forecast"]["bootstrap_seed"]), date))
        for _ in range(int(config["forecast"]["bootstrap_refits"])):
            selected = rng.integers(0, len(prior), len(prior))
            sample_rows = [prior[int(i)] for i in selected]
            sample_outcomes = [outcomes[int(i)] for i in selected]
            scenario_location, _ = fit_location(sample_rows, sample_outcomes, current)
            scenario = bracket_probabilities(scenario_location, sigma, yes)
            for ticker, probability in scenario.items():
                scenario_values[ticker].append(probability)
        quantile = float(config["forecast"]["conservative_probability_quantile"])
        conservative = {
            ticker: float(np.quantile(values, quantile, method="linear"))
            for ticker, values in scenario_values.items()
        }
        output.append({
            "climate_date": date,
            "decision_at": current["decision_at"],
            "location_f": location,
            "sigma_f": sigma,
            "central_probabilities": central,
            "conservative_probabilities": conservative,
            "location_fit": location_fit,
            "variance_fit": variance_fit,
            "training_dates": [row["climate_date"] for row in prior],
        })
    expected = context["dates"][config["evaluation_scope"]["warmup_dates"] :]
    if [row["climate_date"] for row in output] != expected:
        raise ValueError("common scoring cohort differs")
    return output


def _summary(trades: list[dict]) -> dict:
    outlay = sum(row["entry_outlay_dollars"] for row in trades)
    profit = sum(row["net_profit_dollars"] for row in trades)
    dates = sorted({row["climate_date"] for row in trades})
    return {
        "selected_trades": len(trades),
        "selected_dates": len(dates),
        "total_entry_outlay_dollars": outlay,
        "total_net_profit_dollars": profit,
        "aggregate_realized_net_return": profit / outlay if outlay else -1.0,
    }


def _score_variant(context: dict, forecasts: list[dict], config: dict, variant: str) -> dict:
    if variant not in {"single_best_bucket", "multi_bucket"}:
        raise ValueError("unknown variant")
    strategy = config["strategy"]
    allowed_grades = set(strategy["allowed_execution_grades"])
    all_trades, abstentions, briers, baseline_briers = [], [], [], []
    dates = [row["climate_date"] for row in forecasts]
    forecast_by_date = {row["climate_date"]: row for row in forecasts}
    for date in dates:
        forecast = forecast_by_date[date]
        yes = sorted(
            [row for row in context["rows_by_date"][date] if row["contract_side"] == "YES"],
            key=lambda row: (-10000 if row["floor_strike"] is None else row["floor_strike"], row["market_ticker"]),
        )
        actual = context["labels"][date]
        winners = {row["market_ticker"]: int(_contains(row, actual)) for row in yes}
        if sum(winners.values()) != 1:
            raise ValueError("nonexhaustive settlement")
        briers.append(sum((forecast["central_probabilities"][ticker] - winner) ** 2 for ticker, winner in winners.items()))
        baseline_briers.append(sum((context["probabilities"][(date, ticker)] - winner) ** 2 for ticker, winner in winners.items()))
        candidates = []
        for row in yes:
            grade = row["execution_evidence_grade"]
            ask, bid = row["execution_price_cents"], row["bid_price_cents"]
            if grade not in allowed_grades or ask is None or bid is None:
                continue
            spread = int(ask) - int(bid)
            if spread < 0 or spread > int(strategy["maximum_spread_cents"]) or not 0 < int(ask) < 100:
                continue
            fee = float(_fee(date, int(ask)))
            outlay = int(ask) / 100.0 + fee
            central_q = forecast["central_probabilities"][row["market_ticker"]]
            conservative_q = forecast["conservative_probabilities"][row["market_ticker"]]
            conservative_profit = conservative_q - outlay
            if conservative_profit + 1e-12 < float(strategy["minimum_conservative_edge_dollars"]):
                continue
            won = bool(winners[row["market_ticker"]])
            candidates.append({
                "climate_date": date,
                "market_ticker": row["market_ticker"],
                "contract_side": "YES",
                "execution_evidence_grade": grade,
                "assumed_fill": bool(row["assumed_fill"]),
                "verified_fill": False,
                "entry_price_cents": int(ask),
                "bid_price_cents": int(bid),
                "fee_dollars": fee,
                "entry_outlay_dollars": outlay,
                "central_probability": central_q,
                "conservative_probability": conservative_q,
                "central_expected_profit_dollars": central_q - outlay,
                "conservative_expected_profit_dollars": conservative_profit,
                "central_expected_net_return": (central_q - outlay) / outlay,
                "conservative_expected_net_return": conservative_profit / outlay,
                "won": won,
                "net_profit_dollars": int(won) - outlay,
                "reported_high_f": actual,
                "quote_at_utc": row.get("quote_at_utc"),
                "strike_type": row["strike_type"],
                "floor_strike": row["floor_strike"],
                "cap_strike": row["cap_strike"],
            })
        if variant == "single_best_bucket" and candidates:
            candidates = [max(candidates, key=lambda row: (row["conservative_expected_profit_dollars"], -row["entry_price_cents"], row["market_ticker"]))]
        if candidates:
            all_trades.extend(candidates)
        else:
            abstentions.append({"climate_date": date, "reason": "no_conservative_long_yes_edge"})
    date_fold = {date: min(4, index * 5 // len(dates)) for index, date in enumerate(dates)}
    folds = []
    for fold in range(5):
        folds.append({"fold": fold + 1, **_summary([row for row in all_trades if date_fold[row["climate_date"]] == fold])})
    stress = {}
    for cents in (1, 2, 3):
        stressed = []
        for trade in all_trades:
            price = min(99, trade["entry_price_cents"] + cents)
            outlay = price / 100.0 + float(_fee(trade["climate_date"], price))
            stressed.append({**trade, "entry_outlay_dollars": outlay, "net_profit_dollars": int(trade["won"]) - outlay})
        stress[str(cents)] = {**_summary(stressed), "selection_fixed": True}
    by_date = {}
    for trade in all_trades:
        by_date.setdefault(trade["climate_date"], []).append(trade)
    best_date = max(by_date, key=lambda date: sum(row["net_profit_dollars"] for row in by_date[date])) if by_date else None
    without_best = [row for row in all_trades if row["climate_date"] != best_date]
    quality = {"A": 1.0, "B_PLUS": 0.65, "B": 0.30}
    grade_counts = {grade: sum(row["execution_evidence_grade"] == grade for row in all_trades) for grade in quality}
    total_outlay = sum(row["entry_outlay_dollars"] for row in all_trades)
    central_expected_profit = sum(row["central_expected_profit_dollars"] for row in all_trades)
    conservative_expected_profit = sum(row["conservative_expected_profit_dollars"] for row in all_trades)
    win_rate = mean(float(row["won"]) for row in all_trades) if all_trades else 0.0
    selected_central_probability = mean(row["central_probability"] for row in all_trades) if all_trades else 0.0
    selected_conservative_probability = mean(row["conservative_probability"] for row in all_trades) if all_trades else 0.0
    best_date_profit = sum(row["net_profit_dollars"] for row in by_date.get(best_date, [])) if best_date else 0.0
    result = {
        "schema": "friend-method-data-adapted-development-result-v1",
        "variant": variant,
        **_summary(all_trades),
        "mean_central_expected_net_return": mean(row["central_expected_net_return"] for row in all_trades) if all_trades else -1.0,
        "mean_conservative_expected_net_return": mean(row["conservative_expected_net_return"] for row in all_trades) if all_trades else -1.0,
        "aggregate_central_expected_net_return": central_expected_profit / total_outlay if total_outlay else -1.0,
        "aggregate_conservative_expected_net_return": conservative_expected_profit / total_outlay if total_outlay else -1.0,
        "selection_calibration": {
            "wins": sum(bool(row["won"]) for row in all_trades),
            "win_rate": win_rate,
            "mean_central_probability": selected_central_probability,
            "mean_conservative_probability": selected_conservative_probability,
            "central_probability_minus_win_rate": selected_central_probability - win_rate,
            "conservative_probability_minus_win_rate": selected_conservative_probability - win_rate,
            "binary_brier_central": mean((row["central_probability"] - float(row["won"])) ** 2 for row in all_trades) if all_trades else 1.0,
            "binary_brier_conservative": mean((row["conservative_probability"] - float(row["won"])) ** 2 for row in all_trades) if all_trades else 1.0,
        },
        "positive_fold_count": sum(row["selected_dates"] > 0 and row["aggregate_realized_net_return"] > 0 for row in folds),
        "worst_nonempty_fold_return": min((row["aggregate_realized_net_return"] for row in folds if row["selected_dates"]), default=-1.0),
        "temporal_folds": folds,
        "multiclass_brier": mean(briers),
        "baseline_multiclass_brier": mean(baseline_briers),
        "execution_grade_counts": grade_counts,
        "evidence_quality_score": mean(quality[row["execution_evidence_grade"]] for row in all_trades) if all_trades else 0.0,
        "adverse_stress": stress,
        "best_date_removed": best_date,
        "best_date_removed_return": _summary(without_best)["aggregate_realized_net_return"],
        "best_date_net_profit_dollars": best_date_profit,
        "best_date_share_of_total_net_profit": best_date_profit / result_profit if (result_profit := sum(row["net_profit_dollars"] for row in all_trades)) else None,
        "grade_a_sensitivity": {**_summary([row for row in all_trades if row["execution_evidence_grade"] == "A"]), "selection_fixed": True},
        "trades": all_trades,
        "abstentions": abstentions,
        "development_only": True,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "limitations": [
            "Data-adapted HRRR/GEFS test; exact GFS/GFS-Seamless/NAM/NBM history is absent.",
            "Repeatedly exposed development dates; this is not independent confirmation.",
            "Historical prices and grades do not prove fills; depth-aware sizing is unavailable.",
            "One unit per qualifying bucket is a diagnostic cap, not the full portfolio allocator in the supplied specification.",
        ],
    }
    gates = config["ranking_gates"]
    checks = {
        "sample_dates": result["selected_dates"] >= int(gates["minimum_selected_dates"]),
        "realized_return": result["aggregate_realized_net_return"] >= float(gates["minimum_realized_return"]),
        "mean_expected_return": result["mean_central_expected_net_return"] >= float(gates["minimum_mean_expected_return"]),
        "positive_folds": result["positive_fold_count"] >= int(gates["minimum_positive_folds"]),
        "worst_fold": result["worst_nonempty_fold_return"] >= float(gates["minimum_worst_fold_return"]),
        "evidence_quality": result["evidence_quality_score"] >= float(gates["minimum_evidence_quality"]),
        "adverse_fill": result["adverse_stress"]["2"]["aggregate_realized_net_return"] > 0,
        "best_day_removed": result["best_date_removed_return"] > 0,
        "calibration": result["multiclass_brier"] <= result["baseline_multiclass_brier"] + 1e-12,
    }
    result["gate_checks"] = checks
    result["gate_failures"] = [name for name, passed in checks.items() if not passed]
    result["all_gates_passed"] = all(checks.values())
    json.dumps(result, allow_nan=False)
    return result


def evaluate(root: str | Path, config: dict) -> dict:
    context = load_development(root)
    forecasts = forecast_sequence(context, config)
    results = {
        variant: _score_variant(context, forecasts, config, variant)
        for variant in config["strategy"]["variants"]
    }
    return {
        "schema": "friend-method-data-adapted-evaluation-v1",
        "config": config,
        "forecast_count": len(forecasts),
        "forecasts": forecasts,
        "results": results,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
