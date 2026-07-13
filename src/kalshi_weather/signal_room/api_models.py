from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SignalRoomModelKey = Literal["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]
CANONICAL_SIGNAL_ROOM_MODEL_KEYS = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]


class EventSummary(BaseModel):
    ticker: str
    target_date: date
    station: str
    status: str


class EventState(EventSummary):
    relative_day: Literal["today", "tomorrow", "other"] | None = None
    market_open_at: datetime | None = None
    market_close_at: datetime | None = None
    settlement_bracket: str | None = None
    final_decimal_high_f: float | None = None
    official_high_f: float | None = None


class StrategyState(BaseModel):
    strategy_id: str
    mode: Literal["shadow"]
    live_trading_enabled: bool
    canary_enabled: bool
    taker_enabled: bool
    order_submission_reachable: bool
    code_revision: str
    config_hash: str


class DecisionState(BaseModel):
    evaluated_at: datetime
    status: Literal["TRADE_CANDIDATE", "SHADOW_ONLY", "NO_TRADE", "DATA_INCOMPLETE"]
    reason_code: str
    reason_text: str
    focus_ticker: str | None = None
    focus_bracket: str | None = None
    focus_side: str | None = None
    executable_price: str | None = None
    p_mean: float | None = None
    p_safe: float | None = None
    required_probability: float | None = None
    modeled_net_roi: str | None = None
    max_acceptable_price: str | None = None
    proposed_quantity: str | None = None


class RiskSnapshot(BaseModel):
    model_spread_f: float | None = None
    active_roi_hurdle: float
    adjusted_probability_hurdle: float | None = None
    observed_high_f: float | None = None
    market_leader_bracket: str | None = None
    risk_multiplier: str | None = None
    target_date_exposure_pct: str | None = None
    daily_loss_pct: str | None = None


class ModelSlot(BaseModel):
    model_key: SignalRoomModelKey
    label: str
    display_order: int
    color: str
    state_f: float | None = None
    remaining_window_max_f: float | None = None
    observed_floor_f: float | None = None
    mapped_bracket: str | None = None
    prior_weight: str | None = None
    effective_weight: str | None = None
    maturity_completed_dates: int | None = None
    maturity_required_dates: int | None = None
    maturity_status: Literal["mature", "provisional", "excluded"]
    source_available_at: datetime | None = None
    received_at: datetime | None = None
    age_seconds: int | None = None
    strict_as_of_valid: bool | None = None
    feed_status: Literal["healthy", "stale", "missing", "invalid", "reference_only"]
    status_detail: str | None = None


class GateState(BaseModel):
    code: str
    label: str
    severity: Literal["pass", "info", "warning", "block"]
    detail: str


class ReadinessState(BaseModel):
    tradable_feed_count: int
    required_tradable_feed_count: int
    independent_family_count: int
    required_independent_family_count: int
    nbm_completed_dates: int
    nbm_next_maturity_threshold: int
    orderbook_sequence_valid: bool
    orderbook_depth_available: bool
    fee_schedule_verified: bool
    settlement_rules_verified: bool
    capture_health_status: Literal["healthy", "warning", "invalid"]


class MarketRow(BaseModel):
    ticker: str
    bracket: str
    yes_bid: str | None = None
    yes_ask: str | None = None
    no_bid: str | None = None
    no_ask: str | None = None
    p_mean_yes: float | None = None
    p_safe_yes: float | None = None
    p_mean_no: float | None = None
    p_safe_no: float | None = None
    required_probability_yes: float | None = None
    required_probability_no: float | None = None
    modeled_net_roi_yes: str | None = None
    modeled_net_roi_no: str | None = None
    max_acceptable_yes_price: str | None = None
    max_acceptable_no_price: str | None = None
    model_point_support_count: int | None = None
    eligible: bool = False
    candidate: bool = False
    status_code: str | None = None
    settled_outcome: str | None = None


class SignalRoomSnapshot(BaseModel):
    model_config = ConfigDict(json_schema_extra={"title": "SignalRoomSnapshot"})

    schema_version: str = "1"
    revision: str
    generated_at: datetime
    event: EventState
    strategy: StrategyState
    decision: DecisionState
    risk: RiskSnapshot
    models: list[ModelSlot] = Field(min_length=5, max_length=5)
    gates: list[GateState]
    capture_health: dict[str, object] = Field(default_factory=dict)
    readiness: ReadinessState
    market: list[MarketRow]
    probability_lab: dict[str, object] = Field(default_factory=dict)
    explainability: dict[str, object] = Field(default_factory=dict)
    replay_mode: bool = False
    sample_mode: bool = False
    banner: str | None = None

    @model_validator(mode="after")
    def require_exact_model_slots(self) -> "SignalRoomSnapshot":
        keys = [model.model_key for model in self.models]
        if keys != CANONICAL_SIGNAL_ROOM_MODEL_KEYS:
            raise ValueError("signal room snapshots must contain exactly the five canonical model slots")
        return self


class SignalRoomTimelinePoint(BaseModel):
    evaluated_at: datetime
    observed_high_f: float | None = None
    model_states: dict[str, float | None]
    decision_status: str
    reason_code: str
    focus_ticker: str | None = None
    market_price: str | None = None
    revision: str
    source_ids: list[str] = Field(default_factory=list)


class CaptureHealth(BaseModel):
    event_ticker: str
    status: Literal["healthy", "warning", "invalid", "not_ready"]
    generated_at: datetime
    details: list[GateState]


class HealthResponse(BaseModel):
    status: Literal["healthy", "not_ready"]
    generated_at: datetime
    database_present: bool
    strategy_id: str
    mode: Literal["shadow"]
