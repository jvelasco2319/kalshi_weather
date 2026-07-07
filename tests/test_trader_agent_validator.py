from dataclasses import replace

from kalshi_weather.trader_agent.agent import TraderAgent
from kalshi_weather.trader_agent.decision_schema import TraderDecision
from kalshi_weather.trader_agent.llm_client import StaticTraderLLMClient
from kalshi_weather.trader_agent.trade_board import build_trade_board
from kalshi_weather.trader_agent.validator import validate_decision
from test_trader_trade_board import sample_context


def best_buy_decision(context):
    candidate = max(
        [c for c in context.candidate_trades if c.action == "BUY" and c.eligible],
        key=lambda c: c.fee_adjusted_edge_cents,
    )
    return TraderDecision(
        action="PLACE_FAKE_LIMIT_BUY",
        selected_candidate_id=candidate.candidate_id,
        contract_ticker=candidate.contract_ticker,
        bracket=candidate.bracket_label,
        side=candidate.side,
        limit_price_cents=candidate.entry_price_cents,
        max_contracts=1,
        estimated_edge_cents=candidate.fee_adjusted_edge_cents,
        confidence="medium",
        time_horizon="scalp",
        trader_thesis="Edge.",
        why_this_trade="Best edge.",
        why_not_most_likely_bracket="Edge-ranked.",
        why_not_other_side="Lower edge.",
    )


def test_validator_accepts_valid_candidate():
    context = sample_context()
    decision = best_buy_decision(context)
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True


def test_validator_rejects_unknown_candidate():
    context = sample_context()
    decision = replace(best_buy_decision(context), selected_candidate_id="UNKNOWN")
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is False
    assert result.fallback_action == "HOLD"


def test_validator_canonicalizes_changed_side():
    context = sample_context()
    decision = replace(best_buy_decision(context), side="NO" if best_buy_decision(context).side == "YES" else "YES")
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True
    assert result.approved_action["side"] == best_buy_decision(context).side
    assert any("side" in warning for warning in result.warnings)


def test_validator_canonicalizes_changed_ticker():
    context = sample_context()
    decision = replace(best_buy_decision(context), contract_ticker="INVENTED-TICKER")
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True
    assert result.approved_action["contract_ticker"] == best_buy_decision(context).contract_ticker
    assert any("contract_ticker" in warning for warning in result.warnings)


def test_validator_canonicalizes_worse_buy_price():
    context = sample_context()
    decision = best_buy_decision(context)
    decision = replace(decision, limit_price_cents=(decision.limit_price_cents or 0) + 1)
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True
    assert result.approved_action["limit_price_cents"] == best_buy_decision(context).limit_price_cents
    assert any("worse" in warning for warning in result.warnings)


def test_validator_rejects_low_edge_trade_as_hold():
    context = sample_context()
    candidate = next(c for c in context.candidate_trades if c.action == "BUY" and not c.eligible)
    decision = TraderDecision(
        action="PLACE_FAKE_LIMIT_BUY",
        selected_candidate_id=candidate.candidate_id,
        contract_ticker=candidate.contract_ticker,
        bracket=candidate.bracket_label,
        side=candidate.side,
        limit_price_cents=candidate.entry_price_cents,
        max_contracts=1,
        estimated_edge_cents=candidate.fee_adjusted_edge_cents,
        confidence="medium",
        time_horizon="scalp",
    )
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is False
    assert result.approved_action["action"] == "HOLD"


def test_validator_rejects_real_money_action():
    context = sample_context()
    decision = replace(best_buy_decision(context), action="PLACE_REAL_ORDER")
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is False
    assert result.approved_action["action"] == "HOLD"
    assert "real-money" in (result.rejection_reason or "")


def test_validator_accepts_fake_cancel_candidate():
    context = sample_context()
    context = replace(
        context,
        open_orders=[
            {
                "order_id": "order-1",
                "contract_ticker": "T70-T71",
                "bracket_label": "70-71",
                "side": "YES",
                "quantity": 3,
                "status": "open",
            }
        ],
    )
    context = build_trade_board(context)
    candidate = next(candidate for candidate in context.candidate_trades if candidate.action == "CANCEL")
    decision = TraderDecision(
        action="CANCEL_FAKE_ORDER",
        selected_candidate_id=candidate.candidate_id,
        contract_ticker=candidate.contract_ticker,
        bracket=candidate.bracket_label,
        side=candidate.side,
        max_contracts=0,
        confidence="low",
        time_horizon="no_trade",
    )
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True


def test_invalid_json_becomes_hold():
    context = sample_context()
    result = TraderAgent(llm_client=StaticTraderLLMClient("not json")).recommend(context)
    assert result.validation.valid is False
    assert result.approved_action["action"] == "HOLD"
    assert result.decision.action == "HOLD"


def test_hold_is_valid():
    context = sample_context()
    decision = TraderDecision.hold("No clean edge.")
    result = validate_decision(decision=decision, candidate_trades=context.candidate_trades, risk_limits=context.risk_limits)
    assert result.valid is True
