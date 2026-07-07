from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .decision_schema import TraderDecision
from .validator import ValidationResult


@dataclass(frozen=True)
class PaperOrderRequest:
    action: str
    contract_ticker: str
    side: str
    limit_price_cents: int
    quantity: int
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "contract_ticker": self.contract_ticker,
            "side": self.side,
            "limit_price_cents": self.limit_price_cents,
            "quantity": self.quantity,
            "metadata": self.metadata,
        }


def decision_to_paper_order(decision: TraderDecision, validation: ValidationResult) -> PaperOrderRequest | None:
    """Convert an approved LLM decision into a fake-money paper-broker request.

    This adapter intentionally does not call Kalshi or any real order endpoint.
    Codex should wire the returned request into the existing paper_broker module.
    """
    if not validation.valid:
        return None
    approved = validation.approved_action or decision.to_dict()
    action = str(approved.get("action") or decision.action)
    if action not in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY", "PLACE_FAKE_LIMIT_SELL", "CLOSE_FAKE_POSITION"}:
        return None
    contract_ticker = approved.get("contract_ticker")
    side = approved.get("side")
    limit_price_cents = approved.get("limit_price_cents")
    quantity = int(approved.get("max_contracts") or 0)
    if not contract_ticker or not side or limit_price_cents is None:
        return None
    if quantity <= 0:
        return None
    validated_candidate = approved.get("validated_candidate") or {}
    candidate_metadata = validated_candidate.get("metadata") if isinstance(validated_candidate, dict) else {}
    candidate_metadata = candidate_metadata if isinstance(candidate_metadata, dict) else {}
    engine_metadata = approved.get("validated_engine_candidate_metadata")
    if isinstance(engine_metadata, dict):
        candidate_metadata = {**candidate_metadata, **engine_metadata}
    posted_model_fields = {
        key: candidate_metadata.get(key)
        for key in (
            "model_disagreement_level_at_post",
            "full_model_spread_f_at_post",
            "consensus_spread_f_at_post",
            "model_cluster_status_at_post",
            "top_bracket_at_post",
            "fair_value_cents_at_post",
            "net_edge_cents_at_post",
            "active_profile",
            "raw_model_probability",
            "calibrated_model_probability",
            "market_implied_probability",
            "model_weight",
            "market_weight",
            "final_trade_probability",
            "probability_blend_reason",
            "station_lead_time_skill_score",
            "entry_thesis",
            "current_thesis",
            "position_quality",
            "position_state",
            "thesis_label",
            "thesis_direction",
            "thesis_exposure_score",
            "incremental_thesis_risk",
            "thesis_allowed",
            "thesis_rejection_reason",
            "selected_execution_style",
            "entry_price_source",
            "entry_price_cents",
            "eligible_edge_field",
            "taker_raw_edge_cents",
            "taker_net_edge_cents",
            "passive_raw_edge_cents",
            "passive_net_edge_cents",
            "p_model_yes",
            "p_market_yes",
            "p_used_yes",
            "p_used_source",
        )
        if candidate_metadata.get(key) is not None
    }

    return PaperOrderRequest(
        action=action,
        contract_ticker=str(contract_ticker),
        side=str(side),
        limit_price_cents=int(limit_price_cents),
        quantity=quantity,
        metadata={
            "decision_id": approved.get("decision_id") or decision.decision_id,
            "selected_candidate_id": approved.get("selected_candidate_id"),
            "bracket_label": approved.get("bracket"),
            "estimated_edge_cents": approved.get("estimated_edge_cents"),
            "fake_money_only": True,
            **posted_model_fields,
        },
    )
