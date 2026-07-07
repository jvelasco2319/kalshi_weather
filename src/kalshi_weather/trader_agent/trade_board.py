from __future__ import annotations

from dataclasses import replace
from math import floor
from typing import Iterable

from .trader_types import (
    FakePosition,
    LiquidityScore,
    MarketBracket,
    ProbabilityBin,
    RiskLimits,
    Side,
    TradeCandidate,
    TraderContext,
)


def _label_key(label: str) -> str:
    return label.strip().lower().replace(" ", "").replace("°", "").replace("+", "plus")


def _probability_map(probability_bins: Iterable[ProbabilityBin]) -> dict[str, ProbabilityBin]:
    return {_label_key(pb.bracket_label): pb for pb in probability_bins}


def _find_probability(bracket: MarketBracket, probabilities: dict[str, ProbabilityBin]) -> ProbabilityBin | None:
    direct = probabilities.get(_label_key(bracket.bracket_label))
    if direct is not None:
        return direct
    # Fallback by bounds when labels differ between model and Kalshi formatting.
    for candidate in probabilities.values():
        if candidate.lower_f == bracket.lower_f and candidate.upper_f == bracket.upper_f:
            return candidate
    return None


def _fee_cents_per_contract(price_cents: int, risk_limits: RiskLimits) -> float:
    price_dollars = price_cents / 100.0
    return 100.0 * risk_limits.fee_rate() * price_dollars * (1.0 - price_dollars)


def _spread_cents(bid_cents: int | None, ask_cents: int | None) -> float | None:
    if bid_cents is None or ask_cents is None:
        return None
    return max(0.0, float(ask_cents - bid_cents))


def _liquidity_score(bracket: MarketBracket, spread: float | None) -> LiquidityScore:
    volume = bracket.volume or 0
    open_interest = bracket.open_interest or 0
    if spread is not None and spread <= 2 and (volume >= 500 or open_interest >= 500):
        return "high"
    if spread is not None and spread <= 5 and (volume >= 50 or open_interest >= 50):
        return "medium"
    return "low"


def _max_contracts_for_buy(price_cents: int | None, risk_limits: RiskLimits) -> int:
    if price_cents is None or price_cents <= 0:
        return 0
    max_by_risk = floor(risk_limits.max_risk_dollars_per_trade / (price_cents / 100.0))
    return max(0, min(risk_limits.max_contracts_per_trade, max_by_risk))


def _ineligible_reason_for_buy(
    *,
    price_cents: int | None,
    fee_adjusted_edge_cents: float,
    max_contracts: int,
    risk_limits: RiskLimits,
    bracket: MarketBracket,
) -> str | None:
    if price_cents is None:
        return "missing entry price"
    if max_contracts <= 0:
        return "position size would be zero under risk limits"
    if (bracket.volume or 0) < risk_limits.min_volume:
        return f"volume below min_volume={risk_limits.min_volume}"
    if fee_adjusted_edge_cents < risk_limits.min_edge_cents:
        return f"fee-adjusted edge {fee_adjusted_edge_cents:.2f}c below min_edge {risk_limits.min_edge_cents:.2f}c"
    return None


def _candidate_id(contract_ticker: str | None, side: Side | None, action: str) -> str:
    if action == "HOLD":
        return "HOLD"
    return f"{contract_ticker}:{side}:{action}"


class TradeBoardBuilder:
    """Build all trade candidates the LLM trader may choose from."""

    def build(self, context: TraderContext) -> list[TradeCandidate]:
        probabilities = _probability_map(context.probability_bins)
        candidates: list[TradeCandidate] = []

        for bracket in context.market_brackets:
            probability_bin = _find_probability(bracket, probabilities)
            if probability_bin is None:
                candidates.extend(self._missing_probability_candidates(bracket))
                continue

            p_yes = probability_bin.probability
            candidates.append(self._buy_candidate(context.risk_limits, bracket, "YES", p_yes))
            candidates.append(self._buy_candidate(context.risk_limits, bracket, "NO", 1.0 - p_yes))

        for position in context.positions:
            close_candidate = self._close_candidate(position, context.market_brackets, probabilities, context.risk_limits)
            candidates.append(close_candidate)

        for order in context.open_orders:
            candidates.append(self._cancel_candidate(order))

        candidates.append(
            TradeCandidate(
                candidate_id="HOLD",
                contract_ticker=None,
                bracket_label=None,
                side=None,
                action="HOLD",
                eligible=True,
                notes="No trade. Use when edge, liquidity, or forecast confidence is not good enough.",
            )
        )
        return candidates

    def _missing_probability_candidates(self, bracket: MarketBracket) -> list[TradeCandidate]:
        return [
            TradeCandidate(
                candidate_id=_candidate_id(bracket.contract_ticker, "YES", "BUY"),
                contract_ticker=bracket.contract_ticker,
                bracket_label=bracket.bracket_label,
                side="YES",
                action="BUY",
                eligible=False,
                ineligible_reason="missing model probability for bracket",
            ),
            TradeCandidate(
                candidate_id=_candidate_id(bracket.contract_ticker, "NO", "BUY"),
                contract_ticker=bracket.contract_ticker,
                bracket_label=bracket.bracket_label,
                side="NO",
                action="BUY",
                eligible=False,
                ineligible_reason="missing model probability for bracket",
            ),
        ]

    def _buy_candidate(
        self,
        risk_limits: RiskLimits,
        bracket: MarketBracket,
        side: Side,
        fair_probability: float,
    ) -> TradeCandidate:
        if side == "YES":
            entry_price = bracket.effective_yes_ask_cents()
            bid_price = bracket.effective_yes_bid_cents()
        else:
            entry_price = bracket.effective_no_ask_cents()
            bid_price = bracket.effective_no_bid_cents()

        model_fair_cents = 100.0 * fair_probability
        fee_cents = _fee_cents_per_contract(entry_price, risk_limits) if entry_price is not None else 0.0
        raw_edge = model_fair_cents - float(entry_price or 0)
        fee_adjusted_edge = raw_edge - fee_cents
        spread = _spread_cents(bid_price, entry_price)
        max_contracts = _max_contracts_for_buy(entry_price, risk_limits)
        reason = _ineligible_reason_for_buy(
            price_cents=entry_price,
            fee_adjusted_edge_cents=fee_adjusted_edge,
            max_contracts=max_contracts,
            risk_limits=risk_limits,
            bracket=bracket,
        )

        return TradeCandidate(
            candidate_id=_candidate_id(bracket.contract_ticker, side, "BUY"),
            contract_ticker=bracket.contract_ticker,
            bracket_label=bracket.bracket_label,
            side=side,
            action="BUY",
            entry_price_cents=entry_price,
            model_fair_cents=round(model_fair_cents, 4),
            raw_edge_cents=round(raw_edge, 4),
            fee_cents=round(fee_cents, 4),
            fee_adjusted_edge_cents=round(fee_adjusted_edge, 4),
            spread_cents=spread,
            max_contracts=max_contracts,
            risk_dollars=round(max_contracts * ((entry_price or 0) / 100.0), 2),
            liquidity_score=_liquidity_score(bracket, spread),
            eligible=reason is None,
            ineligible_reason=reason,
        )

    def _close_candidate(
        self,
        position: FakePosition,
        brackets: list[MarketBracket],
        probabilities: dict[str, ProbabilityBin],
        risk_limits: RiskLimits,
    ) -> TradeCandidate:
        bracket = next((b for b in brackets if b.contract_ticker == position.contract_ticker), None)
        if bracket is None:
            return TradeCandidate(
                candidate_id=_candidate_id(position.contract_ticker, position.side, "CLOSE"),
                contract_ticker=position.contract_ticker,
                bracket_label=position.bracket_label,
                side=position.side,
                action="CLOSE",
                max_contracts=position.quantity,
                eligible=False,
                ineligible_reason="position contract missing from market brackets",
            )

        probability_bin = _find_probability(bracket, probabilities)
        p_yes = probability_bin.probability if probability_bin else 0.0
        fair_probability = p_yes if position.side == "YES" else (1.0 - p_yes)
        exit_price = bracket.effective_yes_bid_cents() if position.side == "YES" else bracket.effective_no_bid_cents()
        fee_cents = _fee_cents_per_contract(exit_price, risk_limits) if exit_price is not None else 0.0
        raw_edge = float(exit_price or 0) - float(position.avg_entry_price_cents)
        fee_adjusted = raw_edge - fee_cents
        spread = _spread_cents(
            bracket.effective_yes_bid_cents() if position.side == "YES" else bracket.effective_no_bid_cents(),
            bracket.effective_yes_ask_cents() if position.side == "YES" else bracket.effective_no_ask_cents(),
        )

        return TradeCandidate(
            candidate_id=_candidate_id(position.contract_ticker, position.side, "CLOSE"),
            contract_ticker=position.contract_ticker,
            bracket_label=position.bracket_label,
            side=position.side,
            action="CLOSE",
            exit_price_cents=exit_price,
            model_fair_cents=round(100.0 * fair_probability, 4),
            raw_edge_cents=round(raw_edge, 4),
            fee_cents=round(fee_cents, 4),
            fee_adjusted_edge_cents=round(fee_adjusted, 4),
            spread_cents=spread,
            max_contracts=position.quantity,
            risk_dollars=0.0,
            liquidity_score=_liquidity_score(bracket, spread),
            eligible=exit_price is not None and position.quantity > 0,
            ineligible_reason=None if exit_price is not None and position.quantity > 0 else "missing exit price or empty position",
            notes="Close existing fake-money position.",
        )

    def _cancel_candidate(self, order: dict) -> TradeCandidate:
        order_id = str(order.get("order_id") or order.get("id") or "")
        contract_ticker = order.get("contract_ticker")
        side = order.get("side")
        bracket = order.get("bracket_label") or order.get("bracket")
        eligible = bool(order_id and str(order.get("status") or "open").lower() == "open")
        return TradeCandidate(
            candidate_id=f"{order_id}:CANCEL",
            contract_ticker=str(contract_ticker) if contract_ticker else None,
            bracket_label=str(bracket) if bracket else None,
            side=side if side in {"YES", "NO"} else None,
            action="CANCEL",
            max_contracts=int(order.get("quantity") or 0),
            eligible=eligible,
            ineligible_reason=None if eligible else "fake order is not open or is missing an id",
            notes="Cancel existing fake-money order.",
        )


def build_trade_board(context: TraderContext) -> TraderContext:
    """Return a copy of context with candidate_trades populated."""
    candidates = TradeBoardBuilder().build(context)
    return replace(context, candidate_trades=candidates)
