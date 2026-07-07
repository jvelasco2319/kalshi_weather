from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone

from .market_implied import normalize_quote
from .types import MarketQuote, Side


@dataclass(frozen=True)
class PassiveOrder:
    candidate_id: str
    side: Side
    bracket_label: str
    limit_price_cents: int
    quantity: int
    created_ts: str


@dataclass(frozen=True)
class PassiveFillResult:
    filled: bool
    fill_price_cents: Optional[int] = None
    reason: str = ""


def check_passive_buy_fill(order: PassiveOrder, quote: MarketQuote) -> PassiveFillResult:
    """Conservative paper fill rule for passive BUY orders."""
    q = normalize_quote(quote)
    ask = q.ask_for(order.side)
    if ask is None:
        return PassiveFillResult(False, None, "missing_ask")
    if ask <= order.limit_price_cents:
        return PassiveFillResult(True, min(ask, order.limit_price_cents), "ask_crossed_limit")
    return PassiveFillResult(False, None, "not_crossed")


@dataclass(frozen=True)
class PassiveCancelResult:
    should_cancel: bool
    reason: str = ""


def should_cancel_passive_order(
    *,
    order: PassiveOrder,
    current_edge_cents: Optional[float],
    min_edge_cents: float,
    now_ts: Optional[str] = None,
    max_order_age_seconds: int = 300,
) -> PassiveCancelResult:
    if current_edge_cents is None:
        return PassiveCancelResult(True, "missing_edge")
    if current_edge_cents < min_edge_cents:
        return PassiveCancelResult(True, "edge_disappeared")
    if now_ts is not None and _age_seconds(order.created_ts, now_ts) > max_order_age_seconds:
        return PassiveCancelResult(True, "order_stale")
    return PassiveCancelResult(False, "keep_open")


def _age_seconds(start_ts: str, end_ts: str) -> int:
    start = _parse_ts(start_ts)
    end = _parse_ts(end_ts)
    return max(0, int((end - start).total_seconds()))


def _parse_ts(ts: str) -> datetime:
    cleaned = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
