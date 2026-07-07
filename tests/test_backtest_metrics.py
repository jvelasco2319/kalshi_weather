from decimal import Decimal

from kalshi_weather.backtest.metrics import gross_pnl, max_drawdown


def test_gross_pnl() -> None:
    assert gross_pnl(Decimal("0.40"), Decimal("0.60"), Decimal("10")) == Decimal("2.00")


def test_max_drawdown() -> None:
    dd = max_drawdown([Decimal("100"), Decimal("110"), Decimal("105"), Decimal("115"), Decimal("90")])
    assert dd == Decimal("-25")
