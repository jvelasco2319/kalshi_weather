from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .types import CandidateTrade


@dataclass(frozen=True)
class TradeAttribution:
    category: str
    detail: str


def classify_trade_outcome(
    *,
    candidate: Optional[CandidateTrade],
    settled_result: Optional[int],
    net_pnl_cents: Optional[float],
    clv_final_cents: Optional[float],
    hold_or_rejection_reason: Optional[str] = None,
) -> TradeAttribution:
    """Classify why a paper trade or hold won/lost.

    This is intentionally simple. Codex should adapt it into journal analysis so
    bad results become diagnosable instead of just "the bot lost money".
    """
    if candidate is None:
        return TradeAttribution("hold", hold_or_rejection_reason or "no_candidate")
    if hold_or_rejection_reason:
        return TradeAttribution("rejected", hold_or_rejection_reason)
    if settled_result is None or net_pnl_cents is None:
        return TradeAttribution("open", "not_settled")
    if net_pnl_cents > 0 and (clv_final_cents is None or clv_final_cents >= 0):
        return TradeAttribution("good_trade", "profitable_with_nonnegative_clv")
    if net_pnl_cents > 0 and clv_final_cents is not None and clv_final_cents < 0:
        return TradeAttribution("lucky_trade", "profitable_but_negative_clv")
    if net_pnl_cents <= 0 and clv_final_cents is not None and clv_final_cents > 0:
        return TradeAttribution("unlucky_or_late_variance", "negative_pnl_but_positive_clv")
    if candidate.raw_edge_cents is not None and candidate.net_edge_cents is not None and candidate.raw_edge_cents > 0 >= candidate.net_edge_cents:
        return TradeAttribution("costs_ate_edge", "fees_slippage_or_padding_removed_raw_edge")
    if settled_result == 0:
        return TradeAttribution("model_error", "contract_resolved_against_position")
    return TradeAttribution("unknown_loss", "needs_manual_review")
