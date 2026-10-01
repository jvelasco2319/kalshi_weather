from __future__ import annotations

from dataclasses import replace

import pytest

from v8.regime_calibration import (
    DayRecord,
    METHOD_AXES,
    calibrated_probabilities,
    walk_forward_predictions,
)


def _day(index: int, *, outcome: int | None = None) -> DayRecord:
    month = 1 if index < 20 else 3
    day = index + 1 if index < 20 else index - 19
    climate_date = f"2025-{month:02d}-{day:02d}"
    disagreement = 1.0 if index % 3 == 0 else 3.0 if index % 3 == 1 else 6.0
    disagreement_bin = (
        "low_le_2f" if disagreement <= 2 else
        "medium_2_to_5f" if disagreement <= 5 else
        "high_gt_5f"
    )
    probabilities = (0.0, 0.05, 0.20, 0.55, 0.20, 0.0)
    return DayRecord(
        climate_date=climate_date,
        partition=(
            "calibration_overlap_diagnostic" if index < 10
            else "post_calibration_primary"
        ),
        tickers=tuple(f"T{position}" for position in range(6)),
        raw_probabilities=probabilities,
        outcome_position=(index % 6 if outcome is None else outcome),
        season="winter" if month == 1 else "spring",
        disagreement_bin=disagreement_bin,
        hrrr_max_f=70.0 + disagreement,
        gefs_mean_max_f=70.0,
        signed_hrrr_minus_gefs_f=disagreement,
        absolute_hrrr_gefs_disagreement_f=disagreement,
        gefs_spread_mean_f=0.6,
        gefs_spread_max_f=1.0,
    )


def test_calibrated_vectors_are_strictly_positive_and_normalized() -> None:
    history = [_day(index) for index in range(10)]
    current = _day(10)
    for axes in ((), ("season",), ("disagreement_bin",), ("season", "disagreement_bin")):
        probabilities = calibrated_probabilities(history, current, axes)
        assert len(probabilities) == 6
        assert min(probabilities) > 0
        assert sum(probabilities) == pytest.approx(1.0)


def test_walk_forward_does_not_use_same_day_or_future_outcomes() -> None:
    original = [_day(index) for index in range(25)]
    altered = [
        replace(row, outcome_position=(row.outcome_position + 3) % 6)
        if index >= 18 else row
        for index, row in enumerate(original)
    ]
    first = walk_forward_predictions(original)
    second = walk_forward_predictions(altered)
    for method in METHOD_AXES:
        for index in range(19):
            # Day 18 itself must be unchanged: its altered outcome is appended
            # only after every method has emitted that day's probabilities.
            assert first[method][index]["probabilities"] == second[method][index]["probabilities"]
        assert first[method][18]["training_end_date"] == original[17].climate_date
        assert first[method][18]["training_rows"] == 18


def test_regime_fit_uses_only_matching_prior_group_after_pooled_shrinkage() -> None:
    history = [_day(index) for index in range(18)]
    current = _day(18)
    season_probabilities = calibrated_probabilities(history, current, ("season",))
    disagreement_probabilities = calibrated_probabilities(
        history, current, ("disagreement_bin",)
    )
    assert season_probabilities != disagreement_probabilities
    assert min(season_probabilities) > 0
    assert min(disagreement_probabilities) > 0


def test_raw_modal_tie_break_matches_v7y_ticker_rule() -> None:
    row = replace(
        _day(0),
        raw_probabilities=(0.0, 0.1, 0.4, 0.4, 0.1, 0.0),
        tickers=("A", "B", "C", "Z", "Y", "X"),
    )
    assert row.modal_position == 3

