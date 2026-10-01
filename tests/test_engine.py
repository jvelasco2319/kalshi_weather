from kalshi_swarm.engine import evaluate, fee_dollars, model_probabilities, pressure_state, score_record, select_no_trade
from kalshi_swarm.verify import verify_frozen


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


def test_frozen_artifact_hashes_and_identities():
    assert set(verify_frozen()) == {"V5B", "V8", "V10"}


def test_historical_override_and_unavailable_model_are_explicit():
    row = score_record({
        "date": "2025-07-01",
        "base_probabilities": [1, 1, 1, 1, 1, 1],
        "historical_model_probabilities": {"V8": [1, 0, 0, 0, 0, 0]},
        "unavailable_models": {"V10": "missing pressure history"},
        "outcome_index": 0,
    })
    assert row["models"]["V8"]["probabilities"] == [1, 0, 0, 0, 0, 0]
    assert row["models"]["V8"]["decision"]["brier"] == 0
    assert row["models"]["V10"]["probabilities"] is None
    assert row["models"]["V10"]["decision"]["status"] == "UNAVAILABLE"


def test_forecast_and_return_rankings_are_separate():
    result = evaluate([{
        "date": "2025-07-01",
        "base_probabilities": [0.95, 0.01, 0.01, 0.01, 0.01, 0.01],
        "outcome_index": 0,
        "unavailable_models": {"V10": "missing pressure history"},
    }])
    assert result["schema_version"].endswith("v2")
    assert result["summaries"]["V10"]["unavailable_dates"] == 1
    assert result["common_forecast_date_count"] == 0
    assert result["return_ranking"] == []

