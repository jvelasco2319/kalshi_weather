from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class PnLAttribution:
    failure_reason: str
    trades: int
    net_pnl_cents: float
    avg_clv_cents: Optional[float]


def classify_trade_failure(row: Mapping[str, object]) -> str:
    """Classify why a trade likely won/lost.

    This is intentionally heuristic. It is meant to make journal review useful,
    not to prove causality.
    """
    rejection = row.get("rejection_reason") or row.get("failure_reason")
    if rejection:
        return str(rejection)

    net_pnl = _as_float(row.get("net_pnl_cents"))
    clv = _first_float(row, ["clv_final_cents", "clv_30m_cents", "clv_15m_cents", "clv_5m_cents"])
    net_edge = _as_float(row.get("net_edge_cents"))
    model_probability = _as_float(row.get("model_probability"))
    settled = row.get("settled_result")

    if net_pnl is None:
        return "not_settled_or_not_traded"
    if net_pnl >= 0:
        if clv is not None and clv < 0:
            return "profitable_but_negative_clv"
        return "profitable"
    if clv is not None and clv < 0:
        return "negative_clv_execution_or_model"
    if net_edge is not None and net_edge < 3:
        return "fees_spread_ate_small_edge"
    if settled == 0 and model_probability is not None:
        return "model_probability_miss"
    return "loss_unclassified"


def summarize_attribution(rows: Iterable[Mapping[str, object]]) -> List[PnLAttribution]:
    buckets: Dict[str, Dict[str, float]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "clv_sum": 0.0, "clv_count": 0})
    for row in rows:
        reason = classify_trade_failure(row)
        b = buckets[reason]
        b["trades"] += 1
        b["pnl"] += _as_float(row.get("net_pnl_cents")) or 0.0
        clv = _first_float(row, ["clv_final_cents", "clv_30m_cents", "clv_15m_cents", "clv_5m_cents"])
        if clv is not None:
            b["clv_sum"] += clv
            b["clv_count"] += 1
    out = []
    for reason, b in buckets.items():
        avg_clv = None if b["clv_count"] == 0 else b["clv_sum"] / b["clv_count"]
        out.append(PnLAttribution(reason, int(b["trades"]), b["pnl"], avg_clv))
    return sorted(out, key=lambda x: x.net_pnl_cents)


def _as_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(row: Mapping[str, object], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = _as_float(row.get(key))
        if value is not None:
            return value
    return None
