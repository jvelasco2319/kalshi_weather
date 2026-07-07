from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, Optional

from .types import MarketQuote, canonicalize_label


def _valid_cents(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if value < 0 or value > 100:
        raise ValueError(f"Invalid cents value: {value}")
    return int(value)


def normalize_quote(quote: MarketQuote) -> MarketQuote:
    """Fill missing implied asks from opposite-side bids.

    Kalshi binary books commonly expose YES bids and NO bids. The ask can be
    inferred from the opposite-side bid:

        YES ask = 100 - NO bid
        NO ask  = 100 - YES bid
    """
    yes_bid = _valid_cents(quote.yes_bid_cents)
    yes_ask = _valid_cents(quote.yes_ask_cents)
    no_bid = _valid_cents(quote.no_bid_cents)
    no_ask = _valid_cents(quote.no_ask_cents)

    if yes_ask is None and no_bid is not None:
        yes_ask = 100 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 100 - yes_bid
    if yes_bid is None and no_ask is not None:
        yes_bid = 100 - no_ask
    if no_bid is None and yes_ask is not None:
        no_bid = 100 - yes_ask

    return replace(
        quote,
        bracket_label=canonicalize_label(quote.bracket_label),
        yes_bid_cents=yes_bid,
        yes_ask_cents=yes_ask,
        no_bid_cents=no_bid,
        no_ask_cents=no_ask,
    )


def yes_mid_cents(quote: MarketQuote) -> Optional[float]:
    q = normalize_quote(quote)
    if q.yes_bid_cents is not None and q.yes_ask_cents is not None:
        return (q.yes_bid_cents + q.yes_ask_cents) / 2.0
    if q.yes_bid_cents is not None:
        return float(q.yes_bid_cents)
    if q.yes_ask_cents is not None:
        return float(q.yes_ask_cents)
    return None


def normalize_market_probabilities(quotes: Iterable[MarketQuote]) -> Dict[str, float]:
    """Normalize YES mid prices across mutually exclusive brackets."""
    mids: Dict[str, float] = {}
    for quote in quotes:
        q = normalize_quote(quote)
        mid = yes_mid_cents(q)
        if mid is not None and mid > 0:
            mids[q.bracket_label] = mid / 100.0
    total = sum(mids.values())
    if total <= 0:
        return {label: 0.0 for label in mids}
    return {label: value / total for label, value in mids.items()}
