from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class ForecastOutcome:
    probability: float
    outcome: int


def brier_score(rows: Iterable[ForecastOutcome]) -> float:
    vals = [(r.probability - r.outcome) ** 2 for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def log_loss(rows: Iterable[ForecastOutcome], eps: float = 1e-9) -> float:
    losses = []
    for r in rows:
        p = min(1 - eps, max(eps, r.probability))
        losses.append(-(r.outcome * math.log(p) + (1 - r.outcome) * math.log(1 - p)))
    return sum(losses) / len(losses) if losses else 0.0


def bucket_reliability(rows: Iterable[ForecastOutcome], bucket_size: float = 0.1) -> list[dict[str, float]]:
    buckets: dict[int, list[ForecastOutcome]] = {}
    for r in rows:
        idx = min(int(r.probability / bucket_size), int(1 / bucket_size) - 1)
        buckets.setdefault(idx, []).append(r)
    out = []
    for idx, vals in sorted(buckets.items()):
        out.append({
            "bucket_low": idx * bucket_size,
            "bucket_high": (idx + 1) * bucket_size,
            "count": len(vals),
            "avg_probability": sum(v.probability for v in vals) / len(vals),
            "hit_rate": sum(v.outcome for v in vals) / len(vals),
        })
    return out
