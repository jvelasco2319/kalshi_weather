import json

import pytest

from kalshi_weather.trader_agent.decision_schema import DecisionParseError, TraderDecision


def test_parse_valid_decision_json():
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "action": "HOLD",
            "selected_candidate_id": None,
            "contract_ticker": None,
            "bracket": None,
            "side": None,
            "limit_price_cents": None,
            "max_contracts": 0,
            "estimated_edge_cents": 0,
            "confidence": "low",
            "time_horizon": "no_trade",
            "trader_thesis": "No trade.",
            "why_this_trade": "No valid edge.",
            "why_not_most_likely_bracket": "No valid edge.",
            "why_not_other_side": "No valid edge.",
            "exit_plan": {"invalidate_if": "No trade."},
            "risk_notes": "Fake-money only.",
            "no_trade_reason": "No edge.",
        }
    )
    decision = TraderDecision.from_json(raw)
    assert decision.action == "HOLD"


def test_invalid_json_raises_parse_error():
    with pytest.raises(DecisionParseError):
        TraderDecision.from_json("not json")
