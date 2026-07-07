from __future__ import annotations

from decimal import Decimal
from typing import Any

from kalshi_weather.schemas import OrderbookTop

ONE = Decimal("1.0000")


def normalize_price(value: Any) -> Decimal:
    """Normalize Kalshi price values to decimal probability units."""
    price = Decimal(str(value))
    if price > ONE:
        return price / Decimal("100")
    return price


def best_bid(levels: list[list[Any]] | None, cents: bool = False) -> Decimal | None:
    if not levels:
        return None
    if cents:
        prices = [Decimal(str(level[0])) / Decimal("100") for level in levels if level]
    else:
        prices = [normalize_price(level[0]) for level in levels if level]
    return max(prices) if prices else None


def parse_orderbook_top(ticker: str, data: dict[str, Any]) -> OrderbookTop:
    """Parse Kalshi orderbook response into top-of-book prices.

    Supports the current fixed-point `orderbook_fp` response and older `orderbook`
    cent-style shapes if encountered.
    """
    if "orderbook_fp" in data:
        ob = data.get("orderbook_fp") or {}
        yes_bid = best_bid(ob.get("yes_dollars"))
        no_bid = best_bid(ob.get("no_dollars"))
    else:
        ob = data.get("orderbook") or {}
        yes_raw = ob.get("yes") or []
        no_raw = ob.get("no") or []
        yes_bid = best_bid(yes_raw, cents=True)
        no_bid = best_bid(no_raw, cents=True)

    yes_ask = ONE - no_bid if no_bid is not None else None
    no_ask = ONE - yes_bid if yes_bid is not None else None
    return OrderbookTop(ticker=ticker, yes_bid=yes_bid, no_bid=no_bid, yes_ask=yes_ask, no_ask=no_ask)
