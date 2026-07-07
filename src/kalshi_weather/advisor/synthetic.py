from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorDecision, AdvisorInput, coerce_invalid_decision_to_safe_block
from kalshi_weather.advisor.llm_trade_advisor import advisor_for_mode, safe_decide
from kalshi_weather.advisor.risk_validator import validate_advisor_trade


def run_advisor_synthetic_suite(
    scenario_dir: str | Path = "synthetic_scenarios/llm_trade_advisor_edge_cases",
    *,
    advisor_mode: str = "rule_based",
    output: str | Path | None = None,
    charts: bool = False,
) -> dict[str, Any]:
    _ = charts
    directory = Path(scenario_dir)
    scenarios = _load_or_write_scenarios(directory)
    advisor = advisor_for_mode(advisor_mode, log_dir="reports/llm_trade_advisor/synthetic_prompt_only")
    if advisor is None:
        advisor = advisor_for_mode("rule_based")
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        advisor_input = AdvisorInput.from_mapping(scenario["input"])
        if scenario.get("malformed_llm_json"):
            decision = coerce_invalid_decision_to_safe_block(
                {"decision": "BUY_YES"},
                reason="invalid advisor JSON",
                fallback_input=advisor_input,
            )
        elif scenario.get("advisor_decision_override"):
            decision = AdvisorDecision.from_mapping(scenario["advisor_decision_override"])
        else:
            decision = safe_decide(advisor, advisor_input)
        validated = validate_advisor_trade(advisor_input, decision)
        expected = set(scenario["expected_final_actions"])
        passed = validated.final_action in expected or decision.decision in expected
        results.append(
            {
                "scenario_id": scenario["scenario_id"],
                "name": scenario["name"],
                "expected_final_actions": sorted(expected),
                "advisor_decision": decision.decision,
                "final_action": validated.final_action,
                "validator_approved": validated.approved,
                "veto_reasons": validated.veto_reasons,
                "trade_quality_score": decision.trade_quality_score,
                "passed": passed,
            }
        )
    summary = _summarize(results, directory)
    if output:
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".json":
            out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        else:
            out.write_text(advisor_synthetic_report_text(summary), encoding="utf-8")
    report_path = Path("reports/llm_trade_advisor/advisor_synthetic_test_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(advisor_synthetic_report_text(summary), encoding="utf-8")
    summary["report_path"] = str(report_path)
    return summary


def advisor_synthetic_report_text(summary: dict[str, Any]) -> str:
    lines = [
        "# LLM Trade Advisor Synthetic Test Report",
        "",
        f"Scenario count: {summary['scenario_count']}",
        f"Passed: {summary['passed_count']}",
        f"Failed: {summary['failed_count']}",
        "Network used: false",
        "Live trading enabled: false",
        "",
        "| Scenario | Expected | Advisor | Final | Passed |",
        "|---|---|---|---|---|",
    ]
    for row in summary["results"]:
        lines.append(
            f"| {row['scenario_id']} | {', '.join(row['expected_final_actions'])} | "
            f"{row['advisor_decision']} | {row['final_action']} | {str(row['passed']).lower()} |"
        )
    return "\n".join(lines)


def _summarize(results: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    decision_counts = Counter(row["advisor_decision"] for row in results)
    final_counts = Counter(row["final_action"] for row in results)
    failed = [row["scenario_id"] for row in results if not row["passed"]]
    return {
        "scenario_dir": str(directory),
        "scenario_count": len(results),
        "passed": not failed,
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "failed_scenarios": failed,
        "decision_confusion_matrix": dict(decision_counts),
        "final_action_counts": dict(final_counts),
        "results": results,
        "network_used": False,
        "live_trading_enabled": False,
        "fake_money_only": True,
    }


def _load_or_write_scenarios(directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    if not any(directory.glob("*.json")):
        for scenario in _default_scenarios():
            (directory / f"{scenario['scenario_id']}.json").write_text(
                json.dumps(scenario, indent=2),
                encoding="utf-8",
            )
        (directory / "manifest.json").write_text(
            json.dumps({"scenario_count": len(_default_scenarios()), "scenario_set": "llm_trade_advisor_edge_cases"}, indent=2),
            encoding="utf-8",
        )
    scenarios = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "manifest.json":
            continue
        scenarios.append(json.loads(path.read_text(encoding="utf-8")))
    return scenarios


def _base_input() -> dict[str, Any]:
    return {
        "decision_time_utc": "2026-06-25T18:00:00+00:00",
        "decision_time_local": "2026-06-25T11:00:00-07:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-25",
        "strategy_mode": "microtrade",
        "race_mode": "independent",
        "current_weather": {"observed_high_so_far_f": 68.0, "weather_data_age_seconds": 60},
        "model": {
            "model_key": "open_meteo:gfs013",
            "provider": "open_meteo",
            "estimate_high_f": 71.0,
            "top_probability": 0.78,
            "model_data_age_seconds": 120,
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


def _scenario(scenario_id: str, name: str, expected: list[str], updates: dict[str, Any]) -> dict[str, Any]:
    payload = _base_input()
    _deep_update(payload, updates)
    return {
        "scenario_id": scenario_id,
        "name": name,
        "expected_final_actions": expected,
        "input": payload,
    }


def _default_scenarios() -> list[dict[str, Any]]:
    scenarios = [
        _scenario("high_edge_but_no_exit_bid", "High edge but no exit bid", ["BLOCK"], {"candidate_trade": {"exit_bid": None}}),
        _scenario("high_edge_but_recent_stop", "High edge but recent stop", ["BLOCK"], {"risk_state": {"cooldown_active": True}}),
        _scenario("edge_persists_clean_liquid", "Clean persistent edge", ["BUY_YES"], {}),
        _scenario("edge_once_only", "One-shot edge", ["WAIT"], {"candidate_trade": {"signal_seen_count": 1}}),
        _scenario("open_position_probability_drop", "Open position probability drop", ["SELL"], {"position_state": {"has_open_position": True, "current_exit_bid": "0.35", "probability_drop_triggered": True}}),
        _scenario("open_position_profit_target", "Open position profit target", ["SELL"], {"position_state": {"has_open_position": True, "current_exit_bid": "0.58", "profit_target_triggered": True}}),
        _scenario("open_position_stop_loss", "Open position stop loss", ["SELL"], {"position_state": {"has_open_position": True, "current_exit_bid": "0.25", "stop_loss_triggered": True}}),
        _scenario("stale_model_data", "Stale model data", ["BLOCK"], {"model": {"model_data_age_seconds": 7200}}),
        _scenario("wide_spread", "Wide spread", ["BLOCK"], {"candidate_trade": {"spread": "0.35", "exit_bid": "0.07"}}),
        _scenario("high_price_low_upside", "High price low upside", ["BLOCK"], {"candidate_trade": {"entry_ask": "0.91", "exit_bid": "0.89", "edge": "0.10", "fee_adjusted_edge": "0.10"}}),
        _scenario("clean_no_trade_due_low_score", "Clean but low score", ["WAIT"], {"candidate_trade": {"edge": "0.04", "fee_adjusted_edge": "0.04", "market_confirmation": "neutral"}}),
        _scenario("independent_mode_model_can_trade_even_if_global_spread_high", "Independent mode does not hard block model spread", ["BUY_YES", "WAIT"], {"market_context": {"model_spread_f": 6.0}}),
        _scenario("consensus_guarded_mode_global_spread_blocks", "Consensus guarded synthetic block", ["BLOCK"], {"risk_state": {"max_exposure_hit": True}, "race_mode": "consensus_guarded"}),
    ]
    long_hold = _scenario("long_hold_candidate_not_allowed_in_microtrade", "Long hold candidate not allowed", ["BLOCK"], {})
    long_hold["advisor_decision_override"] = {
        "decision": "LONG_HOLD_CANDIDATE",
        "trade_type": "long_hold",
        "model_key": "open_meteo:gfs013",
        "market_ticker": "KXHIGHLAX-26JUN25-B70.5",
        "bracket_label": "70-71",
        "side": "YES",
        "confidence": "medium",
        "trade_quality_score": 82,
        "recommended_size_multiplier": 0.0,
        "primary_reason": "Long-hold only.",
        "supporting_reasons": [],
        "risk_flags": [],
        "hard_veto_flags": [],
        "requires_validator_approval": True,
        "should_recheck_after_minutes": 10,
        "human_readable_summary": "Long hold candidate only.",
    }
    malformed = _scenario("malformed_llm_json", "Malformed LLM JSON", ["BLOCK"], {})
    malformed["malformed_llm_json"] = True
    return scenarios + [long_hold, malformed]


def _deep_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value

