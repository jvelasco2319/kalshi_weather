from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.trading.model_race import (
    DEFAULT_MODEL_RACE_MODELS,
    ModelRaceAccountState,
    ModelRaceConfig,
    compact_model_race_text,
    force_flat_model_race,
    flatten_model_race,
    model_race_debug_text,
    model_race_report_payload,
    model_race_report_text,
    model_specs,
    run_model_race_exit_monitor,
    run_model_race_once,
    should_exit_model_position,
    should_enter_model_trade,
    size_model_trade,
)


def _store(tmp_path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "paper.sqlite", tmp_path / "snapshots")


def _estimate(provider: str = "current", model_id: str = "current_weighted_blend", high: float = 71.0) -> dict:
    return {
        "asof_utc": datetime.now(timezone.utc).isoformat(),
        "station": "KLAX",
        "market_date": "2026-06-22",
        "provider": provider,
        "model_id": model_id,
        "model_name": model_id,
        "model_family": provider,
        "observed_high_so_far_f": 69.0,
        "future_high_f": high,
        "settlement_high_estimate_f": high,
        "successful": True,
    }


def _prob(
    provider: str = "current",
    model_id: str = "current_weighted_blend",
    ticker: str = "T70",
    label: str = "70-71",
    p_yes: float = 0.70,
    yes_ask: str | None = "0.50",
    yes_bid: str | None = "0.50",
    no_ask: str | None = "0.80",
    no_bid: str | None = "0.20",
    lo: int | None = 70,
    hi: int | None = 71,
    yes_ask_size: str | None = None,
) -> dict:
    yes_edge = Decimal(str(p_yes)) - Decimal(yes_ask) if yes_ask is not None else None
    no_edge = Decimal("1") - Decimal(str(p_yes)) - Decimal(no_ask) if no_ask is not None else None
    return {
        "asof_utc": datetime.now(timezone.utc).isoformat(),
        "station": "KLAX",
        "market_date": "2026-06-22",
        "provider": provider,
        "model_id": model_id,
        "market_ticker": ticker,
        "bracket_label": label,
        "bracket_lower_f": lo,
        "bracket_upper_f": hi,
        "bracket_type": "range" if lo is not None and hi is not None else "above",
        "p_yes": p_yes,
        "yes_bid": Decimal(yes_bid) if yes_bid is not None else None,
        "yes_ask": Decimal(yes_ask) if yes_ask is not None else None,
        "no_bid": Decimal(no_bid) if no_bid is not None else None,
        "no_ask": Decimal(no_ask) if no_ask is not None else None,
        "yes_edge": yes_edge,
        "no_edge": no_edge,
        "yes_ask_size": Decimal(yes_ask_size) if yes_ask_size is not None else None,
    }


def _payload(estimates: list[dict] | None = None, probabilities: list[dict] | None = None, observed: float = 69.0) -> dict:
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-22",
        "observed_high_so_far_f": observed,
        "latest_observation_utc": datetime.now(timezone.utc).isoformat(),
        "current_production_estimate_f": 71.0,
        "markets_count": 2,
        "bracket_count": 2,
        "estimates": estimates or [_estimate()],
        "probabilities": probabilities or [_prob()],
    }


def _config(*models: str) -> ModelRaceConfig:
    return ModelRaceConfig(include_models=list(models) or ["current:current_weighted_blend"], force_flat_time_local="23:59")


def test_independent_mode_is_default() -> None:
    config = ModelRaceConfig()
    assert config.race_mode == "independent"
    assert config.block_outlier_model_entries is False


def test_model_race_accounts_initialize_with_100_each(tmp_path) -> None:
    store = _store(tmp_path)
    store.create_model_race("default", model_specs(), Decimal("100"))
    accounts = store.load_model_race_accounts("default")
    assert len(accounts) == len(DEFAULT_MODEL_RACE_MODELS)
    assert {row["current_cash"] for row in accounts} == {"100"}


def test_each_model_has_separate_cash(tmp_path) -> None:
    store = _store(tmp_path)
    payload = _payload(
        estimates=[_estimate(), _estimate("open_meteo", "best_match", 72)],
        probabilities=[_prob(), _prob("open_meteo", "best_match", ticker="T72", p_yes=0.40, yes_ask="0.39")],
    )
    run_model_race_once(store, payload, _config("current:current_weighted_blend", "open_meteo:best_match"))
    accounts = {row["model_key"]: row for row in store.load_model_race_accounts("default")}
    assert accounts["current:current_weighted_blend"]["current_cash"] != accounts["open_meteo:best_match"]["current_cash"]


def test_yes_entry_when_edge_clears_hurdle(tmp_path) -> None:
    store = _store(tmp_path)
    payload = run_model_race_once(store, _payload(), _config())
    row = payload["scoreboard"][0]
    assert row["action"] == "bought"
    assert store.load_open_model_race_positions("default")[0]["side"] == "yes"


def test_no_entry_when_no_edge_clears_hurdle(tmp_path) -> None:
    store = _store(tmp_path)
    payload = _payload(probabilities=[_prob(p_yes=0.20, yes_ask="0.90", no_ask="0.60", no_bid="0.60")])
    result = run_model_race_once(store, payload, _config())
    assert result["scoreboard"][0]["action"] == "bought"
    assert store.load_open_model_race_positions("default")[0]["side"] == "no"


def test_no_entry_when_edge_below_hurdle(tmp_path) -> None:
    store = _store(tmp_path)
    payload = _payload(probabilities=[_prob(p_yes=0.54, yes_ask="0.50", no_ask="0.50")])
    result = run_model_race_once(store, payload, _config())
    assert result["scoreboard"][0]["action"] == "wait"
    assert store.load_open_model_race_positions("default") == []


def test_unavailable_model_skipped(tmp_path) -> None:
    store = _store(tmp_path)
    bad = _estimate()
    bad["successful"] = False
    result = run_model_race_once(store, _payload(estimates=[bad], probabilities=[]), _config())
    assert result["scoreboard"][0]["action"] == "unavailable"


def test_position_sizing_respects_max_risk() -> None:
    account = ModelRaceAccountState("m", "p", "m", Decimal("100"), Decimal("100"))
    qty = size_model_trade(Decimal("0.50"), account, ModelRaceConfig(max_risk_per_trade=Decimal("5")))
    assert qty == 10


def test_max_exposure_blocks_entries() -> None:
    account = ModelRaceAccountState("m", "p", "m", Decimal("100"), Decimal("100"), exposure=Decimal("25"))
    ok, reason = should_enter_model_trade(
            {"edge": Decimal("0.20"), "ask": Decimal("0.50"), "market_ticker": "T70", "bracket_upper_f": 71},
            account,
        ModelRaceConfig(require_exit_bid_for_entry=False),
        {"observed_high_so_far_f": 69},
    )
    assert not ok
    assert "max exposure" in reason


def test_daily_loss_limit_can_be_disabled_for_testing() -> None:
    trade = {
        "edge": Decimal("0.20"),
        "ask": Decimal("0.50"),
        "bid": Decimal("0.49"),
        "market_ticker": "T70",
        "bracket_upper_f": 71,
    }
    account = ModelRaceAccountState(
        "m",
        "p",
        "m",
        Decimal("100"),
        Decimal("100"),
        closed_pnl=Decimal("-11"),
    )
    ok, reason = should_enter_model_trade(
        trade,
        account,
        ModelRaceConfig(max_daily_fake_loss_per_model=Decimal("10")),
        {"observed_high_so_far_f": 69},
    )
    assert not ok
    assert reason == "daily fake loss limit hit"

    ok, reason = should_enter_model_trade(
        trade,
        account,
        ModelRaceConfig(max_daily_fake_loss_per_model=None),
        {"observed_high_so_far_f": 69},
    )
    assert ok
    assert reason == "edge clears hurdle"


def test_profit_target_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(probabilities=[_prob(yes_bid="0.61")]), _config())
    assert any(trade["reason"] == "profit target" for trade in result["closed_trades_this_update"])


def test_stop_loss_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(probabilities=[_prob(yes_bid="0.43")]), _config())
    assert any(trade["reason"] == "stop loss" for trade in result["closed_trades_this_update"])


def test_edge_disappears_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(probabilities=[_prob(p_yes=0.50, yes_bid="0.50", yes_ask="0.50")]), _config())
    assert any(trade["reason"] == "edge disappeared" for trade in result["closed_trades_this_update"])


def test_probability_drop_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(probabilities=[_prob(p_yes=0.80, yes_ask="0.50")]), _config())
    result = run_model_race_once(
        store,
        _payload(probabilities=[_prob(p_yes=0.60, yes_bid="0.52", yes_ask="0.40")]),
        _config(),
    )
    assert any(trade["reason"] == "probability drop" for trade in result["closed_trades_this_update"])


def test_bracket_invalidation_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(observed=72.0), _config())
    assert any(trade["reason"] == "weather invalidates bracket" for trade in result["closed_trades_this_update"])


def test_max_hold_exit() -> None:
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    position = {
        "side": "yes",
        "entry_price": "0.50",
        "quantity": "1",
        "max_hold_until_utc": past.isoformat(),
        "bracket_upper_f": 71,
    }
    reason = should_exit_model_position(position, _prob(), ModelRaceConfig(), {"observed_high_so_far_f": 69})
    assert reason == "max hold"


def test_force_flat_at_end(tmp_path) -> None:
    store = _store(tmp_path)
    payload = _payload()
    run_model_race_once(store, payload, _config())
    closed = force_flat_model_race(store, "default", payload, _config())
    assert closed
    assert store.load_open_model_race_positions("default") == []


def test_one_position_per_event_rule(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    run_model_race_once(store, _payload(probabilities=[_prob(ticker="T72", label="72-73", p_yes=0.9, yes_ask="0.50")]), _config())
    assert len(store.load_open_model_race_positions("default")) == 1


def test_rotation_waits_until_old_edge_gone(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(probabilities=[_prob(ticker="T72", label="72-73", p_yes=0.9, yes_ask="0.50")]), _config())
    assert result["scoreboard"][0]["action"].startswith("holding")


def test_multiple_open_positions_per_model_when_cap_allows(tmp_path) -> None:
    store = _store(tmp_path)
    config = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        max_open_positions_per_model=2,
        max_exposure_per_model=Decimal("100"),
        max_exposure_per_bracket=Decimal("50"),
        force_flat_time_local="23:59",
    )
    run_model_race_once(store, _payload(), config)
    second_payload = _payload(
        probabilities=[
            _prob(),
            _prob(ticker="T72", label="72-73", p_yes=0.90, yes_ask="0.50", yes_bid="0.50", lo=72, hi=73),
        ]
    )
    result = run_model_race_once(store, second_payload, config)
    assert result["scoreboard"][0]["action"] == "bought"
    positions = store.load_open_model_race_positions("default")
    assert len(positions) == 2
    assert {position["market_ticker"] for position in positions} == {"T70", "T72"}


def test_exit_monitor_can_close_one_of_multiple_positions(tmp_path) -> None:
    store = _store(tmp_path)
    config = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        max_open_positions_per_model=2,
        max_exposure_per_model=Decimal("100"),
        max_exposure_per_bracket=Decimal("50"),
        force_flat_time_local="23:59",
    )
    run_model_race_once(store, _payload(), config)
    run_model_race_once(
        store,
        _payload(
            probabilities=[
                _prob(),
                _prob(ticker="T72", label="72-73", p_yes=0.90, yes_ask="0.50", yes_bid="0.50", lo=72, hi=73),
            ]
        ),
        config,
    )
    exit_payload = _payload(
        probabilities=[
            _prob(yes_bid="0.43"),
            _prob(ticker="T72", label="72-73", p_yes=0.90, yes_ask="0.50", yes_bid="0.50", lo=72, hi=73),
        ]
    )
    result = run_model_race_exit_monitor(store, exit_payload, config)
    assert any(trade["market_ticker"] == "T70" and trade["reason"] == "stop loss" for trade in result["closed_trades_this_update"])
    positions = store.load_open_model_race_positions("default")
    assert len(positions) == 1
    assert positions[0]["market_ticker"] == "T72"


def test_outlier_model_blocked_when_configured(tmp_path) -> None:
    store = _store(tmp_path)
    estimates = [_estimate(), _estimate("noaa_herbie", "rap", 78.0), _estimate("open_meteo", "best_match", 71.0)]
    probs = [_prob(), _prob("noaa_herbie", "rap", ticker="T78", p_yes=0.9, yes_ask="0.50"), _prob("open_meteo", "best_match", ticker="T71", p_yes=0.6, yes_ask="0.50")]
    config = ModelRaceConfig(
        include_models=["current:current_weighted_blend", "open_meteo:best_match", "noaa_herbie:rap"],
        block_new_entries_if_model_spread_gt_f=20,
        block_outlier_model_entries=True,
    )
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probs), config)
    rap = [row for row in result["scoreboard"] if row["model_key"] == "noaa_herbie:rap"][0]
    assert rap["action"] == "blocked: outlier"


def test_compact_shell_output_required_columns(tmp_path) -> None:
    payload = run_model_race_once(_store(tmp_path), _payload(), _config())
    text = compact_model_race_text(payload)
    for header in ["Model", "Est", "Top", "P(top)", "Best trade", "Edge", "Action", "Cash", "Open", "Closed"]:
        assert header in text


def test_compact_shell_output_puts_advisor_details_in_wide_columns(tmp_path) -> None:
    config = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        force_flat_time_local="23:59",
        advisor_mode="rule_based",
    )
    payload = run_model_race_once(_store(tmp_path), _payload(), config)
    text = compact_model_race_text(payload)
    assert "Advisor Score Decision Validator Final" not in text
    assert "LLM      Score Risk   Final  Why" in text
    assert "WAIT" in text
    assert "\n  Advisor:" not in text


def test_compact_shell_output_shortens_full_market_titles(tmp_path) -> None:
    title = "Will the **high temp in LA** be 72-73 deg on Jun 22, 2026?"
    payload = run_model_race_once(
        _store(tmp_path),
        _payload(probabilities=[_prob(label=title, lo=72, hi=73)]),
        _config(),
    )
    text = compact_model_race_text(payload)
    assert "72-73 YES" in text
    assert title not in text


def test_open_positions_section_only_when_positions_exist(tmp_path) -> None:
    empty_payload = run_model_race_once(_store(tmp_path), _payload(probabilities=[_prob(p_yes=0.54, yes_ask="0.50")]), _config())
    full_payload = run_model_race_once(_store(tmp_path / "two"), _payload(), _config())
    assert "Open positions:" not in compact_model_race_text(empty_payload)
    assert "Open positions:" in compact_model_race_text(full_payload)


def test_closed_trades_section_only_when_trades_close(tmp_path) -> None:
    store = _store(tmp_path)
    first = run_model_race_once(store, _payload(), _config())
    second = run_model_race_once(store, _payload(probabilities=[_prob(yes_bid="0.61")]), _config())
    assert "Closed trades this update:" not in compact_model_race_text(first)
    assert "Closed trades this update:" in compact_model_race_text(second)


def test_model_race_debug_text_is_copy_paste_diagnostic(tmp_path) -> None:
    payload = run_model_race_once(
        _store(tmp_path),
        _payload(probabilities=[_prob(p_yes=0.54, yes_ask="0.50")]),
        _config(),
    )
    text = model_race_debug_text(payload)

    assert "COPY/PASTE DEBUG" in text
    assert "scoreboard:" in text
    assert "action=wait" in text
    assert "reason=" in text
    assert "open_positions_count=0" in text
    assert "closed_trades_this_update_count=0" in text
    assert "if open_positions_count=0, there is nothing currently available to sell" in text


def test_entry_requires_ask() -> None:
    account = ModelRaceAccountState("current:current_weighted_blend", "current", "current_weighted_blend", Decimal("100"), Decimal("100"))
    ok, reason = should_enter_model_trade(
        {
            "edge": Decimal("0.20"),
            "ask": None,
            "bid": Decimal("0.40"),
            "market_ticker": "T70",
            "bracket_upper_f": 71,
        },
        account,
        ModelRaceConfig(),
        {"observed_high_so_far_f": 69},
    )
    assert not ok
    assert reason == "ask missing"


def test_entry_requires_exit_bid_when_configured(tmp_path) -> None:
    payload = _payload(probabilities=[_prob(yes_bid=None, yes_ask="0.50")])
    result = run_model_race_once(_store(tmp_path), payload, _config())
    assert result["scoreboard"][0]["action"] == "blocked: exit bid missing"


def test_no_entry_when_spread_too_wide(tmp_path) -> None:
    payload = _payload(probabilities=[_prob(yes_bid="0.20", yes_ask="0.50")])
    result = run_model_race_once(_store(tmp_path), payload, _config())
    assert result["scoreboard"][0]["action"] == "blocked: spread too wide"


def test_no_entry_for_penny_contract_when_exit_bid_missing(tmp_path) -> None:
    payload = _payload(probabilities=[_prob(p_yes=0.50, yes_ask="0.02", yes_bid=None)])
    result = run_model_race_once(_store(tmp_path), payload, _config())
    assert "bid missing" in result["scoreboard"][0]["reason"]


def test_open_pnl_is_na_when_bid_missing(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_once(store, _payload(probabilities=[_prob(yes_bid=None)]), _config())
    text = compact_model_race_text(result)
    assert "open P/L n/a" in text
    assert "no exit bid" in text
    assert "$1." not in text


def test_missing_bid_increments_count_and_blocks_exit(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    result = run_model_race_exit_monitor(store, _payload(probabilities=[_prob(yes_bid=None)]), _config())
    position = store.load_open_model_race_positions("default")[0]
    assert position["missing_bid_count"] == 1
    assert position["liquidity_status"] == "no_exit_bid"
    assert result["closed_trades_this_update"] == []


def test_exit_blocked_when_bid_missing_after_max_hold(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    position = store.load_open_model_race_positions("default")[0]
    position["max_hold_until_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store.save_model_race_position(position)
    run_model_race_exit_monitor(store, _payload(probabilities=[_prob(yes_bid=None)]), _config())
    updated = store.load_open_model_race_positions("default")[0]
    assert updated["exit_blocked_reason"] == "max hold"


def test_manual_flatten_closes_only_positions_with_bids(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    payload = flatten_model_race(store, "default", _payload(probabilities=[_prob(yes_bid="0.55")]), _config())
    assert payload["positions_closed"] == 1
    assert payload["positions_blocked_no_bid"] == 0
    assert store.load_open_model_race_positions("default") == []


def test_manual_flatten_marks_no_bid_positions_blocked(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    payload = flatten_model_race(store, "default", _payload(probabilities=[_prob(yes_bid=None)]), _config())
    assert payload["positions_closed"] == 0
    assert payload["positions_blocked_no_bid"] == 1
    assert store.load_open_model_race_positions("default")


def test_force_flat_can_synthetic_zero_exit_no_bid_positions(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    config = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        force_flat_time_local="23:59",
        synthetic_zero_exit_on_force_flat=True,
    )

    closed = force_flat_model_race(store, "default", _payload(probabilities=[_prob(yes_bid=None)]), config)

    assert closed[0]["reason"] == "synthetic zero force flat"
    assert closed[0]["price"] == "0"
    assert store.load_open_model_race_positions("default") == []


def test_model_spread_blocks_all_new_entries(tmp_path) -> None:
    store = _store(tmp_path)
    estimates = [_estimate(high=70), _estimate("open_meteo", "best_match", 76)]
    probs = [_prob(), _prob("open_meteo", "best_match")]
    config = ModelRaceConfig(
        race_mode="consensus_guarded",
        include_models=["current:current_weighted_blend", "open_meteo:best_match"],
        block_outlier_model_entries=True,
    )
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probs), config)
    assert {row["action"] for row in result["scoreboard"]} == {"blocked: spread"}


def test_independent_mode_does_not_block_global_model_spread(tmp_path) -> None:
    store = _store(tmp_path)
    estimates = [_estimate(high=70), _estimate("open_meteo", "best_match", 76)]
    probs = [
        _prob(p_yes=0.90, yes_ask="0.50", yes_bid="0.49"),
        _prob("open_meteo", "best_match", p_yes=0.88, yes_ask="0.50", yes_bid="0.49"),
    ]
    config = ModelRaceConfig(include_models=["current:current_weighted_blend", "open_meteo:best_match"])
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probs), config)
    assert result["race_mode"] == "independent"
    assert result["entry_blocked_reason"] is None
    assert {row["action"] for row in result["scoreboard"]} == {"bought"}


def test_independent_mode_lets_nbm_trade_even_if_rap_far_away(tmp_path) -> None:
    store = _store(tmp_path)
    estimates = [_estimate(high=70), _estimate("noaa_herbie", "nbm", 70), _estimate("noaa_herbie", "rap", 78)]
    probs = [
        _prob(p_yes=0.52, yes_ask="0.50", yes_bid="0.49"),
        _prob("noaa_herbie", "nbm", ticker="T70", p_yes=0.90, yes_ask="0.50", yes_bid="0.49"),
        _prob("noaa_herbie", "rap", ticker="T78", p_yes=0.50, yes_ask="0.50", yes_bid="0.49"),
    ]
    config = ModelRaceConfig(include_models=["current:current_weighted_blend", "noaa_herbie:nbm", "noaa_herbie:rap"])
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probs), config)
    nbm = [row for row in result["scoreboard"] if row["model_key"] == "noaa_herbie:nbm"][0]
    assert nbm["action"] == "bought"


def test_independent_mode_lets_rap_trade_when_outlier_by_default(tmp_path) -> None:
    store = _store(tmp_path)
    estimates = [_estimate(), _estimate("open_meteo", "best_match", 71), _estimate("noaa_herbie", "rap", 78)]
    probs = [
        _prob(p_yes=0.52, yes_ask="0.50", yes_bid="0.49"),
        _prob("open_meteo", "best_match", p_yes=0.52, yes_ask="0.50", yes_bid="0.49"),
        _prob("noaa_herbie", "rap", ticker="T78", p_yes=0.90, yes_ask="0.50", yes_bid="0.49"),
    ]
    config = ModelRaceConfig(include_models=["current:current_weighted_blend", "open_meteo:best_match", "noaa_herbie:rap"])
    result = run_model_race_once(store, _payload(estimates=estimates, probabilities=probs), config)
    rap = [row for row in result["scoreboard"] if row["model_key"] == "noaa_herbie:rap"][0]
    assert rap["action"] == "bought / outlier watch"


def test_consensus_guarded_mode_displays_global_spread_block(tmp_path) -> None:
    estimates = [_estimate(high=70), _estimate("open_meteo", "best_match", 76)]
    probs = [_prob(p_yes=0.90, yes_ask="0.50", yes_bid="0.49"), _prob("open_meteo", "best_match", p_yes=0.90, yes_ask="0.50", yes_bid="0.49")]
    config = ModelRaceConfig(race_mode="consensus_guarded", include_models=["current:current_weighted_blend", "open_meteo:best_match"])
    result = run_model_race_once(_store(tmp_path), _payload(estimates=estimates, probabilities=probs), config)
    text = compact_model_race_text(result)
    assert "Race mode: CONSENSUS_GUARDED - new entries blocked because spread > 4F" in text


def test_compact_output_independent_mode_no_global_spread_block(tmp_path) -> None:
    estimates = [_estimate(high=70), _estimate("open_meteo", "best_match", 76)]
    probs = [_prob(p_yes=0.90, yes_ask="0.50", yes_bid="0.49"), _prob("open_meteo", "best_match", p_yes=0.90, yes_ask="0.50", yes_bid="0.49")]
    config = ModelRaceConfig(include_models=["current:current_weighted_blend", "open_meteo:best_match"])
    result = run_model_race_once(_store(tmp_path), _payload(estimates=estimates, probabilities=probs), config)
    text = compact_model_race_text(result)
    assert "Race mode: INDEPENDENT - no global spread block" in text
    assert "New entries: BLOCKED because spread" not in text


def test_stored_equity_payload_includes_race_mode_and_blocked_reason(tmp_path) -> None:
    store = _store(tmp_path)
    payload = _payload(probabilities=[_prob(yes_bid=None)])
    run_model_race_once(store, payload, _config())
    row = store.conn.execute("SELECT payload_json FROM model_race_equity ORDER BY id DESC LIMIT 1").fetchone()
    details = json.loads(row["payload_json"])
    assert details["race_mode"] == "independent"
    assert details["blocked_reason"] == "exit bid missing"


def test_spread_two_to_four_reduces_trade_size() -> None:
    account = ModelRaceAccountState("m", "p", "m", Decimal("100"), Decimal("100"))
    config = ModelRaceConfig(race_mode="consensus_guarded", max_risk_per_trade=Decimal("5"), reduce_size_if_spread_gt_f=2.0)
    normal = size_model_trade(Decimal("0.50"), account, config, model_spread_f=1.0)
    reduced = size_model_trade(Decimal("0.50"), account, config, model_spread_f=3.0)
    assert normal == 10
    assert reduced == 5


def test_cooldown_after_stop_loss_blocks_reentry(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    run_model_race_once(store, _payload(probabilities=[_prob(yes_bid="0.43")]), _config())
    result = run_model_race_once(store, _payload(), _config())
    assert "cooldown" in result["scoreboard"][0]["action"]


def test_zero_cooldown_ignores_existing_stop_loss_cooldown(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    run_model_race_once(store, _payload(probabilities=[_prob(yes_bid="0.43")]), _config())
    no_cooldown = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        cooldown_after_stop_minutes=0,
        force_flat_time_local="23:59",
    )
    result = run_model_race_once(store, _payload(), no_cooldown)
    assert result["scoreboard"][0]["action"] == "bought"


def test_high_entry_price_filter_blocks_and_override_allows(tmp_path) -> None:
    blocked = run_model_race_once(
        _store(tmp_path),
        _payload(probabilities=[_prob(p_yes=0.95, yes_ask="0.85", yes_bid="0.84")]),
        _config(),
    )
    assert blocked["scoreboard"][0]["action"] == "blocked: price too high"
    allowed_config = ModelRaceConfig(
        include_models=["current:current_weighted_blend"],
        allow_high_price_entries=True,
    )
    allowed = run_model_race_once(
        _store(tmp_path / "allowed"),
        _payload(probabilities=[_prob(p_yes=0.95, yes_ask="0.85", yes_bid="0.84")]),
        allowed_config,
    )
    assert allowed["scoreboard"][0]["action"] == "bought"


def test_exit_monitor_closes_profit_target_and_stop_loss(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    profit = run_model_race_exit_monitor(store, _payload(probabilities=[_prob(yes_bid="0.61")]), _config())
    assert any(trade["reason"] == "profit target" for trade in profit["closed_trades_this_update"])
    store2 = _store(tmp_path / "stop")
    run_model_race_once(store2, _payload(), _config())
    stop = run_model_race_exit_monitor(store2, _payload(probabilities=[_prob(yes_bid="0.43")]), _config())
    assert any(trade["reason"] == "stop loss" for trade in stop["closed_trades_this_update"])


def test_compact_output_shows_blocked_reasons(tmp_path) -> None:
    payload = run_model_race_once(
        _store(tmp_path),
        _payload(probabilities=[_prob(yes_bid="0.20", yes_ask="0.50")]),
        _config(),
    )
    text = compact_model_race_text(payload)
    assert "blocked: spread too wide" in text


def test_report_command_payload_shows_leaderboard(tmp_path) -> None:
    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    payload = model_race_report_payload(store, "default")
    assert payload["leaderboard"]
    assert "PAPER MODEL RACE REPORT" in model_race_report_text(payload)


def test_reset_command_requires_confirmation() -> None:
    result = CliRunner().invoke(app, ["paper-model-race-reset"])
    assert result.exit_code != 0
    assert "Reset requires --confirm" in result.output


def test_flatten_command_requires_confirmation() -> None:
    result = CliRunner().invoke(app, ["paper-model-race-flatten"])
    assert result.exit_code != 0
    assert "Flatten requires --confirm" in result.output


def test_json_output_works(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))
    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload", lambda *_args: _payload())
    result = CliRunner().invoke(app, ["paper-model-race-once", "--json"])
    assert result.exit_code == 0
    assert '"scoreboard"' in result.output


def test_exit_monitor_does_not_refresh_direct_noaa_models(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))
    monkeypatch.setattr(
        "kalshi_weather.cli._model_race_model_payload",
        lambda *_args: (_ for _ in ()).throw(AssertionError("heavy refresh should not run")),
    )
    monkeypatch.setattr("kalshi_weather.cli._model_race_exit_payload", lambda *_args, **_kwargs: _payload())
    result = CliRunner().invoke(app, ["paper-model-race-exit-monitor", "--max-iterations", "1"])
    assert result.exit_code == 0
    assert "FAKE MONEY ONLY" in result.output


def test_exit_payload_skips_kalshi_when_no_open_positions(monkeypatch, tmp_path) -> None:
    from kalshi_weather import cli as cli_module
    from kalshi_weather.config import load_settings

    store = _store(tmp_path)
    store.save_model_estimate(_estimate())
    store.save_model_estimate_probability(_prob())
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    monkeypatch.setattr(
        "kalshi_weather.cli._kalshi",
        lambda _settings: (_ for _ in ()).throw(AssertionError("Kalshi should not be called")),
    )
    payload = cli_module._model_race_exit_payload(load_settings(), "KXHIGHLAX", "KLAX", race_id="default")
    assert payload["exit_monitor_market_refresh"] == "skipped_no_open_positions"


def test_latest_stored_model_payload_uses_latest_probability_rows(monkeypatch, tmp_path) -> None:
    from kalshi_weather import cli as cli_module
    from kalshi_weather.config import load_settings

    store = _store(tmp_path)
    old_estimate_id = store.save_model_estimate(_estimate(high=71.0))
    old_probability = _prob(
        ticker="T72",
        label="72-73",
        p_yes=0.36,
        yes_ask="0.90",
        no_ask="0.00",
        lo=72,
        hi=73,
    )
    old_probability["estimate_id"] = old_estimate_id
    store.save_model_estimate_probability(old_probability)

    latest_estimate_id = store.save_model_estimate(_estimate(high=72.0))
    latest_probability = _prob(
        ticker="T72",
        label="72-73",
        p_yes=1.0,
        yes_ask="0.99",
        no_ask="0.64",
        lo=72,
        hi=73,
    )
    latest_probability["estimate_id"] = latest_estimate_id
    store.save_model_estimate_probability(latest_probability)

    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    payload = cli_module._latest_stored_model_payload(load_settings(), "KXHIGHLAX", "KLAX")

    assert [row["estimate_id"] for row in payload["probabilities"]] == [latest_estimate_id]
    assert payload["probabilities"][0]["p_yes"] == 1.0
    assert Decimal(payload["probabilities"][0]["no_edge"]) < 0


def test_paper_model_race_run_supports_separate_entry_exit_intervals(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    calls = {"entry": 0, "exit": 0, "sleep": []}
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))

    def entry_payload(*_args):
        calls["entry"] += 1
        return _payload()

    def exit_payload(*_args, **_kwargs):
        calls["exit"] += 1
        return _payload(probabilities=[_prob(yes_bid="0.55")])

    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload", entry_payload)
    monkeypatch.setattr("kalshi_weather.cli._model_race_exit_payload", exit_payload)
    monkeypatch.setattr("kalshi_weather.cli.time.sleep", lambda seconds: calls["sleep"].append(seconds))
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--entry-interval-seconds",
            "900",
            "--exit-interval-seconds",
            "60",
            "--max-entry-iterations",
            "1",
            "--max-exit-iterations",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert calls["entry"] == 1
    assert calls["exit"] == 2
    assert calls["sleep"] == [60, 60]


def test_paper_model_race_run_passes_target_date_to_entry_payload(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    captured: dict[str, date | None] = {}
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli.current_lax_market_date", lambda *_args, **_kwargs: date(2026, 6, 24))
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))

    def entry_payload(_settings, _series, _station, target_date=None):
        captured["target_date"] = target_date
        return _payload()

    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload", entry_payload)
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--target-date",
            "2026-06-25",
            "--entry-only",
            "--max-entry-iterations",
            "1",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0
    assert captured["target_date"] == date(2026, 6, 25)


def test_paper_model_race_run_debug_decisions_outputs_diagnostic_block(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))
    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload", lambda *_args: _payload())
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--entry-only",
            "--max-entry-iterations",
            "1",
            "--debug-decisions",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )

    assert result.exit_code == 0
    assert "COPY/PASTE DEBUG" in result.output
    assert "scoreboard:" in result.output
    assert "open_positions_count=" in result.output


def test_paper_model_race_worker_mode_refreshes_models_separately(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    calls: list[str] = []
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))

    def worker_payload(_settings, _series, _station, spec, _context=None):
        calls.append(spec["model_key"])
        return _payload(
            estimates=[_estimate(spec["provider"], spec["model_id"])],
            probabilities=[_prob(spec["provider"], spec["model_id"])],
        )

    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload_for_spec", worker_payload)
    monkeypatch.setattr(
        "kalshi_weather.cli._model_race_model_payload",
        lambda *_args: (_ for _ in ()).throw(AssertionError("batch refresh should not run")),
    )
    monkeypatch.setattr("kalshi_weather.cli._prediction_context", lambda *_args: {})
    monkeypatch.setattr("kalshi_weather.cli.time.sleep", lambda _seconds: None)
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--model-worker-mode",
            "--entry-only",
            "--max-entry-iterations",
            "1",
            "--model-worker-count",
            "2",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0
    assert set(calls) == {spec["model_key"] for spec in model_specs()}


def test_paper_model_race_run_excludes_noaa_herbie(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    estimates = [
        _estimate("current", "current_weighted_blend"),
        _estimate("open_meteo", "best_match"),
        _estimate("noaa_herbie", "hrrr"),
    ]
    probs = [
        _prob("current", "current_weighted_blend"),
        _prob("open_meteo", "best_match"),
        _prob("noaa_herbie", "hrrr"),
    ]
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _store(tmp_path))
    monkeypatch.setattr("kalshi_weather.cli._model_race_model_payload", lambda *_args: _payload(estimates, probs))
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--exclude-models",
            "noaa_herbie",
            "--entry-only",
            "--max-entry-iterations",
            "1",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0
    assert "current_blend" in result.output
    assert "best_match" in result.output
    assert "HRRR" not in result.output


def test_paper_model_race_run_exposes_bracket_exposure_for_heavier_trades(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    store = _store(tmp_path)
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    monkeypatch.setattr(
        "kalshi_weather.cli._model_race_model_payload",
        lambda *_args: _payload(probabilities=[_prob(p_yes=0.70, yes_ask="0.12", yes_bid="0.11")]),
    )
    result = CliRunner().invoke(
        app,
        [
            "paper-model-race-run",
            "--entry-only",
            "--max-entry-iterations",
            "1",
            "--max-risk-per-trade",
            "12",
            "--max-exposure-per-model",
            "100",
            "--max-exposure-per-bracket",
            "12",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0
    position = store.load_open_model_race_positions("default")[0]
    assert Decimal(position["quantity"]) * Decimal(position["entry_price"]) == Decimal("12.00")


def test_csv_report_output_works(monkeypatch, tmp_path) -> None:
    from kalshi_weather.config import load_settings

    store = _store(tmp_path)
    run_model_race_once(store, _payload(), _config())
    settings = load_settings()
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    output = tmp_path / "leaderboard.csv"
    result = CliRunner().invoke(app, ["paper-model-race-report", "--csv", "--output", str(output)])
    assert result.exit_code == 0
    assert "model_key" in output.read_text()


def test_safety_no_live_order_terms_in_model_race_source() -> None:
    source = (Path(__file__).parents[1] / "src" / "kalshi_weather" / "trading" / "model_race.py").read_text()
    assert "create-order" not in source
    assert "requests.post" not in source
    assert "place_order" not in source
