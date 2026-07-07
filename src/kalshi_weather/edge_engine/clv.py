from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class CLVSnapshot:
    entry_price_cents: float
    later_price_cents: float
    clv_cents: float


def compute_clv(entry_price_cents: float, later_side_price_cents: float) -> CLVSnapshot:
    """Closing-line value in side-price units.

    For BUY YES, use YES prices. For BUY NO, use NO prices.
    """
    return CLVSnapshot(
        entry_price_cents=entry_price_cents,
        later_price_cents=later_side_price_cents,
        clv_cents=later_side_price_cents - entry_price_cents,
    )


def compute_clv_series(entry_price_cents: float, samples: Dict[str, float]) -> Dict[str, float]:
    return {f"clv_{name}_cents": price - entry_price_cents for name, price in samples.items()}
