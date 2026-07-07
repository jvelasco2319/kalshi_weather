from __future__ import annotations

from decimal import Decimal

from kalshi_weather.schemas import OrderbookTop, TradeSignal


def decimal_from_probability(p: float) -> Decimal:
    return Decimal(str(round(p, 6)))


def terminal_edges(p_yes: float, top: OrderbookTop) -> tuple[Decimal | None, Decimal | None]:
    p = decimal_from_probability(p_yes)
    yes_edge = p - top.yes_ask if top.yes_ask is not None else None
    no_edge = (Decimal("1") - p) - top.no_ask if top.no_ask is not None else None
    return yes_edge, no_edge


def make_trade_signal(
    ticker: str,
    p_yes: float,
    top: OrderbookTop,
    quantity: Decimal,
    require_edge: Decimal,
    fee_buffer: Decimal,
    model_error_buffer: Decimal,
) -> TradeSignal | None:
    yes_edge, no_edge = terminal_edges(p_yes, top)
    hurdle = require_edge + fee_buffer + model_error_buffer

    if yes_edge is not None and top.yes_ask is not None and yes_edge > hurdle:
        return TradeSignal(
            ticker=ticker,
            side="yes",
            action="buy",
            quantity=quantity,
            price=top.yes_ask,
            edge=yes_edge,
            reason=f"YES terminal edge {yes_edge} > hurdle {hurdle}",
        )
    if no_edge is not None and top.no_ask is not None and no_edge > hurdle:
        return TradeSignal(
            ticker=ticker,
            side="no",
            action="buy",
            quantity=quantity,
            price=top.no_ask,
            edge=no_edge,
            reason=f"NO terminal edge {no_edge} > hurdle {hurdle}",
        )
    return None


def expected_exit_bid(current_bid: Decimal, current_ask: Decimal, q_next: Decimal, convergence: Decimal) -> Decimal:
    mid = (current_bid + current_ask) / Decimal("2")
    spread = current_ask - current_bid
    expected_mid = mid + convergence * (q_next - mid)
    return max(Decimal("0"), min(Decimal("1"), expected_mid - spread / Decimal("2")))
