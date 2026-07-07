from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from kalshi_weather.advisor.decision_schema import (
    AdvisorDecision,
    AdvisorInput,
    advisor_decision_from_json,
    coerce_invalid_decision_to_safe_block,
    validate_advisor_decision,
)
from kalshi_weather.advisor.llm_trade_advisor import LLMJsonAdvisor, PromptOnlyAdvisor, RuleBasedAdvisor
from kalshi_weather.advisor.risk_validator import validate_advisor_trade
from kalshi_weather.advisor.synthetic import run_advisor_synthetic_suite
from kalshi_weather.advisor.trade_quality import score_trade_quality
from kalshi_weather.cli import app
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.trading.model_race import ModelRaceConfig, run_model_race_once


def _advisor_input(**updates) -> AdvisorInput:
    payload = {
        "decision_time_utc": "2026-06-25T18:00:00+00:00",
        "decision_time_local": "2026-06-25T11:00:00-07:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-25",
        "strategy_mode": "microtrade",
        "race_mode": "independent",
        "current_weather": {"observed_high_so_far_f": 68.0, "weather_data_age_seconds": 60},
        "model": {
            "model_key": "current:current_weighted_blend",
            "provider": "current",
            "estimate_high_f": 71.0,
            "top_probability": 0.78,
            "model_data_age_seconds": 60,
        },
        "candidate_trade": {
            "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
            "bracket_label": "70-71",
            "bracket_lower_f": 70.0,
            "bracket_upper_f": 71.0,
            "bracket_type": "range",
            "side": "YES",
            "model_probability": 0.78,
            "calibrated_probability": 0.78,
            "entry_ask": "0.42",
            "exit_bid": "0.40",
            "edge": "0.24",
            "fee_adjusted_edge": "0.24",
            "spread": "0.02",
            "signal_seen_count": 3,
            "market_confirmation": "positive",
            "liquidity_ok": True,
            "bracket_invalidated": False,
        },
        "position_state": {"has_open_position": False, "current_exit_bid": None},
        "risk_state": {
            "cooldown_active": False,
            "daily_loss_limit_hit": False,
            "max_positions_hit": False,
            "max_exposure_hit": False,
            "live_trading_enabled": False,
        },
        "market_context": {"market_data_age_seconds": 60, "model_spread_f": 1.0},
        "recent_history": {},
        "configuration": {
            "advisor_min_score": 75,
            "min_score_for_buy": 75,
            "min_score_for_small_trade": 60,
            "required_signal_seen_count": 2,
            "require_exit_bid_for_entry": True,
            "max_spread_cents": "15",
            "allow_penny_contract_entries": True,
            "max_entry_price_cents": "80",
            "high_price_override_edge": "0.25",
            "live_trading_enabled": False,
        },
    }
    _deep_update(payload, updates)
    return AdvisorInput.from_mapping(payload)


def _decision(**updates) -> AdvisorDecision:
    payload = {
        "decision": "BUY_YES",
        "trade_type": "microtrade",
        "model_key": "current:current_weighted_blend",
        "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
        "bracket_label": "70-71",
        "side": "YES",
        "confidence": "high",
        "trade_quality_score": 82,
        "recommended_size_multiplier": 0.75,
        "primary_reason": "Confirmed edge.",
        "supporting_reasons": ["Signal persisted."],
        "risk_flags": [],
        "hard_veto_flags": [],
        "requires_validator_approval": True,
        "should_recheck_after_minutes": 1,
        "human_readable_summary": "Buy if validator approves.",
    }
    payload.update(updates)
    return AdvisorDecision.from_mapping(payload)


def _deep_update(target: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _model_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at_utc": now,
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-25",
        "observed_high_so_far_f": 68.0,
        "latest_observation_utc": now,
        "current_production_estimate_f": 71.0,
        "markets_count": 1,
        "bracket_count": 1,
        "estimates": [
            {
                "asof_utc": now,
                "station": "KLAX",
                "market_date": "2026-06-25",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "model_name": "current_weighted_blend",
                "model_family": "current",
                "observed_high_so_far_f": 68.0,
                "future_high_f": 71.0,
                "settlement_high_estimate_f": 71.0,
                "successful": True,
            }
        ],
        "probabilities": [
            {
                "asof_utc": now,
                "station": "KLAX",
                "market_date": "2026-06-25",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "bracket_type": "range",
                "p_yes": 0.78,
                "yes_bid": Decimal("0.40"),
                "yes_ask": Decimal("0.42"),
                "no_bid": Decimal("0.20"),
                "no_ask": Decimal("0.80"),
                "yes_edge": Decimal("0.36"),
                "no_edge": Decimal("-0.58"),
            }
        ],
    }


def test_advisor_decision_schema_accepts_valid_json() -> None:
    parsed = advisor_decision_from_json(_decision().to_json())
    assert parsed.decision == "BUY_YES"
    assert validate_advisor_decision(parsed) == []


def test_advisor_decision_schema_coerces_invalid_json_to_block() -> None:
    block = coerce_invalid_decision_to_safe_block({"decision": "BUY_REAL"}, fallback_input=_advisor_input())
    assert block.decision == "BLOCK"
    assert "invalid_advisor_output" in block.hard_veto_flags


def test_rule_based_advisor_blocks_missing_exit_bid() -> None:
    advisor_input = _advisor_input(candidate_trade={"exit_bid": None})
    decision = RuleBasedAdvisor().decide(advisor_input)
    assert decision.decision == "BLOCK"
    assert "missing_exit_bid" in decision.hard_veto_flags


def test_rule_based_advisor_blocks_recent_stop_cooldown() -> None:
    advisor_input = _advisor_input(risk_state={"cooldown_active": True})
    decision = RuleBasedAdvisor().decide(advisor_input)
    assert decision.decision == "BLOCK"
    assert "cooldown_active" in decision.hard_veto_flags


def test_rule_based_advisor_waits_on_one_shot_signal() -> None:
    advisor_input = _advisor_input(candidate_trade={"signal_seen_count": 1})
    assert RuleBasedAdvisor().decide(advisor_input).decision == "WAIT"


def test_rule_based_advisor_buys_clean_confirmed_edge() -> None:
    advisor_input = _advisor_input()
    decision = RuleBasedAdvisor().decide(advisor_input)
    assert decision.decision == "BUY_YES"
    assert decision.trade_quality_score >= 75


def test_risk_validator_vetoes_unsafe_llm_buy() -> None:
    advisor_input = _advisor_input(candidate_trade={"exit_bid": None})
    result = validate_advisor_trade(advisor_input, _decision())
    assert not result.approved
    assert result.final_action == "BLOCK"
    assert "missing_exit_bid" in result.veto_reasons


def test_risk_validator_approves_safe_sell() -> None:
    advisor_input = _advisor_input(position_state={"has_open_position": True, "current_exit_bid": "0.50"})
    result = validate_advisor_trade(advisor_input, _decision(decision="SELL", recommended_size_multiplier=0.0))
    assert result.approved
    assert result.final_action == "SELL"


def test_trade_quality_score_components_behave() -> None:
    clean = score_trade_quality(_advisor_input())
    missing_bid = score_trade_quality(_advisor_input(candidate_trade={"exit_bid": None}))
    assert clean.score > missing_bid.score
    assert "missing_exit_bid" in missing_bid.hard_veto_flags


def test_paper_model_race_with_advisor_off_behaves_as_before(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "race.sqlite", tmp_path / "snapshots")
    result = run_model_race_once(store, _model_payload(), ModelRaceConfig(force_flat_time_local="23:59"))
    assert result["scoreboard"][0]["action"] == "bought"
    assert not store.load_advisor_decisions()


def test_paper_model_race_rule_based_advisor_logs_decisions(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "race.sqlite", tmp_path / "snapshots")
    config = ModelRaceConfig(
        force_flat_time_local="23:59",
        advisor_mode="rule_based",
        advisor_min_score=75,
    )
    result = run_model_race_once(store, _model_payload(), config)
    assert result["scoreboard"][0]["advisor_decision"] in {"WAIT", "BUY_YES", "BLOCK"}
    assert store.load_advisor_decisions()


def test_advisor_prompt_file_exists() -> None:
    assert Path("prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md").exists()


def test_prompt_only_advisor_writes_artifact(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("prompt", encoding="utf-8")
    decision = PromptOnlyAdvisor(prompt_path=prompt, log_dir=tmp_path / "logs").decide(_advisor_input())
    assert decision.decision == "WAIT"
    assert list((tmp_path / "logs").glob("prompt_only_*.json"))


def test_advisor_synthetic_suite_runs_offline(tmp_path: Path) -> None:
    summary = run_advisor_synthetic_suite(tmp_path / "scenarios")
    assert summary["scenario_count"] == 15
    assert summary["passed"]


def test_malformed_llm_json_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "provider.json"
    response = tmp_path / "response.json"
    response.write_text('{"decision": "BUY_YES"}', encoding="utf-8")
    config.write_text(json.dumps({"response_json_path": str(response)}), encoding="utf-8")
    decision = LLMJsonAdvisor(provider_config=config).decide(_advisor_input())
    assert decision.decision == "BLOCK"


def test_advisor_cli_commands_exist() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in [
        "advisor-synthetic-test",
        "advisor-dry-run",
        "advisor-decision-report",
        "advisor-export-training-examples",
    ]:
        assert command in result.output


def test_advisor_storage_summary_and_export(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "advisor.sqlite", tmp_path / "snapshots")
    decision = _decision()
    validated = validate_advisor_trade(_advisor_input(), decision)
    store.save_advisor_decision(
        {
            "race_id": "r1",
            "advisor_mode": "rule_based",
            "provider": "rule_based",
            "model_key": decision.model_key,
            "market_ticker": decision.market_ticker,
            "bracket_label": decision.bracket_label,
            "side": decision.side,
            "strategy_mode": "microtrade",
            "trade_quality_score": decision.trade_quality_score,
            "advisor_decision": decision.decision,
            "validator_approved": validated.approved,
            "final_action": validated.final_action,
            "primary_reason": decision.primary_reason,
            "risk_flags": decision.risk_flags,
            "hard_veto_flags": decision.hard_veto_flags,
            "veto_reasons": validated.veto_reasons,
            "input": _advisor_input().to_dict(),
            "output": decision.to_dict(),
            "final": validated.to_dict(),
        }
    )
    summary = store.advisor_decision_summary("r1")
    assert summary["decision_count"] == 1
    assert summary["advisor_decision_counts"]["BUY_YES"] == 1


def test_no_advisor_requires_external_api() -> None:
    decision = LLMJsonAdvisor(provider_config=None).decide(_advisor_input())
    assert decision.decision == "BLOCK"
    assert "provider unavailable" in decision.primary_reason

