import pytest

from kalshi_weather.edge_engine.portfolio_math import apply_fake_buy_fill
from kalshi_weather.edge_engine.types import Action, CandidateTrade, OrderType, PortfolioState, Side


def candidate(label="70-71", side=Side.YES, qty=10, price=50):
    return CandidateTrade(
        candidate_id=f"{label}-{side.value}",
        action=Action.BUY,
        side=side,
        bracket_label=label,
        order_type=OrderType.TAKER,
        quantity=qty,
        price_cents=price,
    )


def test_apply_fake_buy_fill_reduces_cash_and_adds_position():
    p = apply_fake_buy_fill(PortfolioState(cash_dollars=100), candidate())
    assert p.cash_dollars == 95
    assert len(p.positions) == 1
    assert p.positions[0].contracts == 10


def test_apply_fake_buy_fill_refuses_negative_cash():
    with pytest.raises(ValueError):
        apply_fake_buy_fill(PortfolioState(cash_dollars=1), candidate(qty=10, price=50))
