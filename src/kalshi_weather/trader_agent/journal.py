from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .trader_types import MarketBracket


class TraderJournalProtocol(Protocol):
    def record_run(self, run: dict[str, Any]) -> None:
        ...


class JsonlTraderJournal:
    """Append-only JSONL journal for trader decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record_run(self, run: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(run, sort_keys=True) + "\n")


class SqliteTraderJournal:
    """Small SQLite journal that can coexist with the existing repo storage."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trader_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    series TEXT,
                    station TEXT,
                    market_date TEXT,
                    action TEXT,
                    selected_candidate_id TEXT,
                    valid INTEGER,
                    rejection_reason TEXT,
                    run_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trader_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'open',
                    contract_ticker TEXT NOT NULL,
                    bracket_label TEXT,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    avg_entry_price_cents REAL NOT NULL,
                    realized_pnl_dollars REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trader_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    action TEXT NOT NULL,
                    contract_ticker TEXT,
                    bracket_label TEXT,
                    side TEXT,
                    quantity INTEGER NOT NULL,
                    price_cents REAL NOT NULL,
                    gross_value_dollars REAL NOT NULL,
                    realized_pnl_dollars REAL NOT NULL DEFAULT 0,
                    decision_id TEXT,
                    selected_candidate_id TEXT,
                    fill_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trader_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    status TEXT NOT NULL DEFAULT 'open',
                    action TEXT NOT NULL,
                    contract_ticker TEXT,
                    bracket_label TEXT,
                    side TEXT,
                    quantity INTEGER NOT NULL,
                    limit_price_cents REAL,
                    decision_id TEXT,
                    selected_candidate_id TEXT,
                    order_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_utc TEXT DEFAULT CURRENT_TIMESTAMP,
                    race_id TEXT,
                    event_ticker TEXT,
                    market_date TEXT,
                    station TEXT,
                    final_high_f REAL,
                    winning_bracket TEXT,
                    settlement_source TEXT,
                    settlement_source_url TEXT,
                    settlement_status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 0,
                    starting_cash REAL,
                    cash_before_settlement REAL,
                    settlement_value_dollars REAL,
                    final_cash_dollars REAL,
                    realized_pnl_dollars REAL,
                    raw_result_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trader_positions_open
                ON trader_positions(status, contract_ticker, side)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_paper_settlements_market_date
                ON paper_settlements(market_date, settlement_status)
                """
            )

    def record_run(self, run: dict[str, Any]) -> None:
        context = run.get("context", {})
        decision = run.get("decision", {})
        validation = run.get("validation", {})
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO trader_runs (
                    series,
                    station,
                    market_date,
                    action,
                    selected_candidate_id,
                    valid,
                    rejection_reason,
                    run_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.get("series"),
                    context.get("station"),
                    context.get("market_date"),
                    decision.get("action"),
                    decision.get("selected_candidate_id"),
                    1 if validation.get("valid") else 0,
                    validation.get("rejection_reason"),
                    json.dumps(run, sort_keys=True),
                ),
            )

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT run_json FROM trader_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def load_open_positions(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM trader_positions
                WHERE status = 'open' AND quantity > 0
                ORDER BY id
                """
            ).fetchall()
        return [_position_row_to_context_dict(dict(row)) for row in rows]

    def load_open_orders(self) -> list[dict[str, Any]]:
        return [_order_row_to_context_dict(row) for row in self._load_open_order_rows()]

    def load_fills(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM trader_fills
                ORDER BY id
                """
            ).fetchall()
        return [_fill_row_to_context_dict(dict(row)) for row in rows]

    def reset_portfolio(self) -> None:
        """Clear fake-money ledger state while preserving historical run logs."""
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM trader_positions")
            conn.execute("DELETE FROM trader_orders")
            conn.execute("DELETE FROM trader_fills")
            conn.execute("DELETE FROM paper_settlements")

    def load_paper_settlements(
        self,
        *,
        market_date: str | None = None,
        settlement_status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM paper_settlements"
        clauses: list[str] = []
        params: list[Any] = []
        if market_date:
            clauses.append("market_date = ?")
            params.append(market_date)
        if settlement_status:
            clauses.append("settlement_status = ?")
            params.append(settlement_status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC"
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row)
            try:
                raw = json.loads(payload.get("raw_result_json") or "{}")
            except json.JSONDecodeError:
                raw = {}
            payload["raw_result"] = raw if isinstance(raw, dict) else {}
            results.append(payload)
        return results

    def has_final_settlement(self, market_date: str | None = None) -> bool:
        if not market_date:
            return False
        return bool(
            self.load_paper_settlements(
                market_date=market_date,
                settlement_status="final_official",
            )
        )

    def settle_open_positions(
        self,
        *,
        winning_bracket: str,
        final_high_f: float | None = None,
        market_date: str | None = None,
        source: str | None = None,
        source_url: str | None = None,
        dry_run: bool = False,
        settlement_status: str = "final_official",
        race_id: str | None = None,
        event_ticker: str | None = None,
        station: str | None = None,
        starting_cash: float | None = None,
        cash_before_settlement: float | None = None,
        final_cash_dollars: float | None = None,
        force_resettle: bool = False,
    ) -> dict[str, Any]:
        """Settle all open fake-money positions against the final winning bracket.

        Settlement is represented as synthetic CLOSE fills at 100c for winning
        sides and 0c for losing sides, keeping the existing paper portfolio math
        and reports consistent.
        """
        winning = _canonical_settlement_label(winning_bracket)
        settlement_status = (settlement_status or "final_official").strip().lower()
        if (
            not dry_run
            and settlement_status == "final_official"
            and market_date
            and not force_resettle
            and self.has_final_settlement(market_date)
        ):
            existing = self.load_paper_settlements(
                market_date=market_date,
                settlement_status="final_official",
            )
            return {
                "executed": False,
                "dry_run": False,
                "blocked": True,
                "reason": "paper journal already finalized for this market date",
                "winning_bracket": winning,
                "final_high_f": final_high_f,
                "market_date": market_date,
                "settlement_status": settlement_status,
                "existing_settlement": existing[0] if existing else None,
                "positions_settled": 0,
                "contracts_settled": 0,
                "settlement_value_dollars": 0.0,
                "realized_pnl_dollars": 0.0,
                "open_orders_canceled": 0,
                "canceled_order_ids": [],
            }
        now = _now()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            position_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM trader_positions
                    WHERE status = 'open' AND quantity > 0
                    ORDER BY id
                    """
                ).fetchall()
            ]
            open_order_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id FROM trader_orders
                    WHERE status = 'open'
                    ORDER BY id
                    """
                ).fetchall()
            ]
            settled: list[dict[str, Any]] = []
            for row in position_rows:
                bracket = _canonical_settlement_label(row.get("bracket_label") or row.get("contract_ticker"))
                side = str(row.get("side") or "").upper()
                quantity = int(row.get("quantity") or 0)
                avg = float(row.get("avg_entry_price_cents") or 0.0)
                side_wins = (side == "YES" and bracket == winning) or (side == "NO" and bracket != winning)
                settlement_price = 100.0 if side_wins else 0.0
                realized = round(((settlement_price - avg) * quantity) / 100.0, 4)
                order = {
                    "action": "CLOSE_FAKE_POSITION",
                    "contract_ticker": row.get("contract_ticker"),
                    "side": side,
                    "quantity": quantity,
                    "limit_price_cents": settlement_price,
                    "metadata": {
                        "bracket_label": row.get("bracket_label"),
                        "decision_id": f"paper_settlement:{market_date or ''}",
                        "selected_candidate_id": f"SETTLEMENT:{winning}",
                        "fake_money_only": True,
                    },
                }
                fill_details = {
                    "settlement_action": "SETTLE",
                    "settlement_winning_bracket": winning,
                    "settlement_position_bracket": bracket,
                    "settlement_final_high_f": final_high_f,
                    "settlement_market_date": market_date,
                    "settlement_source": source,
                    "settlement_source_url": source_url,
                    "settled_result": 1 if side_wins else 0,
                    "avg_entry_price_cents": avg,
                    "position_id": str(row.get("id")),
                    "fill_price_source": "official_settlement",
                }
                fill = _fill_payload(
                    order,
                    action="CLOSE",
                    quantity=quantity,
                    price_cents=settlement_price,
                    realized_pnl=realized,
                    fill_details=fill_details,
                )
                settled.append(
                    {
                        "position_id": str(row.get("id")),
                        "contract_ticker": row.get("contract_ticker"),
                        "bracket": bracket,
                        "raw_bracket_label": row.get("bracket_label"),
                        "side": side,
                        "quantity": quantity,
                        "avg_entry_price_cents": avg,
                        "settled_result": 1 if side_wins else 0,
                        "settlement_price_cents": settlement_price,
                        "settlement_value_dollars": round((quantity * settlement_price) / 100.0, 4),
                        "realized_pnl_dollars": realized,
                        "fill": fill,
                    }
                )
                if dry_run:
                    continue
                conn.execute(
                    """
                    UPDATE trader_positions
                    SET updated_at_utc = ?, status = 'settled', quantity = 0,
                        realized_pnl_dollars = realized_pnl_dollars + ?
                    WHERE id = ?
                    """,
                    (now, realized, row["id"]),
                )
                self._insert_fill(conn, fill)
            canceled_order_ids = [str(row["id"]) for row in open_order_rows]
            if not dry_run and canceled_order_ids:
                conn.execute(
                    """
                    UPDATE trader_orders
                    SET status = 'canceled', updated_at_utc = ?
                    WHERE status = 'open'
                    """,
                    (now,),
                )
        settlement_value = round(
            sum(float(row.get("settlement_value_dollars") or 0.0) for row in settled),
            4,
        )
        realized_pnl = round(
            sum(float(row.get("realized_pnl_dollars") or 0.0) for row in settled),
            4,
        )
        if final_cash_dollars is None and cash_before_settlement is not None:
            final_cash_dollars = round(float(cash_before_settlement) + settlement_value, 4)
        result = {
            "executed": not dry_run,
            "dry_run": dry_run,
            "winning_bracket": winning,
            "final_high_f": final_high_f,
            "market_date": market_date,
            "source": source,
            "source_url": source_url,
            "settlement_status": settlement_status,
            "race_id": race_id,
            "event_ticker": event_ticker,
            "station": station,
            "settled_positions": settled,
            "positions_settled": len(settled),
            "contracts_settled": sum(int(row.get("quantity") or 0) for row in settled),
            "settlement_value_dollars": settlement_value,
            "realized_pnl_dollars": realized_pnl,
            "starting_cash": starting_cash,
            "cash_before_settlement": cash_before_settlement,
            "final_cash_dollars": final_cash_dollars,
            "open_orders_canceled": len(canceled_order_ids),
            "canceled_order_ids": canceled_order_ids,
        }
        if not dry_run and settlement_status == "final_official":
            self._insert_paper_settlement(result)
            result["settlement_recorded"] = True
        else:
            result["settlement_recorded"] = False
        return result

    def _insert_paper_settlement(self, result: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO paper_settlements (
                    race_id,
                    event_ticker,
                    market_date,
                    station,
                    final_high_f,
                    winning_bracket,
                    settlement_source,
                    settlement_source_url,
                    settlement_status,
                    dry_run,
                    starting_cash,
                    cash_before_settlement,
                    settlement_value_dollars,
                    final_cash_dollars,
                    realized_pnl_dollars,
                    raw_result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.get("race_id"),
                    result.get("event_ticker"),
                    result.get("market_date"),
                    result.get("station"),
                    result.get("final_high_f"),
                    result.get("winning_bracket"),
                    result.get("source"),
                    result.get("source_url"),
                    result.get("settlement_status"),
                    1 if result.get("dry_run") else 0,
                    result.get("starting_cash"),
                    result.get("cash_before_settlement"),
                    result.get("settlement_value_dollars"),
                    result.get("final_cash_dollars"),
                    result.get("realized_pnl_dollars"),
                    json.dumps(result, sort_keys=True),
                ),
            )

    def _load_open_order_rows(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM trader_orders
                WHERE status = 'open'
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def process_pending_orders(
        self,
        market_brackets: list[MarketBracket],
        risk_check: Callable[[dict[str, Any], float], str | dict[str, Any] | None] | None = None,
        fill_price_mode: str = "conservative",
    ) -> list[dict[str, Any]]:
        """Fill open fake limit orders when the latest market snapshot crosses their limit."""
        results: list[dict[str, Any]] = []
        for row in self._load_open_order_rows():
            order = _order_payload_from_row(row)
            fill_check = _limit_fill_check(order, market_brackets, fill_price_mode=fill_price_mode)
            if not fill_check["fillable"]:
                continue

            order_id = int(row["id"])
            action = str(order.get("action") or "")
            risk_result = risk_check(order, float(fill_check["fill_price_cents"])) if risk_check else None
            risk_reason = _risk_rejection_reason(risk_result)
            risk_code = _risk_rejection_code(risk_result, risk_reason)
            revalidation = _risk_revalidation_payload(risk_result, performed=risk_check is not None)
            if risk_reason:
                self._mark_order_status(order_id, "rejected")
                results.append(
                    {
                        **fill_check,
                        "executed": False,
                        "reason": risk_reason,
                        **revalidation,
                        "fill_revalidation_passed": False,
                        "fill_revalidation_failure_code": risk_code,
                        "fill_revalidation_failure_message": risk_reason,
                        "order_id": str(order_id),
                        "limit_price_cents": row.get("limit_price_cents"),
                        "market_price_cents": fill_check.get("fill_market_price_cents"),
                        "pending_order_filled": False,
                    }
                )
                continue
            if action == "PLACE_FAKE_LIMIT_BUY":
                result = self._execute_buy(
                    order,
                    fill_price_cents=float(fill_check["fill_price_cents"]),
                    fill_details=fill_check,
                    source_order_id=order_id,
                )
            elif action in {"CLOSE_FAKE_POSITION", "PLACE_FAKE_LIMIT_SELL"}:
                result = self._execute_close(
                    order,
                    fill_price_cents=float(fill_check["fill_price_cents"]),
                    fill_details=fill_check,
                    source_order_id=order_id,
                )
            else:
                result = {"executed": False, "reason": f"unsupported pending fake action: {action}"}

            if result.get("executed"):
                self._mark_order_status(order_id, "filled")
            elif result.get("reason") == "no matching open fake position":
                self._mark_order_status(order_id, "rejected")
            results.append(
                {
                    **fill_check,
                    **result,
                    **revalidation,
                    "fill_revalidation_passed": bool(result.get("executed")) if risk_check is not None else None,
                    "fill_revalidation_failure_code": None if result.get("executed") else _risk_rejection_code(None, str(result.get("reason") or "")),
                    "fill_revalidation_failure_message": None if result.get("executed") else result.get("reason"),
                    "order_id": str(order_id),
                    "limit_price_cents": row.get("limit_price_cents"),
                    "market_price_cents": fill_check.get("fill_market_price_cents"),
                    "pending_order_filled": bool(result.get("executed")),
                }
            )
        return results

    def execute_paper_order(
        self,
        order: dict[str, Any],
        *,
        market_brackets: list[MarketBracket] | None = None,
        fill_price_mode: str = "conservative",
    ) -> dict[str, Any]:
        """Apply a validated fake order to the local trader-agent ledger."""
        action = str(order.get("action") or "")
        if action == "EXECUTE_FAKE_TAKER_BUY":
            limit_price = _float_or_zero(order.get("limit_price_cents"))
            bracket = None if market_brackets is None else _find_market_bracket(market_brackets, str(order.get("contract_ticker") or ""))
            market_price = limit_price
            if bracket is not None:
                executable = _market_entry_price_cents(bracket, str(order.get("side") or ""))
                if executable is None:
                    return {"executed": False, "reason": "entry ask missing from current market snapshot"}
                market_price = float(executable)
                if market_price > limit_price:
                    return {
                        "executed": False,
                        "reason": "taker ask moved above approved price",
                        "fill_limit_price_cents": limit_price,
                        "fill_market_price_cents": market_price,
                        "fill_price_mode": fill_price_mode,
                        "fill_price_source": "taker_ask_rejected",
                    }
            fill_price = market_price
            fill_check = {
                "fillable": True,
                "fill_price_cents": fill_price,
                "fill_limit_price_cents": limit_price,
                "market_price_cents": market_price,
                "fill_market_price_cents": market_price,
                "fill_price_mode": fill_price_mode,
                "fill_price_improvement_cents": 0.0,
                "fill_price_source": "taker_current_ask",
                "conservative_fill_adjustment_cents": 0.0,
                "fill_price_improvement_dollars": 0.0,
                "conservative_fill_adjustment_dollars": 0.0,
                "selected_execution_style": "taker",
                "entry_price_source": "ask",
                "reason": "fake taker buy filled at approved ask",
            }
            result = self._execute_buy(
                order,
                fill_price_cents=float(fill_check["fill_price_cents"]),
                fill_details=fill_check,
            )
            return {**result, **fill_check}
        if action == "PLACE_FAKE_LIMIT_BUY":
            fill_check = _limit_fill_check(order, market_brackets, fill_price_mode=fill_price_mode)
            if not fill_check["fillable"]:
                return self._stage_limit_order(order, fill_check)
            result = self._execute_buy(
                order,
                fill_price_cents=float(fill_check["fill_price_cents"]),
                fill_details=fill_check,
            )
            return {**result, **fill_check}
        if action in {"CLOSE_FAKE_POSITION", "PLACE_FAKE_LIMIT_SELL"}:
            fill_check = _limit_fill_check(order, market_brackets, fill_price_mode=fill_price_mode)
            if not fill_check["fillable"]:
                if not self._has_open_position(order):
                    return {"executed": False, "reason": "no matching open fake position"}
                return self._stage_limit_order(order, fill_check)
            result = self._execute_close(
                order,
                fill_price_cents=float(fill_check["fill_price_cents"]),
                fill_details=fill_check,
            )
            return {**result, **fill_check}
        if action == "CANCEL_FAKE_ORDER":
            return self._execute_cancel(order)
        return {"executed": False, "reason": f"unsupported fake paper action: {action}"}

    def _execute_buy(
        self,
        order: dict[str, Any],
        *,
        fill_price_cents: float | None = None,
        fill_details: dict[str, Any] | None = None,
        source_order_id: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        ticker = str(order.get("contract_ticker") or "")
        side = str(order.get("side") or "")
        quantity = int(order.get("quantity") or 0)
        price = float(fill_price_cents if fill_price_cents is not None else order.get("limit_price_cents") or 0)
        if not ticker or side not in {"YES", "NO"} or quantity <= 0 or price <= 0:
            return {"executed": False, "reason": "malformed fake buy order"}
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                """
                SELECT * FROM trader_positions
                WHERE status = 'open' AND contract_ticker = ? AND side = ?
                ORDER BY id LIMIT 1
                """,
                (ticker, side),
            ).fetchone()
            if existing:
                row = dict(existing)
                old_qty = int(row["quantity"])
                old_avg = float(row["avg_entry_price_cents"])
                new_qty = old_qty + quantity
                new_avg = ((old_qty * old_avg) + (quantity * price)) / new_qty
                conn.execute(
                    """
                    UPDATE trader_positions
                    SET updated_at_utc = ?, quantity = ?, avg_entry_price_cents = ?
                    WHERE id = ?
                    """,
                    (now, new_qty, new_avg, row["id"]),
                )
                position_id = int(row["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO trader_positions (
                        created_at_utc, updated_at_utc, status, contract_ticker,
                        bracket_label, side, quantity, avg_entry_price_cents,
                        metadata_json
                    ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        now,
                        ticker,
                        order.get("metadata", {}).get("bracket_label"),
                        side,
                        quantity,
                        price,
                        json.dumps(order.get("metadata") or {}, sort_keys=True),
                    ),
                )
                position_id = int(cur.lastrowid)
            fill = _fill_payload(
                order,
                action="BUY",
                quantity=quantity,
                price_cents=price,
                realized_pnl=0.0,
                fill_details=fill_details,
                source_order_id=source_order_id,
            )
            self._insert_fill(conn, fill)
        return {"executed": True, "action": "BUY", "position_id": position_id, "fill": fill}

    def _execute_close(
        self,
        order: dict[str, Any],
        *,
        fill_price_cents: float | None = None,
        fill_details: dict[str, Any] | None = None,
        source_order_id: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        ticker = str(order.get("contract_ticker") or "")
        side = str(order.get("side") or "")
        requested_qty = int(order.get("quantity") or 0)
        price = float(fill_price_cents if fill_price_cents is not None else order.get("limit_price_cents") or 0)
        if not ticker or side not in {"YES", "NO"} or requested_qty <= 0 or price < 0:
            return {"executed": False, "reason": "malformed fake close order"}
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row_obj = conn.execute(
                """
                SELECT * FROM trader_positions
                WHERE status = 'open' AND contract_ticker = ? AND side = ?
                ORDER BY id LIMIT 1
                """,
                (ticker, side),
            ).fetchone()
            if row_obj is None:
                return {"executed": False, "reason": "no matching open fake position"}
            row = dict(row_obj)
            open_qty = int(row["quantity"])
            fill_qty = min(requested_qty, open_qty)
            avg = float(row["avg_entry_price_cents"])
            realized = round(((price - avg) * fill_qty) / 100.0, 4)
            remaining = open_qty - fill_qty
            status = "closed" if remaining <= 0 else "open"
            conn.execute(
                """
                UPDATE trader_positions
                SET updated_at_utc = ?, status = ?, quantity = ?,
                    realized_pnl_dollars = realized_pnl_dollars + ?
                WHERE id = ?
                """,
                (now, status, remaining, realized, row["id"]),
            )
            fill = _fill_payload(
                order,
                action="CLOSE",
                quantity=fill_qty,
                price_cents=price,
                realized_pnl=realized,
                fill_details=fill_details,
                source_order_id=source_order_id,
            )
            self._insert_fill(conn, fill)
        return {
            "executed": True,
            "action": "CLOSE",
            "position_id": int(row["id"]),
            "closed_quantity": fill_qty,
            "remaining_quantity": remaining,
            "realized_pnl_dollars": realized,
            "fill": fill,
        }

    def _execute_cancel(self, order: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(order.get("metadata", {}).get("selected_candidate_id") or "")
        order_id = candidate_id.split(":", 1)[0] if candidate_id else str(order.get("order_id") or "")
        if not order_id:
            return {"executed": False, "reason": "cancel missing fake order id"}
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                """
                UPDATE trader_orders
                SET status = 'canceled', updated_at_utc = ?
                WHERE id = ? AND status = 'open'
                """,
                (_now(), order_id),
            )
        return {"executed": cur.rowcount > 0, "action": "CANCEL", "order_id": order_id}

    def _stage_limit_order(self, order: dict[str, Any], fill_check: dict[str, Any]) -> dict[str, Any]:
        ticker = str(order.get("contract_ticker") or "")
        side = str(order.get("side") or "")
        quantity = int(order.get("quantity") or 0)
        limit_price = float(order.get("limit_price_cents") or 0)
        action = str(order.get("action") or "")
        if not ticker or side not in {"YES", "NO"} or quantity <= 0 or limit_price <= 0:
            return {"executed": False, "reason": "malformed fake limit order"}
        now = _now()
        order_json = {
            **order,
            "pending_reason": fill_check.get("reason"),
            "current_market_price_cents": fill_check.get("market_price_cents"),
            "fake_money_only": True,
        }
        with sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                """
                INSERT INTO trader_orders (
                    created_at_utc, updated_at_utc, status, action, contract_ticker,
                    bracket_label, side, quantity, limit_price_cents, decision_id,
                    selected_candidate_id, order_json
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    action,
                    ticker,
                    (order.get("metadata") or {}).get("bracket_label"),
                    side,
                    quantity,
                    limit_price,
                    (order.get("metadata") or {}).get("decision_id"),
                    (order.get("metadata") or {}).get("selected_candidate_id"),
                    json.dumps(order_json, sort_keys=True),
                ),
            )
            order_id = int(cur.lastrowid)
        return {
            "executed": False,
            "accepted": True,
            "action": "OPEN_LIMIT_ORDER",
            "status": "open",
            "order_id": str(order_id),
            "reason": fill_check.get("reason"),
            "market_price_cents": fill_check.get("market_price_cents"),
            "limit_price_cents": limit_price,
        }

    def _mark_order_status(self, order_id: int, status: str) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                UPDATE trader_orders
                SET status = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (status, _now(), order_id),
            )

    def _has_open_position(self, order: dict[str, Any]) -> bool:
        ticker = str(order.get("contract_ticker") or "")
        side = str(order.get("side") or "")
        if not ticker or side not in {"YES", "NO"}:
            return False
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM trader_positions
                WHERE status = 'open' AND contract_ticker = ? AND side = ? AND quantity > 0
                LIMIT 1
                """,
                (ticker, side),
            ).fetchone()
        return row is not None

    def _insert_fill(self, conn: sqlite3.Connection, fill: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO trader_fills (
                created_at_utc, action, contract_ticker, bracket_label, side,
                quantity, price_cents, gross_value_dollars, realized_pnl_dollars,
                decision_id, selected_candidate_id, fill_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill["created_at_utc"],
                fill["action"],
                fill.get("contract_ticker"),
                fill.get("bracket_label"),
                fill.get("side"),
                fill["quantity"],
                fill["price_cents"],
                fill["gross_value_dollars"],
                fill["realized_pnl_dollars"],
                fill.get("decision_id"),
                fill.get("selected_candidate_id"),
                json.dumps(fill, sort_keys=True),
            ),
        )


def _fill_payload(
    order: dict[str, Any],
    *,
    action: str,
    quantity: int,
    price_cents: float,
    realized_pnl: float,
    fill_details: dict[str, Any] | None = None,
    source_order_id: int | None = None,
) -> dict[str, Any]:
    metadata = order.get("metadata") or {}
    fill_details = fill_details or {}
    return {
        "created_at_utc": _now(),
        "action": action,
        "contract_ticker": order.get("contract_ticker"),
        "bracket_label": metadata.get("bracket_label"),
        "side": order.get("side"),
        "quantity": quantity,
        "price_cents": price_cents,
        "gross_value_dollars": round((quantity * price_cents) / 100.0, 4),
        "realized_pnl_dollars": realized_pnl,
        "decision_id": metadata.get("decision_id"),
        "selected_candidate_id": metadata.get("selected_candidate_id"),
        "source_order_id": str(source_order_id) if source_order_id is not None else None,
        **{key: fill_details.get(key) for key in _FILL_DEBUG_KEYS if key in fill_details},
    }


def _canonical_settlement_label(value: Any) -> str:
    text = str(value or "").strip()
    cleaned = (
        text.replace("degrees", "")
        .replace("degree", "")
        .replace("deg", "")
        .replace("F", "")
        .replace("f", "")
        .replace("Â°", "")
        .replace("°", "")
        .replace("â€“", "-")
        .replace("â€”", "-")
        .strip()
    )
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", cleaned)
    if range_match:
        lower = float(range_match.group(1))
        upper = float(range_match.group(2))
        return f"{_simple_number(lower)}-{_simple_number(upper)}"
    threshold_match = re.search(r"([<>]=?)\s*(\d+(?:\.\d+)?)", cleaned)
    if threshold_match:
        prefix = "<" if threshold_match.group(1).startswith("<") else ">"
        return f"{prefix}{_simple_number(float(threshold_match.group(2)))}"
    b_match = re.search(r"\bB(\d+)\.5\b", cleaned, flags=re.IGNORECASE)
    if b_match:
        lower = int(b_match.group(1))
        return f"{lower}-{lower + 1}"
    return cleaned


def _simple_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _position_row_to_context_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        metadata = json.loads(row.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "position_id": f"trader:{row['id']}",
        "contract_ticker": row["contract_ticker"],
        "bracket_label": row.get("bracket_label"),
        "side": row["side"],
        "quantity": int(row["quantity"]),
        "avg_entry_price_cents": float(row["avg_entry_price_cents"]),
        "opened_at_utc": row.get("created_at_utc"),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _order_row_to_context_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        order_json = json.loads(row.get("order_json") or "{}")
    except json.JSONDecodeError:
        order_json = {}
    metadata = order_json.get("metadata") if isinstance(order_json, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "order_id": str(row["id"]),
        "created_at_utc": row.get("created_at_utc"),
        "updated_at_utc": row.get("updated_at_utc"),
        "action": row.get("action"),
        "contract_ticker": row.get("contract_ticker"),
        "bracket_label": row.get("bracket_label"),
        "side": row.get("side"),
        "quantity": int(row.get("quantity") or 0),
        "limit_price_cents": row.get("limit_price_cents"),
        "status": row.get("status"),
        "selected_candidate_id": row.get("selected_candidate_id"),
        "metadata": metadata,
    }


def _fill_row_to_context_dict(row: dict[str, Any]) -> dict[str, Any]:
    try:
        fill_json = json.loads(row.get("fill_json") or "{}")
    except json.JSONDecodeError:
        fill_json = {}
    return {
        **fill_json,
        "fill_id": str(row.get("id")),
        "created_at_utc": row.get("created_at_utc"),
        "action": row.get("action"),
        "contract_ticker": row.get("contract_ticker"),
        "bracket_label": row.get("bracket_label"),
        "side": row.get("side"),
        "quantity": int(row.get("quantity") or 0),
        "price_cents": float(row.get("price_cents") or 0),
        "gross_value_dollars": float(row.get("gross_value_dollars") or 0),
        "realized_pnl_dollars": float(row.get("realized_pnl_dollars") or 0),
        "decision_id": row.get("decision_id"),
        "selected_candidate_id": row.get("selected_candidate_id"),
    }


def _order_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(row.get("order_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload.setdefault("action", row.get("action"))
    payload.setdefault("contract_ticker", row.get("contract_ticker"))
    payload.setdefault("side", row.get("side"))
    payload.setdefault("quantity", row.get("quantity"))
    payload.setdefault("limit_price_cents", row.get("limit_price_cents"))
    metadata = payload.setdefault("metadata", {})
    if isinstance(metadata, dict):
        metadata.setdefault("bracket_label", row.get("bracket_label"))
        metadata.setdefault("decision_id", row.get("decision_id"))
        metadata.setdefault("selected_candidate_id", row.get("selected_candidate_id"))
    return payload


def _limit_fill_check(
    order: dict[str, Any],
    market_brackets: list[MarketBracket] | None,
    *,
    fill_price_mode: str = "conservative",
) -> dict[str, Any]:
    fill_price_mode = _normalize_fill_price_mode(fill_price_mode)
    limit_price = _float_or_zero(order.get("limit_price_cents"))
    action = str(order.get("action") or "")
    side = str(order.get("side") or "")
    if market_brackets is None:
        return {
            "fillable": True,
            "fill_price_cents": limit_price,
            "fill_limit_price_cents": limit_price,
            "market_price_cents": limit_price,
            "fill_market_price_cents": limit_price,
            "fill_price_mode": fill_price_mode,
            "fill_price_improvement_cents": 0.0,
            "fill_price_source": "no_market_snapshot_limit",
            "conservative_fill_adjustment_cents": 0.0,
            "fill_price_improvement_dollars": 0.0,
            "reason": "no market snapshot supplied; filled at limit price",
        }
    bracket = _find_market_bracket(market_brackets, str(order.get("contract_ticker") or ""))
    if bracket is None:
        return {"fillable": False, "market_price_cents": None, "reason": "contract missing from current market snapshot"}
    if action == "PLACE_FAKE_LIMIT_BUY":
        market_price = _market_entry_price_cents(bracket, side)
        if market_price is None:
            return {"fillable": False, "market_price_cents": None, "reason": "entry ask missing from current market snapshot"}
        fillable = market_price <= limit_price
        fill_price, source = _select_limit_fill_price(
            action=action,
            limit_price=limit_price,
            market_price=float(market_price),
            fill_price_mode=fill_price_mode,
        )
        quantity = int(order.get("quantity") or 0)
        improvement = _favorable_fill_improvement_cents(
            action=action,
            limit_price=limit_price,
            fill_price=fill_price,
        )
        conservative_adjustment = _conservative_fill_adjustment_cents(
            action=action,
            limit_price=limit_price,
            market_price=float(market_price),
            fill_price_mode=fill_price_mode,
        )
        return {
            "fillable": fillable,
            "fill_price_cents": fill_price,
            "fill_limit_price_cents": limit_price,
            "market_price_cents": market_price,
            "fill_market_price_cents": market_price,
            "fill_price_mode": fill_price_mode,
            "fill_price_improvement_cents": improvement,
            "fill_price_source": source,
            "conservative_fill_adjustment_cents": conservative_adjustment,
            "fill_price_improvement_dollars": round((quantity * improvement) / 100.0, 4),
            "reason": None if fillable else "buy limit below current ask",
        }
    if action in {"CLOSE_FAKE_POSITION", "PLACE_FAKE_LIMIT_SELL"}:
        market_price = _market_exit_price_cents(bracket, side)
        if market_price is None:
            return {"fillable": False, "market_price_cents": None, "reason": "exit bid missing from current market snapshot"}
        fillable = market_price >= limit_price
        fill_price, source = _select_limit_fill_price(
            action=action,
            limit_price=limit_price,
            market_price=float(market_price),
            fill_price_mode=fill_price_mode,
        )
        quantity = int(order.get("quantity") or 0)
        improvement = _favorable_fill_improvement_cents(
            action=action,
            limit_price=limit_price,
            fill_price=fill_price,
        )
        conservative_adjustment = _conservative_fill_adjustment_cents(
            action=action,
            limit_price=limit_price,
            market_price=float(market_price),
            fill_price_mode=fill_price_mode,
        )
        return {
            "fillable": fillable,
            "fill_price_cents": fill_price,
            "fill_limit_price_cents": limit_price,
            "market_price_cents": market_price,
            "fill_market_price_cents": market_price,
            "fill_price_mode": fill_price_mode,
            "fill_price_improvement_cents": improvement,
            "fill_price_source": source,
            "conservative_fill_adjustment_cents": conservative_adjustment,
            "fill_price_improvement_dollars": round((quantity * improvement) / 100.0, 4),
            "reason": None if fillable else "sell limit above current bid",
        }
    return {"fillable": False, "market_price_cents": None, "reason": f"unsupported fake paper action: {action}"}


_FILL_DEBUG_KEYS = (
    "fill_price_cents",
    "fill_limit_price_cents",
    "fill_market_price_cents",
    "fill_price_mode",
    "fill_price_improvement_cents",
    "fill_price_source",
    "conservative_fill_adjustment_cents",
    "fill_price_improvement_dollars",
    "settlement_action",
    "settlement_winning_bracket",
    "settlement_position_bracket",
    "settlement_final_high_f",
    "settlement_market_date",
    "settlement_source",
    "settlement_source_url",
    "settled_result",
    "avg_entry_price_cents",
    "position_id",
)


def _normalize_fill_price_mode(value: str | None) -> str:
    mode = str(value or "conservative").strip().lower()
    if mode not in {"limit", "market", "conservative"}:
        raise ValueError("fill_price_mode must be limit, market, or conservative")
    return mode


def _select_limit_fill_price(
    *,
    action: str,
    limit_price: float,
    market_price: float,
    fill_price_mode: str,
) -> tuple[float, str]:
    if fill_price_mode == "market":
        improvement = _favorable_fill_improvement_cents(
            action=action,
            limit_price=limit_price,
            fill_price=market_price,
        )
        return market_price, "market_price_improvement" if improvement > 0 else "market_price"
    if fill_price_mode == "limit":
        return limit_price, "limit_price"
    return limit_price, "conservative_limit_price"


def _favorable_fill_improvement_cents(*, action: str, limit_price: float, fill_price: float) -> float:
    if action == "PLACE_FAKE_LIMIT_BUY":
        return round(max(0.0, limit_price - fill_price), 4)
    if action in {"CLOSE_FAKE_POSITION", "PLACE_FAKE_LIMIT_SELL"}:
        return round(max(0.0, fill_price - limit_price), 4)
    return 0.0


def _conservative_fill_adjustment_cents(
    *,
    action: str,
    limit_price: float,
    market_price: float,
    fill_price_mode: str,
) -> float:
    if fill_price_mode == "market":
        return 0.0
    return _favorable_fill_improvement_cents(
        action=action,
        limit_price=limit_price,
        fill_price=market_price,
    )


def _risk_rejection_reason(risk_result: str | dict[str, Any] | None) -> str | None:
    if risk_result is None:
        return None
    if isinstance(risk_result, str):
        return risk_result
    if not bool(risk_result.get("passed")):
        return str(risk_result.get("reason") or "fill_rejected_revalidation_incomplete")
    return None


def _risk_rejection_code(risk_result: str | dict[str, Any] | None, reason: str | None) -> str:
    if isinstance(risk_result, dict) and risk_result.get("failure_code"):
        return str(risk_result["failure_code"])
    if "incomplete" in str(reason):
        return "fill_rejected_revalidation_incomplete"
    return "fill_rejected_edge_no_longer_valid"


def _risk_revalidation_payload(
    risk_result: str | dict[str, Any] | None,
    *,
    performed: bool,
) -> dict[str, Any]:
    if isinstance(risk_result, dict):
        return {
            "fill_revalidation_performed": performed,
            "fill_revalidated_net_edge_cents": risk_result.get("fill_revalidated_net_edge_cents"),
            "fill_revalidated_fair_value_cents": risk_result.get("fill_revalidated_fair_value_cents"),
            "fill_revalidated_market_age_seconds": risk_result.get("fill_revalidated_market_age_seconds"),
            "fill_revalidated_model_age_seconds": risk_result.get("fill_revalidated_model_age_seconds"),
        }
    return {
        "fill_revalidation_performed": performed,
        "fill_revalidated_net_edge_cents": None,
        "fill_revalidated_fair_value_cents": None,
        "fill_revalidated_market_age_seconds": None,
        "fill_revalidated_model_age_seconds": None,
    }


def _find_market_bracket(market_brackets: list[MarketBracket], contract_ticker: str) -> MarketBracket | None:
    return next((bracket for bracket in market_brackets if bracket.contract_ticker == contract_ticker), None)


def _market_entry_price_cents(bracket: MarketBracket, side: str) -> int | None:
    if side == "YES":
        return bracket.effective_yes_ask_cents()
    if side == "NO":
        return bracket.effective_no_ask_cents()
    return None


def _market_exit_price_cents(bracket: MarketBracket, side: str) -> int | None:
    if side == "YES":
        return bracket.effective_yes_bid_cents()
    if side == "NO":
        return bracket.effective_no_bid_cents()
    return None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
