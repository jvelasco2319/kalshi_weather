from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from .trader_types import Confidence, DecisionAction, Side, TimeHorizon, dataclass_to_dict

ALLOWED_DECISION_ACTIONS: set[str] = {
    "HOLD",
    "PLACE_FAKE_LIMIT_BUY",
        "EXECUTE_FAKE_TAKER_BUY",
"PLACE_FAKE_LIMIT_SELL",
    "CLOSE_FAKE_POSITION",
    "CANCEL_FAKE_ORDER",
}
ALLOWED_SIDES: set[str] = {"YES", "NO"}
ALLOWED_CONFIDENCE: set[str] = {"low", "medium", "high"}
ALLOWED_TIME_HORIZONS: set[str] = {"scalp", "intraday", "hold_to_settlement", "no_trade"}


class DecisionParseError(ValueError):
    """Raised when LLM output cannot be parsed as a trader decision."""


@dataclass(frozen=True)
class ExitPlan:
    take_profit_cents: int | None = None
    stop_loss_cents: int | None = None
    close_if_edge_below_cents: float | None = None
    close_if_model_probability_below: float | None = None
    max_hold_minutes: int | None = None
    invalidate_if: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExitPlan":
        data = data or {}
        return cls(
            take_profit_cents=data.get("take_profit_cents"),
            stop_loss_cents=data.get("stop_loss_cents"),
            close_if_edge_below_cents=data.get("close_if_edge_below_cents"),
            close_if_model_probability_below=data.get("close_if_model_probability_below"),
            max_hold_minutes=data.get("max_hold_minutes"),
            invalidate_if=str(data.get("invalidate_if") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass(frozen=True)
class TraderDecision:
    schema_version: str = "1.0"
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action: DecisionAction = "HOLD"
    selected_candidate_id: str | None = None
    contract_ticker: str | None = None
    bracket: str | None = None
    side: Side | None = None
    limit_price_cents: int | None = None
    max_contracts: int = 0
    estimated_edge_cents: float = 0.0
    confidence: Confidence = "low"
    time_horizon: TimeHorizon = "no_trade"
    trader_thesis: str = ""
    why_this_trade: str = ""
    why_not_most_likely_bracket: str = ""
    why_not_other_side: str = ""
    exit_plan: ExitPlan = field(default_factory=ExitPlan)
    risk_notes: str = ""
    no_trade_reason: str | None = None

    @classmethod
    def hold(cls, reason: str = "No valid trade selected.") -> "TraderDecision":
        return cls(
            action="HOLD",
            confidence="low",
            time_horizon="no_trade",
            trader_thesis="HOLD",
            why_this_trade="No trade selected.",
            no_trade_reason=reason,
            risk_notes="Fake-money only.",
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraderDecision":
        if not isinstance(data, dict):
            raise DecisionParseError("decision JSON must be an object")
        action = data.get("action", "HOLD")
        if action not in ALLOWED_DECISION_ACTIONS:
            raise DecisionParseError(f"unknown action: {action}")
        side = data.get("side")
        if side is not None and side not in ALLOWED_SIDES:
            raise DecisionParseError(f"unknown side: {side}")
        confidence = data.get("confidence", "low")
        if confidence not in ALLOWED_CONFIDENCE:
            raise DecisionParseError(f"unknown confidence: {confidence}")
        time_horizon = data.get("time_horizon", "no_trade")
        if time_horizon not in ALLOWED_TIME_HORIZONS:
            raise DecisionParseError(f"unknown time_horizon: {time_horizon}")

        return cls(
            schema_version=str(data.get("schema_version", "1.0")),
            decision_id=str(data.get("decision_id") or uuid.uuid4()),
            action=action,  # type: ignore[arg-type]
            selected_candidate_id=data.get("selected_candidate_id"),
            contract_ticker=data.get("contract_ticker"),
            bracket=data.get("bracket"),
            side=side,  # type: ignore[arg-type]
            limit_price_cents=data.get("limit_price_cents"),
            max_contracts=int(data.get("max_contracts") or 0),
            estimated_edge_cents=float(data.get("estimated_edge_cents") or 0.0),
            confidence=confidence,  # type: ignore[arg-type]
            time_horizon=time_horizon,  # type: ignore[arg-type]
            trader_thesis=str(data.get("trader_thesis") or ""),
            why_this_trade=str(data.get("why_this_trade") or ""),
            why_not_most_likely_bracket=str(data.get("why_not_most_likely_bracket") or ""),
            why_not_other_side=str(data.get("why_not_other_side") or ""),
            exit_plan=ExitPlan.from_dict(data.get("exit_plan")),
            risk_notes=str(data.get("risk_notes") or ""),
            no_trade_reason=data.get("no_trade_reason"),
        )

    @classmethod
    def from_json(cls, raw: str) -> "TraderDecision":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            extracted = _extract_first_json_object(raw)
            if extracted is None:
                raise DecisionParseError("LLM output was not valid JSON") from None
            try:
                parsed = json.loads(extracted)
            except json.JSONDecodeError as exc:
                raise DecisionParseError(f"failed to parse extracted JSON: {exc}") from exc
        return cls.from_dict(parsed)

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def _extract_first_json_object(raw: str) -> str | None:
    """Best-effort extraction for fenced or prefixed JSON responses."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start : end + 1]
