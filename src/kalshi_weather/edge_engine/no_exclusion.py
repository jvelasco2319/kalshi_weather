from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, List, Mapping, Optional, Sequence

from .edge import compute_candidate_edge
from .hold_filters import apply_risk_filters
from .market_implied import normalize_market_probabilities, normalize_quote
from .types import (
    Bracket,
    CandidateTrade,
    CostConfig,
    MarketQuote,
    OrderType,
    PortfolioState,
    RiskConfig,
    Side,
    canonicalize_label,
)


def generate_no_exclusion_candidates(
    *,
    series: str,
    target_date: str,
    probabilities: Mapping[str, float],
    quotes: Iterable[MarketQuote],
    brackets: Optional[Mapping[str, Bracket]],
    cost_config: CostConfig,
    risk_config: RiskConfig,
    portfolio: PortfolioState,
    observed_high_f: Optional[float] = None,
    order_type: OrderType = OrderType.TAKER,
) -> List[CandidateTrade]:
    """Generate eligible BUY NO candidates for low-probability brackets."""
    quote_by_label = {canonicalize_label(q.bracket_label): normalize_quote(q) for q in quotes}
    market_probs = normalize_market_probabilities(quote_by_label.values())
    out: List[CandidateTrade] = []

    for label, p_yes in probabilities.items():
        canon = canonicalize_label(label)
        quote = quote_by_label.get(canon)
        if quote is None:
            continue
        if p_yes > risk_config.max_no_bin_probability:
            continue
        candidate = compute_candidate_edge(
            series=series,
            target_date=target_date,
            quote=quote,
            p_yes=p_yes,
            side=Side.NO,
            quantity=risk_config.max_contracts_per_trade,
            order_type=order_type,
            cost_config=cost_config,
            market_probability=market_probs.get(canon),
        )
        bracket = brackets.get(canon) if brackets else None
        eliminated = bracket.eliminated_by_observed_high(observed_high_f) if bracket else False
        candidate = replace(candidate, metadata={**candidate.metadata, "eliminated_by_observation": eliminated})
        candidate = apply_risk_filters(candidate, portfolio, risk_config)
        if candidate.eligible:
            out.append(candidate)
    return sorted(out, key=lambda c: (c.net_edge_cents or -999.0), reverse=True)


@dataclass(frozen=True)
class NoBasket:
    labels: Sequence[str]
    cost_cents: float
    expected_payout_cents: float
    expected_edge_cents: float
    probability_of_loss: float
    gain_if_no_selected_bracket_wins_cents: float
    worst_case_loss_cents: float
    eligible: bool
    rejection_reason: Optional[str] = None


def evaluate_no_basket(
    *,
    labels: Sequence[str],
    probabilities: Mapping[str, float],
    no_ask_cents_by_label: Mapping[str, float],
    fee_cents: float = 0.0,
    slippage_cents: float = 0.0,
    tail_risk_padding_cents: float = 0.0,
    min_expected_edge_cents: float = 8.0,
    max_probability_of_loss: float = 0.25,
    min_upside_cents: float = 8.0,
) -> NoBasket:
    """Evaluate a one-contract-per-bracket BUY NO basket.

    Temperature brackets are mutually exclusive, so the basket loses on at most
    one NO leg. Probability of loss is sum(P_i for selected brackets).
    """
    canon_labels = [canonicalize_label(x) for x in labels]
    cost = sum(float(no_ask_cents_by_label[lab]) for lab in canon_labels)
    p_loss = sum(float(probabilities[lab]) for lab in canon_labels)
    n = len(canon_labels)
    expected_payout = 100.0 * (n - p_loss)
    expected_edge = expected_payout - cost - fee_cents - slippage_cents - tail_risk_padding_cents
    gain_if_right = 100.0 * n - cost
    worst_case_loss = max(0.0, cost - 100.0 * max(0, n - 1))

    if not canon_labels:
        return NoBasket(canon_labels, cost, expected_payout, expected_edge, p_loss, gain_if_right, worst_case_loss, False, "empty_basket")
    if p_loss > max_probability_of_loss:
        return NoBasket(canon_labels, cost, expected_payout, expected_edge, p_loss, gain_if_right, worst_case_loss, False, "basket_probability_too_high")
    if gain_if_right < min_upside_cents:
        return NoBasket(canon_labels, cost, expected_payout, expected_edge, p_loss, gain_if_right, worst_case_loss, False, "basket_upside_too_small")
    if expected_edge < min_expected_edge_cents:
        return NoBasket(canon_labels, cost, expected_payout, expected_edge, p_loss, gain_if_right, worst_case_loss, False, "basket_edge_below_threshold")
    return NoBasket(canon_labels, cost, expected_payout, expected_edge, p_loss, gain_if_right, worst_case_loss, True, None)
