from kalshi_weather.edge_engine.attribution import classify_trade_outcome
from kalshi_weather.edge_engine.types import Action, CandidateTrade, OrderType, Side


def test_attribution_model_error():
    c = CandidateTrade("c", Action.BUY, Side.YES, "70-71", OrderType.TAKER, price_cents=55, raw_edge_cents=10, net_edge_cents=8)
    out = classify_trade_outcome(candidate=c, settled_result=0, net_pnl_cents=-55, clv_final_cents=-5)
    assert out.category == "model_error"


def test_attribution_costs_ate_edge():
    c = CandidateTrade("c", Action.BUY, Side.YES, "70-71", OrderType.TAKER, price_cents=55, raw_edge_cents=2, net_edge_cents=-1)
    out = classify_trade_outcome(candidate=c, settled_result=1, net_pnl_cents=-1, clv_final_cents=-1)
    assert out.category == "costs_ate_edge"


def test_attribution_hold_reason():
    out = classify_trade_outcome(candidate=None, settled_result=None, net_pnl_cents=None, clv_final_cents=None, hold_or_rejection_reason="edge_below_threshold")
    assert out.category == "hold"
    assert out.detail == "edge_below_threshold"
