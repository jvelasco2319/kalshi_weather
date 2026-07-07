from kalshi_weather.edge_engine.market_implied import normalize_market_probabilities, normalize_quote
from kalshi_weather.edge_engine.types import MarketQuote


def test_implied_yes_ask_from_no_bid():
    q = normalize_quote(MarketQuote(bracket_label="70-71°", yes_bid_cents=58, no_bid_cents=41))
    assert q.bracket_label == "70-71"
    assert q.yes_ask_cents == 59
    assert q.no_ask_cents == 42


def test_market_probabilities_normalize():
    probs = normalize_market_probabilities([
        MarketQuote("68-69", yes_bid_cents=3, no_bid_cents=96),
        MarketQuote("70-71", yes_bid_cents=58, no_bid_cents=41),
        MarketQuote("72-73", yes_bid_cents=36, no_bid_cents=63),
    ])
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert probs["70-71"] > probs["68-69"]
