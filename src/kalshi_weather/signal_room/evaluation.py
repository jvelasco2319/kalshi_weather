from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from scipy.stats import beta as beta_distribution

from kalshi_weather.data.market_discovery import bracket_text_from_market, parse_bracket_label
from kalshi_weather.schemas import Bracket
from kalshi_weather.signal_room.api_models import (
    DecisionState,
    GateState,
    MarketRow,
    ModelSlot,
    RiskSnapshot,
)
from kalshi_weather.strategy_current.config import StrategyConfig
from kalshi_weather.strategy_current.economics import (
    FeeSchedule,
    max_qualifying_price,
    trade_economics,
    whole_cent_price_grid,
)
from kalshi_weather.strategy_current.reason_codes import (
    NO_TRADE_BOOK_INVALID,
    NO_TRADE_BOOK_SEQUENCE_GAP,
    NO_TRADE_OBSERVATION_INVALID,
    NO_TRADE_ROI_BELOW_HURDLE,
    NO_TRADE_SETTLEMENT_RULES_UNVERIFIED,
    NO_TRADE_TOO_FEW_MODELS,
    SHADOW_CANDIDATE_NO,
    SHADOW_CANDIDATE_YES,
)
from kalshi_weather.strategy_current.registry import (
    CANONICAL_MODEL_KEYS,
    canonicalize_model_key,
    strategy_model_by_key,
)
from kalshi_weather.strategy_current.settlement import (
    settlement_bracket_from_market_bracket,
    validate_settlement_brackets,
)

NO_TRADE_VALIDATION_EVALUATION_UNAVAILABLE = "NO_TRADE_VALIDATION_EVALUATION_UNAVAILABLE"
SHADOW_QUOTE_EVALUATED = "SHADOW_QUOTE_EVALUATED"
SHADOW_QUOTE_CANDIDATE = "SHADOW_QUOTE_CANDIDATE"
CALIBRATION_LAUNCH_DEFAULT = "CALIBRATION_LAUNCH_DEFAULT"


@dataclass(frozen=True)
class ValidationShadowEvaluation:
    decision: DecisionState
    risk: RiskSnapshot
    market: list[MarketRow]
    gates: list[GateState]
    probability_lab: dict[str, Any]
    explainability: dict[str, Any]
    source_ids: list[str]


@dataclass(frozen=True)
class SideEvaluation:
    ticker: str
    bracket: str
    side: str
    price: Decimal
    p_mean: float
    p_safe: float
    required_probability: float
    roi: Decimal
    expected_value: Decimal
    max_acceptable_price: Decimal | None


def evaluate_validation_snapshot(
    *,
    snapshot_row: dict[str, Any],
    model_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
    observation_rows: list[dict[str, Any]],
    model_slots: list[ModelSlot],
    config: StrategyConfig,
) -> ValidationShadowEvaluation:
    target = date.fromisoformat(str(snapshot_row["target_date"]))
    evaluated_at = _parse_dt(str(snapshot_row["captured_utc"]))
    source_ids = [f"validation_snapshot:{snapshot_row['id']}"]
    model_inputs = _canonical_model_inputs(model_rows)
    brackets = _market_brackets(market_rows)
    observed_high = _observed_high(observation_rows, target)
    weights = _launch_weights(model_inputs.keys(), config) if model_inputs else {}
    _annotate_model_slots(model_slots, brackets, weights)

    gates = _base_gates(
        model_inputs=model_inputs,
        brackets=brackets,
        observed_high=observed_high,
        config=config,
    )
    enough_models = len(model_inputs) >= config.minimum_feeds_for_trade_probability
    settlement_ready = _settlement_rules_verified(brackets.values())
    enough_brackets = bool(brackets) and settlement_ready
    if not enough_models or not enough_brackets:
        decision = DecisionState(
            evaluated_at=evaluated_at,
            status="DATA_INCOMPLETE",
            reason_code=(
                NO_TRADE_TOO_FEW_MODELS
                if not enough_models
                else NO_TRADE_SETTLEMENT_RULES_UNVERIFIED
            ),
            reason_text=(
                f"{len(model_inputs)} canonical model states and {len(brackets)} market brackets "
                "are available; shadow probability evaluation is waiting for a complete "
                "settlement ladder."
            ),
        )
        market = _plain_market_rows(market_rows, brackets)
        lab = _probability_lab_payload(
            evaluation_id=_evaluation_id(target, evaluated_at, model_inputs, market_rows),
            target=target,
            evaluated_at=evaluated_at,
            model_inputs=model_inputs,
            weights=weights,
            brackets=brackets,
            model_probabilities={},
            bracket_probabilities={},
            market=market,
            side_evaluations=[],
            mode="incomplete",
        )
        risk = RiskSnapshot(
            model_spread_f=_model_spread(model_inputs),
            active_roi_hurdle=float(config.launch_expected_roi_hurdle),
            observed_high_f=observed_high,
            market_leader_bracket=_market_leader(market).bracket if _market_leader(market) else None,
            target_date_exposure_pct="0",
            daily_loss_pct="0",
        )
        return ValidationShadowEvaluation(
            decision=decision,
            risk=risk,
            market=market,
            gates=gates,
            probability_lab=lab,
            explainability=_explainability_payload(
                lab,
                decision,
                gates,
                source_ids,
                config=config,
                quote_only=True,
            ),
            source_ids=source_ids,
        )

    model_probabilities = {
        model_key: _model_probabilities_for_brackets(
            center_f=float(row["estimated_high_f"]),
            observed_high_f=observed_high,
            brackets=brackets,
            sigma_f=_sigma_for_model(model_key),
        )
        for model_key, row in model_inputs.items()
    }
    bracket_probabilities = _mixture_probabilities(
        brackets=brackets,
        model_probabilities=model_probabilities,
        weights=weights,
        effective_n=20.0,
    )
    market, side_evaluations = _evaluated_market_rows(
        market_rows=market_rows,
        brackets=brackets,
        bracket_probabilities=bracket_probabilities,
        model_probabilities=model_probabilities,
        hurdle=config.launch_expected_roi_hurdle,
    )
    selected = _best_side(side_evaluations, config.launch_expected_roi_hurdle)
    for row in market:
        row.candidate = selected is not None and row.ticker == selected.ticker

    if selected is None:
        reason_code = NO_TRADE_ROI_BELOW_HURDLE if side_evaluations else NO_TRADE_BOOK_INVALID
        decision = DecisionState(
            evaluated_at=evaluated_at,
            status="NO_TRADE",
            reason_code=reason_code,
            reason_text=(
                "Recorder data is complete enough for a quote-based shadow evaluation, "
                "but no YES/NO side clears the launch ROI hurdle."
            ),
            focus_ticker=_market_leader(market).ticker if _market_leader(market) else None,
            focus_bracket=_market_leader(market).bracket if _market_leader(market) else None,
            focus_side="YES" if _market_leader(market) else None,
            executable_price=_market_leader(market).yes_ask if _market_leader(market) else None,
        )
    else:
        reason_code = SHADOW_CANDIDATE_YES if selected.side == "yes" else SHADOW_CANDIDATE_NO
        decision = DecisionState(
            evaluated_at=evaluated_at,
            status="SHADOW_ONLY",
            reason_code=reason_code,
            reason_text=(
                "Quote-based shadow candidate found from the validation recorder snapshot. "
                "Live order submission remains disabled."
            ),
            focus_ticker=selected.ticker,
            focus_bracket=selected.bracket,
            focus_side=selected.side.upper(),
            executable_price=_money(selected.price),
            p_mean=selected.p_mean,
            p_safe=selected.p_safe,
            required_probability=selected.required_probability,
            modeled_net_roi=_pct_decimal(selected.roi),
            max_acceptable_price=(
                _money(selected.max_acceptable_price)
                if selected.max_acceptable_price is not None
                else None
            ),
            proposed_quantity="1",
        )

    leader = _market_leader(market)
    lab = _probability_lab_payload(
        evaluation_id=_evaluation_id(target, evaluated_at, model_inputs, market_rows),
        target=target,
        evaluated_at=evaluated_at,
        model_inputs=model_inputs,
        weights=weights,
        brackets=brackets,
        model_probabilities=model_probabilities,
        bracket_probabilities=bracket_probabilities,
        market=market,
        side_evaluations=side_evaluations,
        mode="quote_shadow",
    )
    risk = RiskSnapshot(
        model_spread_f=_model_spread(model_inputs),
        active_roi_hurdle=float(config.launch_expected_roi_hurdle),
        adjusted_probability_hurdle=decision.required_probability,
        observed_high_f=observed_high,
        market_leader_bracket=leader.bracket if leader else None,
        risk_multiplier="1.00",
        target_date_exposure_pct="0",
        daily_loss_pct="0",
    )
    return ValidationShadowEvaluation(
        decision=decision,
        risk=risk,
        market=market,
        gates=gates,
        probability_lab=lab,
        explainability=_explainability_payload(
            lab,
            decision,
            gates,
            source_ids,
            config=config,
            quote_only=True,
        ),
        source_ids=source_ids,
    )


def _canonical_model_inputs(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            key = canonicalize_model_key(str(row.get("model_key")))
        except ValueError:
            continue
        if row.get("fetch_status") != "ok" or row.get("estimated_high_f") is None:
            continue
        current = selected.get(key)
        if current is None or _row_rank(row) > _row_rank(current):
            copy = dict(row)
            copy["model_key"] = key
            selected[key] = copy
    return {key: selected[key] for key in CANONICAL_MODEL_KEYS if key in selected}


def _row_rank(row: dict[str, Any]) -> int:
    key = str(row.get("model_key") or "")
    provider = str(row.get("provider") or "")
    return (2 if key != "nam_conus" else 1) + (1 if "Herbie" in provider else 0)


def _market_brackets(rows: list[dict[str, Any]]) -> dict[str, Bracket]:
    output: dict[str, Bracket] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        bracket = _bracket_from_row(row)
        if bracket is not None:
            output[ticker] = bracket
    return output


def _bracket_from_row(row: dict[str, Any]) -> Bracket | None:
    ticker = str(row.get("ticker") or "")
    raw = _json_object(row.get("raw_json"))
    market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
    candidates = [
        bracket_text_from_market(market),
        str(row.get("bracket_label") or ""),
    ]
    for text in candidates:
        bracket = parse_bracket_label(ticker, text)
        if bracket is not None:
            return Bracket(
                ticker=ticker,
                label=_canonical_bracket_label(bracket),
                lo_f=bracket.lo_f,
                hi_f=bracket.hi_f,
            )
    return None


def _canonical_bracket_label(bracket: Bracket) -> str:
    if bracket.lo_f is None and bracket.hi_f is not None:
        return f"<={bracket.hi_f}"
    if bracket.hi_f is None and bracket.lo_f is not None:
        return f">={bracket.lo_f}"
    if bracket.lo_f is not None and bracket.hi_f is not None:
        return f"{bracket.lo_f}-{bracket.hi_f}"
    return bracket.label


def _launch_weights(keys: Iterable[str], config: StrategyConfig) -> dict[str, float]:
    active = [key for key in CANONICAL_MODEL_KEYS if key in set(keys)]
    raw = {key: float(config.prior_weights[key]) for key in active}
    weights = _normalize(raw)
    gfs_keys = [key for key in ("gfs013", "gfs_seamless") if key in weights]
    gfs_cap = float(config.family_caps.get("GFS", Decimal("1")))
    gfs_total = sum(weights[key] for key in gfs_keys)
    if gfs_total > gfs_cap and gfs_keys:
        scale = gfs_cap / gfs_total
        released = 0.0
        for key in gfs_keys:
            old = weights[key]
            weights[key] *= scale
            released += old - weights[key]
        receivers = [key for key in weights if key not in gfs_keys]
        receiver_total = sum(weights[key] for key in receivers)
        for key in receivers:
            weights[key] += released * weights[key] / receiver_total if receiver_total else 0.0
    return {key: weights.get(key, 0.0) for key in CANONICAL_MODEL_KEYS}


def _normalize(raw: dict[str, float]) -> dict[str, float]:
    total = sum(value for value in raw.values() if value > 0)
    if total <= 0:
        return {key: 0.0 for key in raw}
    return {key: max(0.0, value) / total for key, value in raw.items()}


def _model_probabilities_for_brackets(
    *,
    center_f: float,
    observed_high_f: float | None,
    brackets: dict[str, Bracket],
    sigma_f: float,
) -> dict[str, float]:
    return {
        ticker: _bracket_probability_normal(
            center_f=center_f,
            observed_high_f=observed_high_f,
            bracket=bracket,
            sigma_f=sigma_f,
        )
        for ticker, bracket in brackets.items()
    }


def _bracket_probability_normal(
    *,
    center_f: float,
    observed_high_f: float | None,
    bracket: Bracket,
    sigma_f: float,
) -> float:
    lower = -math.inf if bracket.lo_f is None else float(bracket.lo_f) - 0.5
    upper = math.inf if bracket.hi_f is None else float(bracket.hi_f) + 0.5
    if observed_high_f is None:
        return max(0.0, min(1.0, _normal_cdf(upper, center_f, sigma_f) - _normal_cdf(lower, center_f, sigma_f)))
    floor = float(observed_high_f)
    if floor >= upper:
        return 0.0
    if lower <= floor < upper:
        return max(0.0, min(1.0, _normal_cdf(upper, center_f, sigma_f)))
    return max(0.0, min(1.0, _normal_cdf(upper, center_f, sigma_f) - _normal_cdf(lower, center_f, sigma_f)))


def _normal_cdf(value: float, mean: float, sigma: float) -> float:
    if value == math.inf:
        return 1.0
    if value == -math.inf:
        return 0.0
    z = (value - mean) / (sigma * math.sqrt(2.0))
    return 0.5 * (1.0 + math.erf(z))


def _mixture_probabilities(
    *,
    brackets: dict[str, Bracket],
    model_probabilities: dict[str, dict[str, float]],
    weights: dict[str, float],
    effective_n: float,
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for ticker in brackets:
        p_yes = sum(
            weights.get(model_key, 0.0) * model_probabilities.get(model_key, {}).get(ticker, 0.0)
            for model_key in CANONICAL_MODEL_KEYS
        )
        safe_yes = _beta_lower(p_yes, effective_n)
        safe_no = _beta_lower(1.0 - p_yes, effective_n)
        output[ticker] = {
            "p_mean_yes": p_yes,
            "p_safe_yes": safe_yes,
            "p_mean_no": 1.0 - p_yes,
            "p_safe_no": safe_no,
            "effective_sample_size": effective_n,
        }
    return output


def _beta_lower(probability: float, effective_n: float, alpha: float = 0.5, q: float = 0.10) -> float:
    p = min(1.0, max(0.0, probability))
    yes_alpha = p * effective_n + alpha
    no_alpha = (1.0 - p) * effective_n + alpha
    return float(beta_distribution.ppf(q, yes_alpha, no_alpha))


def _evaluated_market_rows(
    *,
    market_rows: list[dict[str, Any]],
    brackets: dict[str, Bracket],
    bracket_probabilities: dict[str, dict[str, float]],
    model_probabilities: dict[str, dict[str, float]],
    hurdle: Decimal,
) -> tuple[list[MarketRow], list[SideEvaluation]]:
    market: list[MarketRow] = []
    side_evaluations: list[SideEvaluation] = []
    for row in market_rows:
        ticker = str(row.get("ticker") or "")
        bracket = brackets.get(ticker)
        if bracket is None:
            continue
        probabilities = bracket_probabilities.get(ticker) or {}
        yes_ask = _price_from_cents(row.get("yes_ask_cents"))
        yes_bid = _price_from_cents(row.get("yes_bid_cents"))
        no_bid = _price_from_cents(row.get("no_bid_cents"))
        no_ask = _price_from_cents(row.get("no_ask_cents"))
        if yes_ask is None and no_bid is not None:
            yes_ask = Decimal("1") - no_bid
        if no_ask is None and yes_bid is not None:
            no_ask = Decimal("1") - yes_bid
        yes_eval = _side_eval(
            ticker=ticker,
            bracket=bracket.label,
            side="yes",
            price=yes_ask,
            p_mean=float(probabilities.get("p_mean_yes", 0.0)),
            p_safe=float(probabilities.get("p_safe_yes", 0.0)),
            hurdle=hurdle,
        )
        no_eval = _side_eval(
            ticker=ticker,
            bracket=bracket.label,
            side="no",
            price=no_ask,
            p_mean=float(probabilities.get("p_mean_no", 0.0)),
            p_safe=float(probabilities.get("p_safe_no", 0.0)),
            hurdle=hurdle,
        )
        for item in (yes_eval, no_eval):
            if item is not None:
                side_evaluations.append(item)
        eligible = any(item is not None and item.roi >= hurdle for item in (yes_eval, no_eval))
        market.append(
            MarketRow(
                ticker=ticker,
                bracket=f"{bracket.label} F",
                yes_bid=_money_or_none(yes_bid),
                yes_ask=_money_or_none(yes_ask),
                no_bid=_money_or_none(no_bid),
                no_ask=_money_or_none(no_ask),
                p_mean_yes=float(probabilities.get("p_mean_yes", 0.0)),
                p_safe_yes=float(probabilities.get("p_safe_yes", 0.0)),
                p_mean_no=float(probabilities.get("p_mean_no", 0.0)),
                p_safe_no=float(probabilities.get("p_safe_no", 0.0)),
                required_probability_yes=yes_eval.required_probability if yes_eval else None,
                required_probability_no=no_eval.required_probability if no_eval else None,
                modeled_net_roi_yes=_pct_decimal(yes_eval.roi) if yes_eval else None,
                modeled_net_roi_no=_pct_decimal(no_eval.roi) if no_eval else None,
                max_acceptable_yes_price=(
                    _money(yes_eval.max_acceptable_price)
                    if yes_eval and yes_eval.max_acceptable_price is not None
                    else None
                ),
                max_acceptable_no_price=(
                    _money(no_eval.max_acceptable_price)
                    if no_eval and no_eval.max_acceptable_price is not None
                    else None
                ),
                model_point_support_count=sum(
                    1 for values in model_probabilities.values() if ticker in values
                ),
                eligible=eligible,
                candidate=False,
                status_code=SHADOW_QUOTE_CANDIDATE if eligible else NO_TRADE_ROI_BELOW_HURDLE,
            )
        )
    return market, side_evaluations


def _side_eval(
    *,
    ticker: str,
    bracket: str,
    side: str,
    price: Decimal | None,
    p_mean: float,
    p_safe: float,
    hurdle: Decimal,
) -> SideEvaluation | None:
    if price is None or price <= 0 or price >= 1:
        return None
    economics = trade_economics(
        side="yes" if side == "yes" else "no",
        probability=Decimal(str(p_safe)),
        quantity=1,
        price=price,
        role="maker",
        schedule=FeeSchedule(),
    )
    required = _required_probability(price=price, hurdle=hurdle)
    max_price = max_qualifying_price(
        probability=Decimal(str(p_safe)),
        quantity=1,
        role="maker",
        hurdle=hurdle,
        price_levels=whole_cent_price_grid(),
        schedule=FeeSchedule(),
    )
    return SideEvaluation(
        ticker=ticker,
        bracket=f"{bracket} F",
        side=side,
        price=price,
        p_mean=p_mean,
        p_safe=p_safe,
        required_probability=required,
        roi=economics.roi,
        expected_value=economics.expected_value,
        max_acceptable_price=max_price,
    )


def _required_probability(*, price: Decimal, hurdle: Decimal) -> float:
    cost = trade_economics(
        side="yes",
        probability=Decimal("0"),
        quantity=1,
        price=price,
        role="maker",
        schedule=FeeSchedule(),
    ).all_in_cost
    return float(cost * (Decimal("1") + hurdle))


def _best_side(
    side_evaluations: list[SideEvaluation],
    hurdle: Decimal,
) -> SideEvaluation | None:
    passing = [item for item in side_evaluations if item.roi >= hurdle]
    if not passing:
        return None
    return max(passing, key=lambda item: (item.expected_value, item.roi, item.ticker, item.side))


def _base_gates(
    *,
    model_inputs: dict[str, dict[str, Any]],
    brackets: dict[str, Bracket],
    observed_high: float | None,
    config: StrategyConfig,
) -> list[GateState]:
    gates = [
        GateState(
            code="ORDER_PATH_DISABLED",
            label="Live order path",
            severity="pass",
            detail="Dashboard is read-only and order submission is disabled.",
        ),
        GateState(
            code=CALIBRATION_LAUNCH_DEFAULT,
            label="Probability calibration",
            severity="warning",
            detail=(
                "Using deterministic launch residual assumptions until enough settled "
                "current-strategy dates are backfilled."
            ),
        ),
        GateState(
            code=NO_TRADE_BOOK_SEQUENCE_GAP,
            label="Executable book",
            severity="warning",
            detail=(
                "REST top-of-book quotes are available for shadow economics, but no "
                "sequence-valid executable depth book is connected."
            ),
        ),
    ]
    if len(model_inputs) >= config.minimum_feeds_for_trade_probability:
        gates.append(
            GateState(
                code="FIVE_MODEL_RECORDER_VALUES_AVAILABLE",
                label="Five-model availability",
                severity="pass",
                detail=f"{len(model_inputs)} canonical current-strategy model estimates are available.",
            )
        )
    else:
        gates.append(
            GateState(
                code=NO_TRADE_TOO_FEW_MODELS,
                label="Five-model availability",
                severity="block",
                detail=f"{len(model_inputs)} canonical current-strategy model estimates are available.",
            )
        )
    if _settlement_rules_verified(brackets.values()):
        gates.append(
            GateState(
                code="SETTLEMENT_RULES_VERIFIED",
                label="Outcome mapping",
                severity="pass",
                detail="Market brackets parse into a continuous KLAX settlement ladder.",
            )
        )
    else:
        gates.append(
            GateState(
                code=NO_TRADE_SETTLEMENT_RULES_UNVERIFIED,
                label="Outcome mapping",
                severity="block",
                detail="Market brackets are not a complete continuous settlement ladder.",
            )
        )
    if observed_high is None:
        gates.append(
            GateState(
                code=NO_TRADE_OBSERVATION_INVALID,
                label="Observed high",
                severity="warning",
                detail="No accepted observed high is available for this target date yet.",
            )
        )
    else:
        gates.append(
            GateState(
                code="OBSERVED_HIGH_AVAILABLE",
                label="Observed high",
                severity="pass",
                detail=f"Observed high floor is {observed_high:.1f} F.",
            )
        )
    return sorted(gates, key=lambda gate: {"block": 0, "warning": 1, "info": 2, "pass": 3}[gate.severity])


def _settlement_rules_verified(brackets: Iterable[Bracket]) -> bool:
    try:
        validate_settlement_brackets(
            [settlement_bracket_from_market_bracket(bracket) for bracket in brackets]
        )
    except ValueError:
        return False
    return True


def _plain_market_rows(
    market_rows: list[dict[str, Any]],
    brackets: dict[str, Bracket],
) -> list[MarketRow]:
    output: list[MarketRow] = []
    for row in market_rows:
        ticker = str(row.get("ticker") or "")
        bracket = brackets.get(ticker)
        output.append(
            MarketRow(
                ticker=ticker,
                bracket=f"{(bracket.label if bracket else str(row.get('bracket_label') or 'unknown'))} F",
                yes_bid=_money_or_none(_price_from_cents(row.get("yes_bid_cents"))),
                yes_ask=_money_or_none(_price_from_cents(row.get("yes_ask_cents"))),
                no_bid=_money_or_none(_price_from_cents(row.get("no_bid_cents"))),
                no_ask=_money_or_none(_price_from_cents(row.get("no_ask_cents"))),
                status_code=NO_TRADE_VALIDATION_EVALUATION_UNAVAILABLE,
            )
        )
    return output


def _probability_lab_payload(
    *,
    evaluation_id: str,
    target: date,
    evaluated_at: datetime,
    model_inputs: dict[str, dict[str, Any]],
    weights: dict[str, float],
    brackets: dict[str, Bracket],
    model_probabilities: dict[str, dict[str, float]],
    bracket_probabilities: dict[str, dict[str, float]],
    market: list[MarketRow],
    side_evaluations: list[SideEvaluation],
    mode: str,
) -> dict[str, Any]:
    focus = _best_side(side_evaluations, Decimal("0")) if side_evaluations else None
    return {
        "schema_version": "probability_lab.v1",
        "evaluation_id": evaluation_id,
        "target_date": target.isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "mode": mode,
        "calibration": {
            "source": "launch_default_residual",
            "settled_dates": 0,
            "minimum_settled_dates": 30,
            "effective_sample_size": 20.0 if mode != "incomplete" else 0.0,
            "safe_probability_quantile": 0.10,
        },
        "weights": [
            {
                "model_key": key,
                "label": strategy_model_by_key(key).key.upper(),
                "prior_weight": float(strategy_model_by_key(key).prior_weight),
                "effective_weight": weights.get(key, 0.0),
                "completed_dates": 0,
                "maturity_status": "launch_default" if key in model_inputs else "missing",
            }
            for key in CANONICAL_MODEL_KEYS
        ],
        "model_distributions": [
            {
                "model_key": key,
                "state_f": float(row["estimated_high_f"]),
                "corrected_point_f": float(row["estimated_high_f"]),
                "residual_sigma_f": _sigma_for_model(key),
                "effective_weight": weights.get(key, 0.0),
                "bracket_probabilities": [
                    {
                        "ticker": ticker,
                        "bracket": f"{bracket.label} F",
                        "p_yes": model_probabilities.get(key, {}).get(ticker),
                    }
                    for ticker, bracket in brackets.items()
                ],
            }
            for key, row in model_inputs.items()
        ],
        "brackets": [
            {
                "ticker": row.ticker,
                "bracket": row.bracket,
                "p_mean_yes": row.p_mean_yes,
                "p_safe_yes": row.p_safe_yes,
                "p_safe_no": row.p_safe_no,
                "yes_ask": row.yes_ask,
                "no_ask": row.no_ask,
                "required_probability_yes": row.required_probability_yes,
                "required_probability_no": row.required_probability_no,
                "modeled_net_roi_yes": row.modeled_net_roi_yes,
                "modeled_net_roi_no": row.modeled_net_roi_no,
                "component_probabilities": {
                    key: model_probabilities.get(key, {}).get(row.ticker)
                    for key in CANONICAL_MODEL_KEYS
                    if key in model_probabilities
                },
            }
            for row in market
        ],
        "probability_funnel": [
            {"stage": "canonical_model_states", "value": len(model_inputs)},
            {"stage": "market_brackets", "value": len(brackets)},
            {"stage": "positive_weight_models", "value": sum(1 for value in weights.values() if value > 0)},
            {"stage": "quote_sides_evaluated", "value": len(side_evaluations)},
        ],
        "equation_trace": {
            "settlement_high": "settlement_high = max(observed_high_so_far, future_high_sample)",
            "component_probability": "p_model(bracket) = NormalCDF(upper) - NormalCDF(lower), adjusted for observed floor",
            "mixture": "p_mean_yes = sum(model_weight * p_model_yes)",
            "safe_probability": "p_safe = Beta lower 10% bound with launch effective sample size",
            "economics": "EV = p_safe - all_in_cost; ROI = EV / all_in_cost",
            "book_source": "REST top-of-book quote only; sequence-valid executable depth is not connected",
        },
        "sensitivity": _sensitivity_rows(focus) if focus else [],
    }


def _sensitivity_rows(focus: SideEvaluation) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cents in range(1, 100, 5):
        price = Decimal(cents) / Decimal("100")
        economics = trade_economics(
            side="yes" if focus.side == "yes" else "no",
            probability=Decimal(str(focus.p_safe)),
            quantity=1,
            price=price,
            role="maker",
            schedule=FeeSchedule(),
        )
        rows.append(
            {
                "price": _money(price),
                "roi": _pct_decimal(economics.roi),
                "required_probability": _required_probability(price=price, hurdle=Decimal("0.15")),
            }
        )
    return rows


def _explainability_payload(
    lab: dict[str, Any],
    decision: DecisionState,
    gates: list[GateState],
    source_ids: list[str],
    *,
    config: StrategyConfig,
    quote_only: bool,
) -> dict[str, Any]:
    return {
        "schema_version": "explainability_snapshot.v1",
        "evaluation_id": lab["evaluation_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": config.strategy_id,
        "config_hash": config.config_hash,
        "source_ids": source_ids,
        "decision": decision.model_dump(mode="json"),
        "blocking_codes": [gate.code for gate in gates if gate.severity == "block"],
        "warning_codes": [gate.code for gate in gates if gate.severity == "warning"],
        "order_submission_reachable": config.order_submission_reachable,
        "live_trading_enabled": config.live_trading_enabled,
        "quote_only_economics": quote_only,
        "probability_lab_ref": lab["evaluation_id"],
    }


def _annotate_model_slots(
    slots: list[ModelSlot],
    brackets: dict[str, Bracket],
    weights: dict[str, float],
) -> None:
    ordered = list(brackets.values())
    for slot in slots:
        if slot.state_f is not None and ordered:
            slot.mapped_bracket = _label_for_value(slot.state_f, ordered)
        if slot.model_key in weights and weights[slot.model_key] > 0:
            slot.effective_weight = f"{weights[slot.model_key]:.2f}"
        if slot.feed_status == "healthy":
            slot.status_detail = "Shadow-evaluated from validation recorder snapshot."


def _label_for_value(value: float, brackets: list[Bracket]) -> str | None:
    for bracket in brackets:
        lower = -math.inf if bracket.lo_f is None else bracket.lo_f - 0.5
        upper = math.inf if bracket.hi_f is None else bracket.hi_f + 0.5
        if lower <= float(value) < upper:
            return f"{bracket.label} F"
    return None


def _observed_high(rows: list[dict[str, Any]], target: date) -> float | None:
    values: list[float] = []
    for row in rows:
        if str(row.get("target_date")) != target.isoformat():
            continue
        value = row.get("high_so_far_f")
        if value is not None:
            values.append(float(value))
    return max(values) if values else None


def _model_spread(model_inputs: dict[str, dict[str, Any]]) -> float | None:
    values = [float(row["estimated_high_f"]) for row in model_inputs.values()]
    if len(values) < 2:
        return None
    return round(max(values) - min(values), 2)


def _market_leader(rows: list[MarketRow]) -> MarketRow | None:
    if not rows:
        return None

    def score(row: MarketRow) -> Decimal:
        if row.yes_bid is not None:
            return Decimal(row.yes_bid)
        if row.no_ask is not None:
            return Decimal("1") - Decimal(row.no_ask)
        if row.yes_ask is not None:
            return Decimal(row.yes_ask)
        return Decimal("-1")

    ranked = [row for row in rows if score(row) >= 0]
    return max(ranked, key=score) if ranked else None


def _sigma_for_model(model_key: str) -> float:
    return {
        "ecmwf_ifs": 1.2,
        "gfs013": 1.4,
        "gfs_seamless": 1.4,
        "nam": 1.6,
        "nbm": 1.3,
    }.get(model_key, 1.5)


def _price_from_cents(value: Any) -> Decimal | None:
    if value is None:
        return None
    return (Decimal(str(value)) / Decimal("100")).quantize(Decimal("0.01"))


def _money_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _money(value)


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _pct_decimal(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'))}%"


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _evaluation_id(
    target: date,
    evaluated_at: datetime,
    model_inputs: dict[str, dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> str:
    payload = {
        "target": target.isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "models": {
            key: row.get("estimated_high_f")
            for key, row in model_inputs.items()
        },
        "markets": [
            {
                "ticker": row.get("ticker"),
                "yes_bid_cents": row.get("yes_bid_cents"),
                "yes_ask_cents": row.get("yes_ask_cents"),
                "no_bid_cents": row.get("no_bid_cents"),
                "no_ask_cents": row.get("no_ask_cents"),
            }
            for row in market_rows
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
