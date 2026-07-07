from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .calibration import brier_score


@dataclass(frozen=True)
class StrategyReport:
    trades: int
    net_pnl_cents: float
    avg_net_edge_cents: Optional[float]
    avg_clv_cents: Optional[float]
    brier: Optional[float]


def build_strategy_report(rows: Iterable[Mapping[str, object]]) -> StrategyReport:
    traded = [r for r in rows if bool(r.get("trade_taken_by_rule"))]
    pnl = sum(_num(r.get("net_pnl_cents")) or 0.0 for r in traded)
    edges = [_num(r.get("net_edge_cents")) for r in traded if _num(r.get("net_edge_cents")) is not None]
    clvs = [_first_num(r, ["clv_final_cents", "clv_30m_cents", "clv_15m_cents", "clv_5m_cents"]) for r in traded]
    clvs = [x for x in clvs if x is not None]
    probs = [_num(r.get("model_probability")) for r in rows if _num(r.get("model_probability")) is not None and r.get("settled_result") is not None]
    outs = [int(r.get("settled_result")) for r in rows if _num(r.get("model_probability")) is not None and r.get("settled_result") is not None]
    return StrategyReport(
        trades=len(traded),
        net_pnl_cents=pnl,
        avg_net_edge_cents=None if not edges else sum(edges) / len(edges),
        avg_clv_cents=None if not clvs else sum(clvs) / len(clvs),
        brier=None if not probs else brier_score([float(x) for x in probs], outs),
    )


def _num(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_num(row: Mapping[str, object], keys) -> Optional[float]:
    for key in keys:
        value = _num(row.get(key))
        if value is not None:
            return value
    return None
