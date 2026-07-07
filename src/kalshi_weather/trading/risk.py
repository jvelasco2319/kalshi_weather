from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    max_position_per_market: Decimal
    max_order_cost: Decimal
    max_total_exposure: Decimal | None = None
    max_contracts_per_event: Decimal | None = None
    max_contracts_per_bracket: Decimal | None = None
    max_daily_fake_loss: Decimal | None = None
    max_spread: Decimal | None = None


def check_buy_allowed(
    cash: Decimal,
    current_position: Decimal,
    quantity: Decimal,
    price: Decimal,
    limits: RiskLimits,
    current_total_exposure: Decimal = Decimal("0"),
    realized_pnl_today: Decimal = Decimal("0"),
) -> tuple[bool, str]:
    cost = quantity * price
    if price <= 0:
        return False, "missing or invalid ask"
    if cost > limits.max_order_cost:
        return False, f"order cost {cost} exceeds max_order_cost {limits.max_order_cost}"
    if cost > cash:
        return False, f"order cost {cost} exceeds cash {cash}"
    if current_position + quantity > limits.max_position_per_market:
        return False, "position limit exceeded"
    if limits.max_contracts_per_bracket is not None and current_position + quantity > limits.max_contracts_per_bracket:
        return False, "bracket contract limit exceeded"
    if limits.max_total_exposure is not None and current_total_exposure + cost > limits.max_total_exposure:
        return False, "total exposure limit exceeded"
    if limits.max_daily_fake_loss is not None and -realized_pnl_today >= limits.max_daily_fake_loss:
        return False, "daily fake loss limit reached"
    return True, "allowed"


def check_sell_allowed(current_position: Decimal, quantity: Decimal) -> tuple[bool, str]:
    if quantity > current_position:
        return False, "cannot sell more than current paper position"
    return True, "allowed"
