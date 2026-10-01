from pathlib import Path

import numpy as np

from friend_method.exact_evaluation import _date_seed, fit_location, load_exact_features, simplex_ridge_weights


def test_simplex_ridge_weights_are_nonnegative_and_sum_to_one():
    matrix = np.asarray([[1.0, 2.0, 4.0], [2.0, 3.0, 5.0], [3.0, 4.0, 6.0], [4.0, 5.0, 7.0]])
    outcomes = np.asarray([1.2, 2.2, 3.2, 4.2])
    weights = simplex_ridge_weights(matrix, outcomes, 1.0)
    assert np.all(weights >= 0)
    assert np.isclose(weights.sum(), 1.0)


def test_fit_location_uses_three_effective_sources():
    rows = [
        {"gfs": 70.0 + i, "nam": 69.0 + i, "nbm": 68.0 + i}
        for i in range(6)
    ]
    outcomes = [69.0 + i for i in range(6)]
    location, metadata = fit_location(rows, outcomes, {"gfs": 76.0, "nam": 75.0, "nbm": 74.0}, 1.0)
    assert 73.5 < location < 76.5
    assert set(metadata["weights"]) == {"gfs", "nam", "nbm"}
    assert np.isclose(sum(metadata["weights"].values()), 1.0)
    assert metadata["gfs_seamless_duplicate_of"] == "gfs"


def test_exact_windows_load_distinct_declared_nbm_fields():
    root = Path(__file__).resolve().parents[1]
    dates = ["2026-07-03"]
    kalshi = load_exact_features(root, dates, {
        "id": "kalshi_fixed_pst_f008_f031", "nbm_daily_high_field": "daily_high_f"
    })[0]
    fixture = load_exact_features(root, dates, {
        "id": "supplied_fixture_f007_f030", "nbm_daily_high_field": "fixture_compatible_daily_high_f"
    })[0]
    assert kalshi["gfs"] == fixture["gfs"]
    assert kalshi["nam"] == fixture["nam"]
    assert isinstance(kalshi["nbm"], float) and isinstance(fixture["nbm"], float)


def test_bootstrap_seed_depends_on_date_not_window_name():
    assert _date_seed(20260928, "2026-07-03") == _date_seed(20260928, "2026-07-03")
    assert _date_seed(20260928, "2026-07-03") != _date_seed(20260928, "2026-07-04")
