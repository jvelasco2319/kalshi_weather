from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorDecision

DEFAULT_LLM_MODEL = "gpt-oss:120b"
LLM_PROVIDER_OLLAMA = "ollama"

LLM_TRADE_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "BUY_YES",
                "BUY_NO",
                "SELL",
                "HOLD",
                "WAIT",
                "BLOCK",
                "REDUCE_SIZE",
                "LONG_HOLD_CANDIDATE",
            ],
        },
        "trade_type": {"type": "string", "enum": ["microtrade", "long_hold", "scout", "none"]},
        "model_key": {"type": "string"},
        "market_ticker": {"type": "string"},
        "bracket_label": {"type": "string"},
        "side": {"type": "string", "enum": ["YES", "NO", "NONE"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "trade_quality_score": {"type": "integer", "minimum": 0, "maximum": 100},
        "recommended_size_multiplier": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "primary_reason": {"type": "string"},
        "supporting_reasons": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "hard_veto_flags": {"type": "array", "items": {"type": "string"}},
        "requires_validator_approval": {"type": "boolean", "const": True},
        "should_recheck_after_minutes": {"type": "integer", "minimum": 0},
        "human_readable_summary": {"type": "string"},
    },
    "required": [
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
    ],
}


@dataclass(frozen=True)
class LLMRawResponse:
    provider: str
    model: str
    request_id: str
    raw_text: str
    parsed_json: dict[str, Any] | None = None
    latency_ms: int = 0
    success: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LLMTradeDecision = AdvisorDecision

