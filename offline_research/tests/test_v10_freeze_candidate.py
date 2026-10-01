from pathlib import Path

from v10.freeze_candidate import build


ROOT = Path(__file__).resolve().parents[1]


def test_v10_candidate_freeze_preserves_pressure_only_method():
    value = build(ROOT)
    assert value["candidate_id"] == "V10-pressure_and_flow-W75"
    assert value["future_outcome_updates_allowed"] is False
    assert value["genuinely_new_confirmation_required"] is True
    assert value["promotion_or_online_use_authorized"] is False
    assert value["fitted_parameters"]["fit_date_count"] == 359
    assert set(value["fitted_parameters"]["pressure_conditioned_tables"]["0"]) == {"offshore", "neutral", "onshore"}
