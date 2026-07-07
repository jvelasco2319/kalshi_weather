from __future__ import annotations

from copy import deepcopy
from typing import Any


def clean_cancel_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(candidate)
    if cleaned.get("candidate_type") != "CANCEL":
        return cleaned
    for key in (
        "net_edge_cents",
        "raw_edge_cents",
        "passive_net_edge_cents",
        "passive_raw_edge_cents",
        "fair_value_cents",
        "market_probability",
        "model_probability",
    ):
        cleaned[key] = None
    cleaned["candidate_score"] = None
    cleaned["risk_control_priority_score"] = cleaned.get("risk_control_priority_score") or 9999
    cleaned["selection_priority"] = cleaned.get("selection_priority") or 9999
    cleaned["deterministic_note"] = "risk-control cancel"
    cleaned["pricing_filter_result"] = "not_applicable"
    cleaned["price_actionability"] = "not_applicable"
    return cleaned


def normalize_lowball_rejection(candidate: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(candidate)
    if cleaned.get("price_actionability") == "rejected_below_best_bid":
        cleaned["pricing_filter_result"] = "passive_price_below_best_bid"
        cleaned["eligible"] = False
        cleaned["selectable"] = False
    return cleaned


def format_buy_note(
    *,
    fair: float,
    limit: float,
    raw_edge: float,
    final_net_edge: float,
    tail_padding: float,
    slippage: float,
) -> str:
    return (
        f"fair {fair:.1f}c vs limit {limit:.1f}c; "
        f"raw edge {raw_edge:.1f}c; "
        f"final net edge {final_net_edge:.1f}c after "
        f"{tail_padding:.1f}c tail/model padding and {slippage:.1f}c slippage"
    )

