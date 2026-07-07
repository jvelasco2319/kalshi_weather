from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from kalshi_weather.config import load_settings
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.trading.runner import make_default_broker
from kalshi_weather.trading.risk import RiskLimits, check_buy_allowed


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_paper_state_resumes_latest_cash_and_positions() -> None:
    base = _scratch("paper-resume")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_paper_equity(Decimal("990.00"), Decimal("1.25"), {"positions": {"T:yes": "2"}})
        store.save_paper_position("T", "yes", Decimal("2"), Decimal("0.55"))

        broker = make_default_broker(load_settings(), store=store)

        assert broker.cash == Decimal("990.00")
        assert broker.realized_pnl == Decimal("1.25")
        assert broker.position("T", "yes") == Decimal("2")
        assert broker.average_cost("T", "yes") == Decimal("0.55")
    finally:
        rmtree(base, ignore_errors=True)


def test_paper_reset_starts_fresh_and_records_event() -> None:
    base = _scratch("paper-reset")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_paper_equity(Decimal("990.00"), Decimal("1.25"), {"positions": {"T:yes": "2"}})
        store.save_paper_position("T", "yes", Decimal("2"), Decimal("0.55"))

        broker = make_default_broker(load_settings(), store=store, reset=True)

        assert broker.cash == load_settings().paper_starting_cash
        assert broker.positions == {}
        assert store.paper_report()["reset_events"]
    finally:
        rmtree(base, ignore_errors=True)


def test_risk_blocks_max_total_exposure() -> None:
    ok, reason = check_buy_allowed(
        cash=Decimal("100"),
        current_position=Decimal("0"),
        quantity=Decimal("2"),
        price=Decimal("0.60"),
        limits=RiskLimits(
            max_position_per_market=Decimal("10"),
            max_order_cost=Decimal("10"),
            max_total_exposure=Decimal("1.00"),
        ),
        current_total_exposure=Decimal("0.20"),
    )

    assert not ok
    assert "total exposure" in reason
