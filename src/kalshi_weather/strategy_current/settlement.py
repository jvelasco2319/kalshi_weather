from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from kalshi_weather.schemas import Bracket


@dataclass(frozen=True)
class SettlementBracket:
    bracket_id: str
    lower_f: int | None
    upper_f: int | None

    def contains_official_integer_f(self, value: int) -> bool:
        if self.lower_f is not None and value < self.lower_f:
            return False
        if self.upper_f is not None and value > self.upper_f:
            return False
        return True


def settlement_bracket_from_market_bracket(bracket: Bracket) -> SettlementBracket:
    return SettlementBracket(
        bracket_id=bracket.ticker,
        lower_f=bracket.lo_f,
        upper_f=bracket.hi_f,
    )


def official_integer_f(value_f: float | Decimal) -> int:
    return int(Decimal(str(value_f)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_settlement_brackets(brackets: Sequence[SettlementBracket]) -> None:
    if not brackets:
        raise ValueError("settlement brackets are required")
    ordered = sorted(brackets, key=lambda item: (-10_000 if item.lower_f is None else item.lower_f))
    if ordered[0].lower_f is not None:
        raise ValueError("settlement brackets must start with an unbounded lower interval")
    if ordered[-1].upper_f is not None:
        raise ValueError("settlement brackets must end with an unbounded upper interval")
    previous_upper: int | None = None
    for index, bracket in enumerate(ordered):
        if bracket.lower_f is not None and bracket.upper_f is not None and bracket.lower_f > bracket.upper_f:
            raise ValueError("settlement bracket lower bound exceeds upper bound")
        if index == 0:
            previous_upper = bracket.upper_f
            continue
        if previous_upper is None:
            raise ValueError("unbounded interval overlaps later settlement bracket")
        expected_lower = previous_upper + 1
        if bracket.lower_f != expected_lower:
            raise ValueError("settlement brackets have a gap or overlap")
        previous_upper = bracket.upper_f


def bracket_for_official_high(
    value_f: float | Decimal,
    brackets: Sequence[SettlementBracket],
) -> SettlementBracket:
    validate_settlement_brackets(brackets)
    official = official_integer_f(value_f)
    matches = [bracket for bracket in brackets if bracket.contains_official_integer_f(official)]
    if len(matches) != 1:
        raise ValueError("settlement brackets must produce exactly one match")
    return matches[0]
