from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

from kalshi_weather.strategy_current.economics import ONE, ZERO, dec

Side = Literal["yes", "no"]


@dataclass(frozen=True)
class SpreadPolicy:
    hurdle_add: Decimal
    size_multiplier: Decimal
    hard_stop: bool
    reason: str | None = None


@dataclass(frozen=True)
class EventPosition:
    bracket_index: int
    side: Side
    count: Decimal
    all_in_cost: Decimal


def spread_policy(
    spread_f: float,
    *,
    elevated_spread_below_f: float = 3.0,
    hard_stop_spread_at_f: float = 4.0,
) -> SpreadPolicy:
    if spread_f >= hard_stop_spread_at_f:
        return SpreadPolicy(Decimal("0.03"), Decimal("0"), True, "NO_TRADE_SPREAD_HARD_STOP")
    if spread_f >= elevated_spread_below_f:
        return SpreadPolicy(Decimal("0.03"), Decimal("0.50"), False, "SPREAD_ELEVATED")
    return SpreadPolicy(Decimal("0"), Decimal("1.00"), False)


def drift_flag(recent_median_f: float, long_median_f: float) -> bool:
    absolute = abs(float(recent_median_f) - float(long_median_f)) >= 1.5
    reversal = (
        recent_median_f * long_median_f < 0
        and abs(recent_median_f) >= 1.0
        and abs(long_median_f) >= 1.0
    )
    return bool(absolute or reversal)


def full_kelly_fraction(
    probability: Decimal | float | str,
    all_in_cost_per_contract: Decimal | float | str,
) -> Decimal:
    q = dec(probability)
    k = dec(all_in_cost_per_contract)
    if not ZERO <= q <= ONE or not ZERO < k < ONE:
        raise ValueError("invalid probability or all-in cost")
    return max(ZERO, (q - k) / (ONE - k))


def used_kelly_fraction(
    probability: Decimal | float | str,
    all_in_cost_per_contract: Decimal | float | str,
    *,
    kelly_multiplier: Decimal = Decimal("0.25"),
    risk_multiplier: Decimal = Decimal("1"),
) -> Decimal:
    return full_kelly_fraction(probability, all_in_cost_per_contract) * kelly_multiplier * risk_multiplier


def event_outcome_pnl(
    *,
    bracket_count: int,
    positions: Sequence[EventPosition],
) -> tuple[Decimal, ...]:
    outcomes = [ZERO for _ in range(bracket_count)]
    for position in positions:
        if not 0 <= position.bracket_index < bracket_count:
            raise ValueError("position bracket index is out of range")
        for outcome_index in range(bracket_count):
            wins = outcome_index == position.bracket_index
            if position.side == "yes":
                payoff = position.count if wins else ZERO
            elif position.side == "no":
                payoff = position.count if not wins else ZERO
            else:
                raise ValueError("side must be yes or no")
            outcomes[outcome_index] += payoff - position.all_in_cost
    return tuple(outcomes)


def breaches_event_loss_cap(outcomes: Sequence[Decimal], max_loss: Decimal) -> bool:
    return any(value < -max_loss for value in outcomes)
