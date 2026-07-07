"""Rule-based edge engine for fake-money Kalshi weather trading.

This package is intentionally deterministic. It does not place real orders.
It only builds, scores, filters, and selects fake-money/paper trade candidates.
"""

from .types import (
    Action,
    Bracket,
    CandidateTrade,
    CostConfig,
    MarketQuote,
    OrderType,
    PortfolioState,
    Position,
    OpenOrder,
    RiskConfig,
    Side,
    StrategyConfig,
)
from .edge import build_yes_no_candidates, compute_candidate_edge
from .strategy_rules import choose_best_candidate
from .brackets import parse_bracket_label, parse_brackets, determine_final_bracket
from .settlement import BracketSet, SettlementRecord, default_high_temp_bracket_set

__all__ = [
    "Action",
    "Bracket",
    "CandidateTrade",
    "CostConfig",
    "MarketQuote",
    "OrderType",
    "PortfolioState",
    "Position",
    "OpenOrder",
    "RiskConfig",
    "Side",
    "StrategyConfig",
    "build_yes_no_candidates",
    "compute_candidate_edge",
    "choose_best_candidate",
    "parse_bracket_label",
    "parse_brackets",
    "determine_final_bracket",
    "BracketSet",
    "SettlementRecord",
    "default_high_temp_bracket_set",
]
