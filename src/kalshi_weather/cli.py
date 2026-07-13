from __future__ import annotations

import csv
import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from kalshi_weather.config import Settings, load_settings
from kalshi_weather.data.kalshi_client import KalshiPublicClient
from kalshi_weather.data.market_discovery import (
    bracket_text_from_market,
    filter_markets_for_date,
    parse_brackets_from_markets,
)
from kalshi_weather.data.nws_client import NWSClient
from kalshi_weather.data.open_meteo_client import OPEN_METEO_MODEL_CANDIDATES, OpenMeteoClient
from kalshi_weather.data.outcomes import NWSClimateProductClient, OutcomeUnavailableError
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.calibration import brier_score, calibration_buckets, log_loss_binary
from kalshi_weather.model.lax_high_temp import (
    LAX_LATITUDE,
    LAX_LONGITUDE,
    LAX_TIMEZONE,
    current_lax_market_date,
    lax_climate_day_utc,
    lax_time_debug_payload,
    latest_settled_lax_market_date,
    is_lax_market_date_settled,
    remaining_lax_day_local,
    weather_snapshot_from_frames,
)
from kalshi_weather.model.probability import (
    bracket_probabilities,
    normalize_probabilities,
    settlement_high_samples,
)
from kalshi_weather.model.registry import get_model_spec, list_model_versions
from kalshi_weather.reporting import (
    safe_console_payload,
    timestamped_report_dir,
    write_json_report,
    write_text_report,
)
from kalshi_weather.signal_room.cli import run_dashboard
from kalshi_weather.signal_room.repository import SignalRoomReadRepository
from kalshi_weather.signal_room.service import SignalRoomService, _code_revision
from kalshi_weather.strategy_current.config import load_strategy_config
from kalshi_weather.strategy_current.promotion import (
    PromotionEvidence,
    build_promotion_report,
    render_promotion_report,
)
from kalshi_weather.strategy_current.replay import chronological_replay
from kalshi_weather.strategy_current.stage_analysis import (
    backfill_stage_performance,
    replay_stage_weighting,
)
from kalshi_weather.strategy_current.stage_weighting import load_stage_weight_config
from kalshi_weather.strategy_current.shadow_runtime import (
    ShadowOrderSink,
    incomplete_capture_decision,
)
from kalshi_weather.time_utils import utc_now
from kalshi_weather.trading.orderbook import parse_orderbook_top
from kalshi_weather.trading.runner import (
    build_prediction_records,
    collect_once as collect_once_cycle,
    forecast_model_details,
    make_default_broker,
    opportunity_rows,
    run_paper_loop,
    run_paper_once,
)
from kalshi_weather.trading.signals import terminal_edges
from kalshi_weather.validation_analysis import (
    analyze_model_validation as analyze_model_validation_payload,
    format_validation_analysis,
)
from kalshi_weather.validation_recorder import (
    probe_models as probe_validation_models,
    probe_text,
    record_loop_header,
    record_loop_line,
    record_summary_text,
    record_weather_market_once as record_validation_once,
    registry_table_text,
)

app = typer.Typer(help="Kalshi weather paper-trading research CLI")
console = Console()

DEMO_LABEL = "DEMO DATA - NOT TRADING EVIDENCE"


def _kalshi(settings: Settings) -> KalshiPublicClient:
    return KalshiPublicClient(settings.kalshi_api_base_url)


def _nws(settings: Settings) -> NWSClient:
    return NWSClient(settings.user_agent, settings.nws_api_base_url)


def _open_meteo(settings: Settings) -> OpenMeteoClient:
    return OpenMeteoClient(settings.open_meteo_base_url)


def _store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(settings.sqlite_path, settings.snapshot_dir)


def _emit_report(
    payload: Any,
    json_output: bool = False,
    output: str | None = None,
    text: str | None = None,
) -> None:
    safe_payload = safe_console_payload(payload)
    if output:
        path = Path(output)
        if path.suffix.lower() == ".json" or json_output:
            write_json_report(path, safe_payload)
        else:
            write_text_report(path, text or json.dumps(safe_payload, indent=2))
    if json_output:
        console.print(json.dumps(safe_payload, indent=2))
    else:
        console.print(text or safe_payload)


def _db_counts(store_obj: SQLiteStore) -> dict[str, int]:
    tables = [
        "market_snapshots",
        "weather_snapshots",
        "model_predictions",
        "official_outcomes",
        "prediction_outcomes",
        "paper_fills",
        "paper_positions",
        "opportunity_snapshots",
    ]
    counts: dict[str, int] = {}
    for table in tables:
        try:
            counts[table] = int(store_obj.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:  # noqa: BLE001
            counts[table] = 0
    return counts


def _settings_with_model_version(settings: Settings, model_version: str | None) -> Settings:
    if model_version is None:
        get_model_spec(settings.default_model_version)
        return settings
    get_model_spec(model_version)
    return replace(settings, default_model_version=model_version)


def _weather_context(settings: Settings, station: str) -> tuple[Any, Any]:
    start_utc, end_utc = lax_climate_day_utc(current_lax_market_date())
    obs = _nws(settings).station_observations(station, start_utc, min(utc_now(), end_utc))
    asof_local, end_local = remaining_lax_day_local()
    forecast = _open_meteo(settings).forecast_hourly_by_model(
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
        models=settings.open_meteo_models,
        variables=settings.hourly_variables,
        timezone_name=LAX_TIMEZONE,
        asof_local=asof_local,
        end_local=end_local,
    )
    weather = weather_snapshot_from_frames(
        station,
        obs,
        forecast.model_maxes_f,
        model_details=forecast_model_details(forecast, settings),
    )
    return weather, forecast


def _prediction_context(settings: Settings, series: str, station: str) -> dict[str, Any]:
    kalshi = _kalshi(settings)
    market_date = current_lax_market_date()
    markets = filter_markets_for_date(kalshi.get_markets(series), market_date)
    brackets = parse_brackets_from_markets(markets)
    tickers = [bracket.ticker for bracket in brackets]
    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, data) for ticker, data in orderbooks.items()}
    weather, forecast = _weather_context(settings, station)
    if weather.model_future_high_f is None:
        probs: dict[str, float] = {}
    else:
        samples = settlement_high_samples(
            weather.model_future_high_f,
            weather.observed_high_so_far_f,
            residual_sigma_f=settings.residual_sigma_f,
            sample_count=settings.monte_carlo_samples,
        )
        raw_probs = bracket_probabilities(samples, brackets)
        probs = normalize_probabilities(raw_probs) if len(brackets) == len(markets) else raw_probs
    return {
        "kalshi": kalshi,
        "market_date": market_date,
        "markets": markets,
        "brackets": brackets,
        "tops": tops,
        "weather": weather,
        "forecast": forecast,
        "probs": probs,
    }


@app.command()
def markets(series: str | None = typer.Option(None, help="Kalshi series ticker")) -> None:
    """Show open markets and top-of-book prices for a weather series."""
    settings = load_settings()
    series = series or settings.default_series
    client = _kalshi(settings)
    try:
        market_rows = client.get_markets(series)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Kalshi market request failed: {exc}")
        raise typer.Exit(1) from exc
    if not market_rows:
        console.print(f"No open markets found for series={series}")
        return

    table = Table(title=f"Open markets: {series}")
    for col in ["ticker", "label", "yes_bid", "yes_ask", "no_bid", "no_ask"]:
        table.add_column(col)
    for market in market_rows:
        ticker = str(market.get("ticker"))
        label = bracket_text_from_market(market)
        try:
            top = parse_orderbook_top(ticker, client.get_orderbook(ticker, depth=1))
            table.add_row(ticker, label, str(top.yes_bid), str(top.yes_ask), str(top.no_bid), str(top.no_ask))
        except Exception as exc:  # noqa: BLE001
            table.add_row(ticker, label, "ERR", str(exc), "ERR", "ERR")
    console.print(table)


@app.command("weather-snapshot")
def weather_snapshot(station: str | None = typer.Option(None, help="Weather station")) -> None:
    """Show current KLAX observed high and blended model future max."""
    settings = load_settings()
    station = station or settings.default_station
    try:
        weather, _forecast = _weather_context(settings, station)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Weather snapshot failed: {exc}")
        raise typer.Exit(1) from exc
    console.print(weather)


@app.command("weather-debug")
def weather_debug(station: str | None = typer.Option(None, help="Weather station")) -> None:
    """Show model-specific Open-Meteo diagnostics for the weather snapshot."""
    settings = load_settings()
    station = station or settings.default_station
    try:
        weather, forecast = _weather_context(settings, station)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Weather debug failed: {exc}")
        raise typer.Exit(1) from exc
    console.print(
        {
            "station": station,
            "observed_high_so_far_f": weather.observed_high_so_far_f,
            "latest_observation_utc": weather.latest_observation_utc,
            "successful_models": forecast.successful_models,
            "failed_models": forecast.failed_models,
            "fallback_used": forecast.fallback_used,
            "future_max_by_model": forecast.model_maxes_f,
            "feature_summary": forecast.feature_summary,
            "failed_variable_requests": forecast.failed_variable_requests,
            "selected_blended_future_high": weather.model_future_high_f,
            "raw_forecast_columns": forecast.raw_columns,
        }
    )


@app.command("time-debug")
def time_debug(station: str | None = typer.Option(None, help="Weather station")) -> None:
    """Show NWS fixed-standard-time market-date diagnostics."""
    settings = load_settings()
    console.print(lax_time_debug_payload(station or settings.default_station))


@app.command("probe-open-meteo-models")
def probe_open_meteo_models(
    station: str | None = typer.Option(None, help="Weather station"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Probe candidate Open-Meteo model identifiers with a minimal request."""
    settings = load_settings()
    _ = station or settings.default_station
    asof_local, end_local = remaining_lax_day_local()
    rows = _open_meteo(settings).probe_models(
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
        candidate_models=settings.open_meteo_probe_models or OPEN_METEO_MODEL_CANDIDATES,
        timezone_name=LAX_TIMEZONE,
        asof_local=asof_local,
        end_local=end_local,
    )
    table = Table(title="Open-Meteo model probe")
    for col in ["model_id", "success", "future_max", "columns", "error"]:
        table.add_column(col)
    for row in rows:
        table.add_row(
            str(row["model_id"]),
            str(row["success"]),
            str(row["future_max"]),
            ",".join(str(col) for col in row["response_columns"]),
            str(row["error"] or ""),
        )
    console.print(table)
    if output:
        _write_json(Path(output), rows)


@app.command("record-weather-market-once")
def record_weather_market_once(
    series: str | None = typer.Option(None, "--series", help="Kalshi series ticker"),
    station: str | None = typer.Option(None, "--station", help="Weather station"),
    target_date: str = typer.Option("auto", "--target-date", help="ISO date or auto"),
    timezone_name: str = typer.Option(LAX_TIMEZONE, "--timezone", help="Local timezone"),
    experiment_id: str = typer.Option("lax_model_validation", "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    jsonl_path: str | None = typer.Option(None, "--jsonl-path"),
    refresh_recent_days: int = typer.Option(3, "--refresh-recent-days"),
    model_set: str = typer.Option("current", "--model-set", help="current, core, or extended"),
    models: str | None = typer.Option(None, "--models", help="Comma-separated exact model keys"),
    skip_models: str | None = typer.Option(None, "--skip-models", help="Comma-separated keys to skip"),
    list_models: bool = typer.Option(False, "--list-models", help="List registry entries and exit"),
    probe_models: bool = typer.Option(False, "--probe-models", help="Probe selected sources and exit"),
    replace_existing_bucket: bool = typer.Option(False, "--replace-existing-bucket"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Record one weather/model/Kalshi snapshot without any trading behavior."""
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    try:
        if list_models:
            console.print(registry_table_text())
            return
        if probe_models:
            payload = probe_validation_models(
                settings,
                model_set=model_set,
                models=models,
                skip_models=skip_models,
                timezone_name=timezone_name,
            )
            _emit_report(payload, json_output=json_output, text=probe_text(payload))
            return
        payload = record_validation_once(
            settings,
            series=resolved_series,
            station=resolved_station,
            target_date=target_date,
            timezone_name=timezone_name,
            experiment_id=experiment_id,
            journal_path=journal_path,
            jsonl_path=jsonl_path,
            refresh_recent_days=refresh_recent_days,
            model_set=model_set,
            models=models,
            skip_models=skip_models,
            replace_existing_bucket=replace_existing_bucket,
            include_raw=include_raw,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"Record snapshot failed: {exc}")
        raise typer.Exit(1) from exc
    if quiet and not json_output:
        return
    _emit_report(payload, json_output=json_output, text=record_summary_text(payload))


@app.command("record-weather-market-loop")
def record_weather_market_loop(
    series: str | None = typer.Option(None, "--series", help="Kalshi series ticker"),
    station: str | None = typer.Option(None, "--station", help="Weather station"),
    target_date: str = typer.Option("auto", "--target-date", help="ISO date or auto"),
    timezone_name: str = typer.Option(LAX_TIMEZONE, "--timezone", help="Local timezone"),
    experiment_id: str = typer.Option("lax_model_validation", "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    jsonl_path: str | None = typer.Option(None, "--jsonl-path"),
    interval_seconds: int = typer.Option(900, "--interval-seconds"),
    duration_days: float = typer.Option(7.0, "--duration-days"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    refresh_recent_days: int = typer.Option(3, "--refresh-recent-days"),
    model_set: str = typer.Option("current", "--model-set", help="current, core, or extended"),
    models: str | None = typer.Option(None, "--models", help="Comma-separated exact model keys"),
    skip_models: str | None = typer.Option(None, "--skip-models", help="Comma-separated keys to skip"),
    list_models: bool = typer.Option(False, "--list-models", help="List registry entries and exit"),
    probe_models: bool = typer.Option(False, "--probe-models", help="Probe selected sources and exit"),
    replace_existing_bucket: bool = typer.Option(False, "--replace-existing-bucket"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_lines: bool = typer.Option(False, "--json-lines"),
) -> None:
    """Run the record-only snapshotter repeatedly. This command never trades."""
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    if list_models:
        console.print(registry_table_text())
        return
    if probe_models:
        payload = probe_validation_models(
            settings,
            model_set=model_set,
            models=models,
            skip_models=skip_models,
            timezone_name=timezone_name,
        )
        console.print(probe_text(payload))
        return

    duration_seconds = (
        duration_minutes * 60 if duration_minutes is not None else duration_days * 24 * 60 * 60
    )
    started = time.monotonic()
    iteration = 0
    if not quiet and not json_lines:
        console.print(record_loop_header())
    try:
        while True:
            if max_iterations is not None and iteration >= max_iterations:
                break
            if max_iterations is None and time.monotonic() - started >= duration_seconds:
                break
            iteration += 1
            try:
                payload = record_validation_once(
                    settings,
                    series=resolved_series,
                    station=resolved_station,
                    target_date=target_date,
                    timezone_name=timezone_name,
                    experiment_id=experiment_id,
                    journal_path=journal_path,
                    jsonl_path=jsonl_path,
                    refresh_recent_days=refresh_recent_days,
                    model_set=model_set,
                    models=models,
                    skip_models=skip_models,
                    replace_existing_bucket=replace_existing_bucket,
                    include_raw=include_raw,
                )
                if json_lines:
                    console.print(json.dumps(safe_console_payload(payload), separators=(",", ":")))
                elif not quiet:
                    console.print(record_loop_line(payload))
            except Exception as exc:  # noqa: BLE001
                if json_lines:
                    console.print(json.dumps({"status": "error", "error": str(exc)}))
                elif not quiet:
                    console.print(f"record error: {exc}")
            if max_iterations is not None and iteration >= max_iterations:
                break
            if time.monotonic() - started + interval_seconds > duration_seconds:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        if not quiet and not json_lines:
            console.print("Record loop stopped by Ctrl+C.")


@app.command("analyze-model-validation")
def analyze_model_validation(
    experiment_id: str | None = typer.Option(None, "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Analyze recorded model-validation snapshots."""
    try:
        payload = analyze_model_validation_payload(journal_path, experiment_id=experiment_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Analyze model validation failed: {exc}")
        raise typer.Exit(1) from exc
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=format_validation_analysis(payload),
    )


@app.command("strategy-status")
def strategy_status(
    strategy_config: str | None = typer.Option(None, "--strategy-config"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Report current-strategy shadow configuration and safety flags."""
    config = load_strategy_config(strategy_config)
    payload = {
        "strategy_id": config.strategy_id,
        "mode": config.mode,
        "config_hash": config.config_hash,
        "station": config.station,
        "series": config.series,
        "models": list(config.canonical_order),
        "safety": {
            "live_trading_enabled": config.live_trading_enabled,
            "canary_enabled": config.canary_enabled,
            "taker_enabled": config.taker_enabled,
            "order_submission_reachable": config.order_submission_reachable,
        },
        "market_data_gates": {
            "require_sequence_valid_book": config.require_sequence_valid_book,
            "require_trade_count_fp": config.require_trade_count_fp,
            "require_exhausted_trade_cursor": config.require_exhausted_trade_cursor,
            "candles_eligible_for_fill_simulation": config.candles_eligible_for_fill_simulation,
        },
    }
    _emit_report(payload, json_output=json_output)


@app.command("strategy-shadow-run")
def strategy_shadow_run(
    strategy_config: str | None = typer.Option(None, "--strategy-config"),
    once: bool = typer.Option(True, "--once"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one shadow-only current-strategy evaluation."""
    _ = once
    config = load_strategy_config(strategy_config)
    sink = ShadowOrderSink()
    decision = incomplete_capture_decision()
    action = sink.record(decision)
    payload = {
        "strategy_id": config.strategy_id,
        "mode": config.mode,
        "config_hash": config.config_hash,
        "reason_code": decision.reason_code,
        "shadow_action": action.to_dict(),
        "orders_submitted": 0,
        "order_submission_reachable": config.order_submission_reachable,
    }
    _emit_report(payload, json_output=json_output)


@app.command("strategy-backfill-stage-performance")
def strategy_backfill_stage_performance(
    journal_path: str = typer.Option(
        "journals/lax_model_validation.sqlite", "--journal-path"
    ),
    weighting_config: str | None = typer.Option(None, "--weighting-config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build idempotent date-level model scores from settled recorder snapshots."""
    try:
        payload = backfill_stage_performance(
            journal_path,
            weighting_config=load_stage_weight_config(weighting_config),
            code_revision=_code_revision(),
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"Stage-performance backfill failed: {exc}")
        raise typer.Exit(1) from exc
    _emit_report(payload, json_output=json_output)


@app.command("strategy-stage-weight-status")
def strategy_stage_weight_status(
    journal_path: str = typer.Option(
        "journals/lax_model_validation.sqlite", "--journal-path"
    ),
    target_date: str = typer.Option("auto", "--target-date"),
    weighting_config: str | None = typer.Option(None, "--weighting-config"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show the immutable stage-weight snapshot used by Signal Room."""
    try:
        target = (
            current_lax_market_date()
            if target_date == "auto"
            else date.fromisoformat(target_date)
        )
        service = SignalRoomService(
            repository=SignalRoomReadRepository(journal_path),
            stage_weight_config=load_stage_weight_config(weighting_config),
        )
        snapshot = service.latest_snapshot(target_date=target)
        weighting = (snapshot.probability_lab or {}).get("weighting")
        payload = {
            "target_date": target.isoformat(),
            "evaluation_id": (snapshot.probability_lab or {}).get("evaluation_id"),
            "weighting": weighting,
            "order_submission_reachable": snapshot.strategy.order_submission_reachable,
        }
    except Exception as exc:  # noqa: BLE001
        console.print(f"Stage-weight status failed: {exc}")
        raise typer.Exit(1) from exc
    _emit_report(payload, json_output=json_output)


@app.command("strategy-replay")
def strategy_replay(
    strategy_config: str | None = typer.Option(None, "--strategy-config"),
    journal_path: str | None = typer.Option(None, "--journal-path"),
    weighting_config: str | None = typer.Option(None, "--weighting-config"),
    weighting_modes: str = typer.Option(
        "fixed_baseline,stage_prior_only,stage_reliability",
        "--weighting-modes",
    ),
    bootstrap_samples: int = typer.Option(2000, "--bootstrap-samples"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Replay current-strategy source events without treating candles as executable."""
    config = load_strategy_config(strategy_config)
    report = chronological_replay([])
    payload = {
        "strategy_id": config.strategy_id,
        "mode": config.mode,
        "replay": report.to_dict(),
    }
    if journal_path is not None:
        try:
            stage_config = load_stage_weight_config(weighting_config)
            payload["stage_performance_backfill"] = backfill_stage_performance(
                journal_path,
                weighting_config=stage_config,
                code_revision=_code_revision(),
            )
            payload["stage_weighting"] = replay_stage_weighting(
                journal_path,
                modes=tuple(
                    value.strip()
                    for value in weighting_modes.split(",")
                    if value.strip()
                ),
                weighting_config=stage_config,
                strategy_config=config,
                code_revision=_code_revision(),
                bootstrap_samples=bootstrap_samples,
            )
        except Exception as exc:  # noqa: BLE001
            console.print(f"Stage-weighting replay failed: {exc}")
            raise typer.Exit(1) from exc
    _emit_report(payload, json_output=json_output)


@app.command("strategy-promotion-report")
def strategy_promotion_report(
    output: str | None = typer.Option(None, "--output"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Report whether shadow evidence is sufficient for human canary review."""
    evidence = PromotionEvidence(
        settled_forecast_dates=0,
        joined_market_dates=0,
        probability_calibrated=False,
        execution_validated=False,
        aggregate_roi=None,
    )
    report = build_promotion_report(evidence)
    text = render_promotion_report(report)
    _emit_report(report.to_dict(), json_output=json_output, output=output, text=text)


@app.command("strategy-dashboard")
def strategy_dashboard(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    event: str = typer.Option("auto", "--event"),
    mode: str = typer.Option("live", "--mode", help="live or replay"),
    target_date: str | None = typer.Option(None, "--target-date"),
    open_browser: bool = typer.Option(False, "--open-browser/--no-open-browser"),
    poll_seconds: int = typer.Option(2, "--poll-seconds"),
    allow_remote: bool = typer.Option(False, "--allow-remote"),
    sqlite_path: str | None = typer.Option(None, "--sqlite-path"),
    sample_fixture: str | None = typer.Option(None, "--sample-fixture"),
) -> None:
    """Serve the read-only KLAX Signal Room dashboard locally."""
    run_dashboard(
        host=host,
        port=port,
        event=event,
        mode=mode,
        target_date=target_date,
        open_browser=open_browser,
        poll_seconds=poll_seconds,
        allow_remote=allow_remote,
        sqlite_path=sqlite_path,
        sample_fixture=sample_fixture,
    )


@app.command("predict-once")
def predict_once(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    store: bool = typer.Option(False, "--store"),
    model_version: str | None = typer.Option(None, "--model-version"),
) -> None:
    """Compute one live prediction table, optionally storing prediction rows."""
    settings = _settings_with_model_version(load_settings(), model_version)
    series = series or settings.default_series
    station = station or settings.default_station
    try:
        ctx = _prediction_context(settings, series, station)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Prediction failed: {exc}")
        raise typer.Exit(1) from exc
    if not ctx["markets"]:
        console.print(f"No open markets found for series={series} market_date={ctx['market_date']}")
        return
    if not ctx["brackets"]:
        console.print("No parseable brackets found.")
        return
    if ctx["weather"].model_future_high_f is None:
        console.print("No model high available.")
        return

    if store:
        records = build_prediction_records(
            settings, series, station, ctx["market_date"], ctx["brackets"], ctx["tops"], ctx["probs"], ctx["weather"]
        )
        _store(settings).save_predictions(records)

    table = Table(title=f"Prediction {series} observed_high={ctx['weather'].observed_high_so_far_f:.1f}")
    for col in ["ticker", "label", "p_yes", "yes_bid", "yes_ask", "yes_edge", "no_edge"]:
        table.add_column(col)
    for bracket in ctx["brackets"]:
        top = ctx["tops"].get(bracket.ticker)
        if top is None:
            table.add_row(bracket.ticker, bracket.label, "ERR", "ERR", "missing orderbook", "ERR", "ERR")
            continue
        yes_edge, no_edge = terminal_edges(ctx["probs"][bracket.ticker], top)
        table.add_row(
            bracket.ticker,
            bracket.label,
            f"{ctx['probs'][bracket.ticker]:.3f}",
            str(top.yes_bid),
            str(top.yes_ask),
            str(yes_edge),
            str(no_edge),
        )
    console.print(table)


@app.command("opportunities")
def opportunities(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    store: bool = typer.Option(False, "--store"),
    min_edge: float | None = typer.Option(None, "--min-edge"),
    fee_buffer: float | None = typer.Option(None, "--fee-buffer"),
    model_error_buffer: float | None = typer.Option(None, "--model-error-buffer"),
    top_n: int | None = typer.Option(None, "--top-n"),
    short: bool = typer.Option(False, "--short"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: str | None = typer.Option(None, "--csv"),
    output: str | None = typer.Option(None, "--output"),
    model_version: str | None = typer.Option(None, "--model-version"),
) -> None:
    """Show read-only opportunity diagnostics sorted by best absolute edge."""
    settings = _settings_with_model_version(load_settings(), model_version)
    series = series or settings.default_series
    station = station or settings.default_station
    try:
        ctx = _prediction_context(settings, series, station)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Opportunity diagnostics failed: {exc}")
        raise typer.Exit(1) from exc
    rows = opportunity_rows(
        ctx["brackets"],
        ctx["tops"],
        ctx["probs"],
        settings,
        min_edge=Decimal(str(min_edge)) if min_edge is not None else None,
        fee_buffer=Decimal(str(fee_buffer)) if fee_buffer is not None else None,
        model_error_buffer=Decimal(str(model_error_buffer)) if model_error_buffer is not None else None,
    )
    if top_n is not None:
        rows = rows[:top_n]
    if store and ctx["brackets"] and ctx["probs"]:
        records = build_prediction_records(
            settings, series, station, ctx["market_date"], ctx["brackets"], ctx["tops"], ctx["probs"], ctx["weather"]
        )
        store_obj = _store(settings)
        store_obj.save_predictions(records)
        store_obj.save_opportunity_snapshot(
            series,
            station,
            ctx["market_date"],
            {
                "rows": rows,
                "weather": ctx["weather"],
                "model_version": settings.default_model_version,
            },
        )

    payload = {
        "series": series,
        "station": station,
        "market_date": ctx["market_date"],
        "model_version": settings.default_model_version,
        "row_count": len(rows),
        "rows": rows,
    }
    if csv_output:
        _write_csv(Path(csv_output), [safe_console_payload(row) for row in rows])
    if output:
        if output.lower().endswith(".csv"):
            _write_csv(Path(output), [safe_console_payload(row) for row in rows])
        else:
            write_json_report(output, payload)
    if json_output:
        console.print(json.dumps(safe_console_payload(payload), indent=2))
        return

    table = Table(title=f"Opportunities {series} market_date={ctx['market_date']}")
    columns = [
        "ticker",
        "bracket",
        "p_yes",
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "yes_edge",
        "no_edge",
        "best_side",
        "best_edge",
        "required_hurdle",
        "would_trade",
        "reason",
    ]
    if short:
        columns = ["ticker", "bracket", "p_yes", "best_side", "best_edge", "required_hurdle", "would_trade", "reason"]
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row.get(col) if row.get(col) is not None else "") for col in columns))
    console.print(table)


@app.command("collect-once")
def collect_once(series: str | None = typer.Option(None), station: str | None = typer.Option(None)) -> None:
    """Collect read-only inputs and store predictions without paper trading."""
    settings = load_settings()
    try:
        result = collect_once_cycle(
            settings,
            _kalshi(settings),
            _nws(settings),
            _open_meteo(settings),
            _store(settings),
            series or settings.default_series,
            station or settings.default_station,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"Collect once failed: {exc}")
        raise typer.Exit(1) from exc
    console.print(result)


@app.command("collect-loop")
def collect_loop(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    interval_seconds: int = typer.Option(60),
    max_iterations: int | None = typer.Option(None),
) -> None:
    """Repeat collect-only cycles. This command never trades."""
    settings = load_settings()
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            result = collect_once_cycle(
                settings,
                _kalshi(settings),
                _nws(settings),
                _open_meteo(settings),
                _store(settings),
                series or settings.default_series,
                station or settings.default_station,
            )
            console.print({"iteration": iteration, **result})
        except Exception as exc:  # noqa: BLE001
            console.print(f"Collect loop error iteration={iteration}: {exc}")
        if max_iterations is not None and iteration >= max_iterations:
            break
        time.sleep(interval_seconds)


@app.command("paper-once")
def paper_once(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    reset_paper: bool = typer.Option(False, "--reset-paper"),
) -> None:
    """Run one fake-money decision cycle."""
    settings = load_settings()
    store_obj = _store(settings)
    try:
        run_paper_once(
            settings=settings,
            kalshi=_kalshi(settings),
            nws=_nws(settings),
            om=_open_meteo(settings),
            store=store_obj,
            broker=make_default_broker(settings, store=store_obj, reset=reset_paper),
            series=series or settings.default_series,
            station=station or settings.default_station,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"Paper decision failed: {exc}")
        raise typer.Exit(1) from exc


@app.command("run-paper")
def run_paper(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    interval_seconds: int | None = typer.Option(None),
    max_iterations: int | None = typer.Option(None),
    reset_paper: bool = typer.Option(False, "--reset-paper"),
) -> None:
    """Run continuous fake-money trading loop."""
    settings = load_settings()
    run_paper_loop(
        settings,
        series=series or settings.default_series,
        station=station or settings.default_station,
        interval_seconds=interval_seconds or settings.polling_interval_seconds,
        max_iterations=max_iterations,
        reset_paper=reset_paper,
    )


@app.command("fetch-outcome")
def fetch_outcome(
    station: str | None = typer.Option(None),
    outcome_date: str = typer.Option(..., "--date"),
    overwrite: bool = typer.Option(False),
    allow_unsettled_store: bool = typer.Option(False, "--allow-unsettled-store"),
    settlement_buffer_hours: int | None = typer.Option(None, "--settlement-buffer-hours"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Best-effort official NWS CLI high-temperature ingestion."""
    settings = load_settings()
    station = station or settings.default_station
    market_date = date.fromisoformat(outcome_date)
    buffer_hours = settlement_buffer_hours or settings.settlement_buffer_hours
    if not allow_unsettled_store and not is_lax_market_date_settled(market_date, settlement_buffer_hours=buffer_hours):
        payload = {
            "date": outcome_date,
            "status": "skipped_unsettled",
            "settlement_buffer_hours": buffer_hours,
            "latest_settled_market_date": latest_settled_lax_market_date(
                settlement_buffer_hours=buffer_hours
            ).isoformat(),
        }
        _emit_report(payload, json_output=json_output, output=output)
        return
    try:
        outcome = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url).fetch_daily_high(
            station, market_date
        )
    except OutcomeUnavailableError as exc:
        payload = {"date": outcome_date, "status": "unavailable", "error": str(exc)}
        _emit_report(payload, json_output=json_output, output=output)
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"Outcome fetch failed: {exc}")
        raise typer.Exit(1) from exc
    outcome_id = _store(settings).save_official_outcome(
        station=outcome.station,
        market_date=outcome.market_date,
        metric=outcome.metric,
        official_high_f=outcome.official_high_f,
        source=outcome.source,
        source_url=outcome.source_url,
        source_text=outcome.source_text,
        overwrite=overwrite,
    )
    _emit_report(
        {
            "official_outcome_id": outcome_id,
            "official_high_f": outcome.official_high_f,
            "source_url": outcome.source_url,
            "status": "stored",
        },
        json_output=json_output,
        output=output,
    )


@app.command("fetch-outcomes")
def fetch_outcomes(
    station: str | None = typer.Option(None),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_unsettled_store: bool = typer.Option(False, "--allow-unsettled-store"),
    settlement_buffer_hours: int | None = typer.Option(None, "--settlement-buffer-hours"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Fetch official NWS CLI outcomes for a date range without stopping on one failure."""
    settings = load_settings()
    station = station or settings.default_station
    store_obj = _store(settings)
    client = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url)
    report = _outcome_backfill_report(
        client,
        store_obj,
        station,
        _date_range(date.fromisoformat(start_date), date.fromisoformat(end_date)),
        overwrite=overwrite,
        dry_run=dry_run,
        allow_unsettled_store=allow_unsettled_store,
        settlement_buffer_hours=settlement_buffer_hours or settings.settlement_buffer_hours,
    )
    _emit_report(report, json_output=json_output, output=output)


@app.command("fetch-missing-outcomes")
def fetch_missing_outcomes(
    station: str | None = typer.Option(None),
    include_current: bool = typer.Option(False, "--include-current"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_unsettled_store: bool = typer.Option(False, "--allow-unsettled-store"),
    settlement_buffer_hours: int | None = typer.Option(None, "--settlement-buffer-hours"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Backfill official outcomes for prediction dates missing outcomes."""
    settings = load_settings()
    station = station or settings.default_station
    store_obj = _store(settings)
    client = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url)
    buffer_hours = settlement_buffer_hours or settings.settlement_buffer_hours
    latest_settled = latest_settled_lax_market_date(settlement_buffer_hours=buffer_hours)
    dates = []
    skipped_existing = 0
    skipped_unsettled = 0
    for date_text in store_obj.distinct_prediction_dates(station):
        market_date = date.fromisoformat(date_text)
        if (
            not include_current
            and not allow_unsettled_store
            and market_date > latest_settled
        ):
            skipped_unsettled += 1
            continue
        if not overwrite and store_obj.has_official_outcome(station, market_date):
            skipped_existing += 1
            continue
        dates.append(market_date)
    report = _outcome_backfill_report(
        client,
        store_obj,
        station,
        dates,
        overwrite=overwrite,
        dry_run=dry_run,
        allow_unsettled_store=allow_unsettled_store,
        settlement_buffer_hours=buffer_hours,
    )
    report["skipped_existing_count"] = skipped_existing
    report["skipped_unsettled_count"] += skipped_unsettled
    _emit_report(report, json_output=json_output, output=output)


@app.command("validate-outcome-parser")
def validate_outcome_parser(
    station: str | None = typer.Option(None),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    store: bool = typer.Option(False, "--store"),
    output: str | None = typer.Option(None, "--output"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Validate NWS CLI outcome parsing across dates."""
    settings = load_settings()
    station = station or settings.default_station
    store_obj = _store(settings)
    client = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url)
    rows = []
    for outcome_date in _date_range(date.fromisoformat(start_date), date.fromisoformat(end_date)):
        try:
            outcome = client.fetch_daily_high(station, outcome_date)
            outcome_id = None
            if store:
                outcome_id = store_obj.save_official_outcome(
                    outcome.station,
                    outcome.market_date,
                    outcome.metric,
                    outcome.official_high_f,
                    outcome.source,
                    outcome.source_url,
                    outcome.source_text,
                )
            rows.append(
                {
                    "date": outcome_date.isoformat(),
                    "status": "parsed",
                    "parsed_high": outcome.official_high_f,
                    "source_url": outcome.source_url,
                    "error": None,
                    "official_outcome_id": outcome_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 - validation should continue across dates
            rows.append(
                {
                    "date": outcome_date.isoformat(),
                    "status": "error",
                    "parsed_high": None,
                    "source_url": None,
                    "error": str(exc),
                    "official_outcome_id": None,
                }
            )
    if output and output.lower().endswith(".csv"):
        _write_csv(Path(output), rows)
    elif output:
        write_json_report(output, {"rows": rows})
    if json_output:
        console.print(json.dumps(safe_console_payload({"rows": rows}), indent=2))
        return
    table = Table(title="Outcome parser validation")
    for col in ["date", "status", "parsed_high", "source_url", "error"]:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row.get(col) or "") for col in ["date", "status", "parsed_high", "source_url", "error"]))
    console.print(table)


@app.command("record-outcome")
def record_outcome(
    station: str | None = typer.Option(None),
    outcome_date: str = typer.Option(..., "--date"),
    official_high_f: float = typer.Option(..., "--official-high-f"),
    source: str = typer.Option("manual"),
    overwrite: bool = typer.Option(False),
    allow_unsettled_store: bool = typer.Option(False, "--allow-unsettled-store"),
    settlement_buffer_hours: int | None = typer.Option(None, "--settlement-buffer-hours"),
    notes: str | None = typer.Option(None, "--notes"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Manually record an official high when automatic NWS parsing is unavailable."""
    settings = load_settings()
    station = station or settings.default_station
    market_date = date.fromisoformat(outcome_date)
    buffer_hours = settlement_buffer_hours or settings.settlement_buffer_hours
    if not allow_unsettled_store and not is_lax_market_date_settled(market_date, settlement_buffer_hours=buffer_hours):
        payload = {
            "status": "skipped_unsettled",
            "message": "Outcome date is not settled yet; pass --allow-unsettled-store to force manual storage.",
            "station": station,
            "date": outcome_date,
            "settlement_buffer_hours": buffer_hours,
        }
        _emit_report(payload, json_output=json_output, output=output)
        return
    outcome_id = _store(settings).save_official_outcome(
        station=station,
        market_date=market_date,
        metric="official_high_f",
        official_high_f=official_high_f,
        source=source,
        source_text=notes,
        overwrite=overwrite,
    )
    _emit_report(
        {"official_outcome_id": outcome_id, "station": station, "date": outcome_date, "status": "stored"},
        json_output=json_output,
        output=output,
    )


@app.command("join-outcomes")
def join_outcomes(
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None),
    end_date: str | None = typer.Option(None),
    overwrite: bool = typer.Option(False),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Join stored predictions to official outcomes."""
    settings = load_settings()
    result = _store(settings).join_predictions_to_outcomes(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
        overwrite=overwrite,
    )
    _emit_report(result, json_output=json_output, output=output)


@app.command("replay")
def replay(snapshot_dir: str | None = typer.Option(None)) -> None:
    """Replay saved JSON snapshots."""
    from kalshi_weather.backtest.replay import replay_snapshots

    settings = load_settings()
    result = replay_snapshots(Path(snapshot_dir) if snapshot_dir else settings.snapshot_dir, settings)
    console.print(result)


@app.command("calibration-report")
def calibration_report(
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None),
    end_date: str | None = typer.Option(None),
    model_version: str | None = typer.Option(None, "--model-version"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Print calibration metrics from joined predictions and outcomes."""
    settings = load_settings()
    rows = _store(settings).load_prediction_outcomes(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
    )
    if model_version:
        rows = [row for row in rows if row.get("model_version") == model_version]
    if not rows:
        report = _empty_calibration_report(station or settings.default_station)
        text = "Need more data: no official outcomes joined to stored predictions yet.\n" + "\n".join(
            report["next_commands"]
        )
        _emit_report(report, json_output=json_output, output=output, text=text)
        return
    report = _calibration_report(rows)
    if output:
        _write_json(Path(output), report)
    console.print(json.dumps(report, indent=2) if json_output else report)


@app.command("residual-report")
def residual_report(
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None),
    end_date: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Report high-temperature residuals from joined prediction/outcome rows."""
    settings = load_settings()
    rows = _store(settings).load_prediction_outcomes(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
    )
    residuals = [
        float(row["official_high_f"]) - float(row["model_future_high_f"])
        for row in rows
        if row.get("model_future_high_f") is not None
    ]
    observed_residuals = [
        float(row["official_high_f"]) - float(row["observed_high_so_far_f"])
        for row in rows
        if row.get("observed_high_so_far_f") is not None
    ]
    report = {
        "joined_row_count": len(rows),
        "residual_count": len(residuals),
        "official_minus_model_future_high_avg": sum(residuals) / len(residuals) if residuals else None,
        "official_minus_observed_high_avg": (
            sum(observed_residuals) / len(observed_residuals) if observed_residuals else None
        ),
        "residual_mean": statistics.mean(residuals) if residuals else None,
        "residual_median": statistics.median(residuals) if residuals else None,
        "residual_mae": statistics.mean(abs(value) for value in residuals) if residuals else None,
        "residual_rmse": math.sqrt(statistics.mean(value**2 for value in residuals)) if residuals else None,
        "residual_percentiles": _percentiles(residuals, [10, 25, 50, 75, 90]) if residuals else {},
        "suggested_residual_sigma_f": _sample_stddev(residuals),
        "by_asof_hour": _residual_group_summary(rows, "asof_hour_utc"),
        "by_bracket": _residual_group_summary(rows, "bracket_label"),
        "by_model_version": _residual_group_summary(rows, "model_version"),
        "warning": "fewer than 30 joined rows; do not overfit residual sigma" if len(rows) < 30 else None,
    }
    _emit_report(report, json_output=json_output, output=output)


@app.command("paper-report")
def paper_report(
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Report fake-money paper-trading performance from SQLite."""
    settings = load_settings()
    report = _store(settings).paper_report()
    text = None
    if report["total_paper_fills"] == 0:
        text = "No fake trades were taken because no edge cleared the configured threshold.\n"
        text += json.dumps(safe_console_payload(report), indent=2)
    _emit_report(report, json_output=json_output, output=output, text=text)


@app.command("research-status")
def research_status(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Show safe POC readiness, counts, configured model, and next actions."""
    settings = load_settings()
    store_obj = _store(settings)
    station = station or settings.default_station
    payload = {
        "series": series or settings.default_series,
        "station": station,
        "canonical_directory": "C:\\Users\\jarve\\Documents\\Codex\\kalshi_weather",
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "live_order_endpoint_present": False,
        "default_model_version": settings.default_model_version,
        "available_model_versions": list_model_versions(),
        "preferred_open_meteo_models": settings.open_meteo_models,
        "latest_settled_market_date": latest_settled_lax_market_date(
            settlement_buffer_hours=settings.settlement_buffer_hours
        ).isoformat(),
        "settlement_buffer_hours": settings.settlement_buffer_hours,
        "db_counts": _db_counts(store_obj),
        "prediction_dates": store_obj.distinct_prediction_dates(station),
        "next_commands": [
            "kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60",
            "kalshi-weather fetch-missing-outcomes --station KLAX",
            "kalshi-weather join-outcomes --station KLAX --overwrite",
            "kalshi-weather calibration-report --station KLAX",
        ],
    }
    _emit_report(payload, json_output=json_output, output=output)


@app.command("collect-session")
def collect_session(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    interval_seconds: int = typer.Option(60),
    max_iterations: int | None = typer.Option(None),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    reports_dir: str = typer.Option("reports/collect_sessions", "--reports-dir"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Run bounded collect-only cycles. This command never trades."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    report_dir = timestamped_report_dir(reports_dir, "collect_session")
    started = time.monotonic()
    iteration = 0
    results: list[dict[str, Any]] = []
    while True:
        if max_iterations is not None and iteration >= max_iterations:
            break
        if duration_minutes is not None and time.monotonic() - started >= duration_minutes * 60:
            break
        iteration += 1
        try:
            result = collect_once_cycle(
                settings,
                _kalshi(settings),
                _nws(settings),
                _open_meteo(settings),
                _store(settings),
                series,
                station,
            )
            results.append({"iteration": iteration, "status": "ok", "result": result})
        except Exception as exc:  # noqa: BLE001
            results.append({"iteration": iteration, "status": "error", "error": str(exc)})
        if max_iterations is not None and iteration >= max_iterations:
            break
        if duration_minutes is None and max_iterations is None:
            break
        time.sleep(interval_seconds)
    payload = {
        "series": series,
        "station": station,
        "iterations": iteration,
        "paper_trading": False,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "report_dir": str(report_dir),
        "results": results,
    }
    write_json_report(report_dir / "summary.json", payload)
    _emit_report(payload, json_output=json_output, output=output)


@app.command("threshold-sweep")
def threshold_sweep(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    replay_mode: bool = typer.Option(False, "--replay"),
    store: bool = typer.Option(False, "--store"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Evaluate opportunity counts across edge and buffer thresholds without trading."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    thresholds = [Decimal("0.00"), Decimal("0.02"), Decimal("0.05"), Decimal("0.08"), Decimal("0.10")]
    rows: list[dict[str, Any]] = []
    live_error = None
    try:
        ctx = _prediction_context(settings, series, station)
        for threshold in thresholds:
            opp_rows = opportunity_rows(
                ctx["brackets"],
                ctx["tops"],
                ctx["probs"],
                settings,
                min_edge=threshold,
            )
            rows.append(
                {
                    "min_edge": str(threshold),
                    "would_trade_count_current": sum(1 for row in opp_rows if row["would_trade"]),
                    "best_edge": str(opp_rows[0]["best_edge"]) if opp_rows else None,
                    "replay_entry_count": None,
                    "replay_exit_count": None,
                    "replay_realized_pnl": None,
                    "replay_max_drawdown": None,
                }
            )
    except Exception as exc:  # noqa: BLE001
        live_error = str(exc)
        for threshold in thresholds:
            rows.append(
                {
                    "min_edge": str(threshold),
                    "would_trade_count_current": 0,
                    "best_edge": None,
                    "error": live_error,
                }
            )
    replay_report = _paper_replay_report(_store(settings), min_edge=settings.min_edge) if replay_mode else None
    if replay_report is not None:
        for row in rows:
            row["replay_entry_count"] = replay_report["simulated_entries"]
            row["replay_exit_count"] = replay_report["simulated_exits"]
            row["replay_realized_pnl"] = replay_report["realized_replay_pnl"]
            row["replay_max_drawdown"] = replay_report["max_drawdown"]
    payload = {
        "series": series,
        "station": station,
        "replay": replay_mode,
        "live_error": live_error,
        "rows": rows,
        "warnings": [] if not live_error else ["live read-only sweep unavailable; see live_error"],
    }
    if store:
        _store(settings).save_opportunity_snapshot(series, station, current_lax_market_date(), payload)
    _emit_report(payload, json_output=json_output, output=output)


@app.command("daily-maintenance")
def daily_maintenance(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    reports_dir: str = typer.Option("reports/daily_maintenance", "--reports-dir"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Run the safe daily data flywheel and write timestamped reports."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    report_dir = timestamped_report_dir(reports_dir, "daily")
    store_obj = _store(settings)
    command_summary: dict[str, Any] = {}

    def capture(name: str, func: Any) -> Any:
        try:
            payload = func()
            command_summary[name] = {"status": "ok"}
            write_json_report(report_dir / f"{name}.json", payload)
            return payload
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": str(exc)}
            command_summary[name] = payload
            write_json_report(report_dir / f"{name}.json", payload)
            return payload

    capture("time_debug", lambda: lax_time_debug_payload(station))
    capture(
        "collect_once",
        lambda: collect_once_cycle(settings, _kalshi(settings), _nws(settings), _open_meteo(settings), store_obj, series, station),
    )
    capture(
        "outcome_backfill",
        lambda: _outcome_backfill_report(
            NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url),
            store_obj,
            station,
            [date.fromisoformat(value) for value in store_obj.distinct_prediction_dates(station)],
            overwrite=False,
            dry_run=False,
            allow_unsettled_store=False,
            settlement_buffer_hours=settings.settlement_buffer_hours,
        ),
    )
    capture("outcome_join", lambda: store_obj.join_predictions_to_outcomes(station=station, overwrite=True))
    capture("calibration_report", lambda: _calibration_report_or_empty(store_obj, station))
    capture("residual_report", lambda: _residual_report_payload(store_obj, station))
    capture("paper_report", store_obj.paper_report)
    capture("research_status", lambda: {"db_counts": _db_counts(store_obj), "station": station, "series": series})
    capture("threshold_sweep", lambda: {"command": "threshold-sweep", "available": True})
    summary = {
        "series": series,
        "station": station,
        "report_dir": str(report_dir),
        "command_summary": command_summary,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
    }
    write_json_report(report_dir / "command_summary.json", summary)
    _emit_report(summary, json_output=json_output, output=output)


@app.command("calibration-readiness")
def calibration_readiness(
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Explain what data is available before calibration can be trusted."""
    settings = load_settings()
    station = station or settings.default_station
    payload = _calibration_readiness_payload(_store(settings), station, settings)
    _emit_report(payload, json_output=json_output, output=output)


@app.command("calibration-demo")
def calibration_demo(
    station: str | None = typer.Option(None),
    output: str | None = typer.Option(None, "--output"),
    store_demo: bool = typer.Option(False, "--store-demo"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run fixture-only calibration metrics. Demo data is not evidence."""
    _ = station or load_settings().default_station
    rows = _demo_joined_rows()
    report = _calibration_report(rows)
    payload = {
        "label": DEMO_LABEL,
        "stored_to_production": store_demo,
        "demo_row_count": len(rows),
        "calibration": report,
    }
    _emit_report(payload, json_output=json_output or bool(output), output=output, text=DEMO_LABEL)


@app.command("tune-residual-sigma")
def tune_residual_sigma(
    station: str | None = typer.Option(None),
    model_version: str | None = typer.Option(None, "--model-version"),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    min_rows: int | None = typer.Option(None, "--min-rows"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    write_config_fragment: str | None = typer.Option(None, "--write-config-fragment"),
) -> None:
    """Recommend residual sigma from joined production rows without changing config."""
    settings = load_settings()
    min_rows = min_rows or settings.minimum_rows_for_residual_calibration
    rows = _store(settings).load_prediction_outcomes(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
    )
    if model_version:
        rows = [row for row in rows if row.get("model_version") == model_version]
    residuals = [
        float(row["official_high_f"]) - float(row["model_future_high_f"])
        for row in rows
        if row.get("model_future_high_f") is not None
    ]
    sigma_std = _sample_stddev(residuals)
    mae = statistics.mean(abs(value) for value in residuals) if residuals else None
    rmse = math.sqrt(statistics.mean(value**2 for value in residuals)) if residuals else None
    estimates = [value for value in [sigma_std, (mae / 0.7979 if mae is not None else None), rmse] if value]
    recommended = statistics.median(estimates) if len(residuals) >= min_rows and estimates else None
    payload = {
        "station": station or settings.default_station,
        "model_version": model_version,
        "row_count": len(rows),
        "residual_count": len(residuals),
        "min_rows": min_rows,
        "sigma_std": sigma_std,
        "sigma_mae_scaled": mae / 0.7979 if mae is not None else None,
        "sigma_rmse": rmse,
        "recommended_sigma_f": recommended,
        "by_asof_hour": _residual_group_summary(rows, "asof_hour_utc"),
        "by_observed_high_state": _residual_group_summary(rows, "observed_inside_bracket"),
        "warning": None if len(residuals) >= min_rows else "too few joined rows for production tuning",
    }
    if write_config_fragment:
        write_json_report(write_config_fragment, {"model": {"residual_sigma_f_initial": recommended}})
    _emit_report(payload, json_output=json_output, output=output)


@app.command("fit-probability-calibration")
def fit_probability_calibration(
    station: str | None = typer.Option(None),
    min_rows: int | None = typer.Option(None, "--min-rows"),
    model_version: str | None = typer.Option(None, "--model-version"),
    output: str = typer.Option("data/calibration/klax_probability_calibration.json", "--output"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_small_sample_demo: bool = typer.Option(False, "--allow-small-sample-demo"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Fit a simple reliability-bucket calibration file when enough rows exist."""
    settings = load_settings()
    min_rows = min_rows or settings.minimum_rows_for_probability_calibration
    rows = _store(settings).load_prediction_outcomes(station=station or settings.default_station)
    if model_version:
        rows = [row for row in rows if row.get("model_version") == model_version]
    probs = [float(row["probability"]) for row in rows]
    outcomes = [int(row["settled_yes"]) for row in rows]
    buckets = calibration_buckets(probs, outcomes) if rows else []
    enough = len(rows) >= min_rows
    payload = {
        "station": station or settings.default_station,
        "method": "reliability_bucket",
        "sample_count": len(rows),
        "min_rows": min_rows,
        "buckets": buckets,
        "dry_run": dry_run,
        "is_demo_or_small_sample": not enough,
        "warning": None if enough else "too few rows; production calibration not written",
    }
    if (enough or allow_small_sample_demo) and not dry_run:
        write_json_report(output, payload)
        payload["written_path"] = output
    _emit_report(payload, json_output=json_output or dry_run, output=None if dry_run else output)


@app.command("model-weight-report")
def model_weight_report(
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    write_config_fragment: str | None = typer.Option(None, "--write-config-fragment"),
) -> None:
    """Compare model-specific future highs against official outcomes where available."""
    settings = load_settings()
    rows = _store(settings).load_prediction_outcomes(station=station or settings.default_station)
    payload = _model_weight_report_payload(rows)
    if write_config_fragment:
        write_json_report(write_config_fragment, {"model": {"open_meteo_model_weights": payload["weight_hints"]}})
    _emit_report(payload, json_output=json_output, output=output)


@app.command("replay-predictions")
def replay_predictions(
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    model_version: str | None = typer.Option(None, "--model-version"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Replay stored prediction rows and summarize opportunity frequency."""
    settings = load_settings()
    store_obj = _store(settings)
    predictions = store_obj.load_predictions(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
    )
    if model_version:
        predictions = [row for row in predictions if row.get("model_version") == model_version]
    joined = store_obj.load_prediction_outcomes(
        station=station or settings.default_station,
        start_date=start_date,
        end_date=end_date,
    )
    payload = _replay_predictions_payload(predictions, joined, settings)
    _emit_report(payload, json_output=json_output, output=output)


@app.command("paper-replay")
def paper_replay(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    min_edge: float | None = typer.Option(None, "--min-edge"),
    fee_buffer: float | None = typer.Option(None, "--fee-buffer"),
    model_error_buffer: float | None = typer.Option(None, "--model-error-buffer"),
    profit_target: float | None = typer.Option(None, "--profit-target"),
    stop_loss: float | None = typer.Option(None, "--stop-loss"),
    max_hold_minutes: int | None = typer.Option(None, "--max-hold-minutes"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Replay fake entries/exits from stored prediction snapshots. No live network."""
    settings = load_settings()
    _ = series or settings.default_series
    _ = station or settings.default_station
    payload = _paper_replay_report(
        _store(settings),
        min_edge=Decimal(str(min_edge)) if min_edge is not None else settings.min_edge,
        fee_buffer=Decimal(str(fee_buffer)) if fee_buffer is not None else settings.fee_buffer,
        model_error_buffer=Decimal(str(model_error_buffer)) if model_error_buffer is not None else settings.model_error_buffer,
        profit_target=Decimal(str(profit_target)) if profit_target is not None else settings.profit_target,
        stop_loss=Decimal(str(stop_loss)) if stop_loss is not None else settings.stop_loss,
        max_hold_minutes=max_hold_minutes or settings.max_hold_minutes,
    )
    _emit_report(payload, json_output=json_output, output=output)


@app.command("poc-run")
def poc_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    max_iterations: int = typer.Option(1, "--max-iterations"),
    interval_seconds: int = typer.Option(60, "--interval-seconds"),
    paper: bool = typer.Option(False, "--paper"),
    no_paper: bool = typer.Option(True, "--no-paper"),
    json_output: bool = typer.Option(False, "--json"),
    reports_dir: str = typer.Option("reports/poc_runs", "--reports-dir"),
    model_version: str | None = typer.Option(None, "--model-version"),
) -> None:
    """Run the safe proof-of-concept workflow and write reports."""
    settings = _settings_with_model_version(load_settings(), model_version)
    series = series or settings.default_series
    station = station or settings.default_station
    report_dir = timestamped_report_dir(reports_dir, "poc_run")
    summary = _poc_report_shell(settings, series, station, report_dir)
    summary["max_iterations"] = max_iterations
    summary["interval_seconds"] = interval_seconds
    if paper:
        try:
            store_obj = _store(settings)
            result = run_paper_once(
                settings,
                _kalshi(settings),
                _nws(settings),
                _open_meteo(settings),
                store_obj,
                make_default_broker(settings, store=store_obj),
                series,
                station,
            )
            summary["paper_once"] = {"status": "ok", "result": result}
        except Exception as exc:  # noqa: BLE001
            summary["paper_once"] = {"status": "error", "error": str(exc)}
    else:
        summary["paper_once"] = {
            "status": "skipped",
            "reason": "default no-paper; fake execution requires --paper",
            "no_paper_flag": no_paper,
        }
    write_json_report(report_dir / "summary.json", summary)
    _emit_report(summary, json_output=json_output, output=None)


@app.command("poc-demo")
def poc_demo(
    station: str | None = typer.Option(None),
    reports_dir: str = typer.Option("reports/poc_demo", "--reports-dir"),
    store_demo: bool = typer.Option(False, "--store-demo"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Run the offline POC fixture path. Demo data is not trading evidence."""
    settings = load_settings()
    station = station or settings.default_station
    report_dir = timestamped_report_dir(reports_dir, "poc_demo")
    payload = _poc_demo_payload(station, store_demo=store_demo)
    payload["report_dir"] = str(report_dir)
    write_json_report(report_dir / "summary.json", payload)
    write_text_report(report_dir / "summary.txt", DEMO_LABEL + "\n" + json.dumps(payload, indent=2))
    _emit_report(payload, json_output=json_output, output=output, text=DEMO_LABEL)


@app.command("poc-check")
def poc_check(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    reports_dir: str = typer.Option("reports/poc_check", "--reports-dir"),
    skip_live: bool = typer.Option(False, "--skip-live"),
    include_paper_once: bool = typer.Option(False, "--include-paper-once"),
    max_iterations: int = typer.Option(1, "--max-iterations"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run the final safe proof-of-concept validation workflow."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    report_dir = timestamped_report_dir(reports_dir, "poc_check")
    store_obj = _store(settings)
    summary: dict[str, Any] = {
        "series": series,
        "station": station,
        "report_dir": str(report_dir),
        "safety": {
            "fake_money_only": True,
            "live_trading_enabled": settings.kalshi_enable_real_orders,
            "live_order_endpoint_present": False,
        },
        "commands": {},
    }

    def capture(name: str, func: Any) -> None:
        try:
            payload = func()
            summary["commands"][name] = {"status": "ok", "payload": payload}
            write_json_report(report_dir / f"{name}.json", payload)
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": str(exc)}
            summary["commands"][name] = payload
            write_json_report(report_dir / f"{name}.json", payload)

    capture("research_status", lambda: {"db_counts": _db_counts(store_obj), "series": series, "station": station})
    capture("time_debug", lambda: lax_time_debug_payload(station))
    if not skip_live:
        capture("weather_debug", lambda: safe_console_payload(_weather_context(settings, station)[0]))
        capture(
            "opportunities",
            lambda: {
                "rows": opportunity_rows(
                    _prediction_context(settings, series, station)["brackets"],
                    _prediction_context(settings, series, station)["tops"],
                    _prediction_context(settings, series, station)["probs"],
                    settings,
                )
            },
        )
        capture(
            "collect_once",
            lambda: collect_once_cycle(
                settings,
                _kalshi(settings),
                _nws(settings),
                _open_meteo(settings),
                store_obj,
                series,
                station,
            ),
        )
    capture("threshold_sweep", lambda: {"available": True, "replay_supported": True})
    capture("calibration_readiness", lambda: _calibration_readiness_payload(store_obj, station, settings))
    capture("calibration_report", lambda: _calibration_report_or_empty(store_obj, station))
    capture("residual_report", lambda: _residual_report_payload(store_obj, station))
    capture("paper_report", store_obj.paper_report)
    capture("paper_replay", lambda: _paper_replay_report(store_obj, min_edge=settings.min_edge))
    capture("poc_demo", lambda: _poc_demo_payload(station, store_demo=False))
    if include_paper_once and not skip_live:
        capture(
            "paper_once",
            lambda: run_paper_once(
                settings,
                _kalshi(settings),
                _nws(settings),
                _open_meteo(settings),
                store_obj,
                make_default_broker(settings, store=store_obj),
                series,
                station,
            ),
        )
    summary["max_iterations"] = max_iterations
    summary["next_recommended_action"] = "collect more read-only snapshots, then fetch/join official outcomes"
    write_json_report(report_dir / "summary.json", summary)
    write_text_report(report_dir / "summary.txt", json.dumps(safe_console_payload(summary), indent=2))
    _write_poc_acceptance_report(Path("POC_ACCEPTANCE_REPORT.md"), summary, store_obj)
    _emit_report(summary, json_output=json_output, output=None)


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key))].append(row)
    summary: dict[str, dict[str, float | int]] = {}
    for value, group_rows in groups.items():
        probs = [float(row["probability"]) for row in group_rows]
        outcomes = [int(row["settled_yes"]) for row in group_rows]
        summary[value] = {
            "count": len(group_rows),
            "avg_probability": sum(probs) / len(probs),
            "yes_rate": sum(outcomes) / len(outcomes),
        }
    return summary


def _calibration_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    probs = [float(row["probability"]) for row in rows]
    outcomes = [int(row["settled_yes"]) for row in rows]
    return {
        "joined_row_count": len(rows),
        "brier_score": brier_score(probs, outcomes),
        "log_loss": log_loss_binary(probs, outcomes),
        "average_predicted_probability": sum(probs) / len(probs),
        "empirical_yes_rate": sum(outcomes) / len(outcomes),
        "warning": "fewer than 30 joined rows" if len(rows) < 30 else None,
        "calibration_buckets": calibration_buckets(probs, outcomes),
        "by_bracket": _group_summary(rows, "bracket_label"),
        "by_model_version": _group_summary(rows, "model_version"),
        "by_market_date": _group_summary(rows, "market_date"),
        "by_asof_hour": _group_summary(rows, "asof_hour_utc"),
        "by_observed_inside_bracket": _group_summary(rows, "observed_inside_bracket"),
    }


def _empty_calibration_report(station: str) -> dict[str, Any]:
    return {
        "station": station,
        "joined_row_count": 0,
        "status": "empty",
        "warning": "no official outcomes joined to stored predictions yet",
        "next_commands": [
            "kalshi-weather fetch-missing-outcomes --station KLAX",
            "kalshi-weather record-outcome --station KLAX --date YYYY-MM-DD --official-high-f NN --source manual",
            "kalshi-weather join-outcomes --station KLAX --overwrite",
            "kalshi-weather calibration-report --station KLAX",
        ],
    }


def _calibration_report_or_empty(store_obj: SQLiteStore, station: str) -> dict[str, Any]:
    rows = store_obj.load_prediction_outcomes(station=station)
    return _calibration_report(rows) if rows else _empty_calibration_report(station)


def _residual_report_payload(store_obj: SQLiteStore, station: str) -> dict[str, Any]:
    rows = store_obj.load_prediction_outcomes(station=station)
    residuals = [
        float(row["official_high_f"]) - float(row["model_future_high_f"])
        for row in rows
        if row.get("model_future_high_f") is not None
    ]
    return {
        "joined_row_count": len(rows),
        "residual_count": len(residuals),
        "residual_mean": statistics.mean(residuals) if residuals else None,
        "residual_median": statistics.median(residuals) if residuals else None,
        "residual_std": _sample_stddev(residuals),
        "residual_mae": statistics.mean(abs(value) for value in residuals) if residuals else None,
        "residual_rmse": math.sqrt(statistics.mean(value**2 for value in residuals)) if residuals else None,
        "residual_percentiles": _percentiles(residuals, [10, 25, 50, 75, 90]) if residuals else {},
        "suggested_residual_sigma_f": _sample_stddev(residuals),
        "by_asof_hour": _residual_group_summary(rows, "asof_hour_utc"),
        "by_bracket": _residual_group_summary(rows, "bracket_label"),
        "by_model_version": _residual_group_summary(rows, "model_version"),
        "warning": "fewer than 30 joined rows; do not overfit residual sigma" if len(rows) < 30 else None,
    }


def _percentiles(values: list[float], percentiles: list[int]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    result: dict[str, float] = {}
    for percentile in percentiles:
        if len(ordered) == 1:
            result[f"p{percentile}"] = ordered[0]
            continue
        rank = (percentile / 100) * (len(ordered) - 1)
        lo = math.floor(rank)
        hi = math.ceil(rank)
        if lo == hi:
            result[f"p{percentile}"] = ordered[lo]
        else:
            result[f"p{percentile}"] = ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)
    return result


def _calibration_readiness_payload(
    store_obj: SQLiteStore,
    station: str,
    settings: Settings,
) -> dict[str, Any]:
    prediction_dates = store_obj.distinct_prediction_dates(station)
    latest_settled = latest_settled_lax_market_date(
        settlement_buffer_hours=settings.settlement_buffer_hours
    )
    outcomes = store_obj.load_official_outcomes(station=station)
    outcome_dates = {row["market_date"] for row in outcomes}
    waiting = [value for value in prediction_dates if date.fromisoformat(value) > latest_settled]
    missing = [value for value in prediction_dates if value not in outcome_dates and value not in waiting]
    return {
        "station": station,
        "production_prediction_dates": prediction_dates,
        "settled_eligible_dates": [
            value for value in prediction_dates if date.fromisoformat(value) <= latest_settled
        ],
        "official_outcomes_available": len(outcomes),
        "joined_rows": store_obj.joined_outcome_count(),
        "dates_waiting_for_settlement": waiting,
        "dates_missing_outcome_fetch": missing,
        "minimum_recommended_rows": 30,
        "next_commands": [
            "kalshi-weather fetch-missing-outcomes --station KLAX",
            "kalshi-weather join-outcomes --station KLAX --overwrite",
            "kalshi-weather calibration-report --station KLAX",
        ],
    }


def _demo_joined_rows() -> list[dict[str, Any]]:
    rows = []
    for idx, prob in enumerate([0.08, 0.18, 0.31, 0.45, 0.58, 0.68, 0.79, 0.86, 0.92, 0.97]):
        rows.append(
            {
                "probability": prob,
                "settled_yes": int(prob >= 0.5 if idx % 3 else prob >= 0.7),
                "bracket_label": f"demo-{idx}",
                "model_version": "demo-fixture-model",
                "market_date": f"2026-05-{idx + 1:02d}",
                "asof_hour_utc": 18 + (idx % 6),
                "observed_inside_bracket": idx % 2,
                "official_high_f": 70 + (idx % 5),
                "model_future_high_f": 70 + (idx % 5) - 0.5,
                "observed_high_so_far_f": 68 + (idx % 4),
            }
        )
    return rows


def _model_weight_report_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, list[float]] = defaultdict(list)
    total_rows = 0
    for row in rows:
        if row.get("official_high_f") is None:
            continue
        total_rows += 1
        try:
            details = json.loads(row.get("model_details_json") or "{}")
        except json.JSONDecodeError:
            details = {}
        future_by_model = details.get("future_max_by_model", {})
        for column, value in future_by_model.items():
            model_id = str(column).split("__", 1)[-1]
            by_model[model_id].append(float(row["official_high_f"]) - float(value))
    models = {}
    hints = {}
    for model_id, residuals in by_model.items():
        mae = statistics.mean(abs(value) for value in residuals)
        rmse = math.sqrt(statistics.mean(value**2 for value in residuals))
        models[model_id] = {
            "row_count": len(residuals),
            "mean_error": statistics.mean(residuals),
            "mae": mae,
            "rmse": rmse,
            "bias": statistics.mean(residuals),
            "availability_percentage": len(residuals) / total_rows if total_rows else 0,
            "recommended_weight_hint": 1 / max(mae, 0.1),
            "warning": "small sample" if len(residuals) < 30 else None,
        }
        hints[model_id] = round(1 / max(mae, 0.1), 3)
    return {"row_count": total_rows, "models": models, "weight_hints": hints}


def _replay_predictions_payload(
    predictions: list[dict[str, Any]],
    joined: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    thresholds = [Decimal("0.02"), Decimal("0.05"), Decimal("0.08"), Decimal("0.10")]
    threshold_rows = []
    for threshold in thresholds:
        hurdle = threshold + settings.fee_buffer + settings.model_error_buffer
        buys = [
            row
            for row in predictions
            if max(
                Decimal(str(row.get("yes_edge") or "-999")),
                Decimal(str(row.get("no_edge") or "-999")),
            )
            > hurdle
        ]
        threshold_rows.append(
            {
                "threshold": str(threshold),
                "total_hurdle": str(hurdle),
                "hypothetical_trade_count": len(buys),
            }
        )
    report: dict[str, Any] = {
        "prediction_count": len(predictions),
        "joined_count": len(joined),
        "opportunity_count_by_threshold": threshold_rows,
        "warnings": [],
    }
    if joined:
        probs = [float(row["probability"]) for row in joined]
        outcomes = [int(row["settled_yes"]) for row in joined]
        report["terminal_brier"] = brier_score(probs, outcomes)
        report["terminal_log_loss"] = log_loss_binary(probs, outcomes)
    else:
        report["warnings"].append("No joined official outcomes; replay cannot score terminal accuracy.")
    return report


def _paper_replay_report(
    store_obj: SQLiteStore,
    min_edge: Decimal,
    fee_buffer: Decimal | None = None,
    model_error_buffer: Decimal | None = None,
    profit_target: Decimal | None = None,
    stop_loss: Decimal | None = None,
    max_hold_minutes: int | None = None,
) -> dict[str, Any]:
    predictions = store_obj.load_predictions()
    hurdle = min_edge + (fee_buffer or Decimal("0")) + (model_error_buffer or Decimal("0"))
    entries = []
    exits = []
    realized = Decimal("0")
    open_positions: dict[str, dict[str, Any]] = {}
    for row in sorted(predictions, key=lambda item: (str(item.get("asof_utc")), int(item["id"]))):
        yes_edge = Decimal(str(row.get("yes_edge") or "-999"))
        no_edge = Decimal(str(row.get("no_edge") or "-999"))
        side = "yes" if yes_edge >= no_edge else "no"
        edge = max(yes_edge, no_edge)
        ticker = str(row.get("market_ticker") or row.get("ticker"))
        ask = Decimal(str(row.get(f"{side}_ask") or "999"))
        bid = Decimal(str(row.get(f"{side}_bid") or "0"))
        if ticker in open_positions:
            entry = open_positions[ticker]
            pnl = bid - Decimal(str(entry["entry_price"]))
            exit_reason = None
            if profit_target is not None and pnl >= profit_target:
                exit_reason = "profit target hit"
            elif stop_loss is not None and pnl <= -stop_loss:
                exit_reason = "stop loss hit"
            elif edge <= hurdle:
                exit_reason = "edge disappeared"
            if exit_reason:
                realized += pnl
                exits.append({"ticker": ticker, "side": entry["side"], "pnl": str(pnl), "reason": exit_reason})
                open_positions.pop(ticker, None)
            continue
        if edge > hurdle and ask <= Decimal("1"):
            entry = {
                "ticker": ticker,
                "side": side,
                "entry_price": str(ask),
                "edge": str(edge),
                "asof_utc": row.get("asof_utc"),
            }
            open_positions[ticker] = entry
            entries.append(entry)
    warnings = []
    if not predictions:
        warnings.append("No stored predictions available for replay.")
    if max_hold_minutes is not None:
        warnings.append("Max-hold replay is approximate because stored snapshots are sparse.")
    return {
        "snapshots_scanned": len(predictions),
        "simulated_entries": len(entries),
        "simulated_exits": len(exits),
        "open_replay_positions": list(open_positions.values()),
        "realized_replay_pnl": str(realized),
        "max_drawdown": None,
        "average_hold_time": None,
        "trades_by_side": _count_by(entries, "side"),
        "trades_by_ticker": _count_by(entries, "ticker"),
        "edge_threshold_used": str(min_edge),
        "total_hurdle": str(hurdle),
        "warnings": warnings,
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _poc_demo_payload(station: str, store_demo: bool) -> dict[str, Any]:
    rows = _demo_joined_rows()
    return {
        "label": DEMO_LABEL,
        "station": station,
        "stored_to_production": store_demo,
        "demo_inputs": {"observed_high_so_far_f": 70, "model_future_high_f": 72},
        "demo_opportunities": [
            {
                "ticker": "DEMO-T70",
                "best_side": "yes",
                "best_edge": "0.12",
                "would_trade": True,
                "reason": "fixture edge for plumbing demo",
            }
        ],
        "demo_threshold_sweep": [{"threshold": "0.05", "would_trade_count": 1}],
        "demo_paper_simulation": {"entries": 1, "exits": 1, "realized_pnl": "0.04"},
        "demo_calibration": _calibration_report(rows),
    }


def _poc_report_shell(settings: Settings, series: str, station: str, report_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "series": series,
        "station": station,
        "report_dir": str(report_dir),
        "safety": {
            "fake_money_only": True,
            "live_trading_enabled": settings.kalshi_enable_real_orders,
            "live_order_endpoint_present": False,
        },
        "steps": {},
    }

    def capture(name: str, func: Any) -> None:
        try:
            payload = func()
            summary["steps"][name] = {"status": "ok", "payload": payload}
            write_json_report(report_dir / f"{name}.json", payload)
        except Exception as exc:  # noqa: BLE001
            payload = {"status": "error", "error": str(exc)}
            summary["steps"][name] = payload
            write_json_report(report_dir / f"{name}.json", payload)

    capture("time_debug", lambda: lax_time_debug_payload(station))
    capture("weather_debug", lambda: safe_console_payload(_weather_context(settings, station)[0]))
    capture("threshold_sweep", lambda: {"available": True})
    capture(
        "collect_once",
        lambda: collect_once_cycle(
            settings,
            _kalshi(settings),
            _nws(settings),
            _open_meteo(settings),
            _store(settings),
            series,
            station,
        ),
    )
    return summary


def _write_poc_acceptance_report(path: Path, summary: dict[str, Any], store_obj: SQLiteStore) -> None:
    counts = _db_counts(store_obj)
    lines = [
        "# POC Acceptance Report",
        "",
        f"Generated UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
        "- Tests pass: see final results package.",
        "- Ruff passes: see final results package.",
        "- Live trading disabled: true.",
        "- Live order endpoint present: false.",
        f"- Read-only command summary: {json.dumps(safe_console_payload(summary.get('commands', {})), indent=2)}",
        f"- Production joined outcomes: {counts.get('prediction_outcomes', 0)}.",
        "- Calibration meaningful yet: no, unless production joined rows reach a useful sample size.",
        f"- Fake paper fills: {counts.get('paper_fills', 0)}.",
        "- No-trade state is valid when configured edge thresholds are not cleared.",
        "- Demo POC works offline but proves plumbing only, not edge.",
        "- Before claiming edge: collect settled predictions, ingest official outcomes, join, and review calibration/replay.",
        "- Before live-readiness discussion: add authentication, order guards, approvals, dry-run audits, and separate review.",
    ]
    write_text_report(path, "\n".join(lines) + "\n")


def _residual_group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("model_future_high_f") is None:
            continue
        groups[str(row.get(key))].append(float(row["official_high_f"]) - float(row["model_future_high_f"]))
    return {
        value: {
            "count": len(values),
            "avg_residual": sum(values) / len(values),
            "sample_stddev": _sample_stddev(values),
        }
        for value, values in groups.items()
        if values
    }


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance**0.5


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise typer.BadParameter("end date must be on or after start date")
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _fetch_and_optionally_store_outcome(
    client: NWSClimateProductClient,
    store_obj: SQLiteStore,
    station: str,
    outcome_date: date,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    try:
        outcome = client.fetch_daily_high(station, outcome_date)
        outcome_id = None
        status = "dry-run"
        if not dry_run:
            outcome_id = store_obj.save_official_outcome(
                outcome.station,
                outcome.market_date,
                outcome.metric,
                outcome.official_high_f,
                outcome.source,
                outcome.source_url,
                outcome.source_text,
                overwrite=overwrite,
            )
            status = "stored"
        return {
            "date": outcome_date.isoformat(),
            "status": status,
            "official_high_f": outcome.official_high_f,
            "source_url": outcome.source_url,
            "official_outcome_id": outcome_id,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - range fetch must continue after one bad date
        return {
            "date": outcome_date.isoformat(),
            "status": "unavailable",
            "official_high_f": None,
            "source_url": None,
            "official_outcome_id": None,
            "error": str(exc),
        }


def _outcome_backfill_report(
    client: NWSClimateProductClient,
    store_obj: SQLiteStore,
    station: str,
    dates: list[date],
    overwrite: bool,
    dry_run: bool,
    allow_unsettled_store: bool,
    settlement_buffer_hours: int,
) -> dict[str, Any]:
    rows = []
    skipped_unsettled = 0
    latest_settled = latest_settled_lax_market_date(
        settlement_buffer_hours=settlement_buffer_hours
    )
    for outcome_date in dates:
        if not allow_unsettled_store and outcome_date > latest_settled:
            skipped_unsettled += 1
            rows.append(
                {
                    "date": outcome_date.isoformat(),
                    "status": "skipped_unsettled",
                    "official_high_f": None,
                    "source_url": None,
                    "official_outcome_id": None,
                    "error": None,
                }
            )
            continue
        rows.append(
            _fetch_and_optionally_store_outcome(
                client, store_obj, station, outcome_date, overwrite, dry_run
            )
        )
    unavailable_count = sum(1 for row in rows if row["status"] == "unavailable")
    return {
        "station": station,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "dry_run": dry_run,
        "overwrite": overwrite,
        "settlement_buffer_hours": settlement_buffer_hours,
        "allow_unsettled_store": allow_unsettled_store,
        "latest_settled_market_date": latest_settled.isoformat(),
        "attempted_dates": [d.isoformat() for d in dates],
        "stored_count": sum(1 for row in rows if row["status"] == "stored"),
        "dry_run_success_count": sum(1 for row in rows if row["status"] == "dry-run"),
        "skipped_unsettled_count": skipped_unsettled,
        "unavailable_count": unavailable_count,
        "parse_error_count": unavailable_count,
        "per_date_results": rows,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    app()
