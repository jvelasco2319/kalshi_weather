from __future__ import annotations

from dataclasses import dataclass

from kalshi_weather.market_lifecycle.lifecycle_state import LifecycleState


@dataclass(frozen=True)
class EffectiveProfile:
    name: str
    allow_new_entries: bool
    allow_close: bool
    allow_cancel: bool
    allow_reduce_risk: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "active_profile": self.name,
            "allow_new_entries": self.allow_new_entries,
            "allow_close": self.allow_close,
            "allow_cancel": self.allow_cancel,
            "allow_reduce_risk": self.allow_reduce_risk,
            "profile_reason": self.reason,
        }


def profile_for_lifecycle_state(state: LifecycleState) -> EffectiveProfile:
    if state == LifecycleState.WAIT_FOR_MARKET_OPEN:
        return EffectiveProfile("pre_open", False, False, True, False, "market not open")
    if state == LifecycleState.TRADE_OPEN_MARKET:
        return EffectiveProfile("active_nowcast", True, True, True, True, "market open")
    if state == LifecycleState.LATE_DAY_RISK_MANAGE:
        return EffectiveProfile(
            "late_day_risk_manage",
            True,
            True,
            True,
            True,
            "market near close; entries limited by candidate filters",
        )
    if state == LifecycleState.CLOSE_ONLY:
        return EffectiveProfile("close_only", False, True, True, True, "inside close-only window")
    if state == LifecycleState.MARKET_CLOSED_NO_TRADING:
        return EffectiveProfile("post_close", False, False, True, False, "market closed")
    if state == LifecycleState.WAIT_FOR_OFFICIAL_SETTLEMENT:
        return EffectiveProfile("wait_settlement", False, False, False, False, "waiting for official settlement")
    if state in {LifecycleState.SETTLE_PAPER_PORTFOLIO, LifecycleState.SETTLED}:
        return EffectiveProfile("settled", False, False, False, False, "settlement stage")
    return EffectiveProfile("timeline_incomplete", False, False, False, False, "timeline incomplete")

