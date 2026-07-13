from __future__ import annotations

import time
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from rich.console import Console

from kalshi_weather.config import Settings
from kalshi_weather.data.kalshi_client import KalshiPublicClient
from kalshi_weather.data.market_discovery import filter_markets_for_date, parse_brackets_from_markets
from kalshi_weather.data.nws_client import NWSClient
from kalshi_weather.data.open_meteo_client import OpenMeteoClient, OpenMeteoForecastResult
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.lax_high_temp import (
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_STATION_ID,
    LAX_TIMEZONE,
    current_lax_market_date,
    lax_climate_day_utc,
    remaining_lax_day_local,
    weighted_future_high,
    weather_snapshot_from_frames,
)
from kalshi_weather.model.probability import (
    bracket_probabilities,
    normalize_probabilities,
    settlement_high_samples,
)
from kalshi_weather.model.outcomes import bracket_type
from kalshi_weather.time_utils import utc_now
from kalshi_weather.trading.orderbook import parse_orderbook_top
from kalshi_weather.trading.paper_broker import PaperBroker, PaperFill
from kalshi_weather.trading.risk import RiskLimits
from kalshi_weather.trading.signals import make_trade_signal, terminal_edges

console = Console()


def make_default_broker(
    settings: Settings,
    store: SQLiteStore | None = None,
    reset: bool = False,
) -> PaperBroker:
    limits = RiskLimits(
        settings.paper_max_position_per_market,
        settings.paper_max_order_cost,
        max_total_exposure=settings.max_total_exposure,
        max_contracts_per_event=settings.max_contracts_per_event,
        max_contracts_per_bracket=settings.max_contracts_per_bracket,
        max_daily_fake_loss=settings.max_daily_fake_loss,
        max_spread=settings.max_spread,
    )
    if store is not None and reset:
        store.save_paper_state_event(
            "reset",
            cash=settings.paper_starting_cash,
            realized_pnl=Decimal("0"),
            payload={"reason": "manual_reset"},
        )
        store.save_paper_equity(settings.paper_starting_cash, Decimal("0"), {"positions": {}, "reset": True})

    broker = PaperBroker(
        cash=settings.paper_starting_cash,
        limits=limits,
    )
    if store is None or reset:
        return broker

    equity = store.latest_paper_equity()
    if equity:
        broker.cash = Decimal(str(equity["cash"]))
        broker.realized_pnl = Decimal(str(equity["realized_pnl"]))
    for position in store.latest_paper_positions():
        key = (str(position["ticker"]), str(position["side"]))
        broker.positions[key] = Decimal(str(position["quantity"]))
        broker.cost_basis[key] = Decimal(str(position["average_cost"]))
    return broker


def run_paper_loop(
    settings: Settings,
    series: str,
    station: str,
    interval_seconds: int,
    max_iterations: int | None = None,
    reset_paper: bool = False,
) -> None:
    kalshi = KalshiPublicClient(settings.kalshi_api_base_url)
    nws = NWSClient(settings.user_agent, settings.nws_api_base_url)
    om = OpenMeteoClient(settings.open_meteo_base_url)
    store = SQLiteStore(settings.sqlite_path, settings.snapshot_dir)
    broker = make_default_broker(settings, store=store, reset=reset_paper)

    console.print(f"[bold]Starting paper loop[/bold] series={series} station={station}")
    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                run_paper_once(settings, kalshi, nws, om, store, broker, series, station)
            except Exception as exc:  # noqa: BLE001 - top-level runner must survive API hiccups
                console.print(f"[red]Loop error:[/red] {exc}")
            if max_iterations is not None and iteration >= max_iterations:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        console.print("Stopping paper loop.")


def run_paper_once(
    settings: Settings,
    kalshi: KalshiPublicClient,
    nws: NWSClient,
    om: OpenMeteoClient,
    store: SQLiteStore,
    broker: PaperBroker,
    series: str,
    station: str,
) -> dict[str, Any]:
    today = current_lax_market_date()
    markets = filter_markets_for_date(kalshi.get_markets(series), today)
    if not markets:
        console.print(f"No open markets found for series={series} market_date={today}")
        return {"fills": [], "snapshot_id": None, "reason": "no_markets_for_date"}
    brackets = parse_brackets_from_markets(markets)
    tickers = [b.ticker for b in brackets]

    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, ob) for ticker, ob in orderbooks.items()}
    store.save_market_snapshot(
        series,
        {"markets": markets, "orderbook_tops": {k: asdict(v) for k, v in tops.items()}},
    )

    start_utc, end_utc = lax_climate_day_utc(today)
    obs = nws.station_observations(station or LAX_STATION_ID, start_utc, min(utc_now(), end_utc))
    asof_local, end_local = remaining_lax_day_local()
    forecast_result = om.forecast_hourly_by_model(
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
        models=settings.open_meteo_models,
        variables=settings.hourly_variables,
        timezone_name=LAX_TIMEZONE,
        forecast_days=1,
        asof_local=asof_local,
        end_local=end_local,
    )
    model_maxes = forecast_result.model_maxes_f
    weather = weather_snapshot_from_frames(
        station,
        obs,
        model_maxes,
        model_details=forecast_model_details(forecast_result, settings),
    )
    store.save_weather_snapshot(station, asdict(weather))
    if weather.model_future_high_f is None:
        console.print("No model future high available; skipping trading decision.")
        return {"fills": [], "snapshot_id": None, "reason": "no_model_future_high"}

    samples = settlement_high_samples(
        future_high_center_f=weather.model_future_high_f,
        observed_high_so_far_f=weather.observed_high_so_far_f,
        residual_sigma_f=settings.residual_sigma_f,
        sample_count=settings.monte_carlo_samples,
    )
    raw_probs = bracket_probabilities(samples, brackets)
    probs = normalize_probabilities(raw_probs) if len(brackets) == len(markets) else raw_probs
    prediction_records = build_prediction_records(
        settings=settings,
        series=series,
        station=station,
        market_date=today,
        brackets=brackets,
        tops=tops,
        probs=probs,
        weather=weather,
    )
    store.save_predictions(prediction_records)

    snapshot_id = store.save_snapshot(
        "paper_once",
        {
            "markets": markets,
            "weather": asdict(weather),
            "probabilities": probs,
            "orderbook_tops": {k: asdict(v) for k, v in tops.items()},
            "open_meteo": forecast_diagnostics(forecast_result),
            "paper_cash_before": broker.cash,
            "paper_realized_pnl_before": broker.realized_pnl,
        },
    )

    fills = _exit_positions(settings, broker, tops, snapshot_id)
    fills.extend(_enter_positions(settings, broker, tops, probs, snapshot_id))
    for fill in fills:
        store.save_paper_fill(asdict(fill))

    position_keys = set(broker.positions)
    position_keys.update((fill.ticker, fill.side) for fill in fills)
    for ticker, side in position_keys:
        quantity = broker.position(ticker, side)
        store.save_paper_position(ticker, side, quantity, broker.average_cost(ticker, side))
    store.save_paper_equity(
        broker.cash,
        broker.realized_pnl,
        {"positions": {f"{ticker}:{side}": str(qty) for (ticker, side), qty in broker.positions.items()}},
    )

    if fills:
        fill_summary = ", ".join(f"{f.action.upper()} {f.side.upper()} {f.ticker}@{f.price}" for f in fills)
    else:
        fill_summary = "no trade; edge below threshold or no exit rule hit"
    console.print(
        f"snapshot={snapshot_id} observed_high={weather.observed_high_so_far_f:.1f} "
        f"model_high={weather.model_future_high_f:.1f} cash={broker.cash} "
        f"realized_pnl={broker.realized_pnl} {fill_summary}"
    )
    return {"fills": fills, "snapshot_id": snapshot_id, "probabilities": probs}


def collect_once(
    settings: Settings,
    kalshi: KalshiPublicClient,
    nws: NWSClient,
    om: OpenMeteoClient,
    store: SQLiteStore,
    series: str,
    station: str,
) -> dict[str, Any]:
    """Collect live read-only inputs and store predictions without trading."""
    today = current_lax_market_date()
    markets = filter_markets_for_date(kalshi.get_markets(series), today)
    if not markets:
        return {"stored_predictions": 0, "reason": "no_markets_for_date", "market_date": today}
    brackets = parse_brackets_from_markets(markets)
    tickers = [bracket.ticker for bracket in brackets]
    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, ob) for ticker, ob in orderbooks.items()}
    store.save_market_snapshot(series, {"markets": markets, "orderbook_tops": {k: asdict(v) for k, v in tops.items()}})

    start_utc, end_utc = lax_climate_day_utc(today)
    obs = nws.station_observations(station or LAX_STATION_ID, start_utc, min(utc_now(), end_utc))
    asof_local, end_local = remaining_lax_day_local()
    forecast_result = om.forecast_hourly_by_model(
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
        models=settings.open_meteo_models,
        variables=settings.hourly_variables,
        timezone_name=LAX_TIMEZONE,
        forecast_days=1,
        asof_local=asof_local,
        end_local=end_local,
    )
    weather = weather_snapshot_from_frames(
        station,
        obs,
        forecast_result.model_maxes_f,
        model_details=forecast_model_details(forecast_result, settings),
    )
    store.save_weather_snapshot(station, asdict(weather))
    if weather.model_future_high_f is None:
        return {"stored_predictions": 0, "reason": "no_model_future_high", "open_meteo": forecast_diagnostics(forecast_result)}

    samples = settlement_high_samples(
        future_high_center_f=weather.model_future_high_f,
        observed_high_so_far_f=weather.observed_high_so_far_f,
        residual_sigma_f=settings.residual_sigma_f,
        sample_count=settings.monte_carlo_samples,
    )
    raw_probs = bracket_probabilities(samples, brackets)
    probs = normalize_probabilities(raw_probs) if len(brackets) == len(markets) else raw_probs
    records = build_prediction_records(
        settings=settings,
        series=series,
        station=station,
        market_date=today,
        brackets=brackets,
        tops=tops,
        probs=probs,
        weather=weather,
    )
    ids = store.save_predictions(records)
    store.save_snapshot(
        "collect_once",
        {
            "markets": markets,
            "weather": asdict(weather),
            "probabilities": probs,
            "orderbook_tops": {k: asdict(v) for k, v in tops.items()},
            "open_meteo": forecast_diagnostics(forecast_result),
        },
    )
    return {
        "stored_predictions": len(ids),
        "market_count": len(markets),
        "station": station,
        "market_date": today,
        "weather": weather,
        "open_meteo": forecast_diagnostics(forecast_result),
    }


def build_prediction_records(
    settings: Settings,
    series: str,
    station: str,
    market_date: Any,
    brackets: list[Any],
    tops: dict[str, Any],
    probs: dict[str, float],
    weather: Any,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for bracket in brackets:
        top = tops.get(bracket.ticker)
        yes_edge, no_edge = terminal_edges(probs[bracket.ticker], top) if top else (None, None)
        records.append(
            {
                "asof_utc": utc_now(),
                "series": series,
                "event_ticker": None,
                "market_ticker": bracket.ticker,
                "station": station,
                "market_date": market_date,
                "bracket_label": bracket.label,
                "bracket_lower_f": bracket.lo_f,
                "bracket_upper_f": bracket.hi_f,
                "bracket_type": bracket_type(bracket.lo_f, bracket.hi_f),
                "p_yes": probs[bracket.ticker],
                "yes_bid": top.yes_bid if top else None,
                "yes_ask": top.yes_ask if top else None,
                "no_bid": top.no_bid if top else None,
                "no_ask": top.no_ask if top else None,
                "yes_edge": yes_edge,
                "no_edge": no_edge,
                "observed_high_so_far_f": weather.observed_high_so_far_f,
                "latest_observation_utc": weather.latest_observation_utc,
                "model_future_high_f": weather.model_future_high_f,
                "model_details_json": weather.model_details,
                "residual_sigma_f": settings.residual_sigma_f,
                "monte_carlo_samples": settings.monte_carlo_samples,
                "model_version": settings.default_model_version,
                "payload": {
                    "p_yes_raw": probs[bracket.ticker],
                    "p_yes_calibrated": probs[bracket.ticker],
                    "calibration_used": False,
                    "observed_high_so_far_f": weather.observed_high_so_far_f,
                    "model_future_high_f": weather.model_future_high_f,
                    "residual_sigma_f": settings.residual_sigma_f,
                    "sample_count": settings.monte_carlo_samples,
                    "model_version": settings.default_model_version,
                    "future_max_by_model": weather.model_details.get("future_max_by_model", {}),
                    "bracket_lower_f": bracket.lo_f,
                    "bracket_upper_f": bracket.hi_f,
                    "reason": _prediction_reason(
                        weather.observed_high_so_far_f, bracket.lo_f, bracket.hi_f
                    ),
                },
            }
        )
    return records


def forecast_diagnostics(result: OpenMeteoForecastResult) -> dict[str, Any]:
    return {
        "successful_models": result.successful_models,
        "failed_models": result.failed_models,
        "fallback_used": result.fallback_used,
        "model_maxes_f": result.model_maxes_f,
        "feature_summary": result.feature_summary,
        "failed_variable_requests": result.failed_variable_requests,
        "raw_columns": result.raw_columns,
    }


def forecast_model_details(result: OpenMeteoForecastResult, settings: Settings) -> dict[str, Any]:
    selected, components = weighted_future_high(result.model_maxes_f, settings.open_meteo_model_weights)
    return {
        "future_max_by_model": result.model_maxes_f,
        "selected_future_high_f": selected,
        "selected_model_components": components,
        "weights_used": settings.open_meteo_model_weights,
        "successful_models": result.successful_models,
        "failed_models": result.failed_models,
        "fallback_used": result.fallback_used,
        "feature_summary": result.feature_summary,
        "failed_variable_requests": result.failed_variable_requests,
        "raw_columns": result.raw_columns,
    }


def _prediction_reason(observed_high: float, lower_f: int | None, upper_f: int | None) -> str:
    if lower_f is not None and observed_high < lower_f:
        return "below bracket, needs warming into range"
    if upper_f is not None and observed_high > upper_f:
        return "already above bracket"
    return "already inside bracket, risk is future higher print"


def opportunity_rows(
    brackets: list[Any],
    tops: dict[str, Any],
    probs: dict[str, float],
    settings: Settings,
    min_edge: Decimal | None = None,
    fee_buffer: Decimal | None = None,
    model_error_buffer: Decimal | None = None,
) -> list[dict[str, Any]]:
    hurdle = (min_edge or settings.min_edge) + (fee_buffer or settings.fee_buffer) + (
        model_error_buffer or settings.model_error_buffer
    )
    rows: list[dict[str, Any]] = []
    for bracket in brackets:
        top = tops.get(bracket.ticker)
        p_yes = probs.get(bracket.ticker)
        yes_edge = no_edge = None
        if top is not None and p_yes is not None:
            yes_edge, no_edge = terminal_edges(p_yes, top)
        best_side = None
        best_edge = None
        if yes_edge is not None:
            best_side, best_edge = "yes", yes_edge
        if no_edge is not None and (best_edge is None or no_edge > best_edge):
            best_side, best_edge = "no", no_edge
        reason = None
        would_trade = False
        if top is None:
            reason = "missing orderbook"
        elif best_edge is None or best_side is None:
            reason = "missing executable ask"
        elif best_edge <= hurdle:
            reason = f"best edge {best_edge} <= hurdle {hurdle}"
        elif best_side == "yes" and top.yes_spread is not None and top.yes_spread > settings.max_spread:
            reason = f"YES spread {top.yes_spread} > max_spread {settings.max_spread}"
        elif best_side == "no" and top.no_spread is not None and top.no_spread > settings.max_spread:
            reason = f"NO spread {top.no_spread} > max_spread {settings.max_spread}"
        else:
            would_trade = True
        rows.append(
            {
                "ticker": bracket.ticker,
                "bracket": bracket.label,
                "p_yes": p_yes,
                "yes_bid": top.yes_bid if top else None,
                "yes_ask": top.yes_ask if top else None,
                "no_bid": top.no_bid if top else None,
                "no_ask": top.no_ask if top else None,
                "yes_edge": yes_edge,
                "no_edge": no_edge,
                "best_side": best_side,
                "best_edge": best_edge,
                "required_hurdle": hurdle,
                "would_trade": would_trade,
                "reason": reason,
            }
        )
    return sorted(rows, key=lambda row: abs(Decimal(str(row["best_edge"] or "0"))), reverse=True)


def _exit_positions(
    settings: Settings,
    broker: PaperBroker,
    tops: dict[str, Any],
    snapshot_id: int,
) -> list[PaperFill]:
    fills: list[PaperFill] = []
    for (ticker, side), quantity in list(broker.positions.items()):
        top = tops.get(ticker)
        if top is None:
            continue
        bid = top.yes_bid if side == "yes" else top.no_bid
        if bid is None:
            continue
        avg_cost = broker.average_cost(ticker, side)
        reason = None
        if bid >= avg_cost + settings.profit_target:
            reason = f"profit target hit: bid {bid} >= avg cost {avg_cost} + {settings.profit_target}"
        elif bid <= avg_cost - settings.stop_loss:
            reason = f"stop loss hit: bid {bid} <= avg cost {avg_cost} - {settings.stop_loss}"
        if reason is None:
            continue
        fill = broker.sell(ticker, side, quantity, bid, reason=reason, snapshot_id=snapshot_id)
        if fill is not None:
            fills.append(fill)
    return fills


def _enter_positions(
    settings: Settings,
    broker: PaperBroker,
    tops: dict[str, Any],
    probs: dict[str, float],
    snapshot_id: int,
) -> list[PaperFill]:
    fills: list[PaperFill] = []
    for ticker, p_yes in probs.items():
        top = tops.get(ticker)
        if top is None:
            continue
        if top.yes_ask is None and top.no_ask is None:
            continue
        if broker.position(ticker, "yes") > 0 or broker.position(ticker, "no") > 0:
            continue
        signal = make_trade_signal(
            ticker=ticker,
            p_yes=p_yes,
            top=top,
            quantity=settings.default_quantity,
            require_edge=settings.min_edge,
            fee_buffer=settings.fee_buffer,
            model_error_buffer=settings.model_error_buffer,
        )
        if signal is None:
            continue
        if signal.side == "yes" and top.yes_spread is not None and top.yes_spread > settings.max_spread:
            continue
        if signal.side == "no" and top.no_spread is not None and top.no_spread > settings.max_spread:
            continue
        fill = broker.execute_signal(signal, snapshot_id=snapshot_id)
        if fill is not None:
            fill.model_probability = Decimal(str(round(p_yes, 6)))
            fill.yes_bid = top.yes_bid
            fill.yes_ask = top.yes_ask
            fill.no_bid = top.no_bid
            fill.no_ask = top.no_ask
            fill.market_bid = top.yes_bid if fill.side == "yes" else top.no_bid
            fill.market_ask = top.yes_ask if fill.side == "yes" else top.no_ask
            fill.model_version = settings.default_model_version
            fill.asof_utc = utc_now()
            fills.append(fill)
    return fills
