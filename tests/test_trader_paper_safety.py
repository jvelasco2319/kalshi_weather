from datetime import datetime, timezone
from pathlib import Path

from kalshi_weather.cli import (
    _trader_paper_run_header,
    _trader_table_row,
    _validate_paper_buy_against_portfolio,
)
from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from kalshi_weather.trader_agent.trader_types import RiskLimits
from test_trader_trade_board import sample_context


def _candidate() -> dict:
    context = sample_context().to_dict()
    return next(
        row
        for row in context["candidate_trades"]
        if row["contract_ticker"] == "T72-T73" and row["side"] == "NO" and row["action"] == "BUY"
    )


def _decision(candidate: dict, quantity: int = 50) -> dict:
    return {
        "action": "PLACE_FAKE_LIMIT_BUY",
        "selected_candidate_id": candidate["candidate_id"],
        "contract_ticker": candidate["contract_ticker"],
        "bracket": candidate["bracket_label"],
        "side": candidate["side"],
        "limit_price_cents": candidate["entry_price_cents"],
        "max_contracts": quantity,
        "estimated_edge_cents": candidate["fee_adjusted_edge_cents"],
    }


def _portfolio(**overrides):
    base = {
        "cash_value": 1000.0,
        "open_exposure_value": 0.0,
        "open_pnl_value": 0.0,
        "drawdown_value": 0.0,
        "position_groups": 0,
        "exposure_by_bracket": {},
        "contracts_by_bracket": {},
        "contracts_by_side": {},
    }
    base.update(overrides)
    return base


def test_buy_rejected_when_cash_would_go_negative() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(cash_value=1.0),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(),
    )

    assert result is not None
    assert result.rejection_reason == "insufficient fake cash"


def test_existing_negative_cash_blocks_new_buy() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate, quantity=1),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(cash_value=-1.0),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(),
    )

    assert result is not None
    assert result.rejection_reason == "insufficient fake cash"


def test_cumulative_total_exposure_cap_enforced() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(open_exposure_value=245.0),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(max_total_exposure_dollars=250.0),
    )

    assert result is not None
    assert result.rejection_reason == "exposure cap"


def test_per_bracket_exposure_cap_enforced() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(exposure_by_bracket={candidate["bracket_label"]: 95.0}),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(max_exposure_dollars_per_bracket=100.0),
    )

    assert result is not None
    assert result.rejection_reason == "bracket exposure cap"


def test_max_contracts_per_bracket_enforced() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(contracts_by_bracket={candidate["bracket_label"]: 490}),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(max_contracts_per_bracket=500),
    )

    assert result is not None
    assert result.rejection_reason == "bracket contract cap"


def test_max_contracts_per_side_enforced() -> None:
    candidate = _candidate()
    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(contracts_by_side={candidate["side"]: 980}),
        open_positions=[],
        fills=[],
        risk_limits=RiskLimits(max_contracts_per_side=1000),
    )

    assert result is not None
    assert result.rejection_reason == "side contract cap"


def test_scale_in_disabled_blocks_repeated_same_bracket_side_buy() -> None:
    candidate = _candidate()
    open_position = {
        "contract_ticker": candidate["contract_ticker"],
        "bracket_label": candidate["bracket_label"],
        "side": candidate["side"],
        "quantity": 10,
        "avg_entry_price_cents": candidate["entry_price_cents"],
    }

    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(position_groups=1),
        open_positions=[open_position],
        fills=[],
        risk_limits=RiskLimits(allow_scale_in=False),
    )

    assert result is not None
    assert result.rejection_reason == "already positioned; scale-in disabled"


def test_same_candidate_cooldown_blocks_repeated_buy() -> None:
    candidate = _candidate()
    recent_fill = {
        "action": "BUY",
        "selected_candidate_id": candidate["candidate_id"],
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }

    result = _validate_paper_buy_against_portfolio(
        decision=_decision(candidate),
        candidate=candidate,
        context=sample_context().to_dict(),
        portfolio=_portfolio(),
        open_positions=[],
        fills=[recent_fill],
        risk_limits=RiskLimits(same_candidate_cooldown_minutes=15.0),
    )

    assert result is not None
    assert result.rejection_reason == "same candidate cooldown"


def test_existing_journal_state_is_reported_in_header() -> None:
    text = _trader_paper_run_header(
        series="KXHIGHLAX",
        station="KLAX",
        starting_cash=1000.0,
        interval_seconds=60,
        duration_minutes=5,
        max_iterations=5,
        loaded_existing_portfolio=True,
        portfolio={
            "cash": "$950.00",
            "equity": "$990.00",
            "open_exposure": "$50.00",
            "total_contracts": 100,
        },
        implicit_resume_warning=True,
    )

    assert "Loaded existing paper portfolio: yes" in text
    assert "Portfolio cash at run start: $950.00" in text
    assert "Portfolio total value at run start: $990.00" in text
    assert "WARNING: Resuming existing paper portfolio from journal." in text


def test_table_note_is_deterministic_and_short() -> None:
    payload = {
        "context": sample_context().to_dict(),
        "decision": {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "selected_candidate_id": _candidate()["candidate_id"],
            "confidence": "high",
            "estimated_edge_cents": 20.0,
        },
        "validation": {"valid": False, "rejection_reason": "insufficient fake cash"},
        "open_positions": [],
        "portfolio": _portfolio(cash_value=1.0),
    }

    row = _trader_table_row(payload, starting_cash=1000.0)

    assert row["note"] == "insufficient fake cash"
    assert len(row["note"]) < 40


def test_full_llm_reasoning_still_saved_to_journal(tmp_path: Path) -> None:
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    context = sample_context().to_dict()
    context["model_estimates"] = [
        {"provider": "current:current_weighted_blend", "high_f": 70.3},
        {"provider": "open_meteo:best_match", "high_f": 70.6},
    ]
    journal.record_run(
        {
            "context": context,
            "decision": {"action": "HOLD", "trader_thesis": "full LLM thesis retained"},
            "validation": {"valid": True, "rejection_reason": None},
        }
    )

    latest = journal.latest(limit=1)[0]

    assert latest["decision"]["trader_thesis"] == "full LLM thesis retained"
    assert latest["context"]["model_estimates"][0]["provider"] == "current:current_weighted_blend"
    assert latest["context"]["market_brackets"][0]["bracket_label"] == "65 or below"


def test_paper_settlement_closes_losing_open_position(tmp_path: Path) -> None:
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "KXHIGHLAX-26JUN30-B69.5",
            "side": "YES",
            "quantity": 100,
            "limit_price_cents": 5,
            "metadata": {"bracket_label": "Will the **high temp in LA** be 69-70° on Jun 30, 2026?"},
        },
        market_brackets=None,
    )

    result = journal.settle_open_positions(
        winning_bracket=">72",
        final_high_f=73.0,
        market_date="2026-06-30",
        source="test",
    )
    fills = journal.load_fills()

    assert result["positions_settled"] == 1
    assert result["contracts_settled"] == 100
    assert result["settlement_value_dollars"] == 0.0
    assert result["realized_pnl_dollars"] == -5.0
    assert journal.load_open_positions() == []
    assert fills[-1]["action"] == "CLOSE"
    assert fills[-1]["settlement_action"] == "SETTLE"
    assert fills[-1]["settled_result"] == 0


def test_paper_settlement_closes_winning_open_position(tmp_path: Path) -> None:
    journal = SqliteTraderJournal(tmp_path / "trader.sqlite")
    journal.execute_paper_order(
        {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "KXHIGHLAX-26JUN30-B69.5",
            "side": "YES",
            "quantity": 10,
            "limit_price_cents": 5,
            "metadata": {"bracket_label": "69-70"},
        },
        market_brackets=None,
    )

    result = journal.settle_open_positions(winning_bracket="69-70", final_high_f=70.0)
    second = journal.settle_open_positions(winning_bracket="69-70", final_high_f=70.0)

    assert result["settlement_value_dollars"] == 10.0
    assert result["realized_pnl_dollars"] == 9.5
    assert second["positions_settled"] == 0


def test_no_real_kalshi_order_code_added() -> None:
    agent_dir = Path(__file__).resolve().parents[1] / "src" / "kalshi_weather" / "trader_agent"
    source = "\n".join(path.read_text(encoding="utf-8") for path in agent_dir.glob("*.py"))

    assert "PLACE_REAL_ORDER" not in source
    assert "submit_order" not in source
