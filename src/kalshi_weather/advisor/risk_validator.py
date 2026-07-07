from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorDecision, AdvisorInput, ENTRY_DECISIONS
from kalshi_weather.advisor.trade_quality import score_trade_quality


@dataclass(frozen=True)
class ValidatedDecision:
    approved: bool
    final_action: str
    advisor_decision: AdvisorDecision
    veto_reasons: list[str] = field(default_factory=list)
    adjusted_size_multiplier: float = 0.0
    final_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "final_action": self.final_action,
            "advisor_decision": self.advisor_decision.to_dict(),
            "veto_reasons": self.veto_reasons,
            "adjusted_size_multiplier": self.adjusted_size_multiplier,
            "final_reason": self.final_reason,
        }


def validate_advisor_trade(
    advisor_input: AdvisorInput | dict[str, Any],
    advisor_decision: AdvisorDecision,
) -> ValidatedDecision:
    payload = advisor_input.to_dict() if isinstance(advisor_input, AdvisorInput) else advisor_input
    candidate = dict(payload.get("candidate_trade") or {})
    position = dict(payload.get("position_state") or {})
    risk = dict(payload.get("risk_state") or {})
    config = dict(payload.get("configuration") or {})
    quality = score_trade_quality(payload)
    vetoes: list[str] = []
    for flag in advisor_decision.hard_veto_flags:
        _add(vetoes, flag)
    if bool(risk.get("live_trading_enabled")) or bool(config.get("live_trading_enabled")):
        _add(vetoes, "live_trading_path")
    if advisor_decision.requires_validator_approval is not True:
        _add(vetoes, "advisor_did_not_require_validator")

    min_buy_score = int(config.get("advisor_min_score") or config.get("min_score_for_buy") or 75)
    llm_first = bool(config.get("llm_first"))
    if advisor_decision.decision in ENTRY_DECISIONS:
        for flag in quality.hard_veto_flags:
            _add(vetoes, flag)
        _validate_entry(candidate, risk, config, vetoes)
        if advisor_decision.trade_quality_score < min_buy_score or (quality.score < min_buy_score and not llm_first):
            _add(vetoes, "trade_quality_below_buy_threshold")
    elif advisor_decision.decision == "SELL":
        _validate_sell(position, vetoes)
    elif advisor_decision.decision == "LONG_HOLD_CANDIDATE" and str(payload.get("strategy_mode")) not in {"long_hold", "hybrid"}:
        _add(vetoes, "long_hold_not_enabled")

    if vetoes:
        return ValidatedDecision(
            approved=False,
            final_action="BLOCK",
            advisor_decision=advisor_decision,
            veto_reasons=vetoes,
            adjusted_size_multiplier=0.0,
            final_reason="Validator veto: " + ", ".join(vetoes),
        )

    final_action = advisor_decision.decision
    adjusted = max(0.0, min(1.0, advisor_decision.recommended_size_multiplier))
    if final_action in {"WAIT", "HOLD", "BLOCK", "REDUCE_SIZE", "LONG_HOLD_CANDIDATE"}:
        adjusted = 0.0
    return ValidatedDecision(
        approved=True,
        final_action=final_action,
        advisor_decision=advisor_decision,
        veto_reasons=[],
        adjusted_size_multiplier=adjusted,
        final_reason="Validator approved" if final_action in ENTRY_DECISIONS | {"SELL"} else "No trade approved",
    )


def _validate_entry(
    candidate: dict[str, Any],
    risk: dict[str, Any],
    config: dict[str, Any],
    vetoes: list[str],
) -> None:
    ask = _decimal(candidate.get("entry_ask", candidate.get("ask")))
    bid = _decimal(candidate.get("exit_bid", candidate.get("bid")))
    spread = _decimal(candidate.get("spread", candidate.get("spread_cents")))
    if spread is not None and spread > 1:
        spread = spread / Decimal("100")
    max_spread = (_decimal(config.get("max_spread_cents")) or Decimal("15")) / Decimal("100")
    max_price = (_decimal(config.get("max_entry_price_cents")) or Decimal("80")) / Decimal("100")
    edge = _decimal(candidate.get("edge"))
    high_price_override_edge = _decimal(config.get("high_price_override_edge")) or Decimal("0.25")
    if ask is None:
        _add(vetoes, "ask_missing")
    if bool(config.get("require_exit_bid_for_entry", True)) and bid is None:
        _add(vetoes, "missing_exit_bid")
    if spread is not None and spread > max_spread:
        _add(vetoes, "spread_too_wide")
    if ask is not None and ask <= Decimal("0.03") and not bool(config.get("allow_penny_contract_entries", False)):
        _add(vetoes, "penny_contract_blocked")
    if ask is not None and ask > max_price and (edge or Decimal("0")) < high_price_override_edge:
        _add(vetoes, "price_too_high")
    if bool(candidate.get("bracket_invalidated")):
        _add(vetoes, "bracket_invalidated")
    if bool(risk.get("cooldown_active")):
        _add(vetoes, "cooldown_active")
    if bool(risk.get("daily_loss_limit_hit")):
        _add(vetoes, "daily_loss_limit_hit")
    if bool(risk.get("max_positions_hit")):
        _add(vetoes, "position_limit_hit")
    if bool(risk.get("max_exposure_hit")):
        _add(vetoes, "exposure_limit_hit")
    if bool(risk.get("open_position_missing_bid")):
        _add(vetoes, "open_position_missing_bid")


def _validate_sell(position: dict[str, Any], vetoes: list[str]) -> None:
    if not bool(position.get("has_open_position")):
        _add(vetoes, "no_open_position")
    if _decimal(position.get("current_exit_bid")) is None:
        _add(vetoes, "missing_exit_bid")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _add(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)
