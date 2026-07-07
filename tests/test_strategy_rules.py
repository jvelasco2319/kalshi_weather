from kalshi_weather.edge_engine.strategy_rules import choose_best_candidate
from kalshi_weather.edge_engine.types import Action, CandidateTrade, OrderType, PortfolioState, RiskConfig, Side


def test_choose_best_candidate_by_net_edge():
    c1 = CandidateTrade("c1", Action.BUY, Side.YES, "70-71", OrderType.TAKER, quantity=1, price_cents=50, net_edge_cents=9, spread_cents=1, upside_cents=50, max_loss_dollars=0.5)
    c2 = CandidateTrade("c2", Action.BUY, Side.NO, "72-73", OrderType.TAKER, quantity=1, price_cents=64, net_edge_cents=20, spread_cents=1, upside_cents=36, max_loss_dollars=0.64)
    d = choose_best_candidate([c1, c2], portfolio=PortfolioState(cash_dollars=10), risk_config=RiskConfig())
    assert d.candidate.candidate_id == "c2"


def test_hold_when_no_valid_candidate():
    c1 = CandidateTrade("c1", Action.BUY, Side.YES, "70-71", OrderType.TAKER, quantity=1, price_cents=50, net_edge_cents=1, spread_cents=1, upside_cents=50, max_loss_dollars=0.5)
    d = choose_best_candidate([c1], portfolio=PortfolioState(cash_dollars=10), risk_config=RiskConfig(min_yes_edge_cents=8))
    assert d.action == Action.HOLD
    assert d.reason == "edge_below_threshold"
