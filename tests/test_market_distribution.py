from kalshi_weather.rules_engine_ext.market_distribution import MarketQuote, mid_probability, normalize_market_distribution


def test_yes_ask_implied_from_no_bid():
    q = MarketQuote(label="69-70", yes_bid=32, yes_ask=None, no_bid=67)
    assert mid_probability(q) == ((32 + 33) / 2) / 100


def test_market_distribution_normalizes():
    dist = normalize_market_distribution([
        MarketQuote("A", 10, 12),
        MarketQuote("B", 40, 42),
        MarketQuote("C", 50, 52),
    ])
    assert abs(dist.normalized_sum - 1.0) < 1e-9
    assert len(dist.probabilities) == 3
