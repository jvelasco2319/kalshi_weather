from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time
from typing import Any

LEVEL_RANK = {"low": 0, "medium": 1, "high": 2, "extreme": 3}

@dataclass(frozen=True)
class RiskConfig:
    min_edge_cents: float = 8.0
    min_no_edge_cents: float = 8.0
    min_no_upside_cents: float = 8.0
    max_no_bin_probability: float = 0.20
    max_open_positions: int = 4
    max_open_orders: int = 4
    max_total_open_risk_groups: int = 4
    max_risk_dollars_per_trade: float = 50.0
    max_total_exposure_dollars: float = 250.0
    max_contracts_per_trade: int = 100
    allow_new_entries: str | bool = True
    allow_close: bool = True
    allow_cancel: bool = True
    allow_reduce_risk: bool = True
    allow_observation_elimination: str | bool = False
    effective_allow_observation_elimination: bool = False
    allow_no_exclusion: str | bool = True
    force_cancel_open_orders: bool = False
    prefer_close_over_new_entries: bool = False
    prefer_take_profit_or_reduce_risk: bool = False
    stop_new_trading: bool = False

@dataclass(frozen=True)
class ProfileDecision:
    active_profile: str
    previous_profile: str | None
    profile_reason: str
    profile_changed_this_iteration: bool
    effective_risk_config: RiskConfig
    profile_reason_code: str | None = None
    target_date_relation: str | None = None
    profile_overrides_applied: dict[str, Any] = field(default_factory=dict)
    dynamic_overrides_applied: dict[str, Any] = field(default_factory=dict)
    station_timezone: str | None = None
    station_local_time: str | None = None
    station_local_date: str | None = None
    target_date: str | None = None
    minutes_until_profile_end: int | None = None
    profile_start_local: str | None = None
    profile_end_local: str | None = None
    base_cli_config: dict[str, Any] = field(default_factory=dict)
    profile_requested_allow_observation_elimination: str | bool | None = None
    effective_allow_observation_elimination: bool = False
    dynamic_override_reason: str | None = None

@dataclass(frozen=True)
class ProfileInputs:
    now_local: datetime
    target_date_local: datetime
    observation_available: bool = False
    observation_stale: bool = True
    observation_station_matches_settlement: bool = True
    latest_observation_time_utc: str | None = None
    observed_high_so_far_f: float | None = None
    model_disagreement_level: str = "low"
    model_cluster_status: str = "moderate_consensus"
    full_model_spread_f: float | None = None
    open_pnl_dollars: float = 0.0
    total_open_risk_groups: int = 0
    max_risk_groups_reached: bool = False
    worst_case_loss_dollars: float = 0.0

DEFAULT_PROFILES: dict[str, RiskConfig] = {
    "overnight_next_day": RiskConfig(
        min_edge_cents=12, min_no_edge_cents=12, min_no_upside_cents=12,
        max_no_bin_probability=0.10, max_open_positions=2, max_open_orders=2,
        max_total_open_risk_groups=2, max_risk_dollars_per_trade=25,
        max_total_exposure_dollars=100, max_contracts_per_trade=50,
        allow_observation_elimination=False, allow_no_exclusion="limited"),
    "morning_pre_observation": RiskConfig(
        min_edge_cents=10, min_no_edge_cents=10, min_no_upside_cents=10,
        max_no_bin_probability=0.15, max_open_positions=2, max_open_orders=2,
        max_total_open_risk_groups=2, max_risk_dollars_per_trade=35,
        max_total_exposure_dollars=150, max_contracts_per_trade=50,
        allow_observation_elimination=False, allow_no_exclusion="limited"),
    "active_nowcast": RiskConfig(allow_observation_elimination=True),
    "late_day_risk_manage": RiskConfig(
        min_edge_cents=10, min_no_edge_cents=10, min_no_upside_cents=10,
        max_no_bin_probability=0.15, max_open_positions=3, max_open_orders=1,
        max_total_open_risk_groups=3, max_risk_dollars_per_trade=25,
        max_total_exposure_dollars=150, allow_new_entries="limited",
        prefer_close_over_new_entries=True,
        allow_no_exclusion="observation_confirmed_only", allow_observation_elimination=True),
    "risk_reduce": RiskConfig(allow_new_entries=False, allow_close=True, allow_cancel=True),
    "close_only": RiskConfig(
        allow_new_entries=False,
        allow_close=True,
        allow_cancel=True,
        max_open_positions=2,
        max_open_orders=0,
        max_total_open_risk_groups=2,
        force_cancel_open_orders=True,
        prefer_take_profit_or_reduce_risk=True,
    ),
    "post_close": RiskConfig(
        allow_new_entries=False,
        allow_close=False,
        allow_cancel=True,
        allow_reduce_risk=False,
        max_open_positions=0,
        max_open_orders=0,
        max_total_open_risk_groups=0,
        stop_new_trading=True,
    ),
}

def base_profile_for_time(now_local: datetime, target_date_local: datetime) -> str:
    """Return a conservative profile based on station-local clock.

    Assumes KLAX-like schedule; repo integration can replace with config-driven times.
    """
    is_before_target_day = now_local.date() < target_date_local.date()
    clock = now_local.time()
    if is_before_target_day or clock < time(5, 30):
        return "overnight_next_day"
    if clock < time(9, 0):
        return "morning_pre_observation"
    if clock < time(13, 30):
        return "active_nowcast"
    if clock < time(16, 30):
        return "late_day_risk_manage"
    if clock < time(18, 0):
        return "close_only"
    return "post_close"

def observation_elimination_effective(inputs: ProfileInputs) -> bool:
    return bool(
        inputs.observation_available
        and not inputs.observation_stale
        and inputs.observation_station_matches_settlement
        and inputs.latest_observation_time_utc
        and inputs.observed_high_so_far_f is not None
    )

def apply_dynamic_overrides(config: RiskConfig, inputs: ProfileInputs) -> tuple[RiskConfig, dict[str, Any], str | None]:
    overrides: dict[str, Any] = {}
    reason: str | None = None

    effective_elimination = observation_elimination_effective(inputs)
    if not effective_elimination:
        config = replace(config, allow_observation_elimination=False, effective_allow_observation_elimination=False)
        overrides["stale_or_missing_observation"] = {
            "allow_observation_elimination": False,
            "effective_allow_observation_elimination": False,
        }
    else:
        requested = config.allow_observation_elimination
        config = replace(
            config,
            effective_allow_observation_elimination=bool(requested)
            or str(requested).lower() == "true_only_if_fresh_station_matched",
        )

    rank = LEVEL_RANK.get(inputs.model_disagreement_level, 0)
    if rank >= LEVEL_RANK["high"]:
        config = replace(
            config,
            min_edge_cents=config.min_edge_cents + 2,
            min_no_edge_cents=config.min_no_edge_cents + 2,
            max_contracts_per_trade=max(1, int(config.max_contracts_per_trade * 0.5)),
        )
        overrides["high_model_disagreement"] = True
    if rank >= LEVEL_RANK["extreme"]:
        config = replace(
            config,
            min_edge_cents=config.min_edge_cents + 2,
            min_no_edge_cents=config.min_no_edge_cents + 2,
            max_contracts_per_trade=max(1, int(config.max_contracts_per_trade * 0.5)),
        )
        overrides["extreme_model_disagreement"] = True

    if inputs.open_pnl_dollars <= -40:
        config = DEFAULT_PROFILES["close_only"]
        overrides["drawdown_close_only"] = inputs.open_pnl_dollars
        reason = "drawdown_close_only"
    elif inputs.open_pnl_dollars <= -20:
        config = DEFAULT_PROFILES["risk_reduce"]
        overrides["drawdown_risk_reduce"] = inputs.open_pnl_dollars
        reason = "drawdown_risk_reduce"

    if inputs.max_risk_groups_reached:
        config = replace(config, allow_new_entries=False, allow_close=True, allow_cancel=True)
        overrides["max_risk_groups_reached"] = True
        reason = reason or "max_risk_groups_reached"

    if inputs.worst_case_loss_dollars >= 50:
        config = DEFAULT_PROFILES["risk_reduce"]
        overrides["settlement_concentration"] = inputs.worst_case_loss_dollars
        reason = reason or "settlement_concentration"

    return config, overrides, reason

def select_profile(inputs: ProfileInputs, previous_profile: str | None = None) -> ProfileDecision:
    base = base_profile_for_time(inputs.now_local, inputs.target_date_local)
    config = DEFAULT_PROFILES[base]
    config, dyn, dyn_reason = apply_dynamic_overrides(config, inputs)
    active = "risk_reduce" if dyn_reason in {"drawdown_risk_reduce", "max_risk_groups_reached", "settlement_concentration"} else base
    if dyn_reason == "drawdown_close_only":
        active = "close_only"
    relation = (
        "past"
        if inputs.target_date_local.date() < inputs.now_local.date()
        else "today"
        if inputs.target_date_local.date() == inputs.now_local.date()
        else "tomorrow"
        if (inputs.target_date_local.date() - inputs.now_local.date()).days == 1
        else "future"
    )
    reason_code = dyn_reason or ("target_date_tomorrow" if relation in {"tomorrow", "future"} and base == "overnight_next_day" else f"time_window:{base}")
    return ProfileDecision(
        active_profile=active,
        previous_profile=previous_profile,
        profile_reason=reason_code,
        profile_reason_code=reason_code,
        target_date_relation=relation,
        profile_changed_this_iteration=(previous_profile != active),
        effective_risk_config=config,
        profile_overrides_applied={"base_profile": base},
        dynamic_overrides_applied=dyn,
        station_local_time=inputs.now_local.strftime("%H:%M"),
        station_local_date=inputs.now_local.date().isoformat(),
        target_date=inputs.target_date_local.date().isoformat(),
        profile_requested_allow_observation_elimination=DEFAULT_PROFILES[base].allow_observation_elimination,
        effective_allow_observation_elimination=config.effective_allow_observation_elimination,
        dynamic_override_reason="stale_or_missing_observation" if "stale_or_missing_observation" in dyn else None,
    )
