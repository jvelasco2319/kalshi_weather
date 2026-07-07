from kalshi_weather.edge_engine.no_exclusion import evaluate_no_basket, generate_no_exclusion_candidates
from kalshi_weather.edge_engine.types import CostConfig, MarketQuote, OrderType, PortfolioState, RiskConfig


def test_rejects_99c_no_with_tiny_upside():
    candidates = generate_no_exclusion_candidates(
        series="KXHIGHLAX",
        target_date="20260626",
        probabilities={"<66": 0.003},
        quotes=[MarketQuote("<66", yes_bid_cents=0, no_bid_cents=99, no_ask_cents=99)],
        brackets=None,
        cost_config=CostConfig(include_fees=False, slippage_cents=0, tail_risk_padding_cents=0),
        risk_config=RiskConfig(min_no_edge_cents=0, min_no_upside_cents=8, max_no_bin_probability=0.20, max_spread_cents=100),
        portfolio=PortfolioState(cash_dollars=1000),
        order_type=OrderType.TAKER,
    )
    assert candidates == []


def test_accepts_mispriced_64c_no():
    candidates = generate_no_exclusion_candidates(
        series="KXHIGHLAX",
        target_date="20260626",
        probabilities={"72-73": 0.127},
        quotes=[MarketQuote("72-73", yes_bid_cents=36, no_bid_cents=63)],
        brackets=None,
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_no_edge_cents=8, min_no_upside_cents=8, max_no_bin_probability=0.20, max_spread_cents=5),
        portfolio=PortfolioState(cash_dollars=1000),
        order_type=OrderType.TAKER,
    )
    assert len(candidates) == 1
    assert candidates[0].side.value == "NO"
    assert candidates[0].eligible


def test_no_basket_ev_for_mutually_exclusive_brackets():
    basket = evaluate_no_basket(
        labels=["<66", "> 73"],
        probabilities={"<66": 0.003, "> 73": 0.004},
        no_ask_cents_by_label={"<66": 90, "> 73": 90},
        min_expected_edge_cents=1,
        max_probability_of_loss=0.02,
        min_upside_cents=5,
    )
    assert basket.probability_of_loss == 0.007
    assert basket.gain_if_no_selected_bracket_wins_cents == 20
    assert basket.eligible


def test_no_basket_worst_case_loss_is_never_negative():
    basket = evaluate_no_basket(
        labels=["<66", "> 73"],
        probabilities={"<66": 0.01, "> 73": 0.01},
        no_ask_cents_by_label={"<66": 20, "> 73": 20},
        min_expected_edge_cents=1,
        max_probability_of_loss=0.05,
        min_upside_cents=1,
    )
    assert basket.worst_case_loss_cents == 0
