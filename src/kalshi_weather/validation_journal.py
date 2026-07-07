from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


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


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class ValidationJournal:
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
                created_utc TEXT NOT NULL,
                experiment_id TEXT NOT NULL,
                series TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                bucket_start_utc TEXT NOT NULL,
                generated_at_utc TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                raw_json_path TEXT,
                status TEXT NOT NULL DEFAULT 'recorded'
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_validation_snapshot_bucket
                ON validation_snapshots(experiment_id, series, station, target_date, bucket_start_utc);

            CREATE TABLE IF NOT EXISTS validation_model_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                bucket_start_utc TEXT NOT NULL,
                model_key TEXT NOT NULL,
                display_name TEXT,
                provider TEXT,
                model_family TEXT,
                independence_group TEXT,
                source_type TEXT,
                fetch_status TEXT,
                estimated_high_f REAL,
                settlement_high_estimate_f REAL,
                estimated_bracket TEXT,
                top_probability_bracket TEXT,
                top_probability REAL,
                uncertainty_spread_f REAL,
                estimate_source_kind TEXT,
                estimate_source_detail TEXT,
                endpoint_used TEXT,
                raw_model_param_used TEXT,
                cycle_time_utc TEXT,
                forecast_valid_time_utc TEXT,
                valid_times_used_count INTEGER,
                fallback_from_model_key TEXT,
                uses_observation_data INTEGER,
                uses_high_so_far INTEGER,
                is_blend INTEGER,
                is_ensemble INTEGER,
                is_direct_model INTEGER,
                is_station_guidance INTEGER,
                is_synthetic INTEGER,
                estimate_p10_high_f REAL,
                estimate_p25_high_f REAL,
                estimate_p50_high_f REAL,
                estimate_p75_high_f REAL,
                estimate_p90_high_f REAL,
                full_error_message TEXT,
                error_message TEXT,
                metadata_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_validation_model_rows_lookup
                ON validation_model_rows(experiment_id, target_date, model_key, bucket_start_utc);

            CREATE TABLE IF NOT EXISTS validation_market_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                target_date TEXT NOT NULL,
                bucket_start_utc TEXT NOT NULL,
                market_ticker TEXT,
                bracket_label TEXT,
                yes_bid_cents REAL,
                yes_ask_cents REAL,
                no_bid_cents REAL,
                no_ask_cents REAL,
                raw_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_validation_market_rows_lookup
                ON validation_market_rows(experiment_id, target_date, bracket_label, bucket_start_utc);

            CREATE TABLE IF NOT EXISTS validation_observation_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                experiment_id TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                observed_at_utc TEXT,
                temp_f REAL,
                source TEXT,
                raw_message TEXT,
                raw_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_validation_observation_rows_lookup
                ON validation_observation_rows(experiment_id, target_date, observed_at_utc);
            """
        )
        self._ensure_model_provenance_columns()
        self.conn.commit()

    def _ensure_model_provenance_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(validation_model_rows)").fetchall()
        }
        columns = {
            "settlement_high_estimate_f": "REAL",
            "estimate_source_kind": "TEXT",
            "estimate_source_detail": "TEXT",
            "endpoint_used": "TEXT",
            "raw_model_param_used": "TEXT",
            "cycle_time_utc": "TEXT",
            "forecast_valid_time_utc": "TEXT",
            "valid_times_used_count": "INTEGER",
            "fallback_from_model_key": "TEXT",
            "uses_observation_data": "INTEGER",
            "uses_high_so_far": "INTEGER",
            "is_blend": "INTEGER",
            "is_ensemble": "INTEGER",
            "is_direct_model": "INTEGER",
            "is_station_guidance": "INTEGER",
            "is_synthetic": "INTEGER",
            "estimate_p10_high_f": "REAL",
            "estimate_p25_high_f": "REAL",
            "estimate_p50_high_f": "REAL",
            "estimate_p75_high_f": "REAL",
            "estimate_p90_high_f": "REAL",
            "full_error_message": "TEXT",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE validation_model_rows ADD COLUMN {name} {sql_type}")

    def insert_snapshot(
        self,
        payload: dict[str, Any],
        *,
        replace_existing_bucket: bool = False,
        raw_json_path: str | Path | None = None,
    ) -> dict[str, Any]:
        experiment_id = str(payload["experiment_id"])
        series = str(payload["series"])
        station = str(payload["station"])
        target_date = _date_text(payload["target_date"]) or ""
        bucket_start_utc = str(payload["bucket_start_utc"])
        generated_at_utc = str(payload["generated_at_utc"])
        existing = self.conn.execute(
            """
            SELECT id FROM validation_snapshots
            WHERE experiment_id = ? AND series = ? AND station = ? AND target_date = ? AND bucket_start_utc = ?
            """,
            (experiment_id, series, station, target_date, bucket_start_utc),
        ).fetchone()
        if existing and not replace_existing_bucket:
            return {"status": "skipped_duplicate", "snapshot_id": int(existing["id"])}
        if existing and replace_existing_bucket:
            snapshot_id = int(existing["id"])
            for table in ("validation_model_rows", "validation_market_rows", "validation_observation_rows"):
                self.conn.execute(f"DELETE FROM {table} WHERE snapshot_id = ?", (snapshot_id,))
            self.conn.execute("DELETE FROM validation_snapshots WHERE id = ?", (snapshot_id,))
            self.conn.commit()

        cur = self.conn.execute(
            """
            INSERT INTO validation_snapshots(
                created_utc, experiment_id, series, station, target_date, bucket_start_utc,
                generated_at_utc, payload_json, raw_json_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                experiment_id,
                series,
                station,
                target_date,
                bucket_start_utc,
                generated_at_utc,
                _json_dumps(payload),
                str(raw_json_path) if raw_json_path else None,
                "recorded",
            ),
        )
        snapshot_id = int(cur.lastrowid)
        self._insert_model_rows(snapshot_id, payload)
        self._insert_market_rows(snapshot_id, payload)
        self._insert_observation_rows(snapshot_id, payload)
        self.conn.commit()
        return {"status": "recorded", "snapshot_id": snapshot_id}

    def _insert_model_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        for row in payload.get("models", []):
            self.conn.execute(
                """
                INSERT INTO validation_model_rows(
                    snapshot_id, experiment_id, target_date, bucket_start_utc, model_key,
                    display_name, provider, model_family, independence_group, source_type,
                    fetch_status, estimated_high_f, settlement_high_estimate_f, estimated_bracket,
                    top_probability_bracket, top_probability, uncertainty_spread_f,
                    estimate_source_kind, estimate_source_detail, endpoint_used, raw_model_param_used,
                    cycle_time_utc, forecast_valid_time_utc, valid_times_used_count,
                    fallback_from_model_key, uses_observation_data, uses_high_so_far,
                    is_blend, is_ensemble, is_direct_model, is_station_guidance, is_synthetic,
                    estimate_p10_high_f, estimate_p25_high_f, estimate_p50_high_f,
                    estimate_p75_high_f, estimate_p90_high_f, full_error_message,
                    error_message, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    payload["experiment_id"],
                    _date_text(payload["target_date"]),
                    payload["bucket_start_utc"],
                    row.get("model_key"),
                    row.get("display_name"),
                    row.get("provider"),
                    row.get("model_family"),
                    row.get("independence_group"),
                    row.get("source_type"),
                    row.get("fetch_status"),
                    row.get("estimated_high_f"),
                    row.get("settlement_high_estimate_f"),
                    row.get("estimated_bracket"),
                    row.get("top_probability_bracket"),
                    row.get("top_probability"),
                    row.get("uncertainty_spread_f"),
                    row.get("estimate_source_kind"),
                    row.get("estimate_source_detail"),
                    row.get("endpoint_used"),
                    row.get("raw_model_param_used"),
                    row.get("cycle_time_utc"),
                    row.get("forecast_valid_time_utc"),
                    row.get("valid_times_used_count"),
                    row.get("fallback_from_model_key"),
                    1 if row.get("uses_observation_data") else 0,
                    1 if row.get("uses_high_so_far") else 0,
                    1 if row.get("is_blend") else 0,
                    1 if row.get("is_ensemble") else 0,
                    1 if row.get("is_direct_model") else 0,
                    1 if row.get("is_station_guidance") else 0,
                    1 if row.get("is_synthetic") else 0,
                    row.get("estimate_p10_high_f"),
                    row.get("estimate_p25_high_f"),
                    row.get("estimate_p50_high_f"),
                    row.get("estimate_p75_high_f"),
                    row.get("estimate_p90_high_f"),
                    row.get("full_error_message"),
                    row.get("error_message"),
                    _json_dumps(row),
                ),
            )

    def _insert_market_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        for row in (payload.get("market") or {}).get("brackets", []):
            self.conn.execute(
                """
                INSERT INTO validation_market_rows(
                    snapshot_id, experiment_id, target_date, bucket_start_utc, market_ticker,
                    bracket_label, yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    payload["experiment_id"],
                    _date_text(payload["target_date"]),
                    payload["bucket_start_utc"],
                    row.get("market_ticker"),
                    row.get("bracket_label"),
                    row.get("yes_bid_cents"),
                    row.get("yes_ask_cents"),
                    row.get("no_bid_cents"),
                    row.get("no_ask_cents"),
                    _json_dumps(row),
                ),
            )

    def _insert_observation_rows(self, snapshot_id: int, payload: dict[str, Any]) -> None:
        observations = payload.get("observation") or {}
        source = observations.get("source")
        for row in observations.get("observations", []):
            self.conn.execute(
                """
                INSERT INTO validation_observation_rows(
                    snapshot_id, experiment_id, station, target_date, observed_at_utc,
                    temp_f, source, raw_message, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    payload["experiment_id"],
                    payload["station"],
                    _date_text(payload["target_date"]),
                    row.get("timestamp_utc"),
                    row.get("temp_f"),
                    source,
                    row.get("raw_message"),
                    _json_dumps(row),
                ),
            )

    def load_snapshots(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if experiment_id:
            where = "WHERE experiment_id = ?"
            params.append(experiment_id)
        rows = self.conn.execute(
            f"""
            SELECT * FROM validation_snapshots
            {where}
            ORDER BY target_date, bucket_start_utc, id
            """,
            params,
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def count_snapshots(self, experiment_id: str | None = None) -> int:
        params: list[Any] = []
        where = ""
        if experiment_id:
            where = "WHERE experiment_id = ?"
            params.append(experiment_id)
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM validation_snapshots {where}", params).fetchone()
        return int(row["n"] if row else 0)


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(_json_dumps(payload))
        handle.write("\n")
