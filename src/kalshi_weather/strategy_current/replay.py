from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Sequence

from kalshi_weather.strategy_current.decision_engine import TradeCandidate

ReplayEventType = Literal["forecast", "observation", "orderbook", "trade", "candle", "decision"]


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    event_type: ReplayEventType
    available_at_utc: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class ReplayReport:
    event_count: int
    forecast_event_count: int
    orderbook_event_count: int
    trade_event_count: int
    candle_event_count: int
    decision_event_count: int
    executable_simulation_count: int
    candle_only_executable: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "event_count": self.event_count,
            "forecast_event_count": self.forecast_event_count,
            "orderbook_event_count": self.orderbook_event_count,
            "trade_event_count": self.trade_event_count,
            "candle_event_count": self.candle_event_count,
            "decision_event_count": self.decision_event_count,
            "executable_simulation_count": self.executable_simulation_count,
            "candle_only_executable": self.candle_only_executable,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    count: Decimal


@dataclass(frozen=True)
class FillSimulation:
    filled_count: Decimal
    average_price: Decimal | None
    executable: bool
    reason: str | None = None


def chronological_replay(
    events: Sequence[ReplayEvent],
    *,
    allow_candle_execution: bool = False,
) -> ReplayReport:
    sorted_events = sorted(events, key=lambda event: (event.available_at_utc, event.event_id))
    counts = {event_type: 0 for event_type in ("forecast", "observation", "orderbook", "trade", "candle", "decision")}
    for event in sorted_events:
        counts[event.event_type] += 1
    if counts["candle"] and not counts["orderbook"] and not allow_candle_execution:
        notes = ("candles are analytics-only and cannot prove executable fills",)
    else:
        notes = ()
    executable_count = counts["orderbook"] + counts["trade"]
    return ReplayReport(
        event_count=len(sorted_events),
        forecast_event_count=counts["forecast"],
        orderbook_event_count=counts["orderbook"],
        trade_event_count=counts["trade"],
        candle_event_count=counts["candle"],
        decision_event_count=counts["decision"],
        executable_simulation_count=executable_count,
        candle_only_executable=bool(counts["candle"] and not counts["orderbook"] and allow_candle_execution),
        notes=notes,
    )


def simulate_taker_fill(
    candidate: TradeCandidate,
    depth: Sequence[DepthLevel],
) -> FillSimulation:
    remaining = Decimal(candidate.quantity)
    cost = Decimal("0")
    filled = Decimal("0")
    for level in sorted(depth, key=lambda item: item.price):
        if level.price > candidate.limit_price:
            continue
        take = min(remaining, level.count)
        cost += take * level.price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled <= 0:
        return FillSimulation(Decimal("0"), None, False, "NO_EXECUTABLE_DEPTH")
    return FillSimulation(
        filled_count=filled,
        average_price=cost / filled,
        executable=filled == Decimal(candidate.quantity),
        reason=None if filled == Decimal(candidate.quantity) else "PARTIAL_DEPTH",
    )


def simulate_maker_fill_from_book(
    candidate: TradeCandidate,
    *,
    synchronized_book: bool,
    latency_assumption_ms: int | None,
) -> FillSimulation:
    if not synchronized_book:
        return FillSimulation(Decimal("0"), None, False, "UNSYNCHRONIZED_BOOK")
    if latency_assumption_ms is None or latency_assumption_ms < 0:
        return FillSimulation(Decimal("0"), None, False, "MISSING_LATENCY_ASSUMPTION")
    return FillSimulation(
        filled_count=Decimal("0"),
        average_price=None,
        executable=False,
        reason="MAKER_FILL_REQUIRES_QUEUE_MODEL",
    )
