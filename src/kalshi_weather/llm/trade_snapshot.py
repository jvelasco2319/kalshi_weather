from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorInput
from kalshi_weather.advisor.trade_quality import score_trade_quality


def advisor_input_to_trade_snapshot(advisor_input: AdvisorInput | dict[str, Any]) -> dict[str, Any]:
    payload = advisor_input.to_dict() if isinstance(advisor_input, AdvisorInput) else dict(advisor_input)
    candidate = dict(payload.get("candidate_trade") or {})
    model = dict(payload.get("model") or {})
    quality = score_trade_quality(payload)
    return _json_safe(
        {
            "time": {
                "decision_time_utc": payload.get("decision_time_utc"),
                "decision_time_local": payload.get("decision_time_local"),
            },
            "series": payload.get("series"),
            "station": payload.get("station"),
            "target_date": payload.get("target_date"),
            "strategy_mode": payload.get("strategy_mode"),
            "race_mode": payload.get("race_mode"),
            "current_weather": payload.get("current_weather") or {},
            "model_estimate": {
                "model_key": model.get("model_key"),
                "provider": model.get("provider"),
                "estimate_high_f": model.get("estimate_high_f"),
                "top_bracket": model.get("top_bracket"),
                "top_probability": model.get("top_probability"),
                "model_data_age_seconds": model.get("model_data_age_seconds"),
            },
            "candidate_trade": candidate,
            "market_price": {
                "entry_ask": candidate.get("entry_ask"),
                "exit_bid": candidate.get("exit_bid"),
                "spread": candidate.get("spread"),
                "edge": candidate.get("edge"),
                "fee_adjusted_edge": candidate.get("fee_adjusted_edge"),
                "yes_no_side": candidate.get("side"),
            },
            "liquidity": {
                "liquidity_ok": candidate.get("liquidity_ok"),
                "exit_bid": candidate.get("exit_bid"),
                "minimum_top_book_size": (payload.get("configuration") or {}).get("minimum_top_book_size"),
            },
            "signal": {
                "signal_seen_count": candidate.get("signal_seen_count"),
                "market_confirmation": candidate.get("market_confirmation"),
                "legacy_signal_action": candidate.get("legacy_signal_action"),
                "legacy_signal_reason": candidate.get("legacy_signal_reason"),
            },
            "open_position_state": payload.get("position_state") or {},
            "risk_state": payload.get("risk_state") or {},
            "market_context": payload.get("market_context") or {},
            "recent_history": payload.get("recent_history") or {},
            "configuration": payload.get("configuration") or {},
            "deterministic_trade_quality": quality.to_dict(),
        }
    )


def build_sample_advisor_input() -> AdvisorInput:
    now = datetime.now(timezone.utc)
    return AdvisorInput.from_mapping(
        {
            "decision_time_utc": now.isoformat(),
            "decision_time_local": now.astimezone().isoformat(),
            "series": "KXHIGHLAX",
            "station": "KLAX",
            "target_date": "2026-06-25",
            "strategy_mode": "microtrade",
            "race_mode": "independent",
            "current_weather": {
                "observed_high_so_far_f": 68.0,
                "latest_observation_utc": now.isoformat(),
                "weather_data_age_seconds": 60,
            },
            "model": {
                "model_key": "current:current_weighted_blend",
                "provider": "current",
                "estimate_high_f": 71.0,
                "top_bracket": "70-71",
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
                "edge": "0.36",
                "fee_adjusted_edge": "0.36",
                "spread": "0.02",
                "signal_seen_count": 3,
                "market_confirmation": "positive",
                "liquidity_ok": True,
                "bracket_invalidated": False,
                "legacy_signal_action": "bought",
                "legacy_signal_reason": "edge clears hurdle",
            },
            "position_state": {
                "has_open_position": False,
                "current_exit_bid": None,
                "open_position_count": 0,
            },
            "risk_state": {
                "cooldown_active": False,
                "daily_loss_limit_hit": False,
                "max_positions_hit": False,
                "max_exposure_hit": False,
                "open_position_missing_bid": False,
                "live_trading_enabled": False,
            },
            "market_context": {
                "agreement_status": "HIGH",
                "model_spread_f": 1.0,
                "market_data_age_seconds": 60,
            },
            "recent_history": {
                "prior_signal_seen_count": 2,
                "recent_stop_loss_minutes_ago": None,
            },
            "configuration": {
                "advisor_min_score": 75,
                "min_score_for_buy": 75,
                "min_score_for_small_trade": 60,
                "required_signal_seen_count": 2,
                "require_exit_bid_for_entry": True,
                "max_spread_cents": "15",
                "minimum_top_book_size": "1",
                "allow_penny_contract_entries": True,
                "max_entry_price_cents": "80",
                "high_price_override_edge": "0.25",
                "strategy_mode": "microtrade",
                "live_trading_enabled": False,
            },
        }
    )


def build_sample_trade_snapshot() -> dict[str, Any]:
    return advisor_input_to_trade_snapshot(build_sample_advisor_input())


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, default=_json_default))


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)

