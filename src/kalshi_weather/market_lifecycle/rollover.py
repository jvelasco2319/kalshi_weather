from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class RolloverDecision:
    should_roll: bool
    reason: str
    next_target_date: date | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "should_roll": self.should_roll,
            "reason": self.reason,
            "next_target_date": None if self.next_target_date is None else self.next_target_date.isoformat(),
        }


def next_calendar_target_date(current_target_date: date) -> date:
    return current_target_date + timedelta(days=1)


def should_roll_to_next_event(
    *,
    current_settled: bool,
    next_event_exists: bool,
    next_event_open: bool,
    current_target_date: date,
) -> RolloverDecision:
    if not current_settled:
        return RolloverDecision(False, "current event not settled")
    if not next_event_exists:
        return RolloverDecision(False, "next event not listed")
    if not next_event_open:
        return RolloverDecision(False, "next event listed but not open")
    return RolloverDecision(True, "next event open", next_calendar_target_date(current_target_date))

