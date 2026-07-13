from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.strategy_current.decision_engine import TradeCandidate
from kalshi_weather.strategy_current.replay import (
    DepthLevel,
    ReplayEvent,
    chronological_replay,
    simulate_maker_fill_from_book,
    simulate_taker_fill,
)


def _candidate() -> TradeCandidate:
    return TradeCandidate(
        market_ticker="T",
        bracket_id="b1",
        side="yes",
        quantity=10,
        limit_price=Decimal("0.50"),
        conservative_probability=Decimal("0.80"),
        expected_roi=Decimal("0.50"),
        expected_value=Decimal("2.00"),
        reason_code="SHADOW_CANDIDATE_YES",
    )


def test_chronological_replay_marks_candles_analytics_only() -> None:
    report = chronological_replay(
        [
            ReplayEvent(
                "c1",
                "candle",
                datetime(2026, 7, 7, 20, tzinfo=timezone.utc),
                {"close": "0.50"},
            )
        ]
    )

    assert report.candle_event_count == 1
    assert report.executable_simulation_count == 0
    assert report.candle_only_executable is False
    assert "analytics-only" in report.notes[0]


def test_taker_simulation_requires_depth_and_price() -> None:
    fill = simulate_taker_fill(
        _candidate(),
        [
            DepthLevel(Decimal("0.49"), Decimal("4")),
            DepthLevel(Decimal("0.50"), Decimal("6")),
        ],
    )

    assert fill.executable is True
    assert fill.filled_count == Decimal("10")
    assert fill.average_price == Decimal("0.496")

    partial = simulate_taker_fill(_candidate(), [DepthLevel(Decimal("0.50"), Decimal("3"))])
    assert partial.executable is False
    assert partial.reason == "PARTIAL_DEPTH"


def test_maker_simulation_refuses_unsynchronized_books_or_missing_latency() -> None:
    assert simulate_maker_fill_from_book(
        _candidate(),
        synchronized_book=False,
        latency_assumption_ms=100,
    ).reason == "UNSYNCHRONIZED_BOOK"
    assert simulate_maker_fill_from_book(
        _candidate(),
        synchronized_book=True,
        latency_assumption_ms=None,
    ).reason == "MISSING_LATENCY_ASSUMPTION"


def test_strategy_replay_cli_reports_empty_shadow_replay() -> None:
    result = CliRunner().invoke(app, ["strategy-replay", "--json"])

    assert result.exit_code == 0
    assert '"strategy_id": "klax-current-five-model-2026-07-11"' in result.output
    assert '"event_count": 0' in result.output
    assert '"candle_only_executable": false' in result.output
