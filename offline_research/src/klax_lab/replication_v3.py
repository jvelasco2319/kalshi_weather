"""Independent V3 numerical recomputation for campaign promotion.

This module does not call ``OfflineCandidateEvaluatorV3.evaluate``.  It loads
the sealed fitted state emitted by discovery, reconstructs predictions from the
frozen scored features, replays selection and settlement, and independently
recomputes proper scores, fees, payouts, folds, bootstrap, stress and
concentration metrics.  Its code and artifact root are separate from discovery.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
from typing import Any, Mapping, Sequence

from .candidate_model_v3 import fitted_candidate_model_from_state
from .domain import ContractBounds
from .evaluation import FeeScenario, ReplayDecision, settle_purchase
from .market_policy_v3 import (
    SelectiveControls, quote_from_normalized_candle, screen_minute_purchase,
)
from .provenance import canonical_hash
from .research_plan_v3 import ResearchPlanV3


UTC = timezone.utc
BOOTSTRAP_SEED = 20260925
BOOTSTRAP_RESAMPLES = 10_000
REFERENCE_VERSION = "klax-v3-2024-empirical-klax-climatology-v1"
EVALUATOR_VERSION = "klax-v3-offline-candidate-evaluator-v1"
_PROTECTED = {"protected_final", "protected-final", "holdout", "final"}


def _read(path: Path) -> Any:
    raw = path.read_bytes()
    value = json.loads(raw)
    expected = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")
    if raw != expected:
        raise ValueError(f"Noncanonical discovery artifact: {path.name}")
    return value


def _write(path: Path, value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Independent V3 recomputation artifact changed")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _bounds(value: Mapping[str, Any]) -> ContractBounds:
    lower = value.get("integer_lower_f", value.get("lower_integer_f"))
    upper = value.get("integer_upper_f", value.get("upper_integer_f"))
    if lower is not None and type(lower) is not int:
        raise ValueError("Invalid lower settlement bound")
    if upper is not None and type(upper) is not int:
        raise ValueError("Invalid upper settlement bound")
    return ContractBounds(lower, upper)


def _ordered(feature: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    def key(contract):
        lower, upper = _bounds(contract["interval"]).integer_bounds()
        return (float("-inf") if lower is None else lower,
                float("inf") if upper is None else upper, contract["ticker"])
    contracts = tuple(sorted(feature["contracts"], key=key))
    integer_bounds = [_bounds(row["interval"]).integer_bounds() for row in contracts]
    if (len(contracts) < 2 or integer_bounds[0][0] is not None
            or integer_bounds[-1][1] is not None):
        raise ValueError("Nonexhaustive settlement brackets")
    if any(left[1] is None or right[0] is None or left[1] + 1 != right[0]
           for left, right in zip(integer_bounds, integer_bounds[1:])):
        raise ValueError("Settlement brackets overlap or contain a gap")
    return contracts


def _winner(label: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]]) -> int:
    outcomes = {row["ticker"]: row["yes_outcome"] for row in label["contracts"]}
    winners = [index for index, row in enumerate(contracts)
               if outcomes.get(row["ticker"]) == 1]
    if set(outcomes) != {row["ticker"] for row in contracts} or len(winners) != 1:
        raise ValueError("Settlement outcome does not map to exactly one bracket")
    return winners[0]


def _market(contracts: Sequence[Mapping[str, Any]]) -> tuple[float, ...]:
    values = []
    for contract in contracts:
        quote = quote_from_normalized_candle(
            contract["market"]["latest_completed_candle"])
        values.append(float((quote.yes_bid + quote.yes_ask) / 2))
    clipped = [min(1 - 1e-9, max(1e-9, value)) for value in values]
    total = sum(clipped)
    return tuple(value / total for value in clipped)


def _reference(
    state: Mapping[str, Any], contracts: Sequence[Mapping[str, Any]],
) -> tuple[float, ...]:
    counts = state["temperature_counts"]
    total = state["training_rows"]
    result = []
    for contract in contracts:
        bound = _bounds(contract["interval"])
        result.append(sum(row["count"] for row in counts
                          if bound.contains(row["temperature_f"])) / total)
    if abs(sum(result) - 1.0) > 1e-12:
        raise ValueError("Frozen reference mass does not map to brackets")
    return tuple(result)


def _brier(probabilities: Sequence[float], winner: int) -> float:
    return sum((value - int(index == winner)) ** 2
               for index, value in enumerate(probabilities))


def _crps(probabilities: Sequence[float], winner: int) -> float:
    cumulative = score = 0.0
    for index, probability in enumerate(probabilities[:-1]):
        cumulative += probability
        score += (cumulative - float(winner <= index)) ** 2
    return score


def _decision(decision: ReplayDecision) -> dict[str, Any]:
    value = asdict(decision)
    for key, item in tuple(value.items()):
        if isinstance(item, Decimal):
            value[key] = format(item, "f")
        elif isinstance(item, datetime):
            value[key] = item.astimezone(UTC).isoformat()
    return value


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    profit = sum((Decimal(row["net_profit"]) for row in rows), Decimal("0"))
    outlay = sum((Decimal(row["entry_outlay"]) for row in rows), Decimal("0"))
    returns = [Decimal(row["net_return"]) for row in rows]
    return {
        "trade_count": len(rows),
        "total_net_profit": format(profit, "f"),
        "total_entry_outlay": format(outlay, "f"),
        "capital_weighted_return": float(profit / outlay) if outlay else None,
        "mean_trade_return": (
            float(sum(returns, Decimal("0")) / len(returns)) if returns else None),
    }


def _bootstrap(
    by_day: Mapping[str, Sequence[dict[str, Any]]], folds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    ratios: list[float] = []
    undefined = 0
    for _ in range(BOOTSTRAP_RESAMPLES):
        profit = outlay = Decimal("0")
        for fold in folds:
            dates = tuple(fold["dates"])
            for _ in dates:
                sampled = dates[rng.randrange(len(dates))]
                for row in by_day.get(sampled, ()):
                    profit += Decimal(row["net_profit"])
                    outlay += Decimal(row["entry_outlay"])
        if outlay:
            ratios.append(float(profit / outlay))
        else:
            undefined += 1
    ratios.sort()
    return {
        "lower_95": ratios[max(0, int(.05 * len(ratios)) - 1)] if ratios else None,
        "resamples": BOOTSTRAP_RESAMPLES,
        "unit": "independent_settlement_day",
        "stratified_by_development_fold": True,
        "seed": BOOTSTRAP_SEED,
        "one_sided_confidence": .95,
        "undefined_resamples": undefined,
    }


def _breakdown(rows: Sequence[dict[str, Any]], key) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(key(row)), []).append(row)
    return {name: _summary(items) for name, items in sorted(groups.items())}


def _price_band(value: str) -> str:
    cents = int(Decimal(value) * 100)
    lower = cents // 20 * 20
    return f"{lower:02d}-{min(99, lower + 19):02d}"


def _stress(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fee = FeeScenario(
        "registered_v3_cost_stress", "0.10",
        "configs/v3_goal.json: development_promotion_gates.cost_stress",
        historical_verified=False)
    stressed = []
    unavailable = 0
    for row in rows:
        price = Decimal(row["entry_price"]) + Decimal("0.02")
        if price >= Decimal("1"):
            unavailable += 1
            continue
        outlay = price + fee.entry_fee(price, 1)
        profit = Decimal(row["payout"]) - outlay
        stressed.append({
            "net_profit": format(profit, "f"),
            "entry_outlay": format(outlay, "f"),
            "net_return": format(profit / outlay, "f"),
        })
    return {
        **_summary(stressed), "fee_rate": .10,
        "additional_adverse_price_per_contract_dollars": .02,
        "quantity": 1, "unavailable_entry_treatment": "abstain",
        "unavailable_entry_count": unavailable,
    }


def _controls(plan: ResearchPlanV3) -> SelectiveControls:
    return SelectiveControls(
        target_return=str(plan.entry_threshold),
        uncertainty_buffer=str(plan.uncertainty_buffer),
        minimum_candle_volume=str(plan.minimum_candle_volume),
        maximum_price_age_minutes=plan.maximum_price_age_minutes,
        maximum_spread_cents=plan.maximum_spread_cents,
        entry_price_floor_cents=plan.entry_price_floor_cents,
        entry_price_ceiling_cents=plan.entry_price_ceiling_cents,
        allowed_sides=("YES", "NO") if plan.allowed_sides == "BOTH"
                      else (plan.allowed_sides,),
    )


def independently_recompute_candidate(
    *, plan: ResearchPlanV3, inputs: Any, fee_scenario: FeeScenario,
    primary_artifact_directory: Path, output_directory: Path,
    partition_contract_sha256: str,
) -> dict[str, Any]:
    """Recompute one candidate without calling the discovery evaluator."""
    source = Path(primary_artifact_directory).resolve()
    output = Path(output_directory).resolve()
    if any(part.casefold() in _PROTECTED for part in (*source.parts, *output.parts)):
        raise ValueError("Independent V3 verifier refuses protected-final storage")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError(
            "Discovery and independent-verification artifact roots must be isolated")
    inputs.validate(plan.decision_time_utc)
    compiled = _read(source / "compiled_manifest.json")
    saved_predictions = _read(source / "predictions.json")
    saved_ledger = _read(source / "ledger.json")
    saved_folds = _read(source / "fold_metrics.json")
    saved_evaluation = _read(source / "evaluation.json")
    fitted = fitted_candidate_model_from_state(compiled["fitted_model_state"])
    if fitted.plan_sha256 != plan.identity:
        raise ValueError("Independent verifier fitted state belongs to another plan")
    reference_state = compiled["frozen_reference_state"]
    reference_body = {key: value for key, value in reference_state.items()
                      if key != "state_sha256"}
    if reference_state.get("state_sha256") != canonical_hash(reference_body):
        raise ValueError("Independent verifier reference state is modified")

    features = tuple(row for row in inputs.evaluation_features
                     if row.get("decision_time_utc") == plan.decision_time_utc)
    labels = {row["climate_date"]: row for row in inputs.evaluation_labels}
    if len(labels) != len(inputs.evaluation_labels):
        raise ValueError("Independent verifier found duplicate scored labels")
    event_ids = [row.get("event_ticker") for row in features]
    if (any(not isinstance(value, str) or not value for value in event_ids)
            or len(event_ids) != len(set(event_ids))):
        raise ValueError("Independent verifier found nonunique scored events")
    predictions = []
    decisions = []
    ledger = []
    candidate_brier = candidate_crps = reference_brier = reference_crps = 0.0
    conservation = True
    controls = _controls(plan)
    for feature in features:
        day = feature["climate_date"]
        if (day not in labels
                or labels[day].get("event_ticker") != feature.get("event_ticker")):
            raise ValueError(
                "Independent verifier feature/label event identities differ")
        contracts = _ordered(feature)
        bounds = tuple(_bounds(row["interval"]) for row in contracts)
        observed = _winner(labels[day], contracts)
        market = _market(contracts)
        prediction = fitted.predict(feature, bounds, market_probabilities=market)
        probabilities = prediction.probabilities
        reference = _reference(reference_state, contracts)
        candidate_brier += _brier(probabilities, observed)
        candidate_crps += _crps(probabilities, observed)
        reference_brier += _brier(reference, observed)
        reference_crps += _crps(reference, observed)
        conservation = conservation and abs(sum(probabilities) - 1.0) <= 1e-10
        predictions.append({
            "climate_date": day, "event_ticker": feature["event_ticker"],
            "decision_time_utc": plan.decision_time_utc,
            "tickers": [row["ticker"] for row in contracts],
            "probabilities": list(probabilities),
            "raw_probabilities": list(prediction.raw_probabilities),
            "market_midpoint_probabilities": list(market),
            "reference_probabilities": list(reference),
            "regime": prediction.regime,
            "pooled_regime_fallback": prediction.pooled_regime_fallback,
            "conformal_prediction_set": list(prediction.conformal_prediction_set),
            "should_abstain": prediction.should_abstain,
            "central_interval_width_f": prediction.central_interval_width_f,
        })
        candidates = []
        if (not prediction.should_abstain
                and prediction.central_interval_width_f is not None
                and prediction.central_interval_width_f <= plan.maximum_interval_width_f):
            decision_at = datetime.fromisoformat(
                feature["decision_at"].replace("Z", "+00:00")).astimezone(UTC)
            for index, contract in enumerate(contracts):
                quote = quote_from_normalized_candle(
                    contract["market"]["latest_completed_candle"])
                for side in controls.allowed_sides:
                    decision = screen_minute_purchase(
                        decision_id=f"{plan.identity[:16]}:{day}:{contract['ticker']}:{side}",
                        information_cutoff=decision_at, decision_at=decision_at,
                        yes_probability=probabilities[index], side=side, quote=quote,
                        controls=controls, fees=fee_scenario,
                        dataset_version=inputs.dataset_id, model_version=fitted.identity)
                    decisions.append({
                        "climate_date": day, "event_ticker": feature["event_ticker"],
                        "ticker": contract["ticker"], "regime": prediction.regime,
                        **_decision(decision),
                    })
                    if decision.status == "ACCEPTED":
                        candidates.append((decision, contract, index))
        if not candidates:
            continue
        decision, contract, index = min(
            candidates,
            key=lambda item: (-item[0].expected_net_return,
                              item[1]["ticker"], item[0].side))
        settled = settle_purchase(decision, int(index == observed))
        ledger.append({
            "climate_date": day, "event_ticker": feature["event_ticker"],
            "ticker": contract["ticker"], "decision_time_utc": plan.decision_time_utc,
            "regime": prediction.regime,
            "expected_net_return": format(decision.expected_net_return, "f"),
            "entry_price": format(decision.entry_price, "f"),
            "payout": format(settled.payout, "f"),
            "entry_outlay": format(settled.entry_outlay, "f"),
            "settlement_cost": format(settled.settlement_cost, "f"),
            "net_profit": format(settled.net_profit, "f"),
            "net_return": format(settled.net_return, "f"),
            "side": settled.side, "quantity": settled.quantity,
            "evidence_grade": settled.evidence_grade,
            "yes_outcome": int(index == observed),
            "simulation_label": settled.simulation_label,
        })
    count = len(features)
    if count == 0:
        raise ValueError("Independent verifier found no scored features")
    by_day = {row["climate_date"]: [row] for row in ledger}
    folds = []
    for fold in inputs.folds["folds"]:
        rows = [row for day in fold["dates"] for row in by_day.get(day, ())]
        folds.append({
            "fold": fold.get("fold", fold.get("fold_index")), **_summary(rows),
            "selected_settlement_days": [row["climate_date"] for row in rows],
            "evaluation_dates": list(fold["dates"]),
        })
    economics = _summary(ledger)
    best_first = sorted(
        ledger, key=lambda row: (Decimal(row["net_profit"]), row["climate_date"]),
        reverse=True)
    economics.update({
        "bootstrap": _bootstrap(by_day, inputs.folds["folds"]),
        "selected_trade_expected_net_returns": [
            float(row["expected_net_return"]) for row in ledger],
        "selected_event_ids": [row["event_ticker"] for row in ledger],
        "selected_settlement_days": [row["climate_date"] for row in ledger],
        "capital_weighted_return_after_removing_most_profitable_day": (
            _summary(best_first[1:])["capital_weighted_return"] if best_first else None),
        "contribution_breakdowns": {
            "calendar_month": _breakdown(ledger, lambda row: row["climate_date"][:7]),
            "weather_regime": _breakdown(ledger, lambda row: row["regime"]),
            "entry_price_band": _breakdown(ledger, lambda row: _price_band(row["entry_price"])),
            "purchase_side": _breakdown(ledger, lambda row: row["side"]),
            "decision_time": _breakdown(ledger, lambda row: row["decision_time_utc"]),
        },
        "fee_scenario": {
            "name": fee_scenario.name, "rate": format(fee_scenario.rate, "f"),
            "rounding": format(fee_scenario.rounding, "f"),
            "provenance": fee_scenario.provenance,
            "historical_verified": fee_scenario.historical_verified,
        },
        "assumed_fill": True, "actual_orders_placed": False,
        "actual_account_gains_measured": False,
    })
    candidate = {
        "evaluator_version": EVALUATOR_VERSION,
        "research_plan_sha256": plan.identity,
        "fitted_model_sha256": fitted.identity,
        "dataset_id": inputs.dataset_id,
        "partition_audit": {
            "partition_contract_sha256": partition_contract_sha256,
            "weather_model_fit_source": "weather_training_through_2024_12_31",
            "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
            "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
            "calibration_prefix_scored": False,
            "scored_outcomes_used_for_fit_or_thresholds": False,
        },
        "forecast_scores": {
            "brier": candidate_brier / count, "crps_f": candidate_crps / count,
            "crps_definition": "discrete_crps_on_ordered_fahrenheit_settlement_brackets",
            "scored_events": count,
            "probability_conservation_passed": conservation,
        },
        "historical_assumed_fill": economics,
    }
    reference = {
        "reference_version": REFERENCE_VERSION,
        "training_source": "weather_training_through_2024_12_31",
        "forecast_scores": {
            "brier": reference_brier / count, "crps_f": reference_crps / count,
            "crps_definition": "discrete_crps_on_ordered_fahrenheit_settlement_brackets",
            "scored_events": count, "probability_conservation_passed": True,
        },
    }
    stress = _stress(ledger)
    recomputed = {
        "predictions": predictions, "decisions": decisions, "settlements": ledger,
        "candidate": candidate, "reference": reference, "folds": folds,
        "stress_return": stress,
        "source_compiled_sha256": hashlib.sha256(
            (source / "compiled_manifest.json").read_bytes()).hexdigest(),
        "protected_final_read": False, "network_used": False,
    }
    reproduction_sha = _write(output / "recomputed-evidence.json", recomputed)
    checks = {
        "compiled_identity": (
            compiled.get("research_plan_sha256") == plan.identity
            and compiled.get("fitted_model_sha256") == fitted.identity),
        "selected_opportunities": (
            predictions == saved_predictions.get("rows")
            and decisions == saved_ledger.get("decisions")
            and [(row["event_ticker"], row["ticker"], row["side"]) for row in ledger]
            == [(row["event_ticker"], row["ticker"], row["side"])
                for row in saved_ledger.get("settlements", [])]),
        "entry_outlay": [row["entry_outlay"] for row in ledger]
                         == [row["entry_outlay"] for row in saved_ledger.get("settlements", [])],
        "fees": [row["settlement_cost"] for row in ledger]
                == [row["settlement_cost"] for row in saved_ledger.get("settlements", [])],
        "payouts": [row["payout"] for row in ledger]
                   == [row["payout"] for row in saved_ledger.get("settlements", [])],
        "fold_returns": folds == saved_folds,
        "bootstrap_lower_bound": (
            economics["bootstrap"]
            == saved_evaluation.get("candidate", {}).get(
                "historical_assumed_fill", {}).get("bootstrap")),
        "crps": (
            candidate["forecast_scores"]["crps_f"]
            == saved_evaluation.get("candidate", {}).get(
                "forecast_scores", {}).get("crps_f")
            and reference["forecast_scores"]["crps_f"]
            == saved_evaluation.get("reference", {}).get(
                "forecast_scores", {}).get("crps_f")),
        "brier": (
            candidate["forecast_scores"]["brier"]
            == saved_evaluation.get("candidate", {}).get(
                "forecast_scores", {}).get("brier")
            and reference["forecast_scores"]["brier"]
            == saved_evaluation.get("reference", {}).get(
                "forecast_scores", {}).get("brier")),
        "partition_roles_and_nonoverlap": (
            candidate["partition_audit"]
            == saved_evaluation.get("candidate", {}).get("partition_audit")
            and all("2025-02-04" <= row["climate_date"] <= "2025-06-30"
                    for row in ledger)),
    }
    # Whole-object comparisons catch corrupted profit, stress and breakdown
    # fields even when one named summary happens to remain unchanged.
    whole_objects = {
        "candidate": candidate == saved_evaluation.get("candidate"),
        "reference": reference == saved_evaluation.get("reference"),
        "stress_return": stress == saved_evaluation.get("stress_return"),
    }
    differences = [name for name, passed in {**checks, **whole_objects}.items()
                   if not passed]
    return {
        "checks": checks,
        "status": "PASS" if all(checks.values()) and not differences else "FAIL",
        "differences": differences,
        "recomputation_artifact_sha256": reproduction_sha,
        "recomputation_artifact_path": (
            output / "recomputed-evidence.json").as_posix(),
    }
