from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_weather.backtest.metrics import max_drawdown
from kalshi_weather.config import Settings
from kalshi_weather.schemas import OrderbookTop
from kalshi_weather.trading.paper_broker import PaperBroker
from kalshi_weather.trading.risk import RiskLimits
from kalshi_weather.trading.signals import make_trade_signal


def replay_snapshots(snapshot_dir: Path, settings: Settings | None = None) -> dict[str, Any]:
    files = sorted(snapshot_dir.glob("*.json")) if snapshot_dir.exists() else []
    if not files:
        return {"snapshot_count": 0, "trade_count": 0, "net_pnl": "0", "max_drawdown": "0"}

    broker = PaperBroker(
        cash=settings.paper_starting_cash if settings else Decimal("1000.00"),
        limits=RiskLimits(
            settings.paper_max_position_per_market if settings else Decimal("25"),
            settings.paper_max_order_cost if settings else Decimal("25.00"),
        ),
    )
    min_edge = settings.min_edge if settings else Decimal("0.05")
    fee_buffer = settings.fee_buffer if settings else Decimal("0.01")
    model_error_buffer = settings.model_error_buffer if settings else Decimal("0.03")
    quantity = settings.default_quantity if settings else Decimal("1")
    starting_cash = broker.cash
    equity_curve = [starting_cash]

    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        probabilities = payload.get("probabilities", {})
        tops = {
            ticker: _top_from_payload(ticker, top_payload)
            for ticker, top_payload in payload.get("orderbook_tops", {}).items()
        }
        for ticker, p_yes in probabilities.items():
            top = tops.get(ticker)
            if top is None:
                continue
            signal = make_trade_signal(
                ticker=ticker,
                p_yes=float(p_yes),
                top=top,
                quantity=quantity,
                require_edge=min_edge,
                fee_buffer=fee_buffer,
                model_error_buffer=model_error_buffer,
            )
            if signal is not None:
                broker.execute_signal(signal)
        equity_curve.append(broker.cash + broker.realized_pnl)

    net_pnl = broker.cash + broker.realized_pnl - starting_cash
    return {
        "snapshot_count": len(files),
        "trade_count": len(broker.fills),
        "net_pnl": str(net_pnl),
        "max_drawdown": str(max_drawdown(equity_curve)),
    }


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "None":
        return None
    return Decimal(str(value))


def _top_from_payload(ticker: str, payload: dict[str, Any]) -> OrderbookTop:
    return OrderbookTop(
        ticker=ticker,
        yes_bid=_decimal_or_none(payload.get("yes_bid")),
        no_bid=_decimal_or_none(payload.get("no_bid")),
        yes_ask=_decimal_or_none(payload.get("yes_ask")),
        no_ask=_decimal_or_none(payload.get("no_ask")),
    )
