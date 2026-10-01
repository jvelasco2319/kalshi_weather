import json
from pathlib import Path

from past7_replay.engine import _ordered_yes
from v7_shadow.daily import _friend_forecasts, _v5b_probabilities, _v6_forecast, _weather_frames


ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-08-31"


def contracts():
    universe = json.loads((ROOT / "data/manifests/v5b_untouched_outcome_blind_universe.json").read_text(encoding="utf-8"))
    return _ordered_yes([row for row in universe["records"] if row["climate_date"] == DAY])


def test_frozen_forecasters_reproduce_coherent_new_date_vectors():
    yes = contracts()
    hrrr, gefs, bindings = _weather_frames(ROOT, DAY)
    assert len(bindings) == 3
    v5b, metadata = _v5b_probabilities(ROOT, DAY, yes, hrrr, gefs)
    v6 = _v6_forecast(ROOT, DAY, yes, hrrr, gefs)
    friend, diagnostics = _friend_forecasts(ROOT, DAY, yes)
    assert abs(sum(v5b.values()) - 1) < 1e-10
    assert abs(sum(v6["central_probabilities"].values()) - 1) < 1e-8
    assert abs(sum(friend["central_probabilities"].values()) - 1) < 1e-8
    assert set(diagnostics) == {"gfs", "nam", "nbm"}
    assert metadata["target_updates_used"] is False

