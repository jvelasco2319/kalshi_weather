from __future__ import annotations

from datetime import date, datetime, timezone
from copy import deepcopy
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
from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS
from kalshi_weather.strategy_current.stage_weighting import (
    build_stage_weight_snapshot,
    load_stage_weight_config,
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
        assert "strategy_stage_performance" in tables
        assert "strategy_stage_weight_evaluations" in tables
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


def test_stage_performance_and_weight_evaluation_are_additive_and_immutable() -> None:
    base = _scratch("stage-weight-persistence")
    try:
        sqlite_store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store = StrategyCurrentStore(sqlite_store.conn)
        weight_config = load_stage_weight_config()
        performance = {
            "strategy_id": weight_config.strategy_id,
            "weighting_revision": weight_config.weighting_revision,
            "weighting_config_hash": weight_config.config_hash,
            "model_key": "gfs013",
            "target_date_local": "2026-07-12",
            "stage_id": "target_11_13",
            "outcome_map_hash": "outcome-map-1",
            "realized_market_ticker": "KXHIGHLAX-26JUL12-B74.5",
            "realized_bracket_index": 2,
            "evaluation_count": 3,
            "mean_log_loss": 0.5,
            "mean_brier_score": 0.2,
            "mean_absolute_temperature_error": 1.25,
            "mean_temperature_bias": -0.5,
            "source_evaluation_ids": ["eval-1", "eval-2", "eval-3"],
            "settled_at_utc": "2026-07-13T08:00:00+00:00",
            "created_at_utc": "2026-07-13T09:00:00+00:00",
            "code_revision": "test",
        }
        assert store.save_stage_performance_rows([performance]) == 1
        assert store.save_stage_performance_rows([performance]) == 1
        history = store.load_stage_performance_rows(
            strategy_id=weight_config.strategy_id,
            weighting_revision=weight_config.weighting_revision,
            before_target_date="2026-07-13",
            settled_by=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
        )
        assert len(history) == 1
        assert history[0]["source_evaluation_ids"] == ["eval-1", "eval-2", "eval-3"]

        snapshot = build_stage_weight_snapshot(
            evaluation_id="weight-eval-1",
            evaluated_at=datetime(2026, 7, 13, 12, tzinfo=timezone.utc),
            target_date=date(2026, 7, 13),
            strategy_config_hash="strategy-hash",
            code_revision="test",
            bracket_count=6,
            score_rows=history,
            available={key: True for key in CANONICAL_MODEL_KEYS},
            config=weight_config,
        )
        payload = {"mode_outputs": {"fixed_baseline": []}}
        assert store.save_stage_weight_evaluation(
            snapshot,
            source_snapshot_id=7,
            evaluation_payload=payload,
        ) == "recorded"
        assert store.save_stage_weight_evaluation(
            snapshot,
            source_snapshot_id=7,
            evaluation_payload=payload,
        ) == "existing"
        loaded = store.load_stage_weight_evaluation("weight-eval-1")
        assert loaded is not None
        assert loaded["weight_snapshot"]["evaluationId"] == "weight-eval-1"
        assert loaded["evaluation_payload"] == payload

        changed = deepcopy(snapshot)
        changed["status"] = "READY"
        with pytest.raises(ValueError, match="immutable"):
            store.save_stage_weight_evaluation(
                changed,
                source_snapshot_id=7,
                evaluation_payload=payload,
            )
    finally:
        rmtree(base, ignore_errors=True)
