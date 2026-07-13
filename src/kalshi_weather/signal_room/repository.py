from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class SignalRoomReadRepository:
    def __init__(self, sqlite_path: str | Path | None) -> None:
        self.sqlite_path = Path(sqlite_path) if sqlite_path is not None else None

    @property
    def database_present(self) -> bool:
        return self.sqlite_path is not None and self.sqlite_path.exists()

    def list_events(self) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                """
                SELECT DISTINCT target_date_local
                FROM strategy_current_capture_manifests
                ORDER BY target_date_local DESC
                LIMIT 100
                """,
            )
        return [dict(row) for row in rows]

    def list_validation_events(self) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                """
                SELECT target_date, series, station, MAX(captured_utc) AS latest_captured_utc
                FROM validation_snapshots
                GROUP BY target_date, series, station
                ORDER BY target_date DESC
                LIMIT 100
                """,
            )
        return [dict(row) for row in rows]

    def latest_capture_manifest(self, target_date: date | None = None) -> dict[str, Any] | None:
        if not self.database_present:
            return None
        params: list[Any] = []
        clause = ""
        if target_date is not None:
            clause = "WHERE target_date_local = ?"
            params.append(target_date.isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT *
                FROM strategy_current_capture_manifests
                {clause}
                ORDER BY completed_at_utc DESC
                LIMIT 1
                """,
                params,
            )
        return dict(rows[0]) if rows else None

    def model_state_rows(
        self,
        *,
        target_date: date,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        clauses = ["d.target_date_local = ?"]
        params: list[Any] = [target_date.isoformat()]
        if as_of is not None:
            clauses.append("d.evaluated_at_utc <= ?")
            params.append(as_of.astimezone(timezone.utc).isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT ms.*, d.evaluated_at_utc, d.reason_code, d.decision_id
                FROM strategy_current_model_states ms
                JOIN strategy_current_decisions d ON d.decision_id = ms.decision_id
                WHERE {' AND '.join(clauses)}
                ORDER BY d.evaluated_at_utc DESC
                """,
                params,
            )
        return [dict(row) for row in rows]

    def latest_decision(self, target_date: date, as_of: datetime | None = None) -> dict[str, Any] | None:
        if not self.database_present:
            return None
        clauses = ["target_date_local = ?"]
        params: list[Any] = [target_date.isoformat()]
        if as_of is not None:
            clauses.append("evaluated_at_utc <= ?")
            params.append(as_of.astimezone(timezone.utc).isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT *
                FROM strategy_current_decisions
                WHERE {' AND '.join(clauses)}
                ORDER BY evaluated_at_utc DESC
                LIMIT 1
                """,
                params,
            )
        return dict(rows[0]) if rows else None

    def latest_validation_snapshot(
        self,
        target_date: date,
        as_of: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not self.database_present:
            return None
        clauses = ["target_date = ?"]
        params: list[Any] = [target_date.isoformat()]
        if as_of is not None:
            clauses.append("captured_utc <= ?")
            params.append(as_of.astimezone(timezone.utc).isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT *
                FROM validation_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY captured_utc DESC
                LIMIT 1
                """,
                params,
            )
        if not rows:
            return None
        row = dict(rows[0])
        row["payload"] = _json_object(row.get("payload_json"))
        return row

    def validation_model_rows(self, snapshot_id: int) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                """
                SELECT *
                FROM validation_model_rows
                WHERE snapshot_id = ?
                ORDER BY id
                """,
                [snapshot_id],
            )
        return [dict(row) for row in rows]

    def validation_market_rows(self, snapshot_id: int) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                """
                SELECT *
                FROM validation_market_rows
                WHERE snapshot_id = ?
                ORDER BY id
                """,
                [snapshot_id],
            )
        return [dict(row) for row in rows]

    def validation_observation_rows(self, snapshot_id: int) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                """
                SELECT *
                FROM validation_observation_rows
                WHERE snapshot_id = ?
                ORDER BY id
                """,
                [snapshot_id],
            )
        return [dict(row) for row in rows]

    def timeline(
        self,
        *,
        target_date: date,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        bounded_limit = min(max(limit, 1), 500)
        clauses = ["target_date_local = ?"]
        params: list[Any] = [target_date.isoformat()]
        if start is not None:
            clauses.append("evaluated_at_utc >= ?")
            params.append(start.astimezone(timezone.utc).isoformat())
        if end is not None:
            clauses.append("evaluated_at_utc <= ?")
            params.append(end.astimezone(timezone.utc).isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT decision_id, evaluated_at_utc, reason_code, source_ids_json
                FROM strategy_current_decisions
                WHERE {' AND '.join(clauses)}
                ORDER BY evaluated_at_utc
                LIMIT ?
                """,
                [*params, bounded_limit],
            )
        return [dict(row) for row in rows]

    def validation_timeline(
        self,
        *,
        target_date: date,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.database_present:
            return []
        bounded_limit = min(max(limit, 1), 500)
        clauses = ["target_date = ?"]
        params: list[Any] = [target_date.isoformat()]
        if start is not None:
            clauses.append("captured_utc >= ?")
            params.append(start.astimezone(timezone.utc).isoformat())
        if end is not None:
            clauses.append("captured_utc <= ?")
            params.append(end.astimezone(timezone.utc).isoformat())
        with self._connect() as conn:
            rows = _safe_query(
                conn,
                f"""
                SELECT *
                FROM validation_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY captured_utc
                LIMIT ?
                """,
                [*params, bounded_limit],
            )
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_object(item.get("payload_json"))
            output.append(item)
        return output

    def _connect(self) -> sqlite3.Connection:
        if self.sqlite_path is None:
            raise FileNotFoundError("no sqlite path configured")
        uri = f"file:{self.sqlite_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn


def _safe_query(
    conn: sqlite3.Connection,
    sql: str,
    params: list[Any] | None = None,
) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql, params or []))
    except sqlite3.OperationalError:
        return []


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
