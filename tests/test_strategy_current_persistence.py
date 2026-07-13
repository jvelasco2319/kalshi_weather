from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pytest

from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.strategy_current.config import load_strategy_config
from kalshi_weather.strategy_current.persistence import (
    CaptureManifest,
    ForecastPathPoint,
    OrderbookEvent,
    OrderbookSequenceState,
    PublicTradeRecord,
    StrategyCurrentStore,
    forecast_point_id,
    source_history_key,
    validate_trade_pull,
)


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dt(hour: int) -> datetime:
    return datetime(2026, 7, 7, hour, tzinfo=timezone.utc)


def test_strategy_schema_is_added_to_existing_sqlite_store() -> None:
    base = _scratch("strategy-schema")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        tables = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        assert "strategy_current_raw_payloads" in tables
        assert "strategy_current_forecast_points" in tables
        assert "strategy_current_capture_manifests" in tables
    finally:
        rmtree(base, ignore_errors=True)


def test_raw_payload_hash_and_full_forecast_path_persist() -> None:
    base = _scratch("strategy-forecast")
    try:
        sqlite_store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store = StrategyCurrentStore(sqlite_store.conn)
        config = load_strategy_config()
        store.save_config_version(config)
        raw_id = store.save_raw_payload(
            "forecast",
            "open_meteo:gfs013",
            {"hourly": {"time": ["2026-07-07T15:00Z"], "temperature_2m": [73.2]}},
            received_at_utc=_dt(12),
        )
        source_variant = source_history_key("gfs013")
        point_id = forecast_point_id(
            model_key="gfs013",
            source_variant=source_variant,
            run_id="gfs013-2026070712",
            valid_time_utc=_dt(15),
        )

        store.save_forecast_points(
            [
                ForecastPathPoint(
                    point_id=point_id,
                    target_date_local=date(2026, 7, 7),
                    model_key="gfs013",
                    source_variant=source_variant,
                    run_id="gfs013-2026070712",
                    run_time_utc=_dt(12),
                    source_available_at_utc=_dt(13),
                    valid_time_utc=_dt(15),
                    received_at_utc=_dt(13),
                    temperature_f=73.2,
                    raw_payload_id=raw_id,
                )
            ]
        )

        rows = store.load_forecast_points("gfs013", "2026-07-07")
        assert rows[0]["point_id"] == point_id
        assert rows[0]["source_variant"] == source_variant
        assert rows[0]["temperature_f"] == 73.2
        assert sqlite_store.conn.execute(
            "SELECT COUNT(*) FROM strategy_current_config_versions"
        ).fetchone()[0] == 1
    finally:
        rmtree(base, ignore_errors=True)


def test_trade_pull_validation_requires_count_fp_cursor_and_unique_ids() -> None:
    good = PublicTradeRecord(
        trade_id="t1",
        market_ticker="KXHIGHLAX-26JUL7-B70",
        count_fp="1.0000",
        yes_price_dollars="0.4500",
        no_price_dollars="0.5500",
        created_time_utc=_dt(14),
        received_at_utc=_dt(14),
    )

    validate_trade_pull([good], cursor_exhausted=True)
    with pytest.raises(ValueError, match="exhausted cursor"):
        validate_trade_pull([good], cursor_exhausted=False)
    with pytest.raises(ValueError, match="duplicate"):
        validate_trade_pull([good, good], cursor_exhausted=True)
    with pytest.raises(ValueError, match="positive"):
        PublicTradeRecord(
            trade_id="bad",
            market_ticker="KXHIGHLAX-26JUL7-B70",
            count_fp="0",
            yes_price_dollars="0.4500",
            no_price_dollars="0.5500",
            created_time_utc=_dt(14),
            received_at_utc=_dt(14),
        )


def test_orderbook_sequence_gap_invalidates_and_events_persist() -> None:
    base = _scratch("strategy-book")
    try:
        sqlite_store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store = StrategyCurrentStore(sqlite_store.conn)
        state = OrderbookSequenceState()

        assert state.apply_snapshot(10) is True
        assert state.apply_delta(11) is True
        assert state.apply_delta(13) is False

        store.save_orderbook_event(
            OrderbookEvent(
                book_event_id="book-gap",
                market_ticker="KXHIGHLAX-26JUL7-B70",
                event_type="invalidated",
                sequence_number=13,
                received_at_utc=_dt(14),
                valid_after_event=state.valid,
            )
        )
        row = sqlite_store.conn.execute(
            "SELECT valid_after_event FROM strategy_current_orderbook_events"
        ).fetchone()
        assert row[0] == 0
    finally:
        rmtree(base, ignore_errors=True)


def test_capture_manifest_persists_completeness_flags() -> None:
    base = _scratch("strategy-manifest")
    try:
        sqlite_store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store = StrategyCurrentStore(sqlite_store.conn)
        store.save_capture_manifest(
            CaptureManifest(
                capture_id="cap1",
                target_date_local="2026-07-07",
                started_at_utc=_dt(12),
                completed_at_utc=_dt(13),
                expected={"models": 5, "book_levels": 10},
                observed={"models": 4, "book_levels": 0},
                cursor_exhausted=False,
                book_sequence_valid=False,
                schema_valid=True,
                status="partial",
                details={"reason": "missing book depth"},
            )
        )

        row = sqlite_store.conn.execute(
            """
            SELECT cursor_exhausted, book_sequence_valid, schema_valid, status
            FROM strategy_current_capture_manifests
            """
        ).fetchone()
        assert tuple(row) == (0, 0, 1, "partial")
    finally:
        rmtree(base, ignore_errors=True)


def test_decimal_money_style_fields_remain_strings() -> None:
    record = PublicTradeRecord(
        trade_id="t2",
        market_ticker="KXHIGHLAX-26JUL7-B70",
        count_fp=str(Decimal("2.5000")),
        yes_price_dollars="0.4500",
        no_price_dollars="0.5500",
        created_time_utc=_dt(14),
        received_at_utc=_dt(14),
    )

    assert record.count_fp == "2.5000"
