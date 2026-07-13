from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from kalshi_weather.strategy_current.persistence import ForecastPathPoint, ObservationEvent
from kalshi_weather.strategy_current.registry import source_history_key
from kalshi_weather.strategy_current.state_builder import (
    FutureSourceLeakageError,
    ModelLiveState,
    build_model_live_state,
    model_state_spread,
    select_latest_run_asof,
)


def _dt(hour: int) -> datetime:
    return datetime(2026, 7, 7, hour, tzinfo=timezone.utc)


def _point(
    point_id: str,
    *,
    model_key: str = "gfs013",
    run_id: str = "run-a",
    available_hour: int = 18,
    received_hour: int = 18,
    valid_hour: int = 23,
    temp: float = 72.0,
) -> ForecastPathPoint:
    return ForecastPathPoint(
        point_id=point_id,
        target_date_local=date(2026, 7, 7),
        model_key=model_key,
        source_variant=source_history_key(model_key),
        run_id=run_id,
        run_time_utc=_dt(available_hour - 1),
        source_available_at_utc=_dt(available_hour),
        valid_time_utc=_dt(valid_hour),
        received_at_utc=_dt(received_hour),
        temperature_f=temp,
    )


def _observation(
    observation_id: str,
    *,
    available_hour: int = 21,
    received_hour: int = 21,
    observation_hour: int = 21,
    temp: float = 73.4,
    accepted: bool = True,
) -> ObservationEvent:
    return ObservationEvent(
        observation_id=observation_id,
        station="KLAX",
        target_date_local=date(2026, 7, 7),
        observation_time_utc=_dt(observation_hour),
        source_available_at_utc=_dt(available_hour),
        received_at_utc=_dt(received_hour),
        temperature_f=temp,
        accepted=accepted,
        rejection_reason=None if accepted else "QC_REJECTED",
    )


def test_july7_past_forecast_peak_cannot_survive_remaining_window() -> None:
    evaluated_at = _dt(22)
    state = build_model_live_state(
        decision_id="d1",
        model_key="gfs013",
        target_date_local="2026-07-07",
        evaluated_at_utc=evaluated_at,
        forecast_points=[
            _point("past-hot", valid_hour=21, temp=76.3),
            _point("future-cool", valid_hour=23, temp=72.0),
        ],
        observations=[_observation("obs-high", temp=73.4)],
    )

    assert state.future_max_f == 72.0
    assert state.observed_max_f == 73.4
    assert state.raw_live_state_f == 73.4
    assert state.forecast_point_ids == ("future-cool",)


def test_future_source_available_or_received_times_are_rejected() -> None:
    with pytest.raises(FutureSourceLeakageError):
        build_model_live_state(
            decision_id="d1",
            model_key="gfs013",
            target_date_local="2026-07-07",
            evaluated_at_utc=_dt(22),
            forecast_points=[_point("future-source", available_hour=23, received_hour=21)],
        )

    with pytest.raises(FutureSourceLeakageError):
        build_model_live_state(
            decision_id="d1",
            model_key="gfs013",
            target_date_local="2026-07-07",
            evaluated_at_utc=_dt(22),
            forecast_points=[_point("future-received", available_hour=21, received_hour=23)],
        )


def test_latest_backward_run_selection_ignores_future_runs_when_not_strict() -> None:
    selected = select_latest_run_asof(
        [
            _point("older", run_id="run-old", available_hour=18, temp=70.0),
            _point("newer", run_id="run-new", available_hour=21, temp=72.0),
            _point("future", run_id="run-future", available_hour=23, temp=90.0),
        ],
        model_key="gfs013",
        target_date_local="2026-07-07",
        evaluated_at_utc=_dt(22),
        day_start_utc=_dt(8),
        day_end_utc=datetime(2026, 7, 8, 8, tzinfo=timezone.utc),
        strict_source_times=False,
    )

    assert selected.run_id == "run-new"
    assert [point.point_id for point in selected.points] == ["newer"]


def test_observations_must_be_available_and_accepted() -> None:
    with pytest.raises(FutureSourceLeakageError):
        build_model_live_state(
            decision_id="d1",
            model_key="gfs013",
            target_date_local="2026-07-07",
            evaluated_at_utc=_dt(22),
            forecast_points=[_point("future-cool", valid_hour=23, temp=72.0)],
            observations=[_observation("future-obs", available_hour=23, temp=100.0)],
        )

    state = build_model_live_state(
        decision_id="d1",
        model_key="gfs013",
        target_date_local="2026-07-07",
        evaluated_at_utc=_dt(22),
        forecast_points=[_point("future-cool", valid_hour=23, temp=72.0)],
        observations=[
            _observation("rejected", temp=100.0, accepted=False),
            _observation("accepted", temp=71.0),
        ],
    )
    assert state.observed_max_f == 71.0
    assert state.raw_live_state_f == 72.0


def test_model_spread_requires_four_feeds_and_three_families() -> None:
    states = [
        ModelLiveState("d", "ecmwf_ifs", "s", _dt(22), 70.0, 70.0, ("p1",)),
        ModelLiveState("d", "gfs013", "s", _dt(22), 71.0, 71.0, ("p2",)),
        ModelLiveState("d", "gfs_seamless", "s", _dt(22), 72.0, 72.0, ("p3",)),
        ModelLiveState("d", "nam", "s", _dt(22), 74.0, 74.0, ("p4",)),
    ]

    assert model_state_spread(states) == 4.0
    with pytest.raises(ValueError, match="four"):
        model_state_spread(states[:3])
