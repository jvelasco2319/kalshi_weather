from kalshi_weather.edge_engine.hold_filters import apply_risk_filters
from kalshi_weather.edge_engine.types import (
    Action,
    CandidateTrade,
    OpenOrder,
    OrderType,
    PortfolioState,
    Position,
    RiskConfig,
    Side,
)


def _candidate(price=50, qty=10, side=Side.YES, label="70-71", edge=10):
    return CandidateTrade(
        candidate_id=f"c-{label}-{side.value}",
        action=Action.BUY,
        side=side,
        bracket_label=label,
        order_type=OrderType.TAKER,
        quantity=qty,
        price_cents=price,
        net_edge_cents=edge,
        spread_cents=1,
        upside_cents=100-price,
        max_loss_dollars=qty*price/100,
    )


def test_cash_cannot_go_negative():
    c = _candidate(price=90, qty=10)
    checked = apply_risk_filters(c, PortfolioState(cash_dollars=5), RiskConfig())
    assert checked.rejection_reason == "cash_limit"


def test_total_exposure_cap_enforced_cumulatively():
    p = PortfolioState(cash_dollars=1000, positions=(Position("68-69", Side.YES, 100, 50),))
    c = _candidate(price=50, qty=100, label="70-71")
    checked = apply_risk_filters(c, p, RiskConfig(max_total_exposure_dollars=80, max_risk_dollars_per_trade=100, max_contracts_per_trade=200))
    assert checked.rejection_reason == "exposure_limit"


def test_per_bracket_exposure_cap():
    p = PortfolioState(cash_dollars=1000, positions=(Position("70-71", Side.NO, 50, 50),))
    c = _candidate(price=50, qty=100, side=Side.YES, label="70-71")
    checked = apply_risk_filters(c, p, RiskConfig(max_exposure_dollars_per_bracket=60, max_risk_dollars_per_trade=100, max_contracts_per_trade=200))
    assert checked.rejection_reason == "bracket_exposure_limit"


def test_scale_in_blocked_by_default():
    p = PortfolioState(cash_dollars=1000, positions=(Position("70-71", Side.YES, 10, 50),))
    c = _candidate(side=Side.YES, label="70-71")
    checked = apply_risk_filters(c, p, RiskConfig())
    assert checked.rejection_reason == "scale_in_blocked"


def test_edge_threshold_uses_epsilon():
    c = _candidate(edge=7.9999)
    checked = apply_risk_filters(
        c,
        PortfolioState(cash_dollars=1000),
        RiskConfig(min_edge_cents=8, min_yes_edge_cents=8, edge_comparison_epsilon_cents=0.001),
    )
    assert checked.eligible is True


def test_open_orders_reserve_cash_and_exposure():
    p = PortfolioState(
        cash_dollars=100,
        open_orders=(OpenOrder("open1", "68-69", Side.YES, 100, 50),),
    )
    c = _candidate(price=60, qty=100, side=Side.NO, label="70-71", edge=12)
    checked = apply_risk_filters(
        c,
        p,
        RiskConfig(max_total_exposure_dollars=90, max_risk_dollars_per_trade=100, max_contracts_per_trade=200),
    )
    assert checked.rejection_reason == "exposure_limit"


def test_open_same_side_order_blocks_scale_in_by_default():
    p = PortfolioState(
        cash_dollars=100,
        open_orders=(OpenOrder("open1", "70-71", Side.YES, 10, 50),),
    )
    c = _candidate(price=50, qty=10, side=Side.YES, label="70-71", edge=12)
    checked = apply_risk_filters(c, p, RiskConfig())
    assert checked.rejection_reason == "scale_in_blocked"
