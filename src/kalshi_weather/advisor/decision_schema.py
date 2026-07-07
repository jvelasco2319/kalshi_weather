from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ADVISOR_DECISIONS = frozenset(
    {
        "BUY_YES",
        "BUY_NO",
        "SELL",
        "HOLD",
        "WAIT",
        "BLOCK",
        "REDUCE_SIZE",
        "LONG_HOLD_CANDIDATE",
    }
)
TRADE_TYPES = frozenset({"microtrade", "long_hold", "scout", "none"})
SIDES = frozenset({"YES", "NO", "NONE"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
ENTRY_DECISIONS = frozenset({"BUY_YES", "BUY_NO"})
SAFE_NON_TRADE_DECISIONS = frozenset({"HOLD", "WAIT", "BLOCK", "REDUCE_SIZE", "LONG_HOLD_CANDIDATE"})


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


@dataclass(frozen=True)
class AdvisorInput:
    decision_time_utc: str
    decision_time_local: str
    series: str
    station: str
    target_date: str
    strategy_mode: str
    race_mode: str
    current_weather: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    candidate_trade: dict[str, Any] = field(default_factory=dict)
    position_state: dict[str, Any] = field(default_factory=dict)
    risk_state: dict[str, Any] = field(default_factory=dict)
    market_context: dict[str, Any] = field(default_factory=dict)
    recent_history: dict[str, Any] = field(default_factory=dict)
    configuration: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> AdvisorInput:
        required = [
            "decision_time_utc",
            "decision_time_local",
            "series",
            "station",
            "target_date",
            "strategy_mode",
            "race_mode",
        ]
        missing = [key for key in required if payload.get(key) is None]
        if missing:
            raise ValueError("advisor input missing fields: " + ", ".join(missing))
        return cls(
            decision_time_utc=str(payload["decision_time_utc"]),
            decision_time_local=str(payload["decision_time_local"]),
            series=str(payload["series"]),
            station=str(payload["station"]),
            target_date=str(payload["target_date"]),
            strategy_mode=str(payload["strategy_mode"]),
            race_mode=str(payload["race_mode"]),
            current_weather=dict(payload.get("current_weather") or {}),
            model=dict(payload.get("model") or {}),
            candidate_trade=dict(payload.get("candidate_trade") or {}),
            position_state=dict(payload.get("position_state") or {}),
            risk_state=dict(payload.get("risk_state") or {}),
            market_context=dict(payload.get("market_context") or {}),
            recent_history=dict(payload.get("recent_history") or {}),
            configuration=dict(payload.get("configuration") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), default=_json_default, indent=indent, sort_keys=True)


@dataclass(frozen=True)
class AdvisorDecision:
    decision: str
    trade_type: str
    model_key: str
    market_ticker: str
    bracket_label: str
    side: str
    confidence: str
    trade_quality_score: int
    recommended_size_multiplier: float
    primary_reason: str
    supporting_reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    hard_veto_flags: list[str] = field(default_factory=list)
    requires_validator_approval: bool = True
    should_recheck_after_minutes: int = 1
    human_readable_summary: str = ""

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> AdvisorDecision:
        missing = [key for key in _required_decision_fields() if key not in payload]
        if missing:
            raise ValueError("advisor decision missing fields: " + ", ".join(missing))
        decision = cls(
            decision=str(payload["decision"]).upper(),
            trade_type=str(payload["trade_type"]).lower(),
            model_key=str(payload["model_key"]),
            market_ticker=str(payload["market_ticker"]),
            bracket_label=str(payload["bracket_label"]),
            side=str(payload["side"]).upper(),
            confidence=str(payload["confidence"]).lower(),
            trade_quality_score=int(payload["trade_quality_score"]),
            recommended_size_multiplier=float(payload["recommended_size_multiplier"]),
            primary_reason=str(payload["primary_reason"]),
            supporting_reasons=_string_list(payload.get("supporting_reasons")),
            risk_flags=_string_list(payload.get("risk_flags")),
            hard_veto_flags=_string_list(payload.get("hard_veto_flags")),
            requires_validator_approval=bool(payload["requires_validator_approval"]),
            should_recheck_after_minutes=int(payload["should_recheck_after_minutes"]),
            human_readable_summary=str(payload["human_readable_summary"]),
        )
        errors = validate_advisor_decision(decision)
        if errors:
            raise ValueError("invalid advisor decision: " + "; ".join(errors))
        return decision

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), default=_json_default, indent=indent, sort_keys=True)


def _required_decision_fields() -> list[str]:
    return [
        "decision",
        "trade_type",
        "model_key",
        "market_ticker",
        "bracket_label",
        "side",
        "confidence",
        "trade_quality_score",
        "recommended_size_multiplier",
        "primary_reason",
        "supporting_reasons",
        "risk_flags",
        "hard_veto_flags",
        "requires_validator_approval",
        "should_recheck_after_minutes",
        "human_readable_summary",
    ]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected list")
    return [str(item) for item in value]


def validate_advisor_decision(decision: AdvisorDecision | dict[str, Any]) -> list[str]:
    try:
        parsed = decision if isinstance(decision, AdvisorDecision) else AdvisorDecision.from_mapping(decision)
    except (TypeError, ValueError) as exc:
        return [str(exc)]
    errors: list[str] = []
    if parsed.decision not in ADVISOR_DECISIONS:
        errors.append(f"unknown decision {parsed.decision}")
    if parsed.trade_type not in TRADE_TYPES:
        errors.append(f"unknown trade_type {parsed.trade_type}")
    if parsed.side not in SIDES:
        errors.append(f"unknown side {parsed.side}")
    if parsed.confidence not in CONFIDENCE_LEVELS:
        errors.append(f"unknown confidence {parsed.confidence}")
    if not 0 <= int(parsed.trade_quality_score) <= 100:
        errors.append("trade_quality_score must be 0-100")
    if not 0.0 <= float(parsed.recommended_size_multiplier) <= 1.0:
        errors.append("recommended_size_multiplier must be 0.0-1.0")
    if parsed.requires_validator_approval is not True:
        errors.append("requires_validator_approval must be true")
    if parsed.decision in ENTRY_DECISIONS and parsed.side == "NONE":
        errors.append("entry decisions require YES or NO side")
    if parsed.decision == "BUY_YES" and parsed.side != "YES":
        errors.append("BUY_YES requires side YES")
    if parsed.decision == "BUY_NO" and parsed.side != "NO":
        errors.append("BUY_NO requires side NO")
    if parsed.decision in SAFE_NON_TRADE_DECISIONS and parsed.recommended_size_multiplier < 0:
        errors.append("safe decisions cannot have negative size")
    return errors


def advisor_decision_from_json(text: str) -> AdvisorDecision:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("advisor JSON must be an object")
    return AdvisorDecision.from_mapping(payload)


def advisor_decision_to_json(decision: AdvisorDecision, *, indent: int | None = 2) -> str:
    return decision.to_json(indent=indent)


def advisor_input_to_json(advisor_input: AdvisorInput, *, indent: int | None = 2) -> str:
    return advisor_input.to_json(indent=indent)


def coerce_invalid_decision_to_safe_block(
    raw: Any,
    *,
    reason: str = "invalid or unsafe advisor output",
    fallback_input: AdvisorInput | dict[str, Any] | None = None,
) -> AdvisorDecision:
    context = fallback_input.to_dict() if isinstance(fallback_input, AdvisorInput) else dict(fallback_input or {})
    candidate = dict(context.get("candidate_trade") or {})
    model = dict(context.get("model") or {})
    if isinstance(raw, dict):
        candidate.update({key: raw.get(key) for key in ("market_ticker", "bracket_label", "side") if raw.get(key)})
        if raw.get("model_key"):
            model["model_key"] = raw.get("model_key")
    side = str(candidate.get("side") or "NONE").upper()
    if side not in SIDES:
        side = "NONE"
    return AdvisorDecision(
        decision="BLOCK",
        trade_type="none",
        model_key=str(model.get("model_key") or ""),
        market_ticker=str(candidate.get("market_ticker") or ""),
        bracket_label=str(candidate.get("bracket_label") or ""),
        side=side,
        confidence="high",
        trade_quality_score=0,
        recommended_size_multiplier=0.0,
        primary_reason=reason,
        supporting_reasons=["Advisor output failed strict validation; fail closed."],
        risk_flags=["invalid_advisor_output"],
        hard_veto_flags=["invalid_advisor_output"],
        requires_validator_approval=True,
        should_recheck_after_minutes=5,
        human_readable_summary="Blocked because the advisor output was invalid or unsafe.",
    )

