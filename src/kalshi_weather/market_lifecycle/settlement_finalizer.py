from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Bracket:
    label: str
    lower_f: float | None = None
    upper_f: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    def contains(self, high_f: float) -> bool:
        if self.lower_f is not None:
            if self.lower_inclusive and high_f < self.lower_f:
                return False
            if not self.lower_inclusive and high_f <= self.lower_f:
                return False
        if self.upper_f is not None:
            if self.upper_inclusive and high_f > self.upper_f:
                return False
            if not self.upper_inclusive and high_f >= self.upper_f:
                return False
        return True


@dataclass(frozen=True)
class Position:
    bracket: str
    side: str
    quantity: int
    avg_cost_cents: float


@dataclass(frozen=True)
class SettlementResult:
    winning_bracket: str
    settlement_value_dollars: float
    final_cash_dollars: float
    realized_pnl_dollars: float


def winning_bracket_for_high(high_f: float, brackets: Iterable[Bracket]) -> str:
    matches = [bracket.label for bracket in brackets if bracket.contains(high_f)]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one matching bracket for {high_f}, got {matches}")
    return matches[0]


def settle_positions(
    *,
    cash_before_settlement: float,
    starting_cash: float,
    winning_bracket: str,
    positions: Iterable[Position],
) -> SettlementResult:
    settlement_value = 0.0
    for position in positions:
        side = position.side.upper()
        if side not in {"YES", "NO"}:
            raise ValueError(f"unknown side: {position.side}")
        yes_wins = position.bracket == winning_bracket
        wins = yes_wins if side == "YES" else not yes_wins
        if wins:
            settlement_value += position.quantity
    final_cash = cash_before_settlement + settlement_value
    return SettlementResult(
        winning_bracket=winning_bracket,
        settlement_value_dollars=round(settlement_value, 2),
        final_cash_dollars=round(final_cash, 2),
        realized_pnl_dollars=round(final_cash - starting_cash, 2),
    )

