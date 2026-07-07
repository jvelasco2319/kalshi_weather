from __future__ import annotations

from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorDecision, AdvisorInput
from kalshi_weather.advisor.risk_validator import ValidatedDecision, validate_advisor_trade


def validate_llm_trade(
    advisor_input: AdvisorInput | dict[str, Any],
    llm_decision: AdvisorDecision,
) -> dict[str, Any]:
    validated = validate_advisor_trade(advisor_input, llm_decision)
    return hard_validator_result(validated)


def hard_validator_result(validated: ValidatedDecision) -> dict[str, Any]:
    return {
        "final_action": validated.final_action,
        "llm_action": validated.advisor_decision.decision,
        "approved": validated.approved,
        "veto_reasons": validated.veto_reasons,
        "final_size": validated.adjusted_size_multiplier,
        "explanation": validated.final_reason,
    }

