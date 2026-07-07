from __future__ import annotations

from decimal import Decimal


def gross_pnl(entry_price: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    return (exit_price - entry_price) * quantity


def max_drawdown(equity_curve: list[Decimal]) -> Decimal:
    if not equity_curve:
        return Decimal("0")
    peak = equity_curve[0]
    worst = Decimal("0")
    for value in equity_curve:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst
