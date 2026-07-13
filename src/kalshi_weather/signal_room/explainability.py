from __future__ import annotations

import math
from datetime import date, datetime, time, timezone
from decimal import Decimal
from statistics import NormalDist
from typing import Any
from zoneinfo import ZoneInfo

from kalshi_weather.signal_room.api_models import GateState, MarketRow, ModelSlot, SignalRoomSnapshot
from kalshi_weather.strategy_current.economics import (
    FeeSchedule,
    max_qualifying_price,
    trade_economics,
    whole_cent_price_grid,
)
from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS

MODEL_LABELS = {
    "ecmwf_ifs": "ECMWF IFS",
    "gfs013": "GFS 0.13",
    "gfs_seamless": "GFS Seamless",
    "nam": "NAM",
    "nbm": "NBM",
}

MODEL_SIGMA_F = {
    "ecmwf_ifs": 1.2,
    "gfs013": 1.4,
    "gfs_seamless": 1.4,
    "nam": 1.6,
    "nbm": 1.3,
}

PRICE_GRID = [Decimal(cents) / Decimal("100") for cents in range(1, 100, 5)]
SCENARIO_COUNT = 41


def canonical_explainability_snapshot(
    snapshot: SignalRoomSnapshot,
    *,
    replay_mode: bool = False,
) -> dict[str, Any]:
    lab = snapshot.probability_lab or {}
    evaluation_id = str(lab.get("evaluation_id") or snapshot.revision)
    evaluated_at = _iso(snapshot.decision.evaluated_at)
    target = snapshot.event.target_date
    outcome = _outcome_map(snapshot)
    models = _models(snapshot, outcome["brackets"])
    mixture = _mixture(snapshot, models, outcome["brackets"])
    economics = _economics(snapshot)
    selected_side = _side(snapshot.decision.focus_side)
    selected_market = snapshot.decision.focus_ticker
    analysis_state = _analysis_state(snapshot)
    execution_state = _execution_state(snapshot)

    return {
        "schemaVersion": "1.0.0",
        "evaluationId": evaluation_id,
        "strategyId": snapshot.strategy.strategy_id,
        "strategyConfigHash": snapshot.strategy.config_hash,
        "evaluatedAt": evaluated_at,
        "eventTicker": snapshot.event.ticker,
        "targetDate": target.isoformat(),
        "mode": "replay" if replay_mode or snapshot.replay_mode else "shadow",
        "analysisState": analysis_state,
        "executionState": execution_state,
        "finalReasonCode": snapshot.decision.reason_code,
        "selectedMarketTicker": selected_market,
        "selectedSide": selected_side,
        "station": _station(snapshot, target),
        "models": models,
        "outcomeMap": outcome,
        "mixture": mixture,
        "market": _market(snapshot),
        "economics": economics,
        "decision": _decision(snapshot),
        "equations": _equations(snapshot, models, mixture, economics),
        "gates": _gates(snapshot.gates, snapshot),
        "captureHealth": _capture_health(snapshot),
    }


def evaluation_index_item(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    lab = snapshot.probability_lab or {}
    return {
        "evaluationId": str(lab.get("evaluation_id") or snapshot.revision),
        "evaluatedAt": _iso(snapshot.decision.evaluated_at),
        "analysisState": _analysis_state(snapshot),
        "executionState": _execution_state(snapshot),
        "finalReasonCode": snapshot.decision.reason_code,
        "eventTicker": snapshot.event.ticker,
        "selectedMarketTicker": snapshot.decision.focus_ticker,
        "selectedSide": _side(snapshot.decision.focus_side),
    }


def _station(snapshot: SignalRoomSnapshot, target: date) -> dict[str, Any]:
    tz = ZoneInfo("America/Los_Angeles")
    start = datetime.combine(target, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(target, time.max, tzinfo=tz).astimezone(timezone.utc)
    observed = snapshot.risk.observed_high_f
    return {
        "stationId": snapshot.event.station,
        "stationDayStart": _iso(start),
        "stationDayEnd": _iso(end),
        "observedHighF": observed,
        "observedHighAvailableAt": _iso(snapshot.decision.evaluated_at) if observed is not None else None,
        "sourceObservationIds": [f"{snapshot.event.station}:{target.isoformat()}"] if observed is not None else [],
        "status": "healthy" if observed is not None else "missing",
        "statusReason": None if observed is not None else "No accepted observed high is available yet.",
    }


def _outcome_map(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    brackets = [_bracket_from_market(index, row) for index, row in enumerate(snapshot.market)]
    brackets = sorted(brackets, key=lambda item: _bracket_sort_key(item))
    for index, bracket in enumerate(brackets):
        bracket["order"] = index
    verified = bool(snapshot.readiness.settlement_rules_verified)
    reason = "Market brackets are verified and ordered." if verified else "Settlement ladder is incomplete or unavailable."
    return {
        "verified": verified,
        "verificationReason": reason,
        "settlementRulesVersion": "klax_high_temperature_v1" if verified else None,
        "sourceMetadataIds": [snapshot.event.ticker] if brackets else [],
        "brackets": brackets,
    }


def _bracket_from_market(index: int, row: MarketRow) -> dict[str, Any]:
    label = row.bracket or row.ticker
    clean = label.replace(" F", "").strip()
    lower: float | None = None
    upper: float | None = None
    lower_inclusive = True
    upper_inclusive = True
    if clean.startswith("<="):
        upper = _float(clean[2:].strip())
        lower_inclusive = False
    elif clean.startswith(">="):
        lower = _float(clean[2:].strip())
        upper_inclusive = False
    elif "-" in clean:
        left, right = clean.split("-", 1)
        lower = _float(left.strip())
        upper = _float(right.strip())
    return {
        "marketTicker": row.ticker,
        "label": row.bracket,
        "order": index,
        "lowerBoundF": lower,
        "upperBoundF": upper,
        "lowerInclusive": lower_inclusive,
        "upperInclusive": upper_inclusive,
    }


def _models(snapshot: SignalRoomSnapshot, brackets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lab = snapshot.probability_lab or {}
    weights = {
        str(item.get("model_key")): item
        for item in lab.get("weights", [])
        if isinstance(item, dict)
    }
    distributions = {
        str(item.get("model_key")): item
        for item in lab.get("model_distributions", [])
        if isinstance(item, dict)
    }
    slots = {slot.model_key: slot for slot in snapshot.models}
    output: list[dict[str, Any]] = []
    for key in CANONICAL_MODEL_KEYS:
        slot = slots[key]
        weight = weights.get(key, {})
        distribution = distributions.get(key, {})
        effective_weight = _number(weight.get("effective_weight"), _number(slot.effective_weight, 0.0))
        prior_weight = _number(weight.get("prior_weight"), _number(slot.prior_weight, 0.0))
        center = _number(distribution.get("corrected_point_f"), slot.state_f)
        scenarios = _scenario_support(
            center=center,
            observed_floor=snapshot.risk.observed_high_f,
            sigma=_number(distribution.get("residual_sigma_f"), MODEL_SIGMA_F.get(key, 1.5)),
        )
        maturity = _maturity_state(slot, weight)
        eligibility = _eligibility(slot, effective_weight)
        output.append(
            {
                "modelKey": key,
                "label": MODEL_LABELS.get(key, slot.label),
                "sourceVariant": slot.feed_status,
                "runTime": _iso_or_none(slot.source_available_at),
                "sourceAvailableAt": _iso_or_none(slot.source_available_at),
                "receivedAt": _iso_or_none(slot.received_at),
                "remainingWindowStart": None,
                "remainingWindowEnd": None,
                "remainingMaxF": slot.remaining_window_max_f,
                "observedMaxF": snapshot.risk.observed_high_f,
                "rawLiveStateF": slot.state_f,
                "residualMedianF": 0.0 if slot.state_f is not None else None,
                "correctedPointF": center,
                "historyCount": int(weight.get("stage_history_dates") or 0),
                "nEff": float(weight.get("stage_n_eff") or 0.0),
                "priorWeight": prior_weight,
                "effectiveWeight": effective_weight,
                "maturityState": maturity,
                "eligibility": eligibility,
                "ineligibilityReason": weight.get("exclusion_reason")
                or _ineligibility_reason(slot, effective_weight),
                "scenarioTemperaturesF": scenarios["temperatures"],
                "scenarioWeights": scenarios["weights"],
                "bracketProbabilities": _model_bracket_probabilities(distribution, brackets),
                "sourceIds": [f"{snapshot.event.ticker}:{key}"] if slot.feed_status == "healthy" else [],
                "freshnessSeconds": slot.age_seconds,
                "reliabilityScore": _number(weight.get("reliability_multiplier"), 1.0),
                "familyCapApplied": bool(weight.get("family_cap_applied", False)),
            }
        )
    return output


def _maturity_state(slot: ModelSlot, weight: dict[str, Any]) -> str:
    if slot.feed_status != "healthy":
        return "unavailable"
    status = str(weight.get("maturity_status") or slot.maturity_status)
    if status in {"launch_default", "mature", "ready"}:
        return "ready"
    if status == "provisional":
        return "provisional"
    return "immature"


def _eligibility(slot: ModelSlot, effective_weight: float) -> str:
    if slot.feed_status != "healthy":
        return "unavailable"
    if effective_weight > 0:
        return "eligible"
    return "research_only"


def _ineligibility_reason(slot: ModelSlot, effective_weight: float) -> str | None:
    if slot.feed_status != "healthy":
        return slot.status_detail or "Model source is unavailable."
    if effective_weight <= 0:
        return "Model has zero effective weight in this evaluation."
    return None


def _model_effective_n(snapshot: SignalRoomSnapshot, key: str, effective_weight: float) -> float:
    calibration = (snapshot.probability_lab or {}).get("calibration", {})
    base = _number(calibration.get("effective_sample_size"), 0.0) if isinstance(calibration, dict) else 0.0
    if effective_weight <= 0:
        return 0.0
    return round(base * effective_weight, 6)


def _scenario_support(
    *,
    center: float | None,
    observed_floor: float | None,
    sigma: float,
) -> dict[str, list[float]]:
    if center is None or sigma <= 0:
        return {"temperatures": [], "weights": []}
    normal = NormalDist(mu=center, sigma=sigma)
    temps = [
        round(max(observed_floor, normal.inv_cdf((index + 0.5) / SCENARIO_COUNT)), 3)
        if observed_floor is not None
        else round(normal.inv_cdf((index + 0.5) / SCENARIO_COUNT), 3)
        for index in range(SCENARIO_COUNT)
    ]
    weight = round(1.0 / SCENARIO_COUNT, 12)
    weights = [weight for _ in temps]
    if weights:
        weights[-1] = max(0.0, 1.0 - sum(weights[:-1]))
    return {"temperatures": temps, "weights": weights}


def _model_bracket_probabilities(
    distribution: dict[str, Any],
    brackets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_ticker = {
        str(item.get("ticker")): item
        for item in distribution.get("bracket_probabilities", [])
        if isinstance(item, dict)
    }
    rows = []
    for bracket in brackets:
        ticker = bracket["marketTicker"]
        value = _number((by_ticker.get(ticker) or {}).get("p_yes"))
        rows.append(
            {
                "marketTicker": ticker,
                "pMeanYes": value,
                "pSafeYes": value,
                "pMeanNo": None if value is None else 1.0 - value,
                "pSafeNo": None if value is None else 1.0 - value,
            }
        )
    return rows


def _mixture(
    snapshot: SignalRoomSnapshot,
    models: list[dict[str, Any]],
    brackets: list[dict[str, Any]],
) -> dict[str, Any]:
    bracket_rows = {
        row.ticker: row
        for row in snapshot.market
    }
    scenario_temps: list[float] = []
    scenario_weights: list[float] = []
    for model in models:
        model_weight = _number(model.get("effectiveWeight"), 0.0)
        for temp_value, weight_value in zip(
            model["scenarioTemperaturesF"],
            model["scenarioWeights"],
            strict=False,
        ):
            scenario_temps.append(temp_value)
            scenario_weights.append(model_weight * weight_value)
    total_weight = sum(scenario_weights)
    if total_weight > 0:
        scenario_weights = [value / total_weight for value in scenario_weights]
        scenario_weights[-1] = max(0.0, 1.0 - sum(scenario_weights[:-1]))
    calibration = (snapshot.probability_lab or {}).get("calibration", {})
    effective_n = _number(calibration.get("effective_sample_size"), 0.0) if isinstance(calibration, dict) else 0.0
    return {
        "scenarioTemperaturesF": scenario_temps,
        "scenarioWeights": scenario_weights,
        "effectiveSampleSize": effective_n,
        "modelSpreadF": snapshot.risk.model_spread_f,
        "bracketProbabilities": [
            _mixture_bracket_probability(bracket["marketTicker"], bracket_rows.get(bracket["marketTicker"]))
            for bracket in brackets
        ],
    }


def _mixture_bracket_probability(ticker: str, row: MarketRow | None) -> dict[str, Any]:
    p_mean_yes = row.p_mean_yes if row is not None else None
    p_safe_yes = row.p_safe_yes if row is not None else None
    p_mean_no = row.p_mean_no if row is not None else (None if p_mean_yes is None else 1.0 - p_mean_yes)
    p_safe_no = row.p_safe_no if row is not None else None
    return {
        "marketTicker": ticker,
        "pMeanYes": p_mean_yes,
        "mixtureLowerBoundYes": p_safe_yes,
        "weightedComponentLowerBoundYes": p_safe_yes,
        "pTradeYes": p_safe_yes,
        "pMeanNo": p_mean_no,
        "mixtureLowerBoundNo": p_safe_no,
        "weightedComponentLowerBoundNo": p_safe_no,
        "pTradeNo": p_safe_no,
    }


def _market(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    rows = [
        {
            "marketTicker": row.ticker,
            "bracketLabel": row.bracket,
            "yesBid": _number(row.yes_bid),
            "yesAsk": _number(row.yes_ask),
            "noBid": _number(row.no_bid),
            "noAsk": _number(row.no_ask),
            "yesDepth": None,
            "noDepth": None,
            "quoteAvailableAt": _iso(snapshot.decision.evaluated_at) if row.yes_bid or row.yes_ask or row.no_bid or row.no_ask else None,
        }
        for row in snapshot.market
    ]
    if not rows:
        book_state = "unavailable"
    elif snapshot.readiness.orderbook_sequence_valid:
        book_state = "valid"
    else:
        book_state = "unavailable"
    return {
        "bookState": book_state,
        "bookAgeSeconds": None,
        "sequenceValid": snapshot.readiness.orderbook_sequence_valid,
        "feeScheduleVersion": FeeSchedule().version,
        "feeRole": "quote_only",
        "seriesMultiplier": 1,
        "markets": rows,
    }


def _economics(snapshot: SignalRoomSnapshot) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in snapshot.market:
        output.append(_economics_row(snapshot, row, "yes"))
        output.append(_economics_row(snapshot, row, "no"))
    return output


def _economics_row(snapshot: SignalRoomSnapshot, row: MarketRow, side: str) -> dict[str, Any]:
    price = _number(row.yes_ask if side == "yes" else row.no_ask)
    p_mean = row.p_mean_yes if side == "yes" else row.p_mean_no
    p_safe = row.p_safe_yes if side == "yes" else row.p_safe_no
    required = _probability_value(row.required_probability_yes if side == "yes" else row.required_probability_no)
    max_price = _number(row.max_acceptable_yes_price if side == "yes" else row.max_acceptable_no_price)
    active_hurdle = snapshot.risk.active_roi_hurdle
    economics = _trade_numbers(side=side, price=price, p_safe=p_safe, hurdle=active_hurdle)
    return {
        "marketTicker": row.ticker,
        "side": side,
        "quantity": 1,
        "priceBasis": "top_of_book_quote" if price is not None else "unavailable",
        "price": price,
        "exactFee": economics["exactFee"],
        "executionCost": economics["executionCost"],
        "allInCost": economics["allInCost"],
        "pMean": p_mean,
        "pSafe": p_safe,
        "requiredProbability": required if required is not None else economics["requiredProbability"],
        "expectedValue": economics["expectedValue"],
        "modeledNetRoi": _percent_to_ratio(row.modeled_net_roi_yes if side == "yes" else row.modeled_net_roi_no)
        if (row.modeled_net_roi_yes if side == "yes" else row.modeled_net_roi_no)
        else economics["modeledNetRoi"],
        "maxAcceptablePrice": max_price if max_price is not None else economics["maxAcceptablePrice"],
        "activeHurdle": active_hurdle,
        "eligible": row.eligible and price is not None,
        "rejectionReason": None if row.eligible and price is not None else row.status_code,
        "priceSensitivity": _price_sensitivity(active_hurdle),
    }


def _trade_numbers(
    *,
    side: str,
    price: float | None,
    p_safe: float | None,
    hurdle: float,
) -> dict[str, Any]:
    if price is None or price <= 0 or price >= 1:
        return {
            "exactFee": None,
            "executionCost": None,
            "allInCost": None,
            "requiredProbability": None,
            "expectedValue": None,
            "modeledNetRoi": None,
            "maxAcceptablePrice": None,
        }
    price_decimal = Decimal(str(price))
    probability = Decimal(str(p_safe if p_safe is not None else 0.0))
    trade = trade_economics(
        side="yes" if side == "yes" else "no",
        probability=probability,
        quantity=1,
        price=price_decimal,
        role="maker",
        schedule=FeeSchedule(),
    )
    required = _required_probability(price_decimal, Decimal(str(hurdle)))
    max_price = (
        max_qualifying_price(
            probability=probability,
            quantity=1,
            role="maker",
            hurdle=Decimal(str(hurdle)),
            price_levels=whole_cent_price_grid(),
            schedule=FeeSchedule(),
        )
        if p_safe is not None
        else None
    )
    return {
        "exactFee": float(trade.fee),
        "executionCost": float(price_decimal),
        "allInCost": float(trade.all_in_cost),
        "requiredProbability": _probability_value(required),
        "expectedValue": float(trade.expected_value) if p_safe is not None else None,
        "modeledNetRoi": float(trade.roi) if p_safe is not None else None,
        "maxAcceptablePrice": float(max_price) if max_price is not None else None,
    }


def _required_probability(price: Decimal, hurdle: Decimal) -> float:
    cost = trade_economics(
        side="yes",
        probability=Decimal("0"),
        quantity=1,
        price=price,
        role="maker",
        schedule=FeeSchedule(),
    ).all_in_cost
    return float(cost * (Decimal("1") + hurdle))


def _price_sensitivity(hurdle: float) -> list[dict[str, float]]:
    hurdle_decimal = Decimal(str(hurdle))
    return [
        {
            "price": float(price),
            "requiredProbability": _probability_value(_required_probability(price, hurdle_decimal)),
        }
        for price in PRICE_GRID
    ]


def _decision(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    return {
        "candidateId": f"{snapshot.revision}:{snapshot.decision.focus_ticker}:{snapshot.decision.focus_side}"
        if snapshot.decision.focus_ticker
        else None,
        "focusMarketTicker": snapshot.decision.focus_ticker,
        "focusSide": _side(snapshot.decision.focus_side),
        "pMean": snapshot.decision.p_mean,
        "pSafe": snapshot.decision.p_safe,
        "requiredProbability": snapshot.decision.required_probability,
        "modeledNetRoi": _percent_to_ratio(snapshot.decision.modeled_net_roi),
        "maxAcceptablePrice": _number(snapshot.decision.max_acceptable_price),
        "proposedQuantity": _number(snapshot.decision.proposed_quantity, 0.0),
        "reasonCode": snapshot.decision.reason_code,
    }


def _equations(
    snapshot: SignalRoomSnapshot,
    models: list[dict[str, Any]],
    mixture: dict[str, Any],
    economics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lab = snapshot.probability_lab or {}
    weighting = lab.get("weighting") if isinstance(lab.get("weighting"), dict) else {}
    stage = weighting.get("stage") if isinstance(weighting.get("stage"), dict) else {}
    if weighting:
        rows.extend(
            [
                _equation(
                    "stage_classification",
                    "Market stage",
                    {},
                    "stage = backend PT stage classifier(evaluated_at, target_date)",
                    stage.get("stageId"),
                    None,
                ),
                _equation(
                    "transition_blend",
                    "Stage transition blend",
                    {},
                    "prior = (1-alpha) * previous_stage_prior + alpha * stage_prior",
                    stage.get("transitionAlpha"),
                    "ratio",
                ),
            ]
        )
        for weight in weighting.get("models", []):
            if not isinstance(weight, dict):
                continue
            scope = {"modelKey": weight.get("modelKey")}
            rows.extend(
                [
                    _equation("stage_prior", "Stage prior", scope, "prior_m = backend stage-prior lookup", weight.get("stagePrior"), "ratio"),
                    _equation("stage_log_loss", "Prior-date stage log loss", scope, "loss_m = mean prior settled-date log loss", weight.get("stageLogLoss"), None),
                    _equation("shrinkage", "Shrunk stage loss", scope, "shrunk_loss_m = backend recency and uniform shrinkage", weight.get("shrunkLogLoss"), None),
                    _equation("reliability_multiplier", "Reliability multiplier", scope, "r_m = exp(-eta * excess_shrunk_loss_m)", weight.get("reliabilityMultiplier"), "ratio"),
                    _equation("pre_cap_weight", "Pre-cap influence", scope, "u_m = stage_prior_m * reliability_multiplier_m", weight.get("preCapWeight"), "ratio"),
                    _equation("individual_cap", "Individual cap applied", scope, "w_m <= individual_model_cap", bool(weight.get("individualCapApplied")), None),
                    _equation("family_cap", "Family cap applied", scope, "sum(w_family) <= family_cap", bool(weight.get("familyCapApplied")), None),
                    _equation("nbm_maturity_cap", "NBM maturity cap", scope, "w_nbm <= maturity_cap(completed_dates)", weight.get("maturityCap"), "ratio"),
                    _equation("final_weight", "Final normalized weight", scope, "w_m = backend deterministic capped redistribution", weight.get("finalWeight"), "ratio"),
                ]
            )
    for model in models:
        scope = {"modelKey": model["modelKey"]}
        rows.extend(
            [
                _equation("remaining_window_max", "Remaining-window maximum", scope, "R_m = max(forecast remaining hours)", model["remainingMaxF"], "F"),
                _equation("raw_live_state", "Observed floor plus remaining forecast", scope, "X_m = max(O, R_m)", model["rawLiveStateF"], "F"),
                _equation("residual_correction", "Historical correction", scope, "C_m = X_m + residual_median_m", model["residualMedianF"], "F"),
                _equation("corrected_model_state", "Corrected model state", scope, "corrected_point_m = max(O, C_m)", model["correctedPointF"], "F"),
                _equation("mixture_weight", "Mixture model weight", scope, "w_m = effective_weight_m", model["effectiveWeight"], None),
            ]
        )
        for probability in model["bracketProbabilities"]:
            scoped = {**scope, "marketTicker": probability["marketTicker"]}
            rows.append(
                _equation(
                    "model_posterior_mean",
                    "Model posterior mean",
                    scoped,
                    "p_mean_model = backend bracket probability",
                    probability["pMeanYes"],
                    "probability",
                )
            )
            rows.append(
                _equation(
                    "model_conservative_bound",
                    "Model conservative bound",
                    scoped,
                    "p_safe_model = backend conservative probability",
                    probability["pSafeYes"],
                    "probability",
                )
            )
    for probability in mixture["bracketProbabilities"]:
        scope = {"marketTicker": probability["marketTicker"]}
        rows.append(
            _equation(
                "mixture_probability",
                "Weighted mixture probability",
                scope,
                "p_mean = backend weighted mixture",
                probability["pMeanYes"],
                "probability",
            )
        )
        rows.append(
            _equation(
                "final_ptrade",
                "Final conservative trade probability",
                {**scope, "side": "yes"},
                "p_trade = backend final conservative probability",
                probability["pTradeYes"],
                "probability",
            )
        )
        rows.append(
            _equation(
                "final_ptrade",
                "Final conservative trade probability",
                {**scope, "side": "no"},
                "p_trade = backend final conservative probability",
                probability["pTradeNo"],
                "probability",
            )
        )
    for item in economics:
        scope = {"marketTicker": item["marketTicker"], "side": item["side"]}
        rows.extend(
            [
                _equation("required_probability", "Required probability", scope, "required_probability = backend economics output", item["requiredProbability"], "probability"),
                _equation("exact_fee", "Exact fee", scope, "fee = backend fee schedule output", item["exactFee"], "dollars"),
                _equation("expected_value", "Expected value", scope, "expected_value = backend economics output", item["expectedValue"], "dollars"),
                _equation("modeled_roi", "Modeled net ROI", scope, "roi = backend economics output", item["modeledNetRoi"], "ratio"),
                _equation("max_acceptable_price", "Maximum acceptable price", scope, "max_price = backend price grid output", item["maxAcceptablePrice"], "dollars"),
                _equation("kelly_or_risk_size", "Shadow quantity", scope, "quantity = backend risk output", item["quantity"], "contracts"),
            ]
        )
    if not rows:
        rows.append(
            _equation(
                "data_unavailable",
                "Equation trace unavailable",
                {},
                "missing inputs",
                None,
                None,
                missing=["complete probability evaluation"],
            )
        )
    return rows


def _equation(
    equation_id: str,
    label: str,
    scope: dict[str, Any],
    formula: str,
    result: Any,
    units: str | None,
    *,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    missing_inputs = list(missing or ([] if result is not None else ["backend value unavailable"]))
    return {
        "equationId": equation_id,
        "label": label,
        "scope": scope,
        "formula": formula,
        "substitutedExpression": _substitution(result, units) if result is not None else None,
        "result": result,
        "units": units,
        "status": "available" if result is not None else "blocked",
        "missingInputs": missing_inputs,
    }


def _gates(gates: list[GateState], snapshot: SignalRoomSnapshot) -> list[dict[str, Any]]:
    output = [
        {
            "gateCode": gate.code,
            "status": _gate_status(gate.severity),
            "observedValue": gate.severity,
            "requiredValue": "pass",
            "detail": f"{gate.label}: {gate.detail}",
        }
        for gate in gates
    ]
    output.extend(
        [
            {
                "gateCode": "ORDER_PATH_DISABLED",
                "status": "pass" if not snapshot.strategy.order_submission_reachable else "fail",
                "observedValue": snapshot.strategy.order_submission_reachable,
                "requiredValue": False,
                "detail": "Order submission is disabled for the Probability Lab.",
            },
            {
                "gateCode": "BOOK_SEQUENCE_VALIDITY",
                "status": "pass" if snapshot.readiness.orderbook_sequence_valid else "warn",
                "observedValue": snapshot.readiness.orderbook_sequence_valid,
                "requiredValue": True,
                "detail": "Sequence-valid executable book is unavailable; economics are quote-only.",
            },
            {
                "gateCode": "FEE_VERIFICATION",
                "status": "pass" if snapshot.readiness.fee_schedule_verified else "warn",
                "observedValue": snapshot.readiness.fee_schedule_verified,
                "requiredValue": True,
                "detail": "Fee schedule is serialized by the backend economics layer.",
            },
        ]
    )
    seen: set[str] = set()
    deduped = []
    for item in output:
        key = item["gateCode"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _capture_health(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    sources = [
        {
            "sourceKey": f"model:{model.model_key}",
            "status": model.feed_status if model.feed_status in {"healthy", "stale", "missing", "invalid", "disabled"} else "healthy",
            "lastSuccessAt": _iso_or_none(model.received_at),
            "ageSeconds": model.age_seconds,
            "detail": model.status_detail or model.feed_status,
        }
        for model in snapshot.models
    ]
    sources.append(
        {
            "sourceKey": "market:top_of_book",
            "status": "healthy" if snapshot.market else "missing",
            "lastSuccessAt": _iso(snapshot.decision.evaluated_at) if snapshot.market else None,
            "ageSeconds": None,
            "detail": "REST top-of-book quote rows are present." if snapshot.market else "No market rows are present.",
        }
    )
    sources.append(
        {
            "sourceKey": "observation:klax_high",
            "status": "healthy" if snapshot.risk.observed_high_f is not None else "missing",
            "lastSuccessAt": _iso(snapshot.decision.evaluated_at) if snapshot.risk.observed_high_f is not None else None,
            "ageSeconds": None,
            "detail": "Observed high floor is available." if snapshot.risk.observed_high_f is not None else "Observed high floor is unavailable.",
        }
    )
    has_fail = any(gate.severity == "block" for gate in snapshot.gates)
    has_warn = has_fail or any(gate.severity == "warning" for gate in snapshot.gates)
    return {
        "captureHealthId": snapshot.revision,
        "overallStatus": "blocked" if has_fail else "degraded" if has_warn else "healthy",
        "sources": sources,
        "orderPathReachable": False,
    }


def _analysis_state(snapshot: SignalRoomSnapshot) -> str:
    lab_mode = str((snapshot.probability_lab or {}).get("mode") or "")
    if snapshot.decision.status == "DATA_INCOMPLETE":
        return "DATA_BLOCKED" if lab_mode == "incomplete" else "ANALYSIS_PARTIAL"
    if not snapshot.market:
        return "ANALYSIS_PARTIAL"
    return "ANALYSIS_READY"


def _execution_state(snapshot: SignalRoomSnapshot) -> str:
    if snapshot.decision.status == "DATA_INCOMPLETE":
        return "BLOCKED"
    if snapshot.decision.status == "SHADOW_ONLY":
        return "SHADOW_CANDIDATE"
    return "NO_TRADE"


def _gate_status(severity: str) -> str:
    if severity == "pass":
        return "pass"
    if severity == "block":
        return "fail"
    if severity == "warning":
        return "warn"
    return "not_applicable"


def _bracket_sort_key(item: dict[str, Any]) -> tuple[float, float]:
    lower = item["lowerBoundF"]
    upper = item["upperBoundF"]
    return (
        -math.inf if lower is None else float(lower),
        math.inf if upper is None else float(upper),
    )


def _substitution(result: Any, units: str | None) -> str:
    if isinstance(result, float):
        text = f"{result:.6g}"
    else:
        text = str(result)
    return f"{text} {units}" if units else text


def _side(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"yes", "no"}:
        return lowered
    return None


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "")
    if not text or text == "--":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _probability_value(value: Any) -> float | None:
    numeric = _number(value)
    if numeric is None:
        return None
    return max(0.0, min(1.0, numeric))


def _float(value: Any) -> float | None:
    return _number(value)


def _percent_to_ratio(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) / 100.0
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_or_none(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None
