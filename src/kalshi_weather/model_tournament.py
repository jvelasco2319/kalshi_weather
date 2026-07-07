from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TournamentConfig:
    run_id: str
    series: str = "KXHIGHLAX"
    station: str = "KLAX"
    target_date: str | None = None
    yes_stake_dollars: float = 100.0
    no_stake_dollars: float = 10.0
    min_no_ranges_per_model: int = 2
    profit_target_pct: float = 0.10
    dashboard_refresh_seconds: int = 5
    include_consensus: bool = True
    allow_target_impossible_entries: bool = True


@dataclass(frozen=True)
class StakeSizing:
    contracts: int
    cost_dollars: float
    target_profit_dollars: float
    target_exit_bid_cents: float | None
    target_possible: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_label(value: Any, lower_f: Any = None, upper_f: Any = None) -> str:
    text = str(value or "").replace("°F", "").replace("°", "").strip()
    if lower_f is None and upper_f is not None:
        return f"<{int(float(upper_f)) + 1}"
    if upper_f is None and lower_f is not None:
        return f">{int(float(lower_f)) - 1}"
    if lower_f is not None and upper_f is not None:
        return f"{int(float(lower_f))}-{int(float(upper_f))}"
    return text.replace("<=", "<").replace(">=", ">")


def bracket_for_temperature(temp_f: float | None, brackets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if temp_f is None:
        return None
    try:
        settlement_temp = math.floor(float(temp_f) + 0.5)
    except (TypeError, ValueError):
        return None
    for bracket in brackets:
        lo = _float_or_none(bracket.get("bracket_lower_f", bracket.get("lower_f")))
        hi = _float_or_none(bracket.get("bracket_upper_f", bracket.get("upper_f")))
        if lo is None and hi is not None and settlement_temp <= hi:
            return bracket
        if hi is None and lo is not None and settlement_temp >= lo:
            return bracket
        if lo is not None and hi is not None and lo <= settlement_temp <= hi:
            return bracket
    return None


def stake_sizing(stake_dollars: float, entry_price_cents: float | None, profit_target_pct: float) -> StakeSizing:
    price = _float_or_none(entry_price_cents)
    target_profit = stake_dollars * profit_target_pct
    if price is None or price <= 0:
        return StakeSizing(0, 0.0, round(target_profit, 4), None, False)
    contracts = int(math.floor((stake_dollars * 100.0) / price))
    if contracts <= 0:
        return StakeSizing(0, 0.0, round(target_profit, 4), None, False)
    cost = contracts * price / 100.0
    target_exit = price + (target_profit * 100.0 / contracts)
    return StakeSizing(
        contracts=contracts,
        cost_dollars=round(cost, 4),
        target_profit_dollars=round(target_profit, 4),
        target_exit_bid_cents=round(target_exit, 4),
        target_possible=target_exit <= 100.0,
    )


def load_position_overrides(output_dir: Path) -> dict[str, dict[str, Any]]:
    path = output_dir / "position_overrides.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return _normalize_position_overrides(payload)


def write_position_overrides(output_dir: Path, overrides: dict[str, Any]) -> None:
    _write_json(output_dir / "position_overrides.json", _normalize_position_overrides(overrides))


def apply_position_overrides(
    state: dict[str, Any],
    overrides: dict[str, Any] | None,
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    normalized = _normalize_position_overrides(overrides or {})
    state["position_overrides"] = normalized
    events: list[dict[str, Any]] = []
    if not normalized:
        return events
    event_time = now or str(state.get("updated_at_utc") or utc_now_iso())
    for position in state.get("positions", []):
        position_id = str(position.get("position_id") or "")
        override = normalized.get(position_id)
        if not override:
            continue
        position["position_override"] = override
        if position.get("status") != "open":
            position["position_override_note"] = "Override ignored because this fake position is already closed."
            continue
        changed = _apply_open_position_override(position, override, event_time)
        if changed:
            events.append(
                _event(
                    event_time,
                    "position_override",
                    str(position.get("model_key")),
                    position.get("side"),
                    position.get("bracket_label"),
                    (
                        f"manual fake-money settings: stake {_money(position.get('stake_dollars'))}, "
                        f"target {float(position.get('profit_target_pct') or 0.0) * 100:.1f}%"
                    ),
                    position=position,
                )
            )
    return events


def update_position_override(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_tournament_state(output_dir)
    if state is None:
        raise ValueError("model tournament state not found")
    position_id = str(payload.get("position_id") or "").strip()
    if not position_id:
        raise ValueError("position_id is required")
    positions = state.get("positions") or []
    position = next((row for row in positions if str(row.get("position_id")) == position_id), None)
    if position is None:
        raise ValueError("position not found")
    if position.get("status") != "open":
        raise ValueError("closed positions are read-only")

    stake = _positive_float(payload.get("stake_dollars"), "stake_dollars")
    target_pct = _positive_float(payload.get("profit_target_pct"), "profit_target_pct")
    if target_pct > 1.0:
        target_pct /= 100.0

    overrides = load_position_overrides(output_dir)
    overrides[position_id] = {
        "position_id": position_id,
        "stake_dollars": round(stake, 4),
        "profit_target_pct": round(target_pct, 6),
        "updated_at_utc": utc_now_iso(),
    }
    write_position_overrides(output_dir, overrides)

    now = str(state.get("updated_at_utc") or utc_now_iso())
    events = apply_position_overrides(state, overrides, now=now)
    market_rows = state.get("market_snapshot") or []
    if market_rows:
        events.extend(_mark_and_close_positions(state, market_rows, now))
    if events:
        state.setdefault("trade_events", []).extend(events)
    books = _model_books(state.get("positions") or [])
    state["positions_open"] = [p for p in state.get("positions", []) if p.get("status") == "open"]
    state["positions_closed"] = [p for p in state.get("positions", []) if p.get("status") == "closed"]
    state["model_books"] = books
    state["summary"] = _summary(state, books)
    state["dashboard"] = _dashboard_state(state, state.get("market_snapshot") or [], state.get("model_feed_status") or [])
    write_tournament_files(state, output_dir)
    return state


def market_rows_from_payload(model_payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows_by_ticker: dict[str, dict[str, Any]] = {}
    for row in model_payload.get("probabilities") or []:
        ticker = str(row.get("market_ticker") or "")
        if not ticker or ticker in rows_by_ticker:
            continue
        label = canonical_label(row.get("bracket_label"), row.get("bracket_lower_f"), row.get("bracket_upper_f"))
        yes_bid = _probability_to_cents(row.get("yes_bid"))
        yes_ask = _probability_to_cents(row.get("yes_ask"))
        no_bid = _probability_to_cents(row.get("no_bid"))
        no_ask = _probability_to_cents(row.get("no_ask"))
        rows_by_ticker[ticker] = {
            "market_ticker": ticker,
            "bracket_label": label,
            "bracket_lower_f": _float_or_none(row.get("bracket_lower_f")),
            "bracket_upper_f": _float_or_none(row.get("bracket_upper_f")),
            "yes_bid_cents": yes_bid,
            "yes_ask_cents": yes_ask,
            "no_bid_cents": no_bid,
            "no_ask_cents": no_ask,
            "market_midpoint_cents": _mid(yes_bid, yes_ask),
        }
    return sorted(rows_by_ticker.values(), key=_bracket_sort_key)


def estimate_rows_from_payload(model_payload: dict[str, Any], *, include_consensus: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for estimate in model_payload.get("estimates") or []:
        row = dict(estimate)
        row["model_key"] = f"{row.get('provider')}:{row.get('model_id')}"
        row["settlement_high_estimate_f"] = _float_or_none(
            row.get("settlement_high_estimate_f") or row.get("future_high_f")
        )
        row["successful"] = bool(row.get("successful")) and row["settlement_high_estimate_f"] is not None
        rows.append(row)
    if include_consensus:
        highs = [
            _float_or_none(row.get("settlement_high_estimate_f"))
            for row in rows
            if row.get("successful") and _float_or_none(row.get("settlement_high_estimate_f")) is not None
        ]
        if highs:
            median = round(statistics.median([float(v) for v in highs if v is not None]), 3)
            rows.append(
                {
                    "provider": "synthetic",
                    "model_id": "consensus_median",
                    "model_key": "synthetic:consensus_median",
                    "model_name": "Consensus median",
                    "model_family": "synthetic_consensus",
                    "future_high_f": median,
                    "settlement_high_estimate_f": median,
                    "successful": True,
                    "source": "model_tournament_consensus",
                    "asof_utc": model_payload.get("generated_at_utc"),
                }
            )
    return rows


def planned_bets_for_model(
    *,
    estimate: dict[str, Any],
    market_rows: list[dict[str, Any]],
    probabilities: list[dict[str, Any]],
    config: TournamentConfig,
) -> list[dict[str, Any]]:
    estimate_high = _float_or_none(estimate.get("settlement_high_estimate_f") or estimate.get("future_high_f"))
    mapped = bracket_for_temperature(estimate_high, market_rows)
    if mapped is None:
        return []
    model_key = str(estimate.get("model_key") or f"{estimate.get('provider')}:{estimate.get('model_id')}")
    common = {
        "model_key": model_key,
        "provider": estimate.get("provider"),
        "model_id": estimate.get("model_id"),
        "model_name": estimate.get("model_name") or estimate.get("model_id") or model_key,
        "model_family": estimate.get("model_family"),
        "estimate_high_f": estimate_high,
        "estimated_bracket": mapped["bracket_label"],
    }
    plans = [
        {
            **common,
            "side": "YES",
            "bracket_label": mapped["bracket_label"],
            "market_ticker": mapped["market_ticker"],
            "stake_dollars": float(config.yes_stake_dollars),
            "entry_price_cents": mapped.get("yes_ask_cents"),
            "exit_bid_cents": mapped.get("yes_bid_cents"),
            "p_yes": _p_for_label(probabilities, mapped["bracket_label"]),
            "rank_reason": "model estimated bracket",
        }
    ]
    no_added = 0
    for row, p_yes, reason in _rank_no_candidates(market_rows, probabilities, estimate_high, mapped["bracket_label"]):
        if no_added >= max(0, int(config.min_no_ranges_per_model)):
            break
        no_ask = row.get("no_ask_cents")
        plans.append(
            {
                **common,
                "side": "NO",
                "bracket_label": row["bracket_label"],
                "market_ticker": row["market_ticker"],
                "stake_dollars": float(config.no_stake_dollars),
                "entry_price_cents": no_ask,
                "exit_bid_cents": row.get("no_bid_cents"),
                "p_yes": p_yes,
                "rank_reason": reason,
            }
        )
        if _float_or_none(no_ask) is not None:
            no_added += 1
    return plans


def run_tournament_cycle(
    *,
    model_payload: dict[str, Any],
    previous_state: dict[str, Any] | None,
    config: TournamentConfig,
    position_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = str(model_payload.get("generated_at_utc") or utc_now_iso())
    state = _initial_state(config) if not previous_state else dict(previous_state)
    for key in ("positions", "trade_events", "estimate_history", "temperature_observations", "quote_snapshots"):
        state.setdefault(key, [])
    state.update(
        {
            "run_id": config.run_id,
            "series": config.series,
            "station": config.station,
            "target_date": config.target_date or str(model_payload.get("market_date") or ""),
            "updated_at_utc": now,
            "fake_money_only": True,
            "live_trading_enabled": False,
            "real_orders_available": False,
            "config": asdict(config),
        }
    )
    market_rows = market_rows_from_payload(model_payload)
    estimates = estimate_rows_from_payload(model_payload, include_consensus=config.include_consensus)
    probabilities_by_model = _probabilities_by_model(model_payload)
    latest_temp_f = _float_or_none(
        model_payload.get("latest_observed_temp_f") or model_payload.get("latest_actual_temp_f")
    )
    latest_observation_utc = model_payload.get("latest_observation_utc")
    carried_forward = False
    if latest_temp_f is None:
        for prior in reversed(state.get("temperature_observations", [])):
            prior_temp_f = _float_or_none(prior.get("latest_observed_temp_f"))
            if prior_temp_f is not None:
                latest_temp_f = prior_temp_f
                latest_observation_utc = latest_observation_utc or prior.get("latest_observation_utc")
                carried_forward = True
                break
    state["temperature_observations"].append(
        {
            "time_utc": now,
            "latest_observed_temp_f": latest_temp_f,
            "latest_observed_temp_carried_forward": carried_forward,
            "observed_high_so_far_f": _float_or_none(model_payload.get("observed_high_so_far_f")),
            "latest_observation_utc": latest_observation_utc,
        }
    )
    for quote in market_rows:
        state["quote_snapshots"].append({"time_utc": now, **quote})

    cycle_events: list[dict[str, Any]] = []
    feed_rows: list[dict[str, Any]] = []
    for estimate in estimates:
        model_key = str(estimate.get("model_key"))
        estimate_high = _float_or_none(estimate.get("settlement_high_estimate_f") or estimate.get("future_high_f"))
        mapped = bracket_for_temperature(estimate_high, market_rows)
        state["estimate_history"].append(
            {
                "time_utc": now,
                "model_key": model_key,
                "provider": estimate.get("provider"),
                "model_id": estimate.get("model_id"),
                "model_name": estimate.get("model_name"),
                "model_family": estimate.get("model_family"),
                "estimate_high_f": estimate_high,
                "estimated_bracket": mapped.get("bracket_label") if mapped else None,
                "successful": bool(estimate.get("successful")),
                "error_message": estimate.get("error_message"),
            }
        )
        feed_rows.append(_feed_status_row(now, estimate, estimate_high))
        if not estimate.get("successful") or estimate_high is None or mapped is None:
            cycle_events.append(_event(now, "skip", model_key, None, None, "model estimate unavailable or unmapped"))
            continue
        plans = planned_bets_for_model(
            estimate=estimate,
            market_rows=market_rows,
            probabilities=probabilities_by_model.get(model_key, []),
            config=config,
        )
        for plan in plans:
            event = _ensure_position(state, plan, now, config)
            if event is not None:
                cycle_events.append(event)
    if position_overrides is not None:
        cycle_events.extend(apply_position_overrides(state, position_overrides, now=now))
    else:
        cycle_events.extend(apply_position_overrides(state, state.get("position_overrides") or {}, now=now))
    cycle_events.extend(_mark_and_close_positions(state, market_rows, now))
    state["trade_events"].extend(cycle_events)
    books = _model_books(state["positions"])
    state["model_feed_status"] = feed_rows
    state["models"] = estimates
    state["market_snapshot"] = market_rows
    state["positions_open"] = [p for p in state["positions"] if p.get("status") == "open"]
    state["positions_closed"] = [p for p in state["positions"] if p.get("status") == "closed"]
    state["model_books"] = books
    state["cycle_events"] = cycle_events
    state["summary"] = _summary(state, books)
    state["dashboard"] = _dashboard_state(state, market_rows, feed_rows)
    return state


def write_tournament_files(state: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "state": output_dir / "model_tournament_state.json",
        "summary": output_dir / "model_tournament_summary.json",
        "latest": output_dir / "latest.json",
        "dashboard": output_dir / "dashboard.html",
    }
    _write_json(files["state"], state)
    _write_json(files["summary"], state.get("summary") or {})
    _write_json(files["latest"], state)
    if "position_overrides" in state:
        _write_json(output_dir / "position_overrides.json", state.get("position_overrides") or {})
    _write_dashboard_html(files["dashboard"], state)
    _append_rows(output_dir / "model_estimate_history.jsonl", state.get("estimate_history", []), ("time_utc", "model_key"))
    _append_rows(output_dir / "temperature_observations.jsonl", state.get("temperature_observations", []), ("time_utc",))
    _append_rows(output_dir / "quote_snapshots.jsonl", state.get("quote_snapshots", []), ("time_utc", "market_ticker"))
    _append_rows(output_dir / "model_tournament_trades.jsonl", state.get("trade_events", []), ("event_id",))
    _append_rows(
        output_dir / "model_tournament_positions.jsonl",
        state.get("positions", []),
        ("position_id", "status", "last_marked_at_utc"),
    )
    return {name: str(path) for name, path in files.items()}


def load_tournament_state(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "model_tournament_state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_position(
    state: dict[str, Any],
    plan: dict[str, Any],
    now: str,
    config: TournamentConfig,
) -> dict[str, Any] | None:
    for position in state.get("positions", []):
        if (
            position.get("status") == "open"
            and position.get("model_key") == plan["model_key"]
            and position.get("side") == plan["side"]
            and position.get("bracket_label") == plan["bracket_label"]
        ):
            return None
    price = _float_or_none(plan.get("entry_price_cents"))
    if price is None or price <= 0:
        return _event(now, "skip", plan["model_key"], plan["side"], plan["bracket_label"], f"missing {plan['side']} ask")
    sizing = stake_sizing(float(plan["stake_dollars"]), price, config.profit_target_pct)
    if sizing.contracts <= 0:
        return _event(now, "skip", plan["model_key"], plan["side"], plan["bracket_label"], "stake too small for ask price")
    if not sizing.target_possible and not config.allow_target_impossible_entries:
        return _event(now, "skip", plan["model_key"], plan["side"], plan["bracket_label"], "target impossible")
    position_id = f"{plan['model_key']}:{plan['side']}:{plan['bracket_label']}:{len(state.get('positions', [])) + 1}"
    position = {
        **plan,
        "position_id": position_id,
        "entry_price_cents": round(price, 4),
        "entry_price_source": f"{plan['side'].lower()}_ask",
        "contracts": sizing.contracts,
        "cost_dollars": sizing.cost_dollars,
        "profit_target_pct": float(config.profit_target_pct),
        "target_profit_dollars": sizing.target_profit_dollars,
        "target_exit_bid_cents": sizing.target_exit_bid_cents,
        "target_possible": sizing.target_possible,
        "current_exit_price_cents": plan.get("exit_bid_cents"),
        "current_value_dollars": 0.0,
        "unrealized_pnl_dollars": 0.0,
        "realized_pnl_dollars": 0.0,
        "target_progress_pct": 0.0,
        "status": "open",
        "opened_at_utc": now,
        "last_marked_at_utc": now,
        "warnings": [] if sizing.target_possible else ["target impossible due to high entry price"],
        "fake_money_only": True,
    }
    state["positions"].append(position)
    reason = "fake taker buy at ask"
    if not sizing.target_possible:
        reason += "; target impossible"
    return _event(now, "buy", plan["model_key"], plan["side"], plan["bracket_label"], reason, position=position)


def _mark_and_close_positions(state: dict[str, Any], market_rows: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    market_by_label = {row["bracket_label"]: row for row in market_rows}
    events: list[dict[str, Any]] = []
    for position in state.get("positions", []):
        if position.get("status") != "open":
            position.setdefault("close_status_reason", "Closed because the fake position already reached its profit target.")
            continue
        market = market_by_label.get(str(position.get("bracket_label")))
        if not market:
            position.setdefault("warnings", []).append("market bracket missing")
            position["close_status_reason"] = (
                "Still open because this bracket is missing from the latest market snapshot. "
                "Without a current quote, the tournament cannot confirm the profit target."
            )
            continue
        bid_key = "yes_bid_cents" if position.get("side") == "YES" else "no_bid_cents"
        bid = _float_or_none(market.get(bid_key))
        position["current_exit_price_cents"] = bid
        position["exit_price_source"] = bid_key.replace("_cents", "")
        position["last_marked_at_utc"] = now
        if bid is None:
            position.setdefault("warnings", []).append("missing exit bid")
            position["close_status_reason"] = (
                f"Still open because the {position.get('side')} exit bid is missing for {position.get('bracket_label')}. "
                "Without an exit bid, the fake position cannot close at the profit target."
            )
            continue
        contracts = int(position.get("contracts") or 0)
        value = contracts * bid / 100.0
        pnl = value - float(position.get("cost_dollars") or 0.0)
        target_profit = float(position.get("target_profit_dollars") or 0.0)
        position["current_value_dollars"] = round(value, 4)
        position["unrealized_pnl_dollars"] = round(pnl, 4)
        position["target_progress_pct"] = None if target_profit <= 0 else round(max(0.0, pnl) / target_profit, 4)
        position["close_status_reason"] = _close_status_reason(position, bid, pnl, target_profit)
        if target_profit > 0 and pnl >= target_profit:
            position["status"] = "closed"
            position["closed_at_utc"] = now
            position["close_price_cents"] = bid
            position["close_price_source"] = bid_key.replace("_cents", "")
            position["realized_pnl_dollars"] = round(pnl, 4)
            position["unrealized_pnl_dollars"] = 0.0
            position["close_status_reason"] = (
                f"Closed because realized P/L {_money(pnl)} met or exceeded the "
                f"{_money(target_profit)} profit target."
            )
            events.append(
                _event(now, "target_reached", position["model_key"], position["side"], position["bracket_label"], "profit target reached", position=position)
            )
            events.append(
                _event(now, "close", position["model_key"], position["side"], position["bracket_label"], "fake taker close at bid", position=position)
            )
    return events


def _money(value: float | int | None) -> str:
    if value is None:
        return "--"
    amount = float(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):.2f}"


def _cents(value: float | int | None) -> str:
    if value is None:
        return "--"
    return f"{float(value):.1f}c"


def _close_status_reason(position: dict[str, Any], bid: float, pnl: float, target_profit: float) -> str:
    if not position.get("target_possible", True):
        target_pct = _float_or_none(position.get("profit_target_pct"))
        target_text = f"{target_pct * 100:.1f}%" if target_pct is not None else "configured"
        return (
            f"Still open because the {target_text} target is not reachable from this entry price and contract sizing. "
            "The tournament keeps marking it, but it cannot auto-close on that target."
        )
    if target_profit <= 0:
        return "Still open because no positive profit target is configured for this fake position."
    remaining = max(0.0, target_profit - pnl)
    progress = None if target_profit <= 0 else max(0.0, pnl) / target_profit
    target_exit = _float_or_none(position.get("target_exit_bid_cents"))
    progress_text = f"{progress * 100:.0f}% of target" if progress is not None else "below target"
    return (
        f"Still open because current open P/L is {_money(pnl)}, below the {_money(target_profit)} target "
        f"({progress_text}). Needs about {_cents(target_exit)} exit bid; current bid is {_cents(bid)}, "
        f"leaving {_money(remaining)} to go."
    )


def _rank_no_candidates(
    market_rows: list[dict[str, Any]],
    probabilities: list[dict[str, Any]],
    estimate_high: float | None,
    yes_label: str,
) -> list[tuple[dict[str, Any], float | None, str]]:
    p_by_label = {
        canonical_label(row.get("bracket_label"), row.get("bracket_lower_f"), row.get("bracket_upper_f")): _float_or_none(row.get("p_yes"))
        for row in probabilities
    }
    ranked: list[tuple[float, dict[str, Any], float | None, str]] = []
    for row in market_rows:
        if row["bracket_label"] == yes_label:
            continue
        p_yes = p_by_label.get(row["bracket_label"])
        if p_yes is None:
            ranked.append((-_distance_from_estimate(row, estimate_high), row, p_yes, "farthest from model estimate"))
        else:
            ranked.append((p_yes, row, p_yes, "lowest model probability"))
    ranked.sort(key=lambda item: (item[0], _bracket_sort_key(item[1])))
    return [(row, p_yes, reason) for _score, row, p_yes, reason in ranked]


def _model_books(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    books: dict[str, dict[str, Any]] = {}
    for position in positions:
        key = str(position.get("model_key"))
        book = books.setdefault(
            key,
            {
                "model_key": key,
                "provider": position.get("provider"),
                "model_id": position.get("model_id"),
                "model_name": position.get("model_name"),
                "open_positions": 0,
                "closed_positions": 0,
                "total_staked_dollars": 0.0,
                "open_pnl_dollars": 0.0,
                "closed_pnl_dollars": 0.0,
                "total_pnl_dollars": 0.0,
            },
        )
        book["total_staked_dollars"] += float(position.get("cost_dollars") or 0.0)
        if position.get("status") == "open":
            book["open_positions"] += 1
            book["open_pnl_dollars"] += float(position.get("unrealized_pnl_dollars") or 0.0)
        elif position.get("status") == "closed":
            book["closed_positions"] += 1
            book["closed_pnl_dollars"] += float(position.get("realized_pnl_dollars") or 0.0)
    for book in books.values():
        book["total_staked_dollars"] = round(book["total_staked_dollars"], 4)
        book["open_pnl_dollars"] = round(book["open_pnl_dollars"], 4)
        book["closed_pnl_dollars"] = round(book["closed_pnl_dollars"], 4)
        book["total_pnl_dollars"] = round(book["open_pnl_dollars"] + book["closed_pnl_dollars"], 4)
    return sorted(books.values(), key=lambda row: row["model_key"])


def _summary(state: dict[str, Any], books: list[dict[str, Any]]) -> dict[str, Any]:
    positions = state.get("positions", [])
    open_positions = [p for p in positions if p.get("status") == "open"]
    closed_positions = [p for p in positions if p.get("status") == "closed"]
    return {
        "run_id": state.get("run_id"),
        "series": state.get("series"),
        "station": state.get("station"),
        "target_date": state.get("target_date"),
        "updated_at_utc": state.get("updated_at_utc"),
        "fake_money_only": True,
        "live_trading_enabled": False,
        "real_orders_available": False,
        "models": len(books),
        "open_positions": len(open_positions),
        "closed_positions": len(closed_positions),
        "total_positions": len(positions),
        "total_staked_dollars": round(sum(float(p.get("cost_dollars") or 0.0) for p in positions), 4),
        "open_pnl_dollars": round(sum(float(p.get("unrealized_pnl_dollars") or 0.0) for p in open_positions), 4),
        "closed_pnl_dollars": round(sum(float(p.get("realized_pnl_dollars") or 0.0) for p in closed_positions), 4),
        "model_books": books,
    }


def _dashboard_state(state: dict[str, Any], market_rows: list[dict[str, Any]], feed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "updated_at_utc": state.get("updated_at_utc"),
        "temperature_observations": state.get("temperature_observations", []),
        "estimate_history": state.get("estimate_history", []),
        "model_feed_status": feed_rows,
        "market_snapshot": market_rows,
        "positions": _positions_for_dashboard(state, market_rows),
        "trade_events": state.get("trade_events", [])[-250:],
        "summary": state.get("summary") or {},
        "warnings": _warnings(state, feed_rows),
    }


def _positions_for_dashboard(state: dict[str, Any], market_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bracket_counts = _latest_estimated_bracket_counts(state.get("estimate_history") or [])
    bracket_order = {str(row.get("bracket_label")): index for index, row in enumerate(market_rows)}
    rows: list[dict[str, Any]] = []
    for index, position in enumerate(state.get("positions") or []):
        row = dict(position)
        label = str(row.get("bracket_label") or "")
        row["_created_order"] = index
        row["source_group"] = _position_source_group(row)
        row["bracket_model_count"] = int(bracket_counts.get(label, 0))
        rows.append(row)
    return sorted(rows, key=lambda row: _position_dashboard_sort_key(row, bracket_order))


def _position_source_group(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "").lower()
    family = str(row.get("model_family") or "").lower()
    model_key = str(row.get("model_key") or "").lower()
    if provider == "open_meteo" or model_key.startswith("open_meteo:"):
        return "Open-Meteo"
    if provider in {"noaa_herbie", "noaa"} or model_key.startswith("noaa_herbie:") or family in {
        "hrrr",
        "nbm",
        "gfs",
        "rap",
        "nam",
        "href",
        "noaa",
        "noaa_herbie",
    }:
        return "NOAA"
    return "Other"


def _position_source_group_rank(row: dict[str, Any]) -> int:
    return {"Open-Meteo": 0, "NOAA": 1, "Other": 2}.get(str(row.get("source_group") or "Other"), 2)


def _latest_estimated_bracket_counts(estimate_history: list[dict[str, Any]]) -> dict[str, int]:
    latest_by_model: dict[str, dict[str, Any]] = {}
    for row in estimate_history:
        model_key = str(row.get("model_key") or "")
        bracket = row.get("estimated_bracket")
        if not model_key or not bracket:
            continue
        latest_by_model[model_key] = row
    counts: dict[str, int] = {}
    for row in latest_by_model.values():
        bracket = str(row.get("estimated_bracket") or "")
        if bracket:
            counts[bracket] = counts.get(bracket, 0) + 1
    return counts


def _position_dashboard_sort_key(row: dict[str, Any], bracket_order: dict[str, int]) -> tuple[Any, ...]:
    status_rank = 0 if row.get("status") == "open" else 1 if row.get("status") == "closed" else 2
    label = str(row.get("bracket_label") or "")
    side_rank = 0 if row.get("side") == "YES" else 1 if row.get("side") == "NO" else 2
    return (
        status_rank,
        _position_source_group_rank(row),
        -int(row.get("bracket_model_count") or 0),
        bracket_order.get(label, 999),
        side_rank,
        str(row.get("model_key") or ""),
        int(row.get("_created_order") or 0),
    )


def _warnings(state: dict[str, Any], feed_rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for position in state.get("positions", []):
        for warning in position.get("warnings") or []:
            warnings.append(f"{position.get('model_key')} {position.get('side')} {position.get('bracket_label')}: {warning}")
    for row in feed_rows:
        if not row.get("success"):
            warnings.append(f"{row.get('model_key')}: {row.get('error_message') or row.get('skipped_reason') or 'model unavailable'}")
    return list(dict.fromkeys(warnings))[:100]


def _feed_status_row(now: str, estimate: dict[str, Any], high_f: float | None) -> dict[str, Any]:
    return {
        "model_key": estimate.get("model_key"),
        "provider": estimate.get("provider"),
        "family": estimate.get("model_family"),
        "attempted": True,
        "success": bool(estimate.get("successful")) and high_f is not None,
        "high_f": high_f,
        "generated_at_utc": estimate.get("asof_utc") or now,
        "elapsed_seconds": (estimate.get("details_json") or {}).get("elapsed_seconds") if isinstance(estimate.get("details_json"), dict) else None,
        "skipped_reason": None if estimate.get("successful") else "model unavailable",
        "error_message": estimate.get("error_message"),
    }


def _event(
    now: str,
    event_type: str,
    model_key: str,
    side: str | None,
    bracket: str | None,
    reason: str,
    *,
    position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "event_id": f"{now}:{event_type}:{model_key}:{side or '-'}:{bracket or '-'}:{len(reason)}",
        "time_utc": now,
        "event_type": event_type,
        "model_key": model_key,
        "side": side,
        "bracket_label": bracket,
        "reason": reason,
        "fake_money_only": True,
    }
    if position:
        event.update(
            {
                "position_id": position.get("position_id"),
                "contracts": position.get("contracts"),
                "entry_price_cents": position.get("entry_price_cents"),
                "close_price_cents": position.get("close_price_cents"),
                "pnl_dollars": position.get("realized_pnl_dollars") or position.get("unrealized_pnl_dollars"),
            }
        )
    return event


def _initial_state(config: TournamentConfig) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": config.run_id,
        "series": config.series,
        "station": config.station,
        "target_date": config.target_date,
        "created_at_utc": utc_now_iso(),
        "positions": [],
        "trade_events": [],
        "estimate_history": [],
        "temperature_observations": [],
        "quote_snapshots": [],
        "fake_money_only": True,
        "live_trading_enabled": False,
        "real_orders_available": False,
    }


def _probabilities_by_model(model_payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in model_payload.get("probabilities") or []:
        grouped.setdefault(f"{row.get('provider')}:{row.get('model_id')}", []).append(row)
    return grouped


def _p_for_label(probabilities: list[dict[str, Any]], label: str) -> float | None:
    for row in probabilities:
        row_label = canonical_label(row.get("bracket_label"), row.get("bracket_lower_f"), row.get("bracket_upper_f"))
        if row_label == label:
            return _float_or_none(row.get("p_yes"))
    return None


def _distance_from_estimate(row: dict[str, Any], estimate_high: float | None) -> float:
    if estimate_high is None:
        return 0.0
    lo = _float_or_none(row.get("bracket_lower_f"))
    hi = _float_or_none(row.get("bracket_upper_f"))
    if lo is None and hi is not None:
        center = hi - 1.0
    elif hi is None and lo is not None:
        center = lo + 1.0
    elif lo is not None and hi is not None:
        center = (lo + hi) / 2.0
    else:
        center = estimate_high
    return abs(center - estimate_high)


def _probability_to_cents(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return round(number * 100.0, 4)
    return round(number, 4)


def _normalize_position_overrides(payload: Any) -> dict[str, dict[str, Any]]:
    raw = payload.get("positions", payload) if isinstance(payload, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return normalized
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        position_id = str(value.get("position_id") or key)
        stake = _float_or_none(value.get("stake_dollars"))
        target_pct = _float_or_none(value.get("profit_target_pct"))
        if target_pct is not None and target_pct > 1.0:
            target_pct /= 100.0
        if not position_id or stake is None or stake <= 0 or target_pct is None or target_pct <= 0:
            continue
        normalized[position_id] = {
            "position_id": position_id,
            "stake_dollars": round(stake, 4),
            "profit_target_pct": round(target_pct, 6),
            "updated_at_utc": value.get("updated_at_utc"),
        }
    return normalized


def _positive_float(value: Any, name: str) -> float:
    number = _float_or_none(value)
    if number is None or number <= 0:
        raise ValueError(f"{name} must be positive")
    return float(number)


def _apply_open_position_override(position: dict[str, Any], override: dict[str, Any], now: str) -> bool:
    stake = _float_or_none(override.get("stake_dollars"))
    target_pct = _float_or_none(override.get("profit_target_pct"))
    price = _float_or_none(position.get("entry_price_cents"))
    if stake is None or stake <= 0 or target_pct is None or target_pct <= 0:
        position["position_override_note"] = "Override ignored because stake and target percent must be positive."
        return False
    sizing = stake_sizing(stake, price, target_pct)
    if sizing.contracts <= 0:
        position["position_override_note"] = "Override ignored because the stake is too small for this entry price."
        return False
    old = (
        _float_or_none(position.get("stake_dollars")),
        _float_or_none(position.get("profit_target_pct")),
        int(position.get("contracts") or 0),
    )
    position["stake_dollars"] = round(stake, 4)
    position["profit_target_pct"] = round(target_pct, 6)
    position["contracts"] = sizing.contracts
    position["cost_dollars"] = sizing.cost_dollars
    position["target_profit_dollars"] = sizing.target_profit_dollars
    position["target_exit_bid_cents"] = sizing.target_exit_bid_cents
    position["target_possible"] = sizing.target_possible
    position["position_override_applied"] = True
    position["position_override_note"] = "Manual fake-money stake/target settings are active."
    position["position_override_updated_at_utc"] = now
    if not sizing.target_possible:
        warnings = position.setdefault("warnings", [])
        if "target impossible due to high entry price" not in warnings:
            warnings.append("target impossible due to high entry price")
    bid = _float_or_none(position.get("current_exit_price_cents"))
    if bid is not None:
        value = sizing.contracts * bid / 100.0
        pnl = value - sizing.cost_dollars
        position["current_value_dollars"] = round(value, 4)
        position["unrealized_pnl_dollars"] = round(pnl, 4)
        position["target_progress_pct"] = None if sizing.target_profit_dollars <= 0 else round(max(0.0, pnl) / sizing.target_profit_dollars, 4)
    new = (round(stake, 4), round(target_pct, 6), sizing.contracts)
    return old != new


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 4)


def _bracket_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    lo = _float_or_none(row.get("bracket_lower_f"))
    hi = _float_or_none(row.get("bracket_upper_f"))
    return (-999.0 if lo is None else lo, -999.0 if hi is None else hi)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _append_rows(path: Path, rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[Any, ...]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(tuple(data.get(key) for key in key_fields))
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            key = tuple(row.get(field) for field in key_fields)
            if key in seen:
                continue
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            seen.add(key)


def _write_dashboard_html(path: Path, state: dict[str, Any]) -> None:
    dashboard = json.dumps(state.get("dashboard") or {}, default=str)
    refresh = int((state.get("config") or {}).get("dashboard_refresh_seconds") or 5)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="{refresh}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kalshi Weather Model Tournament</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
*{{box-sizing:border-box}}html,body{{max-width:100%;overflow-x:hidden}}
body{{margin:0;background:#0f1217;color:#e8edf4;font-family:Segoe UI,Arial,sans-serif}}
header{{padding:18px 24px;background:#151a22;border-bottom:1px solid #2b3444;min-width:0}}
main{{padding:18px 24px;display:grid;gap:18px;grid-template-columns:minmax(0,1fr);min-width:0}}
section{{background:#171d27;border:1px solid #2b3444;border-radius:8px;padding:14px;min-width:0;overflow:hidden}}
h1{{margin:0;font-size:24px}}h2{{font-size:17px}}#chart{{width:100%;min-width:0}}
.table-wrap{{width:100%;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}}
table{{width:max-content;min-width:100%;border-collapse:collapse;font-size:13px}}
th,td{{border-bottom:1px solid #2b3444;padding:6px 8px;text-align:left;white-space:nowrap;vertical-align:top}}
th{{color:#9fb5d1}}.grid{{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(260px,.8fr);gap:18px;min-width:0}}
.summary-line{{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 10px;color:#9fb5d1;font-size:14px}}.summary-line strong{{color:#e8edf4}}
.reason-cell{{white-space:normal;min-width:280px;max-width:560px;line-height:1.3}}
.status-cell{{text-align:center}}
.status-badge{{display:inline-block;min-width:58px;padding:3px 8px;border-radius:6px;font-weight:700;font-size:12px;text-align:center;text-transform:uppercase;letter-spacing:.02em}}
.status-open{{background:#4d1418;color:#ff9a9f;border:1px solid #a63a42}}
.status-closed{{background:#11391f;color:#7bf19a;border:1px solid #2f9d55}}
.pnl-cell{{font-weight:700;text-align:right}}
.pnl-positive{{color:#72e58f}}
.pnl-negative{{color:#ff8e95}}
.pnl-zero{{color:#c9d3df}}
.control-cell{{min-width:150px}}
.adjust-control{{display:flex;align-items:center;gap:4px}}
.adjust-control button{{width:24px;height:24px;border:1px solid #3b4a60;border-radius:5px;background:#202938;color:#e8edf4;font-weight:700;cursor:pointer}}
.adjust-control button:hover{{background:#2b3648}}
.adjust-control input{{width:68px;background:#101722;color:#e8edf4;border:1px solid #3b4a60;border-radius:5px;padding:3px 5px;text-align:right}}
.control-subtext{{margin-top:3px;color:#9fb5d1;font-size:11px;line-height:1.25}}
.save-state{{margin-top:3px;font-size:11px;color:#9fb5d1;min-height:14px}}
.save-state.saved{{color:#72e58f}}.save-state.error{{color:#ff8e95}}
.bracket-cell{{text-align:center}}
.bracket-token{{display:inline-block;min-width:58px;padding:2px 8px;border-radius:999px;font-weight:800;text-align:center;border:1px solid transparent}}
.bracket-lt66{{color:#8ee7ff;background:rgba(57,180,210,.14);border-color:rgba(57,180,210,.34)}}
.bracket-66-67{{color:#75e2bd;background:rgba(47,190,145,.14);border-color:rgba(47,190,145,.34)}}
.bracket-68-69{{color:#9bf078;background:rgba(100,205,70,.14);border-color:rgba(100,205,70,.34)}}
.bracket-70-71{{color:#ffdf6b;background:rgba(235,185,55,.16);border-color:rgba(235,185,55,.38)}}
.bracket-72-73{{color:#ffaf66;background:rgba(242,133,49,.16);border-color:rgba(242,133,49,.40)}}
.bracket-gt73{{color:#ff8fac;background:rgba(232,76,120,.16);border-color:rgba(232,76,120,.40)}}
.bracket-other{{color:#c8d4e4;background:rgba(159,181,209,.12);border-color:rgba(159,181,209,.30)}}
.ok{{color:#6de38b}}.warn{{color:#ffcf5a}}#warnings{{max-height:360px;overflow:auto}}
#warnings ul{{margin:0;padding-left:1.1rem}}#warnings li{{white-space:normal;overflow-wrap:anywhere;word-break:break-word;line-height:1.35;margin:0 0 8px}}
@media(max-width:1100px){{main{{padding:12px}}.grid{{grid-template-columns:minmax(0,1fr)}}}}
@media(max-width:640px){{header{{padding:14px}}h1{{font-size:20px}}h2{{font-size:15px}}table{{font-size:12px}}}}
</style></head><body>
<header><h1>Kalshi Weather Model Tournament</h1><div id="meta"></div></header>
<main>
<section><h2>Temperature vs Time (PT)</h2><div id="chart" style="height:420px"></div></section>
<div class="grid"><section><h2>Model Tournament</h2><div id="positions"></div></section><section><h2>Warnings</h2><div id="warnings"></div></section></div>
<section><h2>Model Feed Status</h2><div id="feeds"></div></section>
<section><h2>Market Snapshot</h2><div id="market"></div></section>
<section><h2>Trade Events</h2><div id="events"></div></section>
</main><script>
const state={dashboard}; const fmt=v=>v===null||v===undefined?'--':v;
const PT_ZONE='America/Los_Angeles';
const ptDateTimeFmt=new Intl.DateTimeFormat('en-US',{{timeZone:PT_ZONE,month:'short',day:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZoneName:'short'}});
const ptChartFmt=new Intl.DateTimeFormat('en-US',{{timeZone:PT_ZONE,month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}});
function parseUtc(v){{if(!v)return null;const text=String(v).includes('T')?String(v):String(v).replace(' ','T');const d=new Date(text);return Number.isNaN(d.getTime())?null:d;}}
function formatPT(v){{const d=parseUtc(v);return d?ptDateTimeFmt.format(d):fmt(v);}}
function chartPT(v){{const d=parseUtc(v);return d?ptChartFmt.format(d):fmt(v);}}
function isTimeKey(key){{return /(^time_utc$|_at_utc$|_utc$)/.test(String(key));}}
function money(v){{if(v===null||v===undefined||v==='')return '--';const n=Number(v);return Number.isFinite(n)?`${{n<0?'-':''}}$${{Math.abs(n).toFixed(2)}}`:fmt(v);}}
function signedMoney(v){{if(v===null||v===undefined||v==='')return '--';const n=Number(v);return Number.isFinite(n)?`${{n>0?'+':n<0?'-':''}}$${{Math.abs(n).toFixed(2)}}`:fmt(v);}}
function cents(v){{if(v===null||v===undefined||v==='')return '--';const n=Number(v);return Number.isFinite(n)?`${{n.toFixed(1)}}c`:fmt(v);}}
function htmlAttr(v){{return String(v??'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function bracketClass(v){{const key=String(v??'').replace(/[°F\\s]/g,'');const classes={{'<66':'bracket-lt66','66-67':'bracket-66-67','68-69':'bracket-68-69','70-71':'bracket-70-71','72-73':'bracket-72-73','>73':'bracket-gt73'}};return classes[key]||'bracket-other';}}
function bracketBadge(v){{return `<span class="bracket-token ${{bracketClass(v)}}">${{htmlAttr(fmt(v))}}</span>`;}}
function fallbackCloseReason(row){{if(row.close_status_reason)return row.close_status_reason;if(row.status==='closed')return `Closed because realized P/L ${{money(row.realized_pnl_dollars)}} reached the profit target.`;const pnl=Number(row.unrealized_pnl_dollars||0);const target=Number(row.target_profit_dollars||0);if(row.current_exit_price_cents===null||row.current_exit_price_cents===undefined||row.current_exit_price_cents==='')return `Still open because the exit bid is missing for ${{row.side}} ${{row.bracket_label}}. Without an exit bid, the fake position cannot close at the profit target.`;if(row.target_possible===false)return `Still open because the ${{(targetPct(row)*100).toFixed(1)}}% target is not reachable from this entry price and contract sizing.`;if(target<=0)return 'Still open because no positive profit target is configured.';const progress=Math.max(0,pnl)/target;const remaining=Math.max(0,target-pnl);return `Still open because current open P/L is ${{money(pnl)}}, below the ${{money(target)}} target (${{(progress*100).toFixed(0)}}% of target). Needs about ${{cents(row.target_exit_bid_cents)}} exit bid; current bid is ${{cents(row.current_exit_price_cents)}}, leaving ${{money(remaining)}} to go.`;}}
function statusBadge(v){{const s=String(v||'').toLowerCase();if(s==='closed')return '<span class="status-badge status-closed">Closed</span>';if(s==='open')return '<span class="status-badge status-open">Open</span>';return fmt(v);}}
function positionPnl(row){{return row.status==='closed'?row.realized_pnl_dollars:row.unrealized_pnl_dollars;}}
function pnlValue(row){{const value=positionPnl(row);const n=Number(value);const cls=Number.isFinite(n)?(n>0?'pnl-positive':n<0?'pnl-negative':'pnl-zero'):'pnl-zero';return `<span class="${{cls}}">${{signedMoney(value)}}</span>`;}}
function targetPct(row){{const n=Number(row.profit_target_pct);return Number.isFinite(n)&&n>0?n:0.10;}}
function stakeControl(row){{if(row.status!=='open')return money(row.stake_dollars);const value=Number(row.stake_dollars||0).toFixed(2);return `<div class="adjust-control"><button type="button" data-adjust-field="stake_dollars" data-delta="-1">-</button><span>$</span><input type="number" min="0.01" step="5" data-step="5" data-decimals="2" data-field="stake_dollars" value="${{htmlAttr(value)}}"><button type="button" data-adjust-field="stake_dollars" data-delta="1">+</button></div><div class="control-subtext">${{fmt(row.contracts)}} contracts</div><div class="save-state"></div>`;}}
function targetControl(row){{const pct=targetPct(row)*100;if(row.status!=='open')return `${{pct.toFixed(1)}}%`;const progress=Number(row.target_progress_pct);const progressText=Number.isFinite(progress)?`${{Math.max(0,progress*100).toFixed(0)}}% to target`:'-- to target';return `<div class="adjust-control"><button type="button" data-adjust-field="profit_target_pct" data-delta="-1">-</button><input type="number" min="0.1" step="1" data-step="1" data-decimals="1" data-field="profit_target_pct" value="${{htmlAttr(pct.toFixed(1))}}"><span>%</span><button type="button" data-adjust-field="profit_target_pct" data-delta="1">+</button></div><div class="control-subtext">${{progressText}} | target ${{money(row.target_profit_dollars)}}</div><div class="save-state"></div>`;}}
function cell(row,key){{if(key==='_close_status_reason')return fallbackCloseReason(row);if(key==='_position_pnl_dollars')return pnlValue(row);if(key==='_stake_control')return stakeControl(row);if(key==='_target_control')return targetControl(row);if(key==='status')return statusBadge(row[key]);if(key==='bracket_label')return bracketBadge(row[key]);if(String(key).endsWith('_dollars'))return money(row[key]);return isTimeKey(key)?formatPT(row[key]):fmt(row[key]);}}
document.getElementById('meta').textContent=`Updated ${{formatPT(state.updated_at_utc)}} | fake-money-only | times shown in PT | refresh {refresh}s`;
function cellClass(key){{const classes=[];if(key==='_close_status_reason')classes.push('reason-cell');if(key==='status')classes.push('status-cell');if(key==='bracket_label')classes.push('bracket-cell');if(key==='_position_pnl_dollars')classes.push('pnl-cell');if(key==='_stake_control'||key==='_target_control')classes.push('control-cell');return classes.join(' ');}}
function rowAttr(row){{return row.position_id?` data-position-id="${{htmlAttr(row.position_id)}}"`:'';}}
function table(rows,cols){{if(!rows||!rows.length)return '<em>none</em>';return '<div class="table-wrap"><table><thead><tr>'+cols.map(c=>`<th>${{c[0]}}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>`<tr${{rowAttr(r)}}>`+cols.map(c=>`<td class="${{cellClass(c[1])}}">${{cell(r,c[1])}}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>';}}
function setSaveState(rowEl,text,cls){{for(const el of rowEl.querySelectorAll('.save-state')){{el.textContent=text;el.className=`save-state ${{cls||''}}`;}}}}
async function savePositionOverride(rowEl){{if(!rowEl)return;const positionId=rowEl.dataset.positionId;const stakeInput=rowEl.querySelector('input[data-field="stake_dollars"]');const targetInput=rowEl.querySelector('input[data-field="profit_target_pct"]');if(!positionId||!stakeInput||!targetInput)return;const stake=Number(stakeInput.value);const targetPercent=Number(targetInput.value);if(!Number.isFinite(stake)||stake<=0||!Number.isFinite(targetPercent)||targetPercent<=0){{setSaveState(rowEl,'enter positive values','error');return;}}setSaveState(rowEl,'saving...','');try{{const response=await fetch('/api/position-overrides',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{position_id:positionId,stake_dollars:stake,profit_target_pct:targetPercent/100}})}});if(!response.ok)throw new Error(await response.text());setSaveState(rowEl,'saved','saved');}}catch(err){{setSaveState(rowEl,'save failed','error');}}}}
document.addEventListener('click',event=>{{const button=event.target.closest('button[data-adjust-field]');if(!button)return;const rowEl=button.closest('tr[data-position-id]');const input=rowEl?.querySelector(`input[data-field="${{button.dataset.adjustField}}"]`);if(!rowEl||!input)return;const step=Number(input.dataset.step||input.step||1);const delta=Number(button.dataset.delta||0);const decimals=Number(input.dataset.decimals||2);const next=Math.max(Number(input.min||0),Number(input.value||0)+delta*step);input.value=next.toFixed(decimals);savePositionOverride(rowEl);}});
document.addEventListener('change',event=>{{const input=event.target.closest('input[data-field]');if(!input)return;savePositionOverride(input.closest('tr[data-position-id]'));}});
const summary=state.summary||{{}};
document.getElementById('positions').innerHTML=`<div class="summary-line"><span>Closed bet money: <strong>${{money(summary.closed_pnl_dollars)}}</strong></span><span>Open P/L: <strong>${{money(summary.open_pnl_dollars)}}</strong></span><span>Closed bets: <strong>${{fmt(summary.closed_positions)}}</strong></span></div>`+table(state.positions,[['Group','source_group'],['Model','model_key'],['Side','side'],['Bracket','bracket_label'],['Models','bracket_model_count'],['Stake','_stake_control'],['Entry','entry_price_cents'],['Exit','current_exit_price_cents'],['P/L','_position_pnl_dollars'],['Target','_target_control'],['Status','status'],['Why','_close_status_reason']]);
document.getElementById('feeds').innerHTML=table(state.model_feed_status,[['Model','model_key'],['Provider','provider'],['Family','family'],['OK','success'],['High F','high_f'],['Generated (PT)','generated_at_utc'],['Elapsed','elapsed_seconds'],['Error','error_message']]);
document.getElementById('market').innerHTML=table(state.market_snapshot,[['Bracket','bracket_label'],['YES bid','yes_bid_cents'],['YES ask','yes_ask_cents'],['NO bid','no_bid_cents'],['NO ask','no_ask_cents'],['Mid','market_midpoint_cents']]);
document.getElementById('events').innerHTML=table((state.trade_events||[]).slice(-80).reverse(),[['Time (PT)','time_utc'],['Event','event_type'],['Model','model_key'],['Side','side'],['Bracket','bracket_label'],['Reason','reason']]);
document.getElementById('warnings').innerHTML=(state.warnings||[]).length?'<ul>'+state.warnings.map(w=>`<li class="warn">${{w}}</li>`).join('')+'</ul>':'<span class="ok">none</span>';
function numberOrNull(v){{if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null;}}
function marketYesScore(row){{const mid=numberOrNull(row.market_midpoint_cents);if(mid!==null)return mid;const bid=numberOrNull(row.yes_bid_cents);const ask=numberOrNull(row.yes_ask_cents);if(bid!==null&&ask!==null)return (bid+ask)/2;if(bid!==null)return bid;if(ask!==null)return ask;return null;}}
const estimateTemps=(state.estimate_history||[]).map(r=>numberOrNull(r.estimate_high_f)).filter(v=>v!==null);
const minEstimateTemp=estimateTemps.length?Math.min(...estimateTemps):null;
const maxEstimateTemp=estimateTemps.length?Math.max(...estimateTemps):null;
function labelBoundary(label){{const match=String(label||'').match(/-?\\d+(?:\\.\\d+)?/);return match?Number(match[0]):null;}}
function bracketBand(row){{
  const label=String(row.bracket_label||'');
  const labelNumber=labelBoundary(label);
  if(label.startsWith('>')&&labelNumber!==null){{const y0=labelNumber+0.5;const y1=maxEstimateTemp!==null?maxEstimateTemp+2:y0+2;return {{y0,y1:Math.max(y0+0.5,y1)}};}}
  if(label.startsWith('<')&&labelNumber!==null){{const y1=labelNumber-0.5;const y0=minEstimateTemp!==null?minEstimateTemp-2:y1-2;return {{y0:Math.min(y1-0.5,y0),y1}};}}
  const lo=numberOrNull(row.bracket_lower_f);const hi=numberOrNull(row.bracket_upper_f);
  if(lo!==null&&hi!==null)return {{y0:lo-0.5,y1:hi+0.5}};
  if(lo!==null){{const y0=lo-0.5;const y1=maxEstimateTemp!==null?maxEstimateTemp+2:lo+1.5;return {{y0,y1:Math.max(y0+0.5,y1)}};}}
  if(hi!==null){{const y1=hi+0.5;const y0=minEstimateTemp!==null?minEstimateTemp-2:hi-1.5;return {{y0:Math.min(y1-0.5,y0),y1}};}}
  return null;
}}
const traces=[]; const obs=state.temperature_observations||[];
traces.push({{x:obs.map(r=>chartPT(r.time_utc)),y:obs.map(r=>r.observed_high_so_far_f),mode:'lines+markers',name:'Observed high so far'}});
const groups={{}}; for(const row of state.estimate_history||[]){{if(!groups[row.model_key])groups[row.model_key]=[];groups[row.model_key].push(row);}}
for(const [name,rows] of Object.entries(groups))traces.push({{x:rows.map(r=>chartPT(r.time_utc)),y:rows.map(r=>r.estimate_high_f),mode:'lines+markers',name:name}});
const exactSeries=[]; let lastExactTemp=null;
for(const row of obs){{const exactTemp=numberOrNull(row.latest_observed_temp_f);if(exactTemp!==null)lastExactTemp=exactTemp;if(lastExactTemp!==null)exactSeries.push({{time:chartPT(row.time_utc),temp:lastExactTemp}});}}
if(exactSeries.length)traces.push({{x:exactSeries.map(r=>r.time),y:exactSeries.map(r=>r.temp),mode:'lines+markers',name:'KLAX exact temp',connectgaps:true,line:{{color:'#f4f7fb',width:5}},marker:{{symbol:'diamond',size:9,color:'#f4f7fb',line:{{color:'#111722',width:1}}}}}});
const shapes=[]; const annotations=[];
const topMarketRows=[...(state.market_snapshot||[])].map(row=>{{return {{row:row,score:marketYesScore(row)}};}}).filter(item=>item.score!==null).sort((a,b)=>b.score-a.score).slice(0,2);
const shadeColors=['rgba(82, 196, 255, 0.16)','rgba(255, 207, 90, 0.13)'];
const shadeBorders=['rgba(82, 196, 255, 0.34)','rgba(255, 207, 90, 0.30)'];
const yValues=[];
for(const trace of traces){{for(const value of trace.y||[]){{const number=numberOrNull(value);if(number!==null)yValues.push(number);}}}}
topMarketRows.forEach((item,index)=>{{const band=bracketBand(item.row);if(!band)return;shapes.push({{type:'rect',xref:'paper',x0:0,x1:1,yref:'y',y0:band.y0,y1:band.y1,fillcolor:shadeColors[index],line:{{width:0}},layer:'below'}});annotations.push({{xref:'paper',x:0.01,xanchor:'left',yref:'y',y:(band.y0+band.y1)/2,text:`Top ${{index+1}}: ${{item.row.bracket_label}}`,showarrow:false,font:{{size:12,color:'#e8edf4'}},bgcolor:'rgba(15,18,23,0.72)',bordercolor:shadeBorders[index],borderpad:3}});}});
const yDomain=estimateTemps.length?estimateTemps:yValues;
const yRange=yDomain.length?[Math.floor(Math.min(...yDomain)-2),Math.ceil(Math.max(...yDomain)+2)]:undefined;
const yAxis={{title:'Temperature F',rangemode:'normal',fixedrange:true,zeroline:false}};
if(yRange){{yAxis.autorange=false;yAxis.range=[yRange[0],yRange[1]];}}
window.dashboardChartDebug={{minEstimateTemp,maxEstimateTemp,yRange,topMarketRows:topMarketRows.map(item=>({{label:item.row.bracket_label,score:item.score}}))}};
Plotly.newPlot('chart',traces,{{paper_bgcolor:'#171d27',plot_bgcolor:'#111722',font:{{color:'#e8edf4'}},yaxis:yAxis,xaxis:{{title:'Time (PT)',type:'category'}},shapes,annotations}},{{responsive:true}});
</script></body></html>"""
    path.write_text(html, encoding="utf-8")
