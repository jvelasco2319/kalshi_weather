from kalshi_swarm.engine import fee_dollars, model_probabilities, pressure_state, select_no_trade


def test_three_models_are_valid_and_distinct():
    vectors = model_probabilities([0.03, 0.09, 0.20, 0.39, 0.21, 0.08], 2.6)
    assert list(vectors) == ["V5B", "V8", "V10"]
    assert all(abs(sum(values) - 1.0) < 1e-12 for values in vectors.values())
    assert vectors["V5B"] != vectors["V8"] != vectors["V10"]


def test_pressure_boundaries_and_fee_rounding():
    assert pressure_state(-2) == "offshore"
    assert pressure_state(2) == "onshore"
    assert pressure_state(None) == "neutral"
    assert fee_dollars(50) == 0.02


def test_selector_abstains_when_forecast_has_no_clear_mode():
    result = select_no_trade([1 / 6] * 6, [])
    assert result == {"status": "ABSTAIN", "reason": "probability_gap_below_0.10"}

