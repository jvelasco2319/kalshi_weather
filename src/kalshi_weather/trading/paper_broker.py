from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from kalshi_weather.schemas import Action, Side, TradeSignal
from kalshi_weather.trading.risk import RiskLimits, check_buy_allowed, check_sell_allowed


@dataclass
class PaperFill:
    timestamp_utc: datetime
    ticker: str
    side: Side
    action: Action
    quantity: Decimal
    price: Decimal
    fee: Decimal
    cash_after: Decimal
    reason: str
    realized_pnl: Decimal = Decimal("0")
    model_probability: Decimal | None = None
    entry_edge: Decimal | None = None
    market_bid: Decimal | None = None
    market_ask: Decimal | None = None
    yes_bid: Decimal | None = None
    yes_ask: Decimal | None = None
    no_bid: Decimal | None = None
    no_ask: Decimal | None = None
    model_version: str | None = None
    asof_utc: datetime | None = None
    snapshot_id: int | None = None
    bracket_label: str | None = None
    market_date: str | None = None
    best_side: str | None = None
    best_edge: Decimal | None = None
    total_hurdle: Decimal | None = None
    observed_high_so_far_f: float | None = None
    model_future_high_f: float | None = None
    prediction_id: int | None = None
    is_demo: bool = False


@dataclass
class PaperBroker:
    cash: Decimal
    limits: RiskLimits
    positions: dict[tuple[str, Side], Decimal] = field(default_factory=dict)
    cost_basis: dict[tuple[str, Side], Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    fills: list[PaperFill] = field(default_factory=list)

    def position(self, ticker: str, side: Side) -> Decimal:
        return self.positions.get((ticker, side), Decimal("0"))

    def average_cost(self, ticker: str, side: Side) -> Decimal:
        return self.cost_basis.get((ticker, side), Decimal("0"))

    def execute_signal(
        self,
        signal: TradeSignal,
        fee: Decimal = Decimal("0"),
        snapshot_id: int | None = None,
    ) -> PaperFill | None:
        if signal.action == "buy":
            return self.buy(
                signal.ticker,
                signal.side,
                signal.quantity,
                signal.price,
                fee,
                signal.reason,
                snapshot_id=snapshot_id,
                entry_edge=signal.edge,
            )
        return self.sell(
            signal.ticker,
            signal.side,
            signal.quantity,
            signal.price,
            fee,
            signal.reason,
            snapshot_id=snapshot_id,
        )

    def buy(
        self,
        ticker: str,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
        reason: str = "",
        snapshot_id: int | None = None,
        model_probability: Decimal | None = None,
        entry_edge: Decimal | None = None,
        yes_bid: Decimal | None = None,
        yes_ask: Decimal | None = None,
        no_bid: Decimal | None = None,
        no_ask: Decimal | None = None,
        model_version: str | None = None,
        asof_utc: datetime | None = None,
    ) -> PaperFill | None:
        ok, _ = check_buy_allowed(
            cash=self.cash,
            current_position=self.position(ticker, side),
            quantity=quantity,
            price=price,
            limits=self.limits,
            current_total_exposure=self.total_exposure(),
            realized_pnl_today=self.realized_pnl,
        )
        if not ok:
            return None
        total_cost = quantity * price + fee
        if total_cost > self.cash:
            return None
        key = (ticker, side)
        old_quantity = self.position(ticker, side)
        old_cost = self.average_cost(ticker, side)
        new_quantity = old_quantity + quantity
        new_cost = ((old_cost * old_quantity) + (price * quantity)) / new_quantity
        self.cash -= total_cost
        self.positions[key] = new_quantity
        self.cost_basis[key] = new_cost
        fill = PaperFill(
            datetime.now(timezone.utc),
            ticker,
            side,
            "buy",
            quantity,
            price,
            fee,
            self.cash,
            reason,
            model_probability=model_probability,
            entry_edge=entry_edge,
            market_bid=yes_bid if side == "yes" else no_bid,
            market_ask=yes_ask if side == "yes" else no_ask,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            model_version=model_version,
            asof_utc=asof_utc,
            snapshot_id=snapshot_id,
        )
        self.fills.append(fill)
        return fill

    def sell(
        self,
        ticker: str,
        side: Side,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal = Decimal("0"),
        reason: str = "",
        snapshot_id: int | None = None,
        yes_bid: Decimal | None = None,
        yes_ask: Decimal | None = None,
        no_bid: Decimal | None = None,
        no_ask: Decimal | None = None,
        model_version: str | None = None,
        asof_utc: datetime | None = None,
    ) -> PaperFill | None:
        ok, _ = check_sell_allowed(self.position(ticker, side), quantity)
        if not ok:
            return None
        key = (ticker, side)
        avg_cost = self.average_cost(ticker, side)
        realized = (price - avg_cost) * quantity - fee
        proceeds = quantity * price - fee
        self.cash += proceeds
        remaining = self.position(ticker, side) - quantity
        self.realized_pnl += realized
        if remaining == 0:
            self.positions.pop(key, None)
            self.cost_basis.pop(key, None)
        else:
            self.positions[key] = remaining
        fill = PaperFill(
            datetime.now(timezone.utc),
            ticker,
            side,
            "sell",
            quantity,
            price,
            fee,
            self.cash,
            reason,
            realized_pnl=realized,
            market_bid=yes_bid if side == "yes" else no_bid,
            market_ask=yes_ask if side == "yes" else no_ask,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            model_version=model_version,
            asof_utc=asof_utc,
            snapshot_id=snapshot_id,
        )
        self.fills.append(fill)
        return fill

    def total_exposure(self) -> Decimal:
        return sum(
            quantity * self.average_cost(ticker, side)
            for (ticker, side), quantity in self.positions.items()
        )
