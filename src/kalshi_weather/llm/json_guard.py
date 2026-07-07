from __future__ import annotations

import json
import re
from typing import Any

from kalshi_weather.advisor.decision_schema import (
    AdvisorDecision,
    AdvisorInput,
    validate_advisor_decision,
)


def parse_llm_json(text: str) -> dict[str, Any]:
    cleaned = repair_common_json_issues(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid LLM JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM JSON must be an object")
    return payload


def repair_common_json_issues(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end >= start:
        cleaned = cleaned[start : end + 1]
    cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def validate_trade_decision_json(obj: dict[str, Any]) -> AdvisorDecision:
    decision = AdvisorDecision.from_mapping(obj)
    errors = validate_advisor_decision(decision)
    if errors:
        raise ValueError("invalid LLM trade decision: " + "; ".join(errors))
    return decision


def safe_fallback_decision(
    reason: str,
    *,
    fallback_input: AdvisorInput | dict[str, Any] | None = None,
    fallback_action: str = "WAIT",
    hard_veto_flag: str = "invalid_llm_json",
) -> AdvisorDecision:
    context = fallback_input.to_dict() if isinstance(fallback_input, AdvisorInput) else dict(fallback_input or {})
    candidate = dict(context.get("candidate_trade") or {})
    model = dict(context.get("model") or context.get("model_estimate") or {})
    action = str(fallback_action or "WAIT").upper()
    if action not in {"WAIT", "BLOCK"}:
        action = "WAIT"
    side = str(candidate.get("side") or "NONE").upper()
    if side not in {"YES", "NO", "NONE"}:
        side = "NONE"
    return AdvisorDecision(
        decision=action,
        trade_type="none",
        model_key=str(model.get("model_key") or ""),
        market_ticker=str(candidate.get("market_ticker") or ""),
        bracket_label=str(candidate.get("bracket_label") or ""),
        side=side,
        confidence="low",
        trade_quality_score=0,
        recommended_size_multiplier=0.0,
        primary_reason=reason,
        supporting_reasons=["LLM advisor failed closed; no buy is allowed from fallback output."],
        risk_flags=[hard_veto_flag],
        hard_veto_flags=[hard_veto_flag],
        requires_validator_approval=True,
        should_recheck_after_minutes=1,
        human_readable_summary=reason,
    )


def decision_from_llm_text(
    text: str,
    *,
    fallback_input: AdvisorInput | dict[str, Any] | None = None,
    fallback_action: str = "WAIT",
) -> AdvisorDecision:
    try:
        return validate_trade_decision_json(parse_llm_json(text))
    except Exception as exc:  # noqa: BLE001
        return safe_fallback_decision(
            f"invalid LLM JSON: {exc}",
            fallback_input=fallback_input,
            fallback_action=fallback_action,
        )
