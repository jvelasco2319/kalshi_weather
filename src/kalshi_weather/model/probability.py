from __future__ import annotations

import numpy as np

from kalshi_weather.schemas import Bracket


def settlement_high_samples(
    future_high_center_f: float,
    observed_high_so_far_f: float,
    residual_sigma_f: float = 1.0,
    sample_count: int = 20_000,
    seed: int | None = 7,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    future = rng.normal(loc=future_high_center_f, scale=residual_sigma_f, size=sample_count)
    observed_floor = observed_high_so_far_f if np.isfinite(observed_high_so_far_f) else -np.inf
    return np.maximum(future, observed_floor)


def bracket_probability(samples_f: np.ndarray, bracket: Bracket) -> float:
    lower = -np.inf if bracket.lo_f is None else bracket.lo_f - 0.5
    upper = np.inf if bracket.hi_f is None else bracket.hi_f + 0.5
    return float(np.mean((samples_f >= lower) & (samples_f < upper)))


def bracket_probabilities(samples_f: np.ndarray, brackets: list[Bracket]) -> dict[str, float]:
    return {bracket.ticker: bracket_probability(samples_f, bracket) for bracket in brackets}


def normalize_probabilities(probs: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in probs.values() if v >= 0)
    if total <= 0:
        return probs
    return {k: float(v / total) for k, v in probs.items()}
