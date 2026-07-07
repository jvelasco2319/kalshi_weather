from __future__ import annotations

from typing import Iterable

from .hold_filters import filter_candidates
from .types import CandidateTrade, Decision, PortfolioState, RiskConfig


def rank_candidate(candidate: CandidateTrade) -> tuple:
    """Sort key: risk controls first, then exits, then new entries."""
    action_priority = {
        "CANCEL": 4,
        "CLOSE": 3,
        "SELL": 3,
        "BUY": 2,
    }.get(str(candidate.action.value), 0)
    return (
        action_priority,
        candidate.net_edge_cents if candidate.net_edge_cents is not None else -9999.0,
        -candidate.max_loss_dollars,
        candidate.candidate_id,
    )


def choose_best_candidate(
    candidates: Iterable[CandidateTrade],
    *,
    portfolio: PortfolioState,
    risk_config: RiskConfig,
) -> Decision:
    checked = filter_candidates(candidates, portfolio, risk_config)
    valid = [c for c in checked if c.eligible]
    if not valid:
        # Prefer reporting the best rejection reason if available.
        if checked:
            best_rejected = sorted(checked, key=lambda c: (c.net_edge_cents or -9999.0), reverse=True)[0]
            return Decision.hold(best_rejected.rejection_reason or "no_valid_candidate")
        return Decision.hold("no_candidates")
    best = sorted(valid, key=rank_candidate, reverse=True)[0]
    return Decision(action=best.action, candidate=best, reason="best_net_edge")
