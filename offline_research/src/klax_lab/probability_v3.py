"""Deterministic V3 probability and market-residual models.

All fitting inputs must come from a training partition selected by the caller.
The functions have no file, network, clock, market, or protected-data access.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, exp, isfinite, log
from typing import Iterable, Sequence

import numpy as np

from .domain import ContractBounds


def _probability(value: float, name: str = "probability") -> float:
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= float(value) <= 1:
        raise ValueError(f"{name} must be finite and inside 0..1")
    return float(value)


def probability_vector(values: Iterable[float], *, tolerance: float = 1e-10) -> tuple[float, ...]:
    result = tuple(_probability(value) for value in values)
    if not result or abs(sum(result) - 1.0) > tolerance:
        raise ValueError("Mutually exclusive contract probabilities must sum to one")
    return result


@dataclass(frozen=True)
class GaussianComponent:
    mean_f: float
    sd_f: float
    weight: float

    def __post_init__(self) -> None:
        if any(type(value) not in (int, float) or not isfinite(value)
               for value in (self.mean_f, self.sd_f, self.weight)):
            raise ValueError("Gaussian mixture parameters must be finite")
        if self.sd_f <= 0 or self.weight <= 0 or self.weight > 1:
            raise ValueError("Gaussian component standard deviation and weight must be positive")


@dataclass(frozen=True)
class GaussianMixtureDistribution:
    components: tuple[GaussianComponent, ...]

    def __post_init__(self) -> None:
        components = tuple(self.components)
        if not components or abs(sum(item.weight for item in components) - 1.0) > 1e-12:
            raise ValueError("Gaussian mixture weights must sum to one")
        object.__setattr__(self, "components", components)

    def probability(self, bounds: ContractBounds, side: str = "YES") -> float:
        if side not in {"YES", "NO"}:
            raise ValueError("side must be YES or NO")
        yes = sum(item.weight * bounds.probability(item.mean_f, item.sd_f)
                  for item in self.components)
        return yes if side == "YES" else 1.0 - yes

    def contract_vector(self, contracts: Sequence[ContractBounds]) -> tuple[float, ...]:
        return probability_vector(self.probability(bounds) for bounds in contracts)


@dataclass(frozen=True)
class IsotonicCalibrator:
    """Piecewise-constant monotone binary calibrator fitted by PAV."""

    upper_score_bounds: tuple[float, ...]
    fitted_probabilities: tuple[float, ...]
    training_rows: int

    def __post_init__(self) -> None:
        bounds = tuple(self.upper_score_bounds)
        values = tuple(self.fitted_probabilities)
        if not bounds or len(bounds) != len(values) or self.training_rows < len(bounds):
            raise ValueError("Invalid isotonic model shape")
        if any(not isfinite(value) for value in bounds) or any(
                right <= left for left, right in zip(bounds, bounds[1:])):
            raise ValueError("Isotonic score bounds must be finite and increasing")
        if any(_probability(value) != value for value in values) or any(
                right < left for left, right in zip(values, values[1:])):
            raise ValueError("Isotonic probabilities must be monotone")
        object.__setattr__(self, "upper_score_bounds", bounds)
        object.__setattr__(self, "fitted_probabilities", values)

    def predict(self, score: float) -> float:
        if type(score) not in (int, float) or not isfinite(score):
            raise ValueError("Calibration score must be finite")
        for upper, value in zip(self.upper_score_bounds, self.fitted_probabilities):
            if score <= upper:
                return value
        return self.fitted_probabilities[-1]


def fit_isotonic(scores: Sequence[float], outcomes: Sequence[int], *, minimum_rows: int = 20) -> IsotonicCalibrator:
    if len(scores) != len(outcomes) or len(scores) < minimum_rows:
        raise ValueError("Isotonic calibration has insufficient aligned training rows")
    pairs = []
    for score, outcome in zip(scores, outcomes):
        if type(score) not in (int, float) or not isfinite(score):
            raise ValueError("Isotonic score must be finite")
        if type(outcome) is not int or outcome not in (0, 1):
            raise ValueError("Isotonic outcome must be binary")
        pairs.append((float(score), outcome))
    pairs.sort()

    # Collapse tied scores before applying pooled-adjacent-violators.
    tied: list[list[float]] = []
    for score, outcome in pairs:
        if tied and tied[-1][1] == score:
            tied[-1][2] += 1
            tied[-1][3] += outcome
        else:
            tied.append([score, score, 1, outcome])  # lower, upper, count, successes
    blocks: list[list[float]] = []
    for block in tied:
        blocks.append(block)
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            if left[3] / left[2] <= right[3] / right[2]:
                break
            blocks[-2:] = [[left[0], right[1], left[2] + right[2], left[3] + right[3]]]
    return IsotonicCalibrator(
        tuple(block[1] for block in blocks),
        tuple(block[3] / block[2] for block in blocks),
        len(pairs),
    )


def fit_bracket_isotonic(
    raw_probability_rows: Sequence[Sequence[float]],
    observed_bracket_indices: Sequence[int],
    *,
    minimum_rows: int = 20,
) -> tuple[IsotonicCalibrator, ...]:
    if len(raw_probability_rows) != len(observed_bracket_indices) or len(raw_probability_rows) < minimum_rows:
        raise ValueError("Bracket calibration has insufficient aligned training rows")
    rows = [probability_vector(row) for row in raw_probability_rows]
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("Bracket probability rows have inconsistent widths")
    for index in observed_bracket_indices:
        if type(index) is not int or not 0 <= index < width:
            raise ValueError("Observed bracket index is outside the probability vector")
    return tuple(fit_isotonic(
        [row[index] for row in rows],
        [int(observed == index) for observed in observed_bracket_indices],
        minimum_rows=minimum_rows,
    ) for index in range(width))


def calibrate_bracket_vector(
    raw_probabilities: Sequence[float], calibrators: Sequence[IsotonicCalibrator]
) -> tuple[float, ...]:
    raw = probability_vector(raw_probabilities)
    if len(raw) != len(calibrators) or not calibrators:
        raise ValueError("One isotonic calibrator is required per bracket")
    adjusted = tuple(model.predict(value) for model, value in zip(calibrators, raw))
    total = sum(adjusted)
    if total <= 0:
        return raw
    return probability_vector(value / total for value in adjusted)


def _clip_logit(value: float) -> float:
    probability = min(1 - 1e-6, max(1e-6, _probability(value)))
    return log(probability / (1 - probability))


@dataclass(frozen=True)
class MarketResidualLogitModel:
    """Calibrate outcomes using market level and model-vs-market residual."""

    intercept: float
    market_logit_coefficient: float
    residual_logit_coefficient: float
    training_rows: int
    l2_penalty: float

    def __post_init__(self) -> None:
        if any(type(value) not in (int, float) or not isfinite(value) for value in (
                self.intercept, self.market_logit_coefficient,
                self.residual_logit_coefficient, self.l2_penalty)):
            raise ValueError("Market residual model parameters must be finite")
        if self.training_rows < 1 or self.l2_penalty < 0:
            raise ValueError("Invalid market residual fit metadata")

    def predict(self, model_probability: float, market_probability: float) -> float:
        market = _clip_logit(market_probability)
        residual = _clip_logit(model_probability) - market
        linear = self.intercept + self.market_logit_coefficient * market + self.residual_logit_coefficient * residual
        if linear >= 0:
            return 1.0 / (1.0 + exp(-linear))
        exponential = exp(linear)
        return exponential / (1.0 + exponential)


def fit_market_residual_logit(
    model_probabilities: Sequence[float],
    market_probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    minimum_rows: int = 30,
    l2_penalty: float = 1.0,
    maximum_iterations: int = 100,
    tolerance: float = 1e-10,
) -> MarketResidualLogitModel:
    """Fit a small regularized logit model by deterministic Newton steps."""
    count = len(outcomes)
    if len(model_probabilities) != count or len(market_probabilities) != count or count < minimum_rows:
        raise ValueError("Market residual model has insufficient aligned training rows")
    if type(l2_penalty) not in (int, float) or not isfinite(l2_penalty) or l2_penalty < 0:
        raise ValueError("l2_penalty must be finite and nonnegative")
    if type(maximum_iterations) is not int or not 1 <= maximum_iterations <= 1000:
        raise ValueError("maximum_iterations is invalid")
    rows, labels = [], []
    for model, market, outcome in zip(model_probabilities, market_probabilities, outcomes):
        market_logit = _clip_logit(market)
        rows.append((1.0, market_logit, _clip_logit(model) - market_logit))
        if type(outcome) is not int or outcome not in (0, 1):
            raise ValueError("Market residual outcome must be binary")
        labels.append(outcome)
    design = np.asarray(rows, dtype=float)
    target = np.asarray(labels, dtype=float)
    coefficients = np.zeros(3, dtype=float)
    penalty = np.diag([0.0, float(l2_penalty), float(l2_penalty)])
    for _ in range(maximum_iterations):
        linear = np.clip(design @ coefficients, -40, 40)
        predicted = 1.0 / (1.0 + np.exp(-linear))
        weights = np.maximum(predicted * (1 - predicted), 1e-9)
        gradient = design.T @ (target - predicted) - penalty @ coefficients
        information = design.T @ (design * weights[:, None]) + penalty
        try:
            step = np.linalg.solve(information, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(information) @ gradient
        coefficients += step
        if float(np.max(np.abs(step))) <= tolerance:
            break
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("Market residual fit did not produce finite coefficients")
    return MarketResidualLogitModel(
        float(coefficients[0]), float(coefficients[1]), float(coefficients[2]),
        count, float(l2_penalty),
    )


@dataclass(frozen=True)
class QuantileSettlementDistribution:
    """Piecewise-linear CDF with explicit finite zero/one tail anchors."""

    quantiles: tuple[float, ...]
    temperatures_f: tuple[float, ...]

    def __post_init__(self) -> None:
        quantiles, temperatures = tuple(self.quantiles), tuple(self.temperatures_f)
        if len(quantiles) < 3 or len(quantiles) != len(temperatures):
            raise ValueError("Quantile distribution needs at least three aligned points")
        if quantiles[0] != 0.0 or quantiles[-1] != 1.0:
            raise ValueError("Quantile distribution requires explicit 0 and 1 tail anchors")
        if any(type(value) not in (int, float) or not isfinite(value) for value in (*quantiles, *temperatures)):
            raise ValueError("Quantile distribution values must be finite")
        if any(right <= left for left, right in zip(quantiles, quantiles[1:])):
            raise ValueError("Quantiles must be strictly increasing")
        if any(right < left for left, right in zip(temperatures, temperatures[1:])):
            raise ValueError("Quantile temperatures must be nondecreasing")
        object.__setattr__(self, "quantiles", tuple(float(value) for value in quantiles))
        object.__setattr__(self, "temperatures_f", tuple(float(value) for value in temperatures))

    def cdf(self, temperature_f: float) -> float:
        if type(temperature_f) not in (int, float) or not isfinite(temperature_f):
            raise ValueError("CDF temperature must be finite")
        value = float(temperature_f)
        if value < self.temperatures_f[0]:
            return 0.0
        if value >= self.temperatures_f[-1]:
            return 1.0
        for index in range(1, len(self.temperatures_f)):
            left_t, right_t = self.temperatures_f[index - 1:index + 1]
            if value <= right_t:
                left_q, right_q = self.quantiles[index - 1:index + 1]
                if right_t == left_t:
                    return right_q
                fraction = (value - left_t) / (right_t - left_t)
                return left_q + fraction * (right_q - left_q)
        return 1.0

    def probability(self, bounds: ContractBounds, side: str = "YES") -> float:
        if side not in {"YES", "NO"}:
            raise ValueError("side must be YES or NO")
        lower, upper = bounds.integer_bounds()
        if lower is not None and upper is not None and lower > upper:
            yes = 0.0
        else:
            lower_cdf = 0.0 if lower is None else self.cdf(lower - 0.5)
            upper_cdf = 1.0 if upper is None else self.cdf(upper + 0.5)
            yes = min(1.0, max(0.0, upper_cdf - lower_cdf))
        return yes if side == "YES" else 1.0 - yes


@dataclass(frozen=True)
class BetaCalibrator:
    """Binary beta calibration with nonnegative shape coefficients."""

    intercept: float
    log_probability_coefficient: float
    log_complement_coefficient: float
    training_rows: int
    l2_penalty: float

    def __post_init__(self) -> None:
        if any(type(value) not in (int, float) or not isfinite(value) for value in (
                self.intercept, self.log_probability_coefficient,
                self.log_complement_coefficient, self.l2_penalty)):
            raise ValueError("Beta calibrator parameters must be finite")
        if (self.log_probability_coefficient < 0 or self.log_complement_coefficient < 0
                or self.training_rows < 1 or self.l2_penalty < 0):
            raise ValueError("Invalid beta calibrator shape or fit metadata")

    def predict(self, probability: float) -> float:
        p = min(1 - 1e-6, max(1e-6, _probability(probability)))
        linear = (self.intercept + self.log_probability_coefficient * log(p)
                  - self.log_complement_coefficient * log(1 - p))
        if linear >= 0:
            return 1.0 / (1.0 + exp(-linear))
        exponential = exp(linear)
        return exponential / (1.0 + exponential)


def fit_beta_calibrator(
    probabilities: Sequence[float], outcomes: Sequence[int], *,
    minimum_rows: int = 30, l2_penalty: float = 1.0,
    maximum_iterations: int = 100, tolerance: float = 1e-10,
) -> BetaCalibrator:
    if len(probabilities) != len(outcomes) or len(probabilities) < minimum_rows:
        raise ValueError("Beta calibration has insufficient aligned training rows")
    rows, labels = [], []
    for probability, outcome in zip(probabilities, outcomes):
        p = min(1 - 1e-6, max(1e-6, _probability(probability)))
        if type(outcome) is not int or outcome not in (0, 1):
            raise ValueError("Beta calibration outcome must be binary")
        rows.append((1.0, log(p), -log(1 - p)))
        labels.append(outcome)
    design = np.asarray(rows, dtype=float)
    target = np.asarray(labels, dtype=float)
    coefficients = np.asarray((0.0, 1.0, 1.0), dtype=float)
    penalty = np.diag([0.0, float(l2_penalty), float(l2_penalty)])
    for _ in range(maximum_iterations):
        linear = np.clip(design @ coefficients, -40, 40)
        predicted = 1.0 / (1.0 + np.exp(-linear))
        weights = np.maximum(predicted * (1 - predicted), 1e-9)
        gradient = design.T @ (target - predicted) - penalty @ coefficients
        information = design.T @ (design * weights[:, None]) + penalty
        try:
            step = np.linalg.solve(information, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(information) @ gradient
        coefficients += step
        coefficients[1:] = np.maximum(coefficients[1:], 0.0)
        if float(np.max(np.abs(step))) <= tolerance:
            break
    return BetaCalibrator(
        float(coefficients[0]), float(coefficients[1]), float(coefficients[2]),
        len(outcomes), float(l2_penalty),
    )


@dataclass(frozen=True)
class ConformalAbstention:
    """Split-conformal prediction-set rule used only as an abstention signal."""

    nonconformity_threshold: float
    miscoverage_rate: float
    calibration_rows: int

    def __post_init__(self) -> None:
        threshold = _probability(self.nonconformity_threshold, "nonconformity_threshold")
        alpha = _probability(self.miscoverage_rate, "miscoverage_rate")
        if not 0 < alpha < 1 or self.calibration_rows < 1:
            raise ValueError("Invalid conformal abstention metadata")
        object.__setattr__(self, "nonconformity_threshold", threshold)
        object.__setattr__(self, "miscoverage_rate", alpha)

    def prediction_set(self, probabilities: Sequence[float]) -> tuple[int, ...]:
        values = probability_vector(probabilities)
        minimum_probability = 1.0 - self.nonconformity_threshold
        return tuple(index for index, value in enumerate(values) if value >= minimum_probability)

    def should_abstain(self, probabilities: Sequence[float]) -> bool:
        return len(self.prediction_set(probabilities)) != 1


def fit_conformal_abstention(
    calibration_probabilities: Sequence[Sequence[float]],
    observed_indices: Sequence[int], *, miscoverage_rate: float = 0.10,
    minimum_rows: int = 30,
) -> ConformalAbstention:
    if len(calibration_probabilities) != len(observed_indices) or len(observed_indices) < minimum_rows:
        raise ValueError("Conformal calibration has insufficient aligned rows")
    alpha = _probability(miscoverage_rate, "miscoverage_rate")
    if not 0 < alpha < 1:
        raise ValueError("miscoverage_rate must lie strictly inside 0..1")
    scores = []
    for row, observed in zip(calibration_probabilities, observed_indices):
        values = probability_vector(row)
        if type(observed) is not int or not 0 <= observed < len(values):
            raise ValueError("Observed class index is outside its probability vector")
        scores.append(1.0 - values[observed])
    scores.sort()
    # Finite-sample split-conformal quantile, capped to the available rows.
    rank = min(len(scores), ceil((len(scores) + 1) * (1 - alpha)))
    return ConformalAbstention(scores[rank - 1], alpha, len(scores))
