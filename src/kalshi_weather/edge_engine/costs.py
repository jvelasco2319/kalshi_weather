from __future__ import annotations

import math
from .types import CostConfig, OrderType


def _validate_price(price_cents: float) -> None:
    if price_cents <= 0 or price_cents >= 100:
        raise ValueError(f"price_cents must be between 0 and 100 exclusive, got {price_cents}")


def round_up_to_next_cent(dollars: float) -> float:
    """Round a dollar amount up to the next cent."""
    if dollars <= 0:
        return 0.0
    return math.ceil(dollars * 100.0 - 1e-12) / 100.0


def kalshi_fee_dollars(
    price_cents: float,
    contracts: int,
    *,
    rate: float = 0.07,
) -> float:
    """Calculate Kalshi-style trading fee in dollars.

    This implements the commonly documented formula:

        fee = round_up_to_next_cent(rate * C * P * (1-P))

    where P is the contract price in dollars and C is contracts.

    Verify the current fee schedule before relying on this in production.
    """
    _validate_price(price_cents)
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    p = price_cents / 100.0
    return round_up_to_next_cent(rate * contracts * p * (1.0 - p))


def fee_cents_per_contract(
    price_cents: float,
    contracts: int,
    order_type: OrderType,
    config: CostConfig,
) -> float:
    if not config.include_fees:
        return 0.0
    if order_type == OrderType.PASSIVE_LIMIT and config.maker_fee_enabled:
        rate = config.maker_fee_rate
    elif order_type == OrderType.PASSIVE_LIMIT and not config.maker_fee_enabled:
        return 0.0
    else:
        rate = config.taker_fee_rate
    fee_dollars = kalshi_fee_dollars(price_cents, contracts, rate=rate)
    return 100.0 * fee_dollars / contracts
