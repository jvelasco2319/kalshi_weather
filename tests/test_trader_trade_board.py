from kalshi_weather.trader_agent.context_builder import build_context_from_inputs
from kalshi_weather.trader_agent.trader_types import MarketBracket, ProbabilityBin, RiskLimits


def sample_context(min_edge_cents: float = 3.0):
    probs = [
        ProbabilityBin("65 or below", 0.003, None, 65),
        ProbabilityBin("66-67", 0.018, 66, 67),
        ProbabilityBin("68-69", 0.135, 68, 69),
        ProbabilityBin("70-71", 0.540, 70, 71),
        ProbabilityBin("72-73", 0.275, 72, 73),
        ProbabilityBin("74+", 0.029, 74, None),
    ]
    brackets = [
        MarketBracket("KXHIGHLAX", "T65", "65 or below", yes_bid_cents=0, yes_ask_cents=1, volume=10),
        MarketBracket("KXHIGHLAX", "T66-T67", "66-67", yes_bid_cents=1, yes_ask_cents=3, volume=25),
        MarketBracket("KXHIGHLAX", "T68-T69", "68-69", yes_bid_cents=7, yes_ask_cents=8, volume=300),
        MarketBracket("KXHIGHLAX", "T70-T71", "70-71", yes_bid_cents=58, yes_ask_cents=59, volume=1200),
        MarketBracket("KXHIGHLAX", "T72-T73", "72-73", yes_bid_cents=34, yes_ask_cents=35, volume=800),
        MarketBracket("KXHIGHLAX", "T74", "74+", yes_bid_cents=3, yes_ask_cents=4, volume=150),
    ]
    return build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-26",
        probability_bins=probs,
        market_brackets=brackets,
        risk_limits=RiskLimits(min_edge_cents=min_edge_cents, max_risk_dollars_per_trade=50),
    )


def test_trade_board_includes_yes_and_no_for_every_bracket():
    context = sample_context()
    buy_candidates = [c for c in context.candidate_trades if c.action == "BUY"]
    assert len([c for c in buy_candidates if c.side == "YES"]) == 6
    assert len([c for c in buy_candidates if c.side == "NO"]) == 6
    assert any(c.candidate_id == "HOLD" for c in context.candidate_trades)


def test_trade_board_can_make_no_on_overpriced_bracket_eligible():
    context = sample_context()
    no_72 = next(c for c in context.candidate_trades if c.contract_ticker == "T72-T73" and c.side == "NO")
    assert no_72.model_fair_cents == 72.5
    assert no_72.entry_price_cents == 66
    assert no_72.fee_adjusted_edge_cents > 3.0
    assert no_72.eligible is True
