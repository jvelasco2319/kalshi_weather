"""Pure numerical ordered-logistic bracket probabilities for V3.

The caller owns partitioning and supplies every training row.  This module has
no file, clock, process, protected-data, or network interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

import numpy as np


def _matrix(rows: Sequence[Sequence[float]], *, expected_width: int | None = None) -> np.ndarray:
    try:
        value = np.asarray(rows, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ordered-logistic features must be a finite numeric matrix") from exc
    if value.ndim != 2 or value.shape[0] == 0 or value.shape[1] == 0:
        raise ValueError("Ordered-logistic features must be a nonempty two-dimensional matrix")
    if expected_width is not None and value.shape[1] != expected_width:
        raise ValueError("Ordered-logistic feature width differs from the fitted model")
    if not np.all(np.isfinite(value)):
        raise ValueError("Ordered-logistic features must be finite")
    return value


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _probabilities(design: np.ndarray, coefficients: np.ndarray,
                   thresholds: np.ndarray) -> np.ndarray:
    cumulative = _sigmoid(thresholds[None, :] - (design @ coefficients)[:, None])
    result = np.empty((design.shape[0], thresholds.size + 1), dtype=float)
    result[:, 0] = cumulative[:, 0]
    if thresholds.size > 1:
        result[:, 1:-1] = cumulative[:, 1:] - cumulative[:, :-1]
    result[:, -1] = 1.0 - cumulative[:, -1]
    if np.any(result < -1e-10) or not np.all(np.isfinite(result)):
        raise ValueError("Ordered-logistic thresholds produced invalid bracket probabilities")
    result = np.maximum(result, 0.0)
    totals = result.sum(axis=1)
    if np.any(totals <= 0) or not np.all(np.isfinite(totals)):
        raise ValueError("Ordered-logistic probability normalization failed")
    return result / totals[:, None]


def _loss_and_gradient(design: np.ndarray, labels: np.ndarray, coefficients: np.ndarray,
                       thresholds: np.ndarray, l2_penalty: float
                       ) -> tuple[float, np.ndarray, np.ndarray]:
    linear = design @ coefficients
    cumulative = _sigmoid(thresholds[None, :] - linear[:, None])
    density = cumulative * (1.0 - cumulative)
    count = design.shape[0]
    row_indices = np.arange(count)
    probabilities = _probabilities(design, coefficients, thresholds)
    selected = np.maximum(probabilities[row_indices, labels], 1e-12)
    gradient_linear = np.zeros(count, dtype=float)
    gradient_thresholds = np.zeros(thresholds.size, dtype=float)
    final_class = thresholds.size

    first = labels == 0
    if np.any(first):
        ratios = density[first, 0] / selected[first]
        gradient_linear[first] = ratios
        gradient_thresholds[0] -= float(np.sum(ratios))
    last = labels == final_class
    if np.any(last):
        ratios = density[last, -1] / selected[last]
        gradient_linear[last] = -ratios
        gradient_thresholds[-1] += float(np.sum(ratios))
    middle = ~(first | last)
    if np.any(middle):
        middle_rows = row_indices[middle]
        middle_labels = labels[middle]
        lower_indices = middle_labels - 1
        upper_indices = middle_labels
        lower_slopes = density[middle_rows, lower_indices]
        upper_slopes = density[middle_rows, upper_indices]
        denominators = selected[middle]
        gradient_linear[middle] = (upper_slopes - lower_slopes) / denominators
        np.add.at(gradient_thresholds, lower_indices, lower_slopes / denominators)
        np.add.at(gradient_thresholds, upper_indices, -upper_slopes / denominators)

    gradient_coefficients = design.T @ gradient_linear / count + l2_penalty * coefficients
    gradient_thresholds /= count
    loss = float(-np.mean(np.log(selected))
                 + .5 * l2_penalty * np.dot(coefficients, coefficients))
    return loss, gradient_coefficients, gradient_thresholds


def _project_thresholds(thresholds: np.ndarray, minimum_gap: float = 1e-4) -> np.ndarray:
    result = thresholds.copy()
    for index in range(1, result.size):
        result[index] = max(result[index], result[index - 1] + minimum_gap)
    return result


@dataclass(frozen=True)
class OrderedLogisticBracketModel:
    """A proportional-odds model over ordered, exhaustive brackets."""

    feature_names: tuple[str, ...]
    bracket_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    thresholds: tuple[float, ...]
    feature_means: tuple[float, ...]
    feature_scales: tuple[float, ...]
    category_counts: tuple[int, ...]
    training_rows: int
    l2_penalty: float
    iterations: int
    final_loss: float
    deterministic_seed: int
    algorithm: str = "deterministic_projected_gradient_v1"

    def __post_init__(self) -> None:
        width = len(self.feature_names)
        classes = len(self.bracket_names)
        if width < 1 or classes < 3 or len(set(self.feature_names)) != width:
            raise ValueError("Ordered-logistic model has invalid feature or bracket names")
        if (len(self.coefficients) != width or len(self.feature_means) != width
                or len(self.feature_scales) != width or len(self.thresholds) != classes - 1
                or len(self.category_counts) != classes):
            raise ValueError("Ordered-logistic model dimensions are inconsistent")
        numeric = (*self.coefficients, *self.thresholds, *self.feature_means,
                   *self.feature_scales, self.l2_penalty, self.final_loss)
        if any(type(value) not in (int, float) or not isfinite(value) for value in numeric):
            raise ValueError("Ordered-logistic model parameters must be finite")
        if (any(scale <= 0 for scale in self.feature_scales)
                or any(right <= left for left, right in zip(self.thresholds, self.thresholds[1:]))
                or any(type(count) is not int or count < 1 for count in self.category_counts)
                or sum(self.category_counts) != self.training_rows
                or self.training_rows < classes or self.l2_penalty < 0
                or type(self.iterations) is not int or self.iterations < 1
                or type(self.deterministic_seed) is not int or self.deterministic_seed < 0):
            raise ValueError("Ordered-logistic model metadata are invalid")

    @property
    def raw_feature_coefficients(self) -> tuple[float, ...]:
        """Coefficients per original feature unit rather than standardized unit."""
        return tuple(coefficient / scale
                     for coefficient, scale in zip(self.coefficients, self.feature_scales))

    @property
    def raw_thresholds(self) -> tuple[float, ...]:
        """Thresholds on the original feature scale."""
        shift = sum(mean * coefficient for mean, coefficient in zip(
            self.feature_means, self.raw_feature_coefficients))
        return tuple(threshold + shift for threshold in self.thresholds)

    def coefficient_table(self) -> dict[str, float]:
        return dict(zip(self.feature_names, self.raw_feature_coefficients))

    def predict_proba(self, rows: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
        matrix = _matrix(rows, expected_width=len(self.feature_names))
        standardized = ((matrix - np.asarray(self.feature_means, dtype=float))
                        / np.asarray(self.feature_scales, dtype=float))
        values = _probabilities(standardized, np.asarray(self.coefficients, dtype=float),
                                np.asarray(self.thresholds, dtype=float))
        if not np.allclose(values.sum(axis=1), 1.0, atol=1e-12, rtol=0.0):
            raise ValueError("Ordered-logistic probabilities do not conserve mass")
        return tuple(tuple(float(item) for item in row) for row in values)

    def predict_bracket(self, rows: Sequence[Sequence[float]]) -> tuple[str, ...]:
        probabilities = self.predict_proba(rows)
        return tuple(self.bracket_names[int(np.argmax(row))] for row in probabilities)


def fit_ordered_logistic_brackets(
    training_features: Sequence[Sequence[float]],
    observed_bracket_indices: Sequence[int],
    *,
    feature_names: Sequence[str] | None = None,
    bracket_names: Sequence[str] | None = None,
    minimum_rows_per_bracket: int = 5,
    l2_penalty: float = .10,
    maximum_iterations: int = 4000,
    tolerance: float = 1e-7,
    learning_rate: float = .10,
    deterministic_seed: int = 0,
) -> OrderedLogisticBracketModel:
    """Fit an ordered logit using only the aligned rows supplied by the caller."""
    matrix = _matrix(training_features)
    row_count, width = matrix.shape
    if len(observed_bracket_indices) != row_count:
        raise ValueError("Ordered-logistic features and outcomes are not aligned")
    if type(minimum_rows_per_bracket) is not int or minimum_rows_per_bracket < 2:
        raise ValueError("minimum_rows_per_bracket must be at least two")
    if (type(maximum_iterations) is not int or not 1 <= maximum_iterations <= 20000
            or type(deterministic_seed) is not int or deterministic_seed < 0):
        raise ValueError("Ordered-logistic iteration limit or seed is invalid")
    if (type(l2_penalty) not in (int, float) or not isfinite(l2_penalty)
            or l2_penalty < 0):
        raise ValueError("l2_penalty must be finite and nonnegative")
    for value, label in ((tolerance, "tolerance"), (learning_rate, "learning_rate")):
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive")

    labels: list[int] = []
    for value in observed_bracket_indices:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or int(value) < 0:
            raise ValueError("Observed bracket indices must be nonnegative integers")
        labels.append(int(value))
    if not labels:
        raise ValueError("Ordered-logistic training rows are empty")
    class_count = len(bracket_names) if bracket_names is not None else max(labels) + 1
    if class_count < 3 or set(labels) != set(range(class_count)):
        raise ValueError("Ordered-logistic training must contain every ordered bracket")
    counts = tuple(labels.count(index) for index in range(class_count))
    if any(count < minimum_rows_per_bracket for count in counts):
        raise ValueError("Ordered-logistic training has insufficient rows in a bracket")

    names = (tuple(feature_names) if feature_names is not None
             else tuple(f"feature_{index}" for index in range(width)))
    brackets = (tuple(bracket_names) if bracket_names is not None
                else tuple(f"bracket_{index}" for index in range(class_count)))
    if (len(names) != width or len(set(names)) != width
            or any(not isinstance(name, str) or not name.strip() for name in names)):
        raise ValueError("Ordered-logistic feature names must be aligned and unique")
    if (len(brackets) != class_count or len(set(brackets)) != class_count
            or any(not isinstance(name, str) or not name.strip() for name in brackets)):
        raise ValueError("Ordered-logistic bracket names must be aligned and unique")

    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    if np.any(scales <= 1e-12):
        raise ValueError("Ordered-logistic training has an insufficient constant feature")
    standardized = (matrix - means) / scales
    label_array = np.asarray(labels, dtype=int)
    coefficients = np.zeros(width, dtype=float)
    cumulative_counts = np.cumsum(np.asarray(counts[:-1], dtype=float)) / row_count
    cumulative_counts = np.clip(cumulative_counts, 1e-4, 1 - 1e-4)
    thresholds = np.log(cumulative_counts / (1.0 - cumulative_counts))
    thresholds = _project_thresholds(thresholds)

    iterations = 0
    final_loss = float("inf")
    for iteration in range(1, maximum_iterations + 1):
        loss, gradient_coefficients, gradient_thresholds = _loss_and_gradient(
            standardized, label_array, coefficients, thresholds, float(l2_penalty))
        if not isfinite(loss) or not np.all(np.isfinite(gradient_coefficients)) or not np.all(
                np.isfinite(gradient_thresholds)):
            raise ValueError("Ordered-logistic optimization became nonfinite")
        gradient_norm = max(float(np.max(np.abs(gradient_coefficients))),
                            float(np.max(np.abs(gradient_thresholds))))
        iterations = iteration
        final_loss = loss
        if gradient_norm <= tolerance:
            break
        step = float(learning_rate)
        accepted = False
        candidate_coefficients = coefficients
        candidate_thresholds = thresholds
        candidate_loss = loss
        gradient_squared = (float(np.dot(gradient_coefficients, gradient_coefficients))
                            + float(np.dot(gradient_thresholds, gradient_thresholds)))
        for _ in range(30):
            next_coefficients = coefficients - step * gradient_coefficients
            next_thresholds = _project_thresholds(thresholds - step * gradient_thresholds)
            next_loss, _, _ = _loss_and_gradient(
                standardized, label_array, next_coefficients, next_thresholds,
                float(l2_penalty))
            if isfinite(next_loss) and next_loss <= loss - 1e-6 * step * gradient_squared:
                candidate_coefficients = next_coefficients
                candidate_thresholds = next_thresholds
                candidate_loss = next_loss
                accepted = True
                break
            step *= .5
        if not accepted:
            if gradient_norm > 1e-4:
                raise ValueError("Ordered-logistic optimization could not find a finite descent step")
            break
        change = max(float(np.max(np.abs(candidate_coefficients - coefficients))),
                     float(np.max(np.abs(candidate_thresholds - thresholds))))
        coefficients, thresholds, final_loss = (
            candidate_coefficients, candidate_thresholds, candidate_loss)
        if change <= tolerance and abs(loss - candidate_loss) <= tolerance:
            break

    _probabilities(standardized, coefficients, thresholds)
    return OrderedLogisticBracketModel(
        feature_names=names,
        bracket_names=brackets,
        coefficients=tuple(float(value) for value in coefficients),
        thresholds=tuple(float(value) for value in thresholds),
        feature_means=tuple(float(value) for value in means),
        feature_scales=tuple(float(value) for value in scales),
        category_counts=counts,
        training_rows=row_count,
        l2_penalty=float(l2_penalty),
        iterations=iterations,
        final_loss=float(final_loss),
        deterministic_seed=deterministic_seed,
    )
