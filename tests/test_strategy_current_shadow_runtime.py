from __future__ import annotations

from decimal import Decimal

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.strategy_current.decision_engine import MarketQuote, choose_shadow_candidate
from kalshi_weather.strategy_current.probabilities import BracketProbability
from kalshi_weather.strategy_current.reason_codes import (
    NO_TRADE_BOOK_INVALID,
    NO_TRADE_ROI_BELOW_HURDLE,
    SHADOW_CANDIDATE_YES,
)
from kalshi_weather.strategy_current.shadow_runtime import (
    ShadowOrderSink,
    incomplete_capture_decision,
)


def _prob(bracket_id: str, safe_yes: float, safe_no: float) -> BracketProbability:
    return BracketProbability(
        bracket_id=bracket_id,
        posterior_mean_yes=safe_yes,
        safe_yes=safe_yes,
        posterior_mean_no=safe_no,
        safe_no=safe_no,
        effective_sample_size=20.0,
        component_probabilities={},
    )


def test_shadow_order_sink_cannot_submit_exchange_orders() -> None:
    sink = ShadowOrderSink()
    assert not hasattr(sink, "create_order")
    assert not hasattr(sink, "submit_order")
    assert not hasattr(sink, "place_order")

    action = sink.record(incomplete_capture_decision())
    assert action.reason_code == "NO_TRADE_CAPTURE_INCOMPLETE"
    assert len(sink.actions) == 1


def test_decision_engine_selects_best_shadow_candidate_or_reason() -> None:
    result = choose_shadow_candidate(
        [_prob("b1", safe_yes=0.80, safe_no=0.20)],
        {"b1": MarketQuote("b1", "T1", yes_ask=Decimal("0.40"), no_ask=Decimal("0.70"))},
        quantity=10,
        hurdle=Decimal("0.15"),
    )

    assert result.reason_code == SHADOW_CANDIDATE_YES
    assert result.candidate is not None
    assert result.candidate.market_ticker == "T1"

    no_quote = choose_shadow_candidate([], {}, quantity=1, hurdle=Decimal("0.15"))
    assert no_quote.reason_code == NO_TRADE_BOOK_INVALID

    low_roi = choose_shadow_candidate(
        [_prob("b1", safe_yes=0.51, safe_no=0.49)],
        {"b1": MarketQuote("b1", "T1", yes_ask=Decimal("0.50"), no_ask=Decimal("0.50"))},
        quantity=1,
        hurdle=Decimal("0.15"),
    )
    assert low_roi.reason_code == NO_TRADE_ROI_BELOW_HURDLE


def test_strategy_cli_commands_are_shadow_only() -> None:
    runner = CliRunner()
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "strategy-shadow-run" in help_result.output
    assert "strategy-status" in help_result.output

    status = runner.invoke(app, ["strategy-status", "--json"])
    assert status.exit_code == 0
    assert '"mode": "shadow"' in status.output
    assert '"live_trading_enabled": false' in status.output

    run = runner.invoke(app, ["strategy-shadow-run", "--json"])
    assert run.exit_code == 0
    assert '"orders_submitted": 0' in run.output
    assert '"NO_TRADE_CAPTURE_INCOMPLETE"' in run.output
