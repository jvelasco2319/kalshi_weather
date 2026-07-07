from __future__ import annotations

from typing import Any

from .trade_board import build_trade_board
from .trader_types import (
    FakePosition,
    MarketBracket,
    ModelEstimate,
    ProbabilityBin,
    RiskLimits,
    TraderContext,
    utc_now_iso,
)


def build_context_from_inputs(
    *,
    series: str,
    station: str,
    market_date: str | None,
    probability_bins: list[ProbabilityBin],
    market_brackets: list[MarketBracket],
    model_estimates: list[ModelEstimate] | None = None,
    positions: list[FakePosition] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    risk_limits: RiskLimits | None = None,
    observed_high_so_far_f: float | None = None,
    latest_observation_time_utc: str | None = None,
    official_settlement_source: str = "NWS CLI official station high",
    current_time_utc: str | None = None,
    weather_notes: str | None = None,
    market_notes: str | None = None,
    recent_trade_history_summary: dict[str, Any] | None = None,
    recent_price_trend_summary: dict[str, Any] | None = None,
    include_trade_board: bool = True,
) -> TraderContext:
    """Build the structured context the LLM trader will receive.

    This function is intentionally dependency-light so Codex can adapt it to the
    existing repository's Kalshi/weather clients without changing the core
    trader-agent package.
    """
    context = TraderContext(
        mode="fake_money_only",
        series=series,
        station=station,
        market_date=market_date,
        current_time_utc=current_time_utc or utc_now_iso(),
        official_settlement_source=official_settlement_source,
        observed_high_so_far_f=observed_high_so_far_f,
        latest_observation_time_utc=latest_observation_time_utc,
        model_estimates=model_estimates or [],
        probability_bins=probability_bins,
        market_brackets=market_brackets,
        positions=positions or [],
        open_orders=open_orders or [],
        risk_limits=risk_limits or RiskLimits(),
        weather_notes=weather_notes,
        market_notes=market_notes,
        recent_trade_history_summary=recent_trade_history_summary or {},
        recent_price_trend_summary=recent_price_trend_summary or {},
    )
    if include_trade_board:
        context = build_trade_board(context)
    return context


class ExistingRepoContextBuilder:
    """Adapter shell for wiring into the current kalshi_weather codebase.

    Codex should replace the placeholder provider calls with the repo's existing
    Kalshi, weather, probability, portfolio, and risk modules.
    """

    def __init__(self, *, market_provider: Any, weather_provider: Any, portfolio_provider: Any | None = None) -> None:
        self.market_provider = market_provider
        self.weather_provider = weather_provider
        self.portfolio_provider = portfolio_provider

    def build(self, *, series: str, station: str, market_date: str | None = None) -> TraderContext:
        raise NotImplementedError(
            "Wire this adapter to existing kalshi_weather data/model/trading modules. "
            "Use build_context_from_inputs() once probabilities, brackets, and positions are available."
        )
