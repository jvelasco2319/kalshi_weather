from kalshi_weather.cli import _blend_probabilities_for_context
from kalshi_weather.rules_engine_ext.probability_blend import blend_probability, choose_model_weight


def test_overnight_model_weight_conservative():
    w, reason = choose_model_weight("overnight_next_day")
    assert w == 0.35
    assert "overnight_next_day" in reason


def test_high_disagreement_reduces_model_weight():
    low, _ = choose_model_weight("active_nowcast", "low")
    high, _ = choose_model_weight("active_nowcast", "high")
    assert high < low


def test_final_trade_probability_is_blend():
    b = blend_probability(0.70, 0.40, "overnight_next_day")
    assert abs(b.final_trade_probability - (0.35 * 0.70 + 0.65 * 0.40)) < 1e-9
    assert b.model_weight == 0.35


def test_cli_probability_blend_uses_config_override():
    blended, debug = _blend_probabilities_for_context(
        {"70-71": 0.80},
        market_distribution={"probability_by_bracket": {"70-71": 0.20}},
        active_profile="active_nowcast",
        model_disagreement_level="high",
        probability_blend_mode="blend",
        probability_blend_config={
            "defaults": {"min_model_weight": 0.10, "max_model_weight": 0.90},
            "profiles": {"active_nowcast": {"model_weight": 0.80}},
            "overrides": {"model_disagreement_high": {"model_weight_add": -0.10}},
        },
    )

    assert abs(blended["70-71"] - 0.62) < 1e-9
    assert debug["config_loaded"] is True
    assert abs(debug["by_bracket"]["70-71"]["model_weight"] - 0.70) < 1e-9
