from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from kalshi_weather.strategy_current.config import StrategyConfig
from kalshi_weather.strategy_current.registry import canonicalize_model_key, source_history_key

OrderbookEventType = Literal["snapshot", "delta", "invalidated"]


@dataclass(frozen=True)
class ForecastPathPoint:
    point_id: str
    target_date_local: str | date
    model_key: str
    source_variant: str
    run_id: str
    run_time_utc: datetime
    source_available_at_utc: datetime
    valid_time_utc: datetime
    received_at_utc: datetime
    temperature_f: float
    raw_payload_id: str | None = None

    def __post_init__(self) -> None:
        canonicalize_model_key(self.model_key)
        for value in (
            self.run_time_utc,
            self.source_available_at_utc,
            self.valid_time_utc,
            self.received_at_utc,
        ):
            _ensure_aware_utc(value)
        if not -100 <= float(self.temperature_f) <= 160:
            raise ValueError("temperature_f is outside accepted bounds")


@dataclass(frozen=True)
class ObservationEvent:
    observation_id: str
    station: str
    target_date_local: str | date
    observation_time_utc: datetime
    source_available_at_utc: datetime
    received_at_utc: datetime
    temperature_f: float
    accepted: bool
    rejection_reason: str | None = None
    raw_payload_id: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.observation_time_utc,
            self.source_available_at_utc,
            self.received_at_utc,
        ):
            _ensure_aware_utc(value)
        if not self.accepted and not self.rejection_reason:
            raise ValueError("rejected observations require a rejection_reason")


@dataclass(frozen=True)
class PublicTradeRecord:
    trade_id: str
    market_ticker: str
    count_fp: str
    yes_price_dollars: str
    no_price_dollars: str
    created_time_utc: datetime
    received_at_utc: datetime
    is_block_trade: bool | None = None
    page_number: int | None = None
    request_cursor: str | None = None
    response_cursor: str | None = None
    raw_payload_id: str | None = None

    def __post_init__(self) -> None:
        _ensure_positive_decimal(self.count_fp, "count_fp")
        _ensure_aware_utc(self.created_time_utc)
        _ensure_aware_utc(self.received_at_utc)
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number must be positive")


@dataclass(frozen=True)
class OrderbookEvent:
    book_event_id: str
    market_ticker: str
    event_type: OrderbookEventType
    received_at_utc: datetime
    valid_after_event: bool
    subscription_id: str | int | None = None
    sequence_number: int | None = None
    exchange_time_utc: datetime | None = None
    side: str | None = None
    price_dollars: str | None = None
    delta_count_fp: str | None = None
    levels: list[Any] | None = None
    raw_payload_id: str | None = None

    def __post_init__(self) -> None:
        _ensure_aware_utc(self.received_at_utc)
        if self.exchange_time_utc is not None:
            _ensure_aware_utc(self.exchange_time_utc)
        if self.side not in {None, "yes", "no"}:
            raise ValueError("side must be yes, no, or None")
        if self.event_type == "delta" and self.sequence_number is None:
            raise ValueError("delta events require sequence_number")


@dataclass(frozen=True)
class CaptureManifest:
    capture_id: str
    target_date_local: str | date
    started_at_utc: datetime
    completed_at_utc: datetime
    expected: dict[str, Any]
    observed: dict[str, Any]
    cursor_exhausted: bool
    book_sequence_valid: bool
    schema_valid: bool
    status: Literal["complete", "partial", "failed"]
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _ensure_aware_utc(self.started_at_utc)
        _ensure_aware_utc(self.completed_at_utc)
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed_at_utc must be after started_at_utc")


class OrderbookSequenceState:
    def __init__(self) -> None:
        self.valid = False
        self.sequence_number: int | None = None

    def apply_snapshot(self, sequence_number: int | None) -> bool:
        self.valid = True
        self.sequence_number = sequence_number
        return self.valid

    def apply_delta(self, sequence_number: int) -> bool:
        expected = None if self.sequence_number is None else self.sequence_number + 1
        if not self.valid or expected is None or sequence_number != expected:
            self.valid = False
            return False
        self.sequence_number = sequence_number
        return True


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategy_current_raw_payloads (
    raw_payload_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_current_config_versions (
    strategy_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (strategy_id, config_hash)
);

CREATE TABLE IF NOT EXISTS strategy_current_forecast_points (
    point_id TEXT PRIMARY KEY,
    target_date_local TEXT NOT NULL,
    model_key TEXT NOT NULL,
    source_variant TEXT NOT NULL,
    run_id TEXT NOT NULL,
    run_time_utc TEXT NOT NULL,
    source_available_at_utc TEXT NOT NULL,
    valid_time_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    temperature_f REAL NOT NULL,
    raw_payload_id TEXT,
    UNIQUE(model_key, source_variant, run_id, valid_time_utc)
);
CREATE INDEX IF NOT EXISTS idx_strategy_current_forecast_asof
ON strategy_current_forecast_points(
    model_key,
    target_date_local,
    source_available_at_utc,
    received_at_utc,
    valid_time_utc
);

CREATE TABLE IF NOT EXISTS strategy_current_observations (
    observation_id TEXT PRIMARY KEY,
    station TEXT NOT NULL,
    target_date_local TEXT NOT NULL,
    observation_time_utc TEXT NOT NULL,
    source_available_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    temperature_f REAL NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0,1)),
    rejection_reason TEXT,
    raw_payload_id TEXT
);

CREATE TABLE IF NOT EXISTS strategy_current_orderbook_events (
    book_event_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('snapshot','delta','invalidated')),
    subscription_id TEXT,
    sequence_number INTEGER,
    exchange_time_utc TEXT,
    received_at_utc TEXT NOT NULL,
    side TEXT,
    price_dollars TEXT,
    delta_count_fp TEXT,
    levels_json TEXT,
    valid_after_event INTEGER NOT NULL CHECK (valid_after_event IN (0,1)),
    raw_payload_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy_current_book_seq
ON strategy_current_orderbook_events(market_ticker, received_at_utc, sequence_number);

CREATE TABLE IF NOT EXISTS strategy_current_public_trades (
    trade_id TEXT PRIMARY KEY,
    market_ticker TEXT NOT NULL,
    count_fp TEXT NOT NULL,
    yes_price_dollars TEXT NOT NULL,
    no_price_dollars TEXT NOT NULL,
    created_time_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    is_block_trade INTEGER,
    page_number INTEGER,
    request_cursor TEXT,
    response_cursor TEXT,
    raw_payload_id TEXT
);

CREATE TABLE IF NOT EXISTS strategy_current_capture_manifests (
    capture_id TEXT PRIMARY KEY,
    target_date_local TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    expected_json TEXT NOT NULL,
    observed_json TEXT NOT NULL,
    cursor_exhausted INTEGER NOT NULL,
    book_sequence_valid INTEGER NOT NULL,
    schema_valid INTEGER NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);
"""


def ensure_strategy_current_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


class StrategyCurrentStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        ensure_strategy_current_schema(conn)
        self.conn.commit()

    @classmethod
    def open(cls, path: str | Path) -> "StrategyCurrentStore":
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(db_path))

    def save_config_version(self, config: StrategyConfig) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO strategy_current_config_versions (
                strategy_id, config_hash, config_json, created_at_utc
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                config.strategy_id,
                config.config_hash,
                _json_dumps(config),
                _utc_now_text(),
            ),
        )
        self.conn.commit()

    def save_raw_payload(
        self,
        source_type: str,
        source_key: str,
        payload: Any,
        received_at_utc: datetime | None = None,
    ) -> str:
        received = received_at_utc or datetime.now(timezone.utc)
        _ensure_aware_utc(received)
        payload_json = _json_dumps(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        raw_payload_id = f"{source_type}:{source_key}:{payload_hash}"
        self.conn.execute(
            """
            INSERT OR IGNORE INTO strategy_current_raw_payloads (
                raw_payload_id,
                source_type,
                source_key,
                received_at_utc,
                payload_hash,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                raw_payload_id,
                source_type,
                source_key,
                _iso(received),
                payload_hash,
                payload_json,
            ),
        )
        self.conn.commit()
        return raw_payload_id

    def save_forecast_points(self, points: list[ForecastPathPoint]) -> None:
        for point in points:
            self.conn.execute(
                """
                INSERT INTO strategy_current_forecast_points (
                    point_id,
                    target_date_local,
                    model_key,
                    source_variant,
                    run_id,
                    run_time_utc,
                    source_available_at_utc,
                    valid_time_utc,
                    received_at_utc,
                    temperature_f,
                    raw_payload_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    point.point_id,
                    _date_text(point.target_date_local),
                    canonicalize_model_key(point.model_key),
                    point.source_variant,
                    point.run_id,
                    _iso(point.run_time_utc),
                    _iso(point.source_available_at_utc),
                    _iso(point.valid_time_utc),
                    _iso(point.received_at_utc),
                    float(point.temperature_f),
                    point.raw_payload_id,
                ),
            )
        self.conn.commit()

    def load_forecast_points(
        self,
        model_key: str,
        target_date_local: str | date,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM strategy_current_forecast_points
            WHERE model_key = ? AND target_date_local = ?
            ORDER BY source_available_at_utc, valid_time_utc
            """,
            (canonicalize_model_key(model_key), _date_text(target_date_local)),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_observation(self, event: ObservationEvent) -> None:
        self.conn.execute(
            """
            INSERT INTO strategy_current_observations (
                observation_id,
                station,
                target_date_local,
                observation_time_utc,
                source_available_at_utc,
                received_at_utc,
                temperature_f,
                accepted,
                rejection_reason,
                raw_payload_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.observation_id,
                event.station,
                _date_text(event.target_date_local),
                _iso(event.observation_time_utc),
                _iso(event.source_available_at_utc),
                _iso(event.received_at_utc),
                float(event.temperature_f),
                int(event.accepted),
                event.rejection_reason,
                event.raw_payload_id,
            ),
        )
        self.conn.commit()

    def save_orderbook_event(self, event: OrderbookEvent) -> None:
        self.conn.execute(
            """
            INSERT INTO strategy_current_orderbook_events (
                book_event_id,
                market_ticker,
                event_type,
                subscription_id,
                sequence_number,
                exchange_time_utc,
                received_at_utc,
                side,
                price_dollars,
                delta_count_fp,
                levels_json,
                valid_after_event,
                raw_payload_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.book_event_id,
                event.market_ticker,
                event.event_type,
                None if event.subscription_id is None else str(event.subscription_id),
                event.sequence_number,
                _iso_or_none(event.exchange_time_utc),
                _iso(event.received_at_utc),
                event.side,
                event.price_dollars,
                event.delta_count_fp,
                _json_dumps(event.levels or []),
                int(event.valid_after_event),
                event.raw_payload_id,
            ),
        )
        self.conn.commit()

    def save_public_trades(
        self,
        records: list[PublicTradeRecord],
        *,
        cursor_exhausted: bool,
    ) -> None:
        validate_trade_pull(records, cursor_exhausted=cursor_exhausted)
        for record in records:
            self.conn.execute(
                """
                INSERT INTO strategy_current_public_trades (
                    trade_id,
                    market_ticker,
                    count_fp,
                    yes_price_dollars,
                    no_price_dollars,
                    created_time_utc,
                    received_at_utc,
                    is_block_trade,
                    page_number,
                    request_cursor,
                    response_cursor,
                    raw_payload_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trade_id,
                    record.market_ticker,
                    record.count_fp,
                    record.yes_price_dollars,
                    record.no_price_dollars,
                    _iso(record.created_time_utc),
                    _iso(record.received_at_utc),
                    None if record.is_block_trade is None else int(record.is_block_trade),
                    record.page_number,
                    record.request_cursor,
                    record.response_cursor,
                    record.raw_payload_id,
                ),
            )
        self.conn.commit()

    def save_capture_manifest(self, manifest: CaptureManifest) -> None:
        self.conn.execute(
            """
            INSERT INTO strategy_current_capture_manifests (
                capture_id,
                target_date_local,
                started_at_utc,
                completed_at_utc,
                expected_json,
                observed_json,
                cursor_exhausted,
                book_sequence_valid,
                schema_valid,
                status,
                details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.capture_id,
                _date_text(manifest.target_date_local),
                _iso(manifest.started_at_utc),
                _iso(manifest.completed_at_utc),
                _json_dumps(manifest.expected),
                _json_dumps(manifest.observed),
                int(manifest.cursor_exhausted),
                int(manifest.book_sequence_valid),
                int(manifest.schema_valid),
                manifest.status,
                _json_dumps(manifest.details or {}),
            ),
        )
        self.conn.commit()


def validate_trade_pull(records: list[PublicTradeRecord], *, cursor_exhausted: bool) -> None:
    if records and not cursor_exhausted:
        raise ValueError("nonempty trade pull requires exhausted cursor")
    seen: set[str] = set()
    for record in records:
        if record.trade_id in seen:
            raise ValueError(f"duplicate trade_id after normalization: {record.trade_id}")
        seen.add(record.trade_id)
        _ensure_positive_decimal(record.count_fp, "count_fp")


def forecast_point_id(
    *,
    model_key: str,
    source_variant: str | None,
    run_id: str,
    valid_time_utc: datetime,
) -> str:
    key = canonicalize_model_key(model_key)
    source = source_variant or source_history_key(key)
    payload = _json_dumps(
        {
            "model_key": key,
            "source_variant": source,
            "run_id": run_id,
            "valid_time_utc": _iso(valid_time_utc),
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_positive_decimal(value: str, field_name: str) -> None:
    decimal = Decimal(str(value))
    if decimal <= 0:
        raise ValueError(f"{field_name} must be positive")


def _ensure_aware_utc(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("datetime values must be timezone-aware")


def _iso(value: datetime) -> str:
    _ensure_aware_utc(value)
    return value.astimezone(timezone.utc).isoformat()


def _iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else _iso(value)


def _date_text(value: str | date) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":"))


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return str(value)
    return str(value)


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()
