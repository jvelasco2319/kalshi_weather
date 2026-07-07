from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from kalshi_weather.model.outcomes import bracket_type
from kalshi_weather.model.probability import (
    bracket_probabilities,
    normalize_probabilities,
    settlement_high_samples,
)
from kalshi_weather.schemas import Bracket, OrderbookTop, WeatherSnapshot
from kalshi_weather.time_utils import utc_now
from kalshi_weather.trading.signals import terminal_edges


@dataclass(frozen=True)
class ModelEstimate:
    asof_utc: datetime
    station: str
    market_date: date | str
    provider: str
    model_id: str
    model_name: str
    model_family: str
    run_utc: datetime | str | None
    cycle_utc: datetime | str | None
    forecast_window_start_utc: datetime | str | None
    forecast_window_end_utc: datetime | str | None
    forecast_hours_used: list[int | float | str] = field(default_factory=list)
    observed_high_so_far_f: float | None = None
    future_high_f: float | None = None
    settlement_high_estimate_f: float | None = None
    units: str = "F"
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None
    source_url: str | None = None
    successful: bool = True
    error_message: str | None = None
    details_json: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["market_date"] = _date_text(self.market_date)
        record["asof_utc"] = _iso_or_none(self.asof_utc)
        record["run_utc"] = _iso_or_none(self.run_utc)
        record["cycle_utc"] = _iso_or_none(self.cycle_utc)
        record["forecast_window_start_utc"] = _iso_or_none(self.forecast_window_start_utc)
        record["forecast_window_end_utc"] = _iso_or_none(self.forecast_window_end_utc)
        return record


@dataclass(frozen=True)
class ModelProbability:
    asof_utc: datetime
    station: str
    market_date: date | str
    provider: str
    model_id: str
    market_ticker: str
    bracket_label: str
    bracket_lower_f: int | None
    bracket_upper_f: int | None
    bracket_type: str
    p_yes: float
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    yes_edge: Decimal | None
    no_edge: Decimal | None
    method: str
    residual_sigma_f: float
    details_json: dict[str, Any] = field(default_factory=dict)
    estimate_id: int | None = None

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["market_date"] = _date_text(self.market_date)
        record["asof_utc"] = _iso_or_none(self.asof_utc)
        return record


def estimate_key(estimate: ModelEstimate | dict[str, Any]) -> str:
    provider = estimate.provider if isinstance(estimate, ModelEstimate) else estimate["provider"]
    model_id = estimate.model_id if isinstance(estimate, ModelEstimate) else estimate["model_id"]
    return f"{provider}:{model_id}"


def model_id_from_temperature_column(column: str) -> str:
    return column.split("__", 1)[1] if "__" in column else column


def _missing_number(value: float | None) -> bool:
    if value is None:
        return True
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def settlement_estimate(observed_high_so_far_f: float | None, future_high_f: float | None) -> float | None:
    if _missing_number(future_high_f):
        return None if _missing_number(observed_high_so_far_f) else float(observed_high_so_far_f)
    if _missing_number(observed_high_so_far_f):
        return float(future_high_f)
    return max(float(observed_high_so_far_f), float(future_high_f))


def current_and_open_meteo_estimates(
    *,
    station: str,
    market_date: date | str,
    weather: WeatherSnapshot,
    model_maxes_f: dict[str, float],
    successful_models: list[str] | None = None,
    failed_models: dict[str, str] | None = None,
    forecast_window_start_utc: datetime | None = None,
    forecast_window_end_utc: datetime | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> list[ModelEstimate]:
    """Expose the existing production blend and Open-Meteo feeds as comparison estimates."""
    asof = weather.timestamp_utc or utc_now()
    successful = set(successful_models or [])
    failed = failed_models or {}
    observed = weather.observed_high_so_far_f
    estimates = [
        ModelEstimate(
            asof_utc=asof,
            station=station,
            market_date=market_date,
            provider="current",
            model_id="current_weighted_blend",
            model_name="Current weighted Open-Meteo blend",
            model_family="current_production_baseline",
            run_utc=None,
            cycle_utc=None,
            forecast_window_start_utc=forecast_window_start_utc,
            forecast_window_end_utc=forecast_window_end_utc,
            observed_high_so_far_f=observed,
            future_high_f=weather.model_future_high_f,
            settlement_high_estimate_f=settlement_estimate(observed, weather.model_future_high_f),
            latitude=latitude,
            longitude=longitude,
            source="existing_open_meteo_pipeline",
            successful=weather.model_future_high_f is not None,
            error_message=None if weather.model_future_high_f is not None else "current model future high unavailable",
            details_json=weather.model_details,
        )
    ]
    for column, future_high in sorted(model_maxes_f.items()):
        model_id = model_id_from_temperature_column(column)
        ok = model_id in successful or column in model_maxes_f
        estimates.append(
            ModelEstimate(
                asof_utc=asof,
                station=station,
                market_date=market_date,
                provider="open_meteo",
                model_id=model_id,
                model_name=model_id,
                model_family="open_meteo_individual_feed",
                run_utc=None,
                cycle_utc=None,
                forecast_window_start_utc=forecast_window_start_utc,
                forecast_window_end_utc=forecast_window_end_utc,
                observed_high_so_far_f=observed,
                future_high_f=float(future_high),
                settlement_high_estimate_f=settlement_estimate(observed, float(future_high)),
                latitude=latitude,
                longitude=longitude,
                source="open_meteo",
                successful=ok,
                error_message=failed.get(model_id),
                details_json={"source_column": column},
            )
        )
    emitted = {estimate.model_id for estimate in estimates}
    for model_id, error_message in sorted(failed.items()):
        if model_id in emitted:
            continue
        estimates.append(
            ModelEstimate(
                asof_utc=asof,
                station=station,
                market_date=market_date,
                provider="open_meteo",
                model_id=model_id,
                model_name=model_id,
                model_family="open_meteo_individual_feed",
                run_utc=None,
                cycle_utc=None,
                forecast_window_start_utc=forecast_window_start_utc,
                forecast_window_end_utc=forecast_window_end_utc,
                observed_high_so_far_f=observed,
                future_high_f=None,
                settlement_high_estimate_f=None,
                latitude=latitude,
                longitude=longitude,
                source="open_meteo",
                successful=False,
                error_message=error_message,
                details_json={"provider_status": "failed_model_request"},
            )
        )
    return estimates


def probabilities_for_estimate(
    estimate: ModelEstimate,
    brackets: list[Bracket],
    tops: dict[str, OrderbookTop],
    *,
    residual_sigma_f: float,
    sample_count: int,
    normalize: bool = True,
) -> list[ModelProbability]:
    if not estimate.successful or _missing_number(estimate.future_high_f):
        return []
    observed_high = (
        float(estimate.observed_high_so_far_f)
        if not _missing_number(estimate.observed_high_so_far_f)
        else float("nan")
    )
    samples = settlement_high_samples(
        future_high_center_f=float(estimate.future_high_f),
        observed_high_so_far_f=observed_high,
        residual_sigma_f=residual_sigma_f,
        sample_count=sample_count,
    )
    raw = bracket_probabilities(samples, brackets)
    probs = normalize_probabilities(raw) if normalize else raw
    rows: list[ModelProbability] = []
    for bracket in brackets:
        top = tops.get(bracket.ticker)
        p_yes = probs[bracket.ticker]
        yes_edge, no_edge = terminal_edges(p_yes, top) if top else (None, None)
        rows.append(
            ModelProbability(
                asof_utc=estimate.asof_utc,
                station=estimate.station,
                market_date=estimate.market_date,
                provider=estimate.provider,
                model_id=estimate.model_id,
                market_ticker=bracket.ticker,
                bracket_label=bracket.label,
                bracket_lower_f=bracket.lo_f,
                bracket_upper_f=bracket.hi_f,
                bracket_type=bracket_type(bracket.lo_f, bracket.hi_f),
                p_yes=p_yes,
                yes_bid=top.yes_bid if top else None,
                yes_ask=top.yes_ask if top else None,
                no_bid=top.no_bid if top else None,
                no_ask=top.no_ask if top else None,
                yes_edge=yes_edge,
                no_edge=no_edge,
                method="normal_residual_same_as_current_model",
                residual_sigma_f=residual_sigma_f,
                details_json={
                    "estimate_key": estimate_key(estimate),
                    "settlement_high_estimate_f": estimate.settlement_high_estimate_f,
                },
            )
        )
    return rows


def probabilities_for_estimates(
    estimates: list[ModelEstimate],
    brackets: list[Bracket],
    tops: dict[str, OrderbookTop],
    *,
    residual_sigma_f: float,
    sample_count: int,
) -> list[ModelProbability]:
    rows: list[ModelProbability] = []
    for estimate in estimates:
        rows.extend(
            probabilities_for_estimate(
                estimate,
                brackets,
                tops,
                residual_sigma_f=residual_sigma_f,
                sample_count=sample_count,
                normalize=True,
            )
        )
    return rows


def serialize_details(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _date_text(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
