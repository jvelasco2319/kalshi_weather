from __future__ import annotations

from typing import Iterable, List

from .types import CandidateTrade, PortfolioState, RiskConfig, Side


def _metadata_bool(candidate: CandidateTrade, key: str) -> bool:
    return bool(candidate.metadata.get(key, False))


def _no_required_edge_cents(candidate: CandidateTrade, risk: RiskConfig) -> float:
    base = float(risk.min_no_edge_cents)
    metadata_value = candidate.metadata.get("required_edge_cents")
    if metadata_value is not None:
        try:
            return float(metadata_value)
        except (TypeError, ValueError):
            return base
    if risk.no_probability_filter_mode != "soft_penalty" or candidate.model_probability is None:
        return base
    extra = max(
        0.0,
        (float(candidate.model_probability) - float(risk.no_probability_penalty_start))
        * 100.0
        * float(risk.no_probability_penalty_factor),
    )
    return base + extra


def apply_risk_filters(
    candidate: CandidateTrade,
    portfolio: PortfolioState,
    risk: RiskConfig,
) -> CandidateTrade:
    """Apply deterministic risk/HOLD filters to a candidate.

    This function is deliberately conservative. It treats already-resting paper
    orders as reserved exposure so the bot cannot post many passive orders that
    would overdraw cash or exposure if filled.
    """
    if candidate.action.value != "BUY":
        return candidate.accept()
    if candidate.side is None or candidate.bracket_label is None:
        return candidate.reject("invalid_candidate")
    pre_rejection = candidate.metadata.get("pre_rejection_reason")
    if pre_rejection:
        return candidate.reject(str(pre_rejection))
    if candidate.metadata.get("candidate_selectable") is False:
        return candidate.reject(str(candidate.metadata.get("price_actionability") or "candidate_not_selectable"))
    if candidate.price_cents is None or candidate.price_cents <= 0 or candidate.price_cents >= 100:
        return candidate.reject("invalid_price")
    if candidate.quantity <= 0:
        return candidate.reject("invalid_quantity")
    if candidate.quantity > risk.max_contracts_per_trade:
        return candidate.reject("quantity_limit")
    if candidate.net_edge_cents is None:
        return candidate.reject("missing_edge")

    if risk.require_fresh_market and _metadata_bool(candidate, "market_stale"):
        return candidate.reject("market_stale")
    if risk.require_fresh_model and _metadata_bool(candidate, "model_stale"):
        return candidate.reject("model_stale")
    if risk.require_fresh_observation_for_elimination and _metadata_bool(candidate, "uses_elimination") and _metadata_bool(candidate, "observation_stale"):
        return candidate.reject("observation_stale")
    if _metadata_bool(candidate, "uses_elimination") and not _metadata_bool(candidate, "observation_elimination_allowed"):
        return candidate.reject("observation_elimination_not_allowed")
    if candidate.metadata.get("model_uncertainty_block_reason"):
        return candidate.reject(str(candidate.metadata["model_uncertainty_block_reason"]))

    min_edge = risk.min_yes_edge_cents if candidate.side == Side.YES else _no_required_edge_cents(candidate, risk)
    if candidate.net_edge_cents + risk.edge_comparison_epsilon_cents < min_edge:
        return candidate.reject("edge_below_threshold")
    if candidate.spread_cents is None:
        return candidate.reject("missing_spread")
    if candidate.spread_cents > risk.max_spread_cents:
        return candidate.reject("spread_too_wide")
    if candidate.side == Side.YES and candidate.model_probability is not None and candidate.model_probability < risk.min_yes_probability:
        return candidate.reject("yes_probability_too_low")
    if candidate.side == Side.NO and candidate.model_probability is not None:
        p_yes = float(candidate.model_probability)
        if (
            risk.no_probability_filter_mode != "off"
            and p_yes >= float(risk.absolute_no_bin_probability_cap)
            and not _metadata_bool(candidate, "uses_elimination")
        ):
            return candidate.reject("absolute_no_probability_cap")
        if risk.no_probability_filter_mode == "hard" and p_yes > risk.max_no_bin_probability:
            return candidate.reject("no_probability_too_high")
    if candidate.side == Side.NO and candidate.upside_cents is not None and candidate.upside_cents < risk.min_no_upside_cents:
        return candidate.reject("upside_too_small")
    if candidate.metadata.get("model_stale"):
        return candidate.reject("model_stale")
    if candidate.metadata.get("market_stale"):
        return candidate.reject("market_stale")
    if candidate.metadata.get("observation_stale") and _metadata_bool(candidate, "uses_elimination"):
        return candidate.reject("observation_stale")

    max_loss = candidate.max_loss_dollars
    if max_loss > risk.max_risk_dollars_per_trade:
        return candidate.reject("per_trade_risk_limit")
    # Check exposure before cash so cumulative exposure bugs are surfaced clearly.
    if portfolio.total_exposure_dollars + max_loss > risk.max_total_exposure_dollars:
        return candidate.reject("exposure_limit")
    bracket_exposure = portfolio.exposure_for_bracket(candidate.bracket_label) + max_loss
    if bracket_exposure > risk.max_exposure_dollars_per_bracket:
        return candidate.reject("bracket_exposure_limit")
    if portfolio.cash_dollars - portfolio.open_order_exposure_dollars - max_loss < risk.min_cash_buffer_dollars:
        return candidate.reject("cash_limit")
    same_side_open = portfolio.has_same_side_position(candidate.bracket_label, candidate.side) or portfolio.has_same_side_open_order(candidate.bracket_label, candidate.side)
    if len(portfolio.positions) >= risk.max_open_positions and not same_side_open:
        return candidate.reject("max_positions")
    if len(portfolio.open_orders) >= risk.max_open_orders and not same_side_open:
        return candidate.reject("max_open_orders_reached")
    max_groups = risk.max_total_open_risk_groups if risk.max_total_open_risk_groups is not None else risk.max_open_positions
    candidate_group = (candidate.bracket_label, candidate.side)
    existing_groups = {
        *((p.bracket_label, p.side) for p in portfolio.positions),
        *((o.bracket_label, o.side) for o in portfolio.open_orders),
    }
    if candidate_group not in existing_groups and portfolio.total_open_risk_groups >= max_groups:
        return candidate.reject("max_total_open_risk_groups_reached")
    if not risk.allow_scale_in and same_side_open:
        return candidate.reject("scale_in_blocked")
    if candidate.candidate_id in portfolio.recent_candidate_ids:
        return candidate.reject("cooldown")
    if portfolio.has_candidate_open(candidate.candidate_id):
        return candidate.reject("order_already_open")
    return candidate.accept()


def filter_candidates(
    candidates: Iterable[CandidateTrade],
    portfolio: PortfolioState,
    risk: RiskConfig,
) -> List[CandidateTrade]:
    return [apply_risk_filters(c, portfolio, risk) for c in candidates]
