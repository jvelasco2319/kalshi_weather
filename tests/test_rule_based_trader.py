from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timezone

from typer.testing import CliRunner

from kalshi_weather.cli import (
    _edge_cost_config,
    _edge_portfolio_state_from_context,
    _edge_risk_config,
    _rule_decision_for_context,
    _trader_clv_samples,
    _trader_portfolio_snapshot,
    app,
)
from kalshi_weather.trader_agent.validator import validate_decision
from kalshi_weather.trader_agent.trader_types import ModelEstimate, RiskLimits
from kalshi_weather.validation_journal import ValidationJournal
from test_trader_agent_validator import best_buy_decision
from test_trader_trade_board import sample_context


def test_rules_engine_selects_without_llm_and_preserves_rule_board() -> None:
    context = sample_context(min_edge_cents=3.0)
    risk_limits = RiskLimits(min_edge_cents=3.0, max_risk_dollars_per_trade=50.0)
    portfolio = {"cash_value": 1000.0}

    result, rules = _rule_decision_for_context(
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
        risk_config=_edge_risk_config(
            risk_limits=risk_limits,
            min_yes_edge_cents=None,
            min_no_edge_cents=3.0,
            min_no_upside_cents=1.0,
            max_no_bin_probability=0.40,
            max_spread_cents=10,
        ),
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio=portfolio),
    )

    assert result.raw_llm_output is None
    assert result.decision.action == "PLACE_FAKE_LIMIT_BUY"
    assert result.validation.valid is True
    assert rules["selected_candidate"]["net_edge_cents"] > 0
    assert rules["candidate_board"]


def test_taker_mode_uses_ask_and_immediate_fake_buy_action() -> None:
    context = sample_context(min_edge_cents=2.0)
    risk_limits = RiskLimits(min_edge_cents=2.0, max_risk_dollars_per_trade=50.0)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=2.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )

    result, rules = _rule_decision_for_context(
        context,
        strategy="no-exclusion",
        decision_mode="rules",
        order_style="taker",
        risk_limits=risk_limits,
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2.0,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio={"cash_value": 1000.0}),
    )

    selected = rules["selected_candidate"]
    assert result.raw_llm_output is None
    assert result.decision.action == "EXECUTE_FAKE_TAKER_BUY"
    assert result.validation.valid is True
    assert selected["order_type"] == "TAKER"
    assert selected["metadata"]["selected_execution_style"] == "taker"
    assert selected["metadata"]["entry_price_source"] == "ask"
    assert selected["metadata"]["eligible_edge_field"] == "taker_net_edge_cents"
    assert selected["price_cents"] == selected["metadata"]["current_ask_cents"]
    assert selected["net_edge_cents"] == selected["metadata"]["taker_net_edge_cents"]


def test_taker_mode_does_not_use_passive_price_below_best_bid_as_buy_blocker() -> None:
    context = sample_context(min_edge_cents=2.0)
    risk_limits = RiskLimits(min_edge_cents=2.0, max_risk_dollars_per_trade=50.0)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=2.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )

    result, rules = _rule_decision_for_context(
        context,
        strategy="no-exclusion",
        decision_mode="rules",
        order_style="taker",
        risk_limits=risk_limits,
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.5,
            tail_risk_padding_cents=2.0,
            passive_improvement_cents=1,
        ),
        risk_config=risk_config,
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio={"cash_value": 1000.0}),
    )

    selected = rules["selected_candidate"]
    assert result.decision.action == "EXECUTE_FAKE_TAKER_BUY"
    assert selected["eligible"] is True
    assert selected["metadata"]["price_actionability"] == "taker_ask_executable"
    assert selected["rejection_reason"] is None
    assert selected["metadata"].get("pre_rejection_reason") is None


def test_trading_algorithm_outputs_same_given_same_inputs() -> None:
    base_context = sample_context(min_edge_cents=3.0)
    fresh_source_context = replace(
        base_context,
        recent_price_trend_summary={
            "model_source": {
                "model_source_mode": "fresh_recompute_each_iteration",
                "model_cache_used": False,
                "fast_model_cache_used": False,
                "noaa_cache_used": False,
                "noaa_model_mode": "full_recompute_each_iteration",
                "force_model_recompute_every_iteration": True,
                "use_cached_models": False,
                "model_source_degraded": False,
            }
        },
    )

    base_result, base_rules = _rule_output_for_fixed_context(base_context)
    fresh_result, fresh_rules = _rule_output_for_fixed_context(fresh_source_context)

    assert fresh_result.decision.action == base_result.decision.action
    assert fresh_rules["selected_candidate"]["candidate_id"] == base_rules["selected_candidate"]["candidate_id"]
    assert _candidate_math_signature(fresh_rules) == _candidate_math_signature(base_rules)


def test_rules_engine_blocks_stale_model_metadata() -> None:
    context = replace(
        sample_context(min_edge_cents=3.0),
        current_time_utc="2026-06-26T18:00:00+00:00",
        latest_observation_time_utc="2026-06-26T17:59:00+00:00",
        model_estimates=[
            ModelEstimate(
                provider="current:current_weighted_blend",
                high_f=70.5,
                generated_at_utc="2026-06-26T16:00:00+00:00",
            )
        ],
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

    result, rules = _rule_decision_for_context(
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
    )

    assert result.decision.action == "HOLD"
    assert any(row["rejection_reason"] == "model_stale" for row in rules["candidate_board"])


def _rule_output_for_fixed_context(context):
    risk_limits = RiskLimits(min_edge_cents=3.0, max_risk_dollars_per_trade=50.0)
    risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=None,
        min_no_edge_cents=3.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.40,
        max_spread_cents=10,
    )
    return _rule_decision_for_context(
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
    )


def _candidate_math_signature(rules):
    return [
        (
            row.get("candidate_id"),
            row.get("action"),
            row.get("side"),
            row.get("bracket_label"),
            row.get("fair_value_cents"),
            row.get("raw_edge_cents"),
            row.get("passive_raw_edge_cents"),
            row.get("passive_net_edge_cents"),
            row.get("taker_net_edge_cents"),
            row.get("net_edge_cents"),
            row.get("eligible"),
            row.get("rejection_code"),
            row.get("rejection_reason"),
            row.get("pricing_filter_result"),
            row.get("risk_filter_result"),
        )
        for row in rules["candidate_board"]
    ]


def test_trader_clv_samples_use_side_specific_mid_price() -> None:
    context = sample_context().to_dict()
    fill = {
        "fill_id": "1",
        "created_at_utc": "2026-06-26T18:00:00+00:00",
        "action": "BUY",
        "contract_ticker": "T72-T73",
        "bracket_label": "72-73",
        "side": "NO",
        "price_cents": 60,
        "selected_candidate_id": "T72-T73:NO:BUY",
    }

    samples = _trader_clv_samples(
        context,
        [fill],
        now_utc=datetime(2026, 6, 26, 18, 20, tzinfo=timezone.utc),
    )

    assert samples[0]["market_mid_after_5_min"] == 65.5
    assert samples[0]["clv_15m_cents"] == 5.5
    assert samples[0]["clv_30m_cents"] is None


def test_trader_clv_samples_include_60m_and_adverse_flag() -> None:
    context = sample_context().to_dict()
    fill = {
        "fill_id": "2",
        "created_at_utc": "2026-06-26T18:00:00+00:00",
        "action": "BUY",
        "contract_ticker": "T72-T73",
        "bracket_label": "72-73",
        "side": "NO",
        "price_cents": 70,
        "selected_candidate_id": "T72-T73:NO:BUY",
    }

    samples = _trader_clv_samples(
        context,
        [fill],
        now_utc=datetime(2026, 6, 26, 19, 5, tzinfo=timezone.utc),
    )

    assert samples[0]["mark_after_60m_cents"] == 65.5
    assert samples[0]["clv_60m_cents"] == -4.5
    assert samples[0]["latest_clv_cents"] == -4.5
    assert samples[0]["adverse_selection_flag"] is True


def test_validator_preserves_better_passive_buy_limit() -> None:
    context = sample_context()
    decision = best_buy_decision(context)
    better_limit = max(1, (decision.limit_price_cents or 2) - 1)
    decision = replace(decision, limit_price_cents=better_limit)

    result = validate_decision(
        decision=decision,
        candidate_trades=context.candidate_trades,
        risk_limits=context.risk_limits,
    )

    assert result.valid is True
    assert result.approved_action["limit_price_cents"] == better_limit


def test_portfolio_snapshot_reserves_open_order_cash_and_exposure() -> None:
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
            "selected_candidate_id": "T72-T73:NO:BUY",
        }
    ]

    snapshot = _trader_portfolio_snapshot(context, [], [], starting_cash=100.0)

    assert snapshot["cash_value"] == 100.0
    assert snapshot["cash_available_value"] == 93.5
    assert snapshot["open_order_exposure_value"] == 6.5
    assert snapshot["open_exposure_value"] == 6.5
    assert snapshot["total_contracts"] == 10


def test_research_backtest_writes_required_columns(tmp_path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    output_path = tmp_path / "backtest.csv"
    payload = {
        "experiment_id": "test",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "bucket_start_utc": "2026-06-26T18:00:00+00:00",
        "generated_at_utc": "2026-06-26T18:00:00+00:00",
        "final_high": {"official_high_f": 71.0},
        "probabilities": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "bracket_label": row["label"],
                "bracket_lower_f": row["lower"],
                "bracket_upper_f": row["upper"],
                "p_yes": row["p"],
            }
            for row in [
                {"label": "<66", "lower": None, "upper": 65, "p": 0.01},
                {"label": "66-67", "lower": 66, "upper": 67, "p": 0.02},
                {"label": "68-69", "lower": 68, "upper": 69, "p": 0.10},
                {"label": "70-71", "lower": 70, "upper": 71, "p": 0.70},
                {"label": "72-73", "lower": 72, "upper": 73, "p": 0.15},
                {"label": ">73", "lower": 74, "upper": None, "p": 0.02},
            ]
        ],
        "models": [],
        "market": {
            "brackets": [
                {
                    "market_ticker": f"T{idx}",
                    "bracket_label": row["label"],
                    "bracket_lower_f": row["lower"],
                    "bracket_upper_f": row["upper"],
                    "yes_bid_cents": row["bid"],
                    "yes_ask_cents": row["ask"],
                }
                for idx, row in enumerate(
                    [
                        {"label": "<66", "lower": None, "upper": 65, "bid": 0, "ask": 1},
                        {"label": "66-67", "lower": 66, "upper": 67, "bid": 1, "ask": 2},
                        {"label": "68-69", "lower": 68, "upper": 69, "bid": 5, "ask": 6},
                        {"label": "70-71", "lower": 70, "upper": 71, "bid": 48, "ask": 50},
                        {"label": "72-73", "lower": 72, "upper": 73, "bid": 40, "ask": 42},
                        {"label": ">73", "lower": 74, "upper": None, "bid": 2, "ask": 3},
                    ]
                )
            ]
        },
        "observation": {"observations": []},
    }
    ValidationJournal(journal_path).insert_snapshot(payload)

    result = CliRunner().invoke(
        app,
        [
            "research-backtest",
            "--journal-path",
            str(journal_path),
            "--output",
            str(output_path),
            "--strategy",
            "hybrid",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = list(csv.DictReader(output_path.open(newline="", encoding="utf-8")))
    assert rows
    assert "net_edge_cents" in rows[0]
    assert "yes_bid_cents" in rows[0]
    assert "no_ask_cents" in rows[0]
