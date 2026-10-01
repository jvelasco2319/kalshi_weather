from datetime import date, timedelta
import pytest
from klax_lab.candidates import CandidateSpec, fit_candidate, predict_candidate


def spec(**changes):
    return CandidateSpec.from_dict({"gfs_weight": .5, "bias_mode": "global", "spread_mode": "global", "spread_scale": 1., "disagreement_coefficient": 0., **changes})


def test_closed_recipe_rejects_code_and_duplicate_numeric_identity():
    with pytest.raises(ValueError):
        spec(bias_mode="__import__('os').system('anything')")
    with pytest.raises(ValueError):
        CandidateSpec.from_dict({**spec().__dict__, "expression": "1+1"})
    with pytest.raises(ValueError):
        spec(spread_scale=True)
    assert spec(gfs_weight=1).identity == spec(gfs_weight=1.).identity


def test_disagreement_widens_uncertainty_without_fitting_future_labels():
    training = [{"climate_date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "gfs": 68., "nbm": 70., "tmax_f": 70.} for i in range(366)]
    fitted = fit_candidate(spec(spread_mode="disagreement", disagreement_coefficient=.5), training)
    near = predict_candidate(fitted, {"climate_date": "2025-01-05", "gfs": 69., "nbm": 69.})
    far = predict_candidate(fitted, {"climate_date": "2025-01-05", "gfs": 65., "nbm": 73.})
    assert near[0] == far[0] == 70 and far[1] > near[1]
    training[0]["climate_date"] = "2025-01-01"
    with pytest.raises(ValueError, match="restricted"):
        fit_candidate(spec(), training)
