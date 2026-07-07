from __future__ import annotations

import json

from kalshi_weather.model_tournament import (
    TournamentConfig,
    _dashboard_state,
    bracket_for_temperature,
    load_tournament_state,
    market_rows_from_payload,
    planned_bets_for_model,
    run_tournament_cycle,
    stake_sizing,
    update_position_override,
    write_tournament_files,
)


def _prob(
    label: str,
    lo: int | None,
    hi: int | None,
    p_yes: float,
    *,
    yes_bid: float = 0.40,
    yes_ask: float = 0.42,
    no_bid: float = 0.57,
    no_ask: float | None = 0.59,
    model_id: str = "gfs013",
) -> dict:
    ticker_suffix = "T66" if lo is None else ("T73" if hi is None else f"B{lo}.5")
    return {
        "provider": "open_meteo",
        "model_id": model_id,
        "market_ticker": f"KXHIGHLAX-26JUL03-{ticker_suffix}",
        "bracket_label": label,
        "bracket_lower_f": lo,
        "bracket_upper_f": hi,
        "p_yes": p_yes,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
    }


def _payload(
    *,
    no_ask_for_lowest: float | None = 0.99,
    no_bid_for_lowest: float = 0.98,
    yes_bid_7273: float = 0.61,
) -> dict:
    rows = [
        _prob("<66", None, 65, 0.01, yes_bid=0.00, yes_ask=0.01, no_bid=no_bid_for_lowest, no_ask=no_ask_for_lowest),
        _prob("66-67", 66, 67, 0.02, yes_bid=0.01, yes_ask=0.02, no_bid=0.97, no_ask=0.98),
        _prob("68-69", 68, 69, 0.05, yes_bid=0.04, yes_ask=0.05, no_bid=0.94, no_ask=0.95),
        _prob("70-71", 70, 71, 0.20, yes_bid=0.19, yes_ask=0.20, no_bid=0.79, no_ask=0.80),
        _prob("72-73", 72, 73, 0.70, yes_bid=yes_bid_7273, yes_ask=0.40, no_bid=0.59, no_ask=0.60),
        _prob(">73", 74, None, 0.02, yes_bid=0.02, yes_ask=0.03, no_bid=0.96, no_ask=0.97),
    ]
    return {
        "generated_at_utc": "2026-07-03T16:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-07-03",
        "observed_high_so_far_f": 69.5,
        "latest_observed_temp_f": 68.7,
        "latest_observation_utc": "2026-07-03T15:50:00+00:00",
        "estimates": [
            {
                "provider": "open_meteo",
                "model_id": "gfs013",
                "model_name": "GFS013",
                "model_family": "open_meteo",
                "future_high_f": 71.6,
                "settlement_high_estimate_f": 71.6,
                "successful": True,
                "asof_utc": "2026-07-03T16:00:00+00:00",
            }
        ],
        "probabilities": rows,
    }


def test_model_estimate_maps_to_correct_bracket() -> None:
    rows = market_rows_from_payload(_payload())
    assert bracket_for_temperature(71.6, rows)["bracket_label"] == "72-73"


def test_yes_and_no_stake_contract_sizing_and_target() -> None:
    yes = stake_sizing(100, 40, 0.10)
    assert yes.contracts == 250
    assert yes.cost_dollars == 100
    assert yes.target_exit_bid_cents == 44
    no = stake_sizing(10, 50, 0.10)
    assert no.contracts == 20
    assert no.cost_dollars == 10
    assert no.target_exit_bid_cents == 55


def test_target_impossible_detection() -> None:
    sizing = stake_sizing(10, 99, 0.10)
    assert sizing.target_possible is False
    assert sizing.target_exit_bid_cents > 100


def test_at_least_two_no_ranges_attempted_and_yes_bracket_excluded() -> None:
    payload = _payload()
    rows = market_rows_from_payload(payload)
    estimate = {"model_key": "open_meteo:gfs013", **payload["estimates"][0]}
    plans = planned_bets_for_model(
        estimate=estimate,
        market_rows=rows,
        probabilities=payload["probabilities"],
        config=TournamentConfig(run_id="test"),
    )
    no_plans = [plan for plan in plans if plan["side"] == "NO" and plan["entry_price_cents"] is not None]
    assert len(no_plans) >= 2
    assert all(plan["bracket_label"] != "72-73" for plan in no_plans)


def test_missing_no_ask_skips_to_next_range() -> None:
    payload = _payload(no_ask_for_lowest=None)
    rows = market_rows_from_payload(payload)
    estimate = {"model_key": "open_meteo:gfs013", **payload["estimates"][0]}
    plans = planned_bets_for_model(
        estimate=estimate,
        market_rows=rows,
        probabilities=payload["probabilities"],
        config=TournamentConfig(run_id="test"),
    )
    no_plans = [plan for plan in plans if plan["side"] == "NO"]
    assert any(plan["bracket_label"] == "<66" and plan["entry_price_cents"] is None for plan in no_plans)
    assert len([plan for plan in no_plans if plan["entry_price_cents"] is not None]) >= 2


def test_taker_buys_use_asks_and_closes_use_bids() -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.41), previous_state=None, config=config)
    yes = next(p for p in state["positions"] if p["side"] == "YES" and p["bracket_label"] == "72-73")
    no = next(p for p in state["positions"] if p["side"] == "NO")
    assert yes["entry_price_cents"] == 40
    assert yes["entry_price_source"] == "yes_ask"
    assert no["entry_price_source"] == "no_ask"

    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.45), previous_state=state, config=config)
    closed_yes = next(p for p in state["positions"] if p["side"] == "YES" and p["bracket_label"] == "72-73")
    assert closed_yes["status"] == "closed"
    assert closed_yes["close_price_cents"] == 45
    assert closed_yes["close_price_source"] == "yes_bid"
    assert "profit target" in closed_yes["close_status_reason"]


def test_open_position_records_reason_it_has_not_closed() -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    open_position = next(p for p in state["positions"] if p["status"] == "open")

    assert open_position["close_status_reason"].startswith("Still open")
    assert "target" in open_position["close_status_reason"]


def test_position_override_recalculates_open_stake_and_target() -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.41), previous_state=None, config=config)
    yes = next(p for p in state["positions"] if p["side"] == "YES" and p["bracket_label"] == "72-73")

    state = run_tournament_cycle(
        model_payload=_payload(yes_bid_7273=0.41),
        previous_state=state,
        config=config,
        position_overrides={yes["position_id"]: {"stake_dollars": 50, "profit_target_pct": 0.05}},
    )
    resized = next(p for p in state["positions"] if p["position_id"] == yes["position_id"])

    assert resized["status"] == "open"
    assert resized["stake_dollars"] == 50
    assert resized["profit_target_pct"] == 0.05
    assert resized["contracts"] == 125
    assert resized["cost_dollars"] == 50
    assert resized["target_profit_dollars"] == 2.5
    assert resized["target_exit_bid_cents"] == 42
    assert resized["position_override_applied"] is True


def test_position_override_can_lower_target_and_close_position() -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.41), previous_state=None, config=config)
    yes = next(p for p in state["positions"] if p["side"] == "YES" and p["bracket_label"] == "72-73")
    assert yes["status"] == "open"

    state = run_tournament_cycle(
        model_payload=_payload(yes_bid_7273=0.41),
        previous_state=state,
        config=config,
        position_overrides={yes["position_id"]: {"stake_dollars": 100, "profit_target_pct": 0.02}},
    )
    closed = next(p for p in state["positions"] if p["position_id"] == yes["position_id"])

    assert closed["status"] == "closed"
    assert closed["target_profit_dollars"] == 2
    assert closed["realized_pnl_dollars"] == 2.5


def test_dashboard_update_position_override_writes_state_and_file(tmp_path) -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.41), previous_state=None, config=config)
    write_tournament_files(state, tmp_path)
    yes = next(p for p in state["positions"] if p["side"] == "YES" and p["bracket_label"] == "72-73")

    updated = update_position_override(
        tmp_path,
        {"position_id": yes["position_id"], "stake_dollars": 75, "profit_target_pct": 0.04},
    )
    stored = json.loads((tmp_path / "position_overrides.json").read_text(encoding="utf-8"))
    position = next(p for p in updated["positions"] if p["position_id"] == yes["position_id"])

    assert stored[yes["position_id"]]["stake_dollars"] == 75
    assert position["stake_dollars"] == 75
    assert position["profit_target_pct"] == 0.04
    assert (tmp_path / "dashboard.html").exists()


def test_no_position_closes_using_no_bid() -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(
        model_payload=_payload(no_ask_for_lowest=0.50, no_bid_for_lowest=0.50, yes_bid_7273=0.41),
        previous_state=None,
        config=config,
    )
    opened_no = next(p for p in state["positions"] if p["side"] == "NO" and p["bracket_label"] == "<66")
    assert opened_no["entry_price_cents"] == 50
    assert opened_no["entry_price_source"] == "no_ask"

    state = run_tournament_cycle(
        model_payload=_payload(no_ask_for_lowest=0.50, no_bid_for_lowest=0.56, yes_bid_7273=0.41),
        previous_state=state,
        config=config,
    )
    closed_no = next(p for p in state["positions"] if p["side"] == "NO" and p["bracket_label"] == "<66")
    assert closed_no["status"] == "closed"
    assert closed_no["close_price_cents"] == 56
    assert closed_no["close_price_source"] == "no_bid"


def test_dashboard_state_json_and_files_are_written(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    paths = write_tournament_files(state, tmp_path)
    assert (tmp_path / "model_tournament_state.json").exists()
    assert (tmp_path / "model_estimate_history.jsonl").exists()
    assert (tmp_path / "temperature_observations.jsonl").exists()
    assert (tmp_path / "model_tournament_trades.jsonl").exists()
    assert (tmp_path / "model_tournament_positions.jsonl").exists()
    assert (tmp_path / "model_tournament_summary.json").exists()
    assert (tmp_path / "quote_snapshots.jsonl").exists()
    assert (tmp_path / "dashboard.html").exists()
    loaded = load_tournament_state(tmp_path)
    assert loaded["fake_money_only"] is True
    assert loaded["real_orders_available"] is False
    assert set(loaded["dashboard"]) >= {
        "temperature_observations",
        "estimate_history",
        "model_feed_status",
        "market_snapshot",
        "positions",
        "summary",
        "trade_events",
        "warnings",
    }
    assert json.loads((tmp_path / "model_tournament_summary.json").read_text())["total_positions"] >= 3
    assert loaded["temperature_observations"][-1]["latest_observed_temp_f"] == 68.7
    assert paths["dashboard"].endswith("dashboard.html")


def test_dashboard_positions_sort_open_first_then_by_estimated_bracket_count() -> None:
    market_rows = market_rows_from_payload(_payload())
    state = {
        "positions": [
            {"position_id": "closed-popular", "status": "closed", "bracket_label": "72-73", "side": "YES", "model_key": "m1"},
            {"position_id": "open-less-popular", "status": "open", "bracket_label": "68-69", "side": "YES", "model_key": "m2"},
            {"position_id": "open-popular", "status": "open", "bracket_label": "72-73", "side": "YES", "model_key": "m3"},
            {"position_id": "closed-less-popular", "status": "closed", "bracket_label": "68-69", "side": "YES", "model_key": "m4"},
        ],
        "estimate_history": [
            {"time_utc": "2026-07-03T16:00:00+00:00", "model_key": "a", "estimated_bracket": "72-73"},
            {"time_utc": "2026-07-03T16:00:00+00:00", "model_key": "b", "estimated_bracket": "72-73"},
            {"time_utc": "2026-07-03T16:00:00+00:00", "model_key": "c", "estimated_bracket": "68-69"},
        ],
    }

    rows = _dashboard_state(state, market_rows, [])["positions"]

    assert [row["position_id"] for row in rows] == [
        "open-popular",
        "open-less-popular",
        "closed-popular",
        "closed-less-popular",
    ]
    assert rows[0]["bracket_model_count"] == 2
    assert rows[1]["bracket_model_count"] == 1


def test_dashboard_positions_group_by_model_source_within_status() -> None:
    market_rows = market_rows_from_payload(_payload())
    state = {
        "positions": [
            {
                "position_id": "open-noaa",
                "status": "open",
                "bracket_label": "72-73",
                "side": "YES",
                "model_key": "noaa_herbie:hrrr",
                "provider": "noaa_herbie",
            },
            {
                "position_id": "closed-open-meteo",
                "status": "closed",
                "bracket_label": "72-73",
                "side": "YES",
                "model_key": "open_meteo:gfs013",
                "provider": "open_meteo",
            },
            {
                "position_id": "open-other",
                "status": "open",
                "bracket_label": "72-73",
                "side": "YES",
                "model_key": "synthetic:consensus_median",
                "provider": "synthetic",
            },
            {
                "position_id": "open-open-meteo",
                "status": "open",
                "bracket_label": "72-73",
                "side": "YES",
                "model_key": "open_meteo:best_match",
                "provider": "open_meteo",
            },
            {
                "position_id": "closed-noaa",
                "status": "closed",
                "bracket_label": "72-73",
                "side": "YES",
                "model_key": "noaa_herbie:nbm",
                "provider": "noaa_herbie",
            },
        ],
        "estimate_history": [
            {"time_utc": "2026-07-03T16:00:00+00:00", "model_key": "a", "estimated_bracket": "72-73"},
        ],
    }

    rows = _dashboard_state(state, market_rows, [])["positions"]

    assert [row["position_id"] for row in rows] == [
        "open-open-meteo",
        "open-noaa",
        "open-other",
        "closed-open-meteo",
        "closed-noaa",
    ]
    assert [row["source_group"] for row in rows] == ["Open-Meteo", "NOAA", "Other", "Open-Meteo", "NOAA"]


def test_dashboard_shows_closed_bet_money_in_model_tournament_table(tmp_path) -> None:
    config = TournamentConfig(run_id="test")
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.41), previous_state=None, config=config)
    state = run_tournament_cycle(model_payload=_payload(yes_bid_7273=0.45), previous_state=state, config=config)
    write_tournament_files(state, tmp_path)

    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")

    assert "Closed bet money" in html
    assert "closed_pnl_dollars" in html
    assert "P/L" in html
    assert "Group" in html
    assert "source_group" in html
    assert "Models" in html
    assert "bracket_model_count" in html
    assert "bracket-token" in html
    assert "bracketClass" in html
    assert "bracket-72-73" in html
    assert "Open $" not in html
    assert "Closed $" not in html
    assert "_position_pnl_dollars" in html
    assert "pnl-positive" in html
    assert "pnl-negative" in html
    assert "_stake_control" in html
    assert "_target_control" in html
    assert "savePositionOverride" in html
    assert "/api/position-overrides" in html
    assert "realized_pnl_dollars" in html
    assert "Why" in html
    assert "_close_status_reason" in html
    assert "fallbackCloseReason" in html
    assert "status-badge status-open" in html
    assert "status-badge status-closed" in html


def test_dashboard_displays_times_in_pt(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "America/Los_Angeles" in html
    assert "times shown in PT" in html
    assert "Time (PT)" in html
    assert "Generated (PT)" in html


def test_dashboard_contains_responsive_overflow_guards(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "overflow-x:hidden" in html
    assert "table-wrap" in html
    assert "overflow-x:auto" in html
    assert "overflow-wrap:anywhere" in html
    assert "grid-template-columns:minmax(0,1fr)" in html


def test_dashboard_shades_top_two_market_brackets(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "topMarketRows" in html
    assert "marketYesScore" in html
    assert "type:'rect'" in html
    assert "Top ${index+1}" in html


def test_dashboard_caps_open_ended_bracket_shading_to_estimate_range(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "minEstimateTemp" in html
    assert "maxEstimateTemp" in html
    assert "label.startsWith('>')" in html
    assert "label.startsWith('<')" in html
    assert "maxEstimateTemp+2" in html
    assert "minEstimateTemp-2" in html
    assert "dash:'dot'" not in html


def test_dashboard_y_axis_bounds_follow_visible_temperature_domain(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "yDomain" in html
    assert "yRange" in html
    assert "Math.min(...yDomain)-2" in html
    assert "Math.max(...yDomain)+2" in html
    assert "yAxis.range=[yRange[0],yRange[1]]" in html
    assert "yAxis.autorange=false" in html
    assert "rangemode:'normal'" in html
    assert "fixedrange:true" in html
    assert "zeroline:false" in html
    assert "if(v===null||v===undefined||v==='')return null" in html


def test_dashboard_plots_exact_klax_temperature(tmp_path) -> None:
    state = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=TournamentConfig(run_id="test"))
    write_tournament_files(state, tmp_path)
    html = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "latest_observed_temp_f" in html
    assert "KLAX exact temp" in html
    assert "exactSeries.push" in html
    assert "x:exactSeries.map(r=>r.time)" in html
    assert html.index("name:'KLAX exact temp'") > html.index("for(const [name,rows] of Object.entries(groups))")
    assert "width:5" in html


def test_exact_klax_temperature_carries_forward_between_observation_updates() -> None:
    config = TournamentConfig(run_id="test")
    first = run_tournament_cycle(model_payload=_payload(), previous_state=None, config=config)
    second_payload = _payload()
    second_payload["generated_at_utc"] = "2026-07-03T16:01:00+00:00"
    second_payload["latest_observed_temp_f"] = None
    second_payload["latest_actual_temp_f"] = None
    second_payload["latest_observation_utc"] = None

    second = run_tournament_cycle(model_payload=second_payload, previous_state=first, config=config)

    latest = second["temperature_observations"][-1]
    assert latest["latest_observed_temp_f"] == 68.7
    assert latest["latest_observed_temp_carried_forward"] is True
