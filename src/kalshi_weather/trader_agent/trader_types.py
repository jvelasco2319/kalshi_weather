from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal

Side = Literal["YES", "NO"]
CandidateAction = Literal["BUY", "SELL", "CLOSE", "CANCEL", "HOLD"]
DecisionAction = Literal[
    "HOLD",
    "PLACE_FAKE_LIMIT_BUY",
    "EXECUTE_FAKE_TAKER_BUY",
    "PLACE_FAKE_LIMIT_SELL",
    "CLOSE_FAKE_POSITION",
    "CANCEL_FAKE_ORDER",
]
Confidence = Literal["low", "medium", "high"]
TimeHorizon = Literal["scalp", "intraday", "hold_to_settlement", "no_trade"]
LiquidityScore = Literal["low", "medium", "high"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dataclass_to_dict(value: Any) -> Any:
    """Convert nested dataclasses/lists/dicts into JSON-serializable primitives."""
    if is_dataclass(value):
        return {k: dataclass_to_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [dataclass_to_dict(v) for v in value]
    if isinstance(value, tuple):
        return [dataclass_to_dict(v) for v in value]
    if isinstance(value, dict):
        return {str(k): dataclass_to_dict(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class RiskLimits:
    """Risk settings used before and after the LLM trader acts."""

    min_edge_cents: float = 3.0
    max_contracts_per_trade: int = 100
    max_risk_dollars_per_trade: float = 50.0
    max_total_exposure_dollars: float = 250.0
    max_exposure_dollars_per_bracket: float = 100.0
    max_contracts_per_bracket: int = 500
    max_contracts_per_side: int = 1000
    max_open_positions: int = 4
    max_open_orders: int = 4
    max_total_open_risk_groups: int | None = None
    taker_fee_rate: float = 0.07
    maker_fee_rate: float = 0.0175
    use_maker_fees: bool = False
    min_volume: int = 0
    allow_taker: bool = False
    allow_negative_cash: bool = False
    allow_scale_in: bool = False
    scale_in_edge_buffer_cents: float = 0.0
    same_candidate_cooldown_minutes: float = 15.0
    max_open_loss_dollars: float = 100.0
    max_total_drawdown_dollars: float = 150.0
    allow_lowball_passive_orders: bool = False
    max_passive_distance_from_bid_cents: float = 1.0
    max_passive_order_age_minutes: float = 15.0
    block_high_confidence_no_on_extreme_spread: bool = False
    extreme_spread_no_block_threshold_f: float = 8.0
    block_no_on_model_source_degraded: bool = False
    high_spread_reduce_size_factor: float = 0.5
    clustered_disputed_extra_edge_cents: float = 2.0

    def fee_rate(self) -> float:
        return self.maker_fee_rate if self.use_maker_fees else self.taker_fee_rate

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class ModelEstimate:
    provider: str
    high_f: float | None
    weight: float | None = None
    generated_at_utc: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class ProbabilityBin:
    bracket_label: str
    probability: float
    lower_f: float | None = None
    upper_f: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability must be in [0, 1], got {self.probability}")

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class MarketBracket:
    event_ticker: str
    contract_ticker: str
    bracket_label: str
    lower_f: float | None = None
    upper_f: float | None = None
    yes_bid_cents: int | None = None
    yes_ask_cents: int | None = None
    no_bid_cents: int | None = None
    no_ask_cents: int | None = None
    last_price_cents: int | None = None
    volume: int | None = None
    open_interest: int | None = None

    def effective_yes_ask_cents(self) -> int | None:
        if self.yes_ask_cents is not None:
            return self.yes_ask_cents
        if self.no_bid_cents is not None:
            return 100 - self.no_bid_cents
        return None

    def effective_no_ask_cents(self) -> int | None:
        if self.no_ask_cents is not None:
            return self.no_ask_cents
        if self.yes_bid_cents is not None:
            return 100 - self.yes_bid_cents
        return None

    def effective_yes_bid_cents(self) -> int | None:
        if self.yes_bid_cents is not None:
            return self.yes_bid_cents
        if self.no_ask_cents is not None:
            return 100 - self.no_ask_cents
        return None

    def effective_no_bid_cents(self) -> int | None:
        if self.no_bid_cents is not None:
            return self.no_bid_cents
        if self.yes_ask_cents is not None:
            return 100 - self.yes_ask_cents
        return None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class FakePosition:
    position_id: str
    contract_ticker: str
    bracket_label: str
    side: Side
    quantity: int
    avg_entry_price_cents: float
    opened_at_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class TradeCandidate:
    candidate_id: str
    contract_ticker: str | None
    bracket_label: str | None
    side: Side | None
    action: CandidateAction
    entry_price_cents: int | None = None
    exit_price_cents: int | None = None
    model_fair_cents: float = 0.0
    raw_edge_cents: float = 0.0
    fee_cents: float = 0.0
    fee_adjusted_edge_cents: float = 0.0
    spread_cents: float | None = None
    max_contracts: int = 0
    risk_dollars: float = 0.0
    liquidity_score: LiquidityScore = "low"
    eligible: bool = False
    ineligible_reason: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class TraderContext:
    schema_version: str = "1.0"
    mode: str = "fake_money_only"
    series: str = ""
    station: str = ""
    market_date: str | None = None
    current_time_utc: str = field(default_factory=utc_now_iso)
    official_settlement_source: str = "NWS CLI official station high"
    observed_high_so_far_f: float | None = None
    latest_observation_time_utc: str | None = None
    model_estimates: list[ModelEstimate] = field(default_factory=list)
    probability_bins: list[ProbabilityBin] = field(default_factory=list)
    market_brackets: list[MarketBracket] = field(default_factory=list)
    positions: list[FakePosition] = field(default_factory=list)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    risk_limits: RiskLimits = field(default_factory=RiskLimits)
    candidate_trades: list[TradeCandidate] = field(default_factory=list)
    weather_notes: str | None = None
    market_notes: str | None = None
    recent_trade_history_summary: dict[str, Any] = field(default_factory=dict)
    recent_price_trend_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)
