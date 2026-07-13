from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Side = Literal["yes", "no"]
Action = Literal["buy", "sell"]


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lo_f: int | None
    hi_f: int | None

    def contains_integer_f(self, value: int) -> bool:
        if self.lo_f is not None and value < self.lo_f:
            return False
        if self.hi_f is not None and value > self.hi_f:
            return False
        return True


@dataclass(frozen=True)
class OrderbookTop:
    ticker: str
    yes_bid: Decimal | None
    no_bid: Decimal | None
    yes_ask: Decimal | None
    no_ask: Decimal | None

    @property
    def yes_spread(self) -> Decimal | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    @property
    def no_spread(self) -> Decimal | None:
        if self.no_bid is None or self.no_ask is None:
            return None
        return self.no_ask - self.no_bid


@dataclass(frozen=True)
class WeatherSnapshot:
    station_id: str
    timestamp_utc: datetime
    observed_high_so_far_f: float
    latest_observation_utc: datetime | None
    observation_count: int
    model_future_high_f: float | None = None
    model_details: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Prediction:
    ticker: str
    bracket: Bracket
    p_yes: float
    yes_edge: float | None
    no_edge: float | None


@dataclass(frozen=True)
class TradeSignal:
    ticker: str
    side: Side
    action: Action
    quantity: Decimal
    price: Decimal
    edge: Decimal
    reason: str
