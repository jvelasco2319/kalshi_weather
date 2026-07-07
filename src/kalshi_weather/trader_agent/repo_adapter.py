from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .context_builder import build_context_from_inputs
from .trader_types import FakePosition, MarketBracket, ModelEstimate, ProbabilityBin, RiskLimits, TraderContext

DEFAULT_PROBABILITY_MODEL_KEY = "current:current_weighted_blend"


def trader_context_from_model_payload(
    model_payload: dict[str, Any],
    *,
    risk_limits: RiskLimits | None = None,
    positions: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    probability_model_key: str | None = None,
    recent_trade_history_summary: dict[str, Any] | None = None,
    recent_price_trend_summary: dict[str, Any] | None = None,
) -> TraderContext:
    """Adapt the existing model-race payload into an LLM trader context."""
    probabilities_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in model_payload.get("probabilities", []):
        key = _model_key(row)
        probabilities_by_key.setdefault(key, []).append(row)

    selected_key = _select_probability_model_key(probabilities_by_key, probability_model_key)
    selected_rows = probabilities_by_key.get(selected_key, [])
    probability_bins = [_probability_bin_from_row(row) for row in selected_rows]
    market_brackets = [_market_bracket_from_row(row) for row in selected_rows]

    model_source_diagnostics = model_payload.get("model_source_diagnostics") or {}
    trend_summary = dict(recent_price_trend_summary or {})
    if model_source_diagnostics:
        trend_summary["model_source"] = model_source_diagnostics
    return build_context_from_inputs(
        series=str(model_payload.get("series") or ""),
        station=str(model_payload.get("station") or ""),
        market_date=str(model_payload.get("market_date") or "") or None,
        model_estimates=[_model_estimate_from_row(row) for row in model_payload.get("estimates", [])],
        probability_bins=probability_bins,
        market_brackets=market_brackets,
        positions=[_fake_position_from_any(row) for row in positions or []],
        open_orders=open_orders or [],
        risk_limits=risk_limits or RiskLimits(),
        observed_high_so_far_f=_float_or_none(model_payload.get("observed_high_so_far_f")),
        latest_observation_time_utc=_str_or_none(model_payload.get("latest_observation_utc")),
        current_time_utc=_str_or_none(model_payload.get("generated_at_utc")),
        weather_notes=(
            f"Context built from existing kalshi_weather model payload using probability model {selected_key}."
        ),
        market_notes=(
            "Market brackets and bid/ask quotes are adapted from the same Kalshi snapshot used by model-race."
        ),
        recent_trade_history_summary=recent_trade_history_summary or {},
        recent_price_trend_summary=trend_summary,
    )


def context_summary_text(context: TraderContext) -> str:
    eligible = [candidate for candidate in context.candidate_trades if candidate.eligible]
    buy_yes = [candidate for candidate in context.candidate_trades if candidate.action == "BUY" and candidate.side == "YES"]
    buy_no = [candidate for candidate in context.candidate_trades if candidate.action == "BUY" and candidate.side == "NO"]
    best = max(
        [candidate for candidate in context.candidate_trades if candidate.action == "BUY" and candidate.eligible],
        key=lambda candidate: candidate.fee_adjusted_edge_cents,
        default=None,
    )
    lines = [
        "LLM TRADER CONTEXT - FAKE MONEY ONLY",
        f"{context.series} {context.station} target_date={context.market_date}",
        f"observed_high={context.observed_high_so_far_f} latest_observation={context.latest_observation_time_utc}",
        (
            f"brackets={len(context.market_brackets)} buy_yes={len(buy_yes)} "
            f"buy_no={len(buy_no)} candidates={len(context.candidate_trades)} eligible={len(eligible)}"
        ),
        (
            "risk "
            f"min_edge={context.risk_limits.min_edge_cents:.2f}c "
            f"max_contracts={context.risk_limits.max_contracts_per_trade} "
            f"max_risk=${context.risk_limits.max_risk_dollars_per_trade:.2f}"
        ),
    ]
    best_text = (
        f"{best.candidate_id} edge={best.fee_adjusted_edge_cents:.2f}c"
        if best is not None
        else "-- no eligible buy candidate"
    )
    lines.append(f"best_eligible_buy={best_text}")
    lines.append("fake_money_only=True real_orders_added=False")
    return "\n".join(lines)


def _select_probability_model_key(
    probabilities_by_key: dict[str, list[dict[str, Any]]],
    requested_key: str | None,
) -> str:
    if requested_key:
        if requested_key not in probabilities_by_key:
            available = ", ".join(sorted(probabilities_by_key)) or "none"
            raise ValueError(f"probability model {requested_key!r} not found; available: {available}")
        return requested_key
    if DEFAULT_PROBABILITY_MODEL_KEY in probabilities_by_key:
        return DEFAULT_PROBABILITY_MODEL_KEY
    if probabilities_by_key:
        return sorted(probabilities_by_key)[0]
    return DEFAULT_PROBABILITY_MODEL_KEY


def _model_key(row: dict[str, Any]) -> str:
    return f"{row.get('provider')}:{row.get('model_id')}"


def _model_estimate_from_row(row: dict[str, Any]) -> ModelEstimate:
    return ModelEstimate(
        provider=_model_key(row),
        high_f=_float_or_none(row.get("settlement_high_estimate_f") or row.get("future_high_f")),
        generated_at_utc=_str_or_none(row.get("asof_utc")),
        notes=_str_or_none(row.get("model_name") or row.get("model_family")),
    )


def _probability_bin_from_row(row: dict[str, Any]) -> ProbabilityBin:
    return ProbabilityBin(
        bracket_label=str(row.get("bracket_label") or ""),
        probability=float(row.get("p_yes") or 0.0),
        lower_f=_float_or_none(row.get("bracket_lower_f")),
        upper_f=_float_or_none(row.get("bracket_upper_f")),
    )


def _market_bracket_from_row(row: dict[str, Any]) -> MarketBracket:
    ticker = str(row.get("market_ticker") or "")
    return MarketBracket(
        event_ticker=_event_ticker(ticker),
        contract_ticker=ticker,
        bracket_label=str(row.get("bracket_label") or ""),
        lower_f=_float_or_none(row.get("bracket_lower_f")),
        upper_f=_float_or_none(row.get("bracket_upper_f")),
        yes_bid_cents=_price_cents(row.get("yes_bid")),
        yes_ask_cents=_price_cents(row.get("yes_ask")),
        no_bid_cents=_price_cents(row.get("no_bid")),
        no_ask_cents=_price_cents(row.get("no_ask")),
        last_price_cents=_price_cents(row.get("last_price")),
        volume=_int_or_none(row.get("volume")),
        open_interest=_int_or_none(row.get("open_interest")),
    )


def _fake_position_from_any(row: dict[str, Any]) -> FakePosition:
    if str(row.get("position_id") or "").startswith("trader:") or "avg_entry_price_cents" in row:
        return FakePosition(
            position_id=str(row.get("position_id") or row.get("id") or ""),
            contract_ticker=str(row.get("contract_ticker") or row.get("market_ticker") or ""),
            bracket_label=str(row.get("bracket_label") or ""),
            side="YES" if str(row.get("side") or "").upper() == "YES" else "NO",
            quantity=max(0, int(_decimal_or_zero(row.get("quantity")))),
            avg_entry_price_cents=float(_decimal_or_zero(row.get("avg_entry_price_cents"))),
            opened_at_utc=_str_or_none(row.get("opened_at_utc") or row.get("created_at_utc")),
        )
    return _fake_position_from_model_race(row)


def _fake_position_from_model_race(row: dict[str, Any]) -> FakePosition:
    side = "YES" if str(row.get("side") or "").lower() == "yes" else "NO"
    return FakePosition(
        position_id=f"model_race:{row.get('id')}",
        contract_ticker=str(row.get("market_ticker") or ""),
        bracket_label=str(row.get("bracket_label") or ""),
        side=side,
        quantity=max(0, int(_decimal_or_zero(row.get("quantity")))),
        avg_entry_price_cents=float(_decimal_or_zero(row.get("entry_price")) * Decimal("100")),
        opened_at_utc=_str_or_none(row.get("entry_asof_utc") or row.get("created_utc")),
    )


def _price_cents(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int((_decimal_or_zero(value) * Decimal("100")).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def _decimal_or_zero(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _event_ticker(market_ticker: str) -> str:
    parts = market_ticker.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else market_ticker
