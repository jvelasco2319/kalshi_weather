from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence

from .edge import build_yes_no_candidates
from .strategy_rules import choose_best_candidate
from .portfolio_math import apply_fake_buy_fill
from .types import CostConfig, MarketQuote, PortfolioState, RiskConfig, StrategyConfig, Side, canonicalize_label


REQUIRED_BACKTEST_COLUMNS = [
    "target_date",
    "station",
    "run_timestamp",
    "official_final_high",
    "official_final_bracket",
    "bracket",
    "model_probability",
    "market_probability",
    "probability_difference",
    "yes_bid_cents",
    "yes_ask_cents",
    "no_bid_cents",
    "no_ask_cents",
    "fair_yes_cents",
    "fair_no_cents",
    "side",
    "candidate_price_cents",
    "raw_edge_cents",
    "fee_cents",
    "slippage_cents",
    "tail_risk_padding_cents",
    "net_edge_cents",
    "trade_taken_by_rule",
    "settled_result",
    "gross_pnl_cents",
    "net_pnl_cents",
    "clv_5m_cents",
    "clv_15m_cents",
    "clv_30m_cents",
    "clv_final_cents",
    "failure_reason",
]


@dataclass(frozen=True)
class BacktestRecord:
    target_date: str
    station: str
    run_timestamp: str
    official_final_high: float
    official_final_bracket: str
    probabilities: Mapping[str, float]
    quotes: Sequence[MarketQuote]


def settle_candidate(candidate_side: Side, candidate_bracket: str, official_final_bracket: str) -> int:
    bracket_won = canonicalize_label(candidate_bracket) == canonicalize_label(official_final_bracket)
    if candidate_side == Side.YES:
        return 1 if bracket_won else 0
    return 0 if bracket_won else 1


def pnl_cents_for_buy(price_cents: float, settled_result: int, fee_cents: float = 0.0) -> float:
    payout = 100.0 if settled_result else 0.0
    return payout - price_cents - fee_cents


def run_synthetic_backtest(
    records: Sequence[BacktestRecord],
    *,
    series: str,
    cost_config: CostConfig,
    risk_config: RiskConfig,
    strategy_config: StrategyConfig,
    starting_cash_dollars: float = 1000.0,
) -> List[Dict[str, object]]:
    """Small deterministic backtest helper for unit tests and integration.

    Production backtest should adapt this to the repo's actual data loaders.
    """
    rows: List[Dict[str, object]] = []
    portfolio = PortfolioState(cash_dollars=starting_cash_dollars)
    for rec in records:
        candidates = build_yes_no_candidates(
            series=series,
            target_date=rec.target_date,
            probabilities=rec.probabilities,
            quotes=rec.quotes,
            cost_config=cost_config,
            risk_config=risk_config,
            strategy_config=strategy_config,
        )
        decision = choose_best_candidate(candidates, portfolio=portfolio, risk_config=risk_config)
        chosen_id = decision.candidate.candidate_id if decision.candidate else None
        quote_by_label = {canonicalize_label(q.bracket_label): q.normalized() for q in rec.quotes}
        for c in candidates:
            is_chosen = c.candidate_id == chosen_id
            settled = None
            gross_pnl = None
            net_pnl = None
            if is_chosen and c.side and c.bracket_label and c.price_cents is not None:
                settled = settle_candidate(c.side, c.bracket_label, rec.official_final_bracket)
                gross_pnl = pnl_cents_for_buy(c.price_cents, settled, 0.0)
                net_pnl = pnl_cents_for_buy(c.price_cents, settled, c.fee_cents_per_contract)
            rows.append({
                "target_date": rec.target_date,
                "station": rec.station,
                "run_timestamp": rec.run_timestamp,
                "official_final_high": rec.official_final_high,
                "official_final_bracket": rec.official_final_bracket,
                "bracket": c.bracket_label,
                "model_probability": c.model_probability,
                "market_probability": c.market_probability,
                "probability_difference": None if c.market_probability is None or c.model_probability is None else c.model_probability - c.market_probability,
                "yes_bid_cents": None if c.bracket_label is None or canonicalize_label(c.bracket_label) not in quote_by_label else quote_by_label[canonicalize_label(c.bracket_label)].yes_bid_cents,
                "yes_ask_cents": None if c.bracket_label is None or canonicalize_label(c.bracket_label) not in quote_by_label else quote_by_label[canonicalize_label(c.bracket_label)].yes_ask_cents,
                "no_bid_cents": None if c.bracket_label is None or canonicalize_label(c.bracket_label) not in quote_by_label else quote_by_label[canonicalize_label(c.bracket_label)].no_bid_cents,
                "no_ask_cents": None if c.bracket_label is None or canonicalize_label(c.bracket_label) not in quote_by_label else quote_by_label[canonicalize_label(c.bracket_label)].no_ask_cents,
                "fair_yes_cents": None if c.model_probability is None else 100.0 * c.model_probability,
                "fair_no_cents": None if c.model_probability is None else 100.0 * (1.0 - c.model_probability),
                "side": c.side.value if c.side else None,
                "candidate_price_cents": c.price_cents,
                "raw_edge_cents": c.raw_edge_cents,
                "fee_cents": c.fee_cents_per_contract,
                "slippage_cents": c.slippage_cents,
                "tail_risk_padding_cents": c.tail_risk_padding_cents,
                "net_edge_cents": c.net_edge_cents,
                "trade_taken_by_rule": is_chosen,
                "settled_result": settled,
                "gross_pnl_cents": gross_pnl,
                "net_pnl_cents": net_pnl,
                "clv_5m_cents": None,
                "clv_15m_cents": None,
                "clv_30m_cents": None,
                "clv_final_cents": None,
                "failure_reason": c.rejection_reason,
            })
        if decision.candidate is not None and decision.candidate.price_cents is not None:
            # Synthetic backtest state update. The real repo should use its paper broker/journal.
            portfolio = apply_fake_buy_fill(portfolio, decision.candidate)
    return rows
