from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from kalshi_weather.strategy_current.residuals import (
    HistoricalLiveState,
    OutcomeRecord,
    build_residual_library,
    build_residual_records,
    corrected_model_point_f,
    effective_sample_size,
    recency_weights,
    weighted_median,
)


def _state(day: int, value: float, model_key: str = "gfs013") -> HistoricalLiveState:
    return HistoricalLiveState(
        target_date_local=date(2026, 7, day),
        model_key=model_key,
        market_time_bucket="target_15",
        evaluated_at_utc=datetime(2026, 7, day, 22, tzinfo=timezone.utc),
        raw_live_state_f=value,
        observed_max_f=70.0,
    )


def _outcome(day: int, physical: float, official: float) -> OutcomeRecord:
    return OutcomeRecord(
        target_date_local=date(2026, 7, day),
        physical_high_f=physical,
        official_settlement_high_f=official,
    )


def test_residual_records_use_prior_dates_only_and_separate_settlement_gap() -> None:
    records = build_residual_records(
        [
            _state(5, 70.0),
            _state(6, 72.0),
            _state(7, 90.0),
        ],
        [
            _outcome(5, physical=71.5, official=72.0),
            _outcome(6, physical=71.0, official=70.0),
            _outcome(7, physical=99.0, official=99.0),
        ],
        asof_target_date=date(2026, 7, 7),
        model_key="gfs013",
        market_time_bucket="target_15",
    )

    assert [record.target_date_local.day for record in records] == [6, 5]
    assert records[0].residual_f == -1.0
    assert records[0].settlement_gap_f == -1.0
    assert records[1].residual_f == 1.5
    assert records[1].settlement_gap_f == 0.5


def test_residual_library_weights_and_corrected_point() -> None:
    records = build_residual_records(
        [_state(3, 69.0), _state(4, 70.0), _state(5, 72.0)],
        [_outcome(3, 70.0, 70.0), _outcome(4, 70.5, 70.0), _outcome(5, 71.0, 71.0)],
        asof_target_date=date(2026, 7, 6),
        model_key="gfs013",
        market_time_bucket="target_15",
    )
    library = build_residual_library(
        records,
        model_key="gfs013",
        market_time_bucket="target_15",
    )

    assert round(sum(library.normalized_weights), 12) == 1.0
    assert 1.0 < library.effective_sample_size <= 3.0
    assert library.weighted_median_residual_f in {-1.0, 0.5, 1.0}
    assert corrected_model_point_f(72.0, 73.0, library) >= 73.0


def test_recency_weights_effective_sample_size_and_weighted_median_validation() -> None:
    weights = recency_weights([1, 2, 3], half_life_target_dates=21.0)
    assert round(sum(weights), 12) == 1.0
    assert effective_sample_size(weights) <= 3.0
    assert weighted_median([1.0, 10.0, 20.0], [0.2, 0.6, 0.2]) == 10.0
    with pytest.raises(ValueError, match="weights"):
        effective_sample_size([0.5, 0.4])


def test_residual_records_are_capped_to_recent_prior_dates() -> None:
    states = [_state(day, float(day)) for day in range(1, 7)]
    outcomes = [_outcome(day, physical=float(day) + 1.0, official=float(day) + 1.0) for day in range(1, 7)]

    records = build_residual_records(
        states,
        outcomes,
        asof_target_date=date(2026, 7, 7),
        model_key="gfs013",
        maximum_prior_target_dates=3,
    )

    assert [record.target_date_local.day for record in records] == [6, 5, 4]
