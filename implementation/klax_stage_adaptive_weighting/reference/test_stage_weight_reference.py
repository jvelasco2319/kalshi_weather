from datetime import date, datetime
from math import log
from zoneinfo import ZoneInfo

import pytest

from stage_weight_reference import (
    MODELS,
    STAGE_PRIORS,
    ScoreSummary,
    classify_stage,
    compute_weights,
    fixed_weights,
    stage_prior_weights,
    summarize_scores,
)

PT = ZoneInfo("America/Los_Angeles")
TARGET = date(2026, 7, 13)


def test_stage_priors_sum_to_one():
    for stage, priors in STAGE_PRIORS.items():
        assert set(priors) == set(MODELS)
        assert sum(priors.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "when, expected",
    [
        (datetime(2026, 7, 12, 7, 0, tzinfo=PT), "pre_target"),
        (datetime(2026, 7, 13, 1, 59, 59, tzinfo=PT), "pre_target"),
        (datetime(2026, 7, 13, 2, 0, tzinfo=PT), "target_02_10"),
        (datetime(2026, 7, 13, 11, 0, tzinfo=PT), "target_11_13"),
        (datetime(2026, 7, 13, 14, 0, tzinfo=PT), "target_14_16"),
        (datetime(2026, 7, 13, 17, 0, tzinfo=PT), "target_17_close"),
    ],
)
def test_stage_boundaries(when, expected):
    assert classify_stage(when, TARGET).stage_id == expected


def test_transition_alpha_after_boundary():
    state = classify_stage(datetime(2026, 7, 13, 11, 15, tzinfo=PT), TARGET, 30)
    assert state.stage_id == "target_11_13"
    assert state.transition_from_stage == "target_02_10"
    assert state.transition_alpha == pytest.approx(.5)


def test_score_shrinkage_and_readiness():
    rows = [(0.5, i) for i in range(20)]
    s = summarize_scores(rows)
    assert s.ready
    assert s.log_loss == pytest.approx(.5)
    assert .5 < s.shrunk_log_loss < log(6)


def test_insufficient_score_uses_prior_only():
    s = summarize_scores([(0.2, 0)] * 5)
    assert not s.ready
    assert s.reliability_multiplier == 1.0


def _score_map(stage: str):
    # Better loss for the historically preferred stage model.
    losses = {
        "ecmwf_ifs": .75,
        "gfs013": .45 if stage in ("pre_target", "target_02_10") else .68,
        "gfs_seamless": .43 if stage == "target_11_13" else .66,
        "nam": .42 if stage == "target_14_16" else .82,
        "nbm": .58,
    }
    return {m: summarize_scores([(losses[m], age) for age in range(40)]) for m in MODELS}


def test_caps_and_normalization():
    stage = classify_stage(datetime(2026, 7, 13, 8, 0, tzinfo=PT), TARGET)
    result = compute_weights(
        stage_state=stage,
        score_summaries={stage.stage_id: _score_map(stage.stage_id)},
        available={m: True for m in MODELS},
        eligible={m: True for m in MODELS},
        nbm_completed_dates=45,
    )
    w = result["weights"]
    assert sum(w.values()) == pytest.approx(1.0)
    assert max(w.values()) <= .35 + 1e-9
    assert w["gfs013"] + w["gfs_seamless"] <= .45 + 1e-9
    assert w["nbm"] <= .20 + 1e-9


def test_nbm_zero_before_ten_dates():
    stage = classify_stage(datetime(2026, 7, 13, 8, 0, tzinfo=PT), TARGET)
    w = stage_prior_weights(
        stage_state=stage,
        available={m: True for m in MODELS},
        eligible={m: True for m in MODELS},
        nbm_completed_dates=0,
    )
    assert w["nbm"] == pytest.approx(0)
    assert sum(w.values()) == pytest.approx(1)


def test_unavailable_model_zero():
    available = {m: True for m in MODELS}
    available["nam"] = False
    w = fixed_weights(available=available, eligible={m: True for m in MODELS}, nbm_completed_dates=60)
    assert w["nam"] == 0
    assert sum(w.values()) == pytest.approx(1)


def test_stage_strength_changes_relative_weight():
    early = classify_stage(datetime(2026, 7, 13, 8, 0, tzinfo=PT), TARGET)
    afternoon = classify_stage(datetime(2026, 7, 13, 15, 0, tzinfo=PT), TARGET)
    common = dict(available={m: True for m in MODELS}, eligible={m: True for m in MODELS}, nbm_completed_dates=60)
    we = compute_weights(stage_state=early, score_summaries={early.stage_id: _score_map(early.stage_id)}, **common)["weights"]
    wa = compute_weights(stage_state=afternoon, score_summaries={afternoon.stage_id: _score_map(afternoon.stage_id)}, **common)["weights"]
    assert we["gfs013"] > wa["gfs013"]
    assert wa["nam"] > we["nam"]
