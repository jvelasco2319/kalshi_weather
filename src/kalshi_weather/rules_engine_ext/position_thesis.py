from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PositionState = Literal["conviction_hold", "take_profit_watch", "partial_take_profit", "weak_hold", "reduce", "close"]

BRACKET_ORDER = ["<66", "66-67", "68-69", "70-71", "72-73", ">73"]

@dataclass(frozen=True)
class EntryThesis:
    bracket: str
    side: str
    entry_price_cents: float
    entry_final_trade_probability: float
    entry_fair_value_cents: float
    entry_top_bracket: str
    entry_model_disagreement_level: str
    entry_full_model_spread_f: float | None
    active_profile: str

@dataclass(frozen=True)
class CurrentThesis:
    current_final_trade_probability: float
    current_fair_value_cents: float
    current_top_bracket: str
    current_model_disagreement_level: str
    current_full_model_spread_f: float | None
    observation_invalidated: bool = False
    current_mark_cents: float | None = None

@dataclass(frozen=True)
class PositionDecision:
    state: PositionState
    hold_reason: str | None = None
    close_reasons: tuple[str, ...] = ()
    take_profit_target_cents: float | None = None
    probability_decay: float = 0.0
    fair_value_decay_cents: float = 0.0
    market_moved_against_but_model_still_valid: bool = False
    take_profit_target_1_cents: float | None = None
    take_profit_target_2_cents: float | None = None
    take_profit_reached: bool = False
    take_profit_fraction: float = 0.0
    take_profit_reason: str | None = None
    realized_pnl_if_taken: float | None = None

LEVEL_RANK = {"low": 0, "medium": 1, "high": 2, "extreme": 3}

def _adjacent_or_same(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        return abs(BRACKET_ORDER.index(left) - BRACKET_ORDER.index(right)) <= 1
    except ValueError:
        return False

def evaluate_position(
    entry: EntryThesis,
    current: CurrentThesis,
    min_hold_edge_cents: float = 5.0,
    max_probability_decay: float = 0.15,
    take_profit_gain_cents: float = 12.0,
    take_profit_gap_fraction: float = 0.50,
    take_profit_within_fair_cents: float = 5.0,
    take_profit_fraction_to_close: float = 0.50,
) -> PositionDecision:
    reasons: list[str] = []
    probability_decay = entry.entry_final_trade_probability - current.current_final_trade_probability
    fair_decay = entry.entry_fair_value_cents - current.current_fair_value_cents

    if current.observation_invalidated:
        reasons.append("close_observation_invalidated")
    if entry.side.upper() == "YES" and not _adjacent_or_same(current.current_top_bracket, entry.bracket):
        reasons.append("close_model_top_changed")
    if probability_decay > max_probability_decay:
        reasons.append("close_probability_decayed")
    if current.current_fair_value_cents < entry.entry_price_cents + min_hold_edge_cents:
        reasons.append("close_fair_value_no_longer_supports_position")
    if LEVEL_RANK.get(current.current_model_disagreement_level, 0) > LEVEL_RANK.get(entry.entry_model_disagreement_level, 0) + 1:
        reasons.append("close_model_disagreement_worsened")

    if reasons:
        return PositionDecision("close", close_reasons=tuple(reasons), probability_decay=probability_decay, fair_value_decay_cents=fair_decay)

    gap_target = entry.entry_price_cents + max(0.0, current.current_fair_value_cents - entry.entry_price_cents) * take_profit_gap_fraction
    target_1 = min(entry.entry_price_cents + take_profit_gain_cents, gap_target) if gap_target > entry.entry_price_cents else entry.entry_price_cents + take_profit_gain_cents
    target_2 = max(entry.entry_price_cents, current.current_fair_value_cents - take_profit_within_fair_cents)
    if current.current_mark_cents is not None and current.current_mark_cents >= target_2:
        pnl = current.current_mark_cents - entry.entry_price_cents
        return PositionDecision(
            "partial_take_profit",
            hold_reason="take_profit_near_fair_value",
            take_profit_target_cents=target_2,
            probability_decay=probability_decay,
            fair_value_decay_cents=fair_decay,
            take_profit_target_1_cents=target_1,
            take_profit_target_2_cents=target_2,
            take_profit_reached=True,
            take_profit_fraction=take_profit_fraction_to_close,
            take_profit_reason="market within 5c of current fair value",
            realized_pnl_if_taken=pnl,
        )
    if current.current_mark_cents is not None and current.current_mark_cents >= target_1:
        pnl = current.current_mark_cents - entry.entry_price_cents
        return PositionDecision(
            "take_profit_watch",
            hold_reason="take_profit_target_reached",
            take_profit_target_cents=target_1,
            probability_decay=probability_decay,
            fair_value_decay_cents=fair_decay,
            take_profit_target_1_cents=target_1,
            take_profit_target_2_cents=target_2,
            take_profit_reached=True,
            take_profit_fraction=take_profit_fraction_to_close,
            take_profit_reason="market reached first take-profit target",
            realized_pnl_if_taken=pnl,
        )

    if current.current_fair_value_cents >= entry.entry_price_cents + min_hold_edge_cents:
        markdown = bool(current.current_mark_cents is not None and current.current_mark_cents < entry.entry_price_cents)
        return PositionDecision(
            "conviction_hold",
            hold_reason="model_thesis_still_valid",
            probability_decay=probability_decay,
            fair_value_decay_cents=fair_decay,
            market_moved_against_but_model_still_valid=markdown,
            take_profit_target_1_cents=target_1,
            take_profit_target_2_cents=target_2,
        )

    return PositionDecision("weak_hold", hold_reason="edge_thin_but_not_invalidated", probability_decay=probability_decay, fair_value_decay_cents=fair_decay)
