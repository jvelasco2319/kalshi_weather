from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from kalshi_weather.strategy_current.decision_engine import DecisionResult, TradeCandidate
from kalshi_weather.strategy_current.reason_codes import NO_TRADE_CAPTURE_INCOMPLETE


@dataclass(frozen=True)
class ShadowAction:
    created_at_utc: datetime
    reason_code: str
    candidate: TradeCandidate | None

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at_utc": self.created_at_utc.isoformat(),
            "reason_code": self.reason_code,
            "candidate": self.candidate.to_dict() if self.candidate else None,
        }


@dataclass
class ShadowOrderSink:
    actions: list[ShadowAction] = field(default_factory=list)

    def record(self, decision: DecisionResult) -> ShadowAction:
        action = ShadowAction(
            created_at_utc=datetime.now(timezone.utc),
            reason_code=decision.reason_code,
            candidate=decision.candidate,
        )
        self.actions.append(action)
        return action


def incomplete_capture_decision() -> DecisionResult:
    return DecisionResult(
        reason_code=NO_TRADE_CAPTURE_INCOMPLETE,
        candidate=None,
        evaluated_candidates=(),
    )
