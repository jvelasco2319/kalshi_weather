"""Deterministic temperature-distribution fits for V3 candidates.

The caller supplies frozen training matrices and targets.  This module has no
filesystem, clock, market, partition, or network interface.  It provides a
regularized location model plus registered Gaussian, quantile, and finite
mixture residual distributions; selection/calibration remain host-owned.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Mapping, Sequence

import numpy as np

from .domain import ContractBounds
from .probability_v3 import (
    GaussianComponent,
    GaussianMixtureDistribution,
    QuantileSettlementDistribution,
    probability_vector,
)


def _matrix(rows: Sequence[Sequence[float]], width: int | None = None) -> np.ndarray:
    try:
        matrix = np.asarray(rows, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("V3 forecast features must be a finite numeric matrix") from exc
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError("V3 forecast features must be a nonempty matrix")
    if width is not None and matrix.shape[1] != width:
        raise ValueError("V3 forecast feature width differs from the fitted model")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("V3 forecast features must be finite")
    return matrix


def _targets(values: Sequence[float], count: int) -> np.ndarray:
    try:
        targets = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("V3 temperature targets must be finite numeric values") from exc
    if targets.ndim != 1 or len(targets) != count or not np.all(np.isfinite(targets)):
        raise ValueError("V3 temperature targets must align with features and be finite")
    if np.any((targets < -100) | (targets > 150)):
        raise ValueError("V3 temperature target is outside a physical Fahrenheit range")
    return targets


@dataclass(frozen=True)
class RidgeLocationModel:
    coefficients: tuple[float, ...]
    intercept: float
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    training_rows: int
    l2_penalty: float

    def __post_init__(self) -> None:
        width = len(self.coefficients)
        if (width == 0 or len(self.feature_means) != width or len(self.feature_scales) != width
                or self.training_rows < 1 or self.l2_penalty < 0):
            raise ValueError("Invalid V3 ridge location model metadata")
        if any(not isfinite(value) for value in (
                *self.coefficients, self.intercept, *self.feature_means,
                *self.feature_scales, self.l2_penalty)):
            raise ValueError("V3 ridge location model must be finite")
        if any(value <= 0 for value in self.feature_scales):
            raise ValueError("V3 ridge feature scales must be positive")

    def predict(self, rows: Sequence[Sequence[float]]) -> tuple[float, ...]:
        matrix = _matrix(rows, len(self.coefficients))
        standardized = (
            matrix - np.asarray(self.feature_means)[None, :]
        ) / np.asarray(self.feature_scales)[None, :]
        predictions = self.intercept + standardized @ np.asarray(self.coefficients)
        if not np.all(np.isfinite(predictions)):
            raise ValueError("V3 ridge predictions became nonfinite")
        return tuple(float(value) for value in predictions)


def fit_ridge_location(
    features: Sequence[Sequence[float]], targets: Sequence[float], *,
    minimum_rows: int = 30, l2_penalty: float = 1.0,
) -> RidgeLocationModel:
    matrix = _matrix(features)
    values = _targets(targets, matrix.shape[0])
    if matrix.shape[0] < minimum_rows:
        raise ValueError("V3 ridge location fit has insufficient training rows")
    if (isinstance(l2_penalty, bool) or not isinstance(l2_penalty, (int, float))
            or not isfinite(l2_penalty) or l2_penalty < 0):
        raise ValueError("V3 ridge penalty must be finite and nonnegative")
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    if np.any(scales <= 1e-12):
        raise ValueError("V3 ridge fit received a constant feature")
    standardized = (matrix - means) / scales
    design = np.column_stack((np.ones(matrix.shape[0]), standardized))
    penalty = np.eye(design.shape[1]) * float(l2_penalty)
    penalty[0, 0] = 0.0
    try:
        coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ values)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.pinv(design.T @ design + penalty) @ design.T @ values
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("V3 ridge fit did not produce finite coefficients")
    return RidgeLocationModel(
        tuple(float(value) for value in coefficients[1:]), float(coefficients[0]),
        tuple(float(value) for value in means), tuple(float(value) for value in scales),
        matrix.shape[0], float(l2_penalty),
    )


@dataclass(frozen=True)
class ResidualDistribution:
    family: str
    quantiles: tuple[float, ...] = ()
    residual_values: tuple[float, ...] = ()
    component_means: tuple[float, ...] = ()
    component_sds: tuple[float, ...] = ()
    component_weights: tuple[float, ...] = ()
    training_rows: int = 0

    def __post_init__(self) -> None:
        if self.family not in {"gaussian_blend", "quantile_brackets", "gaussian_mixture"}:
            raise ValueError("Unsupported V3 residual distribution family")
        if self.training_rows < 2:
            raise ValueError("V3 residual distribution has insufficient rows")

    def distribution(self, location_f: float):
        if not isfinite(location_f):
            raise ValueError("V3 predicted location must be finite")
        if self.family == "quantile_brackets":
            return QuantileSettlementDistribution(
                self.quantiles,
                tuple(location_f + value for value in self.residual_values),
            )
        return GaussianMixtureDistribution(tuple(
            GaussianComponent(location_f + mean, sd, weight)
            for mean, sd, weight in zip(
                self.component_means, self.component_sds, self.component_weights,
            )
        ))


def _safe_sd(values: np.ndarray, *, floor: float = .50) -> float:
    if len(values) < 2:
        return floor
    return max(floor, float(np.std(values, ddof=1)))


def fit_residual_distribution(
    residuals: Sequence[float], family: str, *, minimum_rows: int = 30,
) -> ResidualDistribution:
    try:
        values = np.asarray(residuals, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("V3 residuals must be finite numeric values") from exc
    if values.ndim != 1 or len(values) < minimum_rows or not np.all(np.isfinite(values)):
        raise ValueError("V3 residual distribution has insufficient finite rows")
    if family == "quantile_brackets":
        levels = (0.0, .05, .25, .50, .75, .95, 1.0)
        quantiles = tuple(float(value) for value in np.quantile(values, levels))
        # Sampling ties may create equal temperature knots, which the CDF
        # supports.  Quantile levels remain strictly increasing.
        return ResidualDistribution(
            family, levels, quantiles, training_rows=len(values),
        )
    if family == "gaussian_blend":
        return ResidualDistribution(
            family, component_means=(float(np.mean(values)),),
            component_sds=(_safe_sd(values),), component_weights=(1.0,),
            training_rows=len(values),
        )
    if family != "gaussian_mixture":
        raise ValueError("Unsupported V3 residual distribution family")
    split = float(np.median(values))
    left, right = values[values <= split], values[values > split]
    if len(left) < 2 or len(right) < 2:
        # A degenerate empirical split is represented as two symmetric finite
        # components so the registered family remains explicit and inspectable.
        center, scale = float(np.mean(values)), _safe_sd(values)
        means = (center - scale / 2, center + scale / 2)
        sds, weights = (scale, scale), (.5, .5)
    else:
        means = (float(np.mean(left)), float(np.mean(right)))
        sds = (_safe_sd(left), _safe_sd(right))
        weights = (len(left) / len(values), len(right) / len(values))
    return ResidualDistribution(
        family, component_means=means, component_sds=sds,
        component_weights=weights, training_rows=len(values),
    )


@dataclass(frozen=True)
class FittedDistributionModel:
    location: RidgeLocationModel
    pooled: ResidualDistribution
    regimes: tuple[tuple[str, ResidualDistribution], ...] = ()
    minimum_regime_training_days: int = 0

    def _residual(self, regime: str | None) -> tuple[ResidualDistribution, bool]:
        if regime is not None:
            for name, model in self.regimes:
                if name == regime:
                    return model, False
        return self.pooled, regime is not None

    def contract_probabilities(
        self, features: Sequence[float], contracts: Sequence[ContractBounds], *,
        regime: str | None = None,
    ) -> tuple[tuple[float, ...], bool, float]:
        location = self.location.predict((features,))[0]
        residual, fallback = self._residual(regime)
        distribution = residual.distribution(location)
        probabilities = probability_vector(
            distribution.probability(bounds) for bounds in contracts
        )
        return probabilities, fallback, location


def fit_distribution_model(
    features: Sequence[Sequence[float]], targets: Sequence[float], *,
    family: str, regimes: Sequence[str] | None = None,
    minimum_rows: int = 30, minimum_regime_training_days: int = 30,
    l2_penalty: float = 1.0,
) -> FittedDistributionModel:
    location = fit_ridge_location(
        features, targets, minimum_rows=minimum_rows, l2_penalty=l2_penalty,
    )
    predicted = location.predict(features)
    residuals = [target - estimate for target, estimate in zip(targets, predicted)]
    pooled = fit_residual_distribution(residuals, family, minimum_rows=minimum_rows)
    fitted_regimes = []
    if regimes is not None:
        if len(regimes) != len(residuals):
            raise ValueError("V3 regime labels are not aligned with training rows")
        for regime in sorted(set(regimes)):
            indices = [index for index, value in enumerate(regimes) if value == regime]
            if len(indices) < minimum_regime_training_days:
                continue
            fitted_regimes.append((regime, fit_residual_distribution(
                [residuals[index] for index in indices], family,
                minimum_rows=minimum_regime_training_days,
            )))
    return FittedDistributionModel(
        location, pooled, tuple(fitted_regimes), minimum_regime_training_days,
    )
