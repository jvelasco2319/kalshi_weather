from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    avg_predicted: float
    actual_rate: float
    difference: float


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have same length")
    if not probabilities:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities)


def log_loss(probabilities: Sequence[float], outcomes: Sequence[int], eps: float = 1e-6) -> float:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have same length")
    if not probabilities:
        return float("nan")
    total = 0.0
    for p, y in zip(probabilities, outcomes):
        p = min(1 - eps, max(eps, p))
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(probabilities)


def reliability_bins(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    bin_width: float = 0.10,
) -> List[CalibrationBin]:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have same length")
    bins: List[CalibrationBin] = []
    n_bins = int(round(1.0 / bin_width))
    for i in range(n_bins):
        lower = i * bin_width
        upper = 1.0 if i == n_bins - 1 else (i + 1) * bin_width
        idx = [j for j, p in enumerate(probabilities) if (p >= lower and (p < upper or (i == n_bins - 1 and p <= upper)))]
        if not idx:
            bins.append(CalibrationBin(lower, upper, 0, float("nan"), float("nan"), float("nan")))
            continue
        avg_p = sum(probabilities[j] for j in idx) / len(idx)
        actual = sum(outcomes[j] for j in idx) / len(idx)
        bins.append(CalibrationBin(lower, upper, len(idx), avg_p, actual, actual - avg_p))
    return bins
