from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Iterable, Literal

ZERO = Decimal("0")
ONE = Decimal("1")
CENTICENT = Decimal("0.0001")

Role = Literal["maker", "taker"]
Side = Literal["yes", "no"]


@dataclass(frozen=True)
class FeeSchedule:
    version: str = "general_default_2026_07"
    taker_rate: Decimal = Decimal("0.07")
    maker_rate: Decimal = Decimal("0.0175")
    fee_multiplier: Decimal = Decimal("1")


@dataclass(frozen=True)
class TradeEconomics:
    side: Side
    probability: Decimal
    quantity: int
    price: Decimal
    role: Role
    fee: Decimal
    slippage: Decimal
    all_in_cost: Decimal
    expected_value: Decimal
    roi: Decimal


def dec(value: Decimal | float | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def ceil_increment(value: Decimal, increment: Decimal = CENTICENT) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def fee(
    *,
    quantity: int,
    price: Decimal | float | str,
    role: Role,
    schedule: FeeSchedule = FeeSchedule(),
) -> Decimal:
    count = dec(quantity)
    p = dec(price)
    if count <= 0 or not ZERO < p < ONE:
        raise ValueError("quantity and price must be positive")
    if role == "taker":
        rate = schedule.taker_rate
    elif role == "maker":
        rate = schedule.maker_rate
    else:
        raise ValueError("role must be maker or taker")
    position_cost = count * p
    raw_fee = schedule.fee_multiplier * rate * count * p * (ONE - p)
    return ceil_increment(position_cost + raw_fee) - position_cost


def trade_economics(
    *,
    side: Side,
    probability: Decimal | float | str,
    quantity: int,
    price: Decimal | float | str,
    role: Role,
    schedule: FeeSchedule = FeeSchedule(),
    slippage: Decimal | float | str = ZERO,
) -> TradeEconomics:
    q = dec(probability)
    p = dec(price)
    s = dec(slippage)
    if not ZERO <= q <= ONE:
        raise ValueError("probability must be between zero and one")
    f = fee(quantity=quantity, price=p, role=role, schedule=schedule)
    cost = dec(quantity) * p + f + s
    ev = dec(quantity) * q - cost
    return TradeEconomics(
        side=side,
        probability=q,
        quantity=quantity,
        price=p,
        role=role,
        fee=f,
        slippage=s,
        all_in_cost=cost,
        expected_value=ev,
        roi=ev / cost,
    )


def max_qualifying_price(
    *,
    probability: Decimal | float | str,
    quantity: int,
    role: Role,
    hurdle: Decimal | float | str,
    price_levels: Iterable[Decimal | float | str],
    schedule: FeeSchedule = FeeSchedule(),
    slippage: Decimal | float | str = ZERO,
) -> Decimal | None:
    threshold = dec(hurdle)
    passing: list[Decimal] = []
    for price in sorted({dec(level) for level in price_levels}):
        economics = trade_economics(
            side="yes",
            probability=probability,
            quantity=quantity,
            price=price,
            role=role,
            schedule=schedule,
            slippage=slippage,
        )
        if economics.roi >= threshold:
            passing.append(price)
    return max(passing) if passing else None


def whole_cent_price_grid() -> list[Decimal]:
    return [Decimal(cents) / Decimal("100") for cents in range(1, 100)]
