from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class MarketQuote:
    label: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None = None
    no_ask: float | None = None

@dataclass(frozen=True)
class MarketProbability:
    label: str
    raw_mid_probability: float
    normalized_probability: float

@dataclass(frozen=True)
class MarketDistribution:
    probabilities: list[MarketProbability]
    raw_sum: float
    normalized_sum: float
    overround_or_gap: float


def mid_probability(q: MarketQuote) -> float:
    """Return a YES probability from quoted cents.

    Uses yes bid/ask when available; falls back to implied ask/bid from NO.
    """
    yes_bid = q.yes_bid
    yes_ask = q.yes_ask
    if yes_ask is None and q.no_bid is not None:
        yes_ask = 100 - q.no_bid
    if yes_bid is None and q.no_ask is not None:
        yes_bid = 100 - q.no_ask
    vals = [v for v in (yes_bid, yes_ask) if v is not None]
    if not vals:
        return 0.0
    return sum(vals) / len(vals) / 100.0


def normalize_market_distribution(quotes: Iterable[MarketQuote]) -> MarketDistribution:
    raw = [(q.label, max(0.0, mid_probability(q))) for q in quotes]
    total = sum(p for _, p in raw)
    if total <= 0:
        n = len(raw) or 1
        probs = [MarketProbability(label, 0.0, 1 / n) for label, _ in raw]
        return MarketDistribution(probs, 0.0, 1.0, -1.0)
    probs = [MarketProbability(label, p, p / total) for label, p in raw]
    return MarketDistribution(probs, total, sum(p.normalized_probability for p in probs), total - 1.0)
