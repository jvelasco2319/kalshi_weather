from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.cli import (
    _build_decision_audit,
    _decision_audit_text,
    _debug_data_freshness,
    _edge_cost_config,
    _edge_portfolio_state_from_context,
    _edge_risk_config,
    _model_consensus_summary,
    _model_uncertainty_penalties,
    _apply_probability_floors,
    _portfolio_reconciliation,
    _profile_decision_for_context,
    _rule_decision_for_context,
    _rule_engine_candidates_for_context,
    _trader_action_label,
    _trader_portfolio_snapshot,
    _write_debug_audit_files,
    app,
)
from kalshi_weather.edge_engine.edge import build_yes_no_candidates
from kalshi_weather.edge_engine.hold_filters import filter_candidates
from kalshi_weather.edge_engine.types import (
    CostConfig,
    MarketQuote,
    OpenOrder,
    PortfolioState,
    RiskConfig,
    Side,
    StrategyConfig,
)
from kalshi_weather.rules_engine_ext.time_profiles import ProfileInputs, select_profile
from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from kalshi_weather.trader_agent.trader_types import MarketBracket, ModelEstimate, ProbabilityBin, RiskLimits
from kalshi_weather.trader_agent.context_builder import build_context_from_inputs
from test_trader_trade_board import sample_context


def _close_only_profile():
    return select_profile(
        ProfileInputs(
            now_local=datetime(2026, 6, 30, 16, 45),
            target_date_local=datetime(2026, 6, 30, 0, 0),
        )
    )


def _audit_for_context(context=None, *, min_edge: float = 8.0):
    context = context or sample_context(min_edge_cents=3.0)
    risk_limits = RiskLimits(min_edge_cents=min_edge, max_risk_dollars_per_trade=50.0)
    cost_config = _edge_cost_config(
        risk_limits=risk_limits,
        slippage_cents=0.5,
        tail_risk_padding_cents=2.0,
        passive_improvement_cents=1,
    )
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=min_edge,
        min_no_upside_cents=8.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )
    portfolio = {"cash_value": 1000.0}
    result, rules = _rule_decision_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        risk_limits=risk_limits,
        cost_config=cost_config,
        risk_config=risk_config,
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio=portfolio),
    )
    payload = result.to_dict()
    payload.update(
        {
            "iteration": 1,
            "decision_mode": "rules",
            "strategy": "hybrid",
            "order_style": "passive",
            "rules_engine": rules,
            "approved_action": result.validation.approved_action,
            "validation": result.validation.to_dict(),
            "paper_order": None,
            "paper_execution": None,
            "paper_order_status": "no_fake_order",
            "portfolio": _trader_portfolio_snapshot(context.to_dict(), [], [], starting_cash=1000.0),
            "open_positions": [],
            "open_orders": [],
            "pending_order_executions": [],
        }
    )
    audit = _build_decision_audit(
        payload,
        race_id="audit_test",
        journal_path="audit.sqlite",
        risk_limits=risk_limits,
        risk_config=risk_config,
        cost_config=cost_config,
        starting_cash=1000.0,
        loaded_existing_portfolio=False,
        implicit_resume_warning=False,
        initial_portfolio=payload["portfolio"],
    )
    return audit, payload


def _no_cache_model_source() -> dict[str, object]:
    return {
        "model_source_mode": "fresh_recompute_each_iteration",
        "model_cache_used": False,
        "fast_model_cache_used": False,
        "noaa_cache_used": False,
        "noaa_model_mode": "full_recompute_each_iteration",
        "noaa_cache_age_seconds": None,
        "noaa_last_refresh_utc": "2026-07-01T18:00:00+00:00",
        "noaa_next_refresh_utc": None,
        "model_fetch_elapsed_seconds": 2.5,
        "noaa_fetch_elapsed_seconds": 1.5,
        "open_meteo_fetch_elapsed_seconds": 1.0,
        "force_model_recompute_every_iteration": True,
        "use_cached_models": False,
    }


def test_latest_json_reports_no_model_cache_used(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    audit["model_source"] = _no_cache_model_source()

    _write_debug_audit_files(
        audit,
        debug_output_dir=str(tmp_path),
        debug_jsonl=None,
        debug_csv=None,
    )

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["model_source"]["model_source_mode"] == "fresh_recompute_each_iteration"
    assert latest["model_source"]["model_cache_used"] is False
    assert latest["model_source"]["fast_model_cache_used"] is False
    assert latest["model_source"]["noaa_cache_used"] is False
    assert latest["model_source"]["noaa_model_mode"] == "full_recompute_each_iteration"


def test_decisions_jsonl_reports_no_model_cache_used(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    audit["model_source"] = _no_cache_model_source()
    jsonl_path = tmp_path / "decisions.jsonl"

    _write_debug_audit_files(
        audit,
        debug_output_dir=None,
        debug_jsonl=str(jsonl_path),
        debug_csv=None,
    )

    row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["model_source"]["model_cache_used"] is False
    assert row["model_source"]["fast_model_cache_used"] is False
    assert row["model_source"]["noaa_cache_used"] is False


def test_close_only_blocks_new_buy_candidates() -> None:
    context = sample_context(min_edge_cents=3.0)
    risk_limits = RiskLimits(min_edge_cents=3.0, max_risk_dollars_per_trade=50.0)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=3.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )

    _result, rules = _rule_decision_for_context(
        context,
        strategy="no-exclusion",
        decision_mode="rules",
        order_style="passive",
        risk_limits=risk_limits,
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2.0,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio={"cash_value": 1000.0}),
        profile_decision=_close_only_profile(),
    )

    buy_rows = [row for row in rules["candidate_board"] if row["action"] == "BUY"]
    assert buy_rows
    assert all(row["metadata"]["close_only_blocked_new_buy"] is True for row in buy_rows)
    assert all(row["rejection_reason"] == "close_only_new_buy_blocked" for row in buy_rows)


def test_close_only_generates_cancel_for_open_orders() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-30",
        model_estimates=[ModelEstimate("open_meteo:gfs013", 70.0)],
        probability_bins=[ProbabilityBin("70-71", 0.70, 70, 71)],
        market_brackets=[MarketBracket("KXHIGHLAX", "T70", "70-71", 70, 71, yes_bid_cents=50, yes_ask_cents=52)],
        open_orders=[
            {
                "order_id": "9",
                "created_at_utc": "2026-06-30T20:00:00+00:00",
                "status": "open",
                "action": "PLACE_FAKE_LIMIT_BUY",
                "contract_ticker": "T70",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 10,
                "limit_price_cents": 50,
                "selected_candidate_id": "T70:YES:BUY",
            }
        ],
        current_time_utc="2026-06-30T23:45:00+00:00",
    )
    risk_limits = RiskLimits(min_edge_cents=3.0, max_risk_dollars_per_trade=50.0)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=3.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )

    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2.0,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
        profile_decision=_close_only_profile(),
    )

    cancel = next(row for row in candidates if row.action.value == "CANCEL")
    assert "close_only_force_cancel_open_order" in cancel.metadata["cancel_reasons"]


def test_profile_uses_station_local_time_and_writes_debug_fields() -> None:
    context = replace(
        sample_context(),
        current_time_utc="2026-06-30T21:05:00+00:00",
        market_date="2026-06-30",
        latest_observation_time_utc="2026-06-30T21:00:00+00:00",
        observed_high_so_far_f=70.0,
    )
    decision = _profile_decision_for_context(
        context,
        portfolio={"open_pnl_value": 0.0, "total_open_risk_groups": 0},
        risk_limits=RiskLimits(),
        risk_config=RiskConfig(),
        profile_mode="auto",
        profile_config={
            "station_timezone": "America/Los_Angeles",
            "profiles": {
                "late_day_risk_manage": {"start_local": "13:30", "end_local": "16:30"},
            },
        },
        previous_profile=None,
    )

    assert decision is not None
    assert decision.active_profile == "late_day_risk_manage"
    assert decision.station_timezone == "America/Los_Angeles"
    assert decision.station_local_time == "14:05"
    assert decision.profile_start_local == "13:30"
    assert decision.profile_end_local == "16:30"
    assert "station local time 14:05" in decision.profile_reason


def test_tomorrow_profile_reason_is_target_date_based() -> None:
    context = replace(
        sample_context(),
        current_time_utc="2026-06-29T17:26:00+00:00",
        market_date="2026-06-30",
        latest_observation_time_utc=None,
        observed_high_so_far_f=None,
    )
    decision = _profile_decision_for_context(
        context,
        portfolio={"open_pnl_value": 0.0, "total_open_risk_groups": 0},
        risk_limits=RiskLimits(),
        risk_config=RiskConfig(),
        profile_mode="auto",
        profile_config={"station_timezone": "America/Los_Angeles"},
        previous_profile=None,
    )

    assert decision is not None
    assert decision.active_profile == "overnight_next_day"
    assert decision.profile_reason_code == "target_date_tomorrow"
    assert decision.target_date_relation == "tomorrow"
    assert "target date 2026-06-30 is tomorrow relative to station local date 2026-06-29" in decision.profile_reason
    assert "station local time" not in decision.profile_reason


def test_lifecycle_profile_override_controls_inner_trader_profile() -> None:
    context = replace(
        sample_context(),
        current_time_utc="2026-06-29T17:26:00+00:00",
        market_date="2026-06-30",
        latest_observation_time_utc=None,
        observed_high_so_far_f=None,
    )
    decision = _profile_decision_for_context(
        context,
        portfolio={"open_pnl_value": 0.0, "total_open_risk_groups": 0},
        risk_limits=RiskLimits(),
        risk_config=RiskConfig(),
        profile_mode="auto",
        profile_config={"station_timezone": "America/Los_Angeles"},
        previous_profile=None,
        forced_active_profile="active_nowcast",
    )

    assert decision is not None
    assert decision.active_profile == "active_nowcast"
    assert decision.profile_reason_code == "lifecycle_profile_override"
    assert decision.effective_risk_config.min_edge_cents == 8.0
    assert decision.profile_overrides_applied["auto_selected_profile"] == "overnight_next_day"


def test_profile_reason_tomorrow_uses_target_date_relation() -> None:
    test_tomorrow_profile_reason_is_target_date_based()


def test_debug_candidate_rejection_reasons_include_values() -> None:
    audit, _payload = _audit_for_context(min_edge=50.0)
    rejected = [row for row in audit["candidates"] if row["rejection_code"] == "passive_price_below_best_bid"]

    assert rejected
    assert "max acceptable price" in rejected[0]["rejection_message"]
    assert "current best bid" in rejected[0]["rejection_message"]


def test_no_99c_no_when_min_upside_8() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"72-73": 0.001},
        quotes=[MarketQuote("72-73", yes_bid_cents=1, no_ask_cents=99, yes_ask_cents=2, no_bid_cents=98)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0, tail_risk_padding_cents=0),
        risk_config=RiskConfig(
            min_edge_cents=-10,
            min_no_edge_cents=-10,
            min_no_upside_cents=8,
            max_no_bin_probability=0.20,
            max_spread_cents=100,
        ),
        strategy_config=StrategyConfig(strategy="no-exclusion", decision_mode="rules", order_style="taker"),
    )
    checked = filter_candidates(candidates, PortfolioState(cash_dollars=1000), RiskConfig(min_no_edge_cents=-10))
    no_candidate = next(row for row in checked if row.side and row.side.value == "NO")

    assert no_candidate.eligible is False
    assert no_candidate.rejection_reason == "upside_too_small"


def test_hold_explain_includes_top_rejections() -> None:
    audit, _payload = _audit_for_context(min_edge=50.0)
    text = _decision_audit_text(audit, show_rejections="top", candidate_table_limit=5)

    assert "Top rejected candidates" in text
    assert "Rejection summary" in text
    assert "lowball passive order is not actionable" in text


def test_fresh_journal_blocks_resume(tmp_path: Path) -> None:
    journal_path = tmp_path / "trader.sqlite"
    journal = SqliteTraderJournal(journal_path)
    journal.execute_paper_order(
        {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "T70",
            "side": "YES",
            "quantity": 1,
            "limit_price_cents": 10,
            "metadata": {"bracket_label": "70-71", "selected_candidate_id": "T70:YES:BUY"},
        },
        market_brackets=None,
    )

    result = CliRunner().invoke(
        app,
        [
            "trader-paper-run",
            "--journal-path",
            str(journal_path),
            "--allow-noncanonical-output-paths",
            "--fresh-journal",
            "--max-iterations",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "fresh-journal" in result.output


def test_starting_cash_ignored_warning_on_resume() -> None:
    audit, payload = _audit_for_context()
    audit = _build_decision_audit(
        payload,
        race_id="audit_test",
        journal_path="audit.sqlite",
        risk_limits=RiskLimits(),
        risk_config=RiskConfig(),
        cost_config=CostConfig(),
        starting_cash=1000.0,
        loaded_existing_portfolio=True,
        implicit_resume_warning=True,
        initial_portfolio={"cash_value": 950.0},
    )

    assert "starting_cash ignored because existing journal portfolio was loaded" in audit["warnings"]


def test_portfolio_equity_reconciliation() -> None:
    reconciliation = _portfolio_reconciliation({"cash_value": 900.0, "position_value": 120.0, "equity_value": 1020.0})

    assert reconciliation["reconciles"] is True
    assert reconciliation["difference"] == 0.0


def test_open_pnl_definition_documented() -> None:
    reconciliation = _portfolio_reconciliation({"cash_value": 900.0, "position_value": 120.0, "equity_value": 1020.0})

    assert "mark-to-market" in reconciliation["open_pnl_definition"]
    assert "resumed cost basis" in reconciliation["open_pnl_definition"]


def test_scale_in_and_cooldown_rejection_debug() -> None:
    audit, _payload = _audit_for_context()
    candidates = audit["candidates"]

    assert all("scale_in_filter_result" in row for row in candidates)
    assert all("cooldown_filter_result" in row for row in candidates)


def test_debug_json_contains_all_sections(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    written = _write_debug_audit_files(audit, debug_output_dir=str(tmp_path), debug_jsonl=None, debug_csv=None)

    assert Path(written["latest_json"]).exists()
    for key in [
        "run",
        "market",
        "models",
        "portfolio",
        "candidates",
        "selected_decision",
        "execution",
        "warnings",
    ]:
        assert key in audit


def test_candidate_table_uses_canonical_labels() -> None:
    audit, _payload = _audit_for_context()

    assert all("Will the" not in row["bracket"] for row in audit["candidates"])
    assert any(row["bracket"] == "72-73" for row in audit["candidates"])


def test_live_kalshi_sentence_labels_generate_rule_candidates() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-29",
        probability_bins=[
            ProbabilityBin("Will the **high temp in LA** be <70° on Jun 29, 2026?", 0.05, None, 69),
            ProbabilityBin("Will the **high temp in LA** be 70-71° on Jun 29, 2026?", 0.70, 70, 71),
            ProbabilityBin("Will the **high temp in LA** be 72-73° on Jun 29, 2026?", 0.25, 72, 73),
        ],
        market_brackets=[
            MarketBracket("KXHIGHLAX", "T70", "Will the **high temp in LA** be <70° on Jun 29, 2026?", None, 69, yes_bid_cents=1, no_bid_cents=98),
            MarketBracket("KXHIGHLAX", "B70.5", "Will the **high temp in LA** be 70-71° on Jun 29, 2026?", 70, 71, yes_bid_cents=50, no_bid_cents=49),
            MarketBracket("KXHIGHLAX", "B72.5", "Will the **high temp in LA** be 72-73° on Jun 29, 2026?", 72, 73, yes_bid_cents=20, no_bid_cents=79),
        ],
        risk_limits=RiskLimits(min_edge_cents=3.0, max_risk_dollars_per_trade=50),
    )
    audit, _payload = _audit_for_context(context, min_edge=3.0)

    assert audit["candidates"]
    assert {row["bracket"] for row in audit["candidates"]} >= {"<70", "70-71", "72-73"}


def test_bracket_probability_sum_warning() -> None:
    context = sample_context()
    bad_bins = [replace(row, probability=row.probability * 0.5) for row in context.probability_bins]
    audit, _payload = _audit_for_context(replace(context, probability_bins=bad_bins))

    assert any("probability sum" in warning for warning in audit["warnings"])


def test_passive_order_reserves_cash_and_exposure_in_audit() -> None:
    context = sample_context().to_dict()
    context["open_orders"] = [
        {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "T72-T73",
            "bracket_label": "72-73",
            "side": "NO",
            "quantity": 10,
            "limit_price_cents": 65,
            "status": "open",
        }
    ]
    snapshot = _trader_portfolio_snapshot(context, [], [], starting_cash=100.0)

    assert snapshot["open_order_exposure_value"] == 6.5
    assert snapshot["cash_available_value"] == 93.5


def test_passive_buy_rejects_limit_below_best_bid() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"72-73": 0.44},
        quotes=[MarketQuote("72-73", yes_bid_cents=54, yes_ask_cents=55, no_bid_cents=45, no_ask_cents=46)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8, max_passive_distance_from_bid_cents=1),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )
    checked = filter_candidates(candidates, PortfolioState(cash_dollars=1000), RiskConfig())
    yes = next(row for row in checked if row.side == Side.YES)

    assert yes.eligible is False
    assert yes.rejection_reason == "passive_price_below_best_bid"
    assert yes.metadata["current_bid_cents"] == 54
    assert yes.metadata["price_actionability"] == "rejected_below_best_bid"


def test_72_73_yes_30_rejected_when_bid_54() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"72-73": 0.405},
        quotes=[MarketQuote("72-73", yes_bid_cents=54, yes_ask_cents=55)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )
    yes = filter_candidates(candidates, PortfolioState(cash_dollars=1000), RiskConfig())[0]

    assert yes.price_cents == 30
    assert yes.rejection_reason == "passive_price_below_best_bid"


def test_lowball_watchlist_not_selected_as_trade() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"<70": 0.10},
        quotes=[MarketQuote("<70", yes_bid_cents=1, yes_ask_cents=2, no_bid_cents=98, no_ask_cents=99)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_no_edge_cents=8, allow_lowball_passive_orders=True),
        strategy_config=StrategyConfig(strategy="no-exclusion", decision_mode="rules", order_style="passive"),
    )
    checked = filter_candidates(candidates, PortfolioState(cash_dollars=1000), RiskConfig(allow_lowball_passive_orders=True))
    no = next(row for row in checked if row.side == Side.NO)

    assert no.metadata["candidate_type"] == "WATCHLIST_LIMIT"
    assert no.metadata["price_actionability"] == "lowball_watchlist"
    assert no.eligible is False


def test_passive_touch_order_allowed_when_max_acceptable_above_bid() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"70-71": 0.50},
        quotes=[MarketQuote("70-71", yes_bid_cents=37, yes_ask_cents=38)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8, max_spread_cents=5),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )
    checked = filter_candidates(candidates, PortfolioState(cash_dollars=1000), RiskConfig(min_yes_edge_cents=8, max_spread_cents=5))
    yes = checked[0]

    assert yes.price_cents == 37
    assert yes.metadata["price_actionability"] == "actionable_at_touch"
    assert yes.eligible is True


def test_taker_edge_separate_from_passive_edge() -> None:
    candidate = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"70-71": 0.50},
        quotes=[MarketQuote("70-71", yes_bid_cents=37, yes_ask_cents=40)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )[0]

    assert candidate.metadata["passive_raw_edge_cents"] > candidate.metadata["taker_raw_edge_cents"]
    assert candidate.metadata["executable_taker_price_cents"] == 40


def test_observation_missing_not_fresh() -> None:
    context = sample_context()
    data = _debug_data_freshness(context.to_dict(), RiskConfig())

    assert data["observation_available"] is False
    assert data["observation_stale"] is True
    assert data["observation_age_seconds"] is None
    assert data["observation_elimination_allowed"] is False


def test_observation_elimination_requires_available_fresh_observation() -> None:
    candidates = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"70-71": 0.01},
        quotes=[MarketQuote("70-71", yes_bid_cents=1, yes_ask_cents=2, no_bid_cents=98, no_ask_cents=99)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0, tail_risk_padding_cents=0),
        risk_config=RiskConfig(min_no_edge_cents=-10, min_no_upside_cents=0),
        strategy_config=StrategyConfig(strategy="no-exclusion", decision_mode="rules", order_style="taker"),
    )
    no = next(row for row in candidates if row.side == Side.NO)
    no = replace(no, metadata={**no.metadata, "uses_elimination": True, "observation_elimination_allowed": False})
    checked = filter_candidates([no], PortfolioState(cash_dollars=1000), RiskConfig(min_no_edge_cents=-10, min_no_upside_cents=0))

    assert checked[0].rejection_reason == "observation_elimination_not_allowed"


def test_cooldown_minutes_converts_to_seconds() -> None:
    risk_15 = _edge_risk_config(
        risk_limits=RiskLimits(same_candidate_cooldown_minutes=15),
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    risk_5 = _edge_risk_config(
        risk_limits=RiskLimits(same_candidate_cooldown_minutes=5),
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )

    assert risk_15.cooldown_seconds == 900
    assert risk_5.cooldown_seconds == 300


def test_action_label_post_vs_buy() -> None:
    payload = {
        "decision": {"action": "PLACE_FAKE_LIMIT_BUY"},
        "validation": {"valid": True},
        "paper_order": {"action": "PLACE_FAKE_LIMIT_BUY"},
        "paper_execution": {"executed": False, "status": "open", "action": "OPEN_LIMIT_ORDER"},
        "order_style": "passive",
    }

    assert _trader_action_label(payload) == "POST"


def test_open_limit_execution_not_displayed_as_buy() -> None:
    payload = {
        "decision": {"action": "PLACE_FAKE_LIMIT_BUY"},
        "validation": {"valid": True},
        "paper_order": {"action": "PLACE_FAKE_LIMIT_BUY"},
        "paper_execution": {"executed": True, "action": "BUY"},
        "order_style": "passive",
    }

    assert _trader_action_label(payload) == "FILL"


def test_max_open_orders_blocks_new_post() -> None:
    candidate = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"70-71": 0.50},
        quotes=[MarketQuote("70-71", yes_bid_cents=37, yes_ask_cents=38)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )[0]
    portfolio = PortfolioState(
        cash_dollars=1000,
        open_orders=(
            OpenOrder("old", "68-69", Side.YES, 1, 10),
        ),
    )
    checked = filter_candidates(
        [candidate],
        portfolio,
        RiskConfig(min_yes_edge_cents=8, max_open_orders=1, max_total_open_risk_groups=5),
    )[0]

    assert checked.rejection_reason == "max_open_orders_reached"


def test_max_total_open_risk_groups_blocks_new_post() -> None:
    candidate = build_yes_no_candidates(
        series="KX",
        target_date="2026-06-29",
        probabilities={"70-71": 0.50},
        quotes=[MarketQuote("70-71", yes_bid_cents=37, yes_ask_cents=38)],
        cost_config=CostConfig(include_fees=False, slippage_cents=0.5, tail_risk_padding_cents=2),
        risk_config=RiskConfig(min_yes_edge_cents=8),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="passive"),
    )[0]
    portfolio = PortfolioState(
        cash_dollars=1000,
        open_orders=(OpenOrder("old", "68-69", Side.YES, 1, 10),),
    )
    checked = filter_candidates(
        [candidate],
        portfolio,
        RiskConfig(min_yes_edge_cents=8, max_open_orders=5, max_total_open_risk_groups=1),
    )[0]

    assert checked.rejection_reason == "max_total_open_risk_groups_reached"


def test_consensus_spread_does_not_replace_full_spread() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 71.0),
            ModelEstimate("open_meteo:gfs_global", 71.2),
            ModelEstimate("noaa_herbie:hrrr", 63.0),
        ],
    )
    summary = _model_consensus_summary(context, outlier_threshold_f=3.0)

    assert summary["consensus_spread_f"] < summary["full_model_spread_f"]
    assert summary["full_model_spread_f"] >= 8
    assert summary["model_disagreement_level"] == "extreme"


def test_clustered_but_disputed_status_when_consensus_tight_full_spread_high() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 71.0),
            ModelEstimate("open_meteo:gfs_global", 71.2),
            ModelEstimate("open_meteo:gfs_seamless", 71.1),
            ModelEstimate("noaa_herbie:rap", 63.0),
        ],
    )
    summary = _model_consensus_summary(context, outlier_threshold_f=3.0)

    assert summary["model_cluster_status"] == "clustered_but_disputed"
    assert summary["clustered_but_disputed"] is True


def test_duplicate_gfs_family_not_overweighted() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 71.0),
            ModelEstimate("open_meteo:gfs_global", 71.2),
            ModelEstimate("open_meteo:gfs_seamless", 71.1),
            ModelEstimate("noaa_herbie:nbm", 69.0),
        ],
    )
    summary = _model_consensus_summary(context)

    assert summary["raw_model_count"] == 4
    assert summary["family_count"] == 2
    assert len(summary["model_families"]["gfs"]) == 3


def test_probability_floor_applied_to_non_eliminated_brackets() -> None:
    summary = {"clustered_but_disputed": False, "model_disagreement_level": "low"}
    after, debug = _apply_probability_floors(
        {"<70": 0.0, "70-71": 1.0},
        consensus=summary,
        min_non_eliminated_bin_probability=0.005,
        min_tail_probability_when_disputed=0.01,
    )

    assert debug["probability_floor_applied"] is True
    assert after["<70"] > 0
    assert abs(sum(after.values()) - 1.0) < 1e-9


def test_debug_json_contains_price_actionability_fields() -> None:
    audit, _payload = _audit_for_context()
    row = audit["candidates"][0]

    assert "current_bid_cents" in row
    assert "current_ask_cents" in row
    assert "passive_limit_price_cents" in row
    assert "price_actionability" in row


def test_debug_json_contains_consensus_probability_and_uncertainty_fields() -> None:
    audit, _payload = _audit_for_context()

    assert "full_model_spread_f" in audit["models"]
    assert "probability_floor_debug" in audit["models"]
    assert "dispersion_penalty_cents" in audit["candidates"][0]
    assert "final_tail_risk_padding_cents" in audit["candidates"][0]


def test_candidate_csv_contains_required_audit_columns(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    _write_debug_audit_files(
        audit,
        debug_output_dir=str(tmp_path),
        debug_jsonl=str(tmp_path / "decisions.jsonl"),
        debug_csv=str(tmp_path / "candidates.csv"),
    )

    with (tmp_path / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert rows
    for column in (
        "timestamp_utc",
        "contract_ticker",
        "primary_rejection_code",
        "primary_rejection_message",
        "all_rejection_reasons",
        "rejection_stage",
        "price_actionability",
        "price_actionability_reason",
        "pricing_filter_result",
        "freshness_filter_result",
        "risk_filter_result",
        "scale_in_filter_result",
        "cooldown_filter_result",
        "profile_allows_candidate",
        "profile_block_reason",
        "candidate_blocked_by_profile",
        "candidate_blocked_by_profile_reason",
        "profile_preferred_action",
        "close_only_blocked_new_buy",
        "late_day_entry_limit_reason",
        "model_disagreement_level",
        "model_confidence_level",
        "model_cluster_status",
        "consensus_center_f",
        "full_model_spread_f",
        "observation_status",
        "observation_available",
        "observation_stale",
        "observation_elimination_allowed",
        "observed_high_so_far_f",
        "deterministic_note",
    ):
        assert column in reader.fieldnames


def test_candidates_csv_includes_pricing_filter_result(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    _write_debug_audit_files(
        audit,
        debug_output_dir=str(tmp_path),
        debug_jsonl=None,
        debug_csv=str(tmp_path / "candidates.csv"),
    )

    with (tmp_path / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert "pricing_filter_result" in reader.fieldnames
        assert "price_actionability_reason" in reader.fieldnames


def test_candidates_csv_includes_rejection_reason_columns(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    _write_debug_audit_files(
        audit,
        debug_output_dir=str(tmp_path),
        debug_jsonl=None,
        debug_csv=str(tmp_path / "candidates.csv"),
    )

    with (tmp_path / "candidates.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for column in (
            "primary_rejection_code",
            "primary_rejection_message",
            "all_rejection_reasons",
            "deterministic_note",
        ):
            assert column in reader.fieldnames


def test_latest_json_contains_runtime_diagnostics(tmp_path: Path) -> None:
    audit, _payload = _audit_for_context()
    audit["runtime_diagnostics"] = {
        "iteration_started_at_utc": "2026-06-30T17:00:00+00:00",
        "iteration_ended_at_utc": "2026-06-30T17:00:01+00:00",
        "iteration_elapsed_seconds": 1.0,
    }
    audit["run"].update(audit["runtime_diagnostics"])

    _write_debug_audit_files(
        audit,
        debug_output_dir=str(tmp_path),
        debug_jsonl=str(tmp_path / "decisions.jsonl"),
        debug_csv=None,
    )
    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))

    assert latest["runtime_diagnostics"]["iteration_elapsed_seconds"] == 1.0
    assert latest["run"]["iteration_elapsed_seconds"] == 1.0


def test_passive_net_edge_uses_final_tail_padding() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 70.0),
            ModelEstimate("noaa_herbie:rap", 78.5),
        ],
    )
    audit, _payload = _audit_for_context(context)
    row = next(row for row in audit["candidates"] if row["passive_raw_edge_cents"] is not None)

    expected = (
        row["passive_raw_edge_cents"]
        - row["estimated_fee_cents"]
        - row["slippage_cents"]
        - row["final_tail_risk_padding_cents"]
    )
    assert round(row["passive_net_edge_cents"], 4) == round(expected, 4)
    assert row["passive_net_edge_cents"] == row["net_edge_cents"]


def test_lowball_rejection_sets_pricing_filter_failed() -> None:
    audit, _payload = _audit_for_context(min_edge=50.0)
    row = next(row for row in audit["candidates"] if row["rejection_code"] == "passive_price_below_best_bid")

    assert row["pricing_filter_result"] == "passive_price_below_best_bid"


def test_cancel_net_edge_null_priority_score_used() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-29",
        probability_bins=[ProbabilityBin("70-71", 0.55, 70, 71)],
        market_brackets=[MarketBracket("KXHIGHLAX", "T70", "70-71", 70, 71, yes_bid_cents=50, yes_ask_cents=52)],
        open_orders=[
            {
                "order_id": "7",
                "created_at_utc": "2026-06-29T14:00:00+00:00",
                "status": "open",
                "action": "PLACE_FAKE_LIMIT_BUY",
                "contract_ticker": "T70",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 10,
                "limit_price_cents": 40,
                "selected_candidate_id": "T70:YES:BUY",
            }
        ],
    )
    risk_limits = RiskLimits(max_passive_distance_from_bid_cents=1)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
    )
    cancel = next(row for row in candidates if row.action.value == "CANCEL")

    assert cancel.net_edge_cents is None
    assert cancel.metadata["risk_control_priority_score"] == 9999


def test_cancel_not_reported_as_simulated_fill() -> None:
    context = sample_context().to_dict()
    payload = {
        "iteration": 1,
        "context": context,
        "decision": {"action": "CANCEL_FAKE_ORDER", "selected_candidate_id": "7:CANCEL"},
        "approved_action": {"action": "CANCEL_FAKE_ORDER", "selected_candidate_id": "7:CANCEL"},
        "validation": {"valid": True, "approved_action": {"action": "CANCEL_FAKE_ORDER"}},
        "rules_engine": {"candidate_board": []},
        "paper_execution": {"executed": True, "action": "CANCEL", "order_id": "7"},
        "paper_order_status": "executed_fake_money_order",
        "portfolio": _trader_portfolio_snapshot(context, [], [], starting_cash=1000.0),
        "open_positions": [],
        "open_orders": [],
        "pending_order_executions": [],
    }
    audit = _build_decision_audit(
        payload,
        race_id="cancel_test",
        journal_path="cancel.sqlite",
        risk_limits=RiskLimits(),
        risk_config=RiskConfig(),
        cost_config=CostConfig(),
        starting_cash=1000,
        loaded_existing_portfolio=False,
        implicit_resume_warning=False,
        initial_portfolio=payload["portfolio"],
    )

    assert audit["execution"]["simulated_fill_reason"] is None
    assert "canceled fake passive order" in audit["execution"]["cancel_execution_message"]


def test_cancel_model_disagreement_requires_stored_order_snapshot() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-29",
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 70.0),
            ModelEstimate("noaa_herbie:rap", 79.0),
        ],
        probability_bins=[ProbabilityBin("70-71", 0.55, 70, 71)],
        market_brackets=[MarketBracket("KXHIGHLAX", "T70", "70-71", 70, 71, yes_bid_cents=50, yes_ask_cents=52)],
        open_orders=[
            {
                "order_id": "8",
                "created_at_utc": "2026-06-29T14:00:00+00:00",
                "status": "open",
                "action": "PLACE_FAKE_LIMIT_BUY",
                "contract_ticker": "T70",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 10,
                "limit_price_cents": 50,
                "selected_candidate_id": "T70:YES:BUY",
            }
        ],
        current_time_utc="2026-06-29T14:01:00+00:00",
    )
    risk_limits = RiskLimits(max_passive_distance_from_bid_cents=1, max_passive_order_age_minutes=15)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
    )

    assert not any(
        "cancel_model_disagreement_increased" in row.metadata.get("cancel_reasons", [])
        for row in candidates
        if row.action.value == "CANCEL"
    )


def test_no_cancel_model_disagreement_when_level_unchanged() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-29",
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 70.0),
            ModelEstimate("noaa_herbie:rap", 79.0),
        ],
        probability_bins=[ProbabilityBin("70-71", 0.55, 70, 71)],
        market_brackets=[MarketBracket("KXHIGHLAX", "T70", "70-71", 70, 71, yes_bid_cents=50, yes_ask_cents=52)],
        open_orders=[
            {
                "order_id": "9",
                "created_at_utc": "2026-06-29T14:00:00+00:00",
                "status": "open",
                "action": "PLACE_FAKE_LIMIT_BUY",
                "contract_ticker": "T70",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 10,
                "limit_price_cents": 50,
                "selected_candidate_id": "T70:YES:BUY",
                "metadata": {
                    "selected_candidate_id": "T70:YES:BUY",
                    "model_disagreement_level_at_post": "extreme",
                    "full_model_spread_f_at_post": 9.0,
                    "top_bracket_at_post": "70-71",
                },
            }
        ],
        current_time_utc="2026-06-29T14:01:00+00:00",
    )
    risk_limits = RiskLimits(max_passive_distance_from_bid_cents=1, max_passive_order_age_minutes=15)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
    )

    assert not any(
        "cancel_model_disagreement_increased" in row.metadata.get("cancel_reasons", [])
        for row in candidates
        if row.action.value == "CANCEL"
    )


def test_family_internal_conflict_detects_hrrr_rap_spread() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("noaa_herbie:hrrr", 66.79),
            ModelEstimate("noaa_herbie:rap", 72.96),
            ModelEstimate("open_meteo:gfs013", 71.0),
        ],
    )
    summary = _model_consensus_summary(context)
    hrrr_rap = summary["family_internal_metrics"]["hrrr_rap"]

    assert hrrr_rap["family_internal_conflict"] is True
    assert hrrr_rap["family_spread_f"] >= 4
    assert summary["conflicted_families"]
    assert summary["credible_internal_outliers"]


def test_family_internal_conflict_adds_uncertainty_penalty() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("noaa_herbie:hrrr", 66.79),
            ModelEstimate("noaa_herbie:rap", 72.96),
            ModelEstimate("open_meteo:gfs013", 71.0),
        ],
    )
    summary = _model_consensus_summary(context)
    penalty = _model_uncertainty_penalties(summary, base_tail_risk_padding_cents=2.0)

    assert penalty["family_internal_conflict_penalty_cents"] > 0
    assert penalty["final_tail_risk_padding_cents"] > 2.0


def _cancel_test_context(*, stored_level: str | None = None, stored_spread: float | None = None, current_spread: float = 5.5):
    metadata = {
        "selected_candidate_id": "T70:YES:BUY",
        "top_bracket_at_post": "70-71",
    }
    if stored_level is not None:
        metadata["model_disagreement_level_at_post"] = stored_level
    if stored_spread is not None:
        metadata["full_model_spread_f_at_post"] = stored_spread
    return build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-06-29",
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 70.0),
            ModelEstimate("noaa_herbie:rap", 70.0 + current_spread),
        ],
        probability_bins=[ProbabilityBin("70-71", 0.55, 70, 71)],
        market_brackets=[MarketBracket("KXHIGHLAX", "T70", "70-71", 70, 71, yes_bid_cents=50, yes_ask_cents=52)],
        open_orders=[
            {
                "order_id": "cancel-test",
                "created_at_utc": "2026-06-29T14:00:00+00:00",
                "status": "open",
                "action": "PLACE_FAKE_LIMIT_BUY",
                "contract_ticker": "T70",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 10,
                "limit_price_cents": 40,
                "selected_candidate_id": "T70:YES:BUY",
                "metadata": metadata,
            }
        ],
        current_time_utc="2026-06-29T14:01:00+00:00",
    )


def test_cancel_candidate_score_not_treated_as_edge() -> None:
    audit, _payload = _audit_for_context(_cancel_test_context())
    cancel = next(row for row in audit["candidates"] if row["candidate_type"] == "CANCEL")

    assert cancel["net_edge_cents"] is None
    assert cancel["risk_control_priority_score"] == 9999
    assert cancel["selection_priority"] == 9999
    assert cancel["candidate_score"] is None


def test_cancel_note_says_risk_control_cancel() -> None:
    audit, _payload = _audit_for_context(_cancel_test_context())
    cancel = next(row for row in audit["candidates"] if row["candidate_type"] == "CANCEL")

    assert cancel["deterministic_note"] == "risk-control cancel"


def test_buy_note_uses_final_net_edge() -> None:
    context = replace(
        sample_context(),
        model_estimates=[
            ModelEstimate("open_meteo:gfs013", 70.0),
            ModelEstimate("noaa_herbie:rap", 78.5),
        ],
    )
    _audit, payload = _audit_for_context(context)
    buy = next(
        row
        for row in payload["rules_engine"]["candidate_board"]
        if row["action"] == "BUY" and row["raw_edge_cents"] is not None and row["net_edge_cents"] is not None
    )

    assert f"raw edge {buy['raw_edge_cents']:.1f}c" in buy["note"]
    assert f"final net edge {buy['net_edge_cents']:.1f}c" in buy["note"]
    assert f"after {buy['metadata']['final_tail_risk_padding_cents']:.1f}c tail/model padding" in buy["note"]
    assert f"and {buy['slippage_cents']:.1f}c slippage" in buy["note"]


def test_cancel_model_disagreement_requires_hysteresis() -> None:
    context = _cancel_test_context(stored_level="medium", stored_spread=4.9, current_spread=5.5)
    risk_limits = RiskLimits(max_passive_distance_from_bid_cents=20, max_passive_order_age_minutes=15)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
    )

    assert not any(
        "cancel_model_disagreement_increased" in row.metadata.get("cancel_reasons", [])
        for row in candidates
        if row.action.value == "CANCEL"
    )


def test_cancel_model_disagreement_allows_medium_to_high_with_spread_increase() -> None:
    context = _cancel_test_context(stored_level="medium", stored_spread=4.4, current_spread=5.5)
    risk_limits = RiskLimits(max_passive_distance_from_bid_cents=20, max_passive_order_age_minutes=15)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=None,
        min_no_upside_cents=8,
        max_no_bin_probability=0.2,
        max_spread_cents=4,
    )
    candidates = _rule_engine_candidates_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
    )

    assert any(
        "cancel_model_disagreement_increased" in row.metadata.get("cancel_reasons", [])
        for row in candidates
        if row.action.value == "CANCEL"
    )
