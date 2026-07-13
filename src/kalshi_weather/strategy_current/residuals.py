from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from kalshi_weather.strategy_current.registry import canonicalize_model_key


@dataclass(frozen=True)
class HistoricalLiveState:
    target_date_local: str | date
    model_key: str
    market_time_bucket: str
    evaluated_at_utc: datetime
    raw_live_state_f: float
    observed_max_f: float | None = None


@dataclass(frozen=True)
class OutcomeRecord:
    target_date_local: str | date
    physical_high_f: float
    official_settlement_high_f: float

    @property
    def settlement_gap_f(self) -> float:
        return float(self.official_settlement_high_f) - float(self.physical_high_f)


@dataclass(frozen=True)
class ResidualRecord:
    target_date_local: date
    model_key: str
    market_time_bucket: str
    residual_f: float
    settlement_gap_f: float
    age_target_dates: int
    raw_live_state_f: float
    physical_high_f: float
    official_settlement_high_f: float


@dataclass(frozen=True)
class ResidualLibrary:
    model_key: str
    market_time_bucket: str
    records: tuple[ResidualRecord, ...]
    normalized_weights: tuple[float, ...]
    effective_sample_size: float
    weighted_median_residual_f: float


def build_residual_records(
    live_states: Iterable[HistoricalLiveState | dict[str, Any]],
    outcomes: Iterable[OutcomeRecord | dict[str, Any]],
    *,
    asof_target_date: str | date,
    model_key: str,
    market_time_bucket: str | None = None,
    maximum_prior_target_dates: int = 45,
) -> tuple[ResidualRecord, ...]:
    canonical_model = canonicalize_model_key(model_key)
    asof_date = _date(asof_target_date)
    outcomes_by_date = {_date(_value(outcome, "target_date_local")): outcome for outcome in outcomes}
    records: list[ResidualRecord] = []
    for state in live_states:
        state_date = _date(_value(state, "target_date_local"))
        if state_date >= asof_date:
            continue
        if canonicalize_model_key(str(_value(state, "model_key"))) != canonical_model:
            continue
        state_bucket = str(_value(state, "market_time_bucket"))
        if market_time_bucket is not None and state_bucket != market_time_bucket:
            continue
        outcome = outcomes_by_date.get(state_date)
        if outcome is None:
            continue
        physical_high = float(_value(outcome, "physical_high_f"))
        official_high = float(_value(outcome, "official_settlement_high_f"))
        raw_live_state = float(_value(state, "raw_live_state_f"))
        records.append(
            ResidualRecord(
                target_date_local=state_date,
                model_key=canonical_model,
                market_time_bucket=state_bucket,
                residual_f=physical_high - raw_live_state,
                settlement_gap_f=official_high - physical_high,
                age_target_dates=(asof_date - state_date).days,
                raw_live_state_f=raw_live_state,
                physical_high_f=physical_high,
                official_settlement_high_f=official_high,
            )
        )
    records.sort(key=lambda record: record.target_date_local, reverse=True)
    return tuple(records[:maximum_prior_target_dates])


def build_residual_library(
    records: Sequence[ResidualRecord],
    *,
    model_key: str,
    market_time_bucket: str,
    half_life_target_dates: float = 21.0,
) -> ResidualLibrary:
    if not records:
        raise ValueError("residual records are required")
    canonical_model = canonicalize_model_key(model_key)
    filtered = tuple(
        record
        for record in records
        if record.model_key == canonical_model and record.market_time_bucket == market_time_bucket
    )
    if not filtered:
        raise ValueError("no residual records match model and bucket")
    weights = tuple(
        recency_weights(
            [record.age_target_dates for record in filtered],
            half_life_target_dates=half_life_target_dates,
        )
    )
    return ResidualLibrary(
        model_key=canonical_model,
        market_time_bucket=market_time_bucket,
        records=filtered,
        normalized_weights=weights,
        effective_sample_size=effective_sample_size(weights),
        weighted_median_residual_f=weighted_median(
            [record.residual_f for record in filtered],
            weights,
        ),
    )


def recency_weights(
    ages_target_dates: Sequence[int],
    *,
    half_life_target_dates: float = 21.0,
) -> list[float]:
    if not ages_target_dates:
        raise ValueError("ages are required")
    if half_life_target_dates <= 0 or any(age < 0 for age in ages_target_dates):
        raise ValueError("invalid recency age or half-life")
    raw = [2.0 ** (-float(age) / half_life_target_dates) for age in ages_target_dates]
    total = sum(raw)
    return [value / total for value in raw]


def effective_sample_size(normalized_weights: Sequence[float]) -> float:
    if not normalized_weights:
        raise ValueError("weights are required")
    total = sum(normalized_weights)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("weights must sum to one")
    return 1.0 / sum(weight * weight for weight in normalized_weights)


def weighted_median(values: Sequence[float], normalized_weights: Sequence[float]) -> float:
    if len(values) != len(normalized_weights) or not values:
        raise ValueError("values and weights must be nonempty and same length")
    pairs = sorted(zip(values, normalized_weights), key=lambda item: item[0])
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= 0.5:
            return float(value)
    return float(pairs[-1][0])


def corrected_model_point_f(
    raw_live_state_f: float,
    observed_max_f: float | None,
    library: ResidualLibrary,
) -> float:
    corrected = float(raw_live_state_f) + library.weighted_median_residual_f
    if observed_max_f is not None:
        corrected = max(corrected, float(observed_max_f))
    return corrected


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    return getattr(row, key)


def _date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))
