from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_weather.strategy_current.persistence import ensure_strategy_current_schema


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default, sort_keys=True)


def _json_loads_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


class ValidationJournal:
    """Durable record-only journal for model validation snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.ensure_schema()

    def ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS validation_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                captured_utc TEXT NOT NULL,
                captured_local TEXT,
                timezone TEXT,
                bucket_start_utc TEXT NOT NULL,
                series TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                raw_json_path TEXT,
                created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS validation_model_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                model_key TEXT NOT NULL,
                display_name TEXT,
                provider TEXT,
                model_family TEXT,
                independence_group TEXT,
                source_type TEXT,
                fetch_status TEXT NOT NULL,
                estimated_high_f REAL,
                estimated_bracket TEXT,
                uncertainty_spread_f REAL,
                error_message TEXT,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS validation_market_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                ticker TEXT,
                bracket_label TEXT NOT NULL,
                yes_bid_cents REAL,
                yes_ask_cents REAL,
                no_bid_cents REAL,
                no_ask_cents REAL,
                yes_mid_cents REAL,
                market_status TEXT,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS validation_observation_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                station TEXT NOT NULL,
                source TEXT,
                latest_temp_f REAL,
                latest_observation_utc TEXT,
                high_so_far_f REAL,
                final_high_f REAL,
                observation_count INTEGER,
                error_message TEXT,
                raw_json TEXT
            );
            """
        )
        ensure_strategy_current_schema(self.conn)
        self._ensure_additive_columns()
        self.conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_validation_snapshot_bucket
                ON validation_snapshots(
                    experiment_id,
                    series,
                    station,
                    target_date,
                    bucket_start_utc
                );
            CREATE INDEX IF NOT EXISTS idx_validation_models_lookup
                ON validation_model_rows(experiment_id, target_date, model_key);
            CREATE INDEX IF NOT EXISTS idx_validation_markets_lookup
                ON validation_market_rows(experiment_id, target_date, bracket_label);
            CREATE INDEX IF NOT EXISTS idx_validation_obs_lookup
                ON validation_observation_rows(experiment_id, target_date, station);
            """
        )
        self.conn.commit()

    def _ensure_additive_columns(self) -> None:
        self._ensure_columns(
            "validation_snapshots",
            {
                "captured_local": "TEXT",
                "timezone": "TEXT",
                "bucket_start_utc": "TEXT",
                "raw_json_path": "TEXT",
                "created_utc": "TEXT",
            },
        )
        self._ensure_columns(
            "validation_model_rows",
            {
                "display_name": "TEXT",
                "provider": "TEXT",
                "model_family": "TEXT",
                "independence_group": "TEXT",
                "source_type": "TEXT",
                "uncertainty_spread_f": "REAL",
                "error_message": "TEXT",
                "raw_json": "TEXT",
            },
        )
        self._ensure_columns(
            "validation_market_rows",
            {
                "ticker": "TEXT",
                "no_bid_cents": "REAL",
                "no_ask_cents": "REAL",
                "yes_mid_cents": "REAL",
                "market_status": "TEXT",
                "raw_json": "TEXT",
            },
        )
        self._ensure_columns(
            "validation_observation_rows",
            {
                "source": "TEXT",
                "latest_temp_f": "REAL",
                "latest_observation_utc": "TEXT",
                "high_so_far_f": "REAL",
                "final_high_f": "REAL",
                "observation_count": "INTEGER",
                "error_message": "TEXT",
                "raw_json": "TEXT",
            },
        )

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def insert_snapshot(
        self,
        payload: dict[str, Any],
        *,
        replace_existing_bucket: bool = False,
        raw_json_path: str | None = None,
    ) -> dict[str, Any]:
        experiment_id = str(payload["experiment_id"])
        series = str(payload["series"])
        station = str(payload["station"])
        target_date = str(payload["target_date"])
        bucket_start_utc = str(payload["bucket_start_utc"])

        existing = self.conn.execute(
            """
            SELECT id FROM validation_snapshots
            WHERE experiment_id = ?
              AND series = ?
              AND station = ?
              AND target_date = ?
              AND bucket_start_utc = ?
            """,
            (experiment_id, series, station, target_date, bucket_start_utc),
        ).fetchone()
        if existing is not None:
            existing_id = int(existing["id"])
            if not replace_existing_bucket:
                return {"status": "skipped_duplicate", "snapshot_id": existing_id}
            self._delete_snapshot(existing_id)

        cursor = self.conn.execute(
            """
            INSERT INTO validation_snapshots (
                experiment_id,
                schema_version,
                captured_utc,
                captured_local,
                timezone,
                bucket_start_utc,
                series,
                station,
                target_date,
                payload_json,
                raw_json_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                str(payload.get("schema_version", "record_weather_market_v1")),
                str(payload["captured_utc"]),
                str(payload.get("captured_local")),
                str(payload.get("timezone")),
                bucket_start_utc,
                series,
                station,
                target_date,
                _json_dumps(payload),
                raw_json_path,
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        self._insert_model_rows(snapshot_id, payload)
        self._insert_market_rows(snapshot_id, payload)
        self._insert_observation_rows(snapshot_id, payload)
        self.conn.commit()
        return {"status": "recorded", "snapshot_id": snapshot_id}

    def _delete_snapshot(self, snapshot_id: int) -> None:
        self.conn.execute(
            "DELETE FROM strategy_stage_weight_evaluations WHERE source_snapshot_id = ?",
            (snapshot_id,),
        )
        for table in (
            "validation_model_rows",
            "validation_market_rows",
            "validation_observation_rows",
        ):
            self.conn.execute(f"DELETE FROM {table} WHERE snapshot_id = ?", (snapshot_id,))
        self.conn.execute("DELETE FROM validation_snapshots WHERE id = ?", (snapshot_id,))

    def _insert_model_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        rows = payload.get("models") or []
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO validation_model_rows (
                    snapshot_id,
                    experiment_id,
                    target_date,
                    model_key,
                    display_name,
                    provider,
                    model_family,
                    independence_group,
                    source_type,
                    fetch_status,
                    estimated_high_f,
                    estimated_bracket,
                    uncertainty_spread_f,
                    error_message,
                    raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    str(payload["experiment_id"]),
                    str(payload["target_date"]),
                    str(row.get("model_key")),
                    row.get("display_name"),
                    row.get("provider"),
                    row.get("model_family"),
                    row.get("independence_group"),
                    row.get("source_type"),
                    str(row.get("fetch_status", "missing")),
                    row.get("estimated_high_f"),
                    row.get("estimated_bracket"),
                    row.get("uncertainty_spread_f"),
                    row.get("error_message"),
                    _json_dumps(row.get("raw") or {}),
                ),
            )

    def latest_successful_model_row(
        self,
        *,
        experiment_id: str,
        target_date: str,
        model_key: str,
        before_captured_utc: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [experiment_id, target_date, model_key]
        before_clause = ""
        if before_captured_utc is not None:
            before_clause = "AND s.captured_utc < ?"
            params.append(before_captured_utc)
        row = self.conn.execute(
            f"""
            SELECT
                m.*,
                s.id AS source_snapshot_id,
                s.captured_utc AS source_captured_utc
            FROM validation_model_rows m
            JOIN validation_snapshots s ON s.id = m.snapshot_id
            WHERE m.experiment_id = ?
              AND m.target_date = ?
              AND m.model_key = ?
              AND m.fetch_status = 'ok'
              AND m.estimated_high_f IS NOT NULL
              {before_clause}
            ORDER BY s.captured_utc DESC, m.id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["raw"] = _json_loads_object(result.get("raw_json"))
        return result

    def _insert_market_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        rows = payload.get("markets") or []
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO validation_market_rows (
                    snapshot_id,
                    experiment_id,
                    target_date,
                    ticker,
                    bracket_label,
                    yes_bid_cents,
                    yes_ask_cents,
                    no_bid_cents,
                    no_ask_cents,
                    yes_mid_cents,
                    market_status,
                    raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    str(payload["experiment_id"]),
                    str(payload["target_date"]),
                    row.get("ticker"),
                    str(row.get("bracket_label")),
                    row.get("yes_bid_cents"),
                    row.get("yes_ask_cents"),
                    row.get("no_bid_cents"),
                    row.get("no_ask_cents"),
                    row.get("yes_mid_cents"),
                    row.get("market_status"),
                    _json_dumps(row.get("raw") or {}),
                ),
            )

    def _insert_observation_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        rows: list[dict[str, Any]] = []
        observation = payload.get("observation")
        if isinstance(observation, dict):
            rows.append(observation)
        rows.extend(row for row in payload.get("recent_actuals", []) if isinstance(row, dict))

        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("target_date") or payload["target_date"]), str(row.get("source")))
            if key in seen:
                continue
            seen.add(key)
            self.conn.execute(
                """
                INSERT INTO validation_observation_rows (
                    snapshot_id,
                    experiment_id,
                    target_date,
                    station,
                    source,
                    latest_temp_f,
                    latest_observation_utc,
                    high_so_far_f,
                    final_high_f,
                    observation_count,
                    error_message,
                    raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    str(payload["experiment_id"]),
                    str(row.get("target_date") or payload["target_date"]),
                    str(row.get("station") or payload["station"]),
                    row.get("source"),
                    row.get("latest_temp_f"),
                    row.get("latest_observation_utc"),
                    row.get("high_so_far_f"),
                    row.get("final_high_f"),
                    row.get("observation_count"),
                    row.get("error_message"),
                    _json_dumps(row.get("raw") or {}),
                ),
            )

    def load_snapshots(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        if experiment_id:
            rows = self.conn.execute(
                """
                SELECT payload_json
                FROM validation_snapshots
                WHERE experiment_id = ?
                ORDER BY captured_utc
                """,
                (experiment_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT payload_json FROM validation_snapshots ORDER BY captured_utc"
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def count_snapshots(self, experiment_id: str | None = None) -> int:
        if experiment_id:
            row = self.conn.execute(
                "SELECT COUNT(*) AS count FROM validation_snapshots WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM validation_snapshots").fetchone()
        return int(row["count"])


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(_json_dumps(payload) + "\n")
