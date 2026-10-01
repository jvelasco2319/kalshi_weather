from datetime import date, timedelta
import pytest
from klax_lab.models import fit_baselines, predict, seasonal_position


def test_bias_fitted_without_future_rows():
    rows = [{"climate_date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "gfs": 60., "nbm": 62., "tmax_f": 65.} for i in range(120)]
    models = fit_baselines(rows)
    mean, sd = predict(models["equal_blend_bias_corrected"], {"climate_date": "2025-01-10", "gfs": 64., "nbm": 66.})
    assert mean == 69 and sd == 1
    rows[-1]["climate_date"] = "2025-01-01"
    with pytest.raises(ValueError, match="restricted"):
        fit_baselines(rows)


def test_seasonal_calendar():
    assert seasonal_position("2024-03-01") == seasonal_position("2025-03-01")
