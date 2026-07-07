from decimal import Decimal

from kalshi_weather.schemas import OrderbookTop
from kalshi_weather.trading.signals import make_trade_signal, terminal_edges


def test_terminal_edges_use_executable_asks() -> None:
    top = OrderbookTop(
        ticker="T",
        yes_bid=Decimal("0.40"),
        no_bid=Decimal("0.55"),
        yes_ask=Decimal("0.45"),
        no_ask=Decimal("0.60"),
    )

    yes_edge, no_edge = terminal_edges(0.7, top)

    assert yes_edge == Decimal("0.25")
    assert no_edge == Decimal("-0.30")


def test_make_trade_signal_requires_hurdle() -> None:
    top = OrderbookTop("T", Decimal("0.40"), Decimal("0.55"), Decimal("0.45"), Decimal("0.60"))

    signal = make_trade_signal(
        ticker="T",
        p_yes=0.7,
        top=top,
        quantity=Decimal("1"),
        require_edge=Decimal("0.05"),
        fee_buffer=Decimal("0.01"),
        model_error_buffer=Decimal("0.03"),
    )

    assert signal is not None
    assert signal.side == "yes"
    assert signal.price == Decimal("0.45")
