from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


class InputError(ValueError):
    pass


def config_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config"


def _read(name: str) -> dict[str, Any]:
    return json.loads((config_dir() / name).read_text(encoding="utf-8-sig"))


def normalize(values: Iterable[float]) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != 6 or any(not math.isfinite(value) or value < 0 for value in result):
        raise InputError("exactly six finite non-negative bracket probabilities are required")
    total = sum(result)
    if total <= 0:
        raise InputError("probability mass must be positive")
    return [value / total for value in result]


def pressure_state(gradient_hpa: float | None) -> str:
    if gradient_hpa is None:
        return "neutral"
    value = float(gradient_hpa)
    if not math.isfinite(value):
        return "neutral"
    if value <= -2.0:
        return "offshore"
    if value >= 2.0:
        return "onshore"
    return "neutral"


def model_probabilities(base: Iterable[float], gradient_hpa: float | None = None) -> dict[str, list[float]]:
    raw = normalize(base)
    modal = max(range(6), key=lambda index: (raw[index], index))
    v8_freeze = _read("frozen_v8.json")
    v10_freeze = _read("frozen_v10.json")
    table = v8_freeze["probability_model"]["conditional_distributions_by_base_modal_position"][modal]
    v8 = normalize(0.25 * value + 0.75 * float(other) for value, other in zip(raw, table, strict=True))
    state = pressure_state(gradient_hpa)
    pressure = v10_freeze["fitted_parameters"]["pressure_conditioned_tables"][str(modal)][state]["hierarchical_posterior"]
    v10 = normalize(0.25 * value + 0.75 * float(other) for value, other in zip(v8, pressure, strict=True))
    return {"V5B": raw, "V8": v8, "V10": v10}


def fee_dollars(price_cents: int, rate: float = 0.07) -> float:
    if not 0 < int(price_cents) < 100:
        raise InputError("price must be between 1 and 99 cents")
    price = int(price_cents) / 100.0
    return math.ceil(rate * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def _number(quote: dict[str, Any], key: str) -> int | None:
    value = quote.get(key)
    if value is None or value == "":
        return None
    number = int(round(float(value)))
    return number if 0 <= number <= 100 else None


def select_no_trade(probabilities: Iterable[float], quotes: list[dict[str, Any]]) -> dict[str, Any]:
    p = normalize(probabilities)
    ordered = sorted(p, reverse=True)
    if ordered[0] - ordered[1] < 0.10:
        return {"status": "ABSTAIN", "reason": "probability_gap_below_0.10"}
    candidates: list[dict[str, Any]] = []
    for index, quote in enumerate(quotes):
        bracket = int(quote.get("bracket_index", index))
        if not 0 <= bracket < 6:
            continue
        ask = _number(quote, "no_ask_cents")
        bid = _number(quote, "no_bid_cents")
        grade = str(quote.get("evidence_grade", "UNAVAILABLE")).upper()
        if ask is None or bid is None or grade not in {"A", "B_PLUS", "B", "LIVE"}:
            continue
        spread = ask - bid
        if not 5 <= ask <= 80 or not 0 <= spread <= 5:
            continue
        probability = 1.0 - p[bracket]
        fee = fee_dollars(ask)
        outlay = ask / 100.0 + fee
        expected_profit = probability - outlay
        expected_return = expected_profit / outlay
        if expected_return + 1e-12 < 0.10:
            continue
        candidates.append({
            "status": "SELECTED",
            "side": "NO",
            "bracket_index": bracket,
            "ticker": quote.get("ticker", f"bracket-{bracket}"),
            "label": quote.get("label", f"Bracket {bracket + 1}"),
            "evidence_grade": grade,
            "entry_price_cents": ask,
            "spread_cents": spread,
            "fee_dollars": fee,
            "entry_outlay_dollars": outlay,
            "model_probability": probability,
            "expected_profit_dollars": expected_profit,
            "expected_net_return": expected_return,
        })
    if not candidates:
        return {"status": "ABSTAIN", "reason": "no_contract_passed_price_spread_fee_and_return_gates"}
    return max(candidates, key=lambda row: (row["expected_profit_dollars"], -row["entry_price_cents"], str(row["ticker"])))


def score_record(record: dict[str, Any]) -> dict[str, Any]:
    date = str(record.get("date") or record.get("climate_date") or "")
    if not date:
        raise InputError("record date is required")
    vectors = model_probabilities(record["base_probabilities"], record.get("pressure_gradient_hpa"))
    outcome = record.get("outcome_index")
    if outcome is not None:
        outcome = int(outcome)
        if not 0 <= outcome < 6:
            raise InputError(f"invalid outcome_index for {date}")
    quotes = list(record.get("quotes") or [])
    models: dict[str, Any] = {}
    for name, probabilities in vectors.items():
        decision = select_no_trade(probabilities, quotes) if quotes else {"status": "ABSTAIN", "reason": "quotes_unavailable"}
        if outcome is not None:
            decision = dict(decision)
            target = [float(index == outcome) for index in range(6)]
            decision["brier"] = sum((value - truth) ** 2 for value, truth in zip(probabilities, target, strict=True))
            decision["log_loss"] = -math.log(max(probabilities[outcome], 1e-15))
            decision["modal_hit"] = max(range(6), key=lambda index: (probabilities[index], index)) == outcome
            if decision["status"] == "SELECTED":
                won = decision["bracket_index"] != outcome
                decision["won"] = won
                decision["net_profit_dollars"] = float(won) - decision["entry_outlay_dollars"]
                decision["realized_return"] = decision["net_profit_dollars"] / decision["entry_outlay_dollars"]
        models[name] = {"probabilities": probabilities, "decision": decision}
    return {
        "date": date,
        "source": record.get("source", "normalized_history"),
        "pressure_gradient_hpa": record.get("pressure_gradient_hpa"),
        "pressure_state": pressure_state(record.get("pressure_gradient_hpa")),
        "outcome_index": outcome,
        "quotes": quotes,
        "models": models,
    }


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise InputError("history is empty")
    days = [score_record(record) for record in records]
    summaries: dict[str, Any] = {}
    for name in ("V5B", "V8", "V10"):
        decisions = [day["models"][name]["decision"] for day in days]
        scored = [row for row in decisions if "brier" in row]
        trades = [row for row in decisions if row["status"] == "SELECTED" and "net_profit_dollars" in row]
        outlay = sum(row["entry_outlay_dollars"] for row in trades)
        profit = sum(row["net_profit_dollars"] for row in trades)
        summaries[name] = {
            "dates": len(days),
            "forecast_scored_dates": len(scored),
            "selected_trades": len(trades),
            "wins": sum(bool(row.get("won")) for row in trades),
            "win_rate": (sum(bool(row.get("won")) for row in trades) / len(trades)) if trades else None,
            "total_outlay_dollars": outlay,
            "net_profit_dollars": profit,
            "aggregate_realized_return": profit / outlay if outlay else None,
            "mean_brier": sum(row["brier"] for row in scored) / len(scored) if scored else None,
            "mean_log_loss": sum(row["log_loss"] for row in scored) / len(scored) if scored else None,
            "modal_accuracy": sum(bool(row["modal_hit"]) for row in scored) / len(scored) if scored else None,
        }
    ranking = sorted(
        summaries,
        key=lambda name: (
            summaries[name]["aggregate_realized_return"] is not None,
            summaries[name]["aggregate_realized_return"] or -999.0,
            -(summaries[name]["mean_brier"] or 999.0),
        ),
        reverse=True,
    )
    return {"schema_version": "kalshi-swarm-three-model-evaluation-v1", "summaries": summaries, "ranking": ranking, "days": days}

