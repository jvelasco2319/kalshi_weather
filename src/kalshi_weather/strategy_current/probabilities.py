from __future__ import annotations

from dataclasses import dataclass
from math import exp, log
from statistics import median
from typing import Callable, Mapping, Sequence

from scipy.stats import beta as beta_distribution

from kalshi_weather.strategy_current.config import StrategyConfig, load_strategy_config
from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS, strategy_model_by_key
from kalshi_weather.strategy_current.residuals import ResidualLibrary


@dataclass(frozen=True)
class ModelDistribution:
    model_key: str
    bracket_ids: tuple[str, ...]
    bracket_counts: tuple[float, ...]
    effective_sample_size: float
    corrected_point_f: float
    component_mean_yes: tuple[float, ...]
    component_safe_yes: tuple[float, ...]
    component_mean_no: tuple[float, ...]
    component_safe_no: tuple[float, ...]


@dataclass(frozen=True)
class BracketProbability:
    bracket_id: str
    posterior_mean_yes: float
    safe_yes: float
    posterior_mean_no: float
    safe_no: float
    effective_sample_size: float
    component_probabilities: dict[str, float]


def build_model_distribution(
    *,
    model_key: str,
    raw_live_state_f: float,
    observed_max_f: float | None,
    residual_library: ResidualLibrary,
    bracket_ids: Sequence[str],
    quantizer: Callable[[float], int],
    alpha: float = 0.5,
    safe_probability_quantile: float = 0.10,
) -> ModelDistribution:
    if model_key != residual_library.model_key:
        raise ValueError("model_key must match residual library")
    if not bracket_ids:
        raise ValueError("bracket ids are required")
    counts = [0.0] * len(bracket_ids)
    for record, weight in zip(
        residual_library.records,
        residual_library.normalized_weights,
        strict=True,
    ):
        physical = float(raw_live_state_f) + record.residual_f
        if observed_max_f is not None:
            physical = max(physical, float(observed_max_f))
        official = physical + record.settlement_gap_f
        bracket_index = int(quantizer(official))
        if not 0 <= bracket_index < len(bracket_ids):
            raise ValueError("quantizer returned out-of-range bracket index")
        counts[bracket_index] += weight * residual_library.effective_sample_size
    mean_yes, safe_yes, mean_no, safe_no = dirichlet_bounds(
        counts,
        alpha=alpha,
        q=safe_probability_quantile,
    )
    corrected = float(raw_live_state_f) + residual_library.weighted_median_residual_f
    if observed_max_f is not None:
        corrected = max(corrected, float(observed_max_f))
    return ModelDistribution(
        model_key=model_key,
        bracket_ids=tuple(bracket_ids),
        bracket_counts=tuple(counts),
        effective_sample_size=residual_library.effective_sample_size,
        corrected_point_f=corrected,
        component_mean_yes=mean_yes,
        component_safe_yes=safe_yes,
        component_mean_no=mean_no,
        component_safe_no=safe_no,
    )


def dirichlet_bounds(
    counts: Sequence[float],
    *,
    alpha: float = 0.5,
    q: float = 0.10,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if len(counts) < 2 or alpha <= 0 or not 0 < q < 0.5:
        raise ValueError("invalid Dirichlet parameters")
    if any(count < 0 for count in counts):
        raise ValueError("counts must be nonnegative")
    evidence = float(sum(counts))
    if evidence <= 0:
        raise ValueError("positive evidence is required")
    bracket_count = len(counts)
    denominator = evidence + bracket_count * alpha
    mean_yes: list[float] = []
    safe_yes: list[float] = []
    mean_no: list[float] = []
    safe_no: list[float] = []
    for count in counts:
        yes_alpha = float(count) + alpha
        no_alpha = evidence - float(count) + (bracket_count - 1) * alpha
        mean_yes.append(yes_alpha / denominator)
        safe_yes.append(float(beta_distribution.ppf(q, yes_alpha, no_alpha)))
        mean_no.append(no_alpha / denominator)
        safe_no.append(float(beta_distribution.ppf(q, no_alpha, yes_alpha)))
    return tuple(mean_yes), tuple(safe_yes), tuple(mean_no), tuple(safe_no)


def nbm_maturity_cap(completed_dates: int) -> float:
    if completed_dates < 10:
        return 0.0
    if completed_dates < 30:
        return 0.10
    if completed_dates < 60:
        return 0.20
    return 0.25


def reliability_weights(
    *,
    completed_dates: Mapping[str, int],
    mean_log_loss: Mapping[str, float | None],
    bracket_count: int,
    config: StrategyConfig | None = None,
) -> dict[str, float]:
    strategy_config = config or load_strategy_config()
    eligible: dict[str, bool] = {}
    shrunk_scores: dict[str, float] = {}
    uniform_loss = log(bracket_count)
    for model_key in CANONICAL_MODEL_KEYS:
        completed = int(completed_dates.get(model_key, 0))
        eligible[model_key] = completed >= (10 if model_key == "nbm" else 30)
        observed = mean_log_loss.get(model_key)
        if observed is None or completed <= 0:
            observed = uniform_loss
        shrunk_scores[model_key] = (completed * float(observed) + 30.0 * uniform_loss) / (
            completed + 30.0
        )
    if sum(1 for value in eligible.values() if value) < strategy_config.minimum_feeds_for_trade_probability:
        raise ValueError("at least four eligible feeds are required")
    families = {
        strategy_model_by_key(model_key).family
        for model_key, is_eligible in eligible.items()
        if is_eligible
    }
    if len(families) < strategy_config.minimum_independence_families:
        raise ValueError("at least three eligible families are required")

    best_score = min(shrunk_scores[model] for model, is_eligible in eligible.items() if is_eligible)
    raw = {
        model: (
            float(strategy_config.prior_weights[model])
            * exp(-(shrunk_scores[model] - best_score))
            if eligible[model]
            else 0.0
        )
        for model in CANONICAL_MODEL_KEYS
    }
    caps = {model: float(strategy_config.individual_cap) for model in CANONICAL_MODEL_KEYS}
    caps["nbm"] = min(caps["nbm"], nbm_maturity_cap(int(completed_dates.get("nbm", 0))))
    weights = _waterfill(raw, caps)
    return _apply_family_caps(weights, raw, caps, strategy_config)


def combine_model_distributions(
    distributions: Mapping[str, ModelDistribution],
    weights: Mapping[str, float],
    *,
    alpha: float = 0.5,
    safe_probability_quantile: float = 0.10,
) -> list[BracketProbability]:
    positive_models = [model for model in CANONICAL_MODEL_KEYS if weights.get(model, 0.0) > 0]
    if not positive_models:
        raise ValueError("positive model weight is required")
    bracket_ids = distributions[positive_models[0]].bracket_ids
    if any(distributions[model].bracket_ids != bracket_ids for model in positive_models):
        raise ValueError("all model distributions must use the same bracket ids")
    mixture_mean = [0.0] * len(bracket_ids)
    weighted_component_safe_yes = [0.0] * len(bracket_ids)
    weighted_component_safe_no = [0.0] * len(bracket_ids)
    component_by_bracket = [dict[str, float]() for _ in bracket_ids]
    for model in positive_models:
        distribution = distributions[model]
        weight = float(weights[model])
        for index, mean in enumerate(distribution.component_mean_yes):
            mixture_mean[index] += weight * mean
            weighted_component_safe_yes[index] += weight * distribution.component_safe_yes[index]
            weighted_component_safe_no[index] += weight * distribution.component_safe_no[index]
            component_by_bracket[index][model] = mean
    n_mix = min(distributions[model].effective_sample_size for model in positive_models)
    mix_counts = [value * n_mix for value in mixture_mean]
    mean_yes, safe_mix_yes, mean_no, safe_mix_no = dirichlet_bounds(
        mix_counts,
        alpha=alpha,
        q=safe_probability_quantile,
    )
    return [
        BracketProbability(
            bracket_id=bracket_id,
            posterior_mean_yes=mean_yes[index],
            safe_yes=min(safe_mix_yes[index], weighted_component_safe_yes[index]),
            posterior_mean_no=mean_no[index],
            safe_no=min(safe_mix_no[index], weighted_component_safe_no[index]),
            effective_sample_size=n_mix,
            component_probabilities=component_by_bracket[index],
        )
        for index, bracket_id in enumerate(bracket_ids)
    ]


def forecast_report_summary(
    distributions: Mapping[str, ModelDistribution],
    weights: Mapping[str, float],
) -> dict[str, float]:
    points = [
        distributions[model].corrected_point_f
        for model in CANONICAL_MODEL_KEYS
        if weights.get(model, 0.0) > 0
    ]
    if not points:
        raise ValueError("positive model points are required")
    weighted_mean = sum(
        distributions[model].corrected_point_f * float(weights.get(model, 0.0))
        for model in CANONICAL_MODEL_KEYS
    )
    ordered = sorted(points)
    lower_index = max(0, int(0.10 * (len(ordered) - 1)))
    upper_index = min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))
    return {
        "weighted_corrected_point_f": weighted_mean,
        "median_corrected_point_f": float(median(points)),
        "interval_10_f": ordered[lower_index],
        "interval_90_f": ordered[upper_index],
    }


def _waterfill(raw: Mapping[str, float], caps: Mapping[str, float], total: float = 1.0) -> dict[str, float]:
    active = {key for key, value in raw.items() if value > 0 and caps.get(key, 1.0) > 0}
    output = {key: 0.0 for key in raw}
    remaining = total
    while active:
        raw_total = sum(raw[key] for key in active)
        proposed = {
            key: remaining * raw[key] / raw_total if raw_total > 0 else remaining / len(active)
            for key in active
        }
        capped = [key for key in active if proposed[key] > caps.get(key, 1.0) + 1e-12]
        if not capped:
            for key in active:
                output[key] = proposed[key]
            return output
        for key in capped:
            output[key] = caps.get(key, 1.0)
            remaining -= output[key]
            active.remove(key)
    if remaining > 1e-9:
        raise ValueError("weight caps cannot allocate total weight")
    return output


def _apply_family_caps(
    weights: dict[str, float],
    raw: Mapping[str, float],
    caps: Mapping[str, float],
    config: StrategyConfig,
) -> dict[str, float]:
    adjusted = dict(weights)
    for family, cap_value in config.family_caps.items():
        family_models = [
            model
            for model in CANONICAL_MODEL_KEYS
            if strategy_model_by_key(model).family == family and adjusted.get(model, 0.0) > 0
        ]
        family_total = sum(adjusted[model] for model in family_models)
        family_cap = float(cap_value)
        if family_total <= family_cap + 1e-12:
            continue
        scale = family_cap / family_total
        excess = family_total - family_cap
        for model in family_models:
            adjusted[model] *= scale
        outside = [
            model
            for model in CANONICAL_MODEL_KEYS
            if model not in family_models and adjusted.get(model, 0.0) > 0
        ]
        capacity = {model: caps[model] - adjusted[model] for model in outside}
        if sum(max(0.0, value) for value in capacity.values()) + 1e-12 < excess:
            raise ValueError("family cap redistribution is infeasible")
        increments = _waterfill({model: raw[model] for model in outside}, capacity, total=excess)
        for model, increment in increments.items():
            adjusted[model] += increment
    total = sum(adjusted.values())
    if total <= 0:
        raise ValueError("positive total weight required")
    return {model: adjusted.get(model, 0.0) / total for model in CANONICAL_MODEL_KEYS}
