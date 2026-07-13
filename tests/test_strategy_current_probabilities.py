from __future__ import annotations

from datetime import date, datetime, timezone

from kalshi_weather.strategy_current.probabilities import (
    build_model_distribution,
    combine_model_distributions,
    forecast_report_summary,
    nbm_maturity_cap,
    reliability_weights,
)
from kalshi_weather.strategy_current.residuals import (
    HistoricalLiveState,
    OutcomeRecord,
    build_residual_library,
    build_residual_records,
)
from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS


BRACKETS = ("low", "mid", "high")


def _quantizer(value: float) -> int:
    if value < 70:
        return 0
    if value < 74:
        return 1
    return 2


def _library(model_key: str):
    states = [
        HistoricalLiveState(
            target_date_local=date(2026, 7, day),
            model_key=model_key,
            market_time_bucket="target_15",
            evaluated_at_utc=datetime(2026, 7, day, 22, tzinfo=timezone.utc),
            raw_live_state_f=72.0,
            observed_max_f=70.0,
        )
        for day in (1, 2, 3)
    ]
    outcomes = [
        OutcomeRecord(date(2026, 7, 1), 69.0, 69.0),
        OutcomeRecord(date(2026, 7, 2), 72.0, 72.0),
        OutcomeRecord(date(2026, 7, 3), 75.0, 75.0),
    ]
    records = build_residual_records(
        states,
        outcomes,
        asof_target_date=date(2026, 7, 4),
        model_key=model_key,
        market_time_bucket="target_15",
    )
    return build_residual_library(records, model_key=model_key, market_time_bucket="target_15")


def test_model_distribution_preserves_minority_bracket_mass() -> None:
    distribution = build_model_distribution(
        model_key="gfs013",
        raw_live_state_f=72.0,
        observed_max_f=None,
        residual_library=_library("gfs013"),
        bracket_ids=BRACKETS,
        quantizer=_quantizer,
    )

    assert len(distribution.bracket_counts) == 3
    assert all(count > 0 for count in distribution.bracket_counts)
    assert round(sum(distribution.component_mean_yes), 12) == 1.0
    assert all(
        safe <= mean
        for safe, mean in zip(distribution.component_safe_yes, distribution.component_mean_yes)
    )
    assert all(
        safe <= mean
        for safe, mean in zip(distribution.component_safe_no, distribution.component_mean_no)
    )


def test_reliability_weights_apply_nbm_maturity_and_gfs_family_cap() -> None:
    completed = {
        "ecmwf_ifs": 30,
        "gfs013": 30,
        "gfs_seamless": 30,
        "nam": 30,
        "nbm": 5,
    }
    losses = {model: 1.0 for model in CANONICAL_MODEL_KEYS}

    weights = reliability_weights(completed_dates=completed, mean_log_loss=losses, bracket_count=6)

    assert weights["nbm"] == 0.0
    assert weights["gfs013"] + weights["gfs_seamless"] <= 0.45 + 1e-12
    assert round(sum(weights.values()), 12) == 1.0
    assert nbm_maturity_cap(9) == 0.0
    assert nbm_maturity_cap(20) == 0.10
    assert nbm_maturity_cap(45) == 0.20


def test_nbm_weight_is_capped_while_provisional() -> None:
    completed = {model: 30 for model in CANONICAL_MODEL_KEYS}
    completed["nbm"] = 20
    losses = {model: 1.0 for model in CANONICAL_MODEL_KEYS}

    weights = reliability_weights(completed_dates=completed, mean_log_loss=losses, bracket_count=6)

    assert weights["nbm"] <= 0.10 + 1e-12
    assert round(sum(weights.values()), 12) == 1.0


def test_mixture_probabilities_are_conservative_and_sum_mean_yes() -> None:
    distributions = {
        model: build_model_distribution(
            model_key=model,
            raw_live_state_f=72.0,
            observed_max_f=70.0,
            residual_library=_library(model),
            bracket_ids=BRACKETS,
            quantizer=_quantizer,
        )
        for model in CANONICAL_MODEL_KEYS
    }
    weights = {model: 0.2 for model in CANONICAL_MODEL_KEYS}

    probabilities = combine_model_distributions(distributions, weights)

    assert len(probabilities) == 3
    assert round(sum(row.posterior_mean_yes for row in probabilities), 12) == 1.0
    assert all(row.safe_yes <= row.posterior_mean_yes for row in probabilities)
    assert all(row.safe_no <= row.posterior_mean_no for row in probabilities)
    assert probabilities[0].component_probabilities.keys() == set(CANONICAL_MODEL_KEYS)
    summary = forecast_report_summary(distributions, weights)
    assert summary["interval_10_f"] <= summary["median_corrected_point_f"]
    assert summary["median_corrected_point_f"] <= summary["interval_90_f"]
