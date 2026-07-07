from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .agent import TraderAgent, TraderRunResult
from .context_builder import build_context_from_inputs
from .trader_types import MarketBracket, ModelEstimate, ProbabilityBin, RiskLimits, TraderContext


def load_contexts_from_jsonl(path: str | Path) -> list[TraderContext]:
    contexts: list[TraderContext] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            data = json.loads(line)
            contexts.append(context_from_dict(data))
    return contexts


def replay_contexts(contexts: Iterable[TraderContext], agent: TraderAgent) -> list[TraderRunResult]:
    return [agent.recommend(context) for context in contexts]


def context_from_dict(data: dict) -> TraderContext:
    risk = data.get("risk_limits") or {}
    return build_context_from_inputs(
        series=data.get("series", ""),
        station=data.get("station", ""),
        market_date=data.get("market_date"),
        model_estimates=[ModelEstimate(**m) for m in data.get("model_estimates", [])],
        probability_bins=[ProbabilityBin(**p) for p in data.get("probability_bins", [])],
        market_brackets=[MarketBracket(**m) for m in data.get("market_brackets", [])],
        risk_limits=RiskLimits(**risk) if risk else None,
        observed_high_so_far_f=data.get("observed_high_so_far_f"),
        latest_observation_time_utc=data.get("latest_observation_time_utc"),
        official_settlement_source=data.get("official_settlement_source", "NWS CLI official station high"),
        current_time_utc=data.get("current_time_utc"),
        weather_notes=data.get("weather_notes"),
        market_notes=data.get("market_notes"),
        recent_trade_history_summary=data.get("recent_trade_history_summary"),
        recent_price_trend_summary=data.get("recent_price_trend_summary"),
    )
