from kalshi_weather.edge_engine.clv import compute_clv, compute_clv_series
from kalshi_weather.edge_engine.calibration import brier_score, reliability_bins


def test_clv_positive_when_side_price_improves():
    s = compute_clv(55, 62)
    assert s.clv_cents == 7


def test_clv_series():
    series = compute_clv_series(55, {"5m": 57, "15m": 53})
    assert series["clv_5m_cents"] == 2
    assert series["clv_15m_cents"] == -2


def test_brier_score():
    assert round(brier_score([0.8, 0.2], [1, 0]), 2) == 0.04


def test_reliability_bins():
    bins = reliability_bins([0.05, 0.15, 0.85, 0.95], [0, 0, 1, 1], bin_width=0.5)
    assert bins[0].count == 2
    assert bins[1].count == 2
