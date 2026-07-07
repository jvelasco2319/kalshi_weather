from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorInput


@dataclass(frozen=True)
class TradeQualityResult:
    score: int
    component_scores: dict[str, float] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    hard_veto_flags: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "component_scores": self.component_scores,
            "risk_flags": self.risk_flags,
            "hard_veto_flags": self.hard_veto_flags,
            "explanation": self.explanation,
        }


def score_trade_quality(advisor_input: AdvisorInput | dict[str, Any]) -> TradeQualityResult:
    payload = advisor_input.to_dict() if isinstance(advisor_input, AdvisorInput) else advisor_input
    candidate = dict(payload.get("candidate_trade") or {})
    model = dict(payload.get("model") or {})
    weather = dict(payload.get("current_weather") or {})
    risk = dict(payload.get("risk_state") or {})
    market = dict(payload.get("market_context") or {})
    config = dict(payload.get("configuration") or {})
    component_scores: dict[str, float] = {}
    risk_flags: list[str] = []
    hard_veto_flags: list[str] = []

    edge = _decimal(candidate.get("fee_adjusted_edge", candidate.get("edge")))
    model_probability = _float(candidate.get("calibrated_probability", candidate.get("model_probability")))
    ask = _decimal(candidate.get("entry_ask", candidate.get("ask")))
    exit_bid = _decimal(candidate.get("exit_bid", candidate.get("bid")))
    spread = _decimal(candidate.get("spread", candidate.get("spread_cents")))
    if spread is not None and spread > 1:
        spread = spread / Decimal("100")
    signal_seen_count = int(_float(candidate.get("signal_seen_count")) or 0)
    required_signal_count = int(config.get("required_signal_seen_count") or 2)
    max_spread = _decimal(config.get("max_spread"))
    if max_spread is None:
        max_spread = (_decimal(config.get("max_spread_cents")) or Decimal("15")) / Decimal("100")
    max_entry_price = (_decimal(config.get("max_entry_price_cents")) or Decimal("80")) / Decimal("100")
    high_price_override_edge = _decimal(config.get("high_price_override_edge")) or Decimal("0.25")
    require_exit_bid = bool(config.get("require_exit_bid_for_entry", True))

    if not candidate.get("market_ticker") or str(candidate.get("side") or "").upper() not in {"YES", "NO"}:
        _add(hard_veto_flags, "malformed_candidate")
    if edge is None:
        _add(hard_veto_flags, "malformed_candidate")
    if ask is None:
        _add(hard_veto_flags, "ask_missing")
    if require_exit_bid and exit_bid is None:
        _add(hard_veto_flags, "missing_exit_bid")
        _add(risk_flags, "liquidity_risk")
    if bool(candidate.get("bracket_invalidated")):
        _add(hard_veto_flags, "bracket_invalidated")
    if _contradictory_observed_high(candidate, weather):
        _add(hard_veto_flags, "contradictory_observed_high")
    if bool(risk.get("cooldown_active")) or _float(model.get("recent_stop_loss_minutes_ago")) is not None:
        _add(risk_flags, "recent_stop_loss")
        if bool(risk.get("cooldown_active")):
            _add(hard_veto_flags, "cooldown_active")
    if bool(risk.get("daily_loss_limit_hit")):
        _add(hard_veto_flags, "daily_loss_limit_hit")
    if bool(risk.get("max_positions_hit")):
        _add(hard_veto_flags, "position_limit_hit")
    if bool(risk.get("max_exposure_hit")):
        _add(hard_veto_flags, "exposure_limit_hit")
    if bool(risk.get("live_trading_enabled")):
        _add(hard_veto_flags, "live_trading_path")

    market_age = _float(market.get("market_data_age_seconds"))
    weather_age = _float(weather.get("weather_data_age_seconds"))
    model_age = _float(model.get("model_data_age_seconds"))
    if market_age is not None and market_age > float(config.get("stale_market_seconds", 900)):
        _add(hard_veto_flags, "stale_market_data")
    if weather_age is not None and weather_age > float(config.get("stale_weather_seconds", 900)):
        _add(hard_veto_flags, "stale_weather_data")
    if model_age is not None and model_age > float(config.get("stale_model_seconds", 2700)):
        _add(hard_veto_flags, "stale_model_data")

    if spread is not None and spread > max_spread:
        _add(hard_veto_flags, "spread_too_wide")
    if ask is not None and ask > max_entry_price and (edge or Decimal("0")) < high_price_override_edge:
        _add(hard_veto_flags, "price_too_high")
    if ask is not None and ask <= Decimal("0.03") and not bool(config.get("allow_penny_contract_entries", False)):
        _add(hard_veto_flags, "penny_contract_blocked")
    if bool(candidate.get("liquidity_ok")) is False:
        _add(hard_veto_flags, "liquidity_too_low")

    component_scores["calibrated_edge_score"] = _edge_score(edge)
    component_scores["model_confidence_score"] = _probability_score(model_probability)
    component_scores["signal_persistence_score"] = _persistence_score(signal_seen_count, required_signal_count)
    component_scores["market_confirmation_score"] = _market_confirmation_score(candidate.get("market_confirmation"))
    component_scores["liquidity_score"] = _liquidity_score(ask, exit_bid, candidate)
    component_scores["time_of_day_score"] = _time_of_day_score(str(payload.get("decision_time_local") or ""))
    component_scores["spread_penalty"] = -_spread_penalty(spread, max_spread)
    component_scores["missing_bid_penalty"] = -25.0 if exit_bid is None else 0.0
    component_scores["stale_data_penalty"] = -_stale_penalty(hard_veto_flags)
    component_scores["recent_stop_penalty"] = -18.0 if "recent_stop_loss" in risk_flags else 0.0
    component_scores["overexposure_penalty"] = -20.0 if {"exposure_limit_hit", "position_limit_hit"} & set(hard_veto_flags) else 0.0
    component_scores["model_disagreement_penalty"] = -_model_disagreement_penalty(market)
    component_scores["weather_boundary_penalty"] = -12.0 if "bracket_invalidated" in hard_veto_flags else 0.0
    component_scores["price_too_high_penalty"] = -16.0 if "price_too_high" in hard_veto_flags else 0.0
    component_scores["no_exit_bid_penalty"] = -20.0 if "missing_exit_bid" in hard_veto_flags else 0.0

    raw_score = sum(component_scores.values())
    if hard_veto_flags:
        raw_score = min(raw_score, 39)
    score = max(0, min(100, round(raw_score)))
    if score < 40:
        quality = "poor"
    elif score < 60:
        quality = "weak"
    elif score < 75:
        quality = "acceptable"
    elif score < 90:
        quality = "strong"
    else:
        quality = "exceptional"
    explanation = f"{quality} trade quality score {score}"
    if hard_veto_flags:
        explanation += "; hard veto: " + ", ".join(hard_veto_flags)
    return TradeQualityResult(
        score=score,
        component_scores=component_scores,
        risk_flags=risk_flags,
        hard_veto_flags=hard_veto_flags,
        explanation=explanation,
    )


def _edge_score(edge: Decimal | None) -> float:
    if edge is None:
        return 0.0
    return float(max(Decimal("0"), min(Decimal("32"), edge * Decimal("120"))))


def _probability_score(probability: float | None) -> float:
    if probability is None:
        return 4.0
    distance = abs(probability - 0.5)
    return max(0.0, min(16.0, 4.0 + distance * 24.0))


def _persistence_score(seen_count: int, required: int) -> float:
    if required <= 0:
        return 15.0
    return max(0.0, min(15.0, 15.0 * (seen_count / required)))


def _market_confirmation_score(value: Any) -> float:
    normalized = str(value or "neutral").lower()
    if normalized == "positive":
        return 10.0
    if normalized == "negative":
        return -8.0
    return 4.0


def _liquidity_score(ask: Decimal | None, bid: Decimal | None, candidate: dict[str, Any]) -> float:
    if ask is None:
        return 0.0
    if bid is None:
        return 1.0
    if candidate.get("liquidity_ok") is False:
        return 2.0
    return 10.0


def _time_of_day_score(local_text: str) -> float:
    try:
        local_dt = datetime.fromisoformat(local_text)
    except ValueError:
        return 5.0
    hour = local_dt.hour + local_dt.minute / 60
    if hour < 10:
        return 3.0
    if hour < 14:
        return 8.0
    if hour < 17.5:
        return 6.0
    return 3.0


def _spread_penalty(spread: Decimal | None, max_spread: Decimal) -> float:
    if spread is None:
        return 5.0
    if spread <= max_spread / Decimal("2"):
        return 0.0
    if spread <= max_spread:
        return 5.0
    return 20.0


def _stale_penalty(flags: list[str]) -> float:
    return 15.0 * len([flag for flag in flags if flag.startswith("stale_")])


def _model_disagreement_penalty(market_context: dict[str, Any]) -> float:
    spread_f = _float(market_context.get("model_spread_f"))
    if spread_f is None:
        return 2.0
    if spread_f <= 1.0:
        return 0.0
    if spread_f <= 2.0:
        return 4.0
    if spread_f <= 4.0:
        return 9.0
    return 16.0


def _contradictory_observed_high(candidate: dict[str, Any], weather: dict[str, Any]) -> bool:
    observed = _float(weather.get("observed_high_so_far_f"))
    upper = _float(candidate.get("bracket_upper_f"))
    side = str(candidate.get("side") or "").upper()
    if observed is None or upper is None:
        return False
    return side == "YES" and observed > upper


def _add(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

