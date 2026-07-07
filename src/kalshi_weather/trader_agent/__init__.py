"""LLM trader-agent layer for fake-money Kalshi weather trading."""

from .agent import TraderAgent, TraderRunResult
from .context_builder import build_context_from_inputs
from .decision_schema import TraderDecision
from .trade_board import TradeBoardBuilder, build_trade_board
from .trader_types import (
    FakePosition,
    MarketBracket,
    ModelEstimate,
    ProbabilityBin,
    RiskLimits,
    TradeCandidate,
    TraderContext,
)
from .validator import ValidationResult, validate_decision

__all__ = [
    "FakePosition",
    "MarketBracket",
    "ModelEstimate",
    "ProbabilityBin",
    "RiskLimits",
    "TradeBoardBuilder",
    "TradeCandidate",
    "TraderAgent",
    "TraderContext",
    "TraderDecision",
    "TraderRunResult",
    "ValidationResult",
    "build_context_from_inputs",
    "build_trade_board",
    "validate_decision",
]
