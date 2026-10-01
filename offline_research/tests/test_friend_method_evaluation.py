import math
from pathlib import Path

import pytest

from friend_method.evaluation import bracket_probabilities, fit_location, fit_variance
from friend_method.fixture_validation import validate_fixture


def _feature(h, g):
    return {"hrrr_temperature_f_max": h, "gefs_mean_temperature_f_max": g}


def test_fit_location_constrains_weights_and_corrects_bias():
    rows = [_feature(70 + i + 2, 70 + i - 1) for i in range(8)]
    outcomes = [70 + i for i in range(8)]
    value, metadata = fit_location(rows, outcomes, _feature(82, 79))
    assert 0 <= metadata["weight_hrrr"] <= 1
    assert metadata["weight_hrrr"] + metadata["weight_gefs"] == pytest.approx(1)
    assert value == pytest.approx(80)


def test_bracket_probabilities_preserve_tail_mass():
    rows = [
        {"market_ticker": "L", "strike_type": "less", "floor_strike": None, "cap_strike": 79},
        {"market_ticker": "M", "strike_type": "between", "floor_strike": 79, "cap_strike": 80},
        {"market_ticker": "H", "strike_type": "greater", "floor_strike": 80, "cap_strike": None},
    ]
    values = bracket_probabilities(80, 2, rows)
    assert sum(values.values()) == pytest.approx(1)
    assert all(value > 0 for value in values.values())


def test_fit_variance_never_drops_below_floor_and_slope_is_nonnegative():
    records = [{"error_f": (-1) ** i * (1 + i / 10), "spread_variance_f2": i / 10} for i in range(10)]
    sigma, metadata = fit_variance(records, 0.25, 1.0)
    assert sigma >= 1
    assert metadata["spread_variance_coefficient"] >= 0


def test_invalid_partition_rejected():
    rows = [
        {"market_ticker": "L", "strike_type": "less", "floor_strike": None, "cap_strike": 79},
        {"market_ticker": "H", "strike_type": "greater", "floor_strike": 80, "cap_strike": None},
    ]
    with pytest.raises(ValueError, match="exhaustive"):
        bracket_probabilities(80, 2, rows)


def test_supplied_fixture_groups_duplicate_gfs_feeds():
    root = Path(__file__).resolve().parents[1]
    result = validate_fixture(root / "data/reference/friend_method/Los_Angeles_CA_2026-07-03.json")
    assert result["conformance_passed"] is True
    assert result["effective_source_count"] == 3
    assert ["gfs", "gfs_seamless"] in result["effective_source_groups"]
    assert {name: row["sample_count"] for name, row in result["model_reports"].items()} == {
        "gfs": 3, "gfs_seamless": 3, "nam": 8, "nbm": 24,
    }
    assert result["profitability_evidence"] is False
