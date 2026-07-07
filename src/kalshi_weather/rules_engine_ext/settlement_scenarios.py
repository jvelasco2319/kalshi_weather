from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class Position:
    bracket: str
    side: str
    quantity: int
    avg_cost_cents: float

@dataclass(frozen=True)
class ScenarioResult:
    scenario_label: str
    settlement_value_dollars: float
    final_equity_dollars: float
    pnl_vs_starting_cash: float
    pnl_vs_current_equity: float

@dataclass(frozen=True)
class ScenarioReport:
    scenarios: list[ScenarioResult]
    best_case_scenario: str
    worst_case_scenario: str
    best_case_gain_dollars: float
    worst_case_loss_dollars: float
    downside_concentration_score: float


def position_settlement_value(pos: Position, winning_bracket: str) -> float:
    side = pos.side.upper()
    yes_wins = pos.bracket == winning_bracket
    contract_value_cents = 100.0 if ((side == "YES" and yes_wins) or (side == "NO" and not yes_wins)) else 0.0
    return pos.quantity * contract_value_cents / 100.0


def settlement_report(
    brackets: Iterable[str],
    positions: Iterable[Position],
    cash_dollars: float,
    starting_cash_dollars: float,
    current_equity_dollars: float,
) -> ScenarioReport:
    positions = list(positions)
    rows: list[ScenarioResult] = []
    for label in brackets:
        settle_val = sum(position_settlement_value(p, label) for p in positions)
        final_equity = cash_dollars + settle_val
        rows.append(ScenarioResult(label, settle_val, final_equity, final_equity - starting_cash_dollars, final_equity - current_equity_dollars))
    if not rows:
        return ScenarioReport([], "", "", 0.0, 0.0, 0.0)
    best = max(rows, key=lambda r: r.pnl_vs_starting_cash)
    worst = min(rows, key=lambda r: r.pnl_vs_starting_cash)
    span = best.pnl_vs_starting_cash - worst.pnl_vs_starting_cash
    concentration = 0.0 if span <= 0 else abs(worst.pnl_vs_starting_cash) / max(1.0, span)
    return ScenarioReport(rows, best.scenario_label, worst.scenario_label, best.pnl_vs_starting_cash, worst.pnl_vs_starting_cash, concentration)
