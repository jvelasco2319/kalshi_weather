from __future__ import annotations

from datetime import date, datetime, timezone

from typer.testing import CliRunner
from typer.main import get_command

from kalshi_weather.cli import app
from kalshi_weather.market_lifecycle.clock import NwsClimateDay, StationClock
from kalshi_weather.market_lifecycle.lifecycle_profiles import profile_for_lifecycle_state
from kalshi_weather.market_lifecycle.lifecycle_state import LifecycleState, determine_lifecycle_state
from kalshi_weather.market_lifecycle.market_calendar import (
    MarketTimeline,
    incomplete_timeline,
    timeline_from_markets,
)
from kalshi_weather.market_lifecycle.settlement_finalizer import Position, settle_positions
from kalshi_weather.runtime_paths import latest_run_pointer_path
from kalshi_weather.trader_agent.journal import SqliteTraderJournal


def test_lax_nws_climate_day_uses_local_standard_time() -> None:
    clock = StationClock.for_station("KLAX")
    start, end = NwsClimateDay(clock, date(2026, 6, 30)).bounds_utc()

    assert start == datetime(2026, 6, 30, 8, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, 7, 59, 59, 999999, tzinfo=timezone.utc)


def test_timeline_from_markets_merges_event_metadata() -> None:
    markets = [
        {
            "ticker": "KXHIGHLAX-26JUN30-B70.5",
            "event_ticker": "KXHIGHLAX-26JUN30",
            "open_time": "2026-06-29T14:00:00Z",
            "close_time": "2026-07-01T00:00:00Z",
            "status": "open",
        },
        {
            "ticker": "KXHIGHLAX-26JUN30-B72.5",
            "event_ticker": "KXHIGHLAX-26JUN30",
            "open_time": "2026-06-29T14:01:00Z",
            "close_time": "2026-07-01T00:00:30Z",
            "status": "open",
        },
    ]

    timeline = timeline_from_markets(
        series_ticker="KXHIGHLAX",
        markets=markets,
        target_date=date(2026, 6, 30),
    )

    assert timeline is not None
    assert timeline.event_ticker == "KXHIGHLAX-26JUN30"
    assert timeline.target_date == date(2026, 6, 30)
    assert timeline.metadata_complete_for_trading
    assert timeline.market_open_time_utc == datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc)
    assert timeline.trade_close_utc == datetime(2026, 7, 1, 0, 0, 30, tzinfo=timezone.utc)


def test_lifecycle_closed_market_waits_before_official_settlement() -> None:
    timeline = MarketTimeline(
        series_ticker="KXHIGHLAX",
        event_ticker="KXHIGHLAX-26JUN30",
        target_date=date(2026, 6, 30),
        market_open_time_utc=datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc),
        last_trading_time_utc=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
        status="open",
    )
    after_close = datetime(2026, 7, 1, 0, 5, tzinfo=timezone.utc)

    assert determine_lifecycle_state(after_close, timeline) == LifecycleState.MARKET_CLOSED_NO_TRADING
    assert (
        determine_lifecycle_state(after_close, timeline, official_result_available=True)
        == LifecycleState.SETTLE_PAPER_PORTFOLIO
    )


def test_lifecycle_incomplete_timeline_blocks_trading() -> None:
    timeline = incomplete_timeline(
        series_ticker="KXHIGHLAX",
        event_ticker="KXHIGHLAX-26JUN30",
        target_date=date(2026, 6, 30),
    )

    assert determine_lifecycle_state(datetime.now(timezone.utc), timeline) == LifecycleState.TIMELINE_INCOMPLETE


def test_close_only_profile_disallows_new_entries() -> None:
    profile = profile_for_lifecycle_state(LifecycleState.CLOSE_ONLY)

    assert profile.allow_new_entries is False
    assert profile.allow_close is True
    assert profile.allow_cancel is True


def test_settlement_finalizer_settles_yes_and_no_positions() -> None:
    result = settle_positions(
        cash_before_settlement=90.0,
        starting_cash=100.0,
        winning_bracket="70-71",
        positions=[
            Position(bracket="70-71", side="YES", quantity=10, avg_cost_cents=20),
            Position(bracket="72-73", side="NO", quantity=5, avg_cost_cents=80),
        ],
    )

    assert result.winning_bracket == "70-71"
    assert result.settlement_value_dollars == 15.0
    assert result.final_cash_dollars == 105.0
    assert result.realized_pnl_dollars == 5.0


def test_final_paper_settlement_blocks_duplicate_finalization(tmp_path) -> None:
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "KXHIGHLAX-26JUN30-B69.5",
            "side": "YES",
            "quantity": 100,
            "limit_price_cents": 5,
            "metadata": {"bracket_label": "69-70"},
        },
        market_brackets=None,
    )

    first = journal.settle_open_positions(
        winning_bracket=">72",
        final_high_f=73.0,
        market_date="2026-06-30",
        settlement_status="final_official",
    )
    second = journal.settle_open_positions(
        winning_bracket=">72",
        final_high_f=73.0,
        market_date="2026-06-30",
        settlement_status="final_official",
    )

    assert first["executed"] is True
    assert first["settlement_recorded"] is True
    assert journal.has_final_settlement("2026-06-30")
    assert second["blocked"] is True
    assert second["reason"] == "paper journal already finalized for this market date"


def test_lifecycle_cli_help_lists_new_commands() -> None:
    runner = CliRunner()

    settle = runner.invoke(app, ["trader-settle-paper-run", "--help"])
    cycle = runner.invoke(app, ["trader-market-cycle", "--help"])

    assert settle.exit_code == 0
    assert "--settlement-mode" in settle.output
    assert "--force-resettle" in settle.output
    settle_command = get_command(app).commands["trader-settle-paper-run"]
    option_aliases = {
        param.name: set(getattr(param, "opts", []))
        for param in settle_command.params
    }
    assert "--official-high-f" in option_aliases["final_high_f"]
    assert "--settlement-source-status" in option_aliases["settlement_mode"]
    assert cycle.exit_code == 0
    assert "--cycle-mode" in cycle.output
    assert "--settle-when-final" in cycle.output


def test_market_cycle_dry_run_fallback_writes_lifecycle_state(tmp_path) -> None:
    pointer = latest_run_pointer_path()
    old_text = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    try:
        result = CliRunner().invoke(
            app,
            [
                "trader-market-cycle",
                "--series",
                "KXHIGHLAX",
                "--station",
                "KLAX",
                "--target-date",
                "2026-06-30",
                "--cycle-mode",
                "once",
                "--allow-metadata-fallback-times",
                "--dry-run",
                "--debug-root",
                str(tmp_path),
                "--allow-noncanonical-output-paths",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "Live trading: DISABLED" in result.output
        assert list(tmp_path.glob("*/lifecycle_state.json"))
    finally:
        if old_text is None:
            pointer.unlink(missing_ok=True)
        else:
            pointer.write_text(old_text, encoding="utf-8")
