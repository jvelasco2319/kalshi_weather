from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .decision_schema import TraderDecision
from .trader_types import RiskLimits, TradeCandidate, dataclass_to_dict

ACTION_FOR_CANDIDATE_ACTION = {
    "BUY": "PLACE_FAKE_LIMIT_BUY",
    "SELL": "PLACE_FAKE_LIMIT_SELL",
    "CLOSE": "CLOSE_FAKE_POSITION",
    "CANCEL": "CANCEL_FAKE_ORDER",
    "HOLD": "HOLD",
}


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    approved_action: dict[str, Any]
    fallback_action: str = "HOLD"
    rejection_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


def validate_decision(
    *,
    decision: TraderDecision,
    candidate_trades: list[TradeCandidate],
    risk_limits: RiskLimits,
) -> ValidationResult:
    """Validate LLM output against deterministic candidate trades and risk limits."""
    if "REAL" in decision.action.upper():
        return _reject("real-money action is forbidden")

    if decision.action == "HOLD":
        return ValidationResult(valid=True, approved_action=decision.to_dict())

    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidate_trades}
    if not decision.selected_candidate_id:
        return _reject("non-HOLD decision missing selected_candidate_id")

    candidate = candidates_by_id.get(decision.selected_candidate_id)
    if candidate is None:
        return _reject(f"unknown selected_candidate_id: {decision.selected_candidate_id}")

    expected_action = ACTION_FOR_CANDIDATE_ACTION.get(candidate.action)
    if candidate.action == "BUY" and decision.action in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY"}:
        expected_action = decision.action
    if decision.action != expected_action:
        return _reject(f"action {decision.action} does not match candidate action {candidate.action}")

    if not candidate.eligible and candidate.action not in {"CLOSE", "CANCEL"}:
        return _reject(candidate.ineligible_reason or "candidate is not eligible")

    warnings: list[str] = []
    if decision.contract_ticker != candidate.contract_ticker:
        warnings.append("decision contract_ticker differed from selected candidate; using candidate")
    if decision.bracket != candidate.bracket_label:
        warnings.append("decision bracket differed from selected candidate; using candidate")
    if decision.side != candidate.side:
        warnings.append("decision side differed from selected candidate; using candidate")

    if decision.action == "CANCEL_FAKE_ORDER":
        approved = _canonical_approved_action(decision, candidate)
        return ValidationResult(valid=True, approved_action=approved, warnings=warnings)

    if decision.max_contracts <= 0:
        return _reject("max_contracts must be positive for non-HOLD action")
    if decision.max_contracts > candidate.max_contracts:
        return _reject("max_contracts exceeds selected candidate max_contracts")
    if decision.max_contracts > risk_limits.max_contracts_per_trade:
        return _reject("max_contracts exceeds risk limit")

    if decision.action in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY"}:
        if candidate.entry_price_cents is None:
            return _reject("selected buy candidate has no entry price")
        if decision.limit_price_cents is not None and decision.limit_price_cents > candidate.entry_price_cents:
            warnings.append("buy limit was worse than candidate entry price; using candidate")
        if candidate.fee_adjusted_edge_cents < risk_limits.min_edge_cents:
            return _reject("fee-adjusted edge below min_edge_cents")

    if decision.action == "PLACE_FAKE_LIMIT_SELL":
        # Not used by the initial board, but supported for future sell candidates.
        reference_price = candidate.exit_price_cents or candidate.entry_price_cents
        if reference_price is None:
            return _reject("selected sell candidate has no reference price")
        if decision.limit_price_cents is not None and decision.limit_price_cents < reference_price:
            warnings.append("sell limit was worse than candidate reference price; using candidate")

    if decision.action == "CLOSE_FAKE_POSITION":
        if candidate.exit_price_cents is None:
            return _reject("selected close candidate has no exit price")
        if decision.limit_price_cents is not None and decision.limit_price_cents < candidate.exit_price_cents:
            warnings.append("close limit was worse than candidate exit price; using candidate")

    approved = _canonical_approved_action(decision, candidate)
    return ValidationResult(valid=True, approved_action=approved, warnings=warnings)


def _canonical_approved_action(decision: TraderDecision, candidate: TradeCandidate) -> dict[str, Any]:
    approved = decision.to_dict()
    approved["selected_candidate_id"] = candidate.candidate_id
    approved["contract_ticker"] = candidate.contract_ticker
    approved["bracket"] = candidate.bracket_label
    approved["side"] = candidate.side
    if candidate.action == "BUY":
        if decision.limit_price_cents is not None and candidate.entry_price_cents is not None:
            approved["limit_price_cents"] = min(decision.limit_price_cents, candidate.entry_price_cents)
        else:
            approved["limit_price_cents"] = candidate.entry_price_cents
    elif candidate.action in {"SELL", "CLOSE"}:
        reference = candidate.exit_price_cents or candidate.entry_price_cents
        if decision.limit_price_cents is not None and reference is not None:
            approved["limit_price_cents"] = max(decision.limit_price_cents, reference)
        else:
            approved["limit_price_cents"] = reference
    approved["estimated_edge_cents"] = candidate.fee_adjusted_edge_cents
    approved["validated_candidate"] = candidate.to_dict()
    return approved


def _reject(reason: str) -> ValidationResult:
    return ValidationResult(
        valid=False,
        approved_action=TraderDecision.hold(reason).to_dict(),
        fallback_action="HOLD",
        rejection_reason=reason,
    )
