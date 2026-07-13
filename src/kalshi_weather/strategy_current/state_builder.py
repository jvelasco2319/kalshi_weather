from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from kalshi_weather.model.lax_high_temp import lax_climate_day_utc
from kalshi_weather.strategy_current.persistence import ForecastPathPoint, ObservationEvent
from kalshi_weather.strategy_current.registry import canonicalize_model_key, strategy_model_by_key


class FutureSourceLeakageError(ValueError):
    """Raised when an as-of build receives data unavailable at evaluation time."""


class NoEligibleForecastRunError(ValueError):
    """Raised when no run has remaining forecast path points at evaluation time."""


@dataclass(frozen=True)
class SelectedForecastRun:
    model_key: str
    source_variant: str
    run_id: str
    run_time_utc: datetime
    source_available_at_utc: datetime
    received_at_utc: datetime
    points: tuple[ForecastPathPoint | dict[str, Any], ...]


@dataclass(frozen=True)
class ModelLiveState:
    decision_id: str
    model_key: str
    source_variant: str
    evaluated_at_utc: datetime
    future_max_f: float
    raw_live_state_f: float
    forecast_point_ids: tuple[str, ...]
    observed_max_f: float | None = None
    observation_ids: tuple[str, ...] = ()
    corrected_point_f: float | None = None
    residual_history_count: int = 0
    effective_sample_size: float | None = None
    reliability_weight: float | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "model_key": self.model_key,
            "source_variant": self.source_variant,
            "evaluated_at_utc": _iso(self.evaluated_at_utc),
            "future_max_f": self.future_max_f,
            "raw_live_state_f": self.raw_live_state_f,
            "forecast_point_ids": list(self.forecast_point_ids),
            "observed_max_f": self.observed_max_f,
            "observation_ids": list(self.observation_ids),
            "corrected_point_f": self.corrected_point_f,
            "residual_history_count": self.residual_history_count,
            "effective_sample_size": self.effective_sample_size,
            "reliability_weight": self.reliability_weight,
        }


def build_model_live_state(
    *,
    decision_id: str,
    model_key: str,
    target_date_local: str | date,
    evaluated_at_utc: datetime,
    forecast_points: Iterable[ForecastPathPoint | dict[str, Any]],
    observations: Iterable[ObservationEvent | dict[str, Any]] = (),
    strict_source_times: bool = True,
) -> ModelLiveState:
    evaluated_at = _ensure_aware_utc(evaluated_at_utc)
    target_date = _date(target_date_local)
    day_start_utc, day_end_utc = lax_climate_day_utc(target_date)
    canonical_model = canonicalize_model_key(model_key)
    selected = select_latest_run_asof(
        forecast_points,
        model_key=canonical_model,
        target_date_local=target_date,
        evaluated_at_utc=evaluated_at,
        day_start_utc=day_start_utc,
        day_end_utc=day_end_utc,
        strict_source_times=strict_source_times,
    )
    future_max = max(_temperature(point) for point in selected.points)
    observed_max, observation_ids = observed_max_asof(
        observations,
        target_date_local=target_date,
        evaluated_at_utc=evaluated_at,
        day_start_utc=day_start_utc,
        day_end_utc=day_end_utc,
        strict_source_times=strict_source_times,
    )
    raw_live_state = max(future_max, observed_max) if observed_max is not None else future_max
    return ModelLiveState(
        decision_id=decision_id,
        model_key=canonical_model,
        source_variant=selected.source_variant,
        evaluated_at_utc=evaluated_at,
        future_max_f=float(future_max),
        observed_max_f=None if observed_max is None else float(observed_max),
        raw_live_state_f=float(raw_live_state),
        forecast_point_ids=tuple(str(_value(point, "point_id")) for point in selected.points),
        observation_ids=observation_ids,
    )


def select_latest_run_asof(
    forecast_points: Iterable[ForecastPathPoint | dict[str, Any]],
    *,
    model_key: str,
    target_date_local: str | date,
    evaluated_at_utc: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
    strict_source_times: bool = True,
) -> SelectedForecastRun:
    evaluated_at = _ensure_aware_utc(evaluated_at_utc)
    target_date = _date(target_date_local)
    canonical_model = canonicalize_model_key(model_key)
    day_start = _ensure_aware_utc(day_start_utc)
    day_end = _ensure_aware_utc(day_end_utc)
    remaining_start = max(evaluated_at, day_start)
    candidate_points: list[ForecastPathPoint | dict[str, Any]] = []

    for point in forecast_points:
        if canonicalize_model_key(str(_value(point, "model_key"))) != canonical_model:
            continue
        if _date(_value(point, "target_date_local")) != target_date:
            continue
        source_available = _dt(_value(point, "source_available_at_utc"))
        received = _dt(_value(point, "received_at_utc"))
        if source_available > evaluated_at or received > evaluated_at:
            if strict_source_times:
                raise FutureSourceLeakageError(
                    f"{canonical_model} forecast point is unavailable at evaluated_at"
                )
            continue
        valid_time = _dt(_value(point, "valid_time_utc"))
        if not (remaining_start <= valid_time <= day_end):
            continue
        candidate_points.append(point)

    if not candidate_points:
        raise NoEligibleForecastRunError(f"no eligible remaining run for {canonical_model}")

    by_run: dict[tuple[str, str], list[ForecastPathPoint | dict[str, Any]]] = {}
    for point in candidate_points:
        key = (str(_value(point, "source_variant")), str(_value(point, "run_id")))
        by_run.setdefault(key, []).append(point)

    selected_points = max(
        by_run.values(),
        key=lambda rows: (
            max(_dt(_value(row, "source_available_at_utc")) for row in rows),
            max(_dt(_value(row, "received_at_utc")) for row in rows),
            max(_dt(_value(row, "run_time_utc")) for row in rows),
            str(_value(rows[0], "run_id")),
        ),
    )
    first = selected_points[0]
    return SelectedForecastRun(
        model_key=canonical_model,
        source_variant=str(_value(first, "source_variant")),
        run_id=str(_value(first, "run_id")),
        run_time_utc=max(_dt(_value(row, "run_time_utc")) for row in selected_points),
        source_available_at_utc=max(
            _dt(_value(row, "source_available_at_utc")) for row in selected_points
        ),
        received_at_utc=max(_dt(_value(row, "received_at_utc")) for row in selected_points),
        points=tuple(
            sorted(selected_points, key=lambda point: (_dt(_value(point, "valid_time_utc")), str(_value(point, "point_id"))))
        ),
    )


def observed_max_asof(
    observations: Iterable[ObservationEvent | dict[str, Any]],
    *,
    target_date_local: str | date,
    evaluated_at_utc: datetime,
    day_start_utc: datetime,
    day_end_utc: datetime,
    strict_source_times: bool = True,
) -> tuple[float | None, tuple[str, ...]]:
    evaluated_at = _ensure_aware_utc(evaluated_at_utc)
    target_date = _date(target_date_local)
    day_start = _ensure_aware_utc(day_start_utc)
    day_end = _ensure_aware_utc(day_end_utc)
    values: list[tuple[float, str]] = []
    for observation in observations:
        if _date(_value(observation, "target_date_local")) != target_date:
            continue
        source_available = _dt(_value(observation, "source_available_at_utc"))
        received = _dt(_value(observation, "received_at_utc"))
        if source_available > evaluated_at or received > evaluated_at:
            if strict_source_times:
                raise FutureSourceLeakageError("observation is unavailable at evaluated_at")
            continue
        if not bool(_value(observation, "accepted")):
            continue
        observation_time = _dt(_value(observation, "observation_time_utc"))
        if day_start <= observation_time <= min(evaluated_at, day_end):
            values.append((float(_value(observation, "temperature_f")), str(_value(observation, "observation_id"))))
    if not values:
        return None, ()
    return max(value for value, _id in values), tuple(_id for _value_f, _id in values)


def model_state_spread(states: Iterable[ModelLiveState]) -> float:
    by_model = {state.model_key: state.raw_live_state_f for state in states}
    if len(by_model) < 4:
        raise ValueError("at least four model states are required")
    families = {strategy_model_by_key(model_key).family for model_key in by_model}
    if len(families) < 3:
        raise ValueError("at least three model families are required")
    values = list(by_model.values())
    return max(values) - min(values)


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    return getattr(row, key)


def _temperature(point: Any) -> float:
    return float(_value(point, "temperature_f"))


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware_utc(value)
    return _ensure_aware_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _ensure_aware_utc(value).isoformat()
