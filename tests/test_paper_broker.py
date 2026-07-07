from decimal import Decimal

from kalshi_weather.trading.paper_broker import PaperBroker
from kalshi_weather.trading.risk import RiskLimits


def test_buy_and_sell_yes() -> None:
    broker = PaperBroker(cash=Decimal("100.00"), limits=RiskLimits(Decimal("10"), Decimal("50")))
    fill = broker.buy("T", "yes", Decimal("2"), Decimal("0.40"), reason="test")
    assert fill is not None
    assert broker.cash == Decimal("99.20")
    assert broker.position("T", "yes") == Decimal("2")

    sell = broker.sell("T", "yes", Decimal("1"), Decimal("0.60"), reason="exit")
    assert sell is not None
    assert broker.cash == Decimal("99.80")
    assert broker.position("T", "yes") == Decimal("1")
    assert broker.realized_pnl == Decimal("0.20")


def test_cannot_buy_over_limit() -> None:
    broker = PaperBroker(cash=Decimal("100.00"), limits=RiskLimits(Decimal("10"), Decimal("1.00")))
    assert broker.buy("T", "yes", Decimal("10"), Decimal("0.50")) is None
