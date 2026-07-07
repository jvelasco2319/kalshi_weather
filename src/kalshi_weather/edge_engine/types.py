from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, Optional, Tuple


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    CANCEL = "CANCEL"
    HOLD = "HOLD"
    REJECT = "REJECT"


class OrderType(str, Enum):
    TAKER = "TAKER"
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
    PAPER_ONLY = "PAPER_ONLY"


@dataclass(frozen=True)
class Bracket:
    """Canonical temperature bracket.

    Bounds are expressed in final integer degrees F when possible.
    Examples:
      <66: lower_f=None, upper_f=65
      66-67: lower_f=66, upper_f=67
      > 73: lower_f=74, upper_f=None

    The official settlement value should still come from the contract's
    settlement source. Observation-based elimination is only a trading signal.
    """

    label: str
    lower_f: Optional[float] = None
    upper_f: Optional[float] = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    def contains(self, temp_f: float) -> bool:
        if self.lower_f is not None:
            if self.lower_inclusive and temp_f < self.lower_f:
                return False
            if not self.lower_inclusive and temp_f <= self.lower_f:
                return False
        if self.upper_f is not None:
            if self.upper_inclusive and temp_f > self.upper_f:
                return False
            if not self.upper_inclusive and temp_f >= self.upper_f:
                return False
        return True

    def eliminated_by_observed_high(self, observed_high_f: Optional[float]) -> bool:
        """Return True if an observed high already exceeds the bracket upper bound.

        This is useful for NO-exclusion but it is not final settlement truth.
        Public observations can differ from the final climate report.
        """
        if observed_high_f is None or self.upper_f is None:
            return False
        return observed_high_f > self.upper_f


def canonicalize_label(label: str) -> str:
    cleaned = label.strip().replace("°F", "").replace("°", "").replace("F", "")
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace(" <", "<").replace(" >", ">")
    if cleaned.startswith("<"):
        return "<" + cleaned[1:].strip()
    if cleaned.startswith(">"):
        return "> " + cleaned[1:].strip()
    return cleaned.replace(" ", "")


@dataclass(frozen=True)
class MarketQuote:
    bracket_label: str
    yes_bid_cents: Optional[int] = None
    yes_ask_cents: Optional[int] = None
    no_bid_cents: Optional[int] = None
    no_ask_cents: Optional[int] = None
    ts: Optional[str] = None
    liquidity_score: Optional[float] = None

    def normalized(self) -> "MarketQuote":
        from .market_implied import normalize_quote

        return normalize_quote(self)

    def ask_for(self, side: Side) -> Optional[int]:
        q = self.normalized()
        return q.yes_ask_cents if side == Side.YES else q.no_ask_cents

    def bid_for(self, side: Side) -> Optional[int]:
        q = self.normalized()
        return q.yes_bid_cents if side == Side.YES else q.no_bid_cents

    def spread_for(self, side: Side) -> Optional[int]:
        q = self.normalized()
        bid = q.bid_for(side)
        ask = q.ask_for(side)
        if bid is None or ask is None:
            return None
        return max(0, ask - bid)


@dataclass(frozen=True)
class CostConfig:
    include_fees: bool = True
    taker_fee_rate: float = 0.07
    maker_fee_rate: float = 0.0175
    maker_fee_enabled: bool = False
    slippage_cents: float = 0.5
    tail_risk_padding_cents: float = 2.0
    passive_improvement_cents: int = 1


@dataclass(frozen=True)
class RiskConfig:
    min_edge_cents: float = 8.0
    min_yes_edge_cents: float = 8.0
    min_no_edge_cents: float = 8.0
    min_no_upside_cents: float = 8.0
    min_yes_probability: float = 0.0
    max_no_bin_probability: float = 0.20
    no_probability_filter_mode: str = "hard"
    no_probability_penalty_start: float = 0.20
    no_probability_penalty_factor: float = 0.30
    absolute_no_bin_probability_cap: float = 0.60
    max_no_basket_probability: float = 0.25
    min_no_basket_upside_cents: float = 8.0
    no_basket_max_loss_dollars: float = 25.0
    no_basket_max_contracts: int = 10
    max_spread_cents: int = 4
    max_contracts_per_trade: int = 25
    max_risk_dollars_per_trade: float = 25.0
    max_total_exposure_dollars: float = 150.0
    max_exposure_dollars_per_bracket: float = 50.0
    max_open_positions: int = 4
    max_open_orders: int = 4
    max_total_open_risk_groups: Optional[int] = None
    allow_scale_in: bool = False
    cooldown_seconds: int = 300
    min_cash_buffer_dollars: float = 0.0
    max_model_age_seconds: int = 1800
    max_market_age_seconds: int = 300
    max_observation_age_seconds: int = 900
    require_fresh_market: bool = True
    require_fresh_model: bool = True
    require_fresh_observation_for_elimination: bool = True
    allow_lowball_passive_orders: bool = False
    max_passive_distance_from_bid_cents: float = 1.0
    max_passive_order_age_minutes: float = 15.0
    block_high_confidence_no_on_extreme_spread: bool = False
    extreme_spread_no_block_threshold_f: float = 8.0
    block_no_on_model_source_degraded: bool = False
    model_authoritative: bool = False
    model_authoritative_strength: str = "normal"
    model_authoritative_tight_spread_f: float = 3.0
    model_authoritative_wide_spread_f: float = 5.0
    model_authoritative_extreme_spread_f: float = 7.0
    allow_cheap_ask_yes_with_missing_bid: bool = False
    cheap_ask_yes_max_cents: float = 2.0
    cheap_ask_yes_min_net_edge_cents: float = 8.0
    cheap_ask_yes_max_contracts: int = 25
    high_spread_reduce_size_factor: float = 0.5
    clustered_disputed_extra_edge_cents: float = 2.0
    edge_comparison_epsilon_cents: float = 0.001


@dataclass(frozen=True)
class StrategyConfig:
    strategy: str = "hybrid"  # exact-bin, no-exclusion, hybrid
    decision_mode: str = "rules"  # rules, llm, llm-review
    order_style: str = "passive"  # passive, taker, hybrid


@dataclass(frozen=True)
class Position:
    bracket_label: str
    side: Side
    contracts: int
    avg_price_cents: float

    @property
    def exposure_dollars(self) -> float:
        return self.contracts * self.avg_price_cents / 100.0


@dataclass(frozen=True)
class OpenOrder:
    """Paper-only open order used to reserve fake cash/exposure.

    This prevents a passive strategy from posting many resting orders that would
    exceed cash or exposure if all filled. It is intentionally broker-neutral and
    must be adapted to the repo's paper broker state.
    """

    candidate_id: str
    bracket_label: str
    side: Side
    contracts: int
    limit_price_cents: float
    created_ts: Optional[str] = None

    @property
    def exposure_dollars(self) -> float:
        return self.contracts * self.limit_price_cents / 100.0


@dataclass(frozen=True)
class PortfolioState:
    cash_dollars: float
    positions: Tuple[Position, ...] = ()
    open_orders: Tuple[OpenOrder, ...] = ()
    open_candidate_ids: Tuple[str, ...] = ()
    recent_candidate_ids: Tuple[str, ...] = ()

    @property
    def position_exposure_dollars(self) -> float:
        return sum(p.exposure_dollars for p in self.positions)

    @property
    def open_order_exposure_dollars(self) -> float:
        return sum(o.exposure_dollars for o in self.open_orders)

    @property
    def total_exposure_dollars(self) -> float:
        return self.position_exposure_dollars + self.open_order_exposure_dollars

    def exposure_for_bracket(self, bracket_label: str) -> float:
        canon = canonicalize_label(bracket_label)
        pos_exp = sum(p.exposure_dollars for p in self.positions if canonicalize_label(p.bracket_label) == canon)
        order_exp = sum(o.exposure_dollars for o in self.open_orders if canonicalize_label(o.bracket_label) == canon)
        return pos_exp + order_exp

    def has_same_side_position(self, bracket_label: str, side: Side) -> bool:
        canon = canonicalize_label(bracket_label)
        return any(canonicalize_label(p.bracket_label) == canon and p.side == side for p in self.positions)

    def has_same_side_open_order(self, bracket_label: str, side: Side) -> bool:
        canon = canonicalize_label(bracket_label)
        return any(canonicalize_label(o.bracket_label) == canon and o.side == side for o in self.open_orders)

    def has_candidate_open(self, candidate_id: str) -> bool:
        return candidate_id in self.open_candidate_ids or any(o.candidate_id == candidate_id for o in self.open_orders)

    @property
    def open_position_groups(self) -> int:
        return len({(canonicalize_label(p.bracket_label), p.side) for p in self.positions})

    @property
    def open_order_groups(self) -> int:
        return len({(canonicalize_label(o.bracket_label), o.side) for o in self.open_orders})

    @property
    def total_open_risk_groups(self) -> int:
        return self.open_position_groups + self.open_order_groups


@dataclass(frozen=True)
class CandidateTrade:
    candidate_id: str
    action: Action
    side: Optional[Side]
    bracket_label: Optional[str]
    order_type: OrderType
    quantity: int = 0
    price_cents: Optional[float] = None
    model_probability: Optional[float] = None
    market_probability: Optional[float] = None
    fair_value_cents: Optional[float] = None
    raw_edge_cents: Optional[float] = None
    fee_cents_per_contract: float = 0.0
    slippage_cents: float = 0.0
    tail_risk_padding_cents: float = 0.0
    net_edge_cents: Optional[float] = None
    upside_cents: Optional[float] = None
    spread_cents: Optional[float] = None
    max_loss_dollars: float = 0.0
    eligible: bool = False
    rejection_reason: Optional[str] = None
    note: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def total_cost_dollars(self) -> float:
        if self.price_cents is None:
            return 0.0
        return self.quantity * self.price_cents / 100.0

    def reject(self, reason: str) -> "CandidateTrade":
        return replace(self, eligible=False, rejection_reason=reason)

    def accept(self) -> "CandidateTrade":
        return replace(self, eligible=True, rejection_reason=None)


@dataclass(frozen=True)
class Decision:
    action: Action
    candidate: Optional[CandidateTrade] = None
    reason: str = ""

    @classmethod
    def hold(cls, reason: str = "no_valid_candidate") -> "Decision":
        return cls(action=Action.HOLD, candidate=None, reason=reason)
