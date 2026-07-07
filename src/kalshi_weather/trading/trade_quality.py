from __future__ import annotations

from typing import Any

from kalshi_weather.advisor.decision_schema import AdvisorInput
from kalshi_weather.advisor.trade_quality import TradeQualityResult, score_trade_quality


def deterministic_trade_quality(advisor_input: AdvisorInput | dict[str, Any]) -> dict[str, Any]:
    quality = score_trade_quality(advisor_input)
    return {
        **quality.to_dict(),
        "initial_recommendation": _initial_recommendation(quality),
    }


def _initial_recommendation(quality: TradeQualityResult) -> str:
    if quality.hard_veto_flags:
        return "block"
    if quality.score >= 75:
        return "candidate"
    return "wait"

