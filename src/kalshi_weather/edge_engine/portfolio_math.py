from __future__ import annotations

from dataclasses import replace

from .types import CandidateTrade, PortfolioState, Position, canonicalize_label


def cash_required_dollars(candidate: CandidateTrade) -> float:
    if candidate.price_cents is None:
        return 0.0
    return candidate.quantity * candidate.price_cents / 100.0


def apply_fake_buy_fill(portfolio: PortfolioState, candidate: CandidateTrade) -> PortfolioState:
    """Return updated paper portfolio after a fake BUY fill.

    This helper deliberately performs no real order placement. It is only for
    paper simulation/backtests and should be called after risk filters approve
    the candidate.
    """
    if candidate.side is None or candidate.bracket_label is None or candidate.price_cents is None:
        raise ValueError("candidate must have side, bracket_label, and price_cents")
    if candidate.quantity <= 0:
        raise ValueError("candidate quantity must be positive")

    cost = cash_required_dollars(candidate)
    if portfolio.cash_dollars < cost:
        raise ValueError("paper portfolio cash would go negative")

    canon = canonicalize_label(candidate.bracket_label)
    updated = []
    merged = False
    for pos in portfolio.positions:
        if canonicalize_label(pos.bracket_label) == canon and pos.side == candidate.side:
            new_contracts = pos.contracts + candidate.quantity
            avg = ((pos.contracts * pos.avg_price_cents) + (candidate.quantity * candidate.price_cents)) / new_contracts
            updated.append(Position(pos.bracket_label, pos.side, new_contracts, avg))
            merged = True
        else:
            updated.append(pos)
    if not merged:
        updated.append(Position(canon, candidate.side, candidate.quantity, float(candidate.price_cents)))

    return replace(
        portfolio,
        cash_dollars=portfolio.cash_dollars - cost,
        positions=tuple(updated),
        recent_candidate_ids=tuple(list(portfolio.recent_candidate_ids) + [candidate.candidate_id]),
    )


def mark_order_open(portfolio: PortfolioState, candidate_id: str) -> PortfolioState:
    if candidate_id in portfolio.open_candidate_ids:
        return portfolio
    return replace(portfolio, open_candidate_ids=tuple(list(portfolio.open_candidate_ids) + [candidate_id]))


def mark_order_closed(portfolio: PortfolioState, candidate_id: str) -> PortfolioState:
    return replace(portfolio, open_candidate_ids=tuple(x for x in portfolio.open_candidate_ids if x != candidate_id))
