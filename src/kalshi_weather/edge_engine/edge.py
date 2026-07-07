from __future__ import annotations

from typing import Iterable, List, Mapping, Optional

from .costs import fee_cents_per_contract
from .market_implied import normalize_market_probabilities, normalize_quote
from .types import (
    Action,
    CandidateTrade,
    CostConfig,
    MarketQuote,
    OrderType,
    RiskConfig,
    Side,
    StrategyConfig,
    canonicalize_label,
)


def _clean_id_part(label: str) -> str:
    return canonicalize_label(label).replace("<", "LT").replace(">", "GT").replace(" ", "").replace("-", "_")


def fair_value_cents(p_yes: float, side: Side) -> float:
    if p_yes < 0 or p_yes > 1:
        raise ValueError(f"p_yes must be between 0 and 1, got {p_yes}")
    return 100.0 * (p_yes if side == Side.YES else 1.0 - p_yes)


def no_probability_penalty_cents(p_yes: float, risk_config: RiskConfig) -> float:
    if risk_config.no_probability_filter_mode != "soft_penalty":
        return 0.0
    return max(
        0.0,
        (float(p_yes) - float(risk_config.no_probability_penalty_start))
        * 100.0
        * float(risk_config.no_probability_penalty_factor),
    )


def compute_candidate_edge(
    *,
    series: str,
    target_date: str,
    quote: MarketQuote,
    p_yes: float,
    side: Side,
    quantity: int,
    order_type: OrderType,
    cost_config: CostConfig,
    market_probability: Optional[float] = None,
    limit_price_cents: Optional[float] = None,
    metadata: Optional[Mapping[str, object]] = None,
) -> CandidateTrade:
    q = normalize_quote(quote)
    ask = q.ask_for(side)
    if limit_price_cents is not None:
        price = float(limit_price_cents)
    elif ask is not None:
        price = float(ask)
    else:
        base_metadata = {
            "yes_bid_cents": q.yes_bid_cents,
            "yes_ask_cents": q.yes_ask_cents,
            "no_bid_cents": q.no_bid_cents,
            "no_ask_cents": q.no_ask_cents,
            "current_bid_cents": q.bid_for(side),
            "current_ask_cents": ask,
            "touch_bid_cents": q.bid_for(side),
            "touch_ask_cents": ask,
            "executable_taker_price_cents": ask,
            "quote_ts": q.ts,
            "liquidity_score": q.liquidity_score,
        }
        if metadata:
            base_metadata.update(metadata)
        return CandidateTrade(
            candidate_id=f"{series}-{target_date}-{_clean_id_part(q.bracket_label)}-BUY_{side.value}-NO_PRICE",
            action=Action.BUY,
            side=side,
            bracket_label=q.bracket_label,
            order_type=order_type,
            quantity=quantity,
            model_probability=p_yes,
            eligible=False,
            rejection_reason="missing_executable_price",
            metadata=base_metadata,
        )
    if price <= 0 or price >= 100:
        base_metadata = {
            "yes_bid_cents": q.yes_bid_cents,
            "yes_ask_cents": q.yes_ask_cents,
            "no_bid_cents": q.no_bid_cents,
            "no_ask_cents": q.no_ask_cents,
            "current_bid_cents": q.bid_for(side),
            "current_ask_cents": ask,
            "touch_bid_cents": q.bid_for(side),
            "touch_ask_cents": ask,
            "executable_taker_price_cents": ask,
            "quote_ts": q.ts,
            "liquidity_score": q.liquidity_score,
        }
        if metadata:
            base_metadata.update(metadata)
        return CandidateTrade(
            candidate_id=f"{series}-{target_date}-{_clean_id_part(q.bracket_label)}-BUY_{side.value}-INVALID_PRICE",
            action=Action.BUY,
            side=side,
            bracket_label=q.bracket_label,
            order_type=order_type,
            quantity=quantity,
            price_cents=price,
            model_probability=p_yes,
            market_probability=market_probability,
            eligible=False,
            rejection_reason="invalid_executable_price",
            metadata=base_metadata,
        )

    fair = fair_value_cents(p_yes, side)
    raw_edge = fair - price
    fee_pc = fee_cents_per_contract(price, max(1, quantity), order_type, cost_config)
    net_edge = raw_edge - fee_pc - cost_config.slippage_cents - cost_config.tail_risk_padding_cents
    spread = q.spread_for(side)
    upside = 100.0 - price
    max_loss = quantity * price / 100.0

    candidate_id = (
        f"{series}-{target_date}-{_clean_id_part(q.bracket_label)}-"
        f"BUY_{side.value}-{order_type.value}-{int(round(price))}"
    )
    base_metadata = {
        "yes_bid_cents": q.yes_bid_cents,
        "yes_ask_cents": q.yes_ask_cents,
        "no_bid_cents": q.no_bid_cents,
        "no_ask_cents": q.no_ask_cents,
        "current_bid_cents": q.bid_for(side),
        "current_ask_cents": ask,
        "touch_bid_cents": q.bid_for(side),
        "touch_ask_cents": ask,
        "executable_taker_price_cents": ask,
        "quote_ts": q.ts,
        "liquidity_score": q.liquidity_score,
    }
    if metadata:
        base_metadata.update(metadata)

    return CandidateTrade(
        candidate_id=candidate_id,
        action=Action.BUY,
        side=side,
        bracket_label=q.bracket_label,
        order_type=order_type,
        quantity=quantity,
        price_cents=price,
        model_probability=p_yes,
        market_probability=market_probability,
        fair_value_cents=fair,
        raw_edge_cents=raw_edge,
        fee_cents_per_contract=fee_pc,
        slippage_cents=cost_config.slippage_cents,
        tail_risk_padding_cents=cost_config.tail_risk_padding_cents,
        net_edge_cents=net_edge,
        upside_cents=upside,
        spread_cents=spread,
        max_loss_dollars=max_loss,
        eligible=False,
        note=f"fair {fair:.1f}c vs price {price:.1f}c; net edge {net_edge:.1f}c",
        metadata=base_metadata,
    )


def passive_limit_price(
    *,
    bid_cents: Optional[int],
    ask_cents: Optional[int],
    fair_cents: float,
    min_edge_cents: float,
    cost_config: CostConfig,
    risk_config: RiskConfig,
) -> tuple[Optional[int], dict[str, object]]:
    """Choose a passive limit that is actionable near the current best bid.

    A passive order far below the touch is a watchlist idea, not a current edge.
    The returned metadata is used by risk filters and debug output to make that
    distinction explicit.
    """
    max_price = fair_cents - min_edge_cents - cost_config.slippage_cents - cost_config.tail_risk_padding_cents
    metadata: dict[str, object] = {
        "current_bid_cents": bid_cents,
        "current_ask_cents": ask_cents,
        "touch_bid_cents": bid_cents,
        "touch_ask_cents": ask_cents,
        "executable_taker_price_cents": ask_cents,
        "max_acceptable_price_cents": round(max_price, 4),
        "passive_limit_price_cents": None,
        "distance_below_best_bid_cents": None,
        "distance_from_ask_cents": None,
        "price_actionability": None,
        "price_actionability_reason": None,
        "candidate_selectable": True,
    }
    if bid_cents is None:
        metadata.update(
            {
                "price_actionability": "rejected_missing_bid",
                "price_actionability_reason": "rejected: current best bid missing",
                "pre_rejection_reason": "rejected_missing_bid",
            }
        )
        return None, metadata
    if ask_cents is None:
        metadata.update(
            {
                "price_actionability": "rejected_missing_ask",
                "price_actionability_reason": "rejected: current best ask missing",
                "pre_rejection_reason": "rejected_missing_ask",
            }
        )
        return None, metadata

    lowball_limit = int(max(1, min(99, round(max_price)))) if max_price >= 1 else None
    if max_price < bid_cents:
        distance = None if lowball_limit is None else max(0.0, float(bid_cents - lowball_limit))
        reason = (
            f"rejected: max acceptable price {max_price:.1f}c is below current best bid "
            f"{bid_cents}c; lowball passive order is not actionable"
        )
        metadata.update(
            {
                "passive_limit_price_cents": lowball_limit,
                "distance_below_best_bid_cents": distance,
                "distance_from_ask_cents": None if lowball_limit is None else round(float(ask_cents - lowball_limit), 4),
                "price_actionability_reason": reason,
                "pre_rejection_reason": "passive_price_below_best_bid",
            }
        )
        if risk_config.allow_lowball_passive_orders and lowball_limit is not None:
            metadata.update(
                {
                    "candidate_type": "WATCHLIST_LIMIT",
                    "candidate_selectable": False,
                    "price_actionability": "lowball_watchlist",
                }
            )
            return lowball_limit, metadata
        metadata["price_actionability"] = "rejected_below_best_bid"
        return lowball_limit, metadata

    proposed = min(float(bid_cents + cost_config.passive_improvement_cents), max_price)
    if proposed >= ask_cents:
        proposed = float(bid_cents)
        metadata["price_actionability"] = "actionable_at_touch"
    elif proposed > bid_cents:
        metadata["price_actionability"] = "actionable_inside_spread"
    else:
        metadata["price_actionability"] = "actionable_at_touch"

    limit = int(max(1, min(99, round(proposed))))
    distance = max(0.0, float(bid_cents - limit))
    metadata.update(
        {
            "passive_limit_price_cents": limit,
            "distance_below_best_bid_cents": round(distance, 4),
            "distance_from_ask_cents": round(float(ask_cents - limit), 4),
        }
    )
    max_distance = float(risk_config.max_passive_distance_from_bid_cents)
    if distance > max_distance:
        reason = (
            f"rejected: passive limit {limit}c is {distance:.1f}c below current best bid "
            f"{bid_cents}c; max allowed distance is {max_distance:.1f}c"
        )
        metadata.update(
            {
                "price_actionability_reason": reason,
                "pre_rejection_reason": "passive_limit_too_far_below_bid",
            }
        )
        if risk_config.allow_lowball_passive_orders:
            metadata.update(
                {
                    "candidate_type": "WATCHLIST_LIMIT",
                    "candidate_selectable": False,
                    "price_actionability": "lowball_watchlist",
                }
            )
        else:
            metadata["price_actionability"] = "rejected_below_best_bid"
    return limit, metadata


def _edge_at_price(
    *,
    price_cents: Optional[float],
    fair_cents: float,
    quantity: int,
    order_type: OrderType,
    cost_config: CostConfig,
) -> tuple[Optional[float], Optional[float]]:
    if price_cents is None:
        return None, None
    if float(price_cents) <= 0 or float(price_cents) >= 100:
        return None, None
    fee_pc = fee_cents_per_contract(float(price_cents), max(1, quantity), order_type, cost_config)
    raw = fair_cents - float(price_cents)
    net = raw - fee_pc - cost_config.slippage_cents - cost_config.tail_risk_padding_cents
    return round(raw, 4), round(net, 4)


def build_yes_no_candidates(
    *,
    series: str,
    target_date: str,
    probabilities: Mapping[str, float],
    quotes: Iterable[MarketQuote],
    cost_config: CostConfig,
    risk_config: RiskConfig,
    strategy_config: StrategyConfig,
) -> List[CandidateTrade]:
    quote_by_label = {canonicalize_label(q.bracket_label): normalize_quote(q) for q in quotes}
    market_probs = normalize_market_probabilities(quote_by_label.values())
    candidates: List[CandidateTrade] = []
    quantity = risk_config.max_contracts_per_trade

    for label, p_yes in probabilities.items():
        canon = canonicalize_label(label)
        quote = quote_by_label.get(canon)
        if quote is None:
            continue

        include_yes = strategy_config.strategy in {"exact-bin", "hybrid"}
        include_no = strategy_config.strategy in {"no-exclusion", "hybrid"}

        if include_yes:
            side = Side.YES
            fair = fair_value_cents(p_yes, side)
            base_required_edge = float(risk_config.min_yes_edge_cents)
            required_edge = base_required_edge
            pricing_metadata: dict[str, object]
            if strategy_config.order_style == "passive":
                bid = quote.bid_for(side)
                ask = quote.ask_for(side)
                cheap_ask_checked = bid is None and ask is not None
                cheap_ask_allowed = False
                taker_raw_for_exception, taker_net_for_exception = _edge_at_price(
                    price_cents=ask,
                    fair_cents=fair,
                    quantity=min(quantity, risk_config.cheap_ask_yes_max_contracts),
                    order_type=OrderType.TAKER,
                    cost_config=cost_config,
                )
                if (
                    risk_config.allow_cheap_ask_yes_with_missing_bid
                    and cheap_ask_checked
                    and ask is not None
                    and float(ask) <= float(risk_config.cheap_ask_yes_max_cents)
                    and taker_net_for_exception is not None
                    and taker_net_for_exception >= float(risk_config.cheap_ask_yes_min_net_edge_cents)
                    and risk_config.model_authoritative_strength != "degraded"
                ):
                    limit = float(ask)
                    order_type = OrderType.TAKER
                    quantity_for_candidate = min(quantity, risk_config.cheap_ask_yes_max_contracts)
                    cheap_ask_allowed = True
                    pricing_metadata = {
                        "current_bid_cents": bid,
                        "current_ask_cents": ask,
                        "touch_bid_cents": bid,
                        "touch_ask_cents": ask,
                        "executable_taker_price_cents": ask,
                        "price_actionability": "cheap_ask_yes_missing_bid_allowed",
                        "price_actionability_reason": "cheap ask YES missing-bid exception allowed",
                        "candidate_selectable": True,
                    }
                else:
                    quantity_for_candidate = quantity
                    limit, pricing_metadata = passive_limit_price(
                        bid_cents=bid,
                        ask_cents=ask,
                        fair_cents=fair,
                        min_edge_cents=required_edge,
                        cost_config=cost_config,
                        risk_config=risk_config,
                    )
                    order_type = OrderType.PASSIVE_LIMIT
                pricing_metadata.update(
                    {
                        "cheap_ask_yes_exception_checked": cheap_ask_checked,
                        "cheap_ask_yes_exception_allowed": cheap_ask_allowed,
                    }
                )
            else:
                ask = quote.ask_for(side)
                limit = ask
                quantity_for_candidate = quantity
                pricing_metadata = {
                    "current_bid_cents": quote.bid_for(side),
                    "current_ask_cents": ask,
                    "touch_bid_cents": quote.bid_for(side),
                    "touch_ask_cents": ask,
                    "executable_taker_price_cents": ask,
                    "entry_price_cents": ask,
                    "entry_price_source": "ask",
                    "selected_execution_style": "taker",
                    "eligible_edge_field": "taker_net_edge_cents",
                    "price_actionability": "taker_ask_executable" if ask is not None else "rejected_missing_ask",
                    "price_actionability_reason": None if ask is not None else "rejected: current best ask missing",
                    "pre_rejection_reason": None if ask is not None else "rejected_missing_ask",
                    "candidate_selectable": True,
                }
                order_type = OrderType.TAKER
            pricing_metadata.update(
                {
                    "base_required_edge_cents": base_required_edge,
                    "required_edge_cents": required_edge,
                    "no_probability_penalty_cents": 0.0,
                    "no_probability_filter_mode": risk_config.no_probability_filter_mode,
                    "absolute_no_bin_probability_cap": risk_config.absolute_no_bin_probability_cap,
                }
            )
            passive_raw, passive_net = _edge_at_price(
                price_cents=limit if limit is not None else quote.ask_for(side),
                fair_cents=fair,
                quantity=quantity_for_candidate,
                order_type=order_type,
                cost_config=cost_config,
            )
            taker_raw, taker_net = _edge_at_price(
                price_cents=quote.ask_for(side),
                fair_cents=fair,
                quantity=quantity,
                order_type=OrderType.TAKER,
                cost_config=cost_config,
            )
            pricing_metadata.update(
                {
                    "candidate_type": pricing_metadata.get("candidate_type") or "BUY_YES",
                    "passive_raw_edge_cents": passive_raw,
                    "passive_net_edge_cents": passive_net,
                    "taker_raw_edge_cents": taker_raw,
                    "taker_net_edge_cents": taker_net,
                    "selected_execution_style": "taker" if order_type == OrderType.TAKER else "passive",
                    "entry_price_source": "ask" if order_type == OrderType.TAKER else "passive_limit",
                    "entry_price_cents": quote.ask_for(side) if order_type == OrderType.TAKER else limit,
                    "eligible_edge_field": "taker_net_edge_cents" if order_type == OrderType.TAKER else "passive_net_edge_cents",
                    "fair_yes_cents": round(100.0 * float(p_yes), 4),
                    "fair_no_cents": round(100.0 * (1.0 - float(p_yes)), 4),
                    "p_model_yes": round(float(p_yes), 8),
                    "p_market_yes": market_probs.get(canon),
                    "p_used_yes": round(float(p_yes), 8),
                    "p_used_source": "model_probability",
                }
            )
            candidates.append(
                compute_candidate_edge(
                    series=series,
                    target_date=target_date,
                    quote=quote,
                    p_yes=p_yes,
                    side=side,
                    quantity=quantity_for_candidate,
                    order_type=order_type,
                    cost_config=cost_config,
                    market_probability=market_probs.get(canon),
                    limit_price_cents=limit,
                    metadata=pricing_metadata,
                )
            )

        if include_no:
            side = Side.NO
            fair = fair_value_cents(p_yes, side)
            base_required_edge = float(risk_config.min_no_edge_cents)
            no_penalty = no_probability_penalty_cents(p_yes, risk_config)
            required_edge = base_required_edge + no_penalty
            if strategy_config.order_style == "passive":
                limit, pricing_metadata = passive_limit_price(
                    bid_cents=quote.bid_for(side),
                    ask_cents=quote.ask_for(side),
                    fair_cents=fair,
                    min_edge_cents=required_edge,
                    cost_config=cost_config,
                    risk_config=risk_config,
                )
                order_type = OrderType.PASSIVE_LIMIT
            else:
                ask = quote.ask_for(side)
                limit = ask
                pricing_metadata = {
                    "current_bid_cents": quote.bid_for(side),
                    "current_ask_cents": ask,
                    "touch_bid_cents": quote.bid_for(side),
                    "touch_ask_cents": ask,
                    "executable_taker_price_cents": ask,
                    "entry_price_cents": ask,
                    "entry_price_source": "ask",
                    "selected_execution_style": "taker",
                    "eligible_edge_field": "taker_net_edge_cents",
                    "price_actionability": "taker_ask_executable" if ask is not None else "rejected_missing_ask",
                    "price_actionability_reason": None if ask is not None else "rejected: current best ask missing",
                    "pre_rejection_reason": None if ask is not None else "rejected_missing_ask",
                    "candidate_selectable": True,
                }
                order_type = OrderType.TAKER
            pricing_metadata.update(
                {
                    "base_required_edge_cents": base_required_edge,
                    "required_edge_cents": required_edge,
                    "no_probability_penalty_cents": no_penalty,
                    "no_probability_filter_mode": risk_config.no_probability_filter_mode,
                    "no_probability_penalty_start": risk_config.no_probability_penalty_start,
                    "no_probability_penalty_factor": risk_config.no_probability_penalty_factor,
                    "absolute_no_bin_probability_cap": risk_config.absolute_no_bin_probability_cap,
                    "cheap_ask_yes_exception_checked": False,
                    "cheap_ask_yes_exception_allowed": False,
                }
            )
            passive_raw, passive_net = _edge_at_price(
                price_cents=limit if limit is not None else quote.ask_for(side),
                fair_cents=fair,
                quantity=quantity,
                order_type=order_type,
                cost_config=cost_config,
            )
            taker_raw, taker_net = _edge_at_price(
                price_cents=quote.ask_for(side),
                fair_cents=fair,
                quantity=quantity,
                order_type=OrderType.TAKER,
                cost_config=cost_config,
            )
            pricing_metadata.update(
                {
                    "candidate_type": pricing_metadata.get("candidate_type") or "BUY_NO",
                    "passive_raw_edge_cents": passive_raw,
                    "passive_net_edge_cents": passive_net,
                    "taker_raw_edge_cents": taker_raw,
                    "taker_net_edge_cents": taker_net,
                    "selected_execution_style": "taker" if order_type == OrderType.TAKER else "passive",
                    "entry_price_source": "ask" if order_type == OrderType.TAKER else "passive_limit",
                    "entry_price_cents": quote.ask_for(side) if order_type == OrderType.TAKER else limit,
                    "eligible_edge_field": "taker_net_edge_cents" if order_type == OrderType.TAKER else "passive_net_edge_cents",
                    "fair_yes_cents": round(100.0 * float(p_yes), 4),
                    "fair_no_cents": round(100.0 * (1.0 - float(p_yes)), 4),
                    "p_model_yes": round(float(p_yes), 8),
                    "p_market_yes": market_probs.get(canon),
                    "p_used_yes": round(float(p_yes), 8),
                    "p_used_source": "model_probability",
                }
            )
            candidates.append(
                compute_candidate_edge(
                    series=series,
                    target_date=target_date,
                    quote=quote,
                    p_yes=p_yes,
                    side=side,
                    quantity=quantity,
                    order_type=order_type,
                    cost_config=cost_config,
                    market_probability=market_probs.get(canon),
                    limit_price_cents=limit,
                    metadata=pricing_metadata,
                )
            )

    return candidates
