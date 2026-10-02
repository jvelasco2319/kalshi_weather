"""Pure numerical six-regime classifier with an explicit pooled fallback."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

import numpy as np


WEATHER_REGIMES = (
    "persistent_marine_layer",
    "delayed_marine_layer_burnoff",
    "ordinary_sea_breeze",
    "offshore_or_santa_ana_flow",
    "frontal_or_precipitation",
    "heat_or_weak_gradient",
)
POOLED_FALLBACK = "pooled"


def _matrix(rows: Sequence[Sequence[float]], *, expected_width: int | None = None) -> np.ndarray:
    try:
        value = np.asarray(rows, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Regime features must be a finite numeric matrix") from exc
    if value.ndim != 2 or value.shape[0] == 0 or value.shape[1] < 2:
        raise ValueError("Regime classification requires rows with at least two features")
    if expected_width is not None and value.shape[1] != expected_width:
        raise ValueError("Regime feature width differs from the fitted model")
    if not np.all(np.isfinite(value)):
        raise ValueError("Regime features must be finite")
    return value


def _softmax(scores: np.ndarray) -> np.ndarray:
    shifted = scores - np.max(scores, axis=1, keepdims=True)
    exponential = np.exp(np.clip(shifted, -40.0, 0.0))
    totals = exponential.sum(axis=1, keepdims=True)
    if np.any(totals <= 0) or not np.all(np.isfinite(totals)):
        raise ValueError("Regime probability normalization failed")
    return exponential / totals


def _loss_and_gradient(design: np.ndarray, labels: np.ndarray, coefficients: np.ndarray,
                       intercepts: np.ndarray, l2_penalty: float
                       ) -> tuple[float, np.ndarray, np.ndarray]:
    probabilities = _softmax(design @ coefficients.T + intercepts[None, :])
    row_index = np.arange(design.shape[0])
    selected = np.maximum(probabilities[row_index, labels], 1e-12)
    loss = float(-np.mean(np.log(selected)) + .5 * l2_penalty * np.sum(coefficients ** 2))
    residual = probabilities
    residual[row_index, labels] -= 1.0
    gradient_coefficients = residual.T @ design / design.shape[0] + l2_penalty * coefficients
    gradient_intercepts = residual.mean(axis=0)
    return loss, gradient_coefficients, gradient_intercepts


@dataclass(frozen=True)
class RegimeAssignment:
    regime: str
    use_pooled_fallback: bool
    confidence: float
    probabilities: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.regime not in (*WEATHER_REGIMES, POOLED_FALLBACK):
            raise ValueError("Unknown V3 weather regime")
        if self.use_pooled_fallback != (self.regime == POOLED_FALLBACK):
            raise ValueError("Pooled fallback flag and regime disagree")
        if (type(self.confidence) not in (int, float) or not isfinite(self.confidence)
                or not 0 <= self.confidence <= 1 or len(self.probabilities) != len(WEATHER_REGIMES)
                or any(type(value) not in (int, float) or not isfinite(value) or value < 0
                       for value in self.probabilities)
                or abs(sum(self.probabilities) - 1.0) > 1e-10):
            raise ValueError("Invalid regime assignment probabilities")


@dataclass(frozen=True)
class SixRegimeClassifier:
    feature_names: tuple[str, ...]
    regimes: tuple[str, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    regime_counts: tuple[int, ...]
    training_rows: int
    minimum_confidence: float
    l2_penalty: float
    iterations: int
    final_loss: float
    deterministic_seed: int
    algorithm: str = "deterministic_multinomial_gradient_v1"

    def __post_init__(self) -> None:
        width = len(self.feature_names)
        if self.regimes != WEATHER_REGIMES or width < 2 or len(set(self.feature_names)) != width:
            raise ValueError("Classifier regimes or feature names differ from V3 registration")
        if (len(self.coefficients) != len(self.regimes)
                or any(len(row) != width for row in self.coefficients)
                or len(self.intercepts) != len(self.regimes)
                or len(self.feature_means) != width or len(self.feature_scales) != width
                or len(self.regime_counts) != len(self.regimes)):
            raise ValueError("Regime classifier dimensions are inconsistent")
        numeric = (*[value for row in self.coefficients for value in row], *self.intercepts,
                   *self.feature_means, *self.feature_scales, self.minimum_confidence,
                   self.l2_penalty, self.final_loss)
        if any(type(value) not in (int, float) or not isfinite(value) for value in numeric):
            raise ValueError("Regime classifier parameters must be finite")
        if (any(scale <= 0 for scale in self.feature_scales)
                or any(type(count) is not int or count < 1 for count in self.regime_counts)
                or sum(self.regime_counts) != self.training_rows
                or not 1 / len(self.regimes) < self.minimum_confidence <= 1
                or self.l2_penalty < 0 or type(self.iterations) is not int or self.iterations < 1
                or type(self.deterministic_seed) is not int or self.deterministic_seed < 0):
            raise ValueError("Regime classifier metadata are invalid")

    @property
    def raw_intercepts(self) -> tuple[float, ...]:
        """Intercepts paired with original-unit coefficients."""
        table = self.coefficient_table()
        return tuple(
            intercept - sum(mean * table[regime][feature]
                            for mean, feature in zip(self.feature_means, self.feature_names))
            for regime, intercept in zip(self.regimes, self.intercepts)
        )

    def coefficient_table(self) -> dict[str, dict[str, float]]:
        """Return original-unit coefficients by regime and feature."""
        return {
            regime: {
                feature: coefficient / scale
                for feature, coefficient, scale in zip(
                    self.feature_names, self.coefficients[index], self.feature_scales)
            }
            for index, regime in enumerate(self.regimes)
        }

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
        matrix = _matrix(rows, expected_width=len(self.feature_names))
        standardized = ((matrix - np.asarray(self.feature_means, dtype=float))
                        / np.asarray(self.feature_scales, dtype=float))
        probabilities = _softmax(
            standardized @ np.asarray(self.coefficients, dtype=float).T
            + np.asarray(self.intercepts, dtype=float)[None, :])
        if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-12, rtol=0.0):
            raise ValueError("Regime probabilities do not conserve mass")
        return tuple(tuple(float(value) for value in row) for row in probabilities)

    def classify(self, rows: Sequence[Sequence[float]], *,
                 minimum_confidence: float | None = None) -> tuple[RegimeAssignment, ...]:
        threshold = self.minimum_confidence if minimum_confidence is None else minimum_confidence
        if (type(threshold) not in (int, float) or not isfinite(threshold)
                or not 1 / len(self.regimes) < threshold <= 1):
            raise ValueError("Regime fallback confidence threshold is invalid")
        assignments = []
        for row in self.predict_proba(rows):
            index = int(np.argmax(row))
            confidence = row[index]
            fallback = confidence < threshold
            assignments.append(RegimeAssignment(
                POOLED_FALLBACK if fallback else self.regimes[index],
                fallback,
                confidence,
                row,
            ))
        return tuple(assignments)


def fit_six_regime_classifier(
    training_features: Sequence[Sequence[float]],
    observed_regimes: Sequence[str],
    *,
    feature_names: Sequence[str] | None = None,
    minimum_rows_per_regime: int = 5,
    minimum_confidence: float = .50,
    l2_penalty: float = .03,
    maximum_iterations: int = 4000,
    tolerance: float = 1e-7,
    learning_rate: float = .20,
    deterministic_seed: int = 0,
) -> SixRegimeClassifier:
    """Fit a deterministic six-class softmax model from caller-supplied rows."""
    matrix = _matrix(training_features)
    row_count, width = matrix.shape
    if len(observed_regimes) != row_count:
        raise ValueError("Regime features and labels are not aligned")
    if type(minimum_rows_per_regime) is not int or minimum_rows_per_regime < 2:
        raise ValueError("minimum_rows_per_regime must be at least two")
    if (type(minimum_confidence) not in (int, float) or not isfinite(minimum_confidence)
            or not 1 / len(WEATHER_REGIMES) < minimum_confidence <= 1):
        raise ValueError("minimum_confidence must exceed random six-class confidence")
    if (type(maximum_iterations) is not int or not 1 <= maximum_iterations <= 20000
            or type(deterministic_seed) is not int or deterministic_seed < 0):
        raise ValueError("Regime iteration limit or seed is invalid")
    if (type(l2_penalty) not in (int, float) or not isfinite(l2_penalty)
            or l2_penalty < 0):
        raise ValueError("l2_penalty must be finite and nonnegative")
    for value, label in ((tolerance, "tolerance"), (learning_rate, "learning_rate")):
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive")

    names = (tuple(feature_names) if feature_names is not None
             else tuple(f"feature_{index}" for index in range(width)))
    if (len(names) != width or len(set(names)) != width
            or any(not isinstance(name, str) or not name.strip() for name in names)):
        raise ValueError("Regime feature names must be aligned and unique")
    regime_labels = tuple(observed_regimes)
    if any(not isinstance(value, str) for value in regime_labels):
        raise ValueError("Training contains an unregistered weather regime")
    unknown = set(regime_labels) - set(WEATHER_REGIMES)
    if unknown:
        raise ValueError("Training contains an unregistered weather regime")
    counts = tuple(regime_labels.count(regime) for regime in WEATHER_REGIMES)
    if any(count < minimum_rows_per_regime for count in counts):
        raise ValueError("Training has insufficient rows for one or more weather regimes")

    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    if np.any(scales <= 1e-12):
        raise ValueError("Regime training has an insufficient constant feature")
    design = (matrix - means) / scales
    label_lookup = {regime: index for index, regime in enumerate(WEATHER_REGIMES)}
    labels = np.asarray([label_lookup[value] for value in regime_labels], dtype=int)
    coefficients = np.zeros((len(WEATHER_REGIMES), width), dtype=float)
    intercepts = np.log(np.asarray(counts, dtype=float) / row_count)
    intercepts -= intercepts.mean()

    iterations = 0
    final_loss = float("inf")
    for iteration in range(1, maximum_iterations + 1):
        loss, gradient_coefficients, gradient_intercepts = _loss_and_gradient(
            design, labels, coefficients, intercepts, float(l2_penalty))
        if not isfinite(loss) or not np.all(np.isfinite(gradient_coefficients)) or not np.all(
                np.isfinite(gradient_intercepts)):
            raise ValueError("Regime optimization became nonfinite")
        gradient_norm = max(float(np.max(np.abs(gradient_coefficients))),
                            float(np.max(np.abs(gradient_intercepts))))
        iterations = iteration
        final_loss = loss
        if gradient_norm <= tolerance:
            break
        step = float(learning_rate)
        gradient_squared = (float(np.sum(gradient_coefficients ** 2))
                            + float(np.sum(gradient_intercepts ** 2)))
        accepted = False
        candidate_coefficients = coefficients
        candidate_intercepts = intercepts
        candidate_loss = loss
        for _ in range(30):
            next_coefficients = coefficients - step * gradient_coefficients
            next_intercepts = intercepts - step * gradient_intercepts
            # Fix the softmax reference invariance without favoring a regime.
            next_coefficients -= next_coefficients.mean(axis=0, keepdims=True)
            next_intercepts -= next_intercepts.mean()
            next_loss, _, _ = _loss_and_gradient(
                design, labels, next_coefficients, next_intercepts, float(l2_penalty))
            if isfinite(next_loss) and next_loss <= loss - 1e-6 * step * gradient_squared:
                candidate_coefficients = next_coefficients
                candidate_intercepts = next_intercepts
                candidate_loss = next_loss
                accepted = True
                break
            step *= .5
        if not accepted:
            if gradient_norm > 1e-4:
                raise ValueError("Regime optimization could not find a finite descent step")
            break
        change = max(float(np.max(np.abs(candidate_coefficients - coefficients))),
                     float(np.max(np.abs(candidate_intercepts - intercepts))))
        coefficients, intercepts, final_loss = (
            candidate_coefficients, candidate_intercepts, candidate_loss)
        if change <= tolerance and abs(loss - candidate_loss) <= tolerance:
            break

    probabilities = _softmax(design @ coefficients.T + intercepts[None, :])
    if not np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-12, rtol=0.0):
        raise ValueError("Fitted regime probabilities do not conserve mass")
    return SixRegimeClassifier(
        feature_names=names,
        regimes=WEATHER_REGIMES,
        coefficients=tuple(tuple(float(value) for value in row) for row in coefficients),
        intercepts=tuple(float(value) for value in intercepts),
        feature_means=tuple(float(value) for value in means),
        feature_scales=tuple(float(value) for value in scales),
        regime_counts=counts,
        training_rows=row_count,
        minimum_confidence=float(minimum_confidence),
        l2_penalty=float(l2_penalty),
        iterations=iterations,
        final_loss=float(final_loss),
        deterministic_seed=deterministic_seed,
    )
