from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from kalshi_weather.runtime_paths import (
    get_bot_trust_report_path,
    get_final_results_path,
    get_journal_path,
    get_run_dir,
    sanitize_run_id,
)


def write_trader_run_review_reports(
    *,
    run_id: str,
    race_id: str | None = None,
    target_date: str | None = None,
    series: str | None = None,
    station: str | None = None,
    event_ticker: str | None = None,
    journal_path: str | Path | None = None,
    debug_dir: str | Path | None = None,
    starting_cash: float = 1000.0,
    settlement_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write final_results.json and bot_trust_report.json for a fake-money run.

    These reports are diagnostic artifacts only. They do not influence sizing,
    risk limits, fake execution, or real order placement.
    """

    safe_run_id = sanitize_run_id(run_id)
    debug_dir_path = Path(debug_dir) if debug_dir is not None else get_run_dir(safe_run_id)
    journal_path_path = Path(journal_path) if journal_path is not None else get_journal_path(race_id or safe_run_id)
    debug_dir_path.mkdir(parents=True, exist_ok=True)

    journal = _load_journal_snapshot(journal_path_path)
    decision_rows = _load_decision_rows(debug_dir_path / "decisions.jsonl")
    latest_payload = _read_json(debug_dir_path / "latest.json")
    latest_run = journal["latest_run"] or {}
    context = latest_run.get("context") or latest_run.get("raw_context") or {}
    portfolio = _portfolio_from_payload(latest_run, settlement_payload)
    settlement = _settlement_from_payload_or_journal(settlement_payload, journal["settlements"])
    scenarios = latest_run.get("settlement_scenarios") or latest_payload.get("settlement_scenarios") or {}
    fills = journal["fills"]
    orders = journal["orders"]
    runs = journal["runs"]

    resolved_race_id = race_id or latest_run.get("race_id") or context.get("race_id") or safe_run_id
    resolved_target_date = target_date or context.get("market_date") or latest_run.get("target_date")
    resolved_series = series or context.get("series") or latest_run.get("series")
    resolved_station = station or context.get("station") or latest_run.get("station")
    resolved_event_ticker = (
        event_ticker
        or context.get("event_ticker")
        or latest_run.get("event_ticker")
        or _event_ticker_from_payload(latest_payload)
        or _derive_event_ticker(resolved_series, resolved_target_date)
    )

    final_status = _final_status(settlement, portfolio)
    settlement_source_status = _settlement_source_status(settlement)
    positions_settled = _int_or_zero(settlement.get("positions_settled") or settlement.get("positions_settled_count"))
    settled_pnl = _number_or_zero(settlement.get("realized_pnl_dollars") or settlement.get("final_paper_pnl"))
    closed_pnl = _number_or_zero(portfolio.get("closed_pnl_value"))
    realized_pnl = settled_pnl if positions_settled else closed_pnl
    final_cash = _first_number(
        settlement.get("final_cash_dollars"),
        portfolio.get("cash_value"),
        starting_cash + realized_pnl,
    )
    final_equity = _first_number(
        portfolio.get("equity_value"),
        portfolio.get("total_value"),
        settlement.get("final_cash_dollars"),
        final_cash,
    )

    run_identity = {
        "run_id": safe_run_id,
        "race_id": resolved_race_id,
        "target_date": resolved_target_date,
        "station": resolved_station,
        "series": resolved_series,
        "event_ticker": resolved_event_ticker,
        "journal_path": str(journal_path_path),
        "debug_dir": str(debug_dir_path),
        "fake_money_only": True,
        "real_orders_available": False,
        "live_trading_enabled": False,
    }
    final_pnl = {
        "starting_cash": _first_number(portfolio.get("starting_cash"), starting_cash),
        "final_cash": final_cash,
        "final_equity": final_equity,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": _number_or_zero(portfolio.get("open_pnl_value")),
        "settled_pnl": settled_pnl,
        "fees_paid": _fees_paid_dollars(fills),
        "max_drawdown": _max_drawdown(runs, portfolio),
        "final_status": final_status,
    }
    settlement_section = {
        "official_high_f": _first_number(settlement.get("final_high_f"), context.get("official_high_f")),
        "winning_bracket": _canonical_label(settlement.get("winning_bracket")),
        "settlement_source": settlement.get("source") or settlement.get("settlement_source"),
        "settlement_source_status": settlement_source_status,
        "positions_settled_count": positions_settled,
        "final_paper_pnl": settled_pnl,
    }
    portfolio_summary = {
        "open_positions_count": _int_or_zero(portfolio.get("open_positions_count") or len(journal["open_positions"])),
        "open_orders_count": _int_or_zero(portfolio.get("open_orders_count") or len(journal["open_orders"])),
        "open_exposure": _number_or_zero(portfolio.get("open_exposure_value")),
        "total_contracts": _int_or_zero(portfolio.get("total_contracts")),
        "closed_positions_count": _closed_position_count(fills),
        "canceled_orders_count": _canceled_order_count(orders, settlement),
    }
    scenario_summary = _scenario_summary(scenarios)
    runtime_diagnostics = _runtime_diagnostics_section(runs, latest_run, decision_rows)
    model_source_diagnostics = _latest_model_source_diagnostics(latest_run, decision_rows)
    latest_file_model_source = _latest_model_source_diagnostics(latest_payload, [])
    for key, value in latest_file_model_source.items():
        if model_source_diagnostics.get(key) is None and value is not None:
            model_source_diagnostics[key] = value
    profile_state = _profile_state_section(latest_run, decision_rows)
    final_results = {
        "run_identity": run_identity,
        **run_identity,
        "final_pnl": final_pnl,
        **final_pnl,
        "settlement": settlement_section,
        **settlement_section,
        "portfolio_summary": portfolio_summary,
        **portfolio_summary,
        **scenario_summary,
        "runtime_diagnostics": runtime_diagnostics,
        "profile_state": profile_state,
        **profile_state,
        **model_source_diagnostics,
        "model_source": model_source_diagnostics,
        "model_source_diagnostics": model_source_diagnostics,
        "generated_at_utc": _utc_now_text(),
        "diagnostic_only": True,
    }

    clv = _clv_section(runs, latest_run)
    execution = _execution_section(runs, fills, orders, portfolio)
    risk = _risk_section(runs, portfolio, scenarios)
    trade_quality = _trade_quality_section(runs, fills)
    profile_behavior = _profile_behavior_section(runs)
    model_trust = _model_trust_section(latest_run, settlement_section)
    market_vs_model = _market_vs_model_section(runs, latest_run)
    trust_score = _trust_score_section(
        final_pnl=final_pnl,
        settlement=settlement_section,
        model_trust=model_trust,
        clv=clv,
        execution=execution,
        risk=risk,
        trade_quality=trade_quality,
    )
    warnings = list(trust_score.get("trust_score_warnings") or [])
    if not runs:
        warnings.append("no trader runs found in journal")
    if final_status == "open":
        warnings.append("market is not paper-settled yet")
    if model_source_diagnostics.get("model_source_degraded"):
        reason = model_source_diagnostics.get("model_source_degraded_reason") or "unknown"
        warnings.append(f"model source degraded: {reason}")
    if profile_state.get("profile_mismatch_warning"):
        warnings.append(str(profile_state["profile_mismatch_warning"]))

    bot_trust_report = {
        "run_identity": run_identity,
        "final_pnl": final_pnl,
        "settlement": settlement_section,
        "model_trust": model_trust,
        "market_vs_model": market_vs_model,
        "clv": clv,
        "execution": execution,
        "risk": risk,
        "trade_quality": trade_quality,
        "profile_behavior": profile_behavior,
        "profile_state": profile_state,
        "runtime_diagnostics": runtime_diagnostics,
        "model_source": model_source_diagnostics,
        "model_source_diagnostics": model_source_diagnostics,
        "trust_score": trust_score,
        "warnings": warnings,
        "diagnostic_only": True,
        "fake_money_only": True,
        "real_orders_available": False,
        "live_trading_enabled": False,
        "generated_at_utc": _utc_now_text(),
    }

    final_path = get_final_results_path(safe_run_id) if debug_dir is None else debug_dir_path / "final_results.json"
    trust_path = get_bot_trust_report_path(safe_run_id) if debug_dir is None else debug_dir_path / "bot_trust_report.json"
    _write_json(final_path, final_results)
    _write_json(trust_path, bot_trust_report)
    return {
        "final_results_path": str(final_path),
        "bot_trust_report_path": str(trust_path),
        "final_results": final_results,
        "bot_trust_report": bot_trust_report,
    }


def _load_journal_snapshot(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "runs": [],
            "latest_run": {},
            "fills": [],
            "orders": [],
            "open_orders": [],
            "open_positions": [],
            "settlements": [],
        }
    return {
        "runs": _query_json_rows(path, "trader_runs", "run_json", "id"),
        "latest_run": (_query_json_rows(path, "trader_runs", "run_json", "id", limit=1, descending=True) or [{}])[0],
        "fills": _load_fills(path),
        "orders": _load_orders(path),
        "open_orders": [row for row in _load_orders(path) if row.get("status") == "open"],
        "open_positions": _load_positions(path, status="open"),
        "settlements": _load_settlements(path),
    }


def _load_decision_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                payload = _loads_dict(line)
                if payload:
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _query_json_rows(
    path: Path,
    table: str,
    column: str,
    order_column: str,
    *,
    limit: int | None = None,
    descending: bool = False,
) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(path) as conn:
            if not _table_exists(conn, table):
                return []
            order = "DESC" if descending else "ASC"
            query = f"SELECT {column} FROM {table} ORDER BY {order_column} {order}"  # noqa: S608
            params: tuple[Any, ...] = ()
            if limit is not None:
                query += " LIMIT ?"
                params = (limit,)
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []
    payloads: list[dict[str, Any]] = []
    for (text,) in rows:
        payload = _loads_dict(text)
        if payload:
            payloads.append(payload)
    return payloads


def _load_fills(path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(path) as conn:
            if not _table_exists(conn, "trader_fills"):
                return []
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trader_fills ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    fills: list[dict[str, Any]] = []
    for row in rows:
        base = dict(row)
        payload = _loads_dict(base.get("fill_json"))
        fills.append({**payload, **base, "fill_id": str(base.get("id"))})
    return fills


def _load_orders(path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(path) as conn:
            if not _table_exists(conn, "trader_orders"):
                return []
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trader_orders ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    orders: list[dict[str, Any]] = []
    for row in rows:
        base = dict(row)
        payload = _loads_dict(base.get("order_json"))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        orders.append({**payload, **base, "order_id": str(base.get("id")), "metadata": metadata})
    return orders


def _load_positions(path: Path, *, status: str | None = None) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(path) as conn:
            if not _table_exists(conn, "trader_positions"):
                return []
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute("SELECT * FROM trader_positions WHERE status = ? ORDER BY id", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trader_positions ORDER BY id").fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def _load_settlements(path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(path) as conn:
            if not _table_exists(conn, "paper_settlements"):
                return []
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM paper_settlements ORDER BY id DESC").fetchall()
    except sqlite3.Error:
        return []
    settlements: list[dict[str, Any]] = []
    for row in rows:
        base = dict(row)
        raw = _loads_dict(base.get("raw_result_json"))
        settlements.append({**raw, **base})
    return settlements


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _portfolio_from_payload(
    latest_run: dict[str, Any],
    settlement_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if settlement_payload and isinstance(settlement_payload.get("portfolio_after"), dict):
        return dict(settlement_payload["portfolio_after"])
    for key in ("portfolio", "portfolio_after", "portfolio_before"):
        if isinstance(latest_run.get(key), dict):
            return dict(latest_run[key])
    return {}


def _settlement_from_payload_or_journal(
    settlement_payload: dict[str, Any] | None,
    settlements: list[dict[str, Any]],
) -> dict[str, Any]:
    if settlement_payload:
        nested = settlement_payload.get("settlement")
        if isinstance(nested, dict):
            return {**settlement_payload, **nested}
        return settlement_payload
    return settlements[0] if settlements else {}


def _final_status(settlement: dict[str, Any], portfolio: dict[str, Any]) -> str:
    status = str(settlement.get("settlement_status") or "").strip().lower()
    if status in {"final_official", "final_settled", "official_nws", "kalshi_final"}:
        return "final_settled"
    if status == "provisional":
        return "provisional_settled"
    if settlement.get("winning_bracket"):
        return "provisional_settled"
    if _int_or_zero(portfolio.get("open_positions_count")) == 0 and _int_or_zero(portfolio.get("open_orders_count")) == 0:
        return "open"
    return "open"


def _settlement_source_status(settlement: dict[str, Any]) -> str:
    explicit = str(settlement.get("settlement_source_status") or "").strip().lower()
    if explicit in {"official_nws", "kalshi_final", "provisional", "unknown"}:
        return explicit
    status = str(settlement.get("settlement_status") or "").strip().lower()
    source = str(settlement.get("source") or settlement.get("settlement_source") or "").lower()
    if "kalshi" in source:
        return "kalshi_final"
    if status in {"final_official", "official_nws"}:
        return "official_nws"
    if status == "provisional":
        return "provisional"
    return "unknown"


def _scenario_summary(scenarios: dict[str, Any]) -> dict[str, Any]:
    return {
        "best_settlement_scenario": scenarios.get("best_case_scenario"),
        "worst_settlement_scenario": scenarios.get("worst_case_scenario"),
        "worst_case_loss_dollars": _number_or_zero(scenarios.get("worst_case_loss_dollars")),
        "best_case_gain_dollars": _number_or_zero(scenarios.get("best_case_gain_dollars")),
    }


def _model_trust_section(latest_run: dict[str, Any], settlement: dict[str, Any]) -> dict[str, Any]:
    context = latest_run.get("context") or {}
    probs = _probability_rows(context)
    model_top = _canonical_label(
        _model_top_bracket(context, probs)
        or _nested_get(latest_run, "decision_audit", "models", "top_bracket_after_floor")
        or _nested_get(latest_run, "decision_audit", "models", "top_bracket")
        or _nested_get(latest_run, "rules_engine", "probability_floor", "top_bracket_after_floor")
        or _nested_get(latest_run, "models", "top_bracket_after_floor")
        or _nested_get(latest_run, "models", "top_bracket")
    )
    actual = _canonical_label(settlement.get("winning_bracket"))
    winner_prob = _probability_for_label(probs, actual)
    consensus_center = _first_number(
        _nested_get(latest_run, "rules_engine", "model_consensus", "consensus_center_f"),
        _nested_get(latest_run, "rules_engine", "consensus", "consensus_center_f"),
        _nested_get(latest_run, "rules_engine", "consensus", "center_f"),
        _nested_get(latest_run, "decision_audit", "models", "consensus_center_f"),
        _nested_get(latest_run, "models", "consensus_center_f"),
        context.get("estimated_high_f"),
        context.get("current_estimate_f"),
    )
    official_high = _first_number(settlement.get("official_high_f"))
    return {
        "model_top_bracket": model_top,
        "actual_winning_bracket": actual,
        "was_model_top_correct": None if not actual else model_top == actual,
        "model_probability_of_winner": winner_prob,
        "model_probability_of_traded_brackets": _probability_for_traded_brackets(latest_run, probs),
        "consensus_center_f": consensus_center,
        "official_high_f": official_high,
        "absolute_temp_error": (
            round(abs(consensus_center - official_high), 4)
            if consensus_center is not None and official_high is not None
            else None
        ),
        "consensus_spread_f": _first_number(
            _nested_get(latest_run, "rules_engine", "model_consensus", "consensus_spread_f"),
            _nested_get(latest_run, "rules_engine", "consensus", "consensus_spread_f"),
            _nested_get(latest_run, "decision_audit", "models", "consensus_spread_f"),
            _nested_get(latest_run, "models", "consensus_spread_f"),
        ),
        "full_model_spread_f": _first_number(
            _nested_get(latest_run, "rules_engine", "model_consensus", "full_model_spread_f"),
            _nested_get(latest_run, "rules_engine", "consensus", "full_model_spread_f"),
            _nested_get(latest_run, "decision_audit", "models", "full_model_spread_f"),
            _nested_get(latest_run, "decision_audit", "model_consensus", "full_model_spread_f"),
            _nested_get(latest_run, "models", "full_model_spread_f"),
        ),
        "model_disagreement_level": _first_text(
            _nested_get(latest_run, "rules_engine", "model_consensus", "model_disagreement_level"),
            _nested_get(latest_run, "rules_engine", "consensus", "model_disagreement_level"),
            _nested_get(latest_run, "decision_audit", "models", "model_disagreement_level"),
            _nested_get(latest_run, "decision_audit", "model_consensus", "model_disagreement_level"),
            _nested_get(latest_run, "models", "model_disagreement_level"),
        ),
        "model_confidence_level": _first_text(
            _nested_get(latest_run, "rules_engine", "model_consensus", "model_confidence_level"),
            _nested_get(latest_run, "rules_engine", "consensus", "model_confidence_level"),
            _nested_get(latest_run, "decision_audit", "models", "model_confidence_level"),
            _nested_get(latest_run, "decision_audit", "model_consensus", "model_confidence_level"),
            _nested_get(latest_run, "models", "model_confidence_level"),
        ),
        "model_cluster_status": _first_text(
            _nested_get(latest_run, "rules_engine", "model_consensus", "model_cluster_status"),
            _nested_get(latest_run, "rules_engine", "consensus", "model_cluster_status"),
            _nested_get(latest_run, "decision_audit", "models", "model_cluster_status"),
            _nested_get(latest_run, "decision_audit", "model_consensus", "model_cluster_status"),
            _nested_get(latest_run, "models", "model_cluster_status"),
        ),
    }


def _market_vs_model_section(runs: list[dict[str, Any]], latest_run: dict[str, Any]) -> dict[str, Any]:
    candidates = _candidate_rows(runs or [latest_run])
    model_probs: list[float] = []
    market_probs: list[float] = []
    final_probs: list[float] = []
    disagreements = 0
    disagreement_trades = 0
    for row in candidates:
        fair = _first_number(row.get("fair_value_cents"), row.get("model_fair_value_cents"))
        price = _first_number(row.get("limit_price_cents"), row.get("entry_price_cents"), row.get("price_cents"))
        if fair is None or price is None:
            continue
        model_probs.append(fair / 100.0)
        market_probs.append(price / 100.0)
        final_probs.append((_first_number(row.get("final_trade_probability"), fair) or fair) / 100.0)
        if abs(fair - price) >= 10:
            disagreements += 1
            if row.get("selected") or row.get("eligible"):
                disagreement_trades += 1
    return {
        "avg_model_probability": _avg(model_probs),
        "avg_market_implied_probability": _avg(market_probs),
        "avg_final_trade_probability": _avg(final_probs),
        "avg_model_minus_market_probability": (
            round(_avg(model_probs) - _avg(market_probs), 4)
            if model_probs and market_probs
            else None
        ),
        "model_weight_used_by_profile": _nested_get(latest_run, "profile", "model_weight"),
        "market_weight_used_by_profile": _nested_get(latest_run, "profile", "market_weight"),
        "count_large_model_market_disagreements": disagreements,
        "trades_taken_from_large_disagreements": disagreement_trades,
    }


def _clv_section(runs: list[dict[str, Any]], latest_run: dict[str, Any]) -> dict[str, Any]:
    summary = latest_run.get("clv_summary") if isinstance(latest_run.get("clv_summary"), dict) else {}
    samples: list[dict[str, Any]] = []
    for run in runs:
        samples.extend(row for row in run.get("clv_samples") or [] if isinstance(row, dict))
    latest_values = [_number_or_none(row.get("latest_clv_cents")) for row in samples]
    latest_values = [value for value in latest_values if value is not None]
    adverse = sum(1 for value in latest_values if value < 0)
    return {
        "fills_count": _int_or_zero(summary.get("fills_count") or latest_run.get("fills_count") or len(samples)),
        "avg_clv_5m_cents": _avg_key(samples, "clv_5m_cents"),
        "avg_clv_15m_cents": _avg_key(samples, "clv_15m_cents"),
        "avg_clv_30m_cents": _avg_key(samples, "clv_30m_cents"),
        "avg_clv_60m_cents": _avg_key(samples, "clv_60m_cents"),
        "latest_avg_clv_cents": _first_number(summary.get("avg_latest_clv_cents"), _avg(latest_values)),
        "percent_positive_clv": (
            round(100.0 * sum(1 for value in latest_values if value > 0) / len(latest_values), 2)
            if latest_values
            else None
        ),
        "adverse_selection_count": _int_or_zero(summary.get("adverse_selection_count") or adverse),
        "note": "no fills; CLV not available" if not samples else None,
    }


def _runtime_diagnostics_section(
    runs: list[dict[str, Any]],
    latest_run: dict[str, Any],
    decision_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    latest = latest_run.get("runtime_diagnostics") if isinstance(latest_run.get("runtime_diagnostics"), dict) else {}
    journal_rows = [
        run.get("runtime_diagnostics")
        for run in runs
        if isinstance(run.get("runtime_diagnostics"), dict)
    ]
    decision_runtime_rows = _decision_runtime_rows(decision_rows or [])
    iteration_rows = decision_runtime_rows or journal_rows
    last_runtime = iteration_rows[-1] if iteration_rows else {}

    elapsed = _elapsed_iteration_seconds(iteration_rows)
    requested_interval = _first_number(
        latest.get("requested_interval_seconds"),
        last_runtime.get("requested_interval_seconds"),
    )
    slow_threshold = _first_number(
        latest.get("slow_iteration_threshold_seconds"),
        last_runtime.get("slow_iteration_threshold_seconds"),
        max(30.0, requested_interval * 1.5) if requested_interval is not None else None,
        90.0,
    ) or 90.0
    slow_count = sum(1 for value in elapsed if value >= slow_threshold)
    slowest = max(
        iteration_rows,
        key=lambda row: _row_elapsed_seconds(row) or 0.0,
        default={},
    )
    latest_completed = _positive_int(latest.get("iterations_completed"), latest.get("actual_iterations"))
    actual_iterations = latest_completed or len(iteration_rows) or len(decision_rows or []) or len(runs)
    iterations_completed = actual_iterations
    expected_iterations = _first_int(
        latest.get("expected_iterations"),
        latest.get("iterations_requested_or_expected"),
        last_runtime.get("expected_iterations"),
        last_runtime.get("iterations_requested_or_expected"),
    )
    requested_duration = _first_number(
        latest.get("requested_duration_minutes"),
        last_runtime.get("requested_duration_minutes"),
    )
    if expected_iterations is None and requested_duration is not None and requested_interval:
        expected_iterations = int(math.ceil((requested_duration * 60.0) / requested_interval))

    first_row = iteration_rows[0] if iteration_rows else {}
    last_row = iteration_rows[-1] if iteration_rows else {}
    started = _first_text(
        latest.get("run_started_at_utc"),
        first_row.get("run_started_at_utc"),
        first_row.get("iteration_started_at_utc"),
        first_row.get("time_utc"),
        _iteration_timestamp_text(first_row),
    )
    ended = _first_text(
        latest.get("run_ended_at_utc") if latest_completed else None,
        last_row.get("run_ended_at_utc"),
        last_row.get("iteration_ended_at_utc"),
        last_row.get("time_utc"),
        _iteration_timestamp_text(last_row),
    )
    wall_minutes = _number_or_none(latest.get("actual_wall_clock_minutes")) if latest_completed else None
    if wall_minutes is None and started and ended:
        start_dt = _parse_datetime(started)
        end_dt = _parse_datetime(ended)
        if start_dt and end_dt:
            wall_minutes = round((end_dt - start_dt).total_seconds() / 60.0, 4)
    return {
        "requested_duration_minutes": requested_duration,
        "requested_interval_seconds": requested_interval,
        "expected_iterations": expected_iterations,
        "iterations_requested_or_expected": expected_iterations,
        "actual_iterations": actual_iterations,
        "iterations_completed": iterations_completed,
        "run_started_at_utc": started,
        "run_ended_at_utc": ended,
        "actual_wall_clock_minutes": wall_minutes,
        "avg_iteration_seconds": _first_number(latest.get("avg_iteration_seconds") if latest_completed else None, _avg(elapsed)),
        "median_iteration_seconds": _first_number(
            latest.get("median_iteration_seconds") if latest_completed else None,
            _median(elapsed),
        ),
        "max_iteration_seconds": _first_number(
            latest.get("max_iteration_seconds") if latest_completed else None,
            max(elapsed) if elapsed else None,
        ),
        "slow_iteration_count": (
            latest.get("slow_iteration_count")
            if latest_completed and latest.get("slow_iteration_count") is not None
            else slow_count
        ),
        "slow_iteration_threshold_seconds": slow_threshold,
        "slowest_iteration_number": latest.get("slowest_iteration_number") or slowest.get("iteration"),
        "slowest_iteration_reason_if_available": latest.get("slowest_iteration_reason_if_available")
        or slowest.get("slowest_iteration_reason_if_available"),
        "first_iteration_utc": latest.get("first_iteration_utc")
        or first_row.get("iteration_started_at_utc")
        or first_row.get("time_utc")
        or _iteration_timestamp_text(first_row),
        "last_iteration_utc": latest.get("last_iteration_utc")
        or last_row.get("iteration_ended_at_utc")
        or last_row.get("time_utc")
        or _iteration_timestamp_text(last_row),
    }


def _latest_model_source_diagnostics(latest_run: dict[str, Any], decision_rows: list[dict[str, Any]]) -> dict[str, Any]:
    source = latest_run.get("model_source") if isinstance(latest_run.get("model_source"), dict) else {}
    if not source:
        source = latest_run.get("model_source_diagnostics") if isinstance(latest_run.get("model_source_diagnostics"), dict) else {}
    if not source:
        source = _nested_get(latest_run, "decision_audit", "model_source")
        source = source if isinstance(source, dict) else {}
    if not source:
        summary = _nested_get(latest_run, "context", "recent_price_trend_summary", "model_source")
        source = summary if isinstance(summary, dict) else {}
    if not source and decision_rows:
        latest_decision = decision_rows[-1]
        source = latest_decision.get("model_source") if isinstance(latest_decision.get("model_source"), dict) else {}
        if not source:
            source = latest_decision.get("model_source_diagnostics") if isinstance(latest_decision.get("model_source_diagnostics"), dict) else {}
        if not source:
            summary = _nested_get(latest_decision, "context", "recent_price_trend_summary", "model_source")
            source = summary if isinstance(summary, dict) else {}
    keys = (
        "noaa_model_mode",
        "model_cache_used",
        "fast_model_cache_used",
        "noaa_last_refresh_utc",
        "noaa_next_refresh_utc",
        "noaa_cache_age_seconds",
        "noaa_cache_used",
        "model_fetch_elapsed_seconds",
        "noaa_fetch_elapsed_seconds",
        "open_meteo_fetch_elapsed_seconds",
        "fast_model_fetch_elapsed_seconds",
        "market_fetch_elapsed_seconds",
        "total_iteration_elapsed_seconds",
        "model_source_mode",
        "model_source_degraded",
        "model_source_degraded_reason",
        "force_model_recompute_every_iteration",
        "use_cached_models",
    )
    payload = {key: source.get(key) for key in keys}
    if decision_rows:
        latest_runtime = decision_rows[-1].get("runtime_diagnostics")
        if isinstance(latest_runtime, dict):
            for key in keys:
                if payload.get(key) is None and latest_runtime.get(key) is not None:
                    payload[key] = latest_runtime.get(key)
    return payload


def _decision_runtime_rows(decision_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in decision_rows:
        runtime = payload.get("runtime_diagnostics") if isinstance(payload.get("runtime_diagnostics"), dict) else {}
        run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        row = {**runtime}
        for key in (
            "iteration",
            "time_utc",
            "current_time_utc",
            "market_timestamp",
            "model_timestamp",
            "requested_duration_minutes",
            "requested_interval_seconds",
            "expected_iterations",
            "iterations_requested_or_expected",
            "run_started_at_utc",
            "run_ended_at_utc",
            "iteration_started_at_utc",
            "iteration_ended_at_utc",
            "iteration_elapsed_seconds",
        ):
            if row.get(key) is None and run.get(key) is not None:
                row[key] = run.get(key)
        raw_context = payload.get("raw_context") if isinstance(payload.get("raw_context"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        freshness = payload.get("data_freshness") if isinstance(payload.get("data_freshness"), dict) else {}
        timestamp = _first_text(
            row.get("time_utc"),
            run.get("time_utc"),
            raw_context.get("current_time_utc"),
            context.get("current_time_utc"),
            freshness.get("market_timestamp"),
            freshness.get("model_timestamp"),
        )
        if row.get("time_utc") is None:
            row["time_utc"] = timestamp or payload.get("time_utc") or payload.get("timestamp_utc")
        row["current_time_utc"] = row.get("current_time_utc") or raw_context.get("current_time_utc") or context.get("current_time_utc")
        row["market_timestamp"] = row.get("market_timestamp") or freshness.get("market_timestamp")
        row["model_timestamp"] = row.get("model_timestamp") or freshness.get("model_timestamp")
        if row:
            rows.append(row)
    return rows


def _elapsed_iteration_seconds(rows: list[dict[str, Any]]) -> list[float]:
    elapsed: list[float] = []
    for row in rows:
        value = _row_elapsed_seconds(row)
        if value is not None:
            elapsed.append(value)
    if not elapsed and len(rows) > 1:
        timestamps = [_iteration_timestamp(row) for row in rows]
        timestamps = [value for value in timestamps if value is not None]
        elapsed.extend(
            round((later - earlier).total_seconds(), 4)
            for earlier, later in zip(timestamps, timestamps[1:], strict=False)
            if later >= earlier
        )
    return elapsed


def _row_elapsed_seconds(row: dict[str, Any]) -> float | None:
    value = _number_or_none(row.get("iteration_elapsed_seconds"))
    if value is not None:
        return value
    started = _parse_datetime(row.get("iteration_started_at_utc"))
    ended = _parse_datetime(row.get("iteration_ended_at_utc"))
    if started and ended:
        return round((ended - started).total_seconds(), 4)
    return None


def _iteration_timestamp(row: dict[str, Any]) -> datetime | None:
    return (
        _parse_datetime(row.get("iteration_started_at_utc"))
        or _parse_datetime(row.get("iteration_ended_at_utc"))
        or _parse_datetime(row.get("time_utc"))
        or _parse_datetime(row.get("current_time_utc"))
        or _parse_datetime(row.get("market_timestamp"))
        or _parse_datetime(row.get("model_timestamp"))
    )


def _iteration_timestamp_text(row: dict[str, Any]) -> str | None:
    parsed = _iteration_timestamp(row)
    return parsed.isoformat() if parsed else None


def _first_int(*values: Any) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _positive_int(*values: Any) -> int | None:
    for value in values:
        parsed = _int_or_none(value)
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _execution_section(
    runs: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    posted = len([row for row in orders if str(row.get("action") or "") == "PLACE_FAKE_LIMIT_BUY"])
    canceled = len([row for row in orders if str(row.get("status") or "") == "canceled"])
    filled = len([row for row in fills if str(row.get("action") or "").upper() == "BUY"])
    spreads = [_number_or_none(row.get("spread_cents")) for row in _candidate_rows(runs)]
    spreads = [value for value in spreads if value is not None]
    lowball = _count_rejections(runs, "passive_price_below_best_bid")
    return {
        "posted_orders": posted,
        "filled_orders": filled,
        "canceled_orders": canceled,
        "fill_rate": round(filled / posted, 4) if posted else None,
        "avg_spread_cents": _avg(spreads),
        "avg_slippage_cents": _avg([_number_or_zero(fill.get("conservative_fill_adjustment_cents")) for fill in fills]),
        "lowball_rejection_count": lowball,
        "passive_price_below_best_bid_count": lowball,
        "fill_price_mode": _first_text(*(run.get("paper_fill_price_mode") for run in reversed(runs))),
        "optimistic_fill_benefit_dollars": _number_or_zero(portfolio.get("optimistic_fill_benefit_dollars")),
    }


def _risk_section(runs: list[dict[str, Any]], portfolio: dict[str, Any], scenarios: dict[str, Any]) -> dict[str, Any]:
    exposures = [_number_or_none(_nested_get(run, "portfolio", "open_exposure_value")) for run in runs]
    exposures = [value for value in exposures if value is not None]
    return {
        "max_open_exposure": _first_number(max(exposures) if exposures else None, portfolio.get("open_exposure_value")),
        "max_total_risk_groups": _max_key(runs, ("portfolio", "total_open_risk_groups")),
        "max_open_positions": _max_key(runs, ("portfolio", "open_positions_count")),
        "max_open_orders": _max_key(runs, ("portfolio", "open_orders_count")),
        "largest_single_thesis_exposure": _largest_exposure(portfolio),
        **_scenario_summary(scenarios),
        "drawdown_risk_reduce_triggered": _count_rejections(runs, "drawdown") > 0,
        "close_only_triggered": any(_nested_get(run, "profile", "close_only") for run in runs),
    }


def _trade_quality_section(runs: list[dict[str, Any]], fills: list[dict[str, Any]]) -> dict[str, Any]:
    trades_by_side: Counter[str] = Counter()
    trades_by_bracket: Counter[str] = Counter()
    pnl_by_side: dict[str, float] = defaultdict(float)
    pnl_by_bracket: dict[str, float] = defaultdict(float)
    for fill in fills:
        side = str(fill.get("side") or "--")
        bracket = _canonical_label(fill.get("bracket_label")) or "--"
        quantity = _int_or_zero(fill.get("quantity"))
        pnl = _number_or_zero(fill.get("realized_pnl_dollars"))
        trades_by_side[side] += quantity
        trades_by_bracket[bracket] += quantity
        pnl_by_side[side] += pnl
        pnl_by_bracket[bracket] += pnl
    taken_edges: list[float] = []
    rejected_edges: list[float] = []
    rejection_counts: Counter[str] = Counter()
    for row in _candidate_rows(runs):
        edge = _number_or_none(row.get("net_edge_cents"))
        if row.get("selected") or row.get("filled"):
            if edge is not None:
                taken_edges.append(edge)
        elif row.get("rejection_code") not in {None, "", "eligible"}:
            if edge is not None:
                rejected_edges.append(edge)
            rejection_counts[str(row.get("rejection_code"))] += 1
    return {
        "trades_by_side": dict(trades_by_side),
        "trades_by_bracket": dict(trades_by_bracket),
        "pnl_by_side": {key: round(value, 4) for key, value in pnl_by_side.items()},
        "pnl_by_bracket": {key: round(value, 4) for key, value in pnl_by_bracket.items()},
        "clv_by_side": {},
        "clv_by_bracket": {},
        "avg_edge_taken": _avg(taken_edges),
        "avg_edge_rejected": _avg(rejected_edges),
        "rejection_reason_counts": dict(rejection_counts),
    }


def _profile_behavior_section(runs: list[dict[str, Any]]) -> dict[str, Any]:
    profile_counts: Counter[str] = Counter()
    transitions = 0
    previous: str | None = None
    blocked_close_only = 0
    late_blocked = 0
    risk_reduce_blocked = 0
    for run in runs:
        profile = run.get("profile") or {}
        name = str(profile.get("active_profile") or profile.get("profile") or "--")
        profile_counts[name] += 1
        if previous is not None and name != previous:
            transitions += 1
        previous = name
        text = json.dumps(run.get("validation") or {})
        blocked_close_only += int("close_only" in text)
        late_blocked += int("late_day" in text)
        risk_reduce_blocked += int("risk_reduce" in text)
    return {
        "time_in_each_profile": dict(profile_counts),
        "profile_transitions": transitions,
        "buys_blocked_by_close_only": blocked_close_only,
        "late_day_entries_blocked": late_blocked,
        "risk_reduce_entries_blocked": risk_reduce_blocked,
    }


def _profile_state_section(latest_run: dict[str, Any], decision_rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest_decision = decision_rows[-1] if decision_rows else {}
    latest_audit = latest_run.get("decision_audit") if isinstance(latest_run.get("decision_audit"), dict) else {}
    sources = [latest_run, latest_audit, latest_decision]
    profile_sources = [
        source.get("profile")
        for source in sources
        if isinstance(source.get("profile"), dict)
    ]

    def first_field(key: str, profile_key: str | None = None) -> Any:
        for source in sources:
            value = source.get(key)
            if value not in (None, ""):
                return value
            run = source.get("run") if isinstance(source.get("run"), dict) else {}
            value = run.get(key)
            if value not in (None, ""):
                return value
        lookup = profile_key or key
        for profile in profile_sources:
            value = profile.get(lookup)
            if value not in (None, ""):
                return value
        return None

    lifecycle_profile = first_field("lifecycle_active_profile")
    trader_profile = first_field("trader_active_profile", "active_profile") or first_field("active_profile", "active_profile")
    profile_mode = first_field("profile_mode")
    mismatch = None
    if lifecycle_profile and trader_profile and lifecycle_profile != trader_profile and profile_mode != "fixed_test":
        mismatch = f"profile mismatch: lifecycle selected {lifecycle_profile} but trader used {trader_profile}"
    return {
        "lifecycle_active_profile": lifecycle_profile,
        "trader_active_profile": trader_profile,
        "profile_mode": profile_mode,
        "profile_reason": first_field("profile_reason"),
        "target_date_relation": first_field("target_date_relation"),
        "effective_risk_config": first_field("effective_risk_config") or {},
        "profile_overrides_applied": first_field("profile_overrides_applied") or {},
        "dynamic_overrides_applied": first_field("dynamic_overrides_applied") or {},
        "profile_mismatch_warning": mismatch,
    }


def _trust_score_section(
    *,
    final_pnl: dict[str, Any],
    settlement: dict[str, Any],
    model_trust: dict[str, Any],
    clv: dict[str, Any],
    execution: dict[str, Any],
    risk: dict[str, Any],
    trade_quality: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = ["diagnostic only; never used for real trading or automatic sizing"]
    score = 0.0

    latest_clv = _number_or_none(clv.get("latest_avg_clv_cents"))
    if latest_clv is not None:
        clv_points = max(0.0, min(25.0, 12.5 + latest_clv))
        score += clv_points
        reasons.append(f"CLV contribution {clv_points:.1f}/25")
    else:
        warnings.append("CLV sample unavailable")

    if settlement.get("winning_bracket"):
        if model_trust.get("was_model_top_correct") is True:
            score += 20
            reasons.append("model top bracket matched settlement")
        elif model_trust.get("absolute_temp_error") is not None:
            temp_error = float(model_trust["absolute_temp_error"])
            points = max(0.0, 20.0 - temp_error * 4.0)
            score += points
            reasons.append(f"temperature error contribution {points:.1f}/20")
    else:
        warnings.append("settlement unavailable; model accuracy capped")

    max_drawdown = abs(_number_or_zero(final_pnl.get("max_drawdown")))
    drawdown_points = max(0.0, 15.0 - max_drawdown / 10.0)
    score += drawdown_points
    reasons.append(f"drawdown control {drawdown_points:.1f}/15")

    fill_rate = _number_or_none(execution.get("fill_rate"))
    if fill_rate is not None:
        execution_points = min(15.0, max(0.0, fill_rate * 15.0))
        score += execution_points
        reasons.append(f"execution fill-rate contribution {execution_points:.1f}/15")

    rejection_counts = trade_quality.get("rejection_reason_counts") or {}
    if isinstance(rejection_counts, dict) and rejection_counts:
        score += 8.0
        reasons.append("risk/rejection discipline observed")
    else:
        score += 5.0

    concentration = abs(_number_or_zero(risk.get("worst_case_loss_dollars")))
    concentration_points = max(0.0, 10.0 - concentration / 25.0)
    score += concentration_points

    pnl = _number_or_zero(final_pnl.get("realized_pnl")) + _number_or_zero(final_pnl.get("unrealized_pnl"))
    pnl_points = 5.0 if pnl > 0 else 2.5 if pnl == 0 else 0.0
    score += pnl_points

    fills_count = _int_or_zero(clv.get("fills_count"))
    if fills_count < 10:
        warnings.append("small sample size; label capped")
    score = round(max(0.0, min(100.0, score)), 2)
    label = "strong" if score >= 80 else "promising" if score >= 60 else "weak" if score >= 35 else "unproven"
    if fills_count < 10 and label in {"strong", "promising"}:
        label = "promising" if score >= 75 and fills_count >= 3 else "unproven"
    return {
        "trust_score": score,
        "trust_score_label": label,
        "trust_score_reasons": reasons,
        "trust_score_warnings": warnings,
        "diagnostic_only": True,
    }


def _candidate_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        audit = run.get("decision_audit") or {}
        for row in audit.get("candidates") or []:
            if isinstance(row, dict):
                rows.append(row)
        context = run.get("context") or {}
        for row in context.get("trade_candidates") or context.get("candidate_trade_board") or []:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _probability_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows = context.get("bracket_probabilities") or context.get("probabilities") or []
    return [row for row in rows if isinstance(row, dict)]


def _model_top_bracket(context: dict[str, Any], probs: list[dict[str, Any]]) -> str | None:
    for key in ("top_bracket", "model_top_bracket", "market_favorite"):
        label = _canonical_label(context.get(key))
        if label:
            return label
    best_row: dict[str, Any] | None = None
    best_p = -1.0
    for row in probs:
        p = _probability_value(row)
        if p is not None and p > best_p:
            best_p = p
            best_row = row
    if best_row is None:
        return None
    return _canonical_label(best_row.get("bracket_label") or best_row.get("label") or best_row.get("bracket"))


def _probability_for_label(rows: list[dict[str, Any]], label: str | None) -> float | None:
    if not label:
        return None
    for row in rows:
        row_label = _canonical_label(row.get("bracket_label") or row.get("label") or row.get("bracket"))
        if row_label == label:
            return _probability_value(row)
    return None


def _probability_for_traded_brackets(latest_run: dict[str, Any], probs: list[dict[str, Any]]) -> dict[str, float]:
    traded: set[str] = set()
    for position in latest_run.get("open_positions") or []:
        label = _canonical_label(position.get("bracket_label"))
        if label:
            traded.add(label)
    for fill in latest_run.get("fills") or []:
        label = _canonical_label(fill.get("bracket_label"))
        if label:
            traded.add(label)
    return {label: prob for label in sorted(traded) if (prob := _probability_for_label(probs, label)) is not None}


def _probability_value(row: dict[str, Any]) -> float | None:
    for key in ("probability", "probability_pct", "p", "model_probability"):
        value = _number_or_none(row.get(key))
        if value is None:
            continue
        return round(value / 100.0, 6) if key == "probability_pct" or value > 1 else round(value, 6)
    return None


def _count_rejections(runs: list[dict[str, Any]], needle: str) -> int:
    needle = needle.lower()
    count = 0
    for row in _candidate_rows(runs):
        text = json.dumps(row, sort_keys=True).lower()
        if needle in text:
            count += 1
    return count


def _max_drawdown(runs: list[dict[str, Any]], portfolio: dict[str, Any]) -> float:
    values = [_number_or_none(_nested_get(run, "portfolio", "drawdown_value")) for run in runs]
    values = [value for value in values if value is not None]
    values.append(_number_or_zero(portfolio.get("drawdown_value")))
    return round(max(values) if values else 0.0, 4)


def _closed_position_count(fills: list[dict[str, Any]]) -> int:
    return sum(1 for fill in fills if str(fill.get("action") or "").upper() == "CLOSE")


def _canceled_order_count(orders: list[dict[str, Any]], settlement: dict[str, Any]) -> int:
    return len([row for row in orders if str(row.get("status") or "") == "canceled"]) + _int_or_zero(
        settlement.get("open_orders_canceled")
    )


def _fees_paid_dollars(fills: list[dict[str, Any]]) -> float:
    total = 0.0
    for fill in fills:
        quantity = _number_or_zero(fill.get("quantity"))
        fee_cents = _number_or_zero(fill.get("fee_cents") or fill.get("fee_per_contract_cents"))
        total += quantity * fee_cents / 100.0
    return round(total, 4)


def _largest_exposure(portfolio: dict[str, Any]) -> float:
    exposures = portfolio.get("exposure_by_bracket") or {}
    if not isinstance(exposures, dict) or not exposures:
        return 0.0
    return round(max(_number_or_zero(value) for value in exposures.values()), 4)


def _max_key(runs: list[dict[str, Any]], path: tuple[str, ...]) -> float | None:
    values = [_number_or_none(_nested_get(run, *path)) for run in runs]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _avg_key(rows: list[dict[str, Any]], key: str) -> float | None:
    return _avg([value for row in rows if (value := _number_or_none(row.get(key))) is not None])


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_or_zero(value: Any) -> float:
    parsed = _number_or_none(value)
    return 0.0 if parsed is None else parsed


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _derive_event_ticker(series: Any, target_date: Any) -> str | None:
    if not series or not target_date:
        return None
    if isinstance(target_date, date):
        resolved = target_date
    else:
        try:
            resolved = date.fromisoformat(str(target_date)[:10])
        except ValueError:
            return None
    return f"{str(series).upper()}-{resolved.strftime('%y%b%d').upper()}"


def _event_ticker_from_payload(payload: dict[str, Any]) -> str | None:
    for value in _walk_payload_values(payload, "event_ticker"):
        text = _event_ticker_from_text(value)
        if text:
            return text
    for key in ("ticker", "contract_ticker", "selected_candidate_id", "candidate_id"):
        for value in _walk_payload_values(payload, key):
            text = _event_ticker_from_text(value)
            if text:
                return text
    return None


def _walk_payload_values(payload: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(payload, dict):
        for current_key, value in payload.items():
            if current_key == key:
                values.append(value)
            values.extend(_walk_payload_values(value, key))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_walk_payload_values(item, key))
    return values


def _event_ticker_from_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    match = re.search(r"\b([A-Z0-9]+-\d{2}[A-Z]{3}\d{2})\b", text)
    return match.group(1) if match else None


def _canonical_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("degrees", "")
        .replace("degree", "")
        .replace("deg", "")
        .replace("F", "")
        .replace("f", "")
        .replace("°", "")
        .replace("Â°", "")
        .replace("Ã‚Â°", "")
        .replace("B66.5", "66-67")
        .replace("B68.5", "68-69")
        .replace("B70.5", "70-71")
        .replace("B72.5", "72-73")
        .replace("T66", "<66")
        .replace("T73", ">73")
        .strip()
    )
    return text or None


def _loads_dict(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    if not text:
        return {}
    try:
        payload = json.loads(str(text))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()
