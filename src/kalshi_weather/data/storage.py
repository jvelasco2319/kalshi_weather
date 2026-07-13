from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_weather.model.outcomes import settled_yes


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, default=_json_default)


class SQLiteStore:
    def __init__(self, path: Path, snapshot_dir: Path | None = None) -> None:
        self.path = path
        self.snapshot_dir = snapshot_dir
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.snapshot_dir is not None:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                file_path TEXT
            );
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                series TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS weather_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                station TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opportunity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                series TEXT,
                station TEXT,
                market_date TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                ticker TEXT,
                probability REAL,
                payload_json TEXT,
                asof_utc TEXT,
                series TEXT,
                event_ticker TEXT,
                market_ticker TEXT,
                station TEXT,
                market_date TEXT,
                bracket_label TEXT,
                bracket_lower_f REAL,
                bracket_upper_f REAL,
                bracket_type TEXT,
                p_yes REAL,
                yes_bid TEXT,
                yes_ask TEXT,
                no_bid TEXT,
                no_ask TEXT,
                yes_edge TEXT,
                no_edge TEXT,
                observed_high_so_far_f REAL,
                latest_observation_utc TEXT,
                model_future_high_f REAL,
                model_details_json TEXT,
                residual_sigma_f REAL,
                monte_carlo_samples INTEGER,
                model_version TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                reason TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                cash_after TEXT NOT NULL,
                realized_pnl TEXT DEFAULT '0',
                reason TEXT,
                snapshot_id INTEGER,
                model_probability TEXT,
                entry_edge TEXT,
                market_bid TEXT,
                market_ask TEXT,
                yes_bid TEXT,
                yes_ask TEXT,
                no_bid TEXT,
                no_ask TEXT,
                model_version TEXT,
                asof_utc TEXT,
                bracket_label TEXT,
                market_date TEXT,
                best_side TEXT,
                best_edge TEXT,
                total_hurdle TEXT,
                observed_high_so_far_f REAL,
                model_future_high_f REAL,
                prediction_id INTEGER,
                is_demo INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                average_cost TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                cash TEXT NOT NULL,
                realized_pnl TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                event_type TEXT NOT NULL,
                cash TEXT,
                realized_pnl TEXT,
                payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS official_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                station TEXT NOT NULL,
                market_date TEXT NOT NULL,
                metric TEXT NOT NULL,
                official_high_f REAL NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                source_text TEXT
            );
            CREATE TABLE IF NOT EXISTS prediction_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                prediction_id INTEGER,
                station TEXT,
                market_date TEXT,
                market_ticker TEXT,
                bracket_label TEXT,
                p_yes REAL,
                official_high_f REAL,
                settled_yes INTEGER NOT NULL,
                model_version TEXT,
                ticker TEXT,
                probability REAL
            );
            """
        )
        self._ensure_prediction_columns()
        self._ensure_official_outcome_columns()
        self._ensure_prediction_outcome_columns()
        self._ensure_paper_fill_columns()
        self._ensure_opportunity_snapshot_columns()
        from kalshi_weather.strategy_current.persistence import ensure_strategy_current_schema

        ensure_strategy_current_schema(self.conn)
        self.conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_official_outcomes_unique
                ON official_outcomes(station, market_date, metric);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_prediction_outcomes_prediction_id
                ON prediction_outcomes(prediction_id);
            """
        )
        self.conn.commit()

    def _ensure_prediction_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(model_predictions)")
        }
        desired = {
            "ticker": "TEXT",
            "probability": "REAL",
            "payload_json": "TEXT",
            "asof_utc": "TEXT",
            "series": "TEXT",
            "event_ticker": "TEXT",
            "market_ticker": "TEXT",
            "station": "TEXT",
            "market_date": "TEXT",
            "bracket_label": "TEXT",
            "bracket_lower_f": "REAL",
            "bracket_upper_f": "REAL",
            "bracket_type": "TEXT",
            "p_yes": "REAL",
            "yes_bid": "TEXT",
            "yes_ask": "TEXT",
            "no_bid": "TEXT",
            "no_ask": "TEXT",
            "yes_edge": "TEXT",
            "no_edge": "TEXT",
            "observed_high_so_far_f": "REAL",
            "latest_observation_utc": "TEXT",
            "model_future_high_f": "REAL",
            "model_details_json": "TEXT",
            "residual_sigma_f": "REAL",
            "monte_carlo_samples": "INTEGER",
            "model_version": "TEXT",
        }
        for name, sql_type in desired.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE model_predictions ADD COLUMN {name} {sql_type}")

    def _ensure_prediction_outcome_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(prediction_outcomes)")
        }
        desired = {
            "prediction_id": "INTEGER",
            "station": "TEXT",
            "market_date": "TEXT",
            "market_ticker": "TEXT",
            "bracket_label": "TEXT",
            "p_yes": "REAL",
            "official_high_f": "REAL",
            "model_version": "TEXT",
            "ticker": "TEXT",
            "probability": "REAL",
        }
        for name, sql_type in desired.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE prediction_outcomes ADD COLUMN {name} {sql_type}")

    def _ensure_official_outcome_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(official_outcomes)")
        }
        desired = {
            "created_utc": "TEXT",
            "metric": "TEXT",
            "source_url": "TEXT",
            "source_text": "TEXT",
        }
        for name, sql_type in desired.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE official_outcomes ADD COLUMN {name} {sql_type}")

    def _ensure_paper_fill_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(paper_fills)")
        }
        desired = {
            "model_probability": "TEXT",
            "entry_edge": "TEXT",
            "market_bid": "TEXT",
            "market_ask": "TEXT",
            "yes_bid": "TEXT",
            "yes_ask": "TEXT",
            "no_bid": "TEXT",
            "no_ask": "TEXT",
            "model_version": "TEXT",
            "asof_utc": "TEXT",
            "bracket_label": "TEXT",
            "market_date": "TEXT",
            "best_side": "TEXT",
            "best_edge": "TEXT",
            "total_hurdle": "TEXT",
            "observed_high_so_far_f": "REAL",
            "model_future_high_f": "REAL",
            "prediction_id": "INTEGER",
            "is_demo": "INTEGER DEFAULT 0",
        }
        for name, sql_type in desired.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE paper_fills ADD COLUMN {name} {sql_type}")

    def _ensure_opportunity_snapshot_columns(self) -> None:
        columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(opportunity_snapshots)")
        }
        desired = {
            "series": "TEXT",
            "station": "TEXT",
            "market_date": "TEXT",
            "payload_json": "TEXT",
        }
        for name, sql_type in desired.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE opportunity_snapshots ADD COLUMN {name} {sql_type}")

    def _insert(self, table: str, values: dict[str, Any]) -> int:
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        column_sql = ", ".join(columns)
        cur = self.conn.execute(
            f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_snapshot(self, kind: str, payload: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        payload_json = _json_dumps(payload)
        file_path = None
        if self.snapshot_dir is not None:
            safe_kind = kind.replace(" ", "_")
            file_path = str(self.snapshot_dir / f"{now.replace(':', '')}_{safe_kind}.json")
            Path(file_path).write_text(payload_json, encoding="utf-8")
        return self._insert(
            "snapshots",
            {
                "created_utc": now,
                "kind": kind,
                "payload_json": payload_json,
                "file_path": file_path,
            },
        )

    def save_market_snapshot(self, series: str, payload: dict[str, Any]) -> int:
        return self._insert(
            "market_snapshots",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "series": series,
                "payload_json": _json_dumps(payload),
            },
        )

    def save_weather_snapshot(self, station: str, payload: dict[str, Any]) -> int:
        return self._insert(
            "weather_snapshots",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "station": station,
                "payload_json": _json_dumps(payload),
            },
        )

    def save_opportunity_snapshot(
        self,
        series: str,
        station: str,
        market_date: str | date | None,
        payload: dict[str, Any],
    ) -> int:
        return self._insert(
            "opportunity_snapshots",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "series": series,
                "station": station,
                "market_date": _date_or_none(market_date),
                "payload_json": _json_dumps(payload),
            },
        )

    def opportunity_snapshot_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM opportunity_snapshots").fetchone()[0])

    def load_opportunity_snapshots(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if station:
            clauses.append("station = ?")
            params.append(station)
        if start_date:
            clauses.append("market_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("market_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM opportunity_snapshots {where} ORDER BY created_utc, id",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def save_model_prediction(self, ticker: str, probability: float, payload: dict[str, Any]) -> int:
        record = {"market_ticker": ticker, "ticker": ticker, "p_yes": probability, "probability": probability}
        record.update(payload)
        return self.save_prediction(record)

    def save_prediction(self, record: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        payload = record.get("payload_json") or _json_dumps(record.get("payload", record))
        model_details = record.get("model_details_json")
        if isinstance(model_details, dict):
            model_details = _json_dumps(model_details)
        p_yes = record.get("p_yes", record.get("probability"))
        market_ticker = record.get("market_ticker", record.get("ticker"))
        values = {
            "created_utc": now,
            "ticker": market_ticker,
            "probability": p_yes,
            "payload_json": payload,
            "asof_utc": _iso_or_none(record.get("asof_utc")),
            "series": record.get("series"),
            "event_ticker": record.get("event_ticker"),
            "market_ticker": market_ticker,
            "station": record.get("station"),
            "market_date": _date_or_none(record.get("market_date")),
            "bracket_label": record.get("bracket_label"),
            "bracket_lower_f": record.get("bracket_lower_f"),
            "bracket_upper_f": record.get("bracket_upper_f"),
            "bracket_type": record.get("bracket_type"),
            "p_yes": p_yes,
            "yes_bid": _str_or_none(record.get("yes_bid")),
            "yes_ask": _str_or_none(record.get("yes_ask")),
            "no_bid": _str_or_none(record.get("no_bid")),
            "no_ask": _str_or_none(record.get("no_ask")),
            "yes_edge": _str_or_none(record.get("yes_edge")),
            "no_edge": _str_or_none(record.get("no_edge")),
            "observed_high_so_far_f": record.get("observed_high_so_far_f"),
            "latest_observation_utc": _iso_or_none(record.get("latest_observation_utc")),
            "model_future_high_f": record.get("model_future_high_f"),
            "model_details_json": model_details,
            "residual_sigma_f": record.get("residual_sigma_f"),
            "monte_carlo_samples": record.get("monte_carlo_samples"),
            "model_version": record.get("model_version"),
        }
        return self._insert("model_predictions", values)

    def save_predictions(self, records: list[dict[str, Any]]) -> list[int]:
        return [self.save_prediction(record) for record in records]

    def load_predictions(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if station:
            clauses.append("station = ?")
            params.append(station)
        if start_date:
            clauses.append("market_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("market_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM model_predictions {where}", params).fetchall()
        return [dict(row) for row in rows]

    def load_joinable_predictions(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in self.load_predictions(station=station, start_date=start_date, end_date=end_date)
            if row.get("market_date") and row.get("station") and row.get("market_ticker")
        ]

    def prediction_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0])

    def save_official_outcome(
        self,
        station: str,
        market_date: str | date,
        metric: str,
        official_high_f: float,
        source: str,
        source_url: str | None = None,
        source_text: str | None = None,
        overwrite: bool = False,
    ) -> int:
        market_date_text = _date_or_none(market_date)
        if not overwrite:
            existing = self.conn.execute(
                """
                SELECT id FROM official_outcomes
                WHERE station = ? AND market_date = ? AND metric = ?
                """,
                (station, market_date_text, metric),
            ).fetchone()
            if existing:
                return int(existing["id"])
        else:
            self.conn.execute(
                "DELETE FROM official_outcomes WHERE station = ? AND market_date = ? AND metric = ?",
                (station, market_date_text, metric),
            )
            self.conn.commit()
        return self._insert(
            "official_outcomes",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "station": station,
                "market_date": market_date_text,
                "metric": metric,
                "official_high_f": official_high_f,
                "source": source,
                "source_url": source_url,
                "source_text": source_text,
            },
        )

    def outcome_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM official_outcomes").fetchone()[0])

    def distinct_prediction_dates(self, station: str | None = None) -> list[str]:
        clauses = ["market_date IS NOT NULL"]
        params: list[Any] = []
        if station:
            clauses.append("station = ?")
            params.append(station)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT market_date
            FROM model_predictions
            WHERE {' AND '.join(clauses)}
            ORDER BY market_date
            """,
            params,
        ).fetchall()
        return [str(row["market_date"]) for row in rows]

    def has_official_outcome(
        self,
        station: str,
        market_date: str | date,
        metric: str = "official_high_f",
    ) -> bool:
        row = self.conn.execute(
            """
            SELECT id FROM official_outcomes
            WHERE station = ? AND market_date = ? AND metric = ?
            """,
            (station, _date_or_none(market_date), metric),
        ).fetchone()
        return row is not None

    def load_official_outcomes(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if station:
            clauses.append("station = ?")
            params.append(station)
        if start_date:
            clauses.append("market_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("market_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM official_outcomes {where} ORDER BY market_date, station",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def join_predictions_to_outcomes(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        predictions = self.load_joinable_predictions(
            station=station, start_date=start_date, end_date=end_date
        )
        scanned = len(predictions)
        matched = 0
        joined = 0
        duplicate_skipped = 0
        no_outcome_count = 0
        per_date: dict[str, dict[str, int]] = {}
        outcome_dates = {
            row["market_date"]
            for row in self.load_official_outcomes(station=station, start_date=start_date, end_date=end_date)
        }
        for prediction in predictions:
            date_key = str(prediction["market_date"])
            bucket = per_date.setdefault(
                date_key,
                {"predictions_scanned": 0, "joined_count": 0, "duplicate_skipped_count": 0, "no_outcome_count": 0},
            )
            bucket["predictions_scanned"] += 1
            outcome = self.conn.execute(
                """
                SELECT * FROM official_outcomes
                WHERE station = ? AND market_date = ? AND metric = 'official_high_f'
                """,
                (prediction["station"], prediction["market_date"]),
            ).fetchone()
            if outcome is None:
                no_outcome_count += 1
                bucket["no_outcome_count"] += 1
                continue
            matched += 1
            existing = self.conn.execute(
                "SELECT id FROM prediction_outcomes WHERE prediction_id = ?",
                (prediction["id"],),
            ).fetchone()
            if existing and not overwrite:
                duplicate_skipped += 1
                bucket["duplicate_skipped_count"] += 1
                continue
            if existing and overwrite:
                self.conn.execute("DELETE FROM prediction_outcomes WHERE prediction_id = ?", (prediction["id"],))
                self.conn.commit()
            official_high = float(outcome["official_high_f"])
            settled = settled_yes(
                official_high,
                _int_or_none(prediction.get("bracket_lower_f")),
                _int_or_none(prediction.get("bracket_upper_f")),
            )
            self._insert(
                "prediction_outcomes",
                {
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "prediction_id": prediction["id"],
                    "station": prediction["station"],
                    "market_date": prediction["market_date"],
                    "market_ticker": prediction["market_ticker"],
                    "bracket_label": prediction["bracket_label"],
                    "p_yes": prediction["p_yes"],
                    "official_high_f": official_high,
                    "settled_yes": settled,
                    "model_version": prediction["model_version"],
                    "ticker": prediction["market_ticker"],
                    "probability": prediction["p_yes"],
                },
            )
            joined += 1
            bucket["joined_count"] += 1
        return {
            "predictions_scanned": scanned,
            "outcomes_available": len(outcome_dates),
            "matched": matched,
            "joined_count": joined,
            "duplicate_skipped_count": duplicate_skipped,
            "no_outcome_count": no_outcome_count,
            "start_date": start_date,
            "end_date": end_date,
            "per_date_summary": per_date,
            "scanned": scanned,
            "joined": joined,
            "skipped": duplicate_skipped + no_outcome_count,
        }

    def load_prediction_outcomes(
        self,
        station: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if station:
            clauses.append("po.station = ?")
            params.append(station)
        if start_date:
            clauses.append("po.market_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("po.market_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT po.prediction_id, po.station, po.market_date, po.market_ticker, po.bracket_label,
                   COALESCE(po.p_yes, po.probability) AS probability,
                   po.official_high_f, po.settled_yes, po.model_version,
                   mp.asof_utc, mp.bracket_lower_f, mp.bracket_upper_f, mp.bracket_type,
                   mp.observed_high_so_far_f, mp.model_future_high_f, mp.model_details_json,
                   CAST(strftime('%H', mp.asof_utc) AS INTEGER) AS asof_hour_utc,
                   CASE
                     WHEN mp.observed_high_so_far_f IS NULL THEN NULL
                     WHEN mp.bracket_lower_f IS NOT NULL AND mp.observed_high_so_far_f < mp.bracket_lower_f THEN 0
                     WHEN mp.bracket_upper_f IS NOT NULL AND mp.observed_high_so_far_f > mp.bracket_upper_f THEN 0
                     ELSE 1
                   END AS observed_inside_bracket
            FROM prediction_outcomes po
            LEFT JOIN model_predictions mp ON mp.id = po.prediction_id
            {where}
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def joined_outcome_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM prediction_outcomes").fetchone()[0])

    def save_paper_fill(self, fill: dict[str, Any]) -> int:
        return self._insert(
            "paper_fills",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "ticker": fill["ticker"],
                "side": fill["side"],
                "action": fill["action"],
                "quantity": str(fill["quantity"]),
                "price": str(fill["price"]),
                "fee": str(fill.get("fee", "0")),
                "cash_after": str(fill["cash_after"]),
                "realized_pnl": str(fill.get("realized_pnl", "0")),
                "reason": fill.get("reason"),
                "snapshot_id": fill.get("snapshot_id"),
                "model_probability": _str_or_none(fill.get("model_probability")),
                "entry_edge": _str_or_none(fill.get("entry_edge")),
                "market_bid": _str_or_none(fill.get("market_bid")),
                "market_ask": _str_or_none(fill.get("market_ask")),
                "yes_bid": _str_or_none(fill.get("yes_bid")),
                "yes_ask": _str_or_none(fill.get("yes_ask")),
                "no_bid": _str_or_none(fill.get("no_bid")),
                "no_ask": _str_or_none(fill.get("no_ask")),
                "model_version": fill.get("model_version"),
                "asof_utc": _iso_or_none(fill.get("asof_utc")),
                "bracket_label": fill.get("bracket_label"),
                "market_date": _date_or_none(fill.get("market_date")),
                "best_side": fill.get("best_side"),
                "best_edge": _str_or_none(fill.get("best_edge")),
                "total_hurdle": _str_or_none(fill.get("total_hurdle")),
                "observed_high_so_far_f": fill.get("observed_high_so_far_f"),
                "model_future_high_f": fill.get("model_future_high_f"),
                "prediction_id": fill.get("prediction_id"),
                "is_demo": int(bool(fill.get("is_demo", False))),
            },
        )

    def save_paper_position(
        self, ticker: str, side: str, quantity: Decimal, average_cost: Decimal
    ) -> int:
        return self._insert(
            "paper_positions",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "ticker": ticker,
                "side": side,
                "quantity": str(quantity),
                "average_cost": str(average_cost),
            },
        )

    def save_paper_equity(
        self, cash: Decimal, realized_pnl: Decimal, payload: dict[str, Any]
    ) -> int:
        return self._insert(
            "paper_equity",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "cash": str(cash),
                "realized_pnl": str(realized_pnl),
                "payload_json": _json_dumps(payload),
            },
        )

    def save_paper_state_event(
        self,
        event_type: str,
        cash: Decimal | None = None,
        realized_pnl: Decimal | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        return self._insert(
            "paper_state_events",
            {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "cash": _str_or_none(cash),
                "realized_pnl": _str_or_none(realized_pnl),
                "payload_json": _json_dumps(payload or {}),
            },
        )

    def latest_paper_reset_time(self) -> str | None:
        row = self.conn.execute(
            """
            SELECT created_utc FROM paper_state_events
            WHERE event_type = 'reset'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        return str(row["created_utc"]) if row else None

    def latest_paper_equity(self) -> dict[str, Any] | None:
        reset_time = self.latest_paper_reset_time()
        clauses = []
        params: list[Any] = []
        if reset_time:
            clauses.append("created_utc >= ?")
            params.append(reset_time)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.conn.execute(
            f"SELECT * FROM paper_equity {where} ORDER BY id DESC LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None

    def latest_paper_positions(self) -> list[dict[str, Any]]:
        reset_time = self.latest_paper_reset_time()
        params: list[Any] = []
        reset_filter = ""
        if reset_time:
            reset_filter = "WHERE created_utc >= ?"
            params.append(reset_time)
        rows = self.conn.execute(
            f"""
            SELECT p.ticker, p.side, p.quantity, p.average_cost, p.created_utc AS latest_created_utc
            FROM paper_positions p
            JOIN (
                SELECT ticker, side, MAX(id) AS max_id
                FROM paper_positions
                {reset_filter}
                GROUP BY ticker, side
            ) latest ON latest.max_id = p.id
            WHERE CAST(p.quantity AS REAL) != 0
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def paper_report(self) -> dict[str, Any]:
        fills = [dict(row) for row in self.conn.execute("SELECT * FROM paper_fills").fetchall()]
        positions = self.latest_paper_positions()
        latest_equity = self.latest_paper_equity()
        realized = sum(Decimal(str(fill.get("realized_pnl") or "0")) for fill in fills)
        wins = sum(1 for fill in fills if Decimal(str(fill.get("realized_pnl") or "0")) > 0)
        losses = sum(1 for fill in fills if Decimal(str(fill.get("realized_pnl") or "0")) < 0)
        by_ticker: dict[str, int] = {}
        entry_reasons: dict[str, int] = {}
        exit_reasons: dict[str, int] = {}
        for fill in fills:
            by_ticker[fill["ticker"]] = by_ticker.get(fill["ticker"], 0) + 1
            reason = str(fill.get("reason") or "unspecified")
            if fill.get("action") == "buy":
                entry_reasons[reason] = entry_reasons.get(reason, 0) + 1
            elif fill.get("action") == "sell":
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        entry_edges = [
            Decimal(str(fill["entry_edge"]))
            for fill in fills
            if fill.get("action") == "buy" and fill.get("entry_edge") is not None
        ]
        fills_by_day: dict[str, int] = {}
        for fill in fills:
            day = str(fill["created_utc"])[:10]
            fills_by_day[day] = fills_by_day.get(day, 0) + 1
        total_exposure = sum(
            Decimal(str(pos["quantity"])) * Decimal(str(pos["average_cost"]))
            for pos in positions
        )
        reset_events = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM paper_state_events WHERE event_type = 'reset' ORDER BY id"
            ).fetchall()
        ]
        return {
            "total_paper_fills": len(fills),
            "realized_pnl": str(realized),
            "win_count": wins,
            "loss_count": losses,
            "trades_by_ticker": by_ticker,
            "top_tickers_by_fills": by_ticker,
            "entry_reasons": entry_reasons,
            "exit_reasons": exit_reasons,
            "open_positions": positions,
            "estimated_unrealized_pnl": None,
            "average_entry_edge": str(sum(entry_edges) / len(entry_edges)) if entry_edges else None,
            "average_hold_time_minutes": None,
            "total_exposure": str(total_exposure),
            "fills_by_day": fills_by_day,
            "max_drawdown": None,
            "reset_events": reset_events,
            "latest_cash": latest_equity["cash"] if latest_equity else None,
            "current_cash": latest_equity["cash"] if latest_equity else None,
            "latest_equity_record": dict(latest_equity) if latest_equity else None,
        }


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _date_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
