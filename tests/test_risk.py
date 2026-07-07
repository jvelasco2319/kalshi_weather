from decimal import Decimal

from kalshi_weather.trading.risk import RiskLimits, check_buy_allowed, check_sell_allowed


def test_buy_risk_cash() -> None:
    ok, reason = check_buy_allowed(Decimal("1"), Decimal("0"), Decimal("10"), Decimal("0.20"), RiskLimits(Decimal("20"), Decimal("10")))
    assert not ok
    assert "cash" in reason


def test_buy_risk_position() -> None:
    ok, reason = check_buy_allowed(Decimal("100"), Decimal("9"), Decimal("2"), Decimal("0.20"), RiskLimits(Decimal("10"), Decimal("10")))
    assert not ok
    assert "position" in reason


def test_sell_risk() -> None:
    ok, reason = check_sell_allowed(Decimal("1"), Decimal("2"))
    assert not ok
    assert "cannot sell" in reason
