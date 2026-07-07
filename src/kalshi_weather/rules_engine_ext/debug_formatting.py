from __future__ import annotations


def format_buy_note(fair: float, limit: float, raw_edge: float, final_net_edge: float, final_padding: float, slippage: float) -> str:
    return (
        f"fair {fair:.1f}c vs limit {limit:.1f}c; "
        f"raw edge {raw_edge:.1f}c; final net edge {final_net_edge:.1f}c "
        f"after {final_padding:.1f}c tail/model padding and {slippage:.1f}c slippage"
    )


def cleanup_cancel_candidate(c: dict) -> dict:
    c = dict(c)
    c.update({
        "net_edge_cents": None,
        "passive_net_edge_cents": None,
        "raw_edge_cents": None,
        "candidate_score": None,
        "risk_control_priority_score": c.get("risk_control_priority_score", 9999),
        "deterministic_note": "risk-control cancel",
        "pricing_filter_result": "not_applicable",
    })
    return c


def edge_passes(net_edge: float, threshold: float, epsilon: float = 0.001) -> bool:
    return net_edge + epsilon >= threshold
