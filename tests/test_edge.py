from kalshi_weather.edge_engine.edge import compute_candidate_edge
from kalshi_weather.edge_engine.types import CostConfig, MarketQuote, OrderType, Side


def test_no_candidate_edge_example():
    c = compute_candidate_edge(
        series="KXHIGHLAX",
        target_date="20260626",
        quote=MarketQuote("72-73", yes_bid_cents=36, no_bid_cents=63),
        p_yes=0.127,
        side=Side.NO,
        quantity=100,
        order_type=OrderType.TAKER,
        cost_config=CostConfig(include_fees=True, slippage_cents=0.5, tail_risk_padding_cents=2.0),
    )
    assert c.price_cents == 64
    assert round(c.fair_value_cents, 1) == 87.3
    assert c.raw_edge_cents > 20
    assert c.net_edge_cents < c.raw_edge_cents
