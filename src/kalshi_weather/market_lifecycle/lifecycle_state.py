from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from kalshi_weather.market_lifecycle.market_calendar import MarketTimeline


class LifecycleState(str, Enum):
    WAIT_FOR_MARKET_OPEN = "WAIT_FOR_MARKET_OPEN"
    TRADE_OPEN_MARKET = "TRADE_OPEN_MARKET"
    LATE_DAY_RISK_MANAGE = "LATE_DAY_RISK_MANAGE"
    CLOSE_ONLY = "CLOSE_ONLY"
    MARKET_CLOSED_NO_TRADING = "MARKET_CLOSED_NO_TRADING"
    WAIT_FOR_OFFICIAL_SETTLEMENT = "WAIT_FOR_OFFICIAL_SETTLEMENT"
    SETTLE_PAPER_PORTFOLIO = "SETTLE_PAPER_PORTFOLIO"
    SETTLED = "SETTLED"
    TIMELINE_INCOMPLETE = "TIMELINE_INCOMPLETE"


@dataclass(frozen=True)
class LifecycleSnapshot:
    state: LifecycleState
    seconds_until_open: float | None
    seconds_until_close: float | None
    seconds_since_close: float | None
    market_status: str | None
    settlement_status: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "lifecycle_state": self.state.value,
            "seconds_until_open": self.seconds_until_open,
            "seconds_until_close": self.seconds_until_close,
            "seconds_since_close": self.seconds_since_close,
            "market_status": self.market_status,
            "settlement_status": self.settlement_status,
        }


def determine_lifecycle_state(
    now_utc: datetime,
    timeline: MarketTimeline,
    *,
    late_day_before_close: timedelta = timedelta(hours=6),
    close_only_before_close: timedelta = timedelta(hours=2),
    official_result_available: bool = False,
) -> LifecycleState:
    now = now_utc.astimezone(timezone.utc)
    settlement_status = (timeline.settlement_status or "").lower()
    market_status = (timeline.status or "").lower()
    if settlement_status in {"settled", "final", "final_official"}:
        return LifecycleState.SETTLED
    if market_status in {"settled", "finalized"} and not official_result_available and not timeline.result:
        return LifecycleState.SETTLED
    if not timeline.metadata_complete_for_trading:
        return LifecycleState.TIMELINE_INCOMPLETE

    open_time = timeline.market_open_time_utc
    close_time = timeline.trade_close_utc
    assert open_time is not None
    assert close_time is not None

    if now < open_time:
        return LifecycleState.WAIT_FOR_MARKET_OPEN
    if now > close_time or market_status in {"closed", "expired"}:
        if official_result_available or timeline.result:
            return LifecycleState.SETTLE_PAPER_PORTFOLIO
        if market_status in {"closed", "expired"}:
            return LifecycleState.WAIT_FOR_OFFICIAL_SETTLEMENT
        return LifecycleState.MARKET_CLOSED_NO_TRADING

    time_to_close = close_time - now
    if time_to_close <= close_only_before_close:
        return LifecycleState.CLOSE_ONLY
    if time_to_close <= late_day_before_close:
        return LifecycleState.LATE_DAY_RISK_MANAGE
    return LifecycleState.TRADE_OPEN_MARKET


def lifecycle_snapshot(
    now_utc: datetime,
    timeline: MarketTimeline,
    *,
    official_result_available: bool = False,
    late_day_before_close: timedelta = timedelta(hours=6),
    close_only_before_close: timedelta = timedelta(hours=2),
) -> LifecycleSnapshot:
    return LifecycleSnapshot(
        state=determine_lifecycle_state(
            now_utc,
            timeline,
            official_result_available=official_result_available,
            late_day_before_close=late_day_before_close,
            close_only_before_close=close_only_before_close,
        ),
        seconds_until_open=timeline.seconds_until_open(now_utc),
        seconds_until_close=timeline.seconds_until_close(now_utc),
        seconds_since_close=timeline.seconds_since_close(now_utc),
        market_status=timeline.status,
        settlement_status=timeline.settlement_status,
    )
