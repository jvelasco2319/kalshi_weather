from __future__ import annotations

import numpy as np


def brier_score(probs: list[float], outcomes: list[int]) -> float:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if len(p) == 0:
        raise ValueError("No predictions supplied")
    return float(np.mean((p - y) ** 2))


def log_loss_binary(probs: list[float], outcomes: list[int], eps: float = 1e-6) -> float:
    p = np.clip(np.asarray(probs, dtype=float), eps, 1 - eps)
    y = np.asarray(outcomes, dtype=float)
    if len(p) == 0:
        raise ValueError("No predictions supplied")
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration_buckets(
    probs: list[float],
    outcomes: list[int],
    bucket_count: int = 10,
) -> list[dict[str, float | int]]:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if len(p) == 0:
        raise ValueError("No predictions supplied")
    edges = np.linspace(0.0, 1.0, bucket_count + 1)
    buckets: list[dict[str, float | int]] = []
    for i in range(bucket_count):
        lo = edges[i]
        hi = edges[i + 1]
        mask = (p >= lo) & (p <= hi if i == bucket_count - 1 else p < hi)
        if not np.any(mask):
            continue
        buckets.append(
            {
                "bucket_min": float(lo),
                "bucket_max": float(hi),
                "count": int(np.sum(mask)),
                "avg_probability": float(np.mean(p[mask])),
                "observed_rate": float(np.mean(y[mask])),
            }
        )
    return buckets
