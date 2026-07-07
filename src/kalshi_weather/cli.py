from __future__ import annotations

# ruff: noqa: E402

import csv
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
import warnings
from concurrent.futures import Future, ThreadPoolExecutor
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore", message="Pandas requires version .* of 'numexpr'.*")
warnings.filterwarnings("ignore", message="Pandas requires version .* of 'bottleneck'.*")

import typer
import yaml
from rich.console import Console
from rich.table import Table

from kalshi_weather.advisor.llm_trade_advisor import ADVISOR_MODES, RuleBasedAdvisor
from kalshi_weather.advisor.risk_validator import validate_advisor_trade
from kalshi_weather.advisor.synthetic import run_advisor_synthetic_suite
from kalshi_weather import validation as validation_reports
from kalshi_weather.analysis.temperature_estimates import (
    build_temperature_estimate_payload,
    temperature_estimate_summary_text,
    write_temperature_estimate_artifacts,
)
from kalshi_weather.config import Settings, load_settings
from kalshi_weather.data.herbie_client import (
    HerbieFetchResult,
    HerbieModelClient,
    NOAA_HERBIE_MODELS,
    dependency_status,
)
from kalshi_weather.data.kalshi_client import KalshiPublicClient
from kalshi_weather.data.kalshi_history import (
    discover_market_row,
    enrich_trend_rows_with_hurdle,
    generate_trend_charts,
    market_window_for_date,
    microtrade_replay,
    normalize_candlestick_response,
    ts_seconds,
    trend_rows_from_candles,
    trend_summary,
    trend_summary_text,
    write_dashboard,
    write_microtrade_chart,
)
from kalshi_weather.data.market_discovery import (
    bracket_text_from_market,
    filter_markets_for_date,
    market_date_from_market,
    parse_brackets_from_markets,
)
from kalshi_weather.data.nws_client import NWSClient, observations_json_to_frame
from kalshi_weather.data.open_meteo_client import OPEN_METEO_MODEL_CANDIDATES, OpenMeteoClient
from kalshi_weather.data.outcomes import NWSClimateProductClient, OutcomeUnavailableError
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.edge_engine.backtest import (
    REQUIRED_BACKTEST_COLUMNS,
    BacktestRecord as EdgeBacktestRecord,
    run_synthetic_backtest,
)
from kalshi_weather.edge_engine.data_freshness import FreshnessConfig, assess_freshness
from kalshi_weather.edge_engine.edge import build_yes_no_candidates as build_rule_edge_candidates
from kalshi_weather.edge_engine.hold_filters import filter_candidates as filter_rule_edge_candidates
from kalshi_weather.edge_engine.pnl_attribution import summarize_attribution
from kalshi_weather.edge_engine.strategy_rules import choose_best_candidate as choose_rule_edge_candidate
from kalshi_weather.edge_engine.strategy_report import build_strategy_report
from kalshi_weather.edge_engine.types import (
    Action as EdgeAction,
    CandidateTrade as EdgeCandidateTrade,
    CostConfig as EdgeCostConfig,
    MarketQuote as EdgeMarketQuote,
    OpenOrder as EdgeOpenOrder,
    OrderType as EdgeOrderType,
    PortfolioState as EdgePortfolioState,
    Position as EdgePosition,
    RiskConfig as EdgeRiskConfig,
    Side as EdgeSide,
    StrategyConfig as EdgeStrategyConfig,
    canonicalize_label as edge_canonicalize_label,
)
from kalshi_weather.llm.decision_log import write_llm_decision_log
from kalshi_weather.llm.ollama_provider import OllamaLLMProvider
from kalshi_weather.llm.schemas import DEFAULT_LLM_MODEL
from kalshi_weather.llm.trade_snapshot import (
    advisor_input_to_trade_snapshot,
    build_sample_advisor_input,
)
from kalshi_weather.market_lifecycle.lifecycle_profiles import profile_for_lifecycle_state
from kalshi_weather.market_lifecycle.lifecycle_state import (
    LifecycleState,
    lifecycle_snapshot,
)
from kalshi_weather.market_lifecycle.market_calendar import (
    MarketCalendarProvider,
    fallback_weather_market_timeline,
    incomplete_timeline,
)
from kalshi_weather.market_lifecycle.rollover import should_roll_to_next_event
from kalshi_weather.trading.hard_risk_validator import hard_validator_result
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
from kalshi_weather.model.model_estimates import (
    ModelEstimate,
    current_and_open_meteo_estimates,
    estimate_key,
    probabilities_for_estimates,
)
from kalshi_weather.model_tournament import (
    TournamentConfig,
    load_tournament_state,
    run_tournament_cycle,
    write_tournament_files,
)
from kalshi_weather.model_registry import (
    get_model_source,
    provider_model_options,
    registry_rows,
    select_model_keys,
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
from kalshi_weather.debug_packager import create_debug_package
from kalshi_weather.runtime_paths import (
    NonCanonicalPathError,
    ensure_canonical_dirs,
    get_archive_root,
    get_candidates_csv_path,
    get_clv_report_path,
    get_debug_root,
    get_decisions_jsonl_path,
    get_journal_path,
    get_repo_root,
    get_run_dir,
    outside_canonical_warning,
    read_latest_run_pointer,
    resolve_output_path,
    sanitize_run_id,
    write_latest_run_pointer,
    write_run_metadata,
)
from kalshi_weather.trader_reports import write_trader_run_review_reports
from kalshi_weather.rules_engine_ext.clv import CLVRecord, summarize_clv
from kalshi_weather.rules_engine_ext.correlated_thesis import (
    ThesisPosition,
    evaluate_thesis_exposure,
    infer_thesis_label,
)
from kalshi_weather.rules_engine_ext.market_distribution import (
    MarketQuote as ExtMarketQuote,
    normalize_market_distribution,
)
from kalshi_weather.rules_engine_ext.position_thesis import (
    CurrentThesis,
    EntryThesis,
    evaluate_position,
)
from kalshi_weather.rules_engine_ext.probability_blend import (
    blend_probability,
)
from kalshi_weather.rules_engine_ext.settlement_scenarios import (
    Position as SettlementPosition,
    settlement_report,
)
from kalshi_weather.rules_engine_ext.time_profiles import (
    DEFAULT_PROFILES,
    ProfileDecision,
    ProfileInputs,
    RiskConfig as ProfileRiskConfig,
    select_profile,
)
from kalshi_weather.schemas import Bracket, WeatherSnapshot
from kalshi_weather.synthetic.providers import (
    build_default_scenario_set,
    load_or_build_default_scenario_dir,
    run_synthetic_algo_test,
    run_synthetic_scenario,
)
from kalshi_weather.synthetic.scenarios import load_scenario, scenario_index
from kalshi_weather.time_utils import utc_now
from kalshi_weather.trader_agent.agent import TraderAgent, TraderRunResult
from kalshi_weather.trader_agent.decision_schema import TraderDecision
from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from kalshi_weather.trader_agent.llm_client import (
    DryRunTraderLLMClient,
    MockTraderLLMClient,
    OllamaTraderLLMClient,
    OpenAITraderLLMClient,
    TraderLLMClient,
)
from kalshi_weather.trader_agent.paper_adapter import decision_to_paper_order
from kalshi_weather.trader_agent.prompt_builder import TraderPromptBuilder
from kalshi_weather.trader_agent.repo_adapter import trader_context_from_model_payload
from kalshi_weather.trader_agent.replay import load_contexts_from_jsonl, replay_contexts
from kalshi_weather.trader_agent.trader_types import RiskLimits
from kalshi_weather.trader_agent.validator import ValidationResult, validate_decision
from kalshi_weather.trading.model_race import (
    ModelRaceConfig,
    advisor_dry_run_payload,
    compact_model_race_text,
    flatten_model_race,
    force_flat_model_race,
    model_race_debug_text,
    model_specs,
    model_race_report_payload,
    model_race_report_text,
    run_model_race_exit_monitor,
    run_model_race_once,
)
from kalshi_weather.trading.orderbook import parse_orderbook_top
from kalshi_weather.trading.runner import (
    build_prediction_records,
    collect_once as collect_once_cycle,
    forecast_diagnostics,
    forecast_model_details,
    make_default_broker,
    opportunity_rows,
    run_paper_loop,
    run_paper_once,
)
from kalshi_weather.trading.signals import terminal_edges
from kalshi_weather.validation_analysis import analyze_model_validation, format_validation_analysis
from kalshi_weather.validation_journal import ValidationJournal, append_jsonl

app = typer.Typer(help="Kalshi weather paper-trading research CLI")
trader_journal_app = typer.Typer(
    help="Inspect and analyze fake-money trader journal entries.",
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(trader_journal_app, name="trader-journal")
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
        print(json.dumps(safe_payload, indent=2))
    else:
        console.print(text or safe_payload)


def _resolve_cli_output_path(
    path: str | Path | None,
    default: Path,
    *,
    allow_noncanonical: bool,
) -> Path:
    try:
        resolved = resolve_output_path(path, default, allow_noncanonical=allow_noncanonical)
    except NonCanonicalPathError as exc:
        raise typer.BadParameter(str(exc)) from exc
    warning = outside_canonical_warning(resolved)
    if warning and allow_noncanonical:
        console.print(warning)
    return resolved


def _resolve_repo_path_text(path: str | Path | None) -> str | None:
    if path is None or str(path).strip() == "":
        return None
    candidate = Path(path)
    return str(candidate if candidate.is_absolute() else get_repo_root() / candidate)


def _latest_run_id_or(default: str = "trader_agent") -> str:
    pointer = read_latest_run_pointer()
    if pointer and pointer.get("run_id"):
        return sanitize_run_id(str(pointer["run_id"]))
    return sanitize_run_id(default)


def _db_counts(store_obj: SQLiteStore) -> dict[str, int]:
    tables = [
        "market_snapshots",
        "weather_snapshots",
        "model_predictions",
        "model_estimates",
        "model_estimate_probabilities",
        "official_outcomes",
        "prediction_outcomes",
        "paper_fills",
        "paper_positions",
        "opportunity_snapshots",
        "kalshi_candlesticks",
        "kalshi_trend_artifacts",
        "advisor_decisions",
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


def _resolve_target_market_date(target_date: str | None, tomorrow: bool) -> date:
    if target_date and tomorrow:
        raise typer.BadParameter("Use either --target-date or --tomorrow, not both.")
    current_market_date = current_lax_market_date()
    if tomorrow:
        return current_market_date + timedelta(days=1)
    if not target_date:
        return current_market_date
    try:
        resolved = date.fromisoformat(target_date)
    except ValueError as exc:
        raise typer.BadParameter("--target-date must use YYYY-MM-DD.") from exc
    if resolved < current_market_date:
        raise typer.BadParameter("--target-date cannot be before the current LAX market date.")
    return resolved


def _lax_market_window_local(market_date: date) -> tuple[datetime, datetime]:
    start_utc, end_utc = lax_climate_day_utc(market_date)
    zone = ZoneInfo(LAX_TIMEZONE)
    return (
        start_utc.astimezone(zone).replace(tzinfo=None),
        end_utc.astimezone(zone).replace(tzinfo=None),
    )


def _forecast_days_for_market_date(market_date: date, now_utc: datetime | None = None) -> int:
    current_market_date = current_lax_market_date(now_utc)
    days_ahead = (market_date - current_market_date).days
    return max(1, min(16, days_ahead + 2))


def _weather_context(settings: Settings, station: str, target_date: date | None = None) -> tuple[Any, Any]:
    market_date = target_date or current_lax_market_date()
    now = utc_now()
    current_market_date = current_lax_market_date(now)
    start_utc, end_utc = lax_climate_day_utc(market_date)
    is_future_market_date = market_date > current_market_date
    if is_future_market_date:
        asof_local, end_local = _lax_market_window_local(market_date)
    else:
        asof_local, end_local = remaining_lax_day_local(now)
    forecast = _open_meteo(settings).forecast_hourly_by_model(
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
        models=settings.open_meteo_models,
        variables=settings.hourly_variables,
        timezone_name=LAX_TIMEZONE,
        forecast_days=_forecast_days_for_market_date(market_date, now),
        asof_local=asof_local,
        end_local=end_local,
    )
    model_details = forecast_model_details(forecast, settings)
    if is_future_market_date:
        selected = model_details.get("selected_future_high_f")
        weather = WeatherSnapshot(
            station_id=station,
            timestamp_utc=now,
            observed_high_so_far_f=None,
            latest_observation_utc=None,
            observation_count=0,
            model_future_high_f=float(selected) if selected is not None else None,
            model_details=model_details,
        )
        return weather, forecast

    obs = _nws(settings).station_observations(station, start_utc, min(now, end_utc))
    weather = weather_snapshot_from_frames(
        station,
        obs,
        forecast.model_maxes_f,
        model_details=model_details,
    )
    return weather, forecast


def _prediction_context(
    settings: Settings,
    series: str,
    station: str,
    target_date: date | None = None,
) -> dict[str, Any]:
    kalshi = _kalshi(settings)
    market_date = target_date or current_lax_market_date()
    markets = filter_markets_for_date(kalshi.get_markets(series), market_date)
    brackets = parse_brackets_from_markets(markets)
    tickers = [bracket.ticker for bracket in brackets]
    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, data) for ticker, data in orderbooks.items()}
    weather, forecast = _weather_context(settings, station, market_date)
    if weather.model_future_high_f is None:
        probs: dict[str, float] = {}
    else:
        observed_high = (
            float(weather.observed_high_so_far_f)
            if weather.observed_high_so_far_f is not None
            else float("nan")
        )
        samples = settlement_high_samples(
            weather.model_future_high_f,
            observed_high,
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


def _market_context(settings: Settings, series: str, market_date: date) -> dict[str, Any]:
    kalshi = _kalshi(settings)
    markets = filter_markets_for_date(kalshi.get_markets(series), market_date)
    brackets = parse_brackets_from_markets(markets)
    tickers = [bracket.ticker for bracket in brackets]
    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, data) for ticker, data in orderbooks.items()}
    return {
        "kalshi": kalshi,
        "market_date": market_date,
        "markets": markets,
        "brackets": brackets,
        "tops": tops,
        "orderbooks": orderbooks,
    }


def _split_csv_option(raw: str | None, default: list[str]) -> list[str]:
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _provider_model_map(
    settings: Settings,
    providers_raw: str | None,
    models_raw: str | None,
) -> tuple[list[str], dict[str, list[str]]]:
    providers = _split_csv_option(providers_raw, settings.model_estimate_default_providers)
    requested = _split_csv_option(models_raw, [])
    expanded: set[str] = set()
    for item in requested:
        if item == "current":
            expanded.add("current_weighted_blend")
        elif item == "open_meteo":
            expanded.update(settings.model_estimate_default_models.get("open_meteo", []))
        elif item == "noaa_herbie":
            expanded.update(settings.model_estimate_default_models.get("noaa_herbie", []))
        else:
            expanded.add(item)

    model_map: dict[str, list[str]] = {}
    for provider in providers:
        defaults = settings.model_estimate_default_models.get(provider, [])
        if expanded:
            selected = [model_id for model_id in defaults if model_id in expanded]
            extras = []
            for model_id in expanded:
                if model_id in defaults:
                    continue
                try:
                    source = get_model_source(model_id)
                except ValueError:
                    continue
                if provider == "open_meteo" and source.provider == "open_meteo":
                    extras.append(model_id)
                elif (
                    provider == "noaa_herbie"
                    and source.provider == "noaa_herbie"
                    and model_id in (settings.direct_noaa_models.get("models") or {})
                ):
                    extras.append(model_id)
            model_map[provider] = selected + extras
        else:
            model_map[provider] = list(defaults)
    return providers, model_map


def _estimates_from_weather(
    settings: Settings,
    station: str,
    providers_raw: str | None,
    models_raw: str | None,
    weather: Any,
    forecast: Any,
    market_date: date,
) -> list[ModelEstimate]:
    providers, model_map = _provider_model_map(settings, providers_raw, models_raw)
    window_start_utc = utc_now()
    _day_start, window_end_utc = lax_climate_day_utc(market_date)
    estimates: list[ModelEstimate] = []
    base_estimates = current_and_open_meteo_estimates(
        station=station,
        market_date=market_date,
        weather=weather,
        model_maxes_f=forecast.model_maxes_f,
        successful_models=forecast.successful_models,
        failed_models=forecast.failed_models,
        forecast_window_start_utc=window_start_utc,
        forecast_window_end_utc=window_end_utc,
        latitude=LAX_LATITUDE,
        longitude=LAX_LONGITUDE,
    )
    for estimate in base_estimates:
        if estimate.provider not in providers:
            continue
        allowed = model_map.get(estimate.provider, [])
        if allowed and estimate.model_id not in allowed:
            continue
        estimates.append(estimate)

    if "noaa_herbie" in providers:
        noaa_models = model_map.get("noaa_herbie", [])
        herbie = HerbieModelClient(
            cache_dir=settings.herbie_cache_dir,
            max_forecast_hours=settings.max_forecast_hours,
            model_configs=settings.direct_noaa_models.get("models"),
        )
        if settings.enable_direct_noaa_models:
            direct_lat = float(settings.direct_noaa_models.get("station_lat", LAX_LATITUDE))
            direct_lon = float(settings.direct_noaa_models.get("station_lon", LAX_LONGITUDE))
            estimates.extend(
                herbie.fetch_estimates(
                    station=station,
                    market_date=market_date.isoformat(),
                    observed_high_so_far_f=weather.observed_high_so_far_f,
                    forecast_window_start_utc=window_start_utc,
                    forecast_window_end_utc=window_end_utc,
                    latitude=direct_lat,
                    longitude=direct_lon,
                    models=noaa_models,
                )
            )
        else:
            direct_lat = float(settings.direct_noaa_models.get("station_lat", LAX_LATITUDE))
            direct_lon = float(settings.direct_noaa_models.get("station_lon", LAX_LONGITUDE))
            estimates.extend(
                herbie.unavailable_estimates(
                    station=station,
                    market_date=market_date.isoformat(),
                    observed_high_so_far_f=weather.observed_high_so_far_f,
                    forecast_window_start_utc=window_start_utc,
                    forecast_window_end_utc=window_end_utc,
                    latitude=direct_lat,
                    longitude=direct_lon,
                    models=noaa_models,
                    error_message="Direct NOAA/Herbie models are disabled by configuration.",
                )
            )
    return estimates


def _model_estimates_payload(
    settings: Settings,
    series: str,
    station: str,
    target_date: date | None = None,
    providers_raw: str | None = None,
    models_raw: str | None = None,
    include_probabilities: bool = False,
    residual_sigma: float | None = None,
    only_successful: bool = False,
    show_failures: bool = True,
    store_results: bool = False,
) -> dict[str, Any]:
    if include_probabilities:
        ctx = _prediction_context(settings, series, station, target_date)
        weather = ctx["weather"]
        forecast = ctx["forecast"]
        market_date = ctx["market_date"]
        brackets = ctx["brackets"]
        tops = ctx["tops"]
        markets = ctx["markets"]
    else:
        market_date = target_date or current_lax_market_date()
        weather, forecast = _weather_context(settings, station, market_date)
        brackets = []
        tops = {}
        markets = []

    estimates = _estimates_from_weather(
        settings,
        station,
        providers_raw,
        models_raw,
        weather,
        forecast,
        market_date,
    )
    residual_sigma_used = (
        float(residual_sigma)
        if residual_sigma is not None
        else float(settings.model_estimate_probability_residual_sigma_f)
    )
    probabilities = (
        probabilities_for_estimates(
            estimates,
            brackets,
            tops,
            residual_sigma_f=residual_sigma_used,
            sample_count=settings.monte_carlo_samples,
        )
        if include_probabilities and brackets
        else []
    )

    stored_estimate_ids: dict[str, int] = {}
    stored_probability_ids: list[int] = []
    if store_results:
        store_obj = _store(settings)
        for estimate in estimates:
            stored_estimate_ids[estimate_key(estimate)] = store_obj.save_model_estimate(estimate)
        for probability in probabilities:
            record = probability.to_record()
            record["estimate_id"] = stored_estimate_ids.get(f"{probability.provider}:{probability.model_id}")
            stored_probability_ids.append(store_obj.save_model_estimate_probability(record))

    display_estimates = estimates
    if only_successful:
        display_estimates = [estimate for estimate in estimates if estimate.successful]
    elif not show_failures:
        display_estimates = [estimate for estimate in estimates if estimate.successful]
    display_keys = {estimate_key(estimate) for estimate in display_estimates}
    display_probabilities = [
        probability
        for probability in probabilities
        if f"{probability.provider}:{probability.model_id}" in display_keys
    ]

    return {
        "generated_at_utc": utc_now(),
        "series": series,
        "station": station,
        "market_date": market_date,
        "observed_high_so_far_f": weather.observed_high_so_far_f,
        "latest_observation_utc": weather.latest_observation_utc,
        "forecast_window_start_utc": utc_now(),
        "forecast_window_end_utc": lax_climate_day_utc(market_date)[1],
        "current_production_estimate_f": weather.model_future_high_f,
        "markets_count": len(markets),
        "bracket_count": len(brackets),
        "residual_sigma_f": residual_sigma_used,
        "estimates": [estimate.to_record() for estimate in display_estimates],
        "all_estimate_count": len(estimates),
        "probabilities": [probability.to_record() for probability in display_probabilities],
        "stored": store_results,
        "stored_estimate_ids": stored_estimate_ids,
        "stored_probability_ids": stored_probability_ids,
        "open_meteo": forecast_diagnostics(forecast),
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _model_estimates_text(payload: dict[str, Any]) -> str:
    lines = [
        f"MODEL ESTIMATES - {payload['station']} HIGH TEMP",
        "",
        f"Observed high so far: {_fmt_f(payload.get('observed_high_so_far_f'))}",
        f"Market date: {payload.get('market_date')}",
        f"Forecast window end UTC: {payload.get('forecast_window_end_utc')}",
        f"Current production estimate: {_fmt_f(payload.get('current_production_estimate_f'))}",
        "",
        "Provider        Model                    Future high   Settlement estimate   Status",
    ]
    for row in payload.get("estimates", []):
        status = "ok" if row.get("successful") else f"unavailable: {row.get('error_message')}"
        lines.append(
            f"{str(row.get('provider')):<15} {str(row.get('model_id')):<24} "
            f"{_fmt_f(row.get('future_high_f')):<13} {_fmt_f(row.get('settlement_high_estimate_f')):<21} {status}"
        )
    if payload.get("probabilities"):
        lines.append("")
        lines.append("Probability rows are included in JSON/CSV output. Use model-probabilities for the full bracket view.")
    return "\n".join(lines)


def _model_probabilities_text(payload: dict[str, Any], top_n: int | None = None) -> str:
    lines = [
        f"MODEL PROBABILITIES - {payload['station']}",
        "",
        f"Market date: {payload.get('market_date')}",
        f"Residual sigma used: {payload.get('residual_sigma_f')}",
    ]
    estimates_by_key = {
        f"{row['provider']}:{row['model_id']}": row for row in payload.get("estimates", [])
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in payload.get("probabilities", []):
        grouped[f"{row['provider']}:{row['model_id']}"].append(row)
    for key, rows in grouped.items():
        estimate = estimates_by_key.get(key, {})
        lines.extend(
            [
                "",
                f"Model: {key}, future high {_fmt_f(estimate.get('future_high_f'))}, "
                f"settlement estimate {_fmt_f(estimate.get('settlement_high_estimate_f'))}",
                "Bracket             p_yes    YES ask  NO ask   best side  best edge  clears hurdle",
            ]
        )
        sorted_rows = sorted(
            rows,
            key=lambda row: abs(Decimal(str(_best_edge(row)[1] or "0"))),
            reverse=True,
        )
        if top_n is not None:
            sorted_rows = sorted_rows[:top_n]
        for row in sorted_rows:
            best_side, best_edge = _best_edge(row)
            hurdle = Decimal("0")
            clears = best_edge is not None and best_edge > hurdle
            lines.append(
                f"{str(row.get('bracket_label')):<19} {float(row.get('p_yes') or 0):<8.3f} "
                f"{str(row.get('yes_ask') or ''):<8} {str(row.get('no_ask') or ''):<8} "
                f"{str(best_side or 'none'):<10} {str(best_edge or ''):<10} {clears}"
            )
    if not grouped:
        lines.append("")
        lines.append("No probability rows are available. Check provider failures or missing market brackets.")
    return "\n".join(lines)


def _resolve_telemetry_market_date(target_date: str | None, tomorrow: bool) -> date:
    if target_date and tomorrow:
        raise typer.BadParameter("Use either --target-date or --tomorrow, not both.")
    current_market_date = current_lax_market_date()
    if tomorrow:
        return current_market_date + timedelta(days=1)
    if not target_date:
        return current_market_date
    try:
        return date.fromisoformat(target_date)
    except ValueError as exc:
        raise typer.BadParameter("--target-date must use YYYY-MM-DD.") from exc


def _decimal_probability_to_cents(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)) * Decimal("100"))
    except Exception:  # noqa: BLE001
        return None


def _telemetry_market_payload(
    settings: Settings,
    series: str,
    market_date: date,
) -> dict[str, Any]:
    kalshi = _kalshi(settings)
    markets = filter_markets_for_date(kalshi.get_markets(series), market_date)
    brackets = parse_brackets_from_markets(markets)
    tickers = [bracket.ticker for bracket in brackets]
    orderbooks = kalshi.get_multiple_orderbooks(tickers, depth=1) if tickers else {}
    tops = {ticker: parse_orderbook_top(ticker, data) for ticker, data in orderbooks.items()}
    bracket_rows = []
    markets_by_ticker = {str(market.get("ticker")): market for market in markets}
    for bracket in sorted(
        brackets,
        key=lambda row: _snapshot_bracket_sort_key(
            {"bracket_label": row.label, "lower_f": row.lo_f, "upper_f": row.hi_f}
        ),
    ):
        top = tops.get(bracket.ticker)
        bracket_rows.append(
            {
                "market_ticker": bracket.ticker,
                "event_ticker": markets_by_ticker.get(bracket.ticker, {}).get("event_ticker"),
                "bracket_label": _canonical_bracket_label(
                    {"bracket_label": bracket.label, "lower_f": bracket.lo_f, "upper_f": bracket.hi_f}
                ),
                "source_bracket_label": bracket.label,
                "bracket_lower_f": bracket.lo_f,
                "bracket_upper_f": bracket.hi_f,
                "yes_bid_cents": _decimal_probability_to_cents(top.yes_bid) if top else None,
                "yes_ask_cents": _decimal_probability_to_cents(top.yes_ask) if top else None,
                "no_bid_cents": _decimal_probability_to_cents(top.no_bid) if top else None,
                "no_ask_cents": _decimal_probability_to_cents(top.no_ask) if top else None,
                "raw_market": markets_by_ticker.get(bracket.ticker),
            }
        )
    return {
        "series": series,
        "market_date": market_date.isoformat(),
        "market_count": len(markets),
        "bracket_count": len(brackets),
        "markets": markets,
        "brackets": bracket_rows,
        "orderbooks": orderbooks,
        "orderbook_tops": {ticker: asdict(top) for ticker, top in tops.items()},
    }


def _parse_awc_metar_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _awc_metars_to_observation_rows(
    raw: Any,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    source_rows = raw.get("data", []) if isinstance(raw, dict) else raw
    rows: list[dict[str, Any]] = []
    for item in source_rows if isinstance(source_rows, list) else []:
        if not isinstance(item, dict):
            continue
        observed_at = _parse_awc_metar_time(
            item.get("obsTime") or item.get("reportTime") or item.get("receiptTime")
        )
        if observed_at is None or observed_at < start_utc or observed_at > end_utc:
            continue
        temp_c = item.get("temp")
        if temp_c is None:
            continue
        try:
            temp_f = float(temp_c) * 9.0 / 5.0 + 32.0
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "timestamp_utc": observed_at.isoformat(),
                "temp_c": float(temp_c),
                "temp_f": temp_f,
                "raw_message": item.get("rawOb") or item.get("raw"),
            }
        )
    return sorted(rows, key=lambda row: str(row["timestamp_utc"]))


def _awc_observations_payload(
    settings: Settings,
    station: str,
    market_date: date,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    hours = max(1, min(72, math.ceil((end_utc - start_utc).total_seconds() / 3600) + 2))
    url = "https://aviationweather.gov/api/data/metar"
    params = {"ids": station, "format": "json", "hours": hours}
    import requests

    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": settings.user_agent},
        timeout=30,
    )
    response.raise_for_status()
    raw = response.json()
    rows = _awc_metars_to_observation_rows(raw, start_utc=start_utc, end_utc=end_utc)
    latest = rows[-1] if rows else None
    return {
        "station": station,
        "market_date": market_date.isoformat(),
        "status": "ok",
        "source": "awc_metar",
        "request": {"url": url, "params": params},
        "observations": rows,
        "observation_count": len(rows),
        "latest_observation_utc": latest.get("timestamp_utc") if latest else None,
        "latest_temp_f": latest.get("temp_f") if latest else None,
        "high_so_far_f": max((row["temp_f"] for row in rows), default=None),
        "raw_response": raw,
    }


def _nws_observations_payload(
    settings: Settings,
    station: str,
    market_date: date,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    client = _nws(settings)
    url = f"{client.api_base}/stations/{station}/observations"
    params = {
        "start": start_utc.isoformat().replace("+00:00", "Z"),
        "end": end_utc.isoformat().replace("+00:00", "Z"),
        "limit": 500,
    }
    response = client.session.get(url, params=params, timeout=30)
    response.raise_for_status()
    raw = response.json()
    frame = observations_json_to_frame(raw)
    rows: list[dict[str, Any]] = []
    if not frame.empty:
        for row in frame.to_dict(orient="records"):
            timestamp = row.get("timestamp_utc")
            if hasattr(timestamp, "isoformat"):
                row["timestamp_utc"] = timestamp.isoformat()
            rows.append(row)
        latest_row = frame.loc[frame["timestamp_utc"].idxmax()]
        latest_time = latest_row["timestamp_utc"]
        latest_observation = latest_time.isoformat() if hasattr(latest_time, "isoformat") else str(latest_time)
        latest_temp = float(latest_row["temp_f"])
        high_so_far = float(frame["temp_f"].max())
    else:
        latest_observation = None
        latest_temp = None
        high_so_far = None
    return {
        "station": station,
        "market_date": market_date.isoformat(),
        "status": "ok",
        "source": "nws_station_observations",
        "request": {"url": url, "params": params},
        "observations": rows,
        "observation_count": len(rows),
        "latest_observation_utc": latest_observation,
        "latest_temp_f": latest_temp,
        "high_so_far_f": high_so_far,
        "raw_response": raw,
    }


def _telemetry_observations_payload(
    settings: Settings,
    station: str,
    market_date: date,
) -> dict[str, Any]:
    now = utc_now()
    current_market_date = current_lax_market_date(now)
    start_utc, end_utc = lax_climate_day_utc(market_date)
    if market_date > current_market_date:
        return {
            "station": station,
            "market_date": market_date.isoformat(),
            "status": "future_market_date",
            "observations": [],
            "observation_count": 0,
            "latest_observation_utc": None,
            "latest_temp_f": None,
            "high_so_far_f": None,
            "source": "station_observations",
            "raw_response": None,
        }
    end = min(now, end_utc)
    try:
        awc_payload = _awc_observations_payload(settings, station, market_date, start_utc, end)
        if awc_payload.get("observation_count"):
            return awc_payload
        raise ValueError("AWC returned no observations for requested KLAX window")
    except Exception as awc_exc:  # noqa: BLE001
        payload = _nws_observations_payload(settings, station, market_date, start_utc, end)
        payload["fallback_from"] = "awc_metar"
        payload["fallback_error"] = str(awc_exc)
        return payload


def _telemetry_outcome_due(settings: Settings, market_date: date, now: datetime | None = None) -> bool:
    now_utc = now or utc_now()
    _start_utc, end_utc = lax_climate_day_utc(market_date)
    return now_utc >= end_utc + timedelta(hours=settings.settlement_buffer_hours)


def _telemetry_official_outcome_payload(
    settings: Settings,
    store_obj: SQLiteStore,
    station: str,
    market_date: date,
    *,
    fetch_if_due: bool = True,
) -> dict[str, Any]:
    existing = store_obj.load_official_outcomes(
        station=station,
        start_date=market_date.isoformat(),
        end_date=market_date.isoformat(),
    )
    if existing:
        row = existing[0]
        return {
            "status": "stored",
            "official_high_f": row.get("official_high_f"),
            "source": row.get("source"),
            "source_url": row.get("source_url"),
            "outcome_id": row.get("id"),
        }
    if not fetch_if_due or not _telemetry_outcome_due(settings, market_date):
        return {"status": "pending", "official_high_f": None, "reason": "not_due_yet"}
    try:
        outcome = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url).fetch_daily_high(
            station,
            market_date,
        )
        outcome_id = store_obj.save_official_outcome(
            station,
            market_date,
            outcome.metric,
            outcome.official_high_f,
            outcome.source,
            source_url=outcome.source_url,
            source_text=outcome.source_text,
        )
        return {
            "status": "fetched",
            "official_high_f": outcome.official_high_f,
            "source": outcome.source,
            "source_url": outcome.source_url,
            "outcome_id": outcome_id,
        }
    except OutcomeUnavailableError as exc:
        return {"status": "unavailable", "official_high_f": None, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "official_high_f": None, "reason": str(exc)}


def _telemetry_recent_outcomes(
    settings: Settings,
    store_obj: SQLiteStore,
    station: str,
    through_date: date,
    *,
    days: int,
) -> list[dict[str, Any]]:
    if days <= 0:
        return []
    start_date = through_date - timedelta(days=days - 1)
    rows = []
    for offset in range(days):
        market_date = start_date + timedelta(days=offset)
        outcome = _telemetry_official_outcome_payload(settings, store_obj, station, market_date)
        rows.append({"market_date": market_date.isoformat(), **outcome})
    return rows


def _telemetry_bracket_for_temperature(value: Any, market_brackets: list[dict[str, Any]]) -> str | None:
    temperature = _float_or_none(value)
    if temperature is None:
        return None
    # Kalshi daily high brackets settle on whole-degree highs; METAR/model values can be decimal F.
    settlement_temperature = math.floor(temperature + 0.5)
    for bracket in market_brackets:
        lower = _float_or_none(bracket.get("bracket_lower_f", bracket.get("lower_f")))
        upper = _float_or_none(bracket.get("bracket_upper_f", bracket.get("upper_f")))
        if lower is None and upper is not None and settlement_temperature <= upper:
            return str(bracket.get("bracket_label"))
        if upper is None and lower is not None and settlement_temperature >= lower:
            return str(bracket.get("bracket_label"))
        if lower is not None and upper is not None and lower <= settlement_temperature <= upper:
            return str(bracket.get("bracket_label"))
    return None


def _telemetry_top_probability_by_model(probabilities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in probabilities:
        key = f"{row.get('provider')}:{row.get('model_id')}"
        probability = _float_or_none(row.get("p_yes")) or 0.0
        if key not in grouped or probability > float(grouped[key].get("p_yes") or 0):
            grouped[key] = row
    return grouped


def _telemetry_model_rows(model_payload: dict[str, Any], market_payload: dict[str, Any]) -> list[dict[str, Any]]:
    market_brackets = market_payload.get("brackets") or []
    top_probability_by_model = _telemetry_top_probability_by_model(model_payload.get("probabilities") or [])
    rows = []
    for estimate in model_payload.get("estimates") or []:
        key = f"{estimate.get('provider')}:{estimate.get('model_id')}"
        estimate_high = estimate.get("future_high_f")
        settlement_high = estimate.get("settlement_high_estimate_f")
        top_probability = top_probability_by_model.get(key)
        rows.append(
            {
                "provider": estimate.get("provider"),
                "model_id": estimate.get("model_id"),
                "model_name": estimate.get("model_name"),
                "model_family": estimate.get("model_family"),
                "successful": bool(estimate.get("successful")),
                "error_message": estimate.get("error_message"),
                "future_high_f": estimate.get("future_high_f"),
                "settlement_high_estimate_f": settlement_high,
                "estimated_bracket": _telemetry_bracket_for_temperature(estimate_high, market_brackets),
                "settlement_bracket": _telemetry_bracket_for_temperature(settlement_high, market_brackets),
                "top_probability_bracket": (
                    _canonical_bracket_label(top_probability.get("bracket_label")) if top_probability else None
                ),
                "top_probability": top_probability.get("p_yes") if top_probability else None,
                "source": estimate.get("source"),
                "source_url": estimate.get("source_url"),
                "run_utc": estimate.get("run_utc"),
                "cycle_utc": estimate.get("cycle_utc"),
                "forecast_window_start_utc": estimate.get("forecast_window_start_utc"),
                "forecast_window_end_utc": estimate.get("forecast_window_end_utc"),
                "forecast_hours_used": estimate.get("forecast_hours_used") or [],
                "observed_high_so_far_f": estimate.get("observed_high_so_far_f"),
                "latitude": estimate.get("latitude"),
                "longitude": estimate.get("longitude"),
                "details_json": estimate.get("details_json") or {},
            }
        )
    return rows


def _canonicalize_model_probability_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = []
    for row in rows:
        fixed = dict(row)
        fixed["source_bracket_label"] = row.get("bracket_label")
        fixed["bracket_label"] = _canonical_bracket_label(
            {
                "bracket_label": row.get("bracket_label"),
                "lower_f": row.get("bracket_lower_f"),
                "upper_f": row.get("bracket_upper_f"),
            }
        )
        canonical.append(fixed)
    return canonical


def _store_telemetry_model_rows(store_obj: SQLiteStore, model_payload: dict[str, Any]) -> dict[str, Any]:
    stored_estimate_ids = {}
    stored_probability_ids = []
    for estimate in model_payload.get("estimates") or []:
        estimate_id = store_obj.save_model_estimate(estimate)
        stored_estimate_ids[f"{estimate.get('provider')}:{estimate.get('model_id')}"] = estimate_id
    for probability in model_payload.get("probabilities") or []:
        probability = dict(probability)
        probability["estimate_id"] = stored_estimate_ids.get(f"{probability.get('provider')}:{probability.get('model_id')}")
        stored_probability_ids.append(store_obj.save_model_estimate_probability(probability))
    return {
        "stored_estimate_ids": stored_estimate_ids,
        "stored_probability_ids": stored_probability_ids,
    }


def _model_telemetry_payload(
    settings: Settings,
    *,
    series: str,
    station: str,
    target_date: date,
    providers: str | None,
    models: str | None,
    residual_sigma: float | None,
    store_results: bool,
    include_raw: bool,
    finalize_recent_days: int,
) -> dict[str, Any]:
    store_obj = _store(settings)
    generated_at = utc_now()
    model_payload = _model_estimates_payload(
        settings,
        series,
        station,
        target_date=target_date,
        providers_raw=providers,
        models_raw=models,
        include_probabilities=True,
        residual_sigma=residual_sigma,
        only_successful=False,
        show_failures=True,
        store_results=False,
    )
    model_payload["probabilities"] = _canonicalize_model_probability_rows(model_payload.get("probabilities") or [])
    market_payload = _telemetry_market_payload(settings, series, target_date)
    observations = _telemetry_observations_payload(settings, station, target_date)
    official_outcome = _telemetry_official_outcome_payload(settings, store_obj, station, target_date)
    recent_outcomes = _telemetry_recent_outcomes(
        settings,
        store_obj,
        station,
        max(target_date, current_lax_market_date(generated_at)),
        days=finalize_recent_days,
    )
    model_rows = _telemetry_model_rows(model_payload, market_payload)
    storage_ids: dict[str, Any] = {}
    weather_snapshot = {
        "station_id": station,
        "timestamp_utc": generated_at,
        "observed_high_so_far_f": observations.get("high_so_far_f"),
        "latest_observation_utc": observations.get("latest_observation_utc"),
        "observation_count": observations.get("observation_count"),
        "model_future_high_f": model_payload.get("current_production_estimate_f"),
        "model_details": model_payload.get("open_meteo"),
    }
    if store_results:
        storage_ids["market_snapshot_id"] = store_obj.save_market_snapshot(
            series,
            {
                "series": series,
                "market_date": target_date.isoformat(),
                "markets": market_payload.get("markets"),
                "orderbooks": market_payload.get("orderbooks"),
                "orderbook_tops": market_payload.get("orderbook_tops"),
                "brackets": market_payload.get("brackets"),
                "record_only": True,
            },
        )
        storage_ids["weather_snapshot_id"] = store_obj.save_weather_snapshot(station, weather_snapshot)
        model_storage = _store_telemetry_model_rows(store_obj, model_payload)
        storage_ids.update(model_storage)
        model_payload["stored"] = True
        model_payload["stored_estimate_ids"] = model_storage["stored_estimate_ids"]
        model_payload["stored_probability_ids"] = model_storage["stored_probability_ids"]
    else:
        model_payload["stored"] = False
    payload = {
        "schema_version": "model_telemetry_v1",
        "generated_at_utc": generated_at,
        "record_only": True,
        "live_trading_enabled": False,
        "paper_trading": False,
        "llm_trader_used": False,
        "candidate_trade_board_created": False,
        "series": series,
        "station": station,
        "market_date": target_date.isoformat(),
        "providers": providers,
        "models": models,
        "direct_noaa_enabled": settings.enable_direct_noaa_models,
        "residual_sigma_f": model_payload.get("residual_sigma_f"),
        "observed_high_so_far_f": observations.get("high_so_far_f"),
        "latest_observation_utc": observations.get("latest_observation_utc"),
        "latest_observed_temp_f": observations.get("latest_temp_f"),
        "final_high": official_outcome,
        "recent_final_highs": recent_outcomes,
        "model_count": len(model_rows),
        "successful_model_count": sum(1 for row in model_rows if row.get("successful")),
        "models_by_estimated_high": model_rows,
        "market": market_payload,
        "observations": observations,
        "model_payload": model_payload,
        "storage_ids": storage_ids,
        "stored": store_results,
    }
    if not include_raw:
        payload["market"] = {key: value for key, value in market_payload.items() if key not in {"markets", "orderbooks"}}
        payload["observations"] = {key: value for key, value in observations.items() if key != "raw_response"}
    if store_results:
        storage_ids["telemetry_snapshot_id"] = store_obj.save_snapshot("model_telemetry", payload)
    return payload


def _model_telemetry_text(payload: dict[str, Any]) -> str:
    lines = [
        "Kalshi Weather Model Telemetry",
        "==============================",
        (
            f"Time: {_fmt_utc_minute(payload.get('generated_at_utc'))} | "
            f"Series: {payload.get('series')} | Station: {payload.get('station')} | "
            f"Date: {payload.get('market_date')}"
        ),
        "Mode: record_only | Live trading: DISABLED | Paper orders: DISABLED | LLM trader: DISABLED",
        (
            f"Models: {payload.get('successful_model_count', 0)}/{payload.get('model_count', 0)} ok | "
            f"Market brackets: {(payload.get('market') or {}).get('bracket_count', 0)} | "
            f"High so far: {_fmt_f_short(payload.get('observed_high_so_far_f'))} | "
            f"Final high: {_fmt_f_short((payload.get('final_high') or {}).get('official_high_f'))}"
        ),
        (
            f"Stored snapshot: {(payload.get('storage_ids') or {}).get('telemetry_snapshot_id', '--')} | "
            f"Estimates stored: {len((payload.get('storage_ids') or {}).get('stored_estimate_ids', {}))} | "
            f"Probabilities stored: {len((payload.get('storage_ids') or {}).get('stored_probability_ids', []))}"
        ),
        "",
        "Model                 High       Bracket  Top     P(top)  Status",
        "--------------------  ---------  -------  ------  ------  ------------------------------",
    ]
    for row in payload.get("models_by_estimated_high") or []:
        key = f"{row.get('provider')}:{row.get('model_id')}"
        status = "ok" if row.get("successful") else f"failed: {row.get('error_message') or '--'}"
        lines.append(
            f"{_fit_cell(key, 20)}  "
            f"{_fit_cell(_fmt_f_short(row.get('settlement_high_estimate_f')), 9)}  "
            f"{_fit_cell(row.get('estimated_bracket') or '--', 7)}  "
            f"{_fit_cell(row.get('top_probability_bracket') or '--', 6)}  "
            f"{_fit_cell(_fmt_percent(row.get('top_probability')), 6, align='right')}  "
            f"{_short_label(status, max_len=30)}"
        )
    return "\n".join(lines)


def _model_telemetry_run_line(iteration: int, payload: dict[str, Any]) -> str:
    final_high = (payload.get("final_high") or {}).get("official_high_f")
    return (
        f"{iteration:04d} | {_fmt_utc_minute(payload.get('generated_at_utc'))} | "
        f"date {payload.get('market_date')} | "
        f"models {payload.get('successful_model_count', 0)}/{payload.get('model_count', 0)} | "
        f"market {(payload.get('market') or {}).get('bracket_count', 0)} | "
        f"high-so-far {_fmt_f_short(payload.get('observed_high_so_far_f'))} | "
        f"final {_fmt_f_short(final_high)} | "
        f"snapshot {(payload.get('storage_ids') or {}).get('telemetry_snapshot_id', '--')}"
    )


def _registry_table_text(rows: list[dict[str, Any]]) -> str:
    lines = [
        "Weather Model Source Registry",
        "=============================",
        "Key                     Provider      Family        Group           Type               Default  Notes",
        "----------------------  ------------  ------------  --------------  -----------------  -------  ------------------------------",
    ]
    for row in rows:
        lines.append(
            f"{_fit_cell(row.get('model_key'), 22)}  "
            f"{_fit_cell(row.get('provider'), 12)}  "
            f"{_fit_cell(row.get('model_family'), 12)}  "
            f"{_fit_cell(row.get('independence_group'), 14)}  "
            f"{_fit_cell(row.get('source_type'), 17)}  "
            f"{_fit_cell(row.get('enabled_by_default'), 7)}  "
            f"{_short_label(row.get('notes') or '', max_len=30)}"
        )
    return "\n".join(lines)


def _resolve_record_target_date(target_date: str | None, timezone_name: str) -> date:
    if target_date is None or target_date.strip().lower() in {"", "auto", "today"}:
        return datetime.now(ZoneInfo(timezone_name)).date()
    try:
        return date.fromisoformat(target_date)
    except ValueError as exc:
        raise typer.BadParameter("--target-date must be auto or YYYY-MM-DD.") from exc


def _bucket_start_utc(value: Any, *, interval_seconds: int = 900) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    epoch = int(dt.timestamp())
    bucket_epoch = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(bucket_epoch, timezone.utc).replace(microsecond=0).isoformat()


def _record_parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(LAX_TIMEZONE))
    return parsed


def _record_number(value: Any) -> float | None:
    number = _float_or_none(value)
    if number is None or math.isnan(number):
        return None
    return float(number)


def _record_settlement_high(estimated_high_f: float | None, observed_high_so_far_f: Any) -> float | None:
    estimate = _record_number(estimated_high_f)
    observed = _record_number(observed_high_so_far_f)
    if estimate is None:
        return observed
    if observed is None:
        return estimate
    return max(estimate, observed)


def _record_probability_brackets(market_brackets: list[dict[str, Any]]) -> list[Bracket]:
    brackets: list[Bracket] = []
    for row in market_brackets:
        ticker = str(row.get("market_ticker") or row.get("ticker") or row.get("bracket_label") or "")
        label = _canonical_bracket_label(
            {
                "bracket_label": row.get("bracket_label"),
                "lower_f": row.get("bracket_lower_f"),
                "upper_f": row.get("bracket_upper_f"),
            }
        )
        lower = _record_number(row.get("bracket_lower_f"))
        upper = _record_number(row.get("bracket_upper_f"))
        brackets.append(
            Bracket(
                ticker=ticker,
                label=label,
                lo_f=int(lower) if lower is not None else None,
                hi_f=int(upper) if upper is not None else None,
            )
        )
    return brackets


def _record_top_probability(
    *,
    estimated_high_f: float | None,
    observed_high_so_far_f: Any,
    market_brackets: list[dict[str, Any]],
    residual_sigma_f: Any,
    settings: Settings,
) -> tuple[str | None, float | None]:
    estimate = _record_number(estimated_high_f)
    if estimate is None or not market_brackets:
        return None, None
    observed = _record_number(observed_high_so_far_f)
    sigma = _record_number(residual_sigma_f) or float(settings.model_estimate_probability_residual_sigma_f)
    brackets = _record_probability_brackets(market_brackets)
    if not brackets:
        return None, None
    samples = settlement_high_samples(
        future_high_center_f=estimate,
        observed_high_so_far_f=observed if observed is not None else float("nan"),
        residual_sigma_f=sigma,
        sample_count=settings.monte_carlo_samples,
    )
    probabilities = normalize_probabilities(bracket_probabilities(samples, brackets))
    if not probabilities:
        return None, None
    ticker, probability = max(probabilities.items(), key=lambda item: item[1])
    labels = {bracket.ticker: bracket.label for bracket in brackets}
    return labels.get(ticker), probability


def _record_percentile_values(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p25": None, "p50": None, "p75": None, "p90": None}
    ordered = sorted(float(value) for value in values)
    result: dict[str, float | None] = {}
    for percentile in (10, 25, 50, 75, 90):
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


def _record_source_kind(source: Any, row: dict[str, Any], *, successful: bool, estimated_high: Any) -> str:
    if not successful:
        return "unavailable"
    if estimated_high is None:
        return "fallback"
    model_key = str(row.get("model_id") or row.get("model_key") or "")
    source_name = str(row.get("source") or "").lower()
    source_type = str(getattr(source, "source_type", "") or row.get("source_type") or "").lower()
    provider = str(getattr(source, "provider", "") or row.get("provider") or "").lower()
    if source_type == "ensemble_spread" or model_key.endswith("_spread"):
        return "ensemble_spread"
    if "ensemble" in source_type or "ensemble" in source_name:
        return "ensemble_mean"
    if source_type == "ensemble_percentile":
        return "ensemble_spread"
    if source_type == "official_forecast" or provider == "nws" or "nws" in source_name:
        return "official_forecast"
    if source_type == "station_guidance" or provider == "noaa_mdl" or "mdl" in source_name:
        return "station_guidance"
    if provider == "noaa_herbie" or source_name == "herbie":
        return "herbie_model"
    if provider == "open_meteo" or source_name.startswith("open_meteo"):
        return "open_meteo_model"
    if getattr(source, "is_blend", False) or provider == "internal":
        return "synthetic_blend"
    return "direct_model"


def _record_source_detail(source: Any, row: dict[str, Any]) -> str:
    kind = str(row.get("estimate_source_kind") or "")
    model_key = str(row.get("model_id") or row.get("model_key") or getattr(source, "model_key", "model"))
    if kind == "fallback":
        return "fallback to KLAX high-so-far because model estimate unavailable"
    endpoint = _record_endpoint_used(row)
    raw_model = _record_raw_model_param(source, row)
    cycle = row.get("cycle_utc") or row.get("run_utc")
    valid_count = _record_valid_times_count(row)
    parts = [kind or str(getattr(source, "fetcher_type", "") or "model"), model_key]
    if raw_model:
        parts.append(f"model={raw_model}")
    if cycle:
        parts.append(f"cycle={cycle}")
    if valid_count is not None:
        parts.append(f"valid_times={valid_count}")
    if endpoint:
        parts.append(f"endpoint={endpoint}")
    return " | ".join(str(part) for part in parts if part)


def _record_source_label(row: dict[str, Any]) -> str:
    kind = str(row.get("estimate_source_kind") or "")
    provider = str(row.get("provider") or "").lower()
    source = str(row.get("source") or "").lower()
    if kind == "fallback":
        return "fallback"
    if kind in {"ensemble_mean", "ensemble_spread"}:
        return "ens"
    if kind == "official_forecast" or provider == "nws" or source.startswith("nws"):
        return "nws"
    if kind == "station_guidance" or provider == "noaa_mdl":
        return "mos"
    if kind == "herbie_model" or provider == "noaa_herbie":
        return "herbie"
    if kind == "open_meteo_model" or provider == "open_meteo":
        return "om"
    if "blend" in kind or provider == "internal":
        return "blend"
    if row.get("fetch_status") in {"error", "missing"}:
        return "error"
    return _short_label(provider or source or "--", max_len=8)


def _record_endpoint_used(row: dict[str, Any]) -> str | None:
    if row.get("source_url"):
        return str(row.get("source_url"))
    details = row.get("details_json") or row.get("raw") or {}
    request = details.get("request") if isinstance(details, dict) else None
    if isinstance(request, dict) and request.get("url"):
        return str(request.get("url"))
    source_urls = details.get("source_urls") if isinstance(details, dict) else None
    if isinstance(source_urls, list) and source_urls:
        return str(source_urls[0])
    return None


def _record_raw_model_param(source: Any, row: dict[str, Any]) -> str | None:
    details = row.get("details_json") or row.get("raw") or {}
    if isinstance(details, dict):
        request = details.get("request")
        params = request.get("params") if isinstance(request, dict) else None
        if isinstance(params, dict) and params.get("models"):
            return str(params.get("models"))
        if details.get("source_model_id"):
            return str(details.get("source_model_id"))
        target = details.get("target")
        if isinstance(target, dict) and target.get("model"):
            return str(target.get("model"))
    candidates = getattr(source, "model_param_candidates", None) or []
    return str(candidates[0]) if candidates else None


def _record_forecast_valid_time(row: dict[str, Any]) -> str | None:
    details = row.get("details_json") or row.get("raw") or {}
    if isinstance(details, dict):
        best = details.get("best_extraction")
        if isinstance(best, dict) and best.get("valid_time_utc"):
            return str(best.get("valid_time_utc"))
        points = details.get("forecast_points")
        if isinstance(points, list) and points:
            last = points[-1]
            if isinstance(last, dict) and last.get("valid_utc"):
                return str(last.get("valid_utc"))
    return row.get("forecast_window_end_utc")


def _record_valid_times_count(row: dict[str, Any]) -> int | None:
    hours = row.get("forecast_hours_used") or []
    if isinstance(hours, list) and hours:
        return len(hours)
    details = row.get("details_json") or row.get("raw") or {}
    if isinstance(details, dict):
        if isinstance(details.get("forecast_points"), list):
            return len(details["forecast_points"])
        count = details.get("success_count") or details.get("member_count")
        try:
            return int(count) if count is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _record_probability_is_displayable(row: dict[str, Any]) -> bool:
    if row.get("is_blend") and row.get("probability_source") == "calibrated":
        return row.get("top_probability") is not None
    if row.get("probability_source") in {"ensemble_members", "spread_distribution"}:
        return row.get("top_probability") is not None
    return False


def _record_extra_telemetry_row(
    *,
    settings: Settings,
    model_key: str,
    station: str,
    target_date: date,
    payload: dict[str, Any],
    estimated_high_f: float | None,
    successful: bool,
    error_message: str | None = None,
    source_name: str | None = None,
    source_url: str | None = None,
    raw: dict[str, Any] | None = None,
    uncertainty_spread_f: float | None = None,
) -> dict[str, Any]:
    source = get_model_source(model_key)
    observation = payload.get("observation") or payload.get("observations") or {}
    observed_high = observation.get("high_so_far_f", payload.get("observed_high_so_far_f"))
    settlement_high = _record_settlement_high(estimated_high_f, observed_high)
    market_brackets = (payload.get("market") or {}).get("brackets") or []
    now = payload.get("generated_at_utc") or utc_now()
    raw_payload = raw or {}
    row = {
        "provider": source.provider,
        "model_id": model_key,
        "model_name": source.display_name,
        "model_family": source.model_family,
        "successful": bool(successful),
        "error_message": None if successful else error_message,
        "future_high_f": estimated_high_f if successful else None,
        "settlement_high_estimate_f": settlement_high if successful else None,
        "estimated_bracket": _telemetry_bracket_for_temperature(estimated_high_f, market_brackets) if successful else None,
        "settlement_bracket": _telemetry_bracket_for_temperature(settlement_high, market_brackets) if successful else None,
        "top_probability_bracket": None,
        "top_probability": None,
        "uncertainty_spread_f": uncertainty_spread_f,
        "estimate_p10_high_f": raw_payload.get("p10"),
        "estimate_p25_high_f": raw_payload.get("p25"),
        "estimate_p50_high_f": raw_payload.get("p50"),
        "estimate_p75_high_f": raw_payload.get("p75"),
        "estimate_p90_high_f": raw_payload.get("p90"),
        "source": source_name or source.provider,
        "source_url": source_url,
        "station": station,
        "market_date": target_date.isoformat(),
        "asof_utc": now.isoformat() if isinstance(now, datetime) else now,
        "details_json": raw_payload,
    }
    source_kind = _record_source_kind(source, row, successful=successful, estimated_high=estimated_high_f)
    row.update(
        {
            "estimate_source_kind": source_kind,
            "estimate_source_detail": _record_source_detail(source, row),
            "endpoint_used": _record_endpoint_used(row),
            "raw_model_param_used": _record_raw_model_param(source, row),
            "cycle_time_utc": row.get("cycle_utc"),
            "forecast_valid_time_utc": _record_forecast_valid_time(row),
            "valid_times_used_count": _record_valid_times_count(row),
            "fallback_from_model_key": None,
            "uses_observation_data": bool(source.is_blend),
            "uses_high_so_far": source_kind == "fallback",
            "is_blend": source.is_blend,
            "is_ensemble": source.is_ensemble,
            "is_direct_model": source.is_direct_model,
            "is_station_guidance": source.is_station_guidance,
            "is_synthetic": source.is_synthetic,
            "full_error_message": None if successful else error_message,
        }
    )
    return row


def _open_meteo_hourly_high_for_model(
    settings: Settings,
    *,
    model_key: str,
    target_date: date,
) -> dict[str, Any]:
    import requests

    source = get_model_source(model_key)
    candidates = [candidate for candidate in source.model_param_candidates if candidate not in {None, "auto"}]
    errors: list[str] = []
    for candidate in candidates:
        params = {
            "latitude": LAX_LATITUDE,
            "longitude": LAX_LONGITUDE,
            "timezone": LAX_TIMEZONE,
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "hourly": "temperature_2m",
            "models": candidate,
        }
        response = requests.get(settings.open_meteo_base_url, params=params, timeout=30)
        if response.status_code != 200:
            errors.append(f"{candidate}: {response.text[:160]}")
            continue
        data = response.json()
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        values = hourly.get("temperature_2m") or []
        temps: list[float] = []
        for time_text, value in zip(times, values, strict=False):
            parsed = _record_parse_datetime(time_text)
            if parsed is None:
                continue
            if parsed.astimezone(ZoneInfo(LAX_TIMEZONE)).date() != target_date:
                continue
            number = _record_number(value)
            if number is not None:
                temps.append(number)
        if temps:
            return {
                "estimated_high_f": max(temps),
                "source_url": settings.open_meteo_base_url,
                "raw": {"request": {"url": settings.open_meteo_base_url, "params": params}, "response": data},
                "source_model_id": candidate,
            }
        errors.append(f"{candidate}: no hourly temperatures for {target_date.isoformat()}")
    raise ValueError("; ".join(errors) if errors else "no Open-Meteo model candidates configured")


def _open_meteo_ensemble_stats(
    settings: Settings,
    *,
    model_key: str,
    target_date: date,
) -> dict[str, Any]:
    import requests

    source = get_model_source(model_key)
    if model_key.startswith("gefs_"):
        candidates = ["gfs_seamless", "gfs025"]
    else:
        candidates = [candidate for candidate in source.model_param_candidates if candidate not in {None, "auto"}]
    endpoint = "https://ensemble-api.open-meteo.com/v1/ensemble"
    errors: list[str] = []
    for candidate in candidates:
        params = {
            "latitude": LAX_LATITUDE,
            "longitude": LAX_LONGITUDE,
            "timezone": LAX_TIMEZONE,
            "temperature_unit": "fahrenheit",
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "daily": "temperature_2m_max",
            "models": candidate,
        }
        response = requests.get(endpoint, params=params, timeout=30)
        if response.status_code != 200:
            errors.append(f"{candidate}: {response.text[:160]}")
            continue
        data = response.json()
        daily = data.get("daily") or {}
        member_values: list[float] = []
        for key, values in daily.items():
            if not key.startswith("temperature_2m_max_member") or not isinstance(values, list) or not values:
                continue
            number = _record_number(values[0])
            if number is not None:
                member_values.append(number)
        if not member_values:
            values = daily.get("temperature_2m_max") or []
            if values:
                number = _record_number(values[0])
                if number is not None:
                    member_values.append(number)
        if member_values:
            spread = statistics.stdev(member_values) if len(member_values) >= 2 else 0.0
            percentiles = _record_percentile_values(member_values)
            return {
                "estimated_high_f": statistics.mean(member_values),
                "uncertainty_spread_f": spread,
                **{f"estimate_{key}_high_f": value for key, value in percentiles.items()},
                "source_url": endpoint,
                "raw": {
                    "request": {"url": endpoint, "params": params},
                    "member_count": len(member_values),
                    "member_values_f": member_values,
                    **percentiles,
                    "source_model_id": candidate,
                    "response": data,
                },
                "source_model_id": candidate,
            }
        errors.append(f"{candidate}: no ensemble temperature members")
    raise ValueError("; ".join(errors) if errors else "no Open-Meteo ensemble candidates configured")


def _nws_points_payload(settings: Settings) -> dict[str, Any]:
    client = _nws(settings)
    url = f"{client.api_base}/points/{LAX_LATITUDE},{LAX_LONGITUDE}"
    response = client.session.get(url, timeout=30)
    response.raise_for_status()
    return {"url": url, "response": response.json()}


def _nws_hourly_high(settings: Settings, *, target_date: date, points: dict[str, Any]) -> dict[str, Any]:
    client = _nws(settings)
    hourly_url = ((points.get("response") or {}).get("properties") or {}).get("forecastHourly")
    if not hourly_url:
        raise ValueError("NWS points response missing forecastHourly URL")
    response = client.session.get(hourly_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    temps: list[float] = []
    for period in ((data.get("properties") or {}).get("periods") or []):
        parsed = _record_parse_datetime(period.get("startTime"))
        if parsed is None or parsed.astimezone(ZoneInfo(LAX_TIMEZONE)).date() != target_date:
            continue
        number = _record_number(period.get("temperature"))
        if number is None:
            continue
        if str(period.get("temperatureUnit") or "").upper() == "C":
            number = number * 9.0 / 5.0 + 32.0
        temps.append(number)
    if not temps:
        raise ValueError(f"NWS hourly forecast had no temperatures for {target_date.isoformat()}")
    return {
        "estimated_high_f": max(temps),
        "source_url": hourly_url,
        "raw": {"points": points, "hourly": data},
    }


def _nws_grid_high(settings: Settings, *, target_date: date, points: dict[str, Any]) -> dict[str, Any]:
    client = _nws(settings)
    grid_url = ((points.get("response") or {}).get("properties") or {}).get("forecastGridData")
    if not grid_url:
        raise ValueError("NWS points response missing forecastGridData URL")
    response = client.session.get(grid_url, timeout=30)
    response.raise_for_status()
    data = response.json()
    properties = data.get("properties") or {}
    temperature_prop = properties.get("maxTemperature") or properties.get("temperature") or {}
    unit_code = str(temperature_prop.get("uom") or "")
    temps: list[float] = []
    for item in temperature_prop.get("values") or []:
        valid_time = str(item.get("validTime") or "").split("/", 1)[0]
        parsed = _record_parse_datetime(valid_time)
        if parsed is None or parsed.astimezone(ZoneInfo(LAX_TIMEZONE)).date() != target_date:
            continue
        number = _record_number(item.get("value"))
        if number is None:
            continue
        if "degC" in unit_code:
            number = number * 9.0 / 5.0 + 32.0
        temps.append(number)
    if not temps:
        raise ValueError(f"NWS grid forecast had no temperatures for {target_date.isoformat()}")
    return {
        "estimated_high_f": max(temps),
        "source_url": grid_url,
        "raw": {"points": points, "grid": data},
    }


def _mos_station_block(text: str, station: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(station)}\s+.*?MOS GUIDANCE.*$", text)
    if not match:
        return None
    tail = text[match.start() :]
    next_match = re.search(rf"(?m)^\s*(?!{re.escape(station)}\b)[A-Z0-9]{{4}}\s+.*?MOS GUIDANCE.*$", tail[1:])
    if next_match:
        return tail[: next_match.start() + 1]
    return tail


def _parse_mos_tmp_high(
    text: str,
    *,
    station: str,
    target_date: date,
) -> dict[str, Any]:
    block = _mos_station_block(text, station)
    if not block:
        raise ValueError(f"{station} block not found in MOS product")
    header = next((line for line in block.splitlines() if line.strip()), "")
    issue_match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{4})\s+UTC", header)
    if not issue_match:
        raise ValueError("MOS product issue time not found")
    month, day, year, hhmm = issue_match.groups()
    issue_utc = datetime(
        int(year),
        int(month),
        int(day),
        int(hhmm[:2]),
        int(hhmm[2:]),
        tzinfo=timezone.utc,
    )
    hr_line = next((line for line in block.splitlines() if line.lstrip().startswith("HR ")), None)
    tmp_line = next((line for line in block.splitlines() if line.lstrip().startswith("TMP ")), None)
    if not hr_line or not tmp_line:
        raise ValueError("MOS product missing HR/TMP rows")
    hours = [int(value) for value in re.findall(r"\b\d{2}\b", hr_line)]
    temps = [int(value) for value in re.findall(r"-?\d+", tmp_line.split("TMP", 1)[1])]
    if not hours or not temps:
        raise ValueError("MOS product has no parsable hourly temperatures")
    zone = ZoneInfo(LAX_TIMEZONE)
    valid_day = issue_utc.date()
    previous_hour: int | None = None
    rows: list[dict[str, Any]] = []
    target_temps: list[float] = []
    for hour, temp in zip(hours, temps, strict=False):
        if previous_hour is not None and hour <= previous_hour:
            valid_day += timedelta(days=1)
        valid_utc = datetime(valid_day.year, valid_day.month, valid_day.day, hour, tzinfo=timezone.utc)
        previous_hour = hour
        valid_local = valid_utc.astimezone(zone)
        row = {"valid_utc": valid_utc.isoformat(), "valid_local": valid_local.isoformat(), "temp_f": float(temp)}
        rows.append(row)
        if valid_local.date() == target_date:
            target_temps.append(float(temp))
    if not target_temps:
        raise ValueError(f"MOS product had no TMP values for local date {target_date.isoformat()}")
    return {
        "estimated_high_f": max(target_temps),
        "raw": {"station_block": block, "issue_utc": issue_utc.isoformat(), "forecast_points": rows},
    }


def _nam_mos_high(
    settings: Settings,
    *,
    station: str,
    target_date: date,
) -> dict[str, Any]:
    import requests

    now = utc_now().astimezone(timezone.utc)
    product_dates = [now.date(), now.date() - timedelta(days=1)]
    cycles = [12, 0] if now.hour >= 12 else [0, 12]
    errors: list[str] = []
    for product_date in product_dates:
        for cycle in cycles:
            url = (
                "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam_mos/prod/"
                f"nam_mos.{product_date:%Y%m%d}/mdl_nammet.t{cycle:02d}z"
            )
            response = requests.get(url, headers={"User-Agent": settings.user_agent}, timeout=30)
            if response.status_code != 200:
                errors.append(f"{url}: HTTP {response.status_code}")
                continue
            try:
                parsed = _parse_mos_tmp_high(response.text, station=station, target_date=target_date)
                parsed["source_url"] = url
                parsed["raw"]["request"] = {"url": url}
                return parsed
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {exc}")
    raise ValueError("; ".join(errors) if errors else "NAM MOS product unavailable")


def _append_record_extra_model_rows(
    settings: Settings,
    *,
    payload: dict[str, Any],
    station: str,
    target_date: date,
    model_keys: list[str],
    include_raw: bool,
) -> None:
    rows = payload.setdefault("models_by_estimated_high", [])
    successful_keys = {str(row.get("model_id")) for row in rows if row.get("successful")}
    existing_keys = {str(row.get("model_id")) for row in rows}

    def upsert_row(new_row: dict[str, Any]) -> None:
        model_id = str(new_row.get("model_id") or new_row.get("model_key") or "")
        for index, row in enumerate(rows):
            existing_id = str(row.get("model_id") or row.get("model_key") or "")
            if existing_id == model_id:
                rows[index] = new_row
                break
        else:
            rows.append(new_row)
        existing_keys.add(model_id)
        if new_row.get("successful"):
            successful_keys.add(model_id)
        else:
            successful_keys.discard(model_id)

    def raw_if_requested(raw: dict[str, Any]) -> dict[str, Any]:
        return raw if include_raw else {}

    unsupported = {
        "lamp": "NOAA MDL LAMP text parser is not configured in this project yet",
        "gfs_mos": "NOAA MDL GFS MOS text parser is not configured in this project yet",
    }

    points: dict[str, Any] | None = None
    for model_key in model_keys:
        if model_key in successful_keys:
            continue
        source = get_model_source(model_key)
        if source.provider == "open_meteo" and source.is_ensemble:
            try:
                result = _open_meteo_ensemble_stats(settings, model_key=model_key, target_date=target_date)
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=result["estimated_high_f"],
                        successful=True,
                        source_name="open_meteo_ensemble",
                        source_url=result["source_url"],
                        raw=raw_if_requested(result["raw"]),
                        uncertainty_spread_f=result.get("uncertainty_spread_f"),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=None,
                        successful=False,
                        error_message=str(exc),
                        source_name="open_meteo_ensemble",
                    )
                )
            continue
        if source.provider == "open_meteo":
            try:
                result = _open_meteo_hourly_high_for_model(settings, model_key=model_key, target_date=target_date)
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=result["estimated_high_f"],
                        successful=True,
                        source_name="open_meteo",
                        source_url=result["source_url"],
                        raw=raw_if_requested(result["raw"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=None,
                        successful=False,
                        error_message=str(exc),
                        source_name="open_meteo",
                    )
                )
            continue
        if model_key in {"nws_hourly", "nws_grid_high"}:
            try:
                if points is None:
                    points = _nws_points_payload(settings)
                result = (
                    _nws_hourly_high(settings, target_date=target_date, points=points)
                    if model_key == "nws_hourly"
                    else _nws_grid_high(settings, target_date=target_date, points=points)
                )
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=result["estimated_high_f"],
                        successful=True,
                        source_name="nws_api",
                        source_url=result["source_url"],
                        raw=raw_if_requested(result["raw"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                if model_key not in existing_keys:
                    upsert_row(
                        _record_extra_telemetry_row(
                            settings=settings,
                            model_key=model_key,
                            station=station,
                            target_date=target_date,
                            payload=payload,
                            estimated_high_f=None,
                            successful=False,
                            error_message=str(exc),
                            source_name="nws_api",
                        )
                    )
            continue
        if model_key == "nam_mos":
            try:
                result = _nam_mos_high(settings, station=station, target_date=target_date)
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=result["estimated_high_f"],
                        successful=True,
                        source_name="noaa_mdl_text",
                        source_url=result["source_url"],
                        raw=raw_if_requested(result["raw"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                if model_key not in existing_keys:
                    upsert_row(
                        _record_extra_telemetry_row(
                            settings=settings,
                            model_key=model_key,
                            station=station,
                            target_date=target_date,
                            payload=payload,
                            estimated_high_f=None,
                            successful=False,
                            error_message=str(exc),
                            source_name="noaa_mdl_text",
                        )
                    )
            continue
        if source.provider == "noaa_herbie" and model_key not in existing_keys:
            configured_models = settings.direct_noaa_models.get("models") or {}
            if model_key not in configured_models:
                upsert_row(
                    _record_extra_telemetry_row(
                        settings=settings,
                        model_key=model_key,
                        station=station,
                        target_date=target_date,
                        payload=payload,
                        estimated_high_f=None,
                        successful=False,
                        error_message=f"Herbie model config is not configured for {model_key}",
                        source_name="herbie",
                    )
                )
            continue
        if model_key in unsupported and model_key not in existing_keys:
            upsert_row(
                _record_extra_telemetry_row(
                    settings=settings,
                    model_key=model_key,
                    station=station,
                    target_date=target_date,
                    payload=payload,
                    estimated_high_f=None,
                    successful=False,
                    error_message=unsupported[model_key],
                )
            )


def _record_model_row_from_telemetry(row: dict[str, Any]) -> dict[str, Any]:
    model_key = str(row.get("model_id") or row.get("model_key") or "")
    source = get_model_source(model_key) if model_key else None
    successful = bool(row.get("successful"))
    estimated_high = row.get("future_high_f")
    if not successful:
        estimated_high = None
    source_kind = _record_source_kind(source, row, successful=successful, estimated_high=estimated_high)
    converted = {
        "model_key": model_key,
        "display_name": source.display_name if source else model_key,
        "provider": source.provider if source else row.get("provider"),
        "model_family": source.model_family if source else row.get("model_family"),
        "independence_group": source.independence_group if source else row.get("model_family"),
        "source_type": source.source_type if source else "unknown",
        "fetch_status": "ok" if successful else "error",
        "estimated_high_f": estimated_high,
        "settlement_high_estimate_f": row.get("settlement_high_estimate_f") if successful else None,
        "estimated_bracket": row.get("estimated_bracket") if successful else None,
        "settlement_bracket": row.get("settlement_bracket") if successful else None,
        "top_probability_bracket": row.get("top_probability_bracket") if successful else None,
        "top_probability": row.get("top_probability") if successful else None,
        "uncertainty_spread_f": row.get("uncertainty_spread_f"),
        "estimate_p10_high_f": row.get("estimate_p10_high_f"),
        "estimate_p25_high_f": row.get("estimate_p25_high_f"),
        "estimate_p50_high_f": row.get("estimate_p50_high_f"),
        "estimate_p75_high_f": row.get("estimate_p75_high_f"),
        "estimate_p90_high_f": row.get("estimate_p90_high_f"),
        "error_message": row.get("error_message"),
        "full_error_message": row.get("full_error_message") or row.get("error_message"),
        "estimate_source_kind": source_kind,
        "endpoint_used": row.get("endpoint_used") or _record_endpoint_used(row),
        "raw_model_param_used": row.get("raw_model_param_used") or _record_raw_model_param(source, row),
        "cycle_time_utc": row.get("cycle_time_utc") or row.get("cycle_utc") or row.get("run_utc"),
        "forecast_valid_time_utc": row.get("forecast_valid_time_utc") or _record_forecast_valid_time(row),
        "valid_times_used_count": row.get("valid_times_used_count") or _record_valid_times_count(row),
        "fallback_from_model_key": row.get("fallback_from_model_key"),
        "uses_observation_data": bool(row.get("uses_observation_data") or (source.is_blend if source else False)),
        "uses_high_so_far": bool(row.get("uses_high_so_far") or source_kind == "fallback"),
        "is_blend": bool(row.get("is_blend") or (source.is_blend if source else False)),
        "is_ensemble": bool(row.get("is_ensemble") or (source.is_ensemble if source else False)),
        "is_direct_model": bool(row.get("is_direct_model") or (source.is_direct_model if source else False)),
        "is_station_guidance": bool(row.get("is_station_guidance") or (source.is_station_guidance if source else False)),
        "is_synthetic": bool(row.get("is_synthetic") or (source.is_synthetic if source else False)),
        "raw": row,
    }
    converted["estimate_source_detail"] = row.get("estimate_source_detail") or _record_source_detail(source, converted)
    if not _record_probability_is_displayable(converted):
        converted["top_probability"] = None
        converted["top_probability_bracket"] = None
    return converted


def _missing_model_row(model_key: str, *, reason: str = "not fetched by current wiring") -> dict[str, Any]:
    source = get_model_source(model_key)
    return {
        "model_key": model_key,
        "display_name": source.display_name,
        "provider": source.provider,
        "model_family": source.model_family,
        "independence_group": source.independence_group,
        "source_type": source.source_type,
        "fetch_status": "missing",
        "estimated_high_f": None,
        "estimated_bracket": None,
        "top_probability_bracket": None,
        "top_probability": None,
        "uncertainty_spread_f": None,
        "estimate_source_kind": "unavailable",
        "estimate_source_detail": reason,
        "endpoint_used": None,
        "raw_model_param_used": source.model_param_candidates[0] if source.model_param_candidates else None,
        "cycle_time_utc": None,
        "forecast_valid_time_utc": None,
        "valid_times_used_count": None,
        "fallback_from_model_key": None,
        "uses_observation_data": False,
        "uses_high_so_far": False,
        "is_blend": source.is_blend,
        "is_ensemble": source.is_ensemble,
        "is_direct_model": source.is_direct_model,
        "is_station_guidance": source.is_station_guidance,
        "is_synthetic": source.is_synthetic,
        "estimate_p10_high_f": None,
        "estimate_p25_high_f": None,
        "estimate_p50_high_f": None,
        "estimate_p75_high_f": None,
        "estimate_p90_high_f": None,
        "full_error_message": reason,
        "error_message": reason,
        "raw": source.to_dict(),
    }


def _add_registry_model_rows(payload: dict[str, Any], model_keys: list[str]) -> dict[str, Any]:
    rows = [_record_model_row_from_telemetry(row) for row in payload.get("models_by_estimated_high") or []]
    rows_by_key = {row["model_key"]: row for row in rows}
    for model_key in model_keys:
        if model_key not in rows_by_key:
            rows_by_key[model_key] = _missing_model_row(model_key)
    ordered = [rows_by_key[key] for key in model_keys if key in rows_by_key]
    payload["models"] = ordered
    payload["model_registry"] = registry_rows(model_keys)
    payload["model_count"] = len(ordered)
    payload["successful_model_count"] = sum(1 for row in ordered if row.get("fetch_status") == "ok")
    payload["missing_model_count"] = sum(1 for row in ordered if row.get("fetch_status") == "missing")
    payload["error_model_count"] = sum(1 for row in ordered if row.get("fetch_status") == "error")
    return payload


def _minimal_record_payload(
    *,
    experiment_id: str,
    series: str,
    station: str,
    target_date: date,
    timezone_name: str,
    model_keys: list[str],
    errors: list[dict[str, Any]],
    providers: str,
    models: str,
    residual_sigma: float | None,
) -> dict[str, Any]:
    generated_at = utc_now()
    payload = {
        "schema_version": "record_weather_market_v1",
        "generated_at_utc": generated_at,
        "bucket_start_utc": _bucket_start_utc(generated_at),
        "experiment_id": experiment_id,
        "series": series,
        "station": station,
        "target_date": target_date.isoformat(),
        "timezone": timezone_name,
        "record_only": True,
        "live_trading_enabled": False,
        "paper_trading": False,
        "llm_trader_used": False,
        "candidate_trade_board_created": False,
        "providers": providers,
        "models_option": models,
        "residual_sigma_f": residual_sigma,
        "errors": errors,
        "market": {"market_count": 0, "bracket_count": 0, "brackets": [], "status": "error"},
        "observation": {"status": "error", "observations": [], "high_so_far_f": None},
        "final_high": {"status": "pending", "official_high_f": None},
        "models": [_missing_model_row(key, reason="snapshot failed before model fetch") for key in model_keys],
        "model_registry": registry_rows(model_keys),
    }
    payload["model_count"] = len(payload["models"])
    payload["successful_model_count"] = 0
    payload["missing_model_count"] = len(payload["models"])
    payload["error_model_count"] = 0
    return payload


def _record_weather_market_payload(
    settings: Settings,
    *,
    series: str,
    station: str,
    target_date: date,
    timezone_name: str,
    experiment_id: str,
    model_set: str,
    models: str | None,
    skip_models: str | None,
    residual_sigma: float | None,
    include_raw: bool,
    refresh_recent_days: int,
    bucket_interval_seconds: int = 900,
) -> dict[str, Any]:
    model_keys = select_model_keys(model_set=model_set, models=models, skip_models=skip_models)
    providers_option, models_option = provider_model_options(model_keys)
    try:
        payload = _model_telemetry_payload(
            settings,
            series=series,
            station=station,
            target_date=target_date,
            providers=providers_option,
            models=models_option,
            residual_sigma=residual_sigma,
            store_results=False,
            include_raw=include_raw,
            finalize_recent_days=refresh_recent_days,
        )
    except Exception as exc:  # noqa: BLE001
        payload = _minimal_record_payload(
            experiment_id=experiment_id,
            series=series,
            station=station,
            target_date=target_date,
            timezone_name=timezone_name,
            model_keys=model_keys,
            errors=[{"stage": "snapshot", "message": str(exc)}],
            providers=providers_option,
            models=models_option,
            residual_sigma=residual_sigma,
        )
    payload["schema_version"] = "record_weather_market_v1"
    payload["experiment_id"] = experiment_id
    payload["target_date"] = target_date.isoformat()
    payload["timezone"] = timezone_name
    payload["bucket_start_utc"] = _bucket_start_utc(
        payload.get("generated_at_utc"),
        interval_seconds=bucket_interval_seconds,
    )
    payload["models_option"] = models_option
    payload["providers_option"] = providers_option
    payload["model_set"] = model_set
    payload["refresh_recent_days"] = refresh_recent_days
    payload["record_only"] = True
    payload["live_trading_enabled"] = False
    payload["paper_trading"] = False
    payload["llm_trader_used"] = False
    payload["candidate_trade_board_created"] = False
    payload["observation"] = payload.pop("observations", payload.get("observation", {}))
    _append_record_extra_model_rows(
        settings,
        payload=payload,
        station=station,
        target_date=target_date,
        model_keys=model_keys,
        include_raw=include_raw,
    )
    payload = _add_registry_model_rows(payload, model_keys)
    payload["warnings"] = _record_warnings(payload)
    payload["consensus"] = _record_consensus(payload)
    return payload


def _record_weather_market_text(payload: dict[str, Any]) -> str:
    market = payload.get("market") or {}
    observation = payload.get("observation") or {}
    final_high = payload.get("final_high") or {}
    market_top = _market_top_label(market.get("brackets") or [])
    return "\n".join(
        [
            "Kalshi Weather Record-Only Snapshot",
            "===================================",
            (
                f"Experiment: {payload.get('experiment_id')} | "
                f"Captured: {_fmt_utc_minute(payload.get('generated_at_utc'))}"
            ),
            f"Target: {payload.get('target_date')} {payload.get('station')} {payload.get('series')}",
            (
                f"Models: {payload.get('successful_model_count', 0)} ok, "
                f"{payload.get('missing_model_count', 0)} missing, {payload.get('error_model_count', 0)} error"
            ),
            (
                f"Observation: latest {_fmt_f_short(observation.get('latest_temp_f'))}, "
                f"high-so-far {_fmt_f_short(observation.get('high_so_far_f'))}"
            ),
            f"Final high: {_fmt_f_short(final_high.get('official_high_f'))} | Market top: {market_top or '--'}",
            f"Journal status: {payload.get('journal_status', '--')} | Snapshot id: {payload.get('snapshot_id', '--')}",
        ]
    )


def _market_top_label(brackets: list[dict[str, Any]]) -> str | None:
    top = _market_top_row(brackets)
    return str(top[0].get("bracket_label")) if top else None


def _market_yes_prices(row: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None, str]:
    bid = _float_or_none(row.get("yes_bid_cents"))
    ask = _float_or_none(row.get("yes_ask_cents"))
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2
        return bid, ask, mid, mid, "mid"
    if bid is not None:
        return bid, ask, bid, None, "best YES"
    if ask is not None:
        return bid, ask, ask, None, "best YES"
    return bid, ask, None, None, "--"


def _market_top_row(brackets: list[dict[str, Any]]) -> tuple[dict[str, Any], float, str] | None:
    best: tuple[dict[str, Any], float, str] | None = None
    for row in brackets:
        _bid, _ask, best_yes, _mid, method = _market_yes_prices(row)
        if best_yes is None:
            continue
        if best is None or best_yes > best[1]:
            best = (row, best_yes, method)
    return best


def _market_top_summary(brackets: list[dict[str, Any]]) -> str:
    top = _market_top_row(brackets)
    if top is None:
        return "Market top: --"
    row, price, method = top
    return f"Market top: {row.get('bracket_label') or '--'} @ {_fmt_cents(price)} {method}"


def _record_loop_line(payload: dict[str, Any]) -> str:
    observation = payload.get("observation") or {}
    market_top = _market_top_label((payload.get("market") or {}).get("brackets") or [])
    generated = payload.get("generated_at_utc")
    try:
        stamp = datetime.fromisoformat(str(generated).replace("Z", "+00:00")).astimezone(
            ZoneInfo(payload.get("timezone") or LAX_TIMEZONE)
        ).strftime("%Y-%m-%d %H:%M PT")
    except Exception:  # noqa: BLE001
        stamp = str(generated)
    return (
        f"{stamp} | target {payload.get('target_date')} | "
        f"models {payload.get('successful_model_count', 0)} ok/{payload.get('missing_model_count', 0)} miss | "
        f"obs high {_fmt_f_short(observation.get('high_so_far_f'))} | "
        f"market {market_top or '--'} | {payload.get('journal_status', 'recorded')}"
    )


def _record_success_model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in payload.get("models") or []
        if row.get("fetch_status") == "ok"
        and row.get("estimated_high_f") is not None
        and row.get("estimate_source_kind") != "ensemble_spread"
    ]


def _record_warnings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _record_success_model_rows(payload)
    observation = payload.get("observation") or {}
    high_so_far = _record_number(observation.get("high_so_far_f"))
    warnings_out: list[dict[str, Any]] = []
    by_estimate: dict[float, list[str]] = defaultdict(list)
    for row in rows:
        estimate = _record_number(row.get("estimated_high_f"))
        if estimate is None:
            continue
        by_estimate[round(estimate, 1)].append(str(row.get("model_key")))
    duplicate_estimate, duplicate_models = None, []
    for estimate, model_keys in by_estimate.items():
        if len(model_keys) >= 5 and len(model_keys) > len(duplicate_models):
            duplicate_estimate = estimate
            duplicate_models = model_keys
    if duplicate_estimate is not None:
        warnings_out.append(
            {
                "code": "many_identical_model_estimates",
                "message": f"Many model estimates are identical: {len(duplicate_models)} models report {duplicate_estimate:.1f} F.",
                "model_keys": duplicate_models,
            }
        )
    if high_so_far is not None:
        matching_high = [
            str(row.get("model_key"))
            for row in rows
            if _record_number(row.get("estimated_high_f")) is not None
            and round(float(row.get("estimated_high_f")), 1) == round(high_so_far, 1)
        ]
        if len(matching_high) >= 5:
            warnings_out.append(
                {
                    "code": "many_estimates_match_high_so_far",
                    "message": (
                        f"{len(matching_high)} model estimates equal the current KLAX high-so-far of "
                        f"{high_so_far:.1f} F. Verify these are true forecasts and not fallback/observation leakage."
                    ),
                    "model_keys": matching_high,
                }
            )
        suspicious = []
        for row in rows:
            estimate = _record_number(row.get("estimated_high_f"))
            if estimate is None or round(estimate, 1) != round(high_so_far, 1):
                continue
            if row.get("endpoint_used") or row.get("cycle_time_utc") or row.get("valid_times_used_count"):
                continue
            if row.get("provider") in {"open_meteo", "noaa_herbie"} or row.get("is_direct_model"):
                suspicious.append(str(row.get("model_key")))
        if suspicious:
            warnings_out.append(
                {
                    "code": "possible_fallback_rows",
                    "message": "Possible fallback rows have no endpoint/cycle/valid-time metadata and match high-so-far.",
                    "model_keys": suspicious,
                }
            )
    fallback_ok = [
        str(row.get("model_key"))
        for row in rows
        if row.get("uses_high_so_far") and row.get("fetch_status") == "ok"
    ]
    if fallback_ok:
        warnings_out.append(
            {
                "code": "fallback_displayed_as_ok",
                "message": "Rows using KLAX high-so-far are marked ok; review fallback provenance.",
                "model_keys": fallback_ok,
            }
        )
    return warnings_out


def _record_consensus(payload: dict[str, Any]) -> dict[str, Any]:
    rows = _record_success_model_rows(payload)
    estimates = [float(row["estimated_high_f"]) for row in rows if _record_number(row.get("estimated_high_f")) is not None]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("independence_group") or row.get("model_key"))].append(row)
    group_brackets = []
    for group_rows in groups.values():
        first = next((row for row in group_rows if row.get("estimated_bracket")), None)
        if first:
            group_brackets.append(str(first["estimated_bracket"]))
    common_bracket = None
    if group_brackets:
        common_bracket = max(sorted(set(group_brackets)), key=group_brackets.count)
    median_estimate = statistics.median(estimates) if estimates else None
    outliers = []
    if median_estimate is not None:
        for row in rows:
            estimate = _record_number(row.get("estimated_high_f"))
            if estimate is not None and abs(estimate - median_estimate) >= 3.0:
                outliers.append(f"{row.get('model_key')} {_fmt_f_short(estimate)}")
    return {
        "successful_estimates": len(rows),
        "independent_groups": len(groups),
        "median_estimate_f": median_estimate,
        "mean_estimate_f": statistics.mean(estimates) if estimates else None,
        "min_estimate_f": min(estimates) if estimates else None,
        "max_estimate_f": max(estimates) if estimates else None,
        "most_common_bracket_by_group": common_bracket,
        "market_top": _market_top_label((payload.get("market") or {}).get("brackets") or []),
        "observation_high_so_far_f": (payload.get("observation") or {}).get("high_so_far_f"),
        "outliers": outliers,
    }


def _record_local_minute(payload: dict[str, Any]) -> str:
    generated = payload.get("generated_at_utc")
    try:
        stamp = datetime.fromisoformat(str(generated).replace("Z", "+00:00")).astimezone(
            ZoneInfo(payload.get("timezone") or LAX_TIMEZONE)
        )
        return stamp.strftime("%Y-%m-%d %H:%M PT")
    except Exception:  # noqa: BLE001
        return str(generated)


def _record_market_table(payload: dict[str, Any]) -> list[str]:
    rows = (payload.get("market") or {}).get("brackets") or []
    market_top = _market_top_label(rows)
    lines = [
        "Market",
        "------",
        "Bracket  YES Bid  YES Ask  Best YES  Mid    Status  Ticker",
        "-------  -------  -------  --------  -----  ------  ------------------------------",
    ]
    if not rows:
        lines.append("none")
        return lines
    for row in rows:
        label = str(row.get("bracket_label") or "--")
        marker = "*" if label == market_top else " "
        bid, ask, best_yes, mid, _method = _market_yes_prices(row)
        lines.append(
            f"{marker}{_fit_cell(label, 6)}  "
            f"{_fit_cell(_fmt_cents(bid), 7)}  "
            f"{_fit_cell(_fmt_cents(ask), 7)}  "
            f"{_fit_cell(_fmt_cents(best_yes), 8)}  "
            f"{_fit_cell(_fmt_cents(mid), 5)}  "
            f"{_fit_cell(row.get('status') or row.get('market_status') or 'open', 6)}  "
            f"{_short_label(row.get('market_ticker') or row.get('ticker') or '--', max_len=30)}"
        )
    return lines


def _record_ensemble_base_key(model_key: str) -> str | None:
    for suffix in ("_mean", "_spread", "_p50"):
        if model_key.endswith(suffix):
            base = model_key[: -len(suffix)]
            return "gefs" if base == "gefs" else base
    if model_key in {"href_mean", "href_p50"}:
        return "href"
    return None


def _record_display_model_rows(payload: dict[str, Any], *, include_spread_rows: bool = False) -> list[dict[str, Any]]:
    rows = [dict(row) for row in payload.get("models") or []]
    if include_spread_rows:
        return rows
    merged: dict[str, dict[str, Any]] = {}
    output: list[dict[str, Any]] = []
    for row in rows:
        model_key = str(row.get("model_key") or "")
        base_key = _record_ensemble_base_key(model_key)
        if base_key is None or not row.get("is_ensemble"):
            output.append(row)
            continue
        existing = merged.get(base_key)
        source_type = str(row.get("source_type") or "")
        if existing is None:
            existing = dict(row)
            existing["model_key"] = base_key
            existing["display_name"] = base_key
            existing["fetch_status"] = "partial" if source_type in {"ensemble_spread", "ensemble_percentile"} else row.get("fetch_status")
            if source_type == "ensemble_spread":
                existing["estimated_high_f"] = None
                existing["estimated_bracket"] = None
            merged[base_key] = existing
            output.append(existing)
        if source_type == "ensemble_spread":
            existing["uncertainty_spread_f"] = row.get("uncertainty_spread_f") or existing.get("uncertainty_spread_f")
        elif source_type == "ensemble_percentile":
            existing["estimate_p50_high_f"] = row.get("estimated_high_f") or row.get("estimate_p50_high_f")
        else:
            for key, value in row.items():
                if value is not None:
                    existing[key] = value
            existing["model_key"] = base_key
            existing["display_name"] = base_key
    return output


def _record_model_group(row: dict[str, Any]) -> str:
    model_key = str(row.get("model_key") or "")
    provider = str(row.get("provider") or "")
    if model_key in {"current_weighted_blend", "best_match"}:
        return "Synthetic / Blend"
    if row.get("is_ensemble"):
        return "Ensemble / probabilistic"
    if provider == "open_meteo":
        return "Open-Meteo deterministic"
    if provider == "noaa_herbie":
        return "Direct NOAA / Herbie"
    if provider in {"nws", "noaa_mdl"} or row.get("is_station_guidance"):
        return "Official / station guidance"
    return "Errors / unavailable"


def _record_model_status(row: dict[str, Any]) -> str:
    status = str(row.get("fetch_status") or "--")
    if status in {"ok", "partial"}:
        return status
    if row.get("error_message"):
        return f"{status}: {_short_label(row.get('error_message'), max_len=22)}"
    return status


def _record_prob_display(row: dict[str, Any]) -> str:
    return _fmt_percent(row.get("top_probability")) if _record_probability_is_displayable(row) else "--"


def _record_models_table(payload: dict[str, Any], *, snapshot_style: str = "table") -> list[str]:
    rows = _record_display_model_rows(payload, include_spread_rows=snapshot_style == "full")
    market_top = _market_top_label((payload.get("market") or {}).get("brackets") or [])
    lines = ["Models", "------"]
    if not rows:
        lines.append("none")
        return lines
    group_order = [
        "Synthetic / Blend",
        "Open-Meteo deterministic",
        "Direct NOAA / Herbie",
        "Ensemble / probabilistic",
        "Official / station guidance",
        "Errors / unavailable",
    ]
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[_record_model_group(row)].append(row)
    for group in group_order:
        group_rows = by_group.get(group) or []
        if not group_rows:
            continue
        lines.extend(
            [
                group,
                "Model                   Est       Brkt   MktTop  Prob    Spread  Source   Status",
                "----------------------  --------  -----  ------  ------  ------  -------  ------------------------------",
            ]
        )
        for row in group_rows:
            lines.append(
                f"{_fit_cell(row.get('model_key'), 22)}  "
                f"{_fit_cell(_fmt_f_short(row.get('estimated_high_f')), 8)}  "
                f"{_fit_cell(row.get('estimated_bracket') or '--', 5)}  "
                f"{_fit_cell(market_top or '--', 6)}  "
                f"{_fit_cell(_record_prob_display(row), 6, align='right')}  "
                f"{_fit_cell(_fmt_f_short(row.get('uncertainty_spread_f')).replace(' F', 'F'), 6)}  "
                f"{_fit_cell(_record_source_label(row), 7)}  "
                f"{_record_model_status(row)}"
            )
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _record_warnings_lines(payload: dict[str, Any]) -> list[str]:
    warnings_rows = payload.get("warnings") or []
    if not warnings_rows:
        return []
    lines = ["Warnings", "--------"]
    for warning in warnings_rows:
        if isinstance(warning, dict):
            lines.append(str(warning.get("message") or warning))
            model_keys = warning.get("model_keys") or []
            if model_keys:
                lines.append(f"Models: {_short_label(', '.join(str(key) for key in model_keys), max_len=110)}")
        else:
            lines.append(str(warning))
    return lines


def _record_consensus_lines(payload: dict[str, Any]) -> list[str]:
    consensus = payload.get("consensus") or _record_consensus(payload)
    lines = [
        "Model Consensus",
        "---------------",
        f"Successful estimates: {consensus.get('successful_estimates', 0)}",
        f"Independent groups: {consensus.get('independent_groups', 0)}",
        f"Median estimate: {_fmt_f_short(consensus.get('median_estimate_f'))}",
        f"Mean estimate: {_fmt_f_short(consensus.get('mean_estimate_f'))}",
        (
            "Range: "
            f"{_fmt_f_short(consensus.get('min_estimate_f'))} to "
            f"{_fmt_f_short(consensus.get('max_estimate_f'))}"
        ),
        f"Most common bracket: {consensus.get('most_common_bracket_by_group') or '--'}",
        f"Market top: {consensus.get('market_top') or '--'}",
        f"Observation high-so-far: {_fmt_f_short(consensus.get('observation_high_so_far_f'))}",
    ]
    outliers = consensus.get("outliers") or []
    lines.append(f"Outliers: {', '.join(outliers) if outliers else 'none'}")
    return lines


def _record_error_summary(value: Any) -> str:
    text = str(value or "--")
    lower = text.lower()
    if "certificate verify failed" in lower or "certificate has expired" in lower:
        return "data source SSL certificate failed"
    if "failed to resolve" in lower or "nameresolutionerror" in lower or "getaddrinfo failed" in lower:
        return "network DNS lookup failed"
    if "read timed out" in lower or "timeout" in lower:
        return "data source timed out"
    if "no usable 2-meter temperature" in lower:
        return "no usable 2-meter temperature field"
    if "no index file was found" in lower or "download the full file first" in lower:
        return "Herbie index/subset unavailable"
    if "no hourly temperatures" in lower:
        return "no hourly temperatures for target date"
    if "data corrupted" in lower or "invalid string value" in lower:
        return "provider rejected model parameter"
    if "parser is not configured" in lower:
        return "parser not configured"
    if "not fetched by current wiring" in lower:
        return "not fetched by current wiring"
    return _short_label(text, max_len=70)


def _record_error_lines(payload: dict[str, Any], *, snapshot_style: str = "table") -> list[str]:
    rows = []
    for row in payload.get("models") or []:
        if row.get("fetch_status") in {"ok", None}:
            continue
        rows.append(row)
    if not rows and not payload.get("errors"):
        return []
    show_full = snapshot_style == "full"
    skipped = [
        row
        for row in rows
        if row.get("fetch_status") == "missing"
        and str(row.get("error_message") or "").lower() == "not fetched by current wiring"
    ]
    display_rows = rows if show_full else [row for row in rows if row not in skipped]
    lines = [
        "Errors",
        "------",
        (
            "Model                   Status   Full reason"
            if show_full
            else "Model                   Status   Issue"
        ),
        (
            "----------------------  -------  ------------------------------------------------------------"
            if show_full
            else "----------------------  -------  ----------------------------------------"
        ),
    ]
    if skipped and not show_full:
        skipped_names = ", ".join(str(row.get("model_key")) for row in skipped[:8])
        more = f", +{len(skipped) - 8} more" if len(skipped) > 8 else ""
        lines.append(
            f"{_fit_cell('optional models', 22)}  "
            f"{_fit_cell('skipped', 7)}  "
            f"{len(skipped)} not wired yet ({skipped_names}{more})"
        )
    for row in display_rows:
        reason = row.get("full_error_message") or row.get("error_message") or "--"
        lines.append(
            f"{_fit_cell(row.get('model_key'), 22)}  "
            f"{_fit_cell(row.get('fetch_status') or '--', 7)}  "
            f"{reason if show_full else _record_error_summary(reason)}"
        )
    for error in payload.get("errors") or []:
        lines.append(
            f"{_fit_cell('snapshot', 22)}  error    "
            f"{error if show_full else _record_error_summary(error)}"
        )
    if not show_full:
        lines.append("Full error details are saved in the journal; use --snapshot-style full to print them.")
    return lines


def _record_full_provenance_lines(payload: dict[str, Any]) -> list[str]:
    lines = [
        "Full Model Provenance",
        "---------------------",
        "Model                   Kind                 Param        Cycle/Run UTC              Valid  Endpoint",
        "----------------------  -------------------  -----------  -------------------------  -----  ------------------------------",
    ]
    for row in payload.get("models") or []:
        cycle = row.get("cycle_time_utc") or "--"
        lines.append(
            f"{_fit_cell(row.get('model_key'), 22)}  "
            f"{_fit_cell(row.get('estimate_source_kind'), 19)}  "
            f"{_fit_cell(row.get('raw_model_param_used') or '--', 11)}  "
            f"{_fit_cell(cycle, 25)}  "
            f"{_fit_cell(row.get('valid_times_used_count') or '--', 5, align='right')}  "
            f"{_short_label(row.get('endpoint_used') or '--', max_len=30)}"
        )
    return lines


def _record_snapshot_text(iteration: int, payload: dict[str, Any], *, snapshot_style: str = "table") -> str:
    observation = payload.get("observation") or {}
    final_high = payload.get("final_high") or {}
    status = payload.get("journal_status", "recorded")
    snapshot_id = payload.get("snapshot_id", "--")
    market_rows = (payload.get("market") or {}).get("brackets") or []
    lines = [
        (
            f"Snapshot {iteration:04d} | {_record_local_minute(payload)} | "
            f"target {payload.get('target_date')} | {status} id={snapshot_id}"
        ),
        "-" * 96,
        (
            "Observation: "
            f"source {observation.get('source') or '--'} | "
            f"latest {_fmt_f_short(observation.get('latest_temp_f'))} "
            f"at {observation.get('latest_observation_utc') or '--'} | "
            f"high-so-far {_fmt_f_short(observation.get('high_so_far_f'))} | "
            f"final {_fmt_f_short(final_high.get('official_high_f'))}"
        ),
        (
            "Counts: "
            f"{payload.get('successful_model_count', 0)} ok / "
            f"{payload.get('missing_model_count', 0)} missing / "
            f"{payload.get('error_model_count', 0)} error | "
            f"market brackets {(payload.get('market') or {}).get('bracket_count', 0)}"
        ),
        _market_top_summary(market_rows),
    ]
    blocks: list[list[str]] = []
    warning_lines = _record_warnings_lines(payload)
    if warning_lines:
        blocks.append(warning_lines)
    blocks.append(_record_consensus_lines(payload))
    if snapshot_style != "compact":
        blocks.append(_record_models_table(payload, snapshot_style=snapshot_style))
        blocks.append(_record_market_table(payload))
    error_lines = _record_error_lines(payload, snapshot_style=snapshot_style)
    if error_lines:
        blocks.append(error_lines)
    if snapshot_style == "full":
        blocks.append(_record_full_provenance_lines(payload))
    for block in blocks:
        if block:
            lines.append("")
            lines.extend(block)
    return "\n".join(lines)


def _write_record_payload(
    payload: dict[str, Any],
    *,
    journal_path: str,
    jsonl_path: str | None,
    replace_existing_bucket: bool,
) -> dict[str, Any]:
    journal = ValidationJournal(journal_path)
    result = journal.insert_snapshot(payload, replace_existing_bucket=replace_existing_bucket)
    payload["journal_status"] = result["status"]
    payload["snapshot_id"] = result["snapshot_id"]
    if jsonl_path and result["status"] != "skipped_duplicate":
        append_jsonl(jsonl_path, safe_console_payload(payload))
    return payload


def _provider_probe_payload(
    settings: Settings,
    station: str,
    providers_raw: str | None,
    models_raw: str | None,
) -> dict[str, Any]:
    series = settings.default_series
    payload = _model_estimates_payload(
        settings,
        series,
        station,
        providers_raw=providers_raw,
        models_raw=models_raw,
        include_probabilities=False,
        only_successful=False,
        show_failures=True,
        store_results=False,
    )
    rows = []
    for estimate in payload["estimates"]:
        dependency_available = True
        if estimate["provider"] == "noaa_herbie":
            dependency_available = bool(estimate.get("details_json", {}).get("dependency_available", estimate["successful"]))
        rows.append(
            {
                "provider": estimate["provider"],
                "model_id": estimate["model_id"],
                "available": bool(estimate["successful"]),
                "dependency_available": dependency_available,
                "live_fetch_success": bool(estimate["successful"]),
                "future_high_f": estimate["future_high_f"],
                "observed_high_so_far_f": estimate["observed_high_so_far_f"],
                "error_message": estimate["error_message"],
                "source": estimate["source"],
                "next_action": _provider_next_action(estimate),
            }
        )
    payload["provider_probe"] = rows
    return payload


def _provider_probe_text(payload: dict[str, Any]) -> str:
    lines = [f"MODEL PROVIDER PROBE - {payload['station']}", ""]
    lines.append("Provider        Model                 Available   Dependency   Future high   Error / next action")
    for row in payload.get("provider_probe", []):
        message = row.get("error_message") or row.get("next_action") or ""
        lines.append(
            f"{row['provider']:<15} {row['model_id']:<21} {str(row['available']):<11} "
            f"{str(row['dependency_available']):<12} {_fmt_f(row.get('future_high_f')):<13} {message}"
        )
    return "\n".join(lines)


def _direct_noaa_check_payload(
    settings: Settings,
    station: str,
    *,
    show_attempts: bool = False,
    max_cycles: int = 6,
) -> dict[str, Any]:
    market_date = current_lax_market_date()
    window_start_utc = utc_now()
    _day_start, window_end_utc = lax_climate_day_utc(market_date)
    dep_status = dependency_status()
    targets = settings.direct_noaa_models.get("models") or NOAA_HERBIE_MODELS
    try:
        weather, _forecast = _weather_context(settings, station)
        observed_high = weather.observed_high_so_far_f
        current_estimate = weather.model_future_high_f
        weather_error = None
    except Exception as exc:  # noqa: BLE001
        observed_high = None
        current_estimate = None
        weather_error = str(exc)
    client = HerbieModelClient(
        cache_dir=settings.herbie_cache_dir,
        max_forecast_hours=settings.max_forecast_hours,
        model_configs=targets,
        max_cycles=max_cycles,
    )
    models = list(settings.model_estimate_default_models.get("noaa_herbie", ["hrrr", "nbm", "gfs", "rap"]))
    if settings.enable_direct_noaa_models:
        results = client.fetch_estimate_results(
            station=station,
            market_date=market_date.isoformat(),
            observed_high_so_far_f=observed_high,
            forecast_window_start_utc=window_start_utc,
            forecast_window_end_utc=window_end_utc,
            latitude=float(settings.direct_noaa_models.get("station_lat", LAX_LATITUDE)),
            longitude=float(settings.direct_noaa_models.get("station_lon", LAX_LONGITUDE)),
            models=models,
            max_cycles=max_cycles,
        )
    else:
        results = [
            HerbieFetchResult(estimate=estimate)
            for estimate in client.unavailable_estimates(
                station=station,
                market_date=market_date.isoformat(),
                observed_high_so_far_f=observed_high,
                forecast_window_start_utc=window_start_utc,
                forecast_window_end_utc=window_end_utc,
                latitude=float(settings.direct_noaa_models.get("station_lat", LAX_LATITUDE)),
                longitude=float(settings.direct_noaa_models.get("station_lon", LAX_LONGITUDE)),
                models=models,
                error_message="Direct NOAA/Herbie models are disabled by configuration.",
            )
        ]
    live_rows: list[dict[str, Any]] = []
    for result in results:
        estimate = result.estimate.to_record()
        estimate["successful"] = result.estimate.successful
        estimate["attempt_count"] = len(result.attempts)
        estimate["extraction_count"] = len(result.extractions)
        if show_attempts:
            estimate["attempts"] = [attempt.to_record() for attempt in result.attempts]
        live_rows.append(estimate)
    return {
        "generated_at_utc": utc_now(),
        "station": station,
        "market_date": market_date,
        "forecast_window_start_utc": window_start_utc,
        "forecast_window_end_utc": window_end_utc,
        "dependencies": dep_status,
        "model_targets": {
            model_id: {
                "model": target.get("model", model_id),
                "product": target.get("product"),
                "forecast_hours": target.get("forecast_hours"),
                "cycle_hours": target.get("cycle_hours"),
                "search_strings": target.get("search_strings"),
            }
            for model_id, target in targets.items()
            if model_id in models
        },
        "observed_high_so_far_f": observed_high,
        "current_open_meteo_estimate_f": current_estimate,
        "weather_error": weather_error,
        "live_check": live_rows,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _direct_noaa_check_text(payload: dict[str, Any], show_attempts: bool = False) -> str:
    lines = [
        f"DIRECT NOAA / HERBIE CHECK - {payload['station']}",
        "",
        "Dependencies:",
    ]
    for name, ok in payload.get("dependencies", {}).items():
        lines.append(f"- {name}: {'installed' if ok else 'missing'}")
    lines.extend(["", "Model targets:"])
    for model_id, target in payload.get("model_targets", {}).items():
        lines.append(f"- {model_id}: model={target.get('model')}, product={target.get('product')}")
    if payload.get("weather_error"):
        lines.extend(["", f"Weather context warning: {payload['weather_error']}"])
    lines.extend(
        [
            "",
            "Live check:",
            "Provider      Model   Future high   Settlement estimate   Status",
        ]
    )
    for row in payload.get("live_check", []):
        status = "ok" if row.get("successful") else f"unavailable: {row.get('error_message')}"
        lines.append(
            f"{str(row.get('provider')):<13} {str(row.get('model_id')):<7} "
            f"{_fmt_f(row.get('future_high_f')):<13} {_fmt_f(row.get('settlement_high_estimate_f')):<21} {status}"
        )
        if show_attempts and row.get("attempts"):
            for attempt in row["attempts"][-10:]:
                lines.append(
                    f"  attempt f{attempt.get('forecast_hour')} {attempt.get('search_string')}: "
                    f"{attempt.get('status')} {attempt.get('error') or ''}"
                )
    return "\n".join(lines)


def _model_estimate_score_payload(
    store_obj: SQLiteStore,
    station: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    estimates = store_obj.load_model_estimates(
        station=station,
        start_date=start_date,
        end_date=end_date,
        only_successful=True,
    )
    outcomes = {
        (row["station"], row["market_date"]): row
        for row in store_obj.load_official_outcomes(
            station=station,
            start_date=start_date,
            end_date=end_date,
        )
    }
    scored = []
    for estimate in estimates:
        outcome = outcomes.get((estimate["station"], estimate["market_date"]))
        if outcome is None or estimate.get("settlement_high_estimate_f") is None:
            continue
        error = float(outcome["official_high_f"]) - float(estimate["settlement_high_estimate_f"])
        scored.append({**estimate, "official_high_f": outcome["official_high_f"], "error_f": error})
    by_model = _estimate_score_groups(scored, ["provider", "model_id"])
    by_provider = _estimate_score_groups(scored, ["provider"])
    by_asof_hour = _estimate_score_groups(scored, ["provider", "model_id", "asof_hour_utc"])
    return {
        "station": station,
        "start_date": start_date,
        "end_date": end_date,
        "estimate_count": len(estimates),
        "official_outcome_count": len(outcomes),
        "scored_count": len(scored),
        "unique_market_dates": len({row["market_date"] for row in scored}),
        "status": "ok" if scored else "no_scored_model_estimates",
        "message": None
        if scored
        else "No scored model estimates yet. Run fetch-missing-outcomes and join-outcomes after settlement.",
        "by_model": by_model,
        "by_provider": by_provider,
        "by_asof_hour": by_asof_hour,
    }


def _model_estimate_score_text(payload: dict[str, Any]) -> str:
    lines = [
        f"MODEL ESTIMATE SCORE - {payload['station']}",
        "",
        f"Scored estimates: {payload['scored_count']}",
        f"Unique market dates: {payload['unique_market_dates']}",
    ]
    if payload["scored_count"] == 0:
        lines.append(payload["message"])
        return "\n".join(lines)
    lines.extend(["", "Model                         Count  MAE    RMSE   Mean error  Interpretation"])
    for key, row in payload["by_model"].items():
        lines.append(
            f"{key:<29} {row['count']:<6} {row['mae']:<6.2f} {row['rmse']:<6.2f} "
            f"{row['mean_error']:<11.2f} {row['interpretation']}"
        )
    return "\n".join(lines)


def _estimate_score_groups(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    market_dates: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        enriched = dict(row)
        asof = str(row.get("asof_utc") or "")
        enriched["asof_hour_utc"] = asof[11:13] if len(asof) >= 13 else "unknown"
        key = "|".join(str(enriched.get(part)) for part in keys)
        groups[key].append(float(row["error_f"]))
        market_dates[key].add(str(row.get("market_date")))
    return {
        key: {
            "count": len(values),
            "unique_market_dates": len(market_dates[key]),
            "mean_error": statistics.mean(values),
            "mae": statistics.mean(abs(value) for value in values),
            "rmse": math.sqrt(statistics.mean(value**2 for value in values)),
            "interpretation": _bias_interpretation(values),
        }
        for key, values in groups.items()
    }


def _bias_interpretation(errors: list[float]) -> str:
    if len(errors) < 5:
        return "sample too small"
    mean_error = statistics.mean(errors)
    if mean_error > 0.5:
        return "model too cold"
    if mean_error < -0.5:
        return "model too warm"
    return "bias near zero"


def _best_edge(row: dict[str, Any]) -> tuple[str | None, Decimal | None]:
    yes = Decimal(str(row["yes_edge"])) if row.get("yes_edge") is not None else None
    no = Decimal(str(row["no_edge"])) if row.get("no_edge") is not None else None
    if yes is None and no is None:
        return None, None
    if no is None or (yes is not None and yes >= no):
        return "yes", yes
    return "no", no


def _provider_next_action(estimate: dict[str, Any]) -> str:
    if estimate.get("successful"):
        return "available"
    if estimate.get("provider") == "noaa_herbie":
        return "Install optional Herbie/cfgrib/xarray dependencies or treat direct NOAA as unavailable."
    return "Check provider settings and network response."


def _llm_advisor_smoke_text(payload: dict[str, Any]) -> str:
    decision = payload.get("llm_decision") or {}
    validator = payload.get("hard_validator_result") or {}
    raw = payload.get("llm_raw_response") or {}
    status = "PASS" if payload.get("passed") else "FAIL"
    lines = [
        "KALSHI WEATHER LLM ADVISOR SMOKE TEST - FAKE MONEY ONLY",
        f"Status: {status}",
        f"Provider: {payload.get('provider')} | Model: {payload.get('model')}",
        f"Rule-only: {payload.get('rule_only')}",
        f"LLM decision: {decision.get('decision')} | score {decision.get('trade_quality_score')}",
        (
            "Hard validator: "
            f"{validator.get('final_action')} | approved={validator.get('approved')} | "
            f"{validator.get('explanation')}"
        ),
        f"Decision log: {payload.get('decision_log_path')}",
        "Live trading: DISABLED | Mode: fake money only",
    ]
    if raw.get("error"):
        lines.append(f"LLM error: {raw.get('error')}")
    return "\n".join(lines)


def _model_race_config(
    *,
    race_id: str,
    race_mode: str = "independent",
    starting_cash_per_model: float,
    base_hurdle: float,
    max_risk_per_trade: float,
    max_exposure_per_model: float,
    max_daily_fake_loss_per_model: float | None,
    profit_target_cents: int,
    stop_loss_cents: int,
    max_hold_minutes: int,
    force_flat_time_local: str,
    force_flat_at_end: bool = False,
    max_exposure_per_bracket: float = 10.0,
    max_open_positions_per_model: int = 1,
    stale_model_minutes: int = 45,
    require_exit_bid: bool = True,
    max_spread_cents: float = 15.0,
    minimum_top_book_size: float = 1.0,
    allow_penny_contracts: bool = False,
    missing_bid_mark_mode: str = "na",
    block_new_entries_if_model_spread_gt_f: float = 4.0,
    outlier_threshold_f: float = 3.0,
    reduce_size_if_spread_gt_f: float = 2.0,
    reduced_size_multiplier: float = 0.5,
    synthetic_zero_exit_on_force_flat: bool = False,
    cooldown_after_stop_minutes: int = 30,
    max_entry_price_cents: float = 80.0,
    allow_high_price_entries: bool = False,
    high_price_override_edge: float = 0.25,
    block_outlier_models: bool = False,
    advisor_mode: str = "off",
    advisor_required: bool = True,
    advisor_log_dir: str = "reports/llm_trade_advisor",
    advisor_min_score: int = 75,
    advisor_provider_config: str | None = None,
    advisor_output_json: bool = False,
    use_llm_advisor: bool = False,
    llm_provider: str = "ollama",
    llm_model: str = DEFAULT_LLM_MODEL,
    llm_host: str | None = None,
    llm_timeout_seconds: int = 60,
    llm_max_retries: int = 2,
    llm_temperature: float = 0.0,
    llm_decision_log: str = "reports/llm_advisor_decisions",
    llm_rule_only: bool = False,
    llm_dry_run: bool = False,
    llm_show_prompt: bool = False,
    llm_show_raw_response: bool = False,
    llm_fallback_action: str = "WAIT",
    llm_first: bool = False,
) -> ModelRaceConfig:
    normalized_race_mode = race_mode.strip().lower()
    if normalized_race_mode not in {"independent", "consensus_guarded"}:
        raise typer.BadParameter("race-mode must be independent or consensus_guarded.")
    normalized_advisor_mode = advisor_mode.strip().lower()
    if normalized_advisor_mode not in ADVISOR_MODES:
        raise typer.BadParameter("advisor-mode must be off, rule_based, prompt_only, or llm_json.")
    resolved_llm_provider = _resolve_llm_provider(llm_provider)
    resolved_llm_model = _resolve_llm_model(llm_model)
    resolved_fallback_action = llm_fallback_action.strip().upper()
    if resolved_fallback_action not in {"WAIT", "BLOCK"}:
        raise typer.BadParameter("llm-fallback-action must be wait or block.")
    outlier_block = block_outlier_models or normalized_race_mode == "consensus_guarded"
    return ModelRaceConfig(
        race_id=race_id,
        race_mode=normalized_race_mode,
        starting_cash_per_model=Decimal(str(starting_cash_per_model)),
        base_hurdle=Decimal(str(base_hurdle)),
        max_risk_per_trade=Decimal(str(max_risk_per_trade)),
        max_exposure_per_model=Decimal(str(max_exposure_per_model)),
        max_daily_fake_loss_per_model=(
            None
            if max_daily_fake_loss_per_model is None
            else Decimal(str(max_daily_fake_loss_per_model))
        ),
        max_exposure_per_bracket=Decimal(str(max_exposure_per_bracket)),
        profit_target_price_delta=Decimal(profit_target_cents) / Decimal("100"),
        stop_loss_price_delta=Decimal(stop_loss_cents) / Decimal("100"),
        max_hold_minutes=max_hold_minutes,
        max_open_positions_per_model=max_open_positions_per_model,
        force_flat_time_local=force_flat_time_local,
        force_flat_at_end=force_flat_at_end,
        stale_model_minutes=stale_model_minutes,
        require_exit_bid_for_entry=require_exit_bid,
        max_spread_cents=Decimal(str(max_spread_cents)),
        minimum_top_book_size=Decimal(str(minimum_top_book_size)),
        allow_penny_contract_entries=allow_penny_contracts,
        missing_bid_mark_mode=missing_bid_mark_mode,
        block_new_entries_if_model_spread_gt_f=block_new_entries_if_model_spread_gt_f,
        outlier_threshold_f=outlier_threshold_f,
        reduce_size_if_spread_gt_f=reduce_size_if_spread_gt_f,
        reduced_size_multiplier=Decimal(str(reduced_size_multiplier)),
        synthetic_zero_exit_on_force_flat=synthetic_zero_exit_on_force_flat,
        cooldown_after_stop_minutes=cooldown_after_stop_minutes,
        max_entry_price_cents=Decimal(str(max_entry_price_cents)),
        allow_high_price_entries=allow_high_price_entries,
        high_price_override_edge=Decimal(str(high_price_override_edge)),
        block_outlier_models=outlier_block,
        block_outlier_model_entries=outlier_block,
        advisor_mode=normalized_advisor_mode,
        advisor_required=advisor_required,
        advisor_log_dir=advisor_log_dir,
        advisor_min_score=advisor_min_score,
        advisor_provider_config=advisor_provider_config,
        advisor_output_json=advisor_output_json,
        use_llm_advisor=use_llm_advisor,
        llm_provider=resolved_llm_provider,
        llm_model=resolved_llm_model,
        llm_host=llm_host or os.getenv("OLLAMA_HOST"),
        llm_timeout_seconds=llm_timeout_seconds,
        llm_max_retries=llm_max_retries,
        llm_temperature=llm_temperature,
        llm_decision_log=llm_decision_log,
        llm_rule_only=llm_rule_only,
        llm_dry_run=llm_dry_run,
        llm_show_prompt=llm_show_prompt,
        llm_show_raw_response=llm_show_raw_response,
        llm_fallback_action=resolved_fallback_action,
        llm_first=llm_first,
    )


def _resolve_llm_provider(cli_value: str | None) -> str:
    env_value = os.getenv("KALSHI_LLM_PROVIDER")
    if env_value and (cli_value is None or cli_value == "ollama"):
        return env_value.strip().lower()
    return (cli_value or "ollama").strip().lower()


def _resolve_llm_model(cli_value: str | None) -> str:
    env_value = os.getenv("KALSHI_LLM_MODEL") or os.getenv("OLLAMA_MODEL")
    if env_value and (cli_value is None or cli_value == DEFAULT_LLM_MODEL):
        return env_value.strip()
    return (cli_value or DEFAULT_LLM_MODEL).strip()


def _model_race_config_with_exclusions(config: ModelRaceConfig, exclude_models_raw: str | None) -> ModelRaceConfig:
    if not exclude_models_raw or not exclude_models_raw.strip():
        return config
    excluded = {part.strip().lower() for part in exclude_models_raw.split(",") if part.strip()}
    included: list[str] = []
    for spec in model_specs():
        tokens = {
            spec["provider"].lower(),
            spec["model_id"].lower(),
            spec["model_key"].lower(),
            spec["display_name"].lower(),
            spec["display_name"].lower().replace(" ", "_"),
        }
        if tokens & excluded:
            continue
        included.append(spec["model_key"])
    if not included:
        raise typer.BadParameter("exclude-models removed every model; leave at least one model enabled.")
    return replace(config, include_models=included)


def _model_race_model_payload(
    settings: Settings,
    series: str,
    station: str,
    target_date: date | None = None,
) -> dict[str, Any]:
    return _model_estimates_payload(
        settings,
        series,
        station,
        target_date=target_date,
        include_probabilities=True,
        show_failures=True,
        store_results=True,
    )


def _trader_cached_model_payload(
    *,
    settings: Settings,
    series: str,
    station: str,
    target_date: date | None,
    cache: dict[str, Any],
    noaa_model_mode: str,
    market_refresh_seconds: int,
    fast_model_refresh_seconds: int,
    noaa_model_refresh_seconds: int,
    observation_refresh_seconds: int,
    use_cached_models: bool = False,
    force_model_recompute_every_iteration: bool = True,
    model_refresh_seconds: int = 0,
) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    market_date = target_date or current_lax_market_date(now_dt)
    requested_noaa_model_mode = noaa_model_mode
    cache_disabled = (
        force_model_recompute_every_iteration
        or not use_cached_models
        or model_refresh_seconds <= 0
        or noaa_model_mode in {"full_recompute_each_iteration", "always"}
    )
    if cache_disabled and noaa_model_mode != "off":
        noaa_model_mode = "full_recompute_each_iteration"
    diagnostics: dict[str, Any] = {
        "noaa_model_mode": noaa_model_mode,
        "requested_noaa_model_mode": requested_noaa_model_mode,
        "use_cached_models": use_cached_models,
        "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
        "model_refresh_seconds": model_refresh_seconds,
        "model_cache_used": False,
        "fast_model_cache_used": False,
        "noaa_cache_used": False,
        "noaa_fetch_elapsed_seconds": 0.0,
        "open_meteo_fetch_elapsed_seconds": 0.0,
        "fast_model_fetch_elapsed_seconds": 0.0,
        "market_fetch_elapsed_seconds": 0.0,
        "model_fetch_elapsed_seconds": 0.0,
        "model_source_mode": "full",
        "model_source_degraded": False,
        "model_source_degraded_reason": None,
        "warnings": [],
    }

    market_started = time.perf_counter()
    if (
        cache_disabled
        or _cache_due(cache.get("market_last_refresh_utc"), market_refresh_seconds, now_dt)
        or "market_context" not in cache
    ):
        cache["market_context"] = _market_context(settings, series, market_date)
        cache["market_last_refresh_utc"] = now_dt
        diagnostics["market_cache_used"] = False
    else:
        diagnostics["market_cache_used"] = True
    diagnostics["market_fetch_elapsed_seconds"] = round(time.perf_counter() - market_started, 4)
    market_context = cache["market_context"]

    fast_due = (
        cache_disabled
        or _cache_due(cache.get("fast_model_last_refresh_utc"), fast_model_refresh_seconds, now_dt)
        or _cache_due(cache.get("observation_last_refresh_utc"), observation_refresh_seconds, now_dt)
        or "fast_estimates" not in cache
    )
    if fast_due:
        fast_started = time.perf_counter()
        try:
            weather, forecast = _weather_context(settings, station, market_date)
            cache["weather"] = weather
            cache["forecast"] = forecast
            cache["fast_estimates"] = _estimates_from_weather(
                settings,
                station,
                "current,open_meteo",
                None,
                weather,
                forecast,
                market_date,
            )
            refreshed = datetime.now(timezone.utc)
            cache["fast_model_last_refresh_utc"] = refreshed
            cache["observation_last_refresh_utc"] = refreshed
            diagnostics["fast_model_cache_used"] = False
        except Exception as exc:  # noqa: BLE001
            diagnostics["model_source_degraded"] = True
            if all(key in cache for key in ("weather", "forecast", "fast_estimates")):
                diagnostics["fast_model_cache_used"] = True
                diagnostics["model_cache_used"] = True
                diagnostics["model_source_degraded_reason"] = f"fast_model_fetch_failed_using_cache: {exc}"
            else:
                diagnostics["fast_model_cache_used"] = False
                diagnostics["model_cache_used"] = False
                diagnostics["model_source_degraded_reason"] = f"fast_model_fetch_failed_no_cache: {exc}"
                cache["weather"] = SimpleNamespace(
                    observed_high_so_far_f=None,
                    latest_observation_utc=None,
                    model_future_high_f=None,
                )
                cache["forecast"] = SimpleNamespace()
                cache["fast_estimates"] = []
        diagnostics["fast_model_fetch_elapsed_seconds"] = round(time.perf_counter() - fast_started, 4)
        diagnostics["open_meteo_fetch_elapsed_seconds"] = diagnostics["fast_model_fetch_elapsed_seconds"]
    else:
        diagnostics["fast_model_cache_used"] = True
        diagnostics["model_cache_used"] = True
    weather = cache["weather"]
    forecast = cache["forecast"]

    noaa_last_refresh = cache.get("noaa_last_refresh_utc")
    noaa_estimates = list(cache.get("noaa_estimates") or [])
    requested_noaa_models = set((getattr(settings, "model_estimate_default_models", {}) or {}).get("noaa_herbie", []))
    cached_noaa_models = {
        str(getattr(estimate, "model_id", None) or (estimate.get("model_id") if isinstance(estimate, dict) else ""))
        for estimate in noaa_estimates
    }
    missing_requested_noaa_models = sorted(model for model in requested_noaa_models - cached_noaa_models if model)
    diagnostics["noaa_cache_missing_requested_models"] = missing_requested_noaa_models
    if noaa_model_mode == "off":
        noaa_estimates = []
        diagnostics["model_source_mode"] = "fast_noaa_off"
        diagnostics["model_source_degraded"] = True
        if not diagnostics.get("model_source_degraded_reason"):
            diagnostics["model_source_degraded_reason"] = "noaa_model_mode_off"
    else:
        noaa_due = (
            cache_disabled
            or noaa_model_mode in {"always", "full_recompute_each_iteration"}
            or _cache_due(noaa_last_refresh, noaa_model_refresh_seconds, now_dt)
            or bool(missing_requested_noaa_models)
        )
        if noaa_due or "noaa_estimates" not in cache:
            noaa_started = time.perf_counter()
            try:
                noaa_estimates = _estimates_from_weather(
                    settings,
                    station,
                    "noaa_herbie",
                    None,
                    weather,
                    forecast,
                    market_date,
                )
                cache["noaa_estimates"] = noaa_estimates
                cache["noaa_last_refresh_utc"] = datetime.now(timezone.utc)
                diagnostics["noaa_cache_used"] = False
            except Exception as exc:  # noqa: BLE001
                diagnostics["model_source_degraded"] = True
                diagnostics["model_source_degraded_reason"] = f"noaa_fetch_failed: {exc}"
                if cache_disabled:
                    noaa_estimates = []
                    diagnostics["noaa_cache_used"] = False
                else:
                    diagnostics["noaa_cache_used"] = bool(noaa_estimates)
            diagnostics["noaa_fetch_elapsed_seconds"] = round(time.perf_counter() - noaa_started, 4)
        else:
            diagnostics["noaa_cache_used"] = bool(noaa_estimates)
            diagnostics["model_cache_used"] = bool(noaa_estimates)

    noaa_last_refresh = cache.get("noaa_last_refresh_utc")
    noaa_cache_age = None if cache_disabled else _cache_age_seconds(noaa_last_refresh, now_dt)
    diagnostics["noaa_last_refresh_utc"] = _iso_or_none(noaa_last_refresh)
    diagnostics["noaa_next_refresh_utc"] = _iso_or_none(
        noaa_last_refresh + timedelta(seconds=noaa_model_refresh_seconds)
        if noaa_last_refresh is not None and noaa_model_mode not in {"off", "full_recompute_each_iteration"} and not cache_disabled
        else None
    )
    diagnostics["noaa_cache_age_seconds"] = noaa_cache_age
    if noaa_model_mode == "scheduled" and noaa_cache_age is not None and noaa_cache_age > noaa_model_refresh_seconds * 1.5:
        diagnostics["model_source_degraded"] = True
        diagnostics["model_source_degraded_reason"] = "noaa_cache_stale"

    estimates = [*list(cache.get("fast_estimates") or []), *noaa_estimates]
    residual_sigma_used = float(settings.model_estimate_probability_residual_sigma_f)
    probabilities = (
        probabilities_for_estimates(
            estimates,
            market_context["brackets"],
            market_context["tops"],
            residual_sigma_f=residual_sigma_used,
            sample_count=settings.monte_carlo_samples,
        )
        if market_context["brackets"]
        else []
    )
    stored_estimate_ids: dict[str, int] = {}
    stored_probability_ids: list[int] = []
    store_obj = _store(settings)
    for estimate in estimates:
        stored_estimate_ids[estimate_key(estimate)] = store_obj.save_model_estimate(estimate)
    for probability in probabilities:
        record = probability.to_record()
        record["estimate_id"] = stored_estimate_ids.get(f"{probability.provider}:{probability.model_id}")
        stored_probability_ids.append(store_obj.save_model_estimate_probability(record))

    if cache_disabled and noaa_model_mode != "off":
        diagnostics["model_source_mode"] = "fresh_recompute_each_iteration"
    else:
        diagnostics["model_source_mode"] = (
            diagnostics["model_source_mode"]
            if diagnostics["model_source_degraded"]
            else ("fast_with_cached_noaa" if diagnostics["noaa_cache_used"] else "fast_with_fresh_noaa")
        )
    diagnostics["model_fetch_elapsed_seconds"] = round(
        float(diagnostics.get("fast_model_fetch_elapsed_seconds") or 0.0)
        + float(diagnostics.get("noaa_fetch_elapsed_seconds") or 0.0),
        4,
    )
    diagnostics["model_recomputed_this_iteration"] = not bool(diagnostics.get("model_cache_used"))
    cached_model_violation = bool(
        cache_disabled
        and (
            diagnostics.get("model_cache_used")
            or diagnostics.get("fast_model_cache_used")
            or diagnostics.get("noaa_cache_used")
        )
    )
    diagnostics["cached_model_violation"] = cached_model_violation
    diagnostics["cached_model_violation_message"] = None
    if cached_model_violation:
        diagnostics["cached_model_violation_message"] = (
            "cached model estimate was used even though --no-use-cached-models or force recompute was enabled"
        )
        diagnostics["warnings"].append(diagnostics["cached_model_violation_message"])
    payload = {
        "generated_at_utc": utc_now(),
        "series": series,
        "station": station,
        "market_date": market_date,
        "observed_high_so_far_f": weather.observed_high_so_far_f,
        "latest_observation_utc": weather.latest_observation_utc,
        "latest_observed_temp_f": (getattr(weather, "model_details", {}) or {}).get("latest_observed_temp_f"),
        "forecast_window_start_utc": utc_now(),
        "forecast_window_end_utc": lax_climate_day_utc(market_date)[1],
        "current_production_estimate_f": weather.model_future_high_f,
        "markets_count": len(market_context["markets"]),
        "bracket_count": len(market_context["brackets"]),
        "residual_sigma_f": residual_sigma_used,
        "estimates": [estimate.to_record() for estimate in estimates],
        "all_estimate_count": len(estimates),
        "probabilities": [probability.to_record() for probability in probabilities],
        "stored": True,
        "stored_estimate_ids": stored_estimate_ids,
        "stored_probability_ids": stored_probability_ids,
        "open_meteo": forecast_diagnostics(forecast),
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
        "model_source": diagnostics,
        "model_source_diagnostics": diagnostics,
    }
    payload.update(diagnostics)
    return payload


def _cache_due(last_refresh: Any, cadence_seconds: int, now_dt: datetime) -> bool:
    if last_refresh is None:
        return True
    if cadence_seconds <= 0:
        return True
    parsed = last_refresh if isinstance(last_refresh, datetime) else _parse_utc_datetime(last_refresh)
    if parsed is None:
        return True
    return (now_dt - parsed).total_seconds() >= cadence_seconds


def _cache_age_seconds(last_refresh: Any, now_dt: datetime) -> float | None:
    parsed = last_refresh if isinstance(last_refresh, datetime) else _parse_utc_datetime(last_refresh)
    if parsed is None:
        return None
    return round(max(0.0, (now_dt - parsed).total_seconds()), 4)


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else _parse_utc_datetime(value)
    return parsed.isoformat() if parsed else None


def _cache_namespace_payload(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return safe_console_payload(value)
    if hasattr(value, "__dict__"):
        return safe_console_payload(vars(value))
    return None


def _cache_namespace_from_payload(value: Any) -> SimpleNamespace | None:
    if not isinstance(value, dict):
        return None
    return SimpleNamespace(**value)


def _model_estimates_from_cache_records(rows: Any) -> list[ModelEstimate]:
    estimates: list[ModelEstimate] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        record = dict(row)
        for field_name in (
            "asof_utc",
            "run_utc",
            "cycle_utc",
            "forecast_window_start_utc",
            "forecast_window_end_utc",
        ):
            if record.get(field_name) is not None:
                record[field_name] = _parse_utc_datetime(record.get(field_name))
        if record.get("asof_utc") is None:
            continue
        try:
            estimates.append(ModelEstimate(**record))
        except TypeError:
            continue
    return estimates


def _model_refresh_cache_from_disk(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cache: dict[str, Any] = {}
    for key in ("noaa_last_refresh_utc", "fast_model_last_refresh_utc", "observation_last_refresh_utc"):
        parsed = _parse_utc_datetime(raw.get(key))
        if parsed is not None:
            cache[key] = parsed
    noaa_estimates = _model_estimates_from_cache_records(raw.get("noaa_estimates"))
    if noaa_estimates:
        cache["noaa_estimates"] = noaa_estimates
    fast_estimates = _model_estimates_from_cache_records(raw.get("fast_estimates"))
    if fast_estimates:
        cache["fast_estimates"] = fast_estimates
    weather = _cache_namespace_from_payload(raw.get("weather"))
    if weather is not None:
        cache["weather"] = weather
    forecast = _cache_namespace_from_payload(raw.get("forecast"))
    if forecast is not None:
        cache["forecast"] = forecast
    return cache


def _write_model_refresh_cache(path: str | Path | None, cache: dict[str, Any]) -> None:
    if path is None:
        return
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    noaa_estimates = cache.get("noaa_estimates") or []
    fast_estimates = cache.get("fast_estimates") or []
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "noaa_last_refresh_utc": _iso_or_none(cache.get("noaa_last_refresh_utc")),
        "fast_model_last_refresh_utc": _iso_or_none(cache.get("fast_model_last_refresh_utc")),
        "observation_last_refresh_utc": _iso_or_none(cache.get("observation_last_refresh_utc")),
        "noaa_estimates": [
            estimate.to_record() if hasattr(estimate, "to_record") else estimate
            for estimate in noaa_estimates
        ],
        "fast_estimates": [
            estimate.to_record() if hasattr(estimate, "to_record") else estimate
            for estimate in fast_estimates
        ],
        "weather": _cache_namespace_payload(cache.get("weather")),
        "forecast": _cache_namespace_payload(cache.get("forecast")),
    }
    cache_path.write_text(json.dumps(safe_console_payload(payload), indent=2, sort_keys=True), encoding="utf-8")


def _model_race_model_payload_for_spec(
    settings: Settings,
    series: str,
    station: str,
    spec: dict[str, str],
    context: dict[str, Any] | None = None,
    target_date: date | None = None,
) -> dict[str, Any]:
    if context is not None:
        weather = context["weather"]
        forecast = context["forecast"]
        market_date = context["market_date"]
        brackets = context["brackets"]
        tops = context["tops"]
        markets = context["markets"]
        estimates = _estimates_from_weather(
            settings,
            station,
            spec["provider"],
            spec["model_id"],
            weather,
            forecast,
            market_date,
        )
        residual_sigma_used = float(settings.model_estimate_probability_residual_sigma_f)
        probabilities = probabilities_for_estimates(
            estimates,
            brackets,
            tops,
            residual_sigma_f=residual_sigma_used,
            sample_count=settings.monte_carlo_samples,
        )
        return {
            "generated_at_utc": utc_now(),
            "series": series,
            "station": station,
            "market_date": market_date,
            "observed_high_so_far_f": weather.observed_high_so_far_f,
            "latest_observation_utc": weather.latest_observation_utc,
            "forecast_window_start_utc": utc_now(),
            "forecast_window_end_utc": lax_climate_day_utc(market_date)[1],
            "current_production_estimate_f": weather.model_future_high_f,
            "markets_count": len(markets),
            "bracket_count": len(brackets),
            "residual_sigma_f": residual_sigma_used,
            "estimates": [estimate.to_record() for estimate in estimates],
            "all_estimate_count": len(estimates),
            "probabilities": [probability.to_record() for probability in probabilities],
            "stored": False,
            "stored_estimate_ids": {},
            "stored_probability_ids": [],
            "open_meteo": forecast_diagnostics(forecast),
            "live_trading_enabled": settings.kalshi_enable_real_orders,
            "paper_trading": False,
        }
    return _model_estimates_payload(
        settings,
        series,
        station,
        target_date=target_date,
        providers_raw=spec["provider"],
        models_raw=spec["model_id"],
        include_probabilities=True,
        show_failures=True,
        store_results=False,
    )


def _model_race_config_for_model(config: ModelRaceConfig, model_key_value: str) -> ModelRaceConfig:
    return replace(config, include_models=[model_key_value])


def _store_model_race_payload_results(store_obj: SQLiteStore, payload: dict[str, Any]) -> None:
    if payload.get("stored"):
        return
    stored_estimate_ids: dict[str, int] = {}
    for estimate in payload.get("estimates", []):
        stored_estimate_ids[estimate_key(estimate)] = store_obj.save_model_estimate(estimate)
    stored_probability_ids: list[int] = []
    for probability in payload.get("probabilities", []):
        record = dict(probability)
        record["estimate_id"] = stored_estimate_ids.get(f"{record.get('provider')}:{record.get('model_id')}")
        stored_probability_ids.append(store_obj.save_model_estimate_probability(record))
    payload["stored"] = True
    payload["stored_estimate_ids"] = stored_estimate_ids
    payload["stored_probability_ids"] = stored_probability_ids


def _model_race_worker_failure_payload(
    settings: Settings,
    series: str,
    station: str,
    spec: dict[str, str],
    exc: BaseException,
    target_date: date | None = None,
) -> dict[str, Any]:
    now = utc_now()
    market_date = target_date or current_lax_market_date()
    _day_start, window_end_utc = lax_climate_day_utc(market_date)
    return {
        "generated_at_utc": now,
        "series": series,
        "station": station,
        "market_date": market_date,
        "observed_high_so_far_f": None,
        "latest_observation_utc": None,
        "forecast_window_start_utc": now,
        "forecast_window_end_utc": window_end_utc,
        "current_production_estimate_f": None,
        "markets_count": 0,
        "bracket_count": 0,
        "residual_sigma_f": float(settings.model_estimate_probability_residual_sigma_f),
        "estimates": [
            {
                "asof_utc": now.isoformat(),
                "station": station,
                "market_date": market_date.isoformat(),
                "provider": spec["provider"],
                "model_id": spec["model_id"],
                "model_name": spec.get("display_name", spec["model_id"]),
                "model_family": spec["provider"],
                "run_utc": None,
                "cycle_utc": None,
                "forecast_window_start_utc": now.isoformat(),
                "forecast_window_end_utc": window_end_utc.isoformat(),
                "forecast_hours_used": [],
                "observed_high_so_far_f": None,
                "future_high_f": None,
                "settlement_high_estimate_f": None,
                "successful": False,
                "error_message": str(exc),
                "source": "model_worker_error",
                "source_url": None,
                "details_json": {"worker_error": str(exc)},
            }
        ],
        "all_estimate_count": 1,
        "probabilities": [],
        "stored": False,
        "stored_estimate_ids": {},
        "stored_probability_ids": [],
        "open_meteo": {},
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _model_race_exit_payload(
    settings: Settings,
    series: str,
    station: str,
    race_id: str | None = None,
) -> dict[str, Any]:
    store_obj = _store(settings)
    payload = _latest_stored_model_payload(settings, series, station)
    open_tickers: set[str] = set()
    if race_id is not None:
        open_tickers = {
            row["market_ticker"]
            for row in store_obj.load_open_model_race_positions(race_id)
            if row.get("market_ticker")
        }
    tickers = sorted(open_tickers)
    if not tickers:
        payload["generated_at_utc"] = utc_now()
        payload["exit_monitor_market_refresh"] = "skipped_no_open_positions"
        return payload
    try:
        orderbooks = _kalshi(settings).get_multiple_orderbooks(tickers, depth=1)
        tops = {ticker: parse_orderbook_top(ticker, data) for ticker, data in orderbooks.items()}
    except Exception as exc:  # noqa: BLE001
        payload["exit_monitor_market_refresh_error"] = str(exc)
        return payload
    refreshed: list[dict[str, Any]] = []
    for row in payload.get("probabilities", []):
        record = dict(row)
        top = tops.get(record.get("market_ticker"))
        if top is not None:
            record["yes_bid"] = top.yes_bid
            record["yes_ask"] = top.yes_ask
            record["no_bid"] = top.no_bid
            record["no_ask"] = top.no_ask
            p_yes = Decimal(str(record.get("p_yes") or "0"))
            record["yes_edge"] = p_yes - top.yes_ask if top.yes_ask is not None else None
            record["no_edge"] = Decimal("1") - p_yes - top.no_ask if top.no_ask is not None else None
        refreshed.append(record)
    payload["probabilities"] = refreshed
    payload["generated_at_utc"] = utc_now()
    payload["exit_monitor_market_refresh"] = "ok"
    return payload


def _write_model_race_latest_outputs(
    store_obj: SQLiteStore,
    payload: dict[str, Any],
    *,
    output_dir: str | Path = "reports/model_race",
    text: str | None = None,
) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = text or compact_model_race_text(payload)
    write_text_report(out / "latest_model_race.txt", text)
    write_json_report(out / "latest_model_race.json", payload)
    write_text_report(out / "latest_model_race_safe.txt", text)
    write_json_report(out / "latest_model_race_safe.json", payload)
    _write_csv(out / "model_race_leaderboard.csv", safe_console_payload(payload.get("leaderboard", [])))
    _write_csv(out / "model_race_trades.csv", safe_console_payload(store_obj.load_model_race_fills(payload["race_id"])))
    report_payload = model_race_report_payload(store_obj, payload["race_id"])
    write_text_report(out / "model_race_report_safe.txt", model_race_report_text(report_payload))


def _write_model_race_session_outputs(
    session_dir: Path,
    iterations: list[dict[str, Any]],
    store_obj: SQLiteStore,
    race_id: str,
) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "race_id": race_id,
        "iterations": len(iterations),
        "latest": iterations[-1] if iterations else None,
        "fake_money_only": True,
        "live_trading_enabled": False,
    }
    write_json_report(session_dir / "session_summary.json", summary)
    write_text_report(
        session_dir / "session_summary.txt",
        compact_model_race_text(iterations[-1]) if iterations else "No iterations completed.",
    )
    with (session_dir / "iterations.jsonl").open("w", encoding="utf-8") as handle:
        for payload in iterations:
            handle.write(json.dumps(safe_console_payload(payload), default=str) + "\n")
    _write_csv(session_dir / "leaderboard_final.csv", store_obj.load_model_race_leaderboard(race_id))
    _write_csv(session_dir / "trades.csv", store_obj.load_model_race_fills(race_id))


def _model_race_terminal_text(payload: dict[str, Any], *, debug_decisions: bool = False) -> str:
    text = compact_model_race_text(payload)
    if debug_decisions:
        text += "\n\n" + model_race_debug_text(payload)
    return text


def _run_model_race_worker_loop(
    *,
    settings: Settings,
    series: str,
    station: str,
    target_date: date | None,
    store_obj: SQLiteStore,
    config: ModelRaceConfig,
    entry_interval: int,
    exit_interval_seconds: int,
    max_entry_iterations: int,
    max_exit_iterations: int,
    entry_only: bool,
    force_flat_at_end: bool,
    json_output: bool,
    output_dir: str,
    model_worker_count: int,
    debug_decisions: bool = False,
) -> None:
    specs = [spec for spec in model_specs() if spec["model_key"] in set(config.include_models)]
    if not specs:
        raise typer.BadParameter("No model race workers are enabled.")
    store_obj.create_model_race(config.race_id, specs, config.starting_cash_per_model)
    session_dir = timestamped_report_dir(output_dir, "model_race_workers")
    iterations: list[dict[str, Any]] = []
    latest_model_payload: dict[str, Any] | None = None
    worker_count = max(1, min(model_worker_count, len(specs)))
    entry_counts = {spec["model_key"]: 0 for spec in specs}
    next_due = {spec["model_key"]: 0.0 for spec in specs}
    active: dict[Future[dict[str, Any]], dict[str, str]] = {}
    active_keys: set[str] = set()
    exit_count = 0
    entry_emit_count = 0
    next_exit_due = time.monotonic() + exit_interval_seconds
    last_wait_status_mono = 0.0

    def worker_status(message: str) -> None:
        console.print(message)
        console.file.flush()

    def emit(payload: dict[str, Any], kind: str, number: int) -> None:
        payload["iteration"] = number
        payload["iteration_kind"] = kind
        payload["model_worker_mode"] = True
        iterations.append(payload)
        text = _model_race_terminal_text(payload, debug_decisions=debug_decisions)
        _write_model_race_latest_outputs(store_obj, payload, output_dir=output_dir, text=text)
        if json_output:
            console.print(safe_console_payload(payload))
        else:
            console.print(text)
        console.file.flush()

    def schedule_due(executor: ThreadPoolExecutor) -> None:
        now_mono = time.monotonic()
        due_specs: list[dict[str, str]] = []
        for spec in specs:
            key = spec["model_key"]
            if key in active_keys:
                continue
            if entry_counts[key] >= max_entry_iterations:
                continue
            if now_mono < next_due[key]:
                continue
            due_specs.append(spec)
        if not due_specs:
            return
        worker_status(
            "model workers: refreshing shared Kalshi/weather snapshot for "
            + ", ".join(spec["display_name"] for spec in due_specs)
        )
        try:
            context = _prediction_context(settings, series, station, target_date)
        except Exception as exc:  # noqa: BLE001
            worker_status(f"model worker shared market/weather refresh skipped: {exc}")
            for spec in due_specs:
                key = spec["model_key"]
                entry_counts[key] += 1
                next_due[key] = now_mono + entry_interval
            return
        for spec in due_specs:
            key = spec["model_key"]
            active[executor.submit(_model_race_model_payload_for_spec, settings, series, station, spec, context)] = spec
            active_keys.add(key)
            entry_counts[key] += 1
            next_due[key] = now_mono + entry_interval
        worker_status(
            f"model workers: scheduled {len(due_specs)} model refresh(es); "
            f"{len(active_keys)} active or queued"
        )

    def drain_completed() -> None:
        nonlocal entry_emit_count, latest_model_payload
        for future, spec in list(active.items()):
            if not future.done():
                continue
            active.pop(future)
            active_keys.discard(spec["model_key"])
            try:
                model_payload = future.result()
            except Exception as exc:  # noqa: BLE001
                model_payload = _model_race_worker_failure_payload(settings, series, station, spec, exc, target_date)
            _store_model_race_payload_results(store_obj, model_payload)
            latest_model_payload = model_payload
            model_config = _model_race_config_for_model(config, spec["model_key"])
            entry_emit_count += 1
            payload = run_model_race_once(store_obj, model_payload, model_config)
            payload["worker_model_key"] = spec["model_key"]
            payload["worker_model_display"] = spec["display_name"]
            emit(payload, "model-entry", entry_emit_count)

    def emit_exit_if_due() -> None:
        nonlocal exit_count, latest_model_payload, next_exit_due
        if entry_only or exit_count >= max_exit_iterations:
            return
        now_mono = time.monotonic()
        if now_mono < next_exit_due:
            return
        exit_count += 1
        next_exit_due = now_mono + exit_interval_seconds
        try:
            exit_payload = _model_race_exit_payload(settings, series, station, race_id=config.race_id)
        except Exception as exc:  # noqa: BLE001
            console.print(f"exit monitor skipped: {exc}")
            return
        latest_model_payload = exit_payload
        emit(run_model_race_exit_monitor(store_obj, exit_payload, config), "exit", exit_count)

    def still_running() -> bool:
        pending_entries = any(count < max_entry_iterations for count in entry_counts.values())
        pending_exits = (not entry_only) and exit_count < max_exit_iterations
        return pending_entries or pending_exits or bool(active)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        while still_running():
            schedule_due(executor)
            drain_completed()
            emit_exit_if_due()
            if not still_running():
                break
            next_times = [due for key, due in next_due.items() if entry_counts[key] < max_entry_iterations]
            if not entry_only and exit_count < max_exit_iterations:
                next_times.append(next_exit_due)
            now_mono = time.monotonic()
            sleep_for = 0.25 if active else 1.0
            if next_times:
                sleep_for = max(0.25, min(sleep_for, min(next_times) - now_mono))
            if active and now_mono - last_wait_status_mono >= 30:
                last_wait_status_mono = now_mono
                worker_status("model workers: waiting on " + ", ".join(sorted(active_keys)))
            time.sleep(sleep_for)

    if force_flat_at_end and latest_model_payload is not None:
        force_flat_model_race(store_obj, config.race_id, latest_model_payload, config)
    _write_model_race_session_outputs(session_dir, iterations, store_obj, config.race_id)


def _fmt_f(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
        if math.isnan(number):
            return "n/a"
        return f"{number:.1f} F"
    except (TypeError, ValueError):
        return str(value)


def _fmt_f_short(value: Any) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
        if math.isnan(number):
            return "--"
        return f"{number:.1f} F"
    except (TypeError, ValueError):
        return str(value)


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
        if math.isnan(number):
            return "--"
        return f"{number * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_utc_minute(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return text


def _fmt_time_utc(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.strftime("%H:%M")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        parsed = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return parsed.strftime("%H:%M")
    except ValueError:
        return text[:5] if len(text) >= 5 else text


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def _model_display_key(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "")
    model_id = str(row.get("model_id") or "")
    if provider == "current":
        return model_id
    if not provider:
        return model_id
    return f"{provider}:{model_id}"


def _model_matrix_key(row: dict[str, Any]) -> str:
    provider = str(row.get("provider") or "")
    model_id = str(row.get("model_id") or "")
    return model_id if provider in {"current", "open_meteo"} else _model_display_key(row)


def _compact_bracket_label(row: dict[str, Any]) -> str:
    lo = row.get("bracket_lower_f")
    hi = row.get("bracket_upper_f")
    try:
        lo_int = int(float(lo)) if lo is not None else None
        hi_int = int(float(hi)) if hi is not None else None
    except (TypeError, ValueError):
        return str(row.get("bracket_label") or "")
    if lo_int is None and hi_int is not None:
        return f"<{hi_int + 1}"
    if hi_int is None and lo_int is not None:
        return f">{lo_int - 1}"
    if lo_int is not None and hi_int is not None:
        return f"{lo_int}-{hi_int}"
    return str(row.get("bracket_label") or "")


def _bracket_sort_key(row: dict[str, Any]) -> tuple[float, float]:
    lo = _safe_float(row.get("bracket_lower_f"))
    hi = _safe_float(row.get("bracket_upper_f"))
    return (
        lo if lo is not None else float("-inf"),
        hi if hi is not None else float("inf"),
    )


def _estimate_bracket_label(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "--"
    if number < 67:
        return "<67"
    if number >= 75:
        return ">74"
    lo = math.floor(number)
    if lo % 2 == 0:
        lo -= 1
    lo = max(67, lo)
    return f"{lo}-{lo + 1}"


def _agreement_status(spread_f: float | None) -> str:
    if spread_f is None:
        return "NO_DATA"
    if spread_f <= 1.0:
        return "HIGH"
    if spread_f <= 2.0:
        return "MEDIUM"
    return "LOW"


def _agreement_interpretation(status: str) -> str:
    if status == "HIGH":
        return "Available models mostly agree."
    if status == "MEDIUM":
        return "Available models differ enough to watch the bracket boundary."
    if status == "LOW":
        return "Available models disagree; treat the bracket probabilities cautiously."
    return "No successful model estimates are available."


def _data_readiness_status(readiness: dict[str, Any]) -> str:
    outcomes = int(readiness.get("official_outcomes") or 0)
    joined_rows = int(readiness.get("joined_rows") or 0)
    unique_dates = int(readiness.get("unique_joined_market_dates") or 0)
    if outcomes == 0 or joined_rows == 0:
        return "NO_OUTCOMES"
    if joined_rows < 30 or unique_dates < 5:
        return "SMOKE_TEST_ONLY"
    if joined_rows >= 100 and unique_dates >= 15:
        return "INITIAL_VALIDATION"
    return "EARLY_SIGNAL"


def _readiness_message(status: str, unique_dates: int) -> str:
    if status == "NO_OUTCOMES":
        return "no joined official outcomes yet; use this only to inspect plumbing"
    if status == "SMOKE_TEST_ONLY":
        return (
            f"{unique_dates} joined market date(s), enough to verify plumbing, "
            "not enough to trust edge"
        )
    if status == "EARLY_SIGNAL":
        return "some joined outcomes exist, but the sample is still early"
    return "enough dates for an initial validation pass, still not live-trading evidence"


def _next_action_for_readiness(status: str) -> str:
    if status == "NO_OUTCOMES":
        return "Record or fetch official outcomes, then run join-outcomes."
    if status == "SMOKE_TEST_ONLY":
        return "Continue collecting snapshots and join official outcomes after settlement."
    if status == "EARLY_SIGNAL":
        return "Keep collecting across more market dates before trusting edge estimates."
    return "Review calibration by bracket and model version before changing thresholds."


def _model_summary_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    probabilities_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bracket_templates: dict[str, dict[str, Any]] = {}
    for row in payload.get("probabilities", []):
        key = f"{row.get('provider')}:{row.get('model_id')}"
        probabilities_by_key[key].append(row)
        label = _compact_bracket_label(row)
        bracket_templates[label] = row

    ordered_brackets = [
        label
        for label, _row in sorted(
            bracket_templates.items(),
            key=lambda item: _bracket_sort_key(item[1]),
        )
    ]

    rows: list[dict[str, Any]] = []
    for estimate in payload.get("estimates", []):
        key = f"{estimate.get('provider')}:{estimate.get('model_id')}"
        probs = probabilities_by_key.get(key, [])
        probability_map = {_compact_bracket_label(row): _safe_float(row.get("p_yes")) for row in probs}
        top_probability = None
        top_label = "--"
        if probability_map:
            top_label, top_probability = max(
                probability_map.items(),
                key=lambda item: float("-inf") if item[1] is None else item[1],
            )
        status = "ok" if bool(estimate.get("successful")) else "unavailable"
        estimated_high = (
            _safe_float(estimate.get("settlement_high_estimate_f", estimate.get("future_high_f")))
            if status == "ok"
            else None
        )
        rows.append(
            {
                "provider": estimate.get("provider"),
                "model_id": estimate.get("model_id"),
                "model": _model_display_key(estimate),
                "matrix_model": _model_matrix_key(estimate),
                "estimated_high_f": estimated_high,
                "future_high_f": _safe_float(estimate.get("future_high_f")),
                "most_likely_bracket": top_label,
                "top_probability": top_probability,
                "probabilities": probability_map,
                "status": status,
                "error": estimate.get("error_message"),
                "source": estimate.get("source"),
            }
        )
    return rows, ordered_brackets


def _open_meteo_summary(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("open_meteo") or {}
    return {
        "successful_models": diagnostics.get("successful_models", []),
        "failed_models": diagnostics.get("failed_models", {}),
        "fallback_used": diagnostics.get("fallback_used", False),
        "model_maxes_f": diagnostics.get("model_maxes_f", {}),
    }


def _market_view(
    settings: Settings,
    payload: dict[str, Any],
    include_prices: bool,
    include_edges: bool,
    top_n: int | None,
) -> dict[str, Any]:
    included = include_prices or include_edges
    if not included:
        return {"included": False}
    source_rows = [
        row
        for row in payload.get("probabilities", [])
        if row.get("provider") == "current" and row.get("model_id") == "current_weighted_blend"
    ]
    if not source_rows:
        source_rows = list(payload.get("probabilities", []))
    hurdle = settings.min_edge + settings.fee_buffer + settings.model_error_buffer
    mismatches: list[dict[str, Any]] = []
    for row in source_rows:
        best_side, best_edge = _best_edge(row)
        p_yes = _safe_float(row.get("p_yes"))
        side_probability = p_yes if best_side == "yes" else (1 - p_yes if p_yes is not None else None)
        market_ask = row.get("yes_ask") if best_side == "yes" else row.get("no_ask")
        edge_decimal = _safe_decimal(best_edge)
        mismatches.append(
            {
                "bracket": _compact_bracket_label(row),
                "market_ticker": row.get("market_ticker"),
                "side": (best_side or "none").upper(),
                "model_probability": side_probability,
                "yes_ask": row.get("yes_ask"),
                "no_ask": row.get("no_ask"),
                "market_ask": market_ask,
                "edge": str(best_edge) if best_edge is not None else None,
                "would_trade": bool(edge_decimal is not None and edge_decimal > hurdle),
                "note": "apparent edge" if edge_decimal is not None and edge_decimal > Decimal("0") else "",
            }
        )
    mismatches.sort(key=lambda row: abs(_safe_float(row.get("edge")) or 0.0), reverse=True)
    if top_n is not None:
        mismatches = mismatches[:top_n]
    return {
        "included": True,
        "show_prices": include_prices,
        "show_edges": include_edges,
        "required_hurdle": str(hurdle),
        "top_mismatches": mismatches,
    }


def _simple_summary_from_model_payload(
    settings: Settings,
    store_obj: SQLiteStore,
    payload: dict[str, Any],
    *,
    show_prices: bool,
    show_edges: bool,
    top_n: int | None,
    source: str,
    live_error: str | None = None,
) -> dict[str, Any]:
    rows, bracket_labels = _model_summary_rows(payload)
    successful_highs = [
        row["estimated_high_f"]
        for row in rows
        if row["status"] == "ok" and row.get("estimated_high_f") is not None
    ]
    consensus = statistics.mean(successful_highs) if successful_highs else None
    min_high = min(successful_highs) if successful_highs else None
    max_high = max(successful_highs) if successful_highs else None
    spread = (max_high - min_high) if min_high is not None and max_high is not None else None
    agreement = _agreement_status(spread)
    current_row = next(
        (
            row
            for row in rows
            if row.get("provider") == "current" and row.get("model_id") == "current_weighted_blend"
        ),
        None,
    )
    readiness = validation_reports.calibration_readiness_payload(store_obj, str(payload.get("station")), settings)
    readiness_status = _data_readiness_status(readiness)
    unique_dates = int(readiness.get("unique_joined_market_dates") or 0)
    warnings = [
        f"{readiness_status} - {_readiness_message(readiness_status, unique_dates)}.",
        "This is analysis-only, not a live trading signal.",
    ]
    failed_count = sum(1 for row in rows if row["status"] != "ok")
    if failed_count:
        warnings.append(f"{failed_count} model estimate(s) unavailable; use --show-failures for details.")
    if live_error:
        warnings.append(f"Live read-only fetch failed; showing latest stored data. Error: {live_error}")
    if agreement == "LOW":
        warnings.append("Available model estimates disagree by more than 2.0 F.")

    return {
        "series": payload.get("series"),
        "station": payload.get("station"),
        "market_date": str(payload.get("market_date")),
        "asof_utc": str(payload.get("generated_at_utc") or utc_now()),
        "source": source,
        "observed_high_so_far_f": payload.get("observed_high_so_far_f"),
        "latest_observation_utc": payload.get("latest_observation_utc"),
        "current_production_estimate_f": payload.get("current_production_estimate_f"),
        "consensus_estimate_f": consensus,
        "model_min_estimate_f": min_high,
        "model_max_estimate_f": max_high,
        "model_spread_f": spread,
        "model_agreement_status": agreement,
        "most_likely_bracket_by_current_model": (
            current_row.get("most_likely_bracket") if current_row else None
        ),
        "top_probability_by_current_model": current_row.get("top_probability") if current_row else None,
        "data_readiness_status": readiness_status,
        "unique_joined_market_dates": unique_dates,
        "readiness": readiness,
        "brackets": bracket_labels,
        "models": rows,
        "market_view": _market_view(settings, payload, show_prices, show_edges, top_n),
        "warnings": warnings,
        "warning_summary": warnings,
        "next_action": _next_action_for_readiness(readiness_status),
        "open_meteo": _open_meteo_summary(payload),
        "stored": payload.get("stored", False),
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _latest_stored_model_payload(settings: Settings, series: str, station: str) -> dict[str, Any]:
    store_obj = _store(settings)
    recent_estimates = store_obj.load_latest_model_estimates(station=station, limit=100)
    if not recent_estimates:
        raise RuntimeError("No stored model estimates are available. Run model-probabilities --store or collect-session --include-model-estimates.")
    market_date = str(recent_estimates[0].get("market_date"))
    latest_by_model: dict[str, dict[str, Any]] = {}
    for row in recent_estimates:
        if str(row.get("market_date")) != market_date:
            continue
        key = f"{row.get('provider')}:{row.get('model_id')}"
        if key not in latest_by_model:
            latest_by_model[key] = row
    estimates = list(reversed(list(latest_by_model.values())))
    probabilities = store_obj.load_model_estimate_probabilities_for_estimate_ids(
        [int(row["id"]) for row in estimates if row.get("id") is not None]
    )
    current = next(
        (
            row
            for row in estimates
            if row.get("provider") == "current" and row.get("model_id") == "current_weighted_blend"
        ),
        estimates[-1],
    )
    return {
        "generated_at_utc": estimates[-1].get("created_utc") or utc_now(),
        "series": series,
        "station": station,
        "market_date": market_date,
        "observed_high_so_far_f": current.get("observed_high_so_far_f"),
        "latest_observation_utc": None,
        "current_production_estimate_f": current.get("settlement_high_estimate_f"),
        "estimates": estimates,
        "probabilities": probabilities,
        "stored": False,
        "open_meteo": {},
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _simple_summary_payload(
    settings: Settings,
    series: str,
    station: str,
    *,
    models_raw: str | None,
    residual_sigma: float | None,
    latest_stored: bool,
    live: bool,
    show_failures: bool,
    show_prices: bool,
    show_edges: bool,
    top_n: int | None,
    store_results: bool,
) -> dict[str, Any]:
    store_obj = _store(settings)
    live_error = None
    if live:
        try:
            model_payload = _model_estimates_payload(
                settings,
                series,
                station,
                models_raw=models_raw,
                include_probabilities=True,
                residual_sigma=residual_sigma,
                only_successful=False,
                show_failures=True,
                store_results=store_results,
            )
            return _simple_summary_from_model_payload(
                settings,
                store_obj,
                model_payload,
                show_prices=show_prices,
                show_edges=show_edges,
                top_n=top_n,
                source="live_read_only",
            )
        except Exception as exc:  # noqa: BLE001
            live_error = str(exc)
            if not latest_stored:
                raise
    model_payload = _latest_stored_model_payload(settings, series, station)
    if not show_failures:
        model_payload["estimates"] = list(model_payload.get("estimates", []))
    return _simple_summary_from_model_payload(
        settings,
        store_obj,
        model_payload,
        show_prices=show_prices,
        show_edges=show_edges,
        top_n=top_n,
        source="latest_stored",
        live_error=live_error,
    )


def _simple_summary_text(
    payload: dict[str, Any],
    *,
    show_failures: bool,
    show_details: bool,
) -> str:
    lines = [
        f"{payload['station']} HIGH TEMP MODEL SUMMARY",
        f"Market date: {payload.get('market_date')}",
        f"As of: {_fmt_utc_minute(payload.get('asof_utc'))}",
        f"Observed high so far: {_fmt_f(payload.get('observed_high_so_far_f'))}",
        f"Latest observation: {_fmt_utc_minute(payload.get('latest_observation_utc'))}",
        f"Current production estimate: {_fmt_f(payload.get('current_production_estimate_f'))}",
        f"Consensus estimate: {_fmt_f(payload.get('consensus_estimate_f'))}",
        f"Model range: {_fmt_f_short(payload.get('model_min_estimate_f'))} - {_fmt_f_short(payload.get('model_max_estimate_f'))}",
        f"Most likely bracket: {payload.get('most_likely_bracket_by_current_model') or '--'}",
        (
            f"Data status: {payload.get('data_readiness_status')} - "
            f"{_readiness_message(payload.get('data_readiness_status'), int(payload.get('unique_joined_market_dates') or 0))}"
        ),
        "",
        "MODEL HIGH ESTIMATES",
        "Model                         Est high   Most likely bracket   P(top)   Status",
    ]
    for row in payload.get("models", []):
        status = row.get("status") or ""
        if show_failures and row.get("error"):
            status = f"{status}: {row['error']}"
        elif show_details and row.get("source"):
            status = f"{status} ({row['source']})"
        lines.append(
            f"{str(row.get('model')):<29} "
            f"{_fmt_f_short(row.get('estimated_high_f')):<10} "
            f"{str(row.get('most_likely_bracket') or '--'):<22} "
            f"{_fmt_percent(row.get('top_probability')):<8} {status}"
        )

    lines.extend(["", "PROBABILITIES BY MODEL"])
    brackets = payload.get("brackets", [])
    if brackets:
        header = f"{'Model':<24}" + "".join(f"{label:>10}" for label in brackets)
        lines.append(header)
        for row in payload.get("models", []):
            if not row.get("probabilities"):
                continue
            probs = row["probabilities"]
            line = f"{str(row.get('matrix_model')):<24}" + "".join(
                f"{_fmt_percent(probs.get(label)):>10}" for label in brackets
            )
            lines.append(line)
    else:
        lines.append("No probability rows are available.")

    lines.extend(
        [
            "",
            "MODEL AGREEMENT",
            f"Agreement: {payload.get('model_agreement_status')}",
            f"High estimate spread: {_fmt_f(payload.get('model_spread_f'))}",
            f"Interpretation: {_agreement_interpretation(str(payload.get('model_agreement_status')))}",
        ]
    )

    market_view = payload.get("market_view", {})
    if market_view.get("included"):
        lines.extend(
            [
                "",
                "OPTIONAL MARKET VIEW",
                f"Required hurdle: {market_view.get('required_hurdle')}",
                "Top apparent mismatches:",
                "Bracket    Side   Model probability   Market ask   Edge       Would trade   Note",
            ]
        )
        for row in market_view.get("top_mismatches", []):
            lines.append(
                f"{str(row.get('bracket')):<10} {str(row.get('side')):<6} "
                f"{_fmt_percent(row.get('model_probability')):<19} "
                f"{str(row.get('market_ask') or '--'):<12} "
                f"{str(row.get('edge') or '--'):<10} {str(row.get('would_trade')):<13} "
                f"{row.get('note') or ''}"
            )

    lines.extend(["", "WARNINGS"])
    lines.extend(f"- {warning}" for warning in payload.get("warnings", []))
    lines.extend(["", "NEXT ACTION", str(payload.get("next_action") or "")])
    return "\n".join(lines)


def _simple_summary_csv_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model in payload.get("models", []):
        probabilities = model.get("probabilities") or {}
        if not probabilities:
            rows.append(
                {
                    "model": model.get("model"),
                    "provider": model.get("provider"),
                    "model_id": model.get("model_id"),
                    "estimated_high_f": model.get("estimated_high_f"),
                    "status": model.get("status"),
                    "bracket": None,
                    "p_yes": None,
                    "p_yes_pct": None,
                    "most_likely_bracket": model.get("most_likely_bracket"),
                    "top_probability": model.get("top_probability"),
                    "error": model.get("error"),
                }
            )
            continue
        for bracket in payload.get("brackets", []):
            p_yes = probabilities.get(bracket)
            rows.append(
                {
                    "model": model.get("model"),
                    "provider": model.get("provider"),
                    "model_id": model.get("model_id"),
                    "estimated_high_f": model.get("estimated_high_f"),
                    "status": model.get("status"),
                    "bracket": bracket,
                    "p_yes": p_yes,
                    "p_yes_pct": _fmt_percent(p_yes),
                    "most_likely_bracket": model.get("most_likely_bracket"),
                    "top_probability": model.get("top_probability"),
                    "error": model.get("error"),
                }
            )
    return rows


def _csv_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue()


def _run_simple_summary(
    *,
    series: str | None,
    station: str | None,
    json_output: bool,
    csv_output: bool,
    output: str | None,
    latest_stored: bool,
    live: bool,
    show_prices: bool,
    show_edges: bool,
    show_failures: bool,
    show_details: bool,
    models: str | None,
    top_n: int | None,
    residual_sigma: float | None,
    store: bool,
) -> None:
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    try:
        payload = _simple_summary_payload(
            settings,
            series,
            station,
            models_raw=models,
            residual_sigma=residual_sigma,
            latest_stored=latest_stored,
            live=live,
            show_failures=show_failures,
            show_prices=show_prices,
            show_edges=show_edges,
            top_n=top_n,
            store_results=store,
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"Simple summary failed: {exc}")
        raise typer.Exit(1) from exc

    safe_payload = safe_console_payload(payload)
    csv_rows = _simple_summary_csv_rows(safe_payload)
    text = _simple_summary_text(safe_payload, show_failures=show_failures, show_details=show_details)

    if output:
        output_path = Path(output)
        suffix = output_path.suffix.lower()
        if suffix == ".csv" or csv_output:
            _write_csv(output_path, csv_rows)
        elif suffix == ".json" or json_output:
            write_json_report(output_path, safe_payload)
        else:
            write_text_report(output_path, text)

    if json_output:
        print(json.dumps(safe_payload, indent=2))
    elif csv_output:
        print(_csv_text(csv_rows), end="")
    else:
        console.print(text)


def _weather_summary_payload(settings: Settings, station: str) -> dict[str, Any]:
    weather, forecast = _weather_context(settings, station)
    model_rows = []
    for column, value in sorted(forecast.model_maxes_f.items()):
        model_id = column.split("__", 1)[1] if "__" in column else column
        model_rows.append({"model": model_id, "future_high_f": value})
    feature_summary = forecast.feature_summary or {}
    notes = {
        "low_cloud_max_pct": feature_summary.get("cloud_cover_low_max"),
        "shortwave_radiation_max": feature_summary.get("shortwave_radiation_max"),
        "wind_speed_10m_mean": feature_summary.get("wind_speed_10m_mean"),
    }
    return {
        "station": station,
        "observed_high_so_far_f": weather.observed_high_so_far_f,
        "latest_observation_utc": weather.latest_observation_utc,
        "current_production_estimate_f": weather.model_future_high_f,
        "open_meteo_models": model_rows,
        "successful_models": forecast.successful_models,
        "failed_models": forecast.failed_models,
        "fallback_used": forecast.fallback_used,
        "feature_notes": notes,
        "status": "ok" if weather.model_future_high_f is not None else "unavailable",
    }


def _weather_summary_text(payload: dict[str, Any]) -> str:
    lines = [
        f"WEATHER SUMMARY - {payload['station']}",
        f"Observed high so far: {_fmt_f(payload.get('observed_high_so_far_f'))}",
        f"Latest observation: {_fmt_utc_minute(payload.get('latest_observation_utc'))}",
        f"Current production estimate: {_fmt_f(payload.get('current_production_estimate_f'))}",
        "Open-Meteo models:",
    ]
    if payload.get("open_meteo_models"):
        for row in payload["open_meteo_models"]:
            lines.append(f"  {row['model']}: {_fmt_f(row.get('future_high_f'))}")
    else:
        lines.append("  none")
    notes = payload.get("feature_notes", {})
    lines.extend(
        [
            "Feature notes:",
            f"  Low cloud max: {notes.get('low_cloud_max_pct') if notes.get('low_cloud_max_pct') is not None else 'n/a'}%",
            f"  Shortwave radiation max: {notes.get('shortwave_radiation_max') if notes.get('shortwave_radiation_max') is not None else 'n/a'}",
            f"  Wind mean: {notes.get('wind_speed_10m_mean') if notes.get('wind_speed_10m_mean') is not None else 'n/a'} mph",
        ]
    )
    if payload.get("failed_models"):
        failures = ", ".join(f"{key}: {value}" for key, value in payload["failed_models"].items())
        lines.append(f"Model failures: {failures}")
    lines.append(f"Fallback used: {payload.get('fallback_used')}")
    lines.append(f"Status: {payload.get('status')}")
    return "\n".join(lines)


def _collect_iteration_summary(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("status") != "ok":
        return {
            "iteration": row.get("iteration"),
            "time_utc": _fmt_time_utc(utc_now()),
            "latest_actual_temp_f": None,
            "observed_high_so_far_f": None,
            "latest_observation_utc": None,
            "current_estimate_f": None,
            "top_bracket": "--",
            "stored_predictions": 0,
            "market_count": 0,
            "status": "error",
            "error": row.get("error"),
            "successful_models": [],
            "failed_models": {},
            "model_estimates": {},
        }
    result = row.get("result", {})
    weather = result.get("weather")
    observed = getattr(weather, "observed_high_so_far_f", None)
    estimate = getattr(weather, "model_future_high_f", None)
    timestamp = getattr(weather, "timestamp_utc", None)
    latest = getattr(weather, "latest_observation_utc", None)
    open_meteo = result.get("open_meteo", {}) or {}
    return {
        "iteration": row.get("iteration"),
        "time_utc": _fmt_time_utc(timestamp or utc_now()),
        "latest_actual_temp_f": result.get("latest_observed_temp_f"),
        "observed_high_so_far_f": observed,
        "latest_observation_utc": _fmt_time_utc(latest) if latest else "n/a",
        "current_estimate_f": estimate,
        "top_bracket": _estimate_bracket_label(estimate),
        "stored_predictions": int(result.get("stored_predictions") or 0),
        "market_count": int(result.get("market_count") or 0),
        "status": "ok",
        "error": None,
        "successful_models": open_meteo.get("successful_models") or [],
        "failed_models": open_meteo.get("failed_models") or {},
        "model_estimates": open_meteo.get("model_maxes_f") or {},
    }


def _model_estimates_inline(model_estimates: dict[str, Any]) -> str:
    if not model_estimates:
        return "models n/a"
    parts = []
    for key, value in sorted(model_estimates.items()):
        model = str(key).replace("temperature_2m__", "")
        parts.append(f"{model} {_fmt_f_short(value)}")
    return ", ".join(parts)


def _collect_loop_line(row: dict[str, Any]) -> str:
    status = row.get("status")
    if row.get("error"):
        status = f"error: {row['error']}"
    return (
        f"iter {row.get('iteration')} | {row.get('time_utc')} UTC | "
        f"actual {_fmt_f_short(row.get('latest_actual_temp_f'))} | "
        f"obs high {_fmt_f_short(row.get('observed_high_so_far_f'))} "
        f"(latest obs {row.get('latest_observation_utc')}) | "
        f"estimate {_fmt_f_short(row.get('current_estimate_f'))} | "
        f"top {row.get('top_bracket')} | "
        f"{_model_estimates_inline(row.get('model_estimates') or {})} | "
        f"stored {row.get('stored_predictions')} preds | {status}"
    )


def _collect_session_summary_payload(
    payload: dict[str, Any],
    *,
    interval_seconds: int,
    duration_minutes: float | None,
    max_iterations: int | None,
) -> dict[str, Any]:
    rows = [_collect_iteration_summary(row) for row in payload.get("results", [])]
    successful_models = sorted({model for row in rows for model in row.get("successful_models", [])})
    failed_models: dict[str, str] = {}
    for row in rows:
        failed_models.update(row.get("failed_models") or {})
    return {
        "series": payload.get("series"),
        "station": payload.get("station"),
        "duration_minutes": duration_minutes,
        "interval_seconds": interval_seconds,
        "max_iterations": max_iterations,
        "iterations": payload.get("iterations"),
        "paper_trading": False,
        "live_trading_enabled": payload.get("live_trading_enabled"),
        "include_model_estimates": payload.get("include_model_estimates"),
        "report_dir": payload.get("report_dir"),
        "rows": rows,
        "stored_predictions": sum(int(row.get("stored_predictions") or 0) for row in rows),
        "models_successful": successful_models,
        "model_failures": failed_models,
        "error_count": sum(1 for row in rows if row.get("status") != "ok"),
    }


def _collect_session_summary_text(payload: dict[str, Any]) -> str:
    duration = payload.get("duration_minutes")
    lines = [
        f"COLLECT SESSION - {payload.get('series')} / {payload.get('station')}",
        f"Duration: {duration if duration is not None else 'bounded by max iterations'} min",
        f"Interval: {payload.get('interval_seconds')} sec",
        f"Paper trading: {str(payload.get('paper_trading')).lower()}",
        f"Live trading: {str(bool(payload.get('live_trading_enabled'))).lower()}",
        "",
        "Iter  Time UTC   Obs high   Current est   Top bracket   Stored preds   Status",
    ]
    for row in payload.get("rows", []):
        status = row.get("status")
        if row.get("error"):
            status = f"error: {row['error']}"
        lines.append(
            f"{str(row.get('iteration')):<5} {str(row.get('time_utc')):<10} "
            f"{_fmt_f_short(row.get('observed_high_so_far_f')):<10} "
            f"{_fmt_f_short(row.get('current_estimate_f')):<13} "
            f"{str(row.get('top_bracket')):<13} "
            f"{str(row.get('stored_predictions')):<14} {status}"
        )
    failures = payload.get("model_failures") or {}
    failure_text = "none" if not failures else ", ".join(f"{key}: {value}" for key, value in failures.items())
    lines.extend(
        [
            "",
            "Summary:",
            f"Stored predictions: {payload.get('stored_predictions')}",
            f"Models successful: {', '.join(payload.get('models_successful') or []) or 'none'}",
            f"Model failures: {failure_text}",
            f"Report dir: {payload.get('report_dir')}",
        ]
    )
    return "\n".join(lines)


def _history_dates(start_date: str, end_date: str) -> list[date]:
    return _date_range(date.fromisoformat(start_date), date.fromisoformat(end_date))


def _dedupe_market_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("market_ticker") or "")
        if not ticker:
            continue
        existing = by_ticker.get(ticker)
        if existing is None or existing.get("source_tier") == "explicit":
            by_ticker[ticker] = row
    return sorted(
        by_ticker.values(),
        key=lambda row: (
            str(row.get("market_date") or ""),
            _bracket_sort_key(
                {
                    "bracket_lower_f": row.get("bracket_lower_f"),
                    "bracket_upper_f": row.get("bracket_upper_f"),
                    "bracket_label": row.get("bracket_label"),
                }
            ),
            str(row.get("market_ticker") or ""),
        ),
    )


def _market_rows_from_stored_snapshots(
    store_obj: SQLiteStore,
    series: str,
    dates: set[str],
) -> list[dict[str, Any]]:
    rows = store_obj.conn.execute(
        "SELECT payload_json FROM market_snapshots WHERE series = ? ORDER BY id DESC",
        (series,),
    ).fetchall()
    discovered: list[dict[str, Any]] = []
    for db_row in rows:
        try:
            payload = json.loads(db_row["payload_json"])
        except Exception:  # noqa: BLE001
            continue
        markets = payload.get("markets") if isinstance(payload, dict) else payload
        if not isinstance(markets, list):
            continue
        for market in markets:
            parsed_date = market_date_from_market(market)
            if parsed_date and parsed_date.isoformat() in dates:
                discovered.append(discover_market_row(market, series=series, source_tier="stored"))
    return discovered


def _explicit_market_rows(series: str, tickers_raw: str | None) -> list[dict[str, Any]]:
    rows = []
    for ticker in _split_csv_option(tickers_raw, []):
        market = {"ticker": ticker, "title": ticker}
        rows.append(discover_market_row(market, series=series, source_tier="explicit"))
    return rows


def _kalshi_history_discover_payload(
    settings: Settings,
    *,
    series: str,
    start_date: str,
    end_date: str,
    include_live: bool,
    include_historical: bool,
    tickers: str | None = None,
    from_stored_markets: bool = False,
    max_markets: int | None = None,
) -> dict[str, Any]:
    client = _kalshi(settings)
    store_obj = _store(settings)
    dates = {day.isoformat() for day in _history_dates(start_date, end_date)}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    rows.extend(_explicit_market_rows(series, tickers))

    if include_live and not tickers:
        for status in ["open", "closed", "settled"]:
            try:
                for market in client.get_markets(series, status=status):
                    parsed_date = market_date_from_market(market)
                    if parsed_date and parsed_date.isoformat() in dates:
                        rows.append(discover_market_row(market, series=series, source_tier="live"))
            except Exception as exc:  # noqa: BLE001
                errors.append({"source_tier": "live", "status": status, "error": str(exc)})

    if include_historical:
        try:
            historical = client.get_historical_markets(
                series_ticker=series,
                tickers=_split_csv_option(tickers, []) or None,
                limit=200,
            )
            for market in historical.get("markets", []):
                parsed_date = market_date_from_market(market)
                if tickers or (parsed_date and parsed_date.isoformat() in dates):
                    rows.append(discover_market_row(market, series=series, source_tier="historical"))
        except Exception as exc:  # noqa: BLE001
            errors.append({"source_tier": "historical", "error": str(exc)})

    if from_stored_markets:
        rows.extend(_market_rows_from_stored_snapshots(store_obj, series, dates))

    rows = _dedupe_market_rows(rows)
    if max_markets is not None:
        rows = rows[:max_markets]
    return {
        "series": series,
        "start_date": start_date,
        "end_date": end_date,
        "market_count": len(rows),
        "markets": rows,
        "errors": errors,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _kalshi_history_backfill_payload(
    settings: Settings,
    *,
    series: str,
    start_date: str,
    end_date: str,
    period_interval: int,
    include_live: bool,
    include_historical: bool,
    tickers: str | None,
    from_stored_markets: bool,
    max_markets: int | None,
    dry_run: bool,
    store_rows: bool,
) -> dict[str, Any]:
    discover = _kalshi_history_discover_payload(
        settings,
        series=series,
        start_date=start_date,
        end_date=end_date,
        include_live=include_live,
        include_historical=include_historical,
        tickers=tickers,
        from_stored_markets=from_stored_markets,
        max_markets=max_markets,
    )
    store_obj = _store(settings)
    client = _kalshi(settings)
    failures: list[dict[str, Any]] = []
    stored_ids: list[int] = []
    fetched_count = 0
    market_results = []
    for market in discover["markets"]:
        market_date_text = market.get("market_date") or start_date
        try:
            start_utc, end_utc = market_window_for_date(date.fromisoformat(market_date_text))
        except ValueError:
            start_utc, end_utc = market_window_for_date(date.fromisoformat(start_date))
        start_ts = ts_seconds(start_utc)
        end_ts = ts_seconds(end_utc)
        market_payload = {
            "ticker": market["market_ticker"],
            "event_ticker": market.get("event_ticker"),
            "title": market.get("bracket_label") or market["market_ticker"],
            "subtitle": market.get("bracket_label") or market["market_ticker"],
            "status": market.get("status"),
            "result": market.get("result"),
        }
        candle_rows = []
        source_used = None
        fetch_errors = []
        sources_to_try = []
        if include_live and market.get("source_tier") in {"live", "explicit", "stored"}:
            sources_to_try.append("live")
        if include_historical:
            sources_to_try.append("historical")
        if include_live and "live" not in sources_to_try:
            sources_to_try.append("live")
        for source_tier in sources_to_try:
            try:
                if source_tier == "historical":
                    response = client.get_historical_market_candlesticks(
                        market["market_ticker"],
                        start_ts=start_ts,
                        end_ts=end_ts,
                        period_interval=period_interval,
                    )
                else:
                    response = client.get_market_candlesticks(
                        series,
                        market["market_ticker"],
                        start_ts=start_ts,
                        end_ts=end_ts,
                        period_interval=period_interval,
                    )
                candle_rows = normalize_candlestick_response(
                    response,
                    series=series,
                    market=market_payload,
                    period_interval=period_interval,
                    source_tier=source_tier,
                )
                source_used = source_tier
                if candle_rows:
                    break
            except Exception as exc:  # noqa: BLE001
                fetch_errors.append({"source_tier": source_tier, "error": str(exc)})
        fetched_count += len(candle_rows)
        if candle_rows and store_rows and not dry_run:
            stored_ids.extend(store_obj.save_kalshi_candlesticks(candle_rows))
        if fetch_errors and not candle_rows:
            failures.append({"market_ticker": market["market_ticker"], "errors": fetch_errors})
        market_results.append(
            {
                "market_ticker": market["market_ticker"],
                "market_date": market_date_text,
                "source_used": source_used,
                "candles_fetched": len(candle_rows),
                "stored": bool(candle_rows and store_rows and not dry_run),
                "errors": fetch_errors,
            }
        )
    return {
        "series": series,
        "start_date": start_date,
        "end_date": end_date,
        "period_interval": period_interval,
        "dry_run": dry_run,
        "store": bool(store_rows and not dry_run),
        "markets_found": discover["market_count"],
        "markets_fetched": sum(1 for row in market_results if row["candles_fetched"] > 0),
        "candles_fetched": fetched_count,
        "candles_stored": len(stored_ids),
        "stored_ids": stored_ids,
        "failures": failures,
        "discovery_errors": discover["errors"],
        "markets": market_results,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _trend_inputs(
    settings: Settings,
    *,
    series: str,
    station: str,
    market_date_text: str,
    period_interval: int,
    backfill_if_missing: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    store_obj = _store(settings)
    candles = store_obj.load_kalshi_candlesticks(
        series=series,
        market_date=market_date_text,
        period_interval=period_interval,
    )
    backfill = {}
    if not candles and backfill_if_missing:
        backfill = _kalshi_history_backfill_payload(
            settings,
            series=series,
            start_date=market_date_text,
            end_date=market_date_text,
            period_interval=period_interval,
            include_live=True,
            include_historical=True,
            tickers=None,
            from_stored_markets=True,
            max_markets=None,
            dry_run=False,
            store_rows=True,
        )
        candles = store_obj.load_kalshi_candlesticks(
            series=series,
            market_date=market_date_text,
            period_interval=period_interval,
        )
    predictions = store_obj.load_predictions(
        station=station,
        start_date=market_date_text,
        end_date=market_date_text,
    )
    rows = trend_rows_from_candles(candles, predictions)
    hurdle = settings.min_edge + settings.fee_buffer + settings.model_error_buffer
    rows = enrich_trend_rows_with_hurdle(rows, hurdle)
    return candles, rows, backfill


def _kalshi_trends_payload(
    settings: Settings,
    *,
    series: str,
    station: str,
    market_date_text: str,
    period_interval: int,
    backfill_if_missing: bool,
) -> dict[str, Any]:
    store_obj = _store(settings)
    candles, rows, backfill = _trend_inputs(
        settings,
        series=series,
        station=station,
        market_date_text=market_date_text,
        period_interval=period_interval,
        backfill_if_missing=backfill_if_missing,
    )
    outcomes = store_obj.load_official_outcomes(station=station, start_date=market_date_text, end_date=market_date_text)
    summary = trend_summary(
        candles,
        rows,
        series=series,
        station=station,
        market_date=market_date_text,
        official_outcome=outcomes[0] if outcomes else None,
    )
    return {
        "summary": summary,
        "trend_rows": rows,
        "backfill": backfill,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }


def _save_trend_artifacts(
    store_obj: SQLiteStore,
    *,
    series: str,
    station: str,
    market_date_text: str,
    manifest: dict[str, Any],
) -> None:
    for artifact_type, path in (manifest.get("artifacts") or {}).items():
        store_obj.save_trend_artifact(
            series,
            station,
            market_date_text,
            artifact_type,
            path,
            {"manifest": manifest},
        )
    manifest_path = Path(manifest.get("output_dir", ".")) / "chart_manifest.json"
    store_obj.save_trend_artifact(
        series,
        station,
        market_date_text,
        "chart_manifest",
        manifest_path,
        manifest,
    )


def _history_discover_text(payload: dict[str, Any]) -> str:
    lines = [
        f"KALSHI HISTORY DISCOVERY - {payload['series']}",
        f"Date range: {payload['start_date']} to {payload['end_date']}",
        f"Markets found: {payload['market_count']}",
        "",
        "Date         Ticker                         Bracket                 Source       Status",
    ]
    for row in payload.get("markets", []):
        lines.append(
            f"{str(row.get('market_date') or ''):<12} {str(row.get('market_ticker')):<30} "
            f"{str(row.get('bracket_label') or ''):<23} {str(row.get('source_tier')):<12} "
            f"{str(row.get('status') or '')}"
        )
    if payload.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in payload["errors"])
    return "\n".join(lines)


def _history_backfill_text(payload: dict[str, Any]) -> str:
    lines = [
        f"KALSHI HISTORY BACKFILL - {payload['series']}",
        f"Date range: {payload['start_date']} to {payload['end_date']}",
        f"Period interval: {payload['period_interval']} minute",
        f"Dry run: {payload['dry_run']}",
        f"Markets found: {payload['markets_found']}",
        f"Markets fetched: {payload['markets_fetched']}",
        f"Candles fetched: {payload['candles_fetched']}",
        f"Candles stored: {payload['candles_stored']}",
        f"Failures: {len(payload['failures'])}",
    ]
    if payload.get("failures"):
        lines.append("")
        lines.append("Failures:")
        for failure in payload["failures"]:
            lines.append(f"- {failure['market_ticker']}: {failure['errors']}")
    return "\n".join(lines)


def _microtrade_text(payload: dict[str, Any]) -> str:
    replay = payload["replay"]
    lines = [
        "MICROTRADE TREND REPLAY",
        replay["label"],
        "",
        f"Simulated entries: {replay['simulated_entries']}",
        f"Simulated exits: {replay['simulated_exits']}",
        f"Open positions: {len(replay['open_positions'])}",
        f"Realized fake P&L: {replay['realized_fake_pnl']:.4f}",
        f"Wins: {replay['win_count']}",
        f"Losses: {replay['loss_count']}",
        f"Average hold minutes: {replay['average_hold_minutes']}",
    ]
    if payload.get("chart_path"):
        lines.append(f"Chart: {payload['chart_path']}")
    return "\n".join(lines)


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


@app.command("weather-summary")
def weather_summary(
    station: str | None = typer.Option(None, help="Weather station"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Show a concise weather-only summary without raw Open-Meteo diagnostics."""
    settings = load_settings()
    station = station or settings.default_station
    try:
        payload = _weather_summary_payload(settings, station)
    except Exception as exc:  # noqa: BLE001
        console.print(f"Weather summary failed: {exc}")
        raise typer.Exit(1) from exc
    _emit_report(payload, json_output=json_output, output=output, text=_weather_summary_text(payload))


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
                    console.print(_record_snapshot_text(iteration, payload, snapshot_style="table"))
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
def collect_once(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    include_model_estimates: bool = typer.Option(False, "--include-model-estimates"),
) -> None:
    """Collect read-only inputs and store predictions without paper trading."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
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
        if include_model_estimates:
            comparison = _model_estimates_payload(
                settings,
                series,
                station,
                include_probabilities=True,
                show_failures=True,
                store_results=True,
            )
            result["model_estimates"] = {
                "stored_estimate_count": len(comparison["stored_estimate_ids"]),
                "stored_probability_count": len(comparison["stored_probability_ids"]),
                "estimate_count": len(comparison["estimates"]),
            }
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
    include_model_estimates: bool = typer.Option(False, "--include-model-estimates"),
    debug_json: bool = typer.Option(False, "--debug-json"),
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
            if include_model_estimates:
                comparison = _model_estimates_payload(
                    settings,
                    series or settings.default_series,
                    station or settings.default_station,
                    include_probabilities=True,
                    show_failures=True,
                    store_results=True,
                )
                result["model_estimates"] = {
                    "stored_estimate_count": len(comparison["stored_estimate_ids"]),
                    "stored_probability_count": len(comparison["stored_probability_ids"]),
                    "estimate_count": len(comparison["estimates"]),
                }
            if debug_json:
                console.print({"iteration": iteration, **result})
            else:
                summary = _collect_iteration_summary({"iteration": iteration, "status": "ok", "result": result})
                console.print(_collect_loop_line(summary))
        except Exception as exc:  # noqa: BLE001
            if debug_json:
                console.print(f"Collect loop error iteration={iteration}: {exc}")
            else:
                summary = _collect_iteration_summary({"iteration": iteration, "status": "error", "error": str(exc)})
                console.print(_collect_loop_line(summary))
        if max_iterations is not None and iteration >= max_iterations:
            break
        time.sleep(interval_seconds)


@app.command("simple-summary")
def simple_summary(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
    latest_stored: bool = typer.Option(False, "--latest-stored"),
    live: bool = typer.Option(True, "--live/--no-live"),
    show_prices: bool = typer.Option(False, "--show-prices"),
    show_edges: bool = typer.Option(False, "--show-edges"),
    show_failures: bool = typer.Option(False, "--show-failures"),
    show_details: bool = typer.Option(False, "--show-details"),
    models: str | None = typer.Option(None, "--models"),
    top_n: int | None = typer.Option(5, "--top-n"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    store: bool = typer.Option(False, "--store"),
) -> None:
    """Print a concise, analysis-only model and probability summary."""
    _run_simple_summary(
        series=series,
        station=station,
        json_output=json_output,
        csv_output=csv_output,
        output=output,
        latest_stored=latest_stored,
        live=live,
        show_prices=show_prices,
        show_edges=show_edges,
        show_failures=show_failures,
        show_details=show_details,
        models=models,
        top_n=top_n,
        residual_sigma=residual_sigma,
        store=store,
    )


@app.command("model-summary")
def model_summary(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
    latest_stored: bool = typer.Option(False, "--latest-stored"),
    live: bool = typer.Option(True, "--live/--no-live"),
    show_prices: bool = typer.Option(False, "--show-prices"),
    show_edges: bool = typer.Option(False, "--show-edges"),
    show_failures: bool = typer.Option(False, "--show-failures"),
    show_details: bool = typer.Option(False, "--show-details"),
    models: str | None = typer.Option(None, "--models"),
    top_n: int | None = typer.Option(5, "--top-n"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    store: bool = typer.Option(False, "--store"),
) -> None:
    """Alias for simple-summary."""
    _run_simple_summary(
        series=series,
        station=station,
        json_output=json_output,
        csv_output=csv_output,
        output=output,
        latest_stored=latest_stored,
        live=live,
        show_prices=show_prices,
        show_edges=show_edges,
        show_failures=show_failures,
        show_details=show_details,
        models=models,
        top_n=top_n,
        residual_sigma=residual_sigma,
        store=store,
    )


@app.command("kalshi-history-discover")
def kalshi_history_discover(
    series: str | None = typer.Option(None),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    include_live: bool = typer.Option(True, "--include-live/--no-include-live"),
    include_historical: bool = typer.Option(True, "--include-historical/--no-include-historical"),
    tickers: str | None = typer.Option(None, "--tickers"),
    from_stored_markets: bool = typer.Option(False, "--from-stored-markets"),
    max_markets: int | None = typer.Option(None, "--max-markets"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Discover LA high-temperature markets for history backfill. Read-only."""
    settings = load_settings()
    series = series or settings.default_series
    payload = _kalshi_history_discover_payload(
        settings,
        series=series,
        start_date=start_date,
        end_date=end_date,
        include_live=include_live,
        include_historical=include_historical,
        tickers=tickers,
        from_stored_markets=from_stored_markets,
        max_markets=max_markets,
    )
    _emit_report(payload, json_output=json_output, output=output, text=_history_discover_text(payload))


@app.command("kalshi-history-backfill")
def kalshi_history_backfill(
    series: str | None = typer.Option(None),
    start_date: str = typer.Option(..., "--start-date"),
    end_date: str = typer.Option(..., "--end-date"),
    period_interval: int = typer.Option(1, "--period-interval"),
    include_live: bool = typer.Option(True, "--include-live/--no-include-live"),
    include_historical: bool = typer.Option(True, "--include-historical/--no-include-historical"),
    tickers: str | None = typer.Option(None, "--tickers"),
    from_stored_markets: bool = typer.Option(False, "--from-stored-markets"),
    max_markets: int | None = typer.Option(None, "--max-markets"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    store: bool = typer.Option(True, "--store/--no-store"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Backfill Kalshi market candlesticks. Read-only; stores history rows only."""
    settings = load_settings()
    series = series or settings.default_series
    payload = _kalshi_history_backfill_payload(
        settings,
        series=series,
        start_date=start_date,
        end_date=end_date,
        period_interval=period_interval,
        include_live=include_live,
        include_historical=include_historical,
        tickers=tickers,
        from_stored_markets=from_stored_markets,
        max_markets=max_markets,
        dry_run=dry_run,
        store_rows=store,
    )
    _emit_report(payload, json_output=json_output, output=output, text=_history_backfill_text(payload))


@app.command("kalshi-trends")
def kalshi_trends(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    market_date_text: str = typer.Option(..., "--date"),
    period_interval: int = typer.Option(1, "--period-interval"),
    from_db: bool = typer.Option(True, "--from-db/--no-from-db"),
    backfill_if_missing: bool = typer.Option(False, "--backfill-if-missing"),
    show_model: bool = typer.Option(False, "--show-model"),
    show_volume: bool = typer.Option(False, "--show-volume"),
    top_n: int | None = typer.Option(None, "--top-n"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Show a concise trend table from stored Kalshi candlesticks."""
    del from_db, show_model, show_volume, top_n
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    payload = _kalshi_trends_payload(
        settings,
        series=series,
        station=station,
        market_date_text=market_date_text,
        period_interval=period_interval,
        backfill_if_missing=backfill_if_missing,
    )
    text = trend_summary_text(payload["summary"])
    if output and output.lower().endswith(".csv"):
        _write_csv(Path(output), payload["trend_rows"])
    elif output:
        _emit_report(payload, json_output=json_output, output=output, text=text)
        return
    if csv_output:
        print(_csv_text(safe_console_payload(payload["trend_rows"])), end="")
    else:
        _emit_report(payload, json_output=json_output, text=text)


@app.command("kalshi-trend-chart")
def kalshi_trend_chart(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    market_date_text: str = typer.Option(..., "--date"),
    period_interval: int = typer.Option(1, "--period-interval"),
    from_db: bool = typer.Option(True, "--from-db/--no-from-db"),
    backfill_if_missing: bool = typer.Option(False, "--backfill-if-missing"),
    output_dir: str = typer.Option("reports/kalshi_trends", "--output-dir"),
    image_format: str = typer.Option("png", "--format"),
    include_model: bool = typer.Option(True, "--include-model/--no-include-model"),
    include_edge: bool = typer.Option(True, "--include-edge/--no-include-edge"),
    include_volume: bool = typer.Option(True, "--include-volume/--no-include-volume"),
    include_observed_high: bool = typer.Option(True, "--include-observed-high/--no-include-observed-high"),
    json_output: bool = typer.Option(False, "--json"),
    open_after: bool = typer.Option(False, "--open-after/--no-open-after"),
) -> None:
    """Generate PNG trend charts from stored Kalshi candlesticks."""
    del from_db, image_format, open_after
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    payload = _kalshi_trends_payload(
        settings,
        series=series,
        station=station,
        market_date_text=market_date_text,
        period_interval=period_interval,
        backfill_if_missing=backfill_if_missing,
    )
    chart_dir = Path(output_dir) / market_date_text
    manifest = generate_trend_charts(
        output_dir=chart_dir,
        series=series,
        station=station,
        market_date=market_date_text,
        candles=_store(settings).load_kalshi_candlesticks(
            series=series, market_date=market_date_text, period_interval=period_interval
        ),
        trend_rows=payload["trend_rows"],
        summary=payload["summary"],
        include_model=include_model,
        include_edge=include_edge,
        include_volume=include_volume,
        include_observed_high=include_observed_high,
    )
    _save_trend_artifacts(
        _store(settings),
        series=series,
        station=station,
        market_date_text=market_date_text,
        manifest=manifest,
    )
    text = f"Generated Kalshi trend charts in {chart_dir}\n" + "\n".join(
        f"- {name}: {path}" for name, path in manifest.get("artifacts", {}).items()
    )
    _emit_report(manifest, json_output=json_output, text=text)


@app.command("kalshi-trend-dashboard")
def kalshi_trend_dashboard(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    market_date_text: str = typer.Option(..., "--date"),
    output_dir: str = typer.Option("reports/kalshi_trends", "--output-dir"),
    backfill_if_missing: bool = typer.Option(False, "--backfill-if-missing"),
    period_interval: int = typer.Option(1, "--period-interval"),
) -> None:
    """Generate a static HTML dashboard for Kalshi trend charts."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    payload = _kalshi_trends_payload(
        settings,
        series=series,
        station=station,
        market_date_text=market_date_text,
        period_interval=period_interval,
        backfill_if_missing=backfill_if_missing,
    )
    chart_dir = Path(output_dir) / market_date_text
    manifest = generate_trend_charts(
        output_dir=chart_dir,
        series=series,
        station=station,
        market_date=market_date_text,
        candles=_store(settings).load_kalshi_candlesticks(
            series=series, market_date=market_date_text, period_interval=period_interval
        ),
        trend_rows=payload["trend_rows"],
        summary=payload["summary"],
    )
    dashboard_path = write_dashboard(chart_dir, summary=payload["summary"], chart_manifest=manifest)
    store_obj = _store(settings)
    _save_trend_artifacts(
        store_obj,
        series=series,
        station=station,
        market_date_text=market_date_text,
        manifest=manifest,
    )
    store_obj.save_trend_artifact(
        series,
        station,
        market_date_text,
        "dashboard",
        dashboard_path,
        {"manifest": manifest},
    )
    console.print(f"Generated Kalshi trend dashboard: {dashboard_path}")


@app.command("temperature-estimate-chart")
def temperature_estimate_chart(
    station: str | None = typer.Option(None),
    market_date_text: str = typer.Option(..., "--date"),
    output_dir: str = typer.Option("reports/temperature_estimates", "--output-dir"),
    image_format: str = typer.Option("png", "--format"),
    fetch_actual: bool = typer.Option(True, "--fetch-actual/--no-fetch-actual"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Graph actual KLAX temperature against stored model temperature estimates."""
    settings = load_settings()
    station = station or settings.default_station
    market_date = date.fromisoformat(market_date_text)
    store_obj = _store(settings)
    start_utc, end_utc = lax_climate_day_utc(market_date)
    actual_observations = None
    actual_fetch_error = None
    if fetch_actual:
        try:
            actual_observations = _nws(settings).station_observations(
                station,
                start_utc,
                end_utc,
                limit=500,
            )
        except Exception as exc:  # noqa: BLE001
            actual_fetch_error = str(exc)

    payload = build_temperature_estimate_payload(
        store=store_obj,
        station=station,
        market_date=market_date,
        actual_observations=actual_observations,
    )
    if actual_fetch_error:
        payload["actual_fetch_error"] = actual_fetch_error
    chart_dir = Path(output_dir) / market_date_text
    artifacts = write_temperature_estimate_artifacts(payload, chart_dir, image_format=image_format)
    store_obj.save_trend_artifact(
        settings.default_series,
        station,
        market_date_text,
        "temperature_estimate_chart",
        artifacts["chart"],
        {"artifacts": artifacts, "actual_fetch_error": actual_fetch_error},
    )
    text = temperature_estimate_summary_text(payload)
    if actual_fetch_error:
        text += f"\nActual observation fetch warning: {actual_fetch_error}\n"
    text += "\nGenerated artifacts:\n" + "\n".join(f"- {name}: {path}" for name, path in artifacts.items())
    _emit_report({"payload": payload, "artifacts": artifacts}, json_output=json_output, text=text)


@app.command("microtrade-trend-replay")
def microtrade_trend_replay(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    market_date_text: str = typer.Option(..., "--date"),
    period_interval: int = typer.Option(1, "--period-interval"),
    entry_edge: float = typer.Option(0.09, "--entry-edge"),
    profit_target: float = typer.Option(0.10, "--profit-target"),
    stop_loss: float = typer.Option(0.05, "--stop-loss"),
    max_hold_minutes: int = typer.Option(60, "--max-hold-minutes"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    chart: bool = typer.Option(False, "--chart"),
) -> None:
    """Approximate candle-based microtrade replay. Fake-money analysis only."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    payload = _kalshi_trends_payload(
        settings,
        series=series,
        station=station,
        market_date_text=market_date_text,
        period_interval=period_interval,
        backfill_if_missing=False,
    )
    replay = microtrade_replay(
        payload["trend_rows"],
        entry_edge=entry_edge,
        profit_target=profit_target,
        stop_loss=stop_loss,
        max_hold_minutes=max_hold_minutes,
    )
    chart_path = None
    if chart:
        chart_path = write_microtrade_chart(Path("reports/kalshi_trends") / market_date_text, payload["trend_rows"])
    report = {
        "series": series,
        "station": station,
        "market_date": market_date_text,
        "replay": replay,
        "chart_path": chart_path,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "paper_trading": False,
    }
    _emit_report(report, json_output=json_output, output=output, text=_microtrade_text(report))


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
            "reason": "market date is not settlement-eligible yet",
            "settlement_buffer_hours": buffer_hours,
            "latest_settled_market_date": latest_settled_lax_market_date(
                settlement_buffer_hours=buffer_hours
            ).isoformat(),
            "next_commands": [
                f"kalshi-weather fetch-missing-outcomes --station {station}",
                f"kalshi-weather record-outcome --station {station} --date {outcome_date} --official-high-f NN --source manual --allow-unsettled-store",
            ],
        }
        _emit_report(payload, json_output=json_output, output=output)
        return
    try:
        outcome = NWSClimateProductClient(settings.user_agent, settings.nws_api_base_url).fetch_daily_high(
            station, market_date
        )
    except OutcomeUnavailableError as exc:
        payload = {
            "date": outcome_date,
            "status": "unavailable",
            "error": str(exc),
            "next_commands": [
                f"kalshi-weather record-outcome --station {station} --date {outcome_date} --official-high-f NN --source manual"
            ],
        }
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
    skipped_unsettled_dates = []
    for date_text in store_obj.distinct_prediction_dates(station):
        market_date = date.fromisoformat(date_text)
        if (
            not include_current
            and not allow_unsettled_store
            and market_date > latest_settled
        ):
            skipped_unsettled += 1
            skipped_unsettled_dates.append(date_text)
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
    report["skipped_unsettled_dates"] = sorted(
        set(report.get("skipped_unsettled_dates", []) + skipped_unsettled_dates)
    )
    if not dates and skipped_unsettled_dates:
        report["explanation"] = (
            "Prediction dates exist, but they were skipped because they are not settlement-eligible yet."
        )
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
    parsed_not_stored_dates = []
    stored_dates = []
    failed_dates = []
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
                stored_dates.append(outcome_date.isoformat())
            else:
                parsed_not_stored_dates.append(outcome_date.isoformat())
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
            failed_dates.append(outcome_date.isoformat())
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
    payload = {
        "attempted_dates": [row["date"] for row in rows],
        "parsed_not_stored_dates": parsed_not_stored_dates,
        "stored_dates": stored_dates,
        "failed_dates": failed_dates,
        "next_commands": [
            f"kalshi-weather fetch-missing-outcomes --station {station}",
            f"kalshi-weather join-outcomes --station {station} --overwrite",
        ],
        "rows": rows,
    }
    if output and output.lower().endswith(".csv"):
        _write_csv(Path(output), rows)
    elif output:
        write_json_report(output, payload)
    if json_output:
        console.print(json.dumps(safe_console_payload(payload), indent=2))
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
    include_model_estimates: bool = typer.Option(False, "--include-model-estimates"),
    verbose: bool = typer.Option(False, "--verbose"),
    debug_json: bool = typer.Option(False, "--debug-json"),
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
            if include_model_estimates:
                comparison = _model_estimates_payload(
                    settings,
                    series,
                    station,
                    include_probabilities=True,
                    show_failures=True,
                    store_results=True,
                )
                result["model_estimates"] = {
                    "stored_estimate_count": len(comparison["stored_estimate_ids"]),
                    "stored_probability_count": len(comparison["stored_probability_ids"]),
                    "estimate_count": len(comparison["estimates"]),
                }
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
        "include_model_estimates": include_model_estimates,
        "report_dir": str(report_dir),
        "results": results,
    }
    write_json_report(report_dir / "summary.json", payload)
    if debug_json:
        _emit_report(payload, json_output=True, output=output)
        return
    if verbose:
        _emit_report(payload, json_output=json_output, output=output)
        return
    summary = _collect_session_summary_payload(
        payload,
        interval_seconds=interval_seconds,
        duration_minutes=duration_minutes,
        max_iterations=max_iterations,
    )
    _emit_report(
        summary,
        json_output=json_output,
        output=output,
        text=_collect_session_summary_text(summary),
    )


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
    skip_collect: bool = typer.Option(False, "--skip-collect"),
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
    if skip_collect:
        command_summary["collect_once"] = {"status": "skipped", "reason": "--skip-collect"}
        write_json_report(report_dir / "collect_once.json", command_summary["collect_once"])
    else:
        capture(
            "collect_once",
            lambda: collect_once_cycle(
                settings, _kalshi(settings), _nws(settings), _open_meteo(settings), store_obj, series, station
            ),
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
    readiness_payload = capture("calibration_readiness", lambda: _calibration_readiness_payload(store_obj, station, settings))
    write_text_report(
        report_dir / "calibration_readiness.txt",
        validation_reports.calibration_readiness_text(readiness_payload),
    )
    market_payload = capture(
        "model_vs_market",
        lambda: validation_reports.model_vs_market_payload(store_obj, station, series=series),
    )
    write_text_report(report_dir / "model_vs_market.txt", validation_reports.model_vs_market_text(market_payload))
    model_health_payload = capture(
        "model_health",
        lambda: validation_reports.model_health_payload(
            store_obj,
            settings,
            series,
            station,
            reports_dir=reports_dir,
            paper_replay=_paper_replay_report(store_obj, min_edge=settings.min_edge),
        ),
    )
    write_text_report(report_dir / "model_health.txt", validation_reports.model_health_text(model_health_payload))
    summary = {
        "series": series,
        "station": station,
        "report_dir": str(report_dir),
        "command_summary": command_summary,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
        "skip_collect": skip_collect,
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
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=validation_reports.calibration_readiness_text(payload),
    )


@app.command("model-vs-market")
def model_vs_market(
    station: str | None = typer.Option(None),
    series: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Compare stored model probabilities to Kalshi market-implied probabilities."""
    settings = load_settings()
    station = station or settings.default_station
    series = series or settings.default_series
    payload = validation_reports.model_vs_market_payload(
        _store(settings),
        station,
        series=series,
        start_date=start_date,
        end_date=end_date,
    )
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=validation_reports.model_vs_market_text(payload),
    )


@app.command("model-health")
def model_health(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    reports_dir: str = typer.Option("reports", "--reports-dir"),
    include_threshold_sweep: bool = typer.Option(
        True,
        "--include-threshold-sweep/--no-include-threshold-sweep",
    ),
    include_paper_replay: bool = typer.Option(
        True,
        "--include-paper-replay/--no-include-paper-replay",
    ),
    min_joined_rows_smoke: int = typer.Option(1, "--min-joined-rows-smoke"),
    min_joined_rows_early: int = typer.Option(30, "--min-joined-rows-early"),
    min_market_days_early: int = typer.Option(5, "--min-market-days-early"),
) -> None:
    """Print a plain-English model validation scorecard."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    store_obj = _store(settings)
    paper_replay_payload = (
        _paper_replay_report(store_obj, min_edge=settings.min_edge) if include_paper_replay else None
    )
    threshold_payload = None
    if include_threshold_sweep:
        threshold_payload = _replay_predictions_payload(
            store_obj.load_predictions(station=station),
            store_obj.load_prediction_outcomes(station=station),
            settings,
        )
    payload = validation_reports.model_health_payload(
        store_obj,
        settings,
        series,
        station,
        reports_dir=reports_dir,
        include_threshold_sweep=include_threshold_sweep,
        include_paper_replay=include_paper_replay,
        min_joined_rows_smoke=min_joined_rows_smoke,
        min_joined_rows_early=min_joined_rows_early,
        min_market_days_early=min_market_days_early,
        paper_replay=paper_replay_payload,
        threshold_sweep=threshold_payload,
    )
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=validation_reports.model_health_text(payload),
    )


@app.command("model-provider-probe")
def model_provider_probe(
    station: str | None = typer.Option(None),
    providers: str | None = typer.Option(None, "--providers"),
    models: str | None = typer.Option(None, "--models"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Probe comparison-model provider availability without trading."""
    settings = load_settings()
    station = station or settings.default_station
    payload = _provider_probe_payload(settings, station, providers, models)
    _emit_report(payload, json_output=json_output, output=output, text=_provider_probe_text(payload))


@app.command("direct-noaa-check")
def direct_noaa_check(
    station: str | None = typer.Option(None),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    show_attempts: bool = typer.Option(False, "--show-attempts"),
    max_cycles: int = typer.Option(6, "--max-cycles"),
) -> None:
    """Check optional direct NOAA/Herbie model dependencies and live estimates."""
    settings = load_settings()
    station = station or settings.default_station
    payload = _direct_noaa_check_payload(
        settings,
        station,
        show_attempts=show_attempts,
        max_cycles=max_cycles,
    )
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=_direct_noaa_check_text(payload, show_attempts=show_attempts),
    )


@app.command("model-estimates")
def model_estimates(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    providers: str | None = typer.Option(None, "--providers"),
    models: str | None = typer.Option(None, "--models"),
    include_probabilities: bool = typer.Option(
        False,
        "--include-probabilities/--no-include-probabilities",
    ),
    store: bool = typer.Option(False, "--store"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    only_successful: bool = typer.Option(False, "--only-successful"),
    show_failures: bool = typer.Option(False, "--show-failures"),
) -> None:
    """Show side-by-side high-temperature estimates by model. This command never trades."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    payload = _model_estimates_payload(
        settings,
        series,
        station,
        target_date=resolved_target_date,
        providers_raw=providers,
        models_raw=models,
        include_probabilities=include_probabilities,
        residual_sigma=residual_sigma,
        only_successful=only_successful,
        show_failures=show_failures,
        store_results=store or settings.model_estimate_store_by_default,
    )
    if csv_output or (output and output.lower().endswith(".csv")):
        rows = [safe_console_payload(row) for row in payload["estimates"]]
        if include_probabilities:
            rows.extend(safe_console_payload(row) for row in payload["probabilities"])
        if output:
            _write_csv(Path(output), rows)
        else:
            writer = csv.DictWriter(
                console.file,
                fieldnames=list(rows[0]) if rows else ["message"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows or [{"message": "no rows"}])
        return
    _emit_report(payload, json_output=json_output, output=output, text=_model_estimates_text(payload))


@app.command("model-probabilities")
def model_probabilities(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    providers: str | None = typer.Option(None, "--providers"),
    models: str | None = typer.Option(None, "--models"),
    store: bool = typer.Option(False, "--store"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    top_n: int | None = typer.Option(None, "--top-n"),
    show_market_prices: bool = typer.Option(False, "--show-market-prices"),
    only_successful: bool = typer.Option(False, "--only-successful"),
) -> None:
    """Show each comparison model's implied probabilities for each bracket. This command never trades."""
    _ = show_market_prices
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    payload = _model_estimates_payload(
        settings,
        series,
        station,
        target_date=resolved_target_date,
        providers_raw=providers,
        models_raw=models,
        include_probabilities=True,
        residual_sigma=residual_sigma,
        only_successful=only_successful,
        show_failures=True,
        store_results=store or settings.model_estimate_store_by_default,
    )
    if csv_output or (output and output.lower().endswith(".csv")):
        rows = [safe_console_payload(row) for row in payload["probabilities"]]
        if output:
            _write_csv(Path(output), rows)
        else:
            writer = csv.DictWriter(
                console.file,
                fieldnames=list(rows[0]) if rows else ["message"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows or [{"message": "no rows"}])
        return
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=_model_probabilities_text(payload, top_n=top_n),
    )


@app.command("model-tournament-run")
def model_tournament_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    run_id: str | None = typer.Option(None, "--run-id", "--race-id"),
    yes_stake_dollars: float = typer.Option(100.0, "--yes-stake-dollars"),
    no_stake_dollars: float = typer.Option(10.0, "--no-stake-dollars"),
    min_no_ranges_per_model: int = typer.Option(2, "--min-no-ranges-per-model"),
    profit_target_pct: float = typer.Option(0.10, "--profit-target-pct"),
    interval_seconds: int = typer.Option(60, "--interval-seconds"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    noaa_model_mode: str = typer.Option("full_recompute_each_iteration", "--noaa-model-mode"),
    use_cached_models: bool = typer.Option(False, "--use-cached-models/--no-cached-models"),
    force_model_recompute_every_iteration: bool = typer.Option(
        True,
        "--force-model-recompute-every-iteration/--no-force-model-recompute-every-iteration",
    ),
    dashboard_refresh_seconds: int = typer.Option(5, "--dashboard-refresh-seconds"),
    show_dashboard: bool = typer.Option(False, "--show-dashboard/--no-show-dashboard"),
    dashboard_port: int = typer.Option(8765, "--dashboard-port"),
    reset: bool = typer.Option(False, "--reset"),
    include_consensus: bool = typer.Option(True, "--include-consensus/--no-include-consensus"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run a fake-money-only per-model tournament with taker-style simulated fills."""
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    if interval_seconds < 1:
        raise typer.BadParameter("--interval-seconds must be at least 1.")
    if min_no_ranges_per_model < 0:
        raise typer.BadParameter("--min-no-ranges-per-model must be 0 or greater.")
    if yes_stake_dollars <= 0 or no_stake_dollars <= 0:
        raise typer.BadParameter("stake dollars must be positive.")
    if profit_target_pct <= 0:
        raise typer.BadParameter("--profit-target-pct must be positive.")

    if not use_cached_models or force_model_recompute_every_iteration:
        use_cached_models = False
        force_model_recompute_every_iteration = True
        if noaa_model_mode != "off":
            noaa_model_mode = "full_recompute_each_iteration"
    canonical_run_id = sanitize_run_id(
        run_id
        or f"model_tournament_{resolved_series.lower()}_{resolved_target_date.strftime('%Y%m%d')}_{datetime.now(ZoneInfo(LAX_TIMEZONE)).strftime('%H%M%S')}"
    )
    ensure_canonical_dirs()
    out_dir = get_run_dir(canonical_run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_latest_run_pointer(canonical_run_id, out_dir, journal_path=str(out_dir / "diagnostic.sqlite"))
    write_run_metadata(
        run_id=canonical_run_id,
        race_id=canonical_run_id,
        debug_run_id=canonical_run_id,
        event_ticker=f"{resolved_series}-{resolved_target_date.strftime('%y%b%d').upper()}",
        target_date=resolved_target_date.isoformat(),
        series=resolved_series,
        station=resolved_station,
        journal_path=str(out_dir / "diagnostic.sqlite"),
        latest_json_path=out_dir / "latest.json",
        decisions_jsonl_path=out_dir / "model_tournament_trades.jsonl",
        candidates_csv_path=out_dir / "model_tournament_positions.jsonl",
        terminal_output_path=out_dir / "terminal_output.txt",
        extra={
            "command": "model-tournament-run",
            "fake_money_only": True,
            "live_trading_enabled": False,
            "real_orders_available": False,
            "llm_trader_used": False,
            "execution_style": "taker_simulated",
            "buy_yes_price_source": "yes_ask",
            "buy_no_price_source": "no_ask",
            "close_yes_price_source": "yes_bid",
            "close_no_price_source": "no_bid",
            "use_cached_models": use_cached_models,
            "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
            "noaa_model_mode": noaa_model_mode,
        },
    )
    config = TournamentConfig(
        run_id=canonical_run_id,
        series=resolved_series,
        station=resolved_station,
        target_date=resolved_target_date.isoformat(),
        yes_stake_dollars=yes_stake_dollars,
        no_stake_dollars=no_stake_dollars,
        min_no_ranges_per_model=min_no_ranges_per_model,
        profit_target_pct=profit_target_pct,
        dashboard_refresh_seconds=dashboard_refresh_seconds,
        include_consensus=include_consensus,
    )
    write_json_report(
        out_dir / "effective_config.json",
        {
            "command": "model-tournament-run",
            "config": asdict(config),
            "fake_money_safety": {
                "fake_money_only": True,
                "live_trading_enabled": False,
                "real_orders_available": False,
                "llm_trader_used": False,
            },
            "model_refresh": {
                "use_cached_models": use_cached_models,
                "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
                "noaa_model_mode": noaa_model_mode,
            },
        },
    )
    state = None if reset else load_tournament_state(out_dir)
    started = time.monotonic()
    iteration = 0
    if max_iterations is None and duration_minutes is not None:
        max_iterations = max(1, int(math.ceil((duration_minutes * 60.0) / interval_seconds)))
    if not json_output:
        console.print("Kalshi Weather Model Tournament - FAKE MONEY ONLY")
        console.print(f"Run ID: {canonical_run_id}")
        console.print(f"Target: {resolved_target_date.isoformat()} | Dashboard: {out_dir / 'dashboard.html'}")
    while True:
        iteration += 1
        model_payload = _trader_cached_model_payload(
            settings=settings,
            series=resolved_series,
            station=resolved_station,
            target_date=resolved_target_date,
            cache={},
            noaa_model_mode=noaa_model_mode,
            market_refresh_seconds=interval_seconds,
            fast_model_refresh_seconds=interval_seconds,
            noaa_model_refresh_seconds=interval_seconds,
            observation_refresh_seconds=interval_seconds,
            use_cached_models=use_cached_models,
            force_model_recompute_every_iteration=force_model_recompute_every_iteration,
            model_refresh_seconds=0 if force_model_recompute_every_iteration else interval_seconds,
        )
        state = run_tournament_cycle(model_payload=model_payload, previous_state=state, config=config)
        paths = write_tournament_files(state, out_dir)
        write_json_report(out_dir / "final_results.json", state.get("summary") or {})
        write_json_report(
            out_dir / "bot_trust_report.json",
            {
                "fake_money_only": True,
                "live_trading_enabled": False,
                "real_orders_available": False,
                "llm_trader_used": False,
                "model_tournament": state.get("summary") or {},
                "warnings": (state.get("dashboard") or {}).get("warnings") or [],
            },
        )
        if json_output:
            print(json.dumps(safe_console_payload({"iteration": iteration, "state": state}), separators=(",", ":")))
        else:
            console.print(_model_tournament_run_line(iteration, state, paths["dashboard"]))
        if show_dashboard and iteration == 1:
            console.print(f"Serve dashboard with: kalshi-weather model-tournament-dashboard --run-id {canonical_run_id} --port {dashboard_port}")
        if max_iterations is not None and iteration >= max_iterations:
            break
        if duration_minutes is not None and (time.monotonic() - started) >= duration_minutes * 60.0:
            break
        time.sleep(interval_seconds)
    if not json_output:
        console.print("")
        console.print("Model Tournament Run Complete")
        console.print(f"Output: {out_dir}")
        console.print(f"Dashboard file: {out_dir / 'dashboard.html'}")


@app.command("model-tournament-dashboard")
def model_tournament_dashboard(
    run_id: str = typer.Option(..., "--run-id", "--race-id"),
    port: int = typer.Option(8765, "--port", "--dashboard-port"),
    host: str = typer.Option("127.0.0.1", "--host"),
) -> None:
    """Serve a read-only local dashboard for a model tournament run."""
    import functools
    import http.server
    import socketserver

    out_dir = get_run_dir(sanitize_run_id(run_id))
    dashboard_path = out_dir / "dashboard.html"
    if not dashboard_path.exists():
        raise typer.BadParameter(f"dashboard not found: {dashboard_path}")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out_dir))
    url = f"http://{host}:{port}/dashboard.html"
    console.print(f"Serving model tournament dashboard: {url}")
    console.print(f"Directory: {out_dir}")
    with socketserver.TCPServer((host, port), handler) as server:
        server.serve_forever()


@app.command("model-tournament-report")
def model_tournament_report(
    run_id: str = typer.Option(..., "--run-id", "--race-id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print the latest fake-money model tournament summary."""
    out_dir = get_run_dir(sanitize_run_id(run_id))
    state = load_tournament_state(out_dir)
    if state is None:
        raise typer.BadParameter(f"model tournament state not found under {out_dir}")
    summary = state.get("summary") or {}
    if json_output:
        console.print_json(data=safe_console_payload(summary))
        return
    lines = [
        "Kalshi Weather Model Tournament Report",
        "======================================",
        f"Run ID: {summary.get('run_id')}",
        f"Target: {summary.get('target_date')} | Updated: {summary.get('updated_at_utc')}",
        "Mode: fake_money_only | Live trading: DISABLED | Real orders: NOT AVAILABLE",
        (
            f"Models: {summary.get('models')} | Open positions: {summary.get('open_positions')} | "
            f"Closed positions: {summary.get('closed_positions')} | Total P/L: "
            f"${float(summary.get('open_pnl_dollars') or 0) + float(summary.get('closed_pnl_dollars') or 0):.2f}"
        ),
        "",
        "Model                           Open  Closed  Staked      Open P/L   Closed P/L  Total P/L",
        "------------------------------  ----  ------  ----------  ---------  ----------  ---------",
    ]
    for row in summary.get("model_books") or []:
        lines.append(
            f"{str(row.get('model_key'))[:30]:30}  {int(row.get('open_positions') or 0):4d}  "
            f"{int(row.get('closed_positions') or 0):6d}  ${float(row.get('total_staked_dollars') or 0):9.2f}  "
            f"${float(row.get('open_pnl_dollars') or 0):8.2f}  ${float(row.get('closed_pnl_dollars') or 0):9.2f}  "
            f"${float(row.get('total_pnl_dollars') or 0):8.2f}"
        )
    console.print("\n".join(lines))


def _model_tournament_run_line(iteration: int, state: dict[str, Any], dashboard_path: str) -> str:
    summary = state.get("summary") or {}
    events = state.get("cycle_events") or []
    buys = sum(1 for event in events if event.get("event_type") == "buy")
    closes = sum(1 for event in events if event.get("event_type") == "close")
    skips = sum(1 for event in events if event.get("event_type") == "skip")
    open_pnl = float(summary.get("open_pnl_dollars") or 0.0)
    closed_pnl = float(summary.get("closed_pnl_dollars") or 0.0)
    warnings_count = len((state.get("dashboard") or {}).get("warnings") or [])
    return (
        f"{iteration:04d} | models {summary.get('models', 0)} | "
        f"open {summary.get('open_positions', 0)} | closed {summary.get('closed_positions', 0)} | "
        f"buys {buys} closes {closes} skips {skips} | "
        f"open P/L ${open_pnl:.2f} closed P/L ${closed_pnl:.2f} | "
        f"warnings {warnings_count} | dashboard {dashboard_path}"
    )


@app.command("model-telemetry-once")
def model_telemetry_once(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    providers: str | None = typer.Option(None, "--providers"),
    models: str | None = typer.Option(None, "--models"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    store: bool = typer.Option(True, "--store/--no-store"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    finalize_recent_days: int = typer.Option(7, "--finalize-recent-days"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Record one read-only model/market/weather telemetry snapshot. This command never trades."""
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    market_date = _resolve_telemetry_market_date(target_date, tomorrow)
    payload = _model_telemetry_payload(
        settings,
        series=resolved_series,
        station=resolved_station,
        target_date=market_date,
        providers=providers,
        models=models,
        residual_sigma=residual_sigma,
        store_results=store,
        include_raw=include_raw,
        finalize_recent_days=finalize_recent_days,
    )
    _emit_report(payload, json_output=json_output, output=output, text=_model_telemetry_text(payload))


@app.command("model-telemetry-run")
def model_telemetry_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    providers: str | None = typer.Option(None, "--providers"),
    models: str | None = typer.Option(None, "--models"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    interval_seconds: int = typer.Option(900, "--interval-seconds"),
    duration_days: float | None = typer.Option(7.0, "--duration-days"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    store: bool = typer.Option(True, "--store/--no-store"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    finalize_recent_days: int = typer.Option(7, "--finalize-recent-days"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_lines: bool = typer.Option(False, "--json-lines"),
) -> None:
    """Run read-only model telemetry every interval. No LLM, paper broker, or trading code is used."""
    if interval_seconds < 1:
        raise typer.BadParameter("--interval-seconds must be at least 1.")
    if max_iterations is None:
        if duration_minutes is not None:
            max_iterations = max(1, math.ceil((duration_minutes * 60) / interval_seconds))
        elif duration_days is not None:
            max_iterations = max(1, math.ceil((duration_days * 24 * 60 * 60) / interval_seconds))
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    if not quiet and not json_lines:
        duration_label = (
            f"{duration_days:g}d"
            if duration_minutes is None and duration_days is not None
            else _fmt_duration_label(duration_minutes, max_iterations)
        )
        console.print(
            "\n".join(
                [
                    "Kalshi Weather Model Telemetry Run",
                    "==================================",
                    f"Mode: record_only | Series: {resolved_series} | Station: {resolved_station}",
                    "Live trading: DISABLED | Paper orders: DISABLED | LLM trader: DISABLED",
                    f"Interval: {interval_seconds}s | Duration: {duration_label}",
                    "",
                ]
            )
        )
    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                market_date = _resolve_telemetry_market_date(target_date, tomorrow)
                payload = _model_telemetry_payload(
                    settings,
                    series=resolved_series,
                    station=resolved_station,
                    target_date=market_date,
                    providers=providers,
                    models=models,
                    residual_sigma=residual_sigma,
                    store_results=store,
                    include_raw=include_raw,
                    finalize_recent_days=finalize_recent_days,
                )
                if json_lines:
                    print(json.dumps(safe_console_payload(payload), separators=(",", ":"), sort_keys=True))
                elif not quiet:
                    console.print(_model_telemetry_run_line(iteration, payload))
            except Exception as exc:  # noqa: BLE001
                if json_lines:
                    print(
                        json.dumps(
                            {
                                "iteration": iteration,
                                "status": "error",
                                "error": str(exc),
                                "record_only": True,
                            },
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                else:
                    console.print(f"{iteration:04d} | telemetry error | {exc}")
            if max_iterations is not None and iteration >= max_iterations:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        if not json_lines:
            console.print("Stopping model telemetry run.")


@app.command("record-weather-market-once")
def record_weather_market_once(
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    target_date: str | None = typer.Option("auto", "--target-date"),
    timezone_name: str = typer.Option(LAX_TIMEZONE, "--timezone"),
    experiment_id: str = typer.Option("lax_model_validation", "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    jsonl_path: str | None = typer.Option(None, "--jsonl-path"),
    refresh_recent_days: int = typer.Option(3, "--refresh-recent-days"),
    model_set: str = typer.Option("current", "--model-set"),
    models: str | None = typer.Option(None, "--models"),
    skip_models: str | None = typer.Option(None, "--skip-models"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    replace_existing_bucket: bool = typer.Option(False, "--replace-existing-bucket"),
    list_models: bool = typer.Option(False, "--list-models"),
    probe_models: bool = typer.Option(False, "--probe-models"),
    snapshot_style: str = typer.Option("table", "--snapshot-style"),
    compact: bool = typer.Option(False, "--compact"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Record one weather/model/Kalshi validation snapshot. This command never trades."""
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    if list_models:
        _emit_report(
            registry_rows(),
            json_output=json_output,
            output=output,
            text=_registry_table_text(registry_rows()),
        )
        return
    model_keys = select_model_keys(model_set=model_set, models=models, skip_models=skip_models)
    providers_option, models_option = provider_model_options(model_keys)
    if probe_models:
        payload = _provider_probe_payload(
            settings,
            resolved_station,
            providers_option,
            models_option,
        )
        _emit_report(payload, json_output=json_output, output=output, text=_provider_probe_text(payload))
        return
    market_date = _resolve_record_target_date(target_date, timezone_name)
    payload = _record_weather_market_payload(
        settings,
        series=resolved_series,
        station=resolved_station,
        target_date=market_date,
        timezone_name=timezone_name,
        experiment_id=experiment_id,
        model_set=model_set,
        models=models,
        skip_models=skip_models,
        residual_sigma=residual_sigma,
        include_raw=include_raw,
        refresh_recent_days=refresh_recent_days,
        bucket_interval_seconds=900,
    )
    payload = _write_record_payload(
        payload,
        journal_path=journal_path,
        jsonl_path=jsonl_path,
        replace_existing_bucket=replace_existing_bucket,
    )
    if quiet and not json_output and output is None:
        return
    style = "compact" if compact else snapshot_style.strip().lower()
    if style not in {"compact", "table", "full"}:
        raise typer.BadParameter("--snapshot-style must be compact, table, or full.")
    _emit_report(payload, json_output=json_output, output=output, text=_record_snapshot_text(1, payload, snapshot_style=style))


@app.command("record-weather-market-loop")
def record_weather_market_loop(
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    target_date: str | None = typer.Option("auto", "--target-date"),
    timezone_name: str = typer.Option(LAX_TIMEZONE, "--timezone"),
    experiment_id: str = typer.Option("lax_model_validation", "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    jsonl_path: str | None = typer.Option(None, "--jsonl-path"),
    interval_seconds: int = typer.Option(900, "--interval-seconds"),
    duration_days: float | None = typer.Option(7.0, "--duration-days"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    refresh_recent_days: int = typer.Option(3, "--refresh-recent-days"),
    model_set: str = typer.Option("current", "--model-set"),
    models: str | None = typer.Option(None, "--models"),
    skip_models: str | None = typer.Option(None, "--skip-models"),
    residual_sigma: float | None = typer.Option(None, "--residual-sigma"),
    include_raw: bool = typer.Option(True, "--include-raw/--no-include-raw"),
    replace_existing_bucket: bool = typer.Option(False, "--replace-existing-bucket"),
    list_models: bool = typer.Option(False, "--list-models"),
    probe_models: bool = typer.Option(False, "--probe-models"),
    snapshot_style: str = typer.Option("table", "--snapshot-style"),
    compact: bool = typer.Option(False, "--compact"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_lines: bool = typer.Option(False, "--json-lines"),
) -> None:
    """Run record-only model validation telemetry repeatedly. This command never trades."""
    if interval_seconds < 1:
        raise typer.BadParameter("--interval-seconds must be at least 1.")
    snapshot_style = "compact" if compact else snapshot_style.strip().lower()
    if snapshot_style not in {"compact", "table", "full"}:
        raise typer.BadParameter("--snapshot-style must be compact, table, or full.")
    if max_iterations is None:
        if duration_minutes is not None:
            max_iterations = max(1, math.ceil((duration_minutes * 60) / interval_seconds))
        elif duration_days is not None:
            max_iterations = max(1, math.ceil((duration_days * 24 * 60 * 60) / interval_seconds))
    settings = load_settings()
    resolved_series = series or settings.default_series
    resolved_station = station or settings.default_station
    if list_models:
        console.print(_registry_table_text(registry_rows()))
        return
    model_keys = select_model_keys(model_set=model_set, models=models, skip_models=skip_models)
    providers_option, models_option = provider_model_options(model_keys)
    if probe_models:
        payload = _provider_probe_payload(settings, resolved_station, providers_option, models_option)
        console.print(_provider_probe_text(payload))
        return
    if not quiet and not json_lines:
        duration_label = (
            f"{duration_days:g}d"
            if duration_minutes is None and duration_days is not None
            else _fmt_duration_label(duration_minutes, max_iterations)
        )
        console.print(
            "\n".join(
                [
                    "Kalshi Weather Record-Only Loop",
                    "================================",
                    f"Mode: record_only | Series: {resolved_series} | Station: {resolved_station}",
                    "Live trading: DISABLED | Paper orders: DISABLED | LLM trader: DISABLED",
                    f"Interval: {interval_seconds}s | Duration: {duration_label}",
                    "",
                ]
            )
        )
    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                market_date = _resolve_record_target_date(target_date, timezone_name)
                payload = _record_weather_market_payload(
                    settings,
                    series=resolved_series,
                    station=resolved_station,
                    target_date=market_date,
                    timezone_name=timezone_name,
                    experiment_id=experiment_id,
                    model_set=model_set,
                    models=models,
                    skip_models=skip_models,
                    residual_sigma=residual_sigma,
                    include_raw=include_raw,
                    refresh_recent_days=refresh_recent_days,
                    bucket_interval_seconds=interval_seconds,
                )
                payload = _write_record_payload(
                    payload,
                    journal_path=journal_path,
                    jsonl_path=jsonl_path,
                    replace_existing_bucket=replace_existing_bucket,
                )
                if json_lines:
                    print(json.dumps(safe_console_payload(payload), separators=(",", ":"), sort_keys=True))
                elif not quiet:
                    console.print(_record_snapshot_text(iteration, payload, snapshot_style=snapshot_style))
            except Exception as exc:  # noqa: BLE001
                if json_lines:
                    print(json.dumps({"status": "error", "error": str(exc), "record_only": True}))
                elif not quiet:
                    console.print(f"{iteration:04d} | record error | {exc}")
            if max_iterations is not None and iteration >= max_iterations:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        if not json_lines:
            console.print("Stopping record-weather-market loop.")


@app.command("analyze-model-validation")
def analyze_model_validation_command(
    experiment_id: str | None = typer.Option(None, "--experiment-id"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Analyze record-only model validation snapshots."""
    payload = analyze_model_validation(journal_path=journal_path, experiment_id=experiment_id)
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=format_validation_analysis(payload),
    )


@app.command("advisor-synthetic-test")
def advisor_synthetic_test(
    scenario_dir: str = typer.Option("synthetic_scenarios/llm_trade_advisor_edge_cases", "--scenario-dir"),
    advisor_mode: str = typer.Option("rule_based", "--advisor-mode"),
    fail_on_mismatch: bool = typer.Option(False, "--fail-on-mismatch"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    charts: bool = typer.Option(False, "--charts"),
) -> None:
    """Run offline LLM trade advisor edge-case tests. No network is used."""
    summary = run_advisor_synthetic_suite(
        scenario_dir,
        advisor_mode=advisor_mode,
        output=output,
        charts=charts,
    )
    text = _advisor_synthetic_text(summary)
    _emit_report(summary, json_output=json_output, output=output, text=text)
    if fail_on_mismatch and not summary["passed"]:
        raise typer.Exit(1)


@app.command("llm-advisor-smoke-test")
def llm_advisor_smoke_test(
    provider: str = typer.Option("ollama", "--provider"),
    model: str = typer.Option(DEFAULT_LLM_MODEL, "--model"),
    host: str | None = typer.Option(None, "--host"),
    timeout_seconds: int = typer.Option(60, "--timeout-seconds"),
    max_retries: int = typer.Option(2, "--max-retries"),
    temperature: float = typer.Option(0.0, "--temperature"),
    rule_only: bool = typer.Option(False, "--rule-only"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Run a fake-money LLM advisor smoke test on one synthetic trade."""
    advisor_input = build_sample_advisor_input()
    trade_snapshot = advisor_input_to_trade_snapshot(advisor_input)
    raw_response: dict[str, Any] | None = None
    if rule_only:
        decision = RuleBasedAdvisor().decide(advisor_input)
        resolved_provider = "rule_only"
        resolved_model = "deterministic"
    elif provider.strip().lower() == "ollama":
        llm = OllamaLLMProvider(
            host=host,
            model=_resolve_llm_model(model),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            temperature=temperature,
        )
        decision, raw = llm.advise_trade_with_response(trade_snapshot)
        raw_response = raw.to_dict()
        resolved_provider = llm.provider_name
        resolved_model = llm.model
    else:
        raise typer.BadParameter("Only --provider ollama is currently supported.")
    validated = validate_advisor_trade(advisor_input, decision)
    hard_result = hard_validator_result(validated)
    log_path = write_llm_decision_log(
        "reports/llm_advisor_decisions",
        {
            "race_id": "llm_advisor_smoke_test",
            "model_key": decision.model_key,
            "market_ticker": decision.market_ticker,
            "bracket_label": decision.bracket_label,
            "side": decision.side,
            "trade_snapshot": trade_snapshot,
            "trade_quality_score": decision.trade_quality_score,
            "llm_raw_response": raw_response,
            "llm_parsed_decision": decision.to_dict(),
            "hard_validator_result": hard_result,
            "final_action": validated.final_action,
            "fake_trade_id": None,
            "error": (raw_response or {}).get("error") if raw_response else None,
        },
    )
    passed = bool(rule_only or (raw_response or {}).get("success")) and (
        decision.requires_validator_approval is True
    )
    payload = {
        "passed": passed,
        "provider": resolved_provider,
        "model": resolved_model,
        "rule_only": rule_only,
        "live_trading_enabled": False,
        "trade_snapshot": trade_snapshot,
        "llm_raw_response": raw_response,
        "llm_decision": decision.to_dict(),
        "hard_validator_result": hard_result,
        "decision_log_path": str(log_path),
    }
    text = _llm_advisor_smoke_text(payload)
    _emit_report(payload, json_output=json_output, output=output, text=text)


@app.command("advisor-dry-run")
def advisor_dry_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("advisor_dry_run", "--race-id"),
    advisor_mode: str = typer.Option("rule_based", "--advisor-mode"),
    model_key_filter: str | None = typer.Option(None, "--model-key"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Create current candidates and run the advisor without executing fake trades."""
    _ = verbose
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    config = _model_race_config(
        race_id=race_id,
        race_mode="independent",
        starting_cash_per_model=1000.0,
        base_hurdle=0.09,
        max_risk_per_trade=15.0,
        max_exposure_per_model=50.0,
        max_daily_fake_loss_per_model=10.0,
        profit_target_cents=10,
        stop_loss_cents=6,
        max_hold_minutes=45,
        force_flat_time_local="23:59",
        advisor_mode=advisor_mode,
        advisor_required=True,
    )
    if model_key_filter:
        config = replace(config, include_models=[model_key_filter])
    store_obj = _store(settings)
    model_payload = _model_race_model_payload(settings, series, station, resolved_target_date)
    payload = advisor_dry_run_payload(store_obj, model_payload, config)
    text = _advisor_dry_run_text(payload)
    _emit_report(payload, json_output=json_output, output=output, text=text)


@app.command("advisor-decision-report")
def advisor_decision_report(
    race_id: str | None = typer.Option(None, "--race-id"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Summarize advisor decisions and validator vetoes."""
    settings = load_settings()
    store_obj = _store(settings)
    payload = store_obj.advisor_decision_summary(race_id)
    if csv_output or (output and output.lower().endswith(".csv")):
        rows = safe_console_payload(payload.get("by_model", []))
        if output:
            _write_csv(Path(output), rows)
        else:
            writer = csv.DictWriter(console.file, fieldnames=list(rows[0]) if rows else ["message"])
            writer.writeheader()
            writer.writerows(rows or [{"message": "no rows"}])
        return
    _emit_report(payload, json_output=json_output, output=output, text=_advisor_decision_report_text(payload))


@app.command("advisor-export-training-examples")
def advisor_export_training_examples(
    race_id: str | None = typer.Option(None, "--race-id"),
    output_dir: str = typer.Option("reports/llm_trade_advisor/training_examples", "--output-dir", "--output"),
    limit: int | None = typer.Option(None, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Export advisor inputs, decisions, validator results, and labeled examples as JSONL."""
    settings = load_settings()
    rows = _store(settings).load_advisor_decisions(race_id, limit=limit)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = _write_advisor_training_exports(out, rows)
    payload = {
        "race_id": race_id,
        "output_dir": str(out),
        "decision_count": len(rows),
        "files": counts,
        "fake_money_only": True,
        "live_trading_enabled": False,
    }
    _emit_report(payload, json_output=json_output, text=_advisor_export_text(payload))


def _trader_risk_limits(
    *,
    min_edge_cents: float,
    max_contracts_per_trade: int,
    max_risk_dollars_per_trade: float,
    max_total_exposure_dollars: float,
    max_exposure_dollars_per_bracket: float,
    max_contracts_per_bracket: int = 500,
    max_contracts_per_side: int = 1000,
    max_open_positions: int,
    max_open_orders: int = 4,
    max_total_open_risk_groups: int | None = None,
    min_volume: int = 0,
    allow_negative_cash: bool = False,
    allow_scale_in: bool = False,
    scale_in_edge_buffer_cents: float = 0.0,
    same_candidate_cooldown_minutes: float = 15.0,
    max_open_loss_dollars: float = 100.0,
    max_total_drawdown_dollars: float = 150.0,
    allow_lowball_passive_orders: bool = False,
    max_passive_distance_from_bid_cents: float = 1.0,
    max_passive_order_age_minutes: float = 15.0,
    block_high_confidence_no_on_extreme_spread: bool = False,
    extreme_spread_no_block_threshold_f: float = 8.0,
    block_no_on_model_source_degraded: bool = False,
    high_spread_reduce_size_factor: float = 0.5,
    clustered_disputed_extra_edge_cents: float = 2.0,
) -> RiskLimits:
    return RiskLimits(
        min_edge_cents=min_edge_cents,
        max_contracts_per_trade=max_contracts_per_trade,
        max_risk_dollars_per_trade=max_risk_dollars_per_trade,
        max_total_exposure_dollars=max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=max_exposure_dollars_per_bracket,
        max_contracts_per_bracket=max_contracts_per_bracket,
        max_contracts_per_side=max_contracts_per_side,
        max_open_positions=max_open_positions,
        max_open_orders=max_open_orders,
        max_total_open_risk_groups=max_total_open_risk_groups,
        min_volume=min_volume,
        allow_negative_cash=allow_negative_cash,
        allow_scale_in=allow_scale_in,
        scale_in_edge_buffer_cents=scale_in_edge_buffer_cents,
        same_candidate_cooldown_minutes=same_candidate_cooldown_minutes,
        max_open_loss_dollars=max_open_loss_dollars,
        max_total_drawdown_dollars=max_total_drawdown_dollars,
        allow_lowball_passive_orders=allow_lowball_passive_orders,
        max_passive_distance_from_bid_cents=max_passive_distance_from_bid_cents,
        max_passive_order_age_minutes=max_passive_order_age_minutes,
        block_high_confidence_no_on_extreme_spread=block_high_confidence_no_on_extreme_spread,
        extreme_spread_no_block_threshold_f=extreme_spread_no_block_threshold_f,
        block_no_on_model_source_degraded=block_no_on_model_source_degraded,
        high_spread_reduce_size_factor=high_spread_reduce_size_factor,
        clustered_disputed_extra_edge_cents=clustered_disputed_extra_edge_cents,
    )


def _trader_context_for_cli(
    *,
    settings: Settings,
    store_obj: SQLiteStore,
    series: str,
    station: str,
    target_date: date | None,
    race_id: str,
    model_key: str | None,
    risk_limits: RiskLimits,
    journal_path: str | None = None,
) -> Any:
    context, _ = _trader_context_and_pending_for_cli(
        settings=settings,
        store_obj=store_obj,
        series=series,
        station=station,
        target_date=target_date,
        race_id=race_id,
        model_key=model_key,
        risk_limits=risk_limits,
        journal_path=journal_path,
        process_pending_orders=False,
    )
    return context


def _trader_context_and_pending_for_cli(
    *,
    settings: Settings,
    store_obj: SQLiteStore,
    series: str,
    station: str,
    target_date: date | None,
    race_id: str,
    model_key: str | None,
    risk_limits: RiskLimits,
    journal_path: str | None = None,
    process_pending_orders: bool = False,
    starting_cash: float | None = None,
    paper_fill_price_mode: str = "conservative",
    model_payload: dict[str, Any] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    model_payload = model_payload or _model_race_model_payload(settings, series, station, target_date)
    model_positions = store_obj.load_open_model_race_positions(race_id) if race_id else []
    positions = list(model_positions)
    open_orders: list[dict[str, Any]] = []
    pending_order_executions: list[dict[str, Any]] = []
    if journal_path:
        journal = _trader_journal(journal_path)
        positions = [*positions, *journal.load_open_positions()]
        open_orders = journal.load_open_orders()
        if process_pending_orders:
            pre_sweep_context = trader_context_from_model_payload(
                model_payload,
                risk_limits=risk_limits,
                positions=positions,
                open_orders=open_orders,
                probability_model_key=model_key,
            )
            pre_sweep_payload = pre_sweep_context.to_dict()

            freshness_risk_config = _edge_risk_config(
                risk_limits=risk_limits,
                min_yes_edge_cents=None,
                min_no_edge_cents=None,
                min_no_upside_cents=8.0,
                max_no_bin_probability=0.20,
                max_spread_cents=4,
            )
            freshness = _debug_data_freshness(pre_sweep_payload, freshness_risk_config)

            def _pending_revalidation_result(
                *,
                passed: bool,
                reason: str | None = None,
                failure_code: str | None = None,
                fair_value_cents: float | None = None,
                net_edge_cents: float | None = None,
            ) -> dict[str, Any]:
                return {
                    "passed": passed,
                    "reason": reason,
                    "failure_code": failure_code,
                    "fill_revalidated_fair_value_cents": fair_value_cents,
                    "fill_revalidated_net_edge_cents": net_edge_cents,
                    "fill_revalidated_market_age_seconds": freshness.get("market_age_seconds"),
                    "fill_revalidated_model_age_seconds": freshness.get("model_age_seconds"),
                }

            def _pending_risk_check(order: dict[str, Any], fill_price_cents: float) -> dict[str, Any]:
                selected_id = (order.get("metadata") or {}).get("selected_candidate_id")
                candidate = _selected_trade_candidate(
                    pre_sweep_payload,
                    {
                        "selected_candidate_id": selected_id,
                        "contract_ticker": order.get("contract_ticker"),
                        "side": order.get("side"),
                        "bracket": (order.get("metadata") or {}).get("bracket_label"),
                    },
                )
                if not candidate:
                    return _pending_revalidation_result(
                        passed=False,
                        reason="fill_rejected_revalidation_incomplete",
                        failure_code="fill_rejected_revalidation_incomplete",
                    )
                action = str(order.get("action") or "")
                if freshness.get("market_stale"):
                    return _pending_revalidation_result(
                        passed=False,
                        reason="fill_rejected_stale_market",
                        failure_code="fill_rejected_edge_no_longer_valid",
                        fair_value_cents=_float_or_none(candidate.get("model_fair_cents")),
                    )
                if freshness.get("model_stale"):
                    return _pending_revalidation_result(
                        passed=False,
                        reason="fill_rejected_stale_model",
                        failure_code="fill_rejected_edge_no_longer_valid",
                        fair_value_cents=_float_or_none(candidate.get("model_fair_cents")),
                    )
                candidate = {**candidate, "entry_price_cents": fill_price_cents}
                fair_cents = _float_or_none(candidate.get("model_fair_cents"))
                fee_cents = _float_or_none(candidate.get("fee_cents")) or 0.0
                if fair_cents is None or freshness.get("market_age_seconds") is None or freshness.get("model_age_seconds") is None:
                    return _pending_revalidation_result(
                        passed=False,
                        reason="fill_rejected_revalidation_incomplete",
                        failure_code="fill_rejected_revalidation_incomplete",
                        fair_value_cents=fair_cents,
                    )
                if action == "PLACE_FAKE_LIMIT_BUY":
                    refreshed_edge = fair_cents - float(fill_price_cents) - fee_cents
                    candidate["raw_edge_cents"] = fair_cents - float(fill_price_cents)
                    candidate["fee_adjusted_edge_cents"] = refreshed_edge
                    if refreshed_edge < risk_limits.min_edge_cents:
                        return _pending_revalidation_result(
                            passed=False,
                            reason="fill_rejected_edge_no_longer_valid",
                            failure_code="fill_rejected_edge_no_longer_valid",
                            fair_value_cents=fair_cents,
                            net_edge_cents=refreshed_edge,
                        )
                elif action in {"CLOSE_FAKE_POSITION", "PLACE_FAKE_LIMIT_SELL"}:
                    refreshed_edge = float(fill_price_cents) - fair_cents - fee_cents
                    candidate["exit_price_cents"] = fill_price_cents
                    candidate["fee_adjusted_edge_cents"] = refreshed_edge
                else:
                    return _pending_revalidation_result(
                        passed=False,
                        reason="fill_rejected_revalidation_incomplete",
                        failure_code="fill_rejected_revalidation_incomplete",
                        fair_value_cents=fair_cents,
                    )
                decision = {
                    "action": action,
                    "selected_candidate_id": selected_id,
                    "contract_ticker": order.get("contract_ticker"),
                    "bracket": (order.get("metadata") or {}).get("bracket_label"),
                    "side": order.get("side"),
                    "limit_price_cents": fill_price_cents,
                    "max_contracts": order.get("quantity"),
                }
                context_for_risk = dict(pre_sweep_payload)
                if selected_id:
                    context_for_risk["open_orders"] = [
                        existing_order
                        for existing_order in pre_sweep_payload.get("open_orders", [])
                        if str(existing_order.get("selected_candidate_id") or "") != str(selected_id)
                    ]
                portfolio = _trader_portfolio_snapshot(
                    context_for_risk,
                    journal.load_open_positions(),
                    journal.load_fills(),
                    starting_cash=starting_cash,
                )
                result = _validate_paper_buy_against_portfolio(
                    decision=decision,
                    candidate=candidate,
                    context=pre_sweep_payload,
                    portfolio=portfolio,
                    open_positions=journal.load_open_positions(),
                    fills=journal.load_fills(),
                    risk_limits=risk_limits,
                )
                if result:
                    return _pending_revalidation_result(
                        passed=False,
                        reason=result.rejection_reason,
                        failure_code="fill_rejected_edge_no_longer_valid",
                        fair_value_cents=fair_cents,
                        net_edge_cents=refreshed_edge,
                    )
                return _pending_revalidation_result(
                    passed=True,
                    fair_value_cents=fair_cents,
                    net_edge_cents=refreshed_edge,
                )

            pending_order_executions = journal.process_pending_orders(
                pre_sweep_context.market_brackets,
                risk_check=_pending_risk_check if starting_cash is not None else None,
                fill_price_mode=paper_fill_price_mode,
            )
            positions = [*model_positions, *journal.load_open_positions()]
            open_orders = journal.load_open_orders()
    context = trader_context_from_model_payload(
        model_payload,
        risk_limits=risk_limits,
        positions=positions,
        open_orders=open_orders,
        probability_model_key=model_key,
    )
    return context, pending_order_executions


def _trader_llm_client(
    *,
    llm_provider: str,
    model: str | None,
    llm_host: str | None,
    timeout_seconds: int,
    temperature: float,
    dry_run: bool,
) -> TraderLLMClient:
    provider = llm_provider.strip().lower()
    if dry_run and provider in {"", "dry-run", "dry_run"}:
        provider = "dry-run"
    if provider in {"dry-run", "dry_run"}:
        return DryRunTraderLLMClient()
    if provider == "mock":
        return MockTraderLLMClient()
    if provider == "ollama":
        return OllamaTraderLLMClient(
            host=llm_host,
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
        )
    if provider == "openai":
        return OpenAITraderLLMClient(
            model=model,
            timeout_seconds=timeout_seconds,
            temperature=temperature,
        )
    raise typer.BadParameter("--llm-provider must be dry-run, mock, ollama, or openai.")


def _trader_journal(path: str) -> SqliteTraderJournal:
    return SqliteTraderJournal(path)


def _normalize_trader_strategy(value: str) -> str:
    strategy = value.strip().lower()
    if strategy not in {"exact-bin", "no-exclusion", "hybrid"}:
        raise typer.BadParameter("--strategy must be exact-bin, no-exclusion, or hybrid.")
    return strategy


def _normalize_trader_decision_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"rules", "llm", "llm-review"}:
        raise typer.BadParameter("--decision-mode must be rules, llm, or llm-review.")
    return mode


def _normalize_trader_order_style(value: str) -> str:
    style = value.strip().lower()
    if style not in {"passive", "taker", "hybrid"}:
        raise typer.BadParameter("--order-style must be passive, taker, or hybrid.")
    return style


def _parse_bool_option(value: Any, *, option_name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise typer.BadParameter(f"{option_name} must be true or false.")


def _edge_cost_config(
    *,
    risk_limits: RiskLimits,
    slippage_cents: float,
    tail_risk_padding_cents: float,
    passive_improvement_cents: int,
) -> EdgeCostConfig:
    return EdgeCostConfig(
        include_fees=True,
        taker_fee_rate=risk_limits.taker_fee_rate,
        maker_fee_rate=risk_limits.maker_fee_rate,
        maker_fee_enabled=risk_limits.use_maker_fees,
        slippage_cents=slippage_cents,
        tail_risk_padding_cents=tail_risk_padding_cents,
        passive_improvement_cents=passive_improvement_cents,
    )


def _edge_risk_config(
    *,
    risk_limits: RiskLimits,
    min_yes_edge_cents: float | None,
    min_no_edge_cents: float | None,
    min_no_upside_cents: float,
    max_no_bin_probability: float,
    max_spread_cents: int,
    edge_comparison_epsilon_cents: float = 0.001,
    no_probability_filter_mode: str | None = None,
    no_probability_penalty_start: float | None = None,
    no_probability_penalty_factor: float = 0.30,
    absolute_no_bin_probability_cap: float = 0.60,
    model_authoritative: bool = False,
    allow_cheap_ask_yes_with_missing_bid: bool = False,
    cheap_ask_yes_max_cents: float = 2.0,
    cheap_ask_yes_min_net_edge_cents: float = 8.0,
    cheap_ask_yes_max_contracts: int = 25,
    model_authoritative_tight_spread_f: float = 3.0,
    model_authoritative_wide_spread_f: float = 5.0,
    model_authoritative_extreme_spread_f: float = 7.0,
) -> EdgeRiskConfig:
    min_yes = risk_limits.min_edge_cents if min_yes_edge_cents is None else min_yes_edge_cents
    min_no = risk_limits.min_edge_cents if min_no_edge_cents is None else min_no_edge_cents
    filter_mode = (no_probability_filter_mode or ("soft_penalty" if model_authoritative else "hard")).strip().lower()
    if filter_mode not in {"hard", "soft_penalty", "off"}:
        filter_mode = "soft_penalty" if model_authoritative else "hard"
    penalty_start = 0.20 if no_probability_penalty_start is None else float(no_probability_penalty_start)
    return EdgeRiskConfig(
        min_edge_cents=risk_limits.min_edge_cents,
        min_yes_edge_cents=min_yes,
        min_no_edge_cents=min_no,
        min_no_upside_cents=min_no_upside_cents,
        max_no_bin_probability=max_no_bin_probability,
        no_probability_filter_mode=filter_mode,
        no_probability_penalty_start=penalty_start,
        no_probability_penalty_factor=no_probability_penalty_factor,
        absolute_no_bin_probability_cap=absolute_no_bin_probability_cap,
        max_spread_cents=max_spread_cents,
        max_contracts_per_trade=risk_limits.max_contracts_per_trade,
        max_risk_dollars_per_trade=risk_limits.max_risk_dollars_per_trade,
        max_total_exposure_dollars=risk_limits.max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=risk_limits.max_exposure_dollars_per_bracket,
        max_open_positions=risk_limits.max_open_positions,
        max_open_orders=risk_limits.max_open_orders,
        max_total_open_risk_groups=(
            risk_limits.max_total_open_risk_groups
            if risk_limits.max_total_open_risk_groups is not None
            else risk_limits.max_open_positions
        ),
        allow_scale_in=risk_limits.allow_scale_in,
        cooldown_seconds=int(round(risk_limits.same_candidate_cooldown_minutes * 60)),
        min_cash_buffer_dollars=0.0,
        allow_lowball_passive_orders=risk_limits.allow_lowball_passive_orders,
        max_passive_distance_from_bid_cents=risk_limits.max_passive_distance_from_bid_cents,
        max_passive_order_age_minutes=risk_limits.max_passive_order_age_minutes,
        block_high_confidence_no_on_extreme_spread=risk_limits.block_high_confidence_no_on_extreme_spread,
        extreme_spread_no_block_threshold_f=risk_limits.extreme_spread_no_block_threshold_f,
        block_no_on_model_source_degraded=risk_limits.block_no_on_model_source_degraded,
        model_authoritative=model_authoritative,
        model_authoritative_tight_spread_f=model_authoritative_tight_spread_f,
        model_authoritative_wide_spread_f=model_authoritative_wide_spread_f,
        model_authoritative_extreme_spread_f=model_authoritative_extreme_spread_f,
        allow_cheap_ask_yes_with_missing_bid=allow_cheap_ask_yes_with_missing_bid,
        cheap_ask_yes_max_cents=cheap_ask_yes_max_cents,
        cheap_ask_yes_min_net_edge_cents=cheap_ask_yes_min_net_edge_cents,
        cheap_ask_yes_max_contracts=cheap_ask_yes_max_contracts,
        high_spread_reduce_size_factor=risk_limits.high_spread_reduce_size_factor,
        clustered_disputed_extra_edge_cents=risk_limits.clustered_disputed_extra_edge_cents,
        edge_comparison_epsilon_cents=edge_comparison_epsilon_cents,
    )


def _load_yaml_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    if not config_path.exists():
        raise typer.BadParameter(f"config file not found: {path}")
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise typer.BadParameter(f"config file must contain a YAML mapping: {path}")
    return data


def _profile_risk_from_config(
    profile_name: str,
    profile_config: dict[str, Any],
    fallback: ProfileRiskConfig,
) -> ProfileRiskConfig:
    profile_rows = profile_config.get("profiles") if isinstance(profile_config.get("profiles"), dict) else {}
    raw = profile_rows.get(profile_name) if isinstance(profile_rows, dict) else None
    if not isinstance(raw, dict):
        return fallback
    valid_keys = set(ProfileRiskConfig.__dataclass_fields__)
    overrides = {key: value for key, value in raw.items() if key in valid_keys}
    return replace(fallback, **overrides)


def _profile_window_details(
    profile_name: str,
    profile_config: dict[str, Any],
    now_local: datetime,
) -> dict[str, Any]:
    defaults = {
        "overnight_next_day": ("18:00", "05:30"),
        "morning_pre_observation": ("05:30", "09:00"),
        "active_nowcast": ("09:00", "13:30"),
        "late_day_risk_manage": ("13:30", "16:30"),
        "close_only": ("16:30", "18:00"),
        "post_close": ("18:00", "23:59"),
        "risk_reduce": (None, None),
    }
    profiles = profile_config.get("profiles") if isinstance(profile_config.get("profiles"), dict) else {}
    raw = profiles.get(profile_name) if isinstance(profiles, dict) else {}
    start_text = str((raw or {}).get("start_local") or (defaults.get(profile_name) or (None, None))[0] or "")
    end_text = str((raw or {}).get("end_local") or (defaults.get(profile_name) or (None, None))[1] or "")
    if not start_text or not end_text:
        return {
            "profile_start_local": None,
            "profile_end_local": None,
            "minutes_until_profile_end": None,
        }
    start_hour, start_minute = (int(part) for part in start_text.split(":", 1))
    end_hour, end_minute = (int(part) for part in end_text.split(":", 1))
    start_dt = now_local.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_dt = now_local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if end_dt <= start_dt:
        if now_local < end_dt:
            start_dt -= timedelta(days=1)
        else:
            end_dt += timedelta(days=1)
    minutes_until_end = max(0, int(math.ceil((end_dt - now_local).total_seconds() / 60.0)))
    return {
        "profile_start_local": start_text,
        "profile_end_local": end_text,
        "minutes_until_profile_end": minutes_until_end,
    }


def _profile_requested_observation_elimination(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "true_only_if_fresh_station_matched"}


def _target_date_relation(station_local_date: date, target_date_value: date) -> str:
    delta_days = (target_date_value - station_local_date).days
    if delta_days < 0:
        return "past"
    if delta_days == 0:
        return "today"
    if delta_days == 1:
        return "tomorrow"
    return "future"


def _observation_elimination_effective_from_freshness(freshness: dict[str, Any]) -> bool:
    return bool(
        freshness.get("observation_available")
        and not freshness.get("observation_stale")
        and freshness.get("observation_station_matches_settlement")
        and freshness.get("latest_observation_time_utc")
        and _float_or_none(freshness.get("observed_high_so_far_f")) is not None
    )


def _risk_limits_with_profile(base: RiskLimits, profile_risk: ProfileRiskConfig) -> RiskLimits:
    return replace(
        base,
        min_edge_cents=float(profile_risk.min_edge_cents),
        max_contracts_per_trade=int(profile_risk.max_contracts_per_trade),
        max_risk_dollars_per_trade=float(profile_risk.max_risk_dollars_per_trade),
        max_total_exposure_dollars=float(profile_risk.max_total_exposure_dollars),
        max_open_positions=int(profile_risk.max_open_positions),
        max_open_orders=int(profile_risk.max_open_orders),
        max_total_open_risk_groups=int(profile_risk.max_total_open_risk_groups),
    )


def _edge_risk_config_with_profile(base: EdgeRiskConfig, profile_risk: ProfileRiskConfig) -> EdgeRiskConfig:
    return replace(
        base,
        min_edge_cents=float(profile_risk.min_edge_cents),
        min_yes_edge_cents=float(profile_risk.min_edge_cents),
        min_no_edge_cents=float(profile_risk.min_no_edge_cents),
        min_no_upside_cents=float(profile_risk.min_no_upside_cents),
        max_no_bin_probability=float(profile_risk.max_no_bin_probability),
        max_contracts_per_trade=int(profile_risk.max_contracts_per_trade),
        max_risk_dollars_per_trade=float(profile_risk.max_risk_dollars_per_trade),
        max_total_exposure_dollars=float(profile_risk.max_total_exposure_dollars),
        max_open_positions=int(profile_risk.max_open_positions),
        max_open_orders=int(profile_risk.max_open_orders),
        max_total_open_risk_groups=int(profile_risk.max_total_open_risk_groups),
    )


def _model_source_diagnostics_from_context(context: Any) -> dict[str, Any]:
    summary = getattr(context, "recent_price_trend_summary", None) or {}
    if isinstance(summary, dict):
        diagnostics = summary.get("model_source")
        if isinstance(diagnostics, dict):
            return diagnostics
    return {}


def _risk_limits_with_model_source_degradation(
    base: RiskLimits,
    diagnostics: dict[str, Any],
    *,
    edge_buffer_cents: float,
    size_factor: float,
) -> RiskLimits:
    if not diagnostics.get("model_source_degraded"):
        return base
    return replace(
        base,
        min_edge_cents=float(base.min_edge_cents) + float(edge_buffer_cents),
        max_contracts_per_trade=max(1, int(float(base.max_contracts_per_trade) * float(size_factor))),
    )


def _edge_risk_config_with_model_source_degradation(
    base: EdgeRiskConfig,
    diagnostics: dict[str, Any],
    *,
    edge_buffer_cents: float,
    size_factor: float,
) -> EdgeRiskConfig:
    if not diagnostics.get("model_source_degraded"):
        return base
    return replace(
        base,
        min_edge_cents=float(base.min_edge_cents) + float(edge_buffer_cents),
        min_yes_edge_cents=float(base.min_yes_edge_cents) + float(edge_buffer_cents),
        min_no_edge_cents=float(base.min_no_edge_cents) + float(edge_buffer_cents),
        max_contracts_per_trade=max(1, int(float(base.max_contracts_per_trade) * float(size_factor))),
        block_high_confidence_no_on_extreme_spread=True,
        extreme_spread_no_block_threshold_f=0.0,
    )


def _profile_decision_for_context(
    context: Any,
    *,
    portfolio: dict[str, Any],
    risk_limits: RiskLimits,
    risk_config: EdgeRiskConfig,
    profile_mode: str,
    profile_config: dict[str, Any],
    previous_profile: str | None,
    forced_active_profile: str | None = None,
) -> ProfileDecision | None:
    if profile_mode in {"fixed", "fixed_test"}:
        return None
    context_payload = context.to_dict() if hasattr(context, "to_dict") else dict(context or {})
    consensus = _model_consensus_summary(context)
    freshness = _debug_data_freshness(context_payload, risk_config)
    tz_name = str(profile_config.get("station_timezone") or LAX_TIMEZONE)
    tz = ZoneInfo(tz_name)
    now_utc = _parse_utc_datetime(context_payload.get("current_time_utc")) or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tz)
    target_raw = context_payload.get("market_date")
    target_date_local = datetime.combine(
        date.fromisoformat(str(target_raw)) if target_raw else now_local.date(),
        datetime.min.time(),
        tzinfo=tz,
    )
    max_groups = risk_limits.max_total_open_risk_groups if risk_limits.max_total_open_risk_groups is not None else risk_limits.max_open_positions
    inputs = ProfileInputs(
        now_local=now_local,
        target_date_local=target_date_local,
        observation_available=bool(freshness.get("observation_available")),
        observation_stale=bool(freshness.get("observation_stale")),
        observation_station_matches_settlement=bool(freshness.get("observation_station_matches_settlement", True)),
        latest_observation_time_utc=str(freshness.get("latest_observation_time_utc") or "") or None,
        observed_high_so_far_f=_float_or_none(freshness.get("observed_high_so_far_f")),
        model_disagreement_level=str(consensus.get("model_disagreement_level") or "low"),
        model_cluster_status=str(consensus.get("model_cluster_status") or "moderate_consensus"),
        full_model_spread_f=_float_or_none(consensus.get("full_model_spread_f")),
        open_pnl_dollars=float(portfolio.get("open_pnl_value") or 0.0),
        total_open_risk_groups=int(portfolio.get("total_open_risk_groups") or 0),
        max_risk_groups_reached=int(portfolio.get("total_open_risk_groups") or 0) >= int(max_groups or 0),
        worst_case_loss_dollars=0.0,
    )
    decision = select_profile(inputs, previous_profile=previous_profile)
    if forced_active_profile:
        forced_profile = str(forced_active_profile).strip()
        configured_profiles = profile_config.get("profiles") if isinstance(profile_config.get("profiles"), dict) else {}
        known_profiles = set(DEFAULT_PROFILES) | set(configured_profiles)
        if forced_profile in known_profiles and forced_profile != decision.active_profile:
            decision = replace(
                decision,
                active_profile=forced_profile,
                profile_reason=f"lifecycle selected {forced_profile}",
                profile_reason_code="lifecycle_profile_override",
                profile_changed_this_iteration=(previous_profile != forced_profile),
                profile_overrides_applied={
                    **decision.profile_overrides_applied,
                    "lifecycle_active_profile": forced_profile,
                    "auto_selected_profile": decision.active_profile,
                },
            )
    profile_risk_fallback = DEFAULT_PROFILES.get(decision.active_profile, decision.effective_risk_config)
    configured_risk = _profile_risk_from_config(
        decision.active_profile,
        profile_config,
        profile_risk_fallback,
    )
    requested_elimination = configured_risk.allow_observation_elimination
    effective_elimination = _observation_elimination_effective_from_freshness(freshness) and _profile_requested_observation_elimination(
        requested_elimination
    )
    dynamic_overrides = dict(decision.dynamic_overrides_applied)
    dynamic_reason = decision.dynamic_override_reason
    if not effective_elimination:
        configured_risk = replace(
            configured_risk,
            allow_observation_elimination=False,
            effective_allow_observation_elimination=False,
        )
        if _profile_requested_observation_elimination(requested_elimination):
            dynamic_overrides["stale_or_missing_observation"] = {
                "profile_requested_allow_observation_elimination": requested_elimination,
                "effective_allow_observation_elimination": False,
            }
            dynamic_reason = "stale_or_missing_observation"
    else:
        configured_risk = replace(
            configured_risk,
            effective_allow_observation_elimination=True,
        )
    window = _profile_window_details(decision.active_profile, profile_config, now_local)
    profile_reason = decision.profile_reason
    target_relation = _target_date_relation(now_local.date(), target_date_local.date())
    profile_reason_code = decision.profile_reason_code or str(profile_reason)
    if target_relation in {"tomorrow", "future"} and decision.active_profile == "overnight_next_day":
        profile_reason_code = f"target_date_{target_relation}"
        profile_reason = (
            f"target date {target_date_local.date().isoformat()} is {target_relation} relative to "
            f"station local date {now_local.date().isoformat()}; using overnight_next_day conservative profile"
        )
    elif str(profile_reason).startswith("time_window:"):
        profile_reason = (
            f"station local time {now_local.strftime('%H:%M')} is inside "
            f"{decision.active_profile} window"
        )
    elif profile_reason == "drawdown_risk_reduce":
        profile_reason = "drawdown threshold triggered risk_reduce"
    elif profile_reason == "drawdown_close_only":
        profile_reason = "drawdown threshold triggered close_only"
    elif profile_reason == "settlement_concentration":
        profile_reason = "settlement scenario concentration triggered risk_reduce"
    elif profile_reason == "max_risk_groups_reached":
        profile_reason = "max risk groups reached; disabling new entries"
    return replace(
        decision,
        profile_reason=profile_reason,
        profile_reason_code=profile_reason_code,
        target_date_relation=target_relation,
        effective_risk_config=configured_risk,
        dynamic_overrides_applied=dynamic_overrides,
        station_timezone=tz_name,
        station_local_time=now_local.strftime("%H:%M"),
        station_local_date=now_local.date().isoformat(),
        target_date=target_date_local.date().isoformat(),
        profile_start_local=window.get("profile_start_local"),
        profile_end_local=window.get("profile_end_local"),
        minutes_until_profile_end=window.get("minutes_until_profile_end"),
        base_cli_config={
            "max_open_positions": risk_limits.max_open_positions,
            "max_open_orders": risk_limits.max_open_orders,
            "max_total_open_risk_groups": risk_limits.max_total_open_risk_groups,
            "max_risk_dollars_per_trade": risk_limits.max_risk_dollars_per_trade,
            "max_total_exposure_dollars": risk_limits.max_total_exposure_dollars,
            "min_edge_cents": risk_limits.min_edge_cents,
        },
        profile_requested_allow_observation_elimination=requested_elimination,
        effective_allow_observation_elimination=effective_elimination,
        dynamic_override_reason=dynamic_reason,
    )


def _profile_payload(decision: ProfileDecision | None, *, mode: str, config_path: str | None) -> dict[str, Any]:
    if decision is None:
        profile_name = "fixed_test" if mode == "fixed_test" else "fixed"
        return {
            "profile_mode": mode,
            "active_profile": profile_name,
            "profile_reason": profile_name,
            "profile_reason_code": profile_name,
            "target_date_relation": None,
            "profile_config": config_path,
            "profile_changed_this_iteration": False,
            "effective_risk_config": None,
            "profile_overrides_applied": {},
            "dynamic_overrides_applied": {},
        }
    return {
        "profile_mode": mode,
        "active_profile": decision.active_profile,
        "previous_profile": decision.previous_profile,
        "profile_reason": decision.profile_reason,
        "profile_reason_code": decision.profile_reason_code,
        "target_date_relation": decision.target_date_relation,
        "profile_changed_this_iteration": decision.profile_changed_this_iteration,
        "profile_overrides_applied": decision.profile_overrides_applied,
        "dynamic_overrides_applied": decision.dynamic_overrides_applied,
        "dynamic_override_reason": decision.dynamic_override_reason,
        "profile_config": config_path,
        "station_timezone": decision.station_timezone,
        "station_local_time": decision.station_local_time,
        "station_local_date": decision.station_local_date,
        "target_date": decision.target_date,
        "minutes_until_profile_end": decision.minutes_until_profile_end,
        "profile_start_local": decision.profile_start_local,
        "profile_end_local": decision.profile_end_local,
        "base_cli_config": decision.base_cli_config,
        "profile_requested_allow_observation_elimination": decision.profile_requested_allow_observation_elimination,
        "effective_allow_observation_elimination": decision.effective_allow_observation_elimination,
        "effective_risk_config": asdict(decision.effective_risk_config),
    }


def _runtime_diagnostics_summary(
    iterations: list[dict[str, Any]],
    *,
    requested_duration_minutes: float | None,
    requested_interval_seconds: int,
    expected_iterations: int,
    run_started_at_utc: datetime,
    run_ended_at_utc: datetime | None = None,
) -> dict[str, Any]:
    ended = run_ended_at_utc or datetime.now(timezone.utc)
    elapsed_values = [
        float(row.get("iteration_elapsed_seconds") or 0.0)
        for row in iterations
        if row.get("iteration_elapsed_seconds") is not None
    ]
    slow_threshold = max(30.0, float(requested_interval_seconds) * 1.5)
    slow_rows = [row for row in iterations if float(row.get("iteration_elapsed_seconds") or 0.0) >= slow_threshold]
    slowest = max(
        iterations,
        key=lambda row: float(row.get("iteration_elapsed_seconds") or 0.0),
        default={},
    )
    return {
        "requested_duration_minutes": requested_duration_minutes,
        "requested_interval_seconds": requested_interval_seconds,
        "expected_iterations": expected_iterations,
        "iterations_requested_or_expected": expected_iterations,
        "actual_iterations": len(iterations),
        "iterations_completed": len(iterations),
        "run_started_at_utc": run_started_at_utc.isoformat(),
        "run_ended_at_utc": ended.isoformat(),
        "actual_wall_clock_minutes": round((ended - run_started_at_utc).total_seconds() / 60.0, 4),
        "avg_iteration_seconds": round(sum(elapsed_values) / len(elapsed_values), 4) if elapsed_values else None,
        "median_iteration_seconds": round(statistics.median(elapsed_values), 4) if elapsed_values else None,
        "max_iteration_seconds": round(max(elapsed_values), 4) if elapsed_values else None,
        "slow_iteration_count": len(slow_rows),
        "slow_iteration_threshold_seconds": slow_threshold,
        "slowest_iteration_number": slowest.get("iteration"),
        "slowest_iteration_reason_if_available": slowest.get("slowest_iteration_reason_if_available"),
        "first_iteration_utc": iterations[0].get("iteration_started_at_utc") if iterations else None,
        "last_iteration_utc": iterations[-1].get("iteration_ended_at_utc") if iterations else None,
    }


def _edge_strategy_config(*, strategy: str, order_style: str, decision_mode: str) -> EdgeStrategyConfig:
    return EdgeStrategyConfig(strategy=strategy, decision_mode=decision_mode, order_style=order_style)


def _edge_quote_from_bracket(bracket: Any) -> EdgeMarketQuote:
    return EdgeMarketQuote(
        bracket_label=_canonical_bracket_label(
            getattr(bracket, "bracket_label", None),
            lower_f=getattr(bracket, "lower_f", None),
            upper_f=getattr(bracket, "upper_f", None),
        ),
        yes_bid_cents=_int_cents_or_none(getattr(bracket, "yes_bid_cents", None)),
        yes_ask_cents=_int_cents_or_none(getattr(bracket, "yes_ask_cents", None)),
        no_bid_cents=_int_cents_or_none(getattr(bracket, "no_bid_cents", None)),
        no_ask_cents=_int_cents_or_none(getattr(bracket, "no_ask_cents", None)),
        ts=None,
        liquidity_score=None,
    )


def _edge_freshness_metadata_for_context(context: Any, risk_config: EdgeRiskConfig) -> dict[str, Any]:
    now = _parse_utc_datetime(getattr(context, "current_time_utc", None)) or datetime.now(timezone.utc)
    model_times = [
        parsed
        for parsed in (
            _parse_utc_datetime(getattr(estimate, "generated_at_utc", None))
            for estimate in getattr(context, "model_estimates", []) or []
        )
        if parsed is not None
    ]
    model_ts = max(model_times).isoformat() if model_times else getattr(context, "current_time_utc", None)
    observation_ts = getattr(context, "latest_observation_time_utc", None)
    observed_high = _float_or_none(getattr(context, "observed_high_so_far_f", None))
    report = assess_freshness(
        now=now,
        market_ts=getattr(context, "current_time_utc", None),
        model_ts=model_ts,
        observation_ts=observation_ts,
        config=FreshnessConfig(
            max_market_age_seconds=risk_config.max_market_age_seconds,
            max_model_age_seconds=risk_config.max_model_age_seconds,
            max_observation_age_seconds=risk_config.max_observation_age_seconds,
        ),
    )
    metadata = report.as_candidate_metadata()
    observation_available = observed_high is not None and observation_ts is not None
    observation_stale = True if not observation_available else bool(metadata.get("observation_stale"))
    station_matches = True
    effective_elimination = bool(
        observation_available
        and not observation_stale
        and station_matches
        and observation_ts
        and observed_high is not None
    )
    metadata.update(
        {
            "observation_available": observation_available,
            "observation_status": "available" if observation_available and not observation_stale else ("stale" if observation_available else "missing"),
            "observation_source": getattr(context, "official_settlement_source", None),
            "observation_station_matches_settlement": station_matches,
            "observed_high_so_far_f": observed_high,
            "latest_observation_time_utc": observation_ts,
            "observation_age_seconds": None if not observation_available else metadata.get("observation_age_seconds"),
            "observation_stale": observation_stale,
            "profile_requested_allow_observation_elimination": None,
            "effective_allow_observation_elimination": effective_elimination,
            "observation_elimination_allowed": effective_elimination,
        }
    )
    return metadata


def _edge_elimination_labels_for_context(context: Any) -> set[str]:
    observed_high = _float_or_none(getattr(context, "observed_high_so_far_f", None))
    observation_ts = getattr(context, "latest_observation_time_utc", None)
    if observed_high is None or observation_ts is None:
        return set()
    eliminated: set[str] = set()
    for bracket in getattr(context, "market_brackets", []) or []:
        upper_f = _float_or_none(getattr(bracket, "upper_f", None))
        if upper_f is not None and observed_high > upper_f:
            eliminated.add(edge_canonicalize_label(_canonical_bracket_label(getattr(bracket, "bracket_label", None))))
    return eliminated


def _int_cents_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _edge_probabilities_from_context(context: Any) -> dict[str, float]:
    probabilities: dict[str, float] = {}
    for row in getattr(context, "probability_bins", []) or []:
        label = edge_canonicalize_label(
            _canonical_bracket_label(
                getattr(row, "bracket_label", None),
                lower_f=getattr(row, "lower_f", None),
                upper_f=getattr(row, "upper_f", None),
            )
        )
        try:
            probabilities[label] = float(getattr(row, "probability"))
        except (TypeError, ValueError):
            continue
    return probabilities


def _market_distribution_for_context(context: Any) -> dict[str, Any]:
    quotes = [
        ExtMarketQuote(
            label=edge_canonicalize_label(
                _canonical_bracket_label(
                    getattr(bracket, "bracket_label", None),
                    lower_f=getattr(bracket, "lower_f", None),
                    upper_f=getattr(bracket, "upper_f", None),
                )
            ),
            yes_bid=_float_or_none(getattr(bracket, "yes_bid_cents", None)),
            yes_ask=_float_or_none(getattr(bracket, "yes_ask_cents", None)),
            no_bid=_float_or_none(getattr(bracket, "no_bid_cents", None)),
            no_ask=_float_or_none(getattr(bracket, "no_ask_cents", None)),
        )
        for bracket in getattr(context, "market_brackets", []) or []
    ]
    distribution = normalize_market_distribution(quotes)
    rows = [
        {
            "bracket": row.label,
            "raw_market_mid_probability": row.raw_mid_probability,
            "normalized_market_probability": row.normalized_probability,
        }
        for row in distribution.probabilities
    ]
    return {
        "probabilities": rows,
        "probability_by_bracket": {row["bracket"]: row["normalized_market_probability"] for row in rows},
        "raw_probability_by_bracket": {row["bracket"]: row["raw_market_mid_probability"] for row in rows},
        "market_probability_sum": distribution.raw_sum,
        "normalized_market_probability_sum": distribution.normalized_sum,
        "overround_or_gap": distribution.overround_or_gap,
    }


def _blend_probabilities_for_context(
    raw_probabilities: dict[str, float],
    *,
    market_distribution: dict[str, Any],
    active_profile: str,
    model_disagreement_level: str,
    probability_blend_mode: str,
    probability_blend_config: dict[str, Any] | None = None,
    model_source_degraded: bool = False,
    degraded_model_weight_factor: float = 0.75,
) -> tuple[dict[str, float], dict[str, Any]]:
    market_probs = market_distribution.get("probability_by_bracket") or {}
    blended: dict[str, float] = {}
    rows: dict[str, dict[str, Any]] = {}
    defaults = (probability_blend_config or {}).get("defaults") or {}
    profiles = (probability_blend_config or {}).get("profiles") or {}
    overrides = (probability_blend_config or {}).get("overrides") or {}
    for label, raw_probability in raw_probabilities.items():
        market_probability = _float_or_none(market_probs.get(label))
        if market_probability is None:
            market_probability = raw_probability
        if probability_blend_mode == "model_only":
            model_weight = 1.0
            market_weight = 0.0
            final_probability = raw_probability
            blend = {
                "raw_model_probability": raw_probability,
                "calibrated_model_probability": raw_probability,
                "market_implied_probability": market_probability,
                "model_weight": model_weight,
                "market_weight": market_weight,
                "final_trade_probability": final_probability,
                "p_model_yes": raw_probability,
                "p_market_yes": market_probability,
                "p_used_yes": final_probability,
                "p_used_source": "model_consensus_probability",
                "fair_value_source": "model_consensus_probability",
                "fair_yes_model_only_cents": 100.0 * raw_probability,
                "fair_no_model_only_cents": 100.0 * (1.0 - raw_probability),
                "fair_yes_blended_cents": 100.0 * final_probability,
                "fair_no_blended_cents": 100.0 * (1.0 - final_probability),
                "probability_blend_reason": "model_authoritative:model_only",
                "station_lead_time_skill_score": None,
            }
        elif probability_blend_mode == "raw":
            model_weight = 1.0
            reasons = ["probability_blend_mode=raw"]
            if model_source_degraded:
                model_weight = max(0.0, min(1.0, model_weight * degraded_model_weight_factor))
                reasons.append(f"model_source_degraded_weight_factor={degraded_model_weight_factor:.2f}")
            market_weight = 1.0 - model_weight
            final_probability = model_weight * raw_probability + market_weight * market_probability
            blend = {
                "raw_model_probability": raw_probability,
                "calibrated_model_probability": raw_probability,
                "market_implied_probability": market_probability,
                "model_weight": model_weight,
                "market_weight": market_weight,
                "final_trade_probability": final_probability,
                "p_model_yes": raw_probability,
                "p_market_yes": market_probability,
                "p_used_yes": final_probability,
                "p_used_source": "probability_blend",
                "fair_value_source": "probability_blend",
                "fair_yes_model_only_cents": 100.0 * raw_probability,
                "fair_no_model_only_cents": 100.0 * (1.0 - raw_probability),
                "fair_yes_blended_cents": 100.0 * final_probability,
                "fair_no_blended_cents": 100.0 * (1.0 - final_probability),
                "probability_blend_reason": "; ".join(reasons),
                "station_lead_time_skill_score": None,
            }
        elif probability_blend_config:
            profile_weight = _float_or_none((profiles.get(active_profile) or {}).get("model_weight"))
            model_weight = profile_weight
            if model_weight is None:
                model_weight = _float_or_none(defaults.get("model_weight_no_calibration"))
            if model_weight is None:
                model_weight = 0.35
            reasons = [f"profile_config={active_profile}:{model_weight:.2f}"]
            if model_disagreement_level == "high":
                adjustment = _float_or_none((overrides.get("model_disagreement_high") or {}).get("model_weight_add"))
                if adjustment is not None:
                    model_weight += adjustment
                    reasons.append(f"high_disagreement:{adjustment:+.2f}")
            elif model_disagreement_level == "extreme":
                adjustment = _float_or_none((overrides.get("model_disagreement_extreme") or {}).get("model_weight_add"))
                if adjustment is not None:
                    model_weight += adjustment
                    reasons.append(f"extreme_disagreement:{adjustment:+.2f}")
            min_weight = _float_or_none(defaults.get("min_model_weight"))
            max_weight = _float_or_none(defaults.get("max_model_weight"))
            model_weight = max(min_weight if min_weight is not None else 0.20, model_weight)
            model_weight = min(max_weight if max_weight is not None else 0.75, model_weight)
            if model_source_degraded:
                model_weight = max(min_weight if min_weight is not None else 0.20, model_weight * degraded_model_weight_factor)
                reasons.append(f"model_source_degraded_weight_factor={degraded_model_weight_factor:.2f}")
            market_weight = 1.0 - model_weight
            final_probability = model_weight * raw_probability + market_weight * market_probability
            blend = {
                "raw_model_probability": raw_probability,
                "calibrated_model_probability": raw_probability,
                "market_implied_probability": market_probability,
                "model_weight": model_weight,
                "market_weight": market_weight,
                "final_trade_probability": final_probability,
                "p_model_yes": raw_probability,
                "p_market_yes": market_probability,
                "p_used_yes": final_probability,
                "p_used_source": "probability_blend",
                "fair_value_source": "probability_blend",
                "fair_yes_model_only_cents": 100.0 * raw_probability,
                "fair_no_model_only_cents": 100.0 * (1.0 - raw_probability),
                "fair_yes_blended_cents": 100.0 * final_probability,
                "fair_no_blended_cents": 100.0 * (1.0 - final_probability),
                "probability_blend_reason": "; ".join(reasons),
                "station_lead_time_skill_score": None,
            }
        else:
            result = blend_probability(
                raw_probability,
                market_probability,
                active_profile,
                model_disagreement_level=model_disagreement_level,
                station_skill_score=None,
            )
            model_weight = result.model_weight
            reason = result.probability_blend_reason
            if model_source_degraded:
                model_weight = max(0.0, min(1.0, model_weight * degraded_model_weight_factor))
                reason = f"{reason}; model_source_degraded_weight_factor={degraded_model_weight_factor:.2f}"
            market_weight = 1.0 - model_weight
            final_probability = model_weight * raw_probability + market_weight * market_probability
            blend = {
                "raw_model_probability": result.raw_model_probability,
                "calibrated_model_probability": result.calibrated_model_probability,
                "market_implied_probability": result.market_implied_probability,
                "model_weight": model_weight,
                "market_weight": market_weight,
                "final_trade_probability": final_probability,
                "p_model_yes": result.raw_model_probability,
                "p_market_yes": result.market_implied_probability,
                "p_used_yes": final_probability,
                "p_used_source": "probability_blend",
                "fair_value_source": "probability_blend",
                "fair_yes_model_only_cents": 100.0 * result.raw_model_probability,
                "fair_no_model_only_cents": 100.0 * (1.0 - result.raw_model_probability),
                "fair_yes_blended_cents": 100.0 * final_probability,
                "fair_no_blended_cents": 100.0 * (1.0 - final_probability),
                "probability_blend_reason": reason,
                "station_lead_time_skill_score": None,
            }
        blended[label] = final_probability
        rows[label] = {
            **blend,
            "model_minus_market_probability": raw_probability - market_probability,
        }
    return blended, {
        "probability_blend_mode": probability_blend_mode,
        "active_profile": active_profile,
        "config_loaded": bool(probability_blend_config),
        "by_bracket": rows,
    }


def _model_family(provider: str) -> str:
    text = provider.lower()
    if "gfs" in text:
        return "gfs"
    if "hrrr" in text or "rap" in text:
        return "hrrr_rap"
    if "nbm" in text:
        return "nbm"
    if "best_match" in text or "current_weighted_blend" in text or text.startswith("current:"):
        return "open_meteo_blend"
    if "ecmwf" in text or "aifs" in text:
        return "ecmwf_ai"
    if "nam" in text:
        return "nam"
    return text.split(":", 1)[0] or "other"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _conflict_level(spread_f: float) -> str:
    if spread_f < 2:
        return "low"
    if spread_f < 4:
        return "medium"
    if spread_f < 8:
        return "high"
    return "extreme"


def _model_disagreement_rank(level: Any) -> int | None:
    mapping = {"low": 0, "medium": 1, "high": 2, "extreme": 3}
    return mapping.get(str(level or "").lower())


def _iqr(values: list[float]) -> float:
    if len(values) < 4:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    lower = ordered[:mid]
    upper = ordered[mid:] if len(ordered) % 2 == 0 else ordered[mid + 1 :]
    if not lower or not upper:
        return 0.0
    return float(statistics.median(upper) - statistics.median(lower))


def _model_consensus_summary(
    context: Any,
    *,
    enabled: bool = True,
    consensus_method: str = "family_weighted_iqr",
    outlier_threshold_f: float = 4.0,
    consensus_max_spread_f: float = 3.0,
    full_spread_high_threshold_f: float = 5.0,
    full_spread_extreme_threshold_f: float = 8.0,
) -> dict[str, Any]:
    raw_models: list[dict[str, Any]] = []
    for estimate in getattr(context, "model_estimates", []) or []:
        temp = _float_or_none(getattr(estimate, "high_f", None))
        provider = str(getattr(estimate, "provider", "") or "")
        if temp is None:
            continue
        raw_models.append(
            {
                "provider": provider,
                "high_f": temp,
                "generated_at_utc": getattr(estimate, "generated_at_utc", None),
                "family": _model_family(provider),
                "notes": getattr(estimate, "notes", None),
            }
        )
    if not raw_models:
        return {
            "raw_model_count": 0,
            "family_count": 0,
            "consensus_model_count": 0,
            "excluded_model_count": 0,
            "raw_models": [],
            "model_families": {},
            "family_weighted_models": [],
            "consensus_models": [],
            "excluded_outliers": [],
            "credible_outliers": [],
            "consensus_center_f": None,
            "consensus_spread_f": None,
            "full_model_min_f": None,
            "full_model_max_f": None,
            "full_model_spread_f": None,
            "full_model_std_f": None,
            "model_disagreement_level": "extreme",
            "model_cluster_status": "insufficient_models",
            "model_confidence_level": "low",
            "clustered_but_disputed": False,
            "outlier_method": consensus_method,
            "consensus_method": consensus_method,
            "notes": ["no model estimates available"],
        }

    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in raw_models:
        families[str(row["family"])].append(row)
    family_internal_metrics: dict[str, dict[str, Any]] = {}
    for family, rows in sorted(families.items()):
        values = [float(row["high_f"]) for row in rows]
        spread = max(values) - min(values) if len(values) >= 2 else 0.0
        family_internal_metrics[family] = {
            "family": family,
            "family_min_f": round(min(values), 4),
            "family_max_f": round(max(values), 4),
            "family_spread_f": round(spread, 4),
            "family_std_f": round(statistics.pstdev(values), 4) if len(values) >= 2 else 0.0,
            "family_internal_conflict_level": _conflict_level(spread),
            "family_internal_conflict": spread >= 4.0,
            "members": [row["provider"] for row in rows],
        }
    family_weighted = [
        {
            "family": family,
            "high_f": float(statistics.mean([float(row["high_f"]) for row in rows])),
            "members": [row["provider"] for row in rows],
            **family_internal_metrics[family],
        }
        for family, rows in sorted(families.items())
    ]
    family_values = [float(row["high_f"]) for row in family_weighted] if enabled else [float(row["high_f"]) for row in raw_models]
    center = _median(family_values)
    if center is None:
        center = float(statistics.mean([float(row["high_f"]) for row in raw_models]))
    iqr = _iqr(family_values)
    deviations = [abs(value - center) for value in family_values]
    mad = statistics.median(deviations) if deviations else 0.0
    robust_band = max(float(outlier_threshold_f), 1.5 * iqr, 2.5 * mad)

    consensus_models: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in family_weighted:
        deviation = abs(float(row["high_f"]) - center)
        enriched = {**row, "deviation_from_center_f": round(deviation, 4)}
        if enabled and deviation > robust_band:
            excluded.append(enriched)
        else:
            consensus_models.append(enriched)
    if not consensus_models:
        consensus_models = [{**row, "deviation_from_center_f": abs(float(row["high_f"]) - center)} for row in family_weighted]
        excluded = []

    trusted_outlier_families = {"hrrr_rap", "nbm"}
    credible = [row for row in excluded if str(row.get("family")) in trusted_outlier_families]
    conflicted_families = [
        row for row in family_internal_metrics.values() if row.get("family_internal_conflict")
    ]
    credible_internal_outliers = [
        row for row in conflicted_families if str(row.get("family")) in trusted_outlier_families
    ]
    consensus_values = [float(row["high_f"]) for row in consensus_models]
    raw_values = [float(row["high_f"]) for row in raw_models]
    raw_center = statistics.median(raw_values)
    raw_near_center_count = sum(1 for value in raw_values if abs(value - raw_center) <= 2.0)
    raw_clustered_with_dispute = raw_near_center_count >= max(2, len(raw_values) - 1)
    consensus_spread = max(consensus_values) - min(consensus_values) if len(consensus_values) >= 2 else 0.0
    full_spread = max(raw_values) - min(raw_values) if len(raw_values) >= 2 else 0.0
    if full_spread < 3:
        disagreement = "low"
    elif full_spread < full_spread_high_threshold_f:
        disagreement = "medium"
    elif full_spread < full_spread_extreme_threshold_f:
        disagreement = "high"
    else:
        disagreement = "extreme"
    if len(raw_values) < 2 or len(family_weighted) < 2:
        cluster = "insufficient_models"
    elif consensus_spread <= 2 and full_spread <= 3:
        cluster = "tight_consensus"
    elif (consensus_spread <= 2 or raw_clustered_with_dispute) and full_spread > 4:
        cluster = "clustered_but_disputed"
    elif consensus_spread <= consensus_max_spread_f:
        cluster = "moderate_consensus"
    elif consensus_spread > consensus_max_spread_f:
        cluster = "wide_consensus"
    else:
        cluster = "no_consensus"
    confidence = "high" if cluster == "tight_consensus" else "medium" if cluster in {"moderate_consensus", "wide_consensus"} else "low"
    return {
        "raw_model_count": len(raw_models),
        "family_count": len(family_weighted),
        "consensus_model_count": len(consensus_models),
        "excluded_model_count": len(excluded),
        "raw_models": raw_models,
        "model_families": {family: [row["provider"] for row in rows] for family, rows in families.items()},
        "family_internal_metrics": family_internal_metrics,
        "family_weighted_models": family_weighted,
        "consensus_models": consensus_models,
        "excluded_outliers": excluded,
        "credible_outliers": credible,
        "conflicted_families": conflicted_families,
        "family_internal_conflict": bool(conflicted_families),
        "credible_internal_outliers": credible_internal_outliers,
        "consensus_center_f": round(float(statistics.mean(consensus_values)), 4) if consensus_values else None,
        "consensus_spread_f": round(consensus_spread, 4),
        "full_model_min_f": round(min(raw_values), 4),
        "full_model_max_f": round(max(raw_values), 4),
        "full_model_spread_f": round(full_spread, 4),
        "full_model_std_f": round(statistics.pstdev(raw_values), 4) if len(raw_values) >= 2 else 0.0,
        "model_disagreement_level": disagreement,
        "model_cluster_status": cluster,
        "model_confidence_level": confidence,
        "clustered_but_disputed": cluster == "clustered_but_disputed",
        "outlier_method": consensus_method,
        "consensus_method": consensus_method,
        "notes": [
            "consensus spread used for central confidence",
            "full model spread retained for tail risk and NO safety",
        ],
    }


def _apply_probability_floors(
    probabilities: dict[str, float],
    *,
    consensus: dict[str, Any],
    min_non_eliminated_bin_probability: float,
    min_tail_probability_when_disputed: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    before = {label: float(value) for label, value in probabilities.items()}
    if not before:
        return before, {
            "probabilities_before_floor": {},
            "probabilities_after_floor": {},
            "probability_floor_applied": False,
            "probability_floor_reason": "no probabilities",
            "renormalization_factor": None,
            "final_probability_sum": 0.0,
            "final_probability_sum_error": 1.0,
            "top_bracket_before_floor": None,
            "top_bracket_after_floor": None,
        }
    disputed = bool(consensus.get("clustered_but_disputed")) or str(consensus.get("model_disagreement_level")) in {"high", "extreme"}
    floor = float(min_tail_probability_when_disputed if disputed else min_non_eliminated_bin_probability)
    after = dict(before)
    applied = False
    for label, value in list(after.items()):
        if value < floor:
            after[label] = floor
            applied = True
    total = sum(after.values())
    factor = None
    if total > 0:
        factor = 1.0 / total
        after = {label: value * factor for label, value in after.items()}
    final_sum = sum(after.values())
    return after, {
        "probabilities_before_floor": before,
        "probabilities_after_floor": after,
        "probability_floor_applied": applied,
        "probability_floor_reason": "disputed_tail_floor" if disputed else "minimum_non_eliminated_floor",
        "renormalization_factor": factor,
        "final_probability_sum": final_sum,
        "final_probability_sum_error": abs(1.0 - final_sum),
        "top_bracket_before_floor": max(before, key=before.get),
        "top_bracket_after_floor": max(after, key=after.get) if after else None,
    }


def _model_uncertainty_penalties(consensus: dict[str, Any], *, base_tail_risk_padding_cents: float) -> dict[str, Any]:
    full_spread = _float_or_none(consensus.get("full_model_spread_f")) or 0.0
    dispersion = 0.0
    if full_spread >= 8:
        dispersion += 6.0
    elif full_spread >= 6:
        dispersion += 4.0
    elif full_spread >= 4:
        dispersion += 2.0
    credible_count = len(consensus.get("credible_outliers") or [])
    credible_penalty = min(3.0, float(credible_count))
    internal_conflict_count = len(consensus.get("conflicted_families") or [])
    family_internal_penalty = min(4.0, 2.0 * float(internal_conflict_count))
    return {
        "base_tail_risk_padding_cents": base_tail_risk_padding_cents,
        "dispersion_penalty_cents": dispersion,
        "credible_outlier_penalty_cents": credible_penalty,
        "family_internal_conflict_penalty_cents": family_internal_penalty,
        "model_uncertainty_penalty_cents": dispersion + credible_penalty + family_internal_penalty,
        "final_tail_risk_padding_cents": base_tail_risk_padding_cents + dispersion + credible_penalty + family_internal_penalty,
    }


def _model_authoritative_strength(
    consensus: dict[str, Any],
    *,
    model_source_diagnostics: dict[str, Any],
    tight_spread_f: float,
    wide_spread_f: float,
    extreme_spread_f: float,
) -> tuple[str, list[str]]:
    raw_count = int(consensus.get("raw_model_count") or 0)
    family_count = int(consensus.get("family_count") or 0)
    full_spread = _float_or_none(consensus.get("full_model_spread_f"))
    reasons: list[str] = []
    if full_spread is None:
        reasons.append("full_model_spread_missing")
    if bool(model_source_diagnostics.get("model_source_degraded")):
        reasons.append(str(model_source_diagnostics.get("model_source_degraded_reason") or "model_source_degraded"))
    if family_count < 2:
        reasons.append("family_count_below_2")
    if raw_count < 4:
        reasons.append("raw_model_count_below_4")
    if full_spread is not None and full_spread > float(extreme_spread_f):
        reasons.append(f"full_model_spread_above_{float(extreme_spread_f):.1f}F")
    if reasons:
        return "degraded", reasons
    if family_count >= 3 and raw_count >= 6 and full_spread is not None and full_spread <= float(wide_spread_f):
        return "full", ["family_count>=3", "raw_model_count>=6", f"full_model_spread<={float(wide_spread_f):.1f}F"]
    if family_count >= 2 and raw_count >= 4 and full_spread is not None and full_spread <= float(extreme_spread_f):
        return "reduced", ["family_count>=2", "raw_model_count>=4", f"full_model_spread<={float(extreme_spread_f):.1f}F"]
    return "degraded", ["model_authoritative_strength_fallback_degraded"]


def _edge_portfolio_state_from_context(
    context: Any,
    *,
    fills: list[dict[str, Any]],
    portfolio: dict[str, Any],
) -> EdgePortfolioState:
    positions: list[EdgePosition] = []
    for position in getattr(context, "positions", []) or []:
        side_text = str(getattr(position, "side", "") or "").upper()
        if side_text not in {"YES", "NO"}:
            continue
        positions.append(
            EdgePosition(
                bracket_label=_canonical_bracket_label(getattr(position, "bracket_label", None)),
                side=EdgeSide(side_text),
                contracts=max(0, int(getattr(position, "quantity", 0) or 0)),
                avg_price_cents=float(getattr(position, "avg_entry_price_cents", 0) or 0),
            )
        )
    open_orders: list[EdgeOpenOrder] = []
    open_candidate_ids: list[str] = []
    for order in getattr(context, "open_orders", []) or []:
        side_text = str(order.get("side") or "").upper()
        selected_id = str((order.get("metadata") or {}).get("selected_candidate_id") or order.get("selected_candidate_id") or "")
        if selected_id:
            open_candidate_ids.append(selected_id)
        if side_text not in {"YES", "NO"}:
            continue
        if str(order.get("status") or "open").lower() != "open":
            continue
        open_orders.append(
            EdgeOpenOrder(
                candidate_id=selected_id or str(order.get("order_id") or order.get("id") or ""),
                bracket_label=_canonical_bracket_label(order.get("bracket_label") or order.get("bracket")),
                side=EdgeSide(side_text),
                contracts=max(0, int(order.get("quantity") or 0)),
                limit_price_cents=float(order.get("limit_price_cents") or 0),
                created_ts=str(order.get("created_at_utc") or "") or None,
            )
        )
    recent_ids = [
        str(fill.get("selected_candidate_id"))
        for fill in fills[-25:]
        if fill.get("action") == "BUY" and fill.get("selected_candidate_id")
    ]
    return EdgePortfolioState(
        cash_dollars=float(portfolio.get("cash_value") or 0),
        positions=tuple(positions),
        open_orders=tuple(open_orders),
        open_candidate_ids=tuple(open_candidate_ids),
        recent_candidate_ids=tuple(recent_ids),
    )


def _rule_engine_candidates_for_context(
    context: Any,
    *,
    strategy: str,
    decision_mode: str,
    order_style: str,
    cost_config: EdgeCostConfig,
    risk_config: EdgeRiskConfig,
    model_consensus_enabled: bool = True,
    consensus_method: str = "family_weighted_iqr",
    outlier_threshold_f: float = 4.0,
    consensus_max_spread_f: float = 3.0,
    full_spread_high_threshold_f: float = 5.0,
    full_spread_extreme_threshold_f: float = 8.0,
    min_non_eliminated_bin_probability: float = 0.005,
    min_tail_probability_when_disputed: float = 0.01,
    profile_decision: ProfileDecision | None = None,
    probability_blend_mode: str = "raw",
    probability_blend_config: dict[str, Any] | None = None,
) -> list[Any]:
    raw_probabilities = _edge_probabilities_from_context(context)
    consensus = _model_consensus_summary(
        context,
        enabled=model_consensus_enabled,
        consensus_method=consensus_method,
        outlier_threshold_f=outlier_threshold_f,
        consensus_max_spread_f=consensus_max_spread_f,
        full_spread_high_threshold_f=full_spread_high_threshold_f,
        full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
    )
    active_profile = profile_decision.active_profile if profile_decision is not None else "fixed"
    model_source_diagnostics = _model_source_diagnostics_from_context(context)
    model_source_degraded = bool(model_source_diagnostics.get("model_source_degraded"))
    authoritative_strength, authoritative_strength_reasons = _model_authoritative_strength(
        consensus,
        model_source_diagnostics=model_source_diagnostics,
        tight_spread_f=risk_config.model_authoritative_tight_spread_f,
        wide_spread_f=risk_config.model_authoritative_wide_spread_f,
        extreme_spread_f=risk_config.model_authoritative_extreme_spread_f,
    )
    if risk_config.model_authoritative:
        risk_config = replace(risk_config, model_authoritative_strength=authoritative_strength)
    authoritative_strength, authoritative_strength_reasons = _model_authoritative_strength(
        consensus,
        model_source_diagnostics=model_source_diagnostics,
        tight_spread_f=risk_config.model_authoritative_tight_spread_f,
        wide_spread_f=risk_config.model_authoritative_wide_spread_f,
        extreme_spread_f=risk_config.model_authoritative_extreme_spread_f,
    )
    if risk_config.model_authoritative:
        risk_config = replace(risk_config, model_authoritative_strength=authoritative_strength)
    authoritative_strength, authoritative_strength_reasons = _model_authoritative_strength(
        consensus,
        model_source_diagnostics=model_source_diagnostics,
        tight_spread_f=risk_config.model_authoritative_tight_spread_f,
        wide_spread_f=risk_config.model_authoritative_wide_spread_f,
        extreme_spread_f=risk_config.model_authoritative_extreme_spread_f,
    )
    if risk_config.model_authoritative:
        risk_config = replace(risk_config, model_authoritative_strength=authoritative_strength)
    market_distribution = _market_distribution_for_context(context)
    blended_probabilities, probability_blend_debug = _blend_probabilities_for_context(
        raw_probabilities,
        market_distribution=market_distribution,
        active_profile=active_profile,
        model_disagreement_level=str(consensus.get("model_disagreement_level") or "low"),
        probability_blend_mode=probability_blend_mode,
        probability_blend_config=probability_blend_config,
        model_source_degraded=model_source_degraded,
    )
    probabilities, probability_floor = _apply_probability_floors(
        blended_probabilities,
        consensus=consensus,
        min_non_eliminated_bin_probability=min_non_eliminated_bin_probability,
        min_tail_probability_when_disputed=min_tail_probability_when_disputed,
    )
    quotes = [_edge_quote_from_bracket(bracket) for bracket in getattr(context, "market_brackets", []) or []]
    strategy_config = _edge_strategy_config(strategy=strategy, order_style=order_style, decision_mode=decision_mode)
    engine_candidates = build_rule_edge_candidates(
        series=str(getattr(context, "series", "") or ""),
        target_date=str(getattr(context, "market_date", "") or ""),
        probabilities=probabilities,
        quotes=quotes,
        cost_config=cost_config,
        risk_config=risk_config,
        strategy_config=strategy_config,
    )
    freshness_metadata = _edge_freshness_metadata_for_context(context, risk_config)
    eliminated_labels = _edge_elimination_labels_for_context(context)
    uncertainty = _model_uncertainty_penalties(
        consensus,
        base_tail_risk_padding_cents=float(cost_config.tail_risk_padding_cents),
    )
    extra_cluster_edge = (
        float(risk_config.clustered_disputed_extra_edge_cents)
        if consensus.get("clustered_but_disputed")
        else 0.0
    )
    final_tail_padding = float(uncertainty["final_tail_risk_padding_cents"]) + extra_cluster_edge
    repo_candidates = {
        (
            edge_canonicalize_label(
                _canonical_bracket_label(
                    candidate.bracket_label,
                    lower_f=getattr(candidate, "lower_f", None),
                    upper_f=getattr(candidate, "upper_f", None),
                )
            ),
            str(candidate.side or ""),
            str(candidate.action or ""),
        ): candidate
        for candidate in getattr(context, "candidate_trades", []) or []
        if candidate.action == "BUY" and candidate.side in {"YES", "NO"} and candidate.bracket_label
    }
    annotated: list[Any] = []
    for candidate in engine_candidates:
        if candidate.side is None or candidate.bracket_label is None:
            annotated.append(candidate)
            continue
        repo_candidate = repo_candidates.get(
            (edge_canonicalize_label(candidate.bracket_label), candidate.side.value, "BUY")
        )
        if repo_candidate is None:
            annotated.append(candidate.reject("repo_candidate_missing"))
            continue
        label = edge_canonicalize_label(candidate.bracket_label)
        uses_elimination = bool(candidate.side == EdgeSide.NO and label in eliminated_labels)
        quantity = min(int(candidate.quantity or 0), int(repo_candidate.max_contracts or 0))
        if (
            (_float_or_none(consensus.get("full_model_spread_f")) or 0.0) >= float(full_spread_high_threshold_f)
            and risk_config.high_spread_reduce_size_factor < 1
        ):
            quantity = max(1, int(quantity * float(risk_config.high_spread_reduce_size_factor)))
        price = candidate.price_cents
        raw_edge = candidate.raw_edge_cents
        net_edge = None
        if raw_edge is not None:
            net_edge = round(
                float(raw_edge)
                - float(candidate.fee_cents_per_contract or 0)
                - float(candidate.slippage_cents or 0)
                - final_tail_padding,
                4,
            )
        max_loss = 0.0 if price is None else quantity * float(price) / 100.0
        model_uncertainty_block = None
        if (
            candidate.side == EdgeSide.NO
            and risk_config.block_high_confidence_no_on_extreme_spread
            and (_float_or_none(consensus.get("full_model_spread_f")) or 0.0)
            >= float(risk_config.extreme_spread_no_block_threshold_f)
            and not uses_elimination
        ):
            model_uncertainty_block = "model_uncertainty_blocks_high_confidence_no"
        if (
            candidate.side == EdgeSide.NO
            and model_source_degraded
            and risk_config.block_no_on_model_source_degraded
            and not uses_elimination
        ):
            model_uncertainty_block = "model_source_degraded_blocks_high_confidence_no"
        base_tail_padding = float(cost_config.tail_risk_padding_cents)
        extra_tail_padding = final_tail_padding - base_tail_padding
        passive_raw_edge = _float_or_none(candidate.metadata.get("passive_raw_edge_cents"))
        taker_net_edge = _float_or_none(candidate.metadata.get("taker_net_edge_cents"))
        blend_row = (probability_blend_debug.get("by_bracket") or {}).get(label, {})
        market_distribution_row = next(
            (row for row in market_distribution.get("probabilities", []) if row.get("bracket") == label),
            {},
        )
        passive_net_edge = (
            None
            if passive_raw_edge is None
            else round(
                passive_raw_edge
                - float(candidate.fee_cents_per_contract or 0)
                - float(candidate.slippage_cents or 0)
                - final_tail_padding,
                4,
            )
        )
        taker_net_edge = None if taker_net_edge is None else round(taker_net_edge - extra_tail_padding, 4)
        final_note = _rule_buy_edge_note(
            fair_value_cents=candidate.fair_value_cents,
            limit_price_cents=price,
            raw_edge_cents=raw_edge,
            net_edge_cents=net_edge,
            final_tail_padding_cents=final_tail_padding,
            slippage_cents=candidate.slippage_cents,
        )
        thesis_top = str(probability_floor.get("top_bracket_after_floor") or label)
        thesis_position = ThesisPosition(
            bracket=label,
            side=candidate.side.value,
            risk_dollars=max_loss,
        )
        current_thesis_positions = [
            ThesisPosition(
                bracket=edge_canonicalize_label(_canonical_bracket_label(getattr(position, "bracket_label", None))),
                side=str(getattr(position, "side", "") or ""),
                risk_dollars=(
                    int(getattr(position, "quantity", 0) or 0)
                    * float(getattr(position, "avg_entry_price_cents", 0) or 0)
                    / 100.0
                ),
            )
            for position in getattr(context, "positions", []) or []
        ]
        thesis_label = infer_thesis_label(thesis_position, thesis_top)
        thesis_after = evaluate_thesis_exposure(
            [*current_thesis_positions, thesis_position],
            top_bracket=thesis_top,
        )
        thesis_row = next((row for row in thesis_after if row.thesis_label == thesis_label), None)
        profile_block_reason = None
        profile_preferred_action = "BUY"
        profile_allows_candidate = True
        late_day_entry_limit_reason = None
        close_only_blocked_new_buy = False
        if profile_decision is not None:
            allow_new_entries = profile_decision.effective_risk_config.allow_new_entries
            if active_profile in {"close_only", "post_close"} or allow_new_entries is False:
                profile_allows_candidate = False
                close_only_blocked_new_buy = active_profile in {"close_only", "post_close"}
                profile_block_reason = "close_only_new_buy_blocked" if close_only_blocked_new_buy else "profile_blocks_new_entries"
                profile_preferred_action = "CLOSE_CANCEL_HOLD"
            elif active_profile == "late_day_risk_manage" or str(allow_new_entries).lower() == "limited":
                min_profile_edge = (
                    float(profile_decision.effective_risk_config.min_no_edge_cents)
                    if candidate.side == EdgeSide.NO
                    else float(profile_decision.effective_risk_config.min_edge_cents)
                )
                if net_edge is None or net_edge < min_profile_edge + 2.0:
                    profile_allows_candidate = False
                    profile_block_reason = "late_day_new_entry_not_clean_enough"
                    late_day_entry_limit_reason = "late-day entry requires final net edge at least 2c above profile minimum"
                profile_preferred_action = "CLOSE_CANCEL_REDUCE_RISK"
        metadata = {
            **candidate.metadata,
            **freshness_metadata,
            **uncertainty,
            **blend_row,
            "passive_net_edge_cents": passive_net_edge,
            "taker_net_edge_cents": taker_net_edge,
            "final_tail_risk_padding_cents": final_tail_padding,
            "clustered_disputed_extra_edge_cents": extra_cluster_edge,
            "model_consensus_summary": consensus,
            "model_source": model_source_diagnostics,
            "model_source_mode": model_source_diagnostics.get("model_source_mode"),
            "model_source_degraded": model_source_degraded,
            "model_source_degraded_reason": model_source_diagnostics.get("model_source_degraded_reason"),
            "probability_floor": probability_floor,
            "probability_blend": blend_row,
            "market_distribution": market_distribution_row,
            "market_probability_sum": market_distribution.get("market_probability_sum"),
            "overround_or_gap": market_distribution.get("overround_or_gap"),
            "active_profile": active_profile,
            "profile_allows_candidate": profile_allows_candidate,
            "profile_block_reason": profile_block_reason,
            "candidate_blocked_by_profile": not profile_allows_candidate,
            "profile_preferred_action": profile_preferred_action,
            "late_day_entry_limit_reason": late_day_entry_limit_reason,
            "close_only_blocked_new_buy": close_only_blocked_new_buy,
            "uses_elimination": uses_elimination,
            "model_uncertainty_block_reason": model_uncertainty_block,
            "repo_candidate_id": repo_candidate.candidate_id,
            "contract_ticker": repo_candidate.contract_ticker,
            "repo_entry_price_cents": repo_candidate.entry_price_cents,
            "model_disagreement_level_at_post": consensus.get("model_disagreement_level"),
            "full_model_spread_f_at_post": consensus.get("full_model_spread_f"),
            "consensus_spread_f_at_post": consensus.get("consensus_spread_f"),
            "model_cluster_status_at_post": consensus.get("model_cluster_status"),
            "top_bracket_at_post": probability_floor.get("top_bracket_after_floor"),
            "fair_value_cents_at_post": candidate.fair_value_cents,
            "net_edge_cents_at_post": net_edge,
            "thesis_label": thesis_label,
            "thesis_direction": thesis_label.split(":", 1)[0],
            "correlated_positions": [] if thesis_row is None else [asdict(row) for row in thesis_row.correlated_positions],
            "thesis_exposure_score": None if thesis_row is None else thesis_row.correlated_risk_dollars,
            "incremental_thesis_risk": max_loss,
            "thesis_allowed": True if thesis_row is None else thesis_row.thesis_allowed,
            "thesis_rejection_reason": None if thesis_row is None else thesis_row.thesis_rejection_reason,
        }
        if profile_decision is not None and not profile_allows_candidate:
            metadata["candidate_selectable"] = False
            metadata["pre_rejection_reason"] = profile_block_reason or "profile_blocks_new_entries"
            metadata["profile_blocks_new_entries"] = True
        elif model_uncertainty_block:
            metadata["candidate_selectable"] = False
            metadata["pre_rejection_reason"] = model_uncertainty_block
        elif thesis_row is not None and not thesis_row.thesis_allowed:
            metadata["candidate_selectable"] = False
            metadata["pre_rejection_reason"] = "correlated_thesis_exposure_too_high"
        annotated.append(
            replace(
                candidate,
                candidate_id=repo_candidate.candidate_id,
                quantity=quantity,
                max_loss_dollars=max_loss,
                tail_risk_padding_cents=final_tail_padding,
                net_edge_cents=net_edge,
                note=final_note,
                metadata=metadata,
            )
        )
    annotated.extend(
        _rule_cancel_candidates_for_context(
            context,
            current_buy_candidates=annotated,
            risk_config=risk_config,
            consensus=consensus,
            freshness_metadata=freshness_metadata,
            active_profile=active_profile,
        )
    )
    annotated.extend(
        _rule_close_candidates_for_context(
            context,
            consensus=consensus,
            probability_blend_debug=probability_blend_debug,
            active_profile=active_profile,
        )
    )
    return annotated


def _rule_buy_edge_note(
    *,
    fair_value_cents: Any,
    limit_price_cents: Any,
    raw_edge_cents: Any,
    net_edge_cents: Any,
    final_tail_padding_cents: Any,
    slippage_cents: Any,
) -> str:
    def cents(value: Any) -> str:
        number = _float_or_none(value)
        return "--" if number is None else f"{number:.1f}c"

    return (
        f"fair {cents(fair_value_cents)} vs limit {cents(limit_price_cents)}; "
        f"raw edge {cents(raw_edge_cents)}; final net edge {cents(net_edge_cents)} "
        f"after {cents(final_tail_padding_cents)} tail/model padding and {cents(slippage_cents)} slippage"
    )


def _rule_close_candidates_for_context(
    context: Any,
    *,
    consensus: dict[str, Any],
    probability_blend_debug: dict[str, Any],
    active_profile: str,
) -> list[Any]:
    context_payload = context.to_dict() if hasattr(context, "to_dict") else {}
    top_bracket = _trader_top_probability_bracket(context_payload) or ""
    close_candidates = [
        candidate
        for candidate in getattr(context, "candidate_trades", []) or []
        if getattr(candidate, "action", None) == "CLOSE"
    ]
    by_position_key = {
        (
            str(getattr(candidate, "contract_ticker", "") or ""),
            str(getattr(candidate, "side", "") or ""),
        ): candidate
        for candidate in close_candidates
    }
    output: list[Any] = []
    for position in getattr(context, "positions", []) or []:
        ticker = str(getattr(position, "contract_ticker", "") or "")
        side_text = str(getattr(position, "side", "") or "").upper()
        if side_text not in {"YES", "NO"}:
            continue
        repo_candidate = by_position_key.get((ticker, side_text))
        if repo_candidate is None:
            continue
        position_payload = {
            "contract_ticker": ticker,
            "bracket_label": getattr(position, "bracket_label", None),
            "side": side_text,
        }
        exit_price = _market_exit_price_for_position(context_payload, position_payload)
        if exit_price is None:
            continue
        bracket = edge_canonicalize_label(_canonical_bracket_label(getattr(position, "bracket_label", None)))
        blend_row = (probability_blend_debug.get("by_bracket") or {}).get(bracket, {})
        p_yes = _float_or_none(blend_row.get("final_trade_probability"))
        if p_yes is None:
            p_yes = _edge_probabilities_from_context(context).get(bracket, 0.0)
        side_probability = p_yes if side_text == "YES" else 1.0 - p_yes
        fair_value = 100.0 * side_probability
        avg_entry = _float_or_none(getattr(position, "avg_entry_price_cents", None)) or 0.0
        quantity = max(0, int(getattr(position, "quantity", 0) or 0))
        if quantity <= 0:
            continue
        entry = EntryThesis(
            bracket=bracket,
            side=side_text,
            entry_price_cents=avg_entry,
            entry_final_trade_probability=side_probability,
            entry_fair_value_cents=fair_value,
            entry_top_bracket=top_bracket or bracket,
            entry_model_disagreement_level=str(consensus.get("model_disagreement_level") or "low"),
            entry_full_model_spread_f=_float_or_none(consensus.get("full_model_spread_f")),
            active_profile=active_profile,
        )
        current = CurrentThesis(
            current_final_trade_probability=side_probability,
            current_fair_value_cents=fair_value,
            current_top_bracket=top_bracket or bracket,
            current_model_disagreement_level=str(consensus.get("model_disagreement_level") or "low"),
            current_full_model_spread_f=_float_or_none(consensus.get("full_model_spread_f")),
            observation_invalidated=False,
            current_mark_cents=exit_price,
        )
        decision = evaluate_position(entry, current)
        close_reason = "; ".join(decision.close_reasons)
        close_required = (
            active_profile == "risk_reduce"
            or (active_profile == "close_only" and decision.state not in {"conviction_hold", "weak_hold"})
            or decision.state == "close"
        )
        take_profit = decision.state in {"take_profit_watch", "partial_take_profit"}
        if not (close_required or take_profit):
            continue
        close_quantity = quantity
        if take_profit and quantity > 1:
            close_quantity = max(1, math.ceil(quantity * max(0.0, min(1.0, decision.take_profit_fraction or 0.5))))
        net_edge = round(float(exit_price) - avg_entry, 4)
        metadata = {
            "contract_ticker": ticker,
            "candidate_type": "CLOSE",
            "active_profile": active_profile,
            "position_state": "close" if close_required else decision.state,
            "position_quality": decision.state,
            "entry_thesis": asdict(entry),
            "current_thesis": asdict(current),
            "close_reasons": list(decision.close_reasons) or ([f"close_profile_{active_profile}"] if close_required else []),
            "take_profit_target_cents": decision.take_profit_target_cents,
            "take_profit_target_1_cents": decision.take_profit_target_1_cents,
            "take_profit_target_2_cents": decision.take_profit_target_2_cents,
            "take_profit_reached": decision.take_profit_reached,
            "take_profit_fraction": decision.take_profit_fraction,
            "take_profit_reason": decision.take_profit_reason,
            "realized_pnl_if_taken": None if decision.realized_pnl_if_taken is None else round(decision.realized_pnl_if_taken * close_quantity / 100.0, 4),
            "market_moved_against_but_model_still_valid": decision.market_moved_against_but_model_still_valid,
            "probability_decay": decision.probability_decay,
            "fair_value_decay_cents": decision.fair_value_decay_cents,
            "risk_control_priority_score": 5000 if close_required else 3000,
        }
        output.append(
            EdgeCandidateTrade(
                candidate_id=str(getattr(repo_candidate, "candidate_id", "") or f"{ticker}:{side_text}:CLOSE"),
                action=EdgeAction.CLOSE,
                side=EdgeSide(side_text),
                bracket_label=bracket,
                order_type=EdgeOrderType.PASSIVE_LIMIT,
                quantity=close_quantity,
                price_cents=float(exit_price),
                model_probability=side_probability,
                fair_value_cents=fair_value,
                raw_edge_cents=net_edge,
                net_edge_cents=net_edge,
                max_loss_dollars=0.0,
                eligible=True,
                note=close_reason or ("take profit target hit" if take_profit else f"profile {active_profile} risk reduction"),
                metadata=metadata,
            )
        )
    return output


def _rule_cancel_candidates_for_context(
    context: Any,
    *,
    current_buy_candidates: list[Any],
    risk_config: EdgeRiskConfig,
    consensus: dict[str, Any],
    freshness_metadata: dict[str, Any],
    active_profile: str = "fixed",
) -> list[Any]:
    buy_by_id = {
        candidate.candidate_id: candidate
        for candidate in current_buy_candidates
        if getattr(candidate, "candidate_id", None)
    }
    buy_ids = set(buy_by_id)
    brackets_by_ticker = {getattr(bracket, "contract_ticker", None): bracket for bracket in getattr(context, "market_brackets", []) or []}
    top_bracket = _trader_top_probability_bracket(context.to_dict() if hasattr(context, "to_dict") else {})
    cancels: list[Any] = []
    now = _parse_utc_datetime(getattr(context, "current_time_utc", None)) or datetime.now(timezone.utc)
    for order in getattr(context, "open_orders", []) or []:
        if str(order.get("status") or "open").lower() != "open":
            continue
        order_metadata = order.get("metadata") if isinstance(order.get("metadata"), dict) else {}
        selected_id = str(order.get("selected_candidate_id") or order_metadata.get("selected_candidate_id") or "")
        ticker = str(order.get("contract_ticker") or "")
        side_text = str(order.get("side") or "").upper()
        if side_text not in {"YES", "NO"}:
            continue
        side = EdgeSide(side_text)
        bracket = brackets_by_ticker.get(ticker)
        bid = None
        if bracket is not None:
            quote = _edge_quote_from_bracket(bracket)
            bid = quote.bid_for(side)
        limit_price = _float_or_none(order.get("limit_price_cents")) or 0.0
        reasons: list[str] = []
        if active_profile in {"close_only", "post_close"}:
            reasons.append("close_only_force_cancel_open_order")
        if bid is not None and bid - limit_price > risk_config.max_passive_distance_from_bid_cents:
            reasons.append("cancel_lowball_too_far_from_bid")
        created = _parse_utc_datetime(order.get("created_at_utc"))
        if created is not None:
            age_minutes = (now - created).total_seconds() / 60.0
            if age_minutes > risk_config.max_passive_order_age_minutes:
                reasons.append("cancel_order_too_old")
        if freshness_metadata.get("market_stale"):
            reasons.append("cancel_stale_market")
        if freshness_metadata.get("model_stale"):
            reasons.append("cancel_stale_model")
        order_bracket = _canonical_bracket_label(order.get("bracket_label") or order_metadata.get("bracket_label"))
        top_at_post = order_metadata.get("top_bracket_at_post")
        if top_bracket and top_at_post and top_bracket != top_at_post:
            reasons.append("cancel_model_top_changed")
        if selected_id and selected_id not in buy_ids:
            reasons.append("cancel_candidate_not_reproducible")
        current_candidate = buy_by_id.get(selected_id)
        if current_candidate is not None:
            min_edge = (
                risk_config.min_no_edge_cents
                if current_candidate.side == EdgeSide.NO
                else risk_config.min_yes_edge_cents
            )
            current_net_edge = _float_or_none(current_candidate.net_edge_cents)
            if current_net_edge is None or current_net_edge < float(min_edge):
                reasons.append("cancel_edge_no_longer_valid")
        stored_level = order_metadata.get("model_disagreement_level_at_post")
        current_rank = _model_disagreement_rank(consensus.get("model_disagreement_level"))
        stored_rank = _model_disagreement_rank(stored_level)
        stored_full_spread = _float_or_none(order_metadata.get("full_model_spread_f_at_post"))
        current_full_spread = _float_or_none(consensus.get("full_model_spread_f"))
        disagreement_level_worse = current_rank is not None and stored_rank is not None and current_rank > stored_rank
        spread_increase = (
            None
            if current_full_spread is None or stored_full_spread is None
            else current_full_spread - stored_full_spread
        )
        disagreement_hysteresis_f = 1.0
        disagreement_worse_with_hysteresis = (
            disagreement_level_worse
            and spread_increase is not None
            and spread_increase >= disagreement_hysteresis_f
        )
        if disagreement_worse_with_hysteresis:
            reasons.append("cancel_model_disagreement_increased")
        if not reasons:
            continue
        order_id = str(order.get("order_id") or order.get("id") or "")
        cancels.append(
            EdgeCandidateTrade(
                candidate_id=f"{order_id}:CANCEL",
                action=EdgeAction.CANCEL,
                side=side,
                bracket_label=order_bracket,
                order_type=EdgeOrderType.PAPER_ONLY,
                quantity=int(order.get("quantity") or 0),
                price_cents=limit_price,
                net_edge_cents=None,
                eligible=True,
                note="; ".join(dict.fromkeys(reasons)),
                metadata={
                    "contract_ticker": ticker,
                    "cancel_reasons": list(dict.fromkeys(reasons)),
                    "selected_candidate_id": selected_id,
                    "order_id": order_id,
                    "current_bid_cents": bid,
                    "passive_limit_price_cents": limit_price,
                    "model_consensus_summary": consensus,
                    "risk_control_priority_score": 9999,
                    "stored_model_disagreement_level": stored_level,
                    "current_model_disagreement_level": consensus.get("model_disagreement_level"),
                    "stored_full_model_spread_f": stored_full_spread,
                    "current_full_model_spread_f": current_full_spread,
                    "full_model_spread_increase_f": spread_increase,
                    "cancel_model_disagreement_hysteresis_f": disagreement_hysteresis_f,
                },
            )
        )
    return cancels


def _context_with_rule_engine_candidates(
    context: Any,
    *,
    checked_candidates: list[Any],
) -> Any:
    checked_by_id = {candidate.candidate_id: candidate for candidate in checked_candidates}
    updated = []
    for candidate in context.candidate_trades:
        engine = checked_by_id.get(candidate.candidate_id)
        if engine is None or candidate.action != "BUY":
            updated.append(candidate)
            continue
        price_cents = None if engine.price_cents is None else int(round(float(engine.price_cents)))
        updated.append(
            replace(
                candidate,
                entry_price_cents=price_cents,
                model_fair_cents=round(float(engine.fair_value_cents or 0), 4),
                raw_edge_cents=round(float(engine.raw_edge_cents or 0), 4),
                fee_cents=round(float(engine.fee_cents_per_contract or 0), 4),
                fee_adjusted_edge_cents=round(float(engine.net_edge_cents or 0), 4),
                spread_cents=engine.spread_cents,
                max_contracts=max(0, min(int(candidate.max_contracts or 0), int(engine.quantity or 0))),
                risk_dollars=round(float(engine.max_loss_dollars or 0), 2),
                eligible=bool(engine.eligible),
                ineligible_reason=engine.rejection_reason,
                notes=_rule_candidate_note(engine),
            )
        )
    return replace(context, candidate_trades=updated)


def _rule_candidate_note(candidate: Any) -> str:
    if candidate.eligible:
        edge = float(candidate.net_edge_cents or 0)
        return "huge edge" if edge >= 20 else "edge passed"
    return _deterministic_rejection_note(str(candidate.rejection_reason or "fallback HOLD"))


def _rule_decision_for_context(
    context: Any,
    *,
    strategy: str,
    decision_mode: str,
    order_style: str,
    risk_limits: RiskLimits,
    cost_config: EdgeCostConfig,
    risk_config: EdgeRiskConfig,
    portfolio_state: EdgePortfolioState,
    model_consensus_enabled: bool = True,
    consensus_method: str = "family_weighted_iqr",
    outlier_threshold_f: float = 4.0,
    consensus_max_spread_f: float = 3.0,
    full_spread_high_threshold_f: float = 5.0,
    full_spread_extreme_threshold_f: float = 8.0,
    min_non_eliminated_bin_probability: float = 0.005,
    min_tail_probability_when_disputed: float = 0.01,
    profile_decision: ProfileDecision | None = None,
    probability_blend_mode: str = "raw",
    probability_blend_config: dict[str, Any] | None = None,
) -> tuple[TraderRunResult, dict[str, Any]]:
    raw_candidates = _rule_engine_candidates_for_context(
        context,
        strategy=strategy,
        decision_mode=decision_mode,
        order_style=order_style,
        cost_config=cost_config,
        risk_config=risk_config,
        model_consensus_enabled=model_consensus_enabled,
        consensus_method=consensus_method,
        outlier_threshold_f=outlier_threshold_f,
        consensus_max_spread_f=consensus_max_spread_f,
        full_spread_high_threshold_f=full_spread_high_threshold_f,
        full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
        min_non_eliminated_bin_probability=min_non_eliminated_bin_probability,
        min_tail_probability_when_disputed=min_tail_probability_when_disputed,
        profile_decision=profile_decision,
        probability_blend_mode=probability_blend_mode,
        probability_blend_config=probability_blend_config,
    )
    checked_candidates = filter_rule_edge_candidates(raw_candidates, portfolio_state, risk_config)
    context = _context_with_rule_engine_candidates(context, checked_candidates=checked_candidates)
    decision = choose_rule_edge_candidate(checked_candidates, portfolio=portfolio_state, risk_config=risk_config)
    if decision.candidate is None:
        trader_decision = TraderDecision.hold(_rule_hold_reason(decision.reason))
        validation = ValidationResult(valid=True, approved_action=trader_decision.to_dict())
    else:
        candidate = decision.candidate
        if candidate.action == EdgeAction.CANCEL:
            trader_decision = TraderDecision(
                action="CANCEL_FAKE_ORDER",
                selected_candidate_id=candidate.candidate_id,
                contract_ticker=str(candidate.metadata.get("contract_ticker") or ""),
                bracket=str(candidate.bracket_label or ""),
                side=candidate.side.value if candidate.side else None,
                limit_price_cents=None if candidate.price_cents is None else int(round(float(candidate.price_cents))),
                max_contracts=max(0, int(candidate.quantity or 0)),
                estimated_edge_cents=0.0,
                confidence="high",
                time_horizon="intraday",
                trader_thesis=f"Rule engine selected CANCEL because {candidate.note or 'open order is no longer safe'}.",
                why_this_trade="Cancel stale or non-actionable fake passive order before adding risk.",
                why_not_most_likely_bracket="Cancellation is a risk control, not a forecast bet.",
                why_not_other_side="Cancellation removes resting fake order risk.",
                risk_notes="Fake-money only. No real orders are sent.",
            )
        elif candidate.action == EdgeAction.CLOSE:
            trader_decision = TraderDecision(
                action="CLOSE_FAKE_POSITION",
                selected_candidate_id=candidate.candidate_id,
                contract_ticker=str(candidate.metadata.get("contract_ticker") or ""),
                bracket=str(candidate.bracket_label or ""),
                side=candidate.side.value if candidate.side else None,
                limit_price_cents=None if candidate.price_cents is None else int(round(float(candidate.price_cents))),
                max_contracts=max(0, int(candidate.quantity or 0)),
                estimated_edge_cents=float(candidate.net_edge_cents or 0),
                confidence="high" if (candidate.metadata.get("position_state") == "close") else "medium",
                time_horizon="intraday",
                trader_thesis=f"Rule engine selected CLOSE because {candidate.note or 'position risk should be reduced'}.",
                why_this_trade="Close/reduce existing fake position based on deterministic thesis and risk controls.",
                why_not_most_likely_bracket="Closing is risk management, not a new forecast bet.",
                why_not_other_side="Closing reduces existing exposure instead of adding opposite-side risk.",
                risk_notes="Fake-money only. Deterministic close candidate remains the execution gate.",
            )
        else:
            buy_action = (
                "EXECUTE_FAKE_TAKER_BUY"
                if candidate.order_type == EdgeOrderType.TAKER
                or str(candidate.metadata.get("selected_execution_style") or "").lower() == "taker"
                or order_style == "taker"
                else "PLACE_FAKE_LIMIT_BUY"
            )
            trader_decision = TraderDecision(
                action=buy_action,
                selected_candidate_id=candidate.candidate_id,
                contract_ticker=str(candidate.metadata.get("contract_ticker") or ""),
                bracket=str(candidate.bracket_label or ""),
                side=candidate.side.value if candidate.side else None,
                limit_price_cents=None if candidate.price_cents is None else int(round(float(candidate.price_cents))),
                max_contracts=max(0, int(candidate.quantity or 0)),
                estimated_edge_cents=float(candidate.net_edge_cents or 0),
                confidence=_rule_confidence(candidate),
                time_horizon="intraday",
                trader_thesis=(
                    f"Rule engine selected {candidate.side.value if candidate.side else '--'} "
                    f"{candidate.bracket_label} with net edge {candidate.net_edge_cents:.1f}c."
                ),
                why_this_trade="Highest deterministic net edge after fees, spread, slippage, tail padding, and risk filters.",
                why_not_most_likely_bracket="Rules select expected value, not simply the most likely bracket.",
                why_not_other_side="Opposite side did not rank higher after cost and risk filters.",
                risk_notes="Fake-money only. Deterministic portfolio/risk validator remains the execution gate.",
            )
        validation = validate_decision(
            decision=trader_decision,
            candidate_trades=context.candidate_trades,
            risk_limits=risk_limits,
        )
        if validation.valid:
            validation = ValidationResult(
                valid=True,
                approved_action={
                    **validation.approved_action,
                    "validated_engine_candidate_metadata": candidate.metadata,
                },
                fallback_action=validation.fallback_action,
                rejection_reason=validation.rejection_reason,
                warnings=validation.warnings,
            )
    result = TraderRunResult(
        context=context,
        raw_llm_output=None,
        decision=trader_decision,
        validation=validation,
        approved_action=validation.approved_action,
    )
    raw_probabilities = _edge_probabilities_from_context(context)
    market_distribution = _market_distribution_for_context(context)
    profile_name = profile_decision.active_profile if profile_decision is not None else "fixed"
    probability_blend_debug = _blend_probabilities_for_context(
        raw_probabilities,
        market_distribution=market_distribution,
        active_profile=profile_name,
        model_disagreement_level=str((rules_consensus := _model_consensus_summary(
            context,
            enabled=model_consensus_enabled,
            consensus_method=consensus_method,
            outlier_threshold_f=outlier_threshold_f,
            consensus_max_spread_f=consensus_max_spread_f,
            full_spread_high_threshold_f=full_spread_high_threshold_f,
            full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
        )).get("model_disagreement_level") or "low"),
        probability_blend_mode=probability_blend_mode,
        probability_blend_config=probability_blend_config,
    )[1]
    rules_payload = {
        "strategy": strategy,
        "decision_mode": decision_mode,
        "order_style": order_style,
        "cost_config": asdict(cost_config),
        "risk_config": asdict(risk_config),
        "profile": _profile_payload(profile_decision, mode="auto" if profile_decision is not None else "fixed", config_path=None),
        "model_consensus": rules_consensus,
        "market_distribution": market_distribution,
        "probability_blending": probability_blend_debug,
        "decision_reason": decision.reason,
        "candidate_board": [_edge_candidate_to_dict(candidate) for candidate in checked_candidates],
        "selected_candidate": (
            _edge_candidate_to_dict(decision.candidate) if decision.candidate is not None else None
        ),
    }
    return result, rules_payload


def _rule_hold_reason(reason: str) -> str:
    if reason in {"no_candidates", "no_valid_candidate"}:
        return "no clean edge"
    return _deterministic_rejection_note(reason)


def _rule_confidence(candidate: Any) -> str:
    edge = float(candidate.net_edge_cents or 0)
    if edge >= 20:
        return "high"
    if edge >= 8:
        return "medium"
    return "low"


def _edge_candidate_to_dict(candidate: Any) -> dict[str, Any]:
    side = candidate.side.value if candidate.side is not None else None
    action = candidate.action.value if candidate.action is not None else None
    order_type = candidate.order_type.value if candidate.order_type is not None else None
    return {
        "candidate_id": candidate.candidate_id,
        "action": action,
        "side": side,
        "bracket_label": candidate.bracket_label,
        "order_type": order_type,
        "quantity": candidate.quantity,
        "price_cents": candidate.price_cents,
        "model_probability": candidate.model_probability,
        "market_probability": candidate.market_probability,
        "probability_difference": (
            None
            if candidate.model_probability is None or candidate.market_probability is None
            else candidate.model_probability - candidate.market_probability
        ),
        "fair_value_cents": candidate.fair_value_cents,
        "raw_edge_cents": candidate.raw_edge_cents,
        "fee_cents_per_contract": candidate.fee_cents_per_contract,
        "slippage_cents": candidate.slippage_cents,
        "tail_risk_padding_cents": candidate.tail_risk_padding_cents,
        "net_edge_cents": candidate.net_edge_cents,
        "upside_cents": candidate.upside_cents,
        "spread_cents": candidate.spread_cents,
        "max_loss_dollars": candidate.max_loss_dollars,
        "eligible": candidate.eligible,
        "rejection_reason": candidate.rejection_reason,
        "note": candidate.note,
        "metadata": candidate.metadata,
    }


def _canonical_candidate_type(candidate: dict[str, Any]) -> str:
    action = str(candidate.get("action") or "").upper()
    side = str(candidate.get("side") or "-").upper()
    if action == "BUY" and side in {"YES", "NO"}:
        return f"BUY_{side}"
    return action or "UNKNOWN"


def _candidate_rejection_code(reason: Any) -> str:
    raw = str(reason or "eligible").strip()
    lower = raw.lower()
    if raw == "eligible":
        return "eligible"
    if "upside" in lower:
        return "no_upside_below_minimum"
    if "edge" in lower:
        return "net_edge_below_threshold"
    if "spread" in lower:
        return "spread_too_wide"
    if "scale" in lower:
        return "scale_in_blocked"
    if "cooldown" in lower:
        return "cooldown_active"
    if "passive_price_below_best_bid" in lower:
        return "passive_price_below_best_bid"
    if "passive_limit_too_far_below_bid" in lower:
        return "passive_limit_too_far_below_bid"
    if "model_uncertainty_blocks_high_confidence_no" in lower:
        return "model_uncertainty_blocks_high_confidence_no"
    if "observation_elimination_not_allowed" in lower:
        return "observation_elimination_not_allowed"
    if "close_only_new_buy_blocked" in lower:
        return "close_only_new_buy_blocked"
    if "profile_blocks_new_entries" in lower:
        return "profile_blocks_new_entries"
    if "profile_allows_close_only" in lower:
        return "profile_allows_close_only"
    if "late_day_new_entry_not_clean_enough" in lower:
        return "late_day_new_entry_not_clean_enough"
    if "max_open_orders" in lower:
        return "max_open_orders_reached"
    if "max_total_open_risk_groups" in lower:
        return "max_total_open_risk_groups_reached"
    if "stale" in lower:
        return "stale_data"
    if "cash" in lower:
        return "cash_limit"
    if "exposure" in lower:
        return "exposure_limit"
    if "risk" in lower:
        return "risk_limit"
    if "price" in lower:
        return "missing_or_invalid_price"
    if "probability" in lower:
        return "probability_filter"
    if "order" in lower:
        return "order_already_open"
    return lower.replace(" ", "_") or "rejected"


def _candidate_rejection_message(candidate: dict[str, Any], risk_config: EdgeRiskConfig) -> str:
    reason = str(candidate.get("rejection_reason") or "").strip()
    if not reason:
        return "--"
    side = str(candidate.get("side") or "").upper()
    net_edge = _float_or_none(candidate.get("net_edge_cents"))
    spread = _float_or_none(candidate.get("spread_cents"))
    upside = _float_or_none(candidate.get("upside_cents"))
    model_probability = _float_or_none(candidate.get("model_probability"))
    if reason in {"edge_below_threshold", "missing_edge"} or "edge" in reason:
        min_edge = risk_config.min_no_edge_cents if side == "NO" else risk_config.min_yes_edge_cents
        if net_edge is not None and abs(float(net_edge) - float(min_edge)) < 0.05:
            return f"rejected: net_edge {float(net_edge):+.4f}c < min_edge {float(min_edge):+.4f}c"
        return f"rejected: net_edge {_fmt_edge(net_edge)} < min_edge {_fmt_edge(min_edge)}"
    if reason in {"upside_too_small", "no_upside_below_minimum"} or "upside" in reason:
        return (
            f"rejected: no_upside_cents {_fmt_number(upside)} "
            f"< min_no_upside_cents {_fmt_number(risk_config.min_no_upside_cents)}"
        )
    if reason == "spread_too_wide" or "spread" in reason:
        return f"rejected: spread {_fmt_number(spread)}c > max_spread_cents {risk_config.max_spread_cents}"
    if reason == "no_probability_too_high" or "probability" in reason:
        return (
            f"rejected: p_yes {_fmt_percent(model_probability)} "
            f"> max_no_bin_probability {_fmt_percent(risk_config.max_no_bin_probability)}"
        )
    if reason == "model_stale":
        return "rejected: model data stale"
    if reason == "market_stale":
        return "rejected: market data stale"
    if reason == "observation_stale":
        return "rejected: observation data stale"
    if reason == "observation_elimination_not_allowed":
        return "rejected: observation elimination not allowed because observation data is missing or stale"
    if reason == "passive_price_below_best_bid":
        metadata = candidate.get("metadata") or {}
        return str(
            metadata.get("price_actionability_reason")
            or "rejected: max acceptable price is below current best bid; lowball passive order is not actionable"
        )
    if reason == "passive_limit_too_far_below_bid":
        metadata = candidate.get("metadata") or {}
        return str(metadata.get("price_actionability_reason") or "rejected: passive limit too far below current best bid")
    if reason == "model_uncertainty_blocks_high_confidence_no":
        full_spread = ((candidate.get("metadata") or {}).get("model_consensus_summary") or {}).get("full_model_spread_f")
        return (
            f"rejected: full model spread {_fmt_number(full_spread)}F is extreme; "
            "high-confidence NO blocked unless observation-eliminated or calibrated"
        )
    if reason == "max_open_orders_reached":
        return "rejected: max open fake limit orders reached"
    if reason == "max_total_open_risk_groups_reached":
        return "rejected: max total open risk groups reached"
    if reason == "scale_in_blocked":
        return "rejected: already positioned; scale-in disabled"
    if reason == "cooldown":
        return "rejected: same candidate cooldown active"
    return f"rejected: {reason}"


def _rejection_stage_for_code(code: str) -> str:
    if code in {"eligible"}:
        return "eligible"
    if code in {"close_only_new_buy_blocked", "profile_blocks_new_entries", "late_day_new_entry_not_clean_enough"}:
        return "profile"
    if code in {"stale_data"}:
        return "market_data"
    if code in {
        "rejected_missing_bid",
        "rejected_missing_ask",
        "passive_price_below_best_bid",
        "passive_limit_too_far_below_bid",
        "missing_or_invalid_price",
    }:
        return "price_actionability"
    if code in {"net_edge_below_threshold", "no_upside_below_minimum", "spread_too_wide"}:
        return "pricing_edge"
    if code in {"model_uncertainty_blocks_high_confidence_no"}:
        return "model_uncertainty"
    if code in {"probability_filter", "observation_elimination_not_allowed"}:
        return "probability_filter"
    if code in {
        "cash_limit",
        "exposure_limit",
        "risk_limit",
        "max_open_orders_reached",
        "max_total_open_risk_groups_reached",
        "order_already_open",
    }:
        return "risk"
    if code == "scale_in_blocked":
        return "scale_in"
    if code == "cooldown_active":
        return "cooldown"
    return "safety"


def _normalize_candidate_audit_row(row: dict[str, Any], *, is_cancel: bool) -> dict[str, Any]:
    code = str(row.get("rejection_code") or "eligible")
    message = str(row.get("rejection_message") or "--")
    price_actionability = str(row.get("price_actionability") or "")
    eligible = bool(row.get("eligible"))
    if is_cancel:
        for key in (
            "net_edge_cents",
            "raw_edge_cents",
            "passive_net_edge_cents",
            "passive_raw_edge_cents",
            "taker_net_edge_cents",
            "taker_raw_edge_cents",
        ):
            row[key] = None
        row["candidate_score"] = None
        row["risk_control_priority_score"] = row.get("risk_control_priority_score") or 9999
        row["selection_priority"] = row.get("selection_priority") or 9999
        row["deterministic_note"] = "risk-control cancel"
        row["pricing_filter_result"] = "not_applicable"
        row["price_actionability"] = "not_applicable"
        if eligible:
            code = "eligible"
            message = "--"
        row["rejection_code"] = code
        row["rejection_message"] = message
    elif price_actionability == "rejected_missing_bid":
        code = "rejected_missing_bid"
        row["pricing_filter_result"] = "rejected_missing_bid"
        row["eligible"] = False
        row["selectable"] = False
    elif price_actionability == "rejected_missing_ask":
        code = "rejected_missing_ask"
        row["pricing_filter_result"] = "rejected_missing_ask"
        row["eligible"] = False
        row["selectable"] = False
    elif price_actionability == "rejected_below_best_bid":
        code = "passive_price_below_best_bid"
        row["pricing_filter_result"] = "passive_price_below_best_bid"
        row["eligible"] = False
        row["selectable"] = False
    elif code == "passive_price_below_best_bid":
        row["pricing_filter_result"] = "passive_price_below_best_bid"
        row["eligible"] = False
        row["selectable"] = False
    elif code == "scale_in_blocked":
        row["risk_filter_result"] = "scale_in_blocked"
        row["scale_in_filter_result"] = "scale_in_blocked"
    elif code == "net_edge_below_threshold":
        row["pricing_filter_result"] = "net_edge_below_threshold"
    if bool(row.get("candidate_blocked_by_profile")) or row.get("profile_allows_candidate") is False:
        row["selectable"] = False
        if code == "eligible":
            code = str(row.get("profile_block_reason") or "profile_blocks_new_entries")
            row["eligible"] = False

    row["rejection_code"] = code
    if not bool(row.get("eligible")) and message == "--":
        message = str(row.get("price_actionability_reason") or row.get("profile_block_reason") or code)
        row["rejection_message"] = message
    row["primary_rejection_code"] = "eligible" if bool(row.get("eligible")) else code
    row["primary_rejection_message"] = "--" if bool(row.get("eligible")) else message
    row["rejection_stage"] = _rejection_stage_for_code(row["primary_rejection_code"])
    if bool(row.get("eligible")):
        row["all_rejection_reasons"] = []
    elif not row.get("all_rejection_reasons"):
        row["all_rejection_reasons"] = [message]
    return row


def _fmt_number(value: Any, digits: int = 1) -> str:
    number = _float_or_none(value)
    if number is None:
        return "--"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def _fmt_percent(value: Any) -> str:
    number = _float_or_none(value)
    if number is None:
        return "--"
    return f"{number * 100:.1f}%"


def _debug_quote_sources(bracket: dict[str, Any]) -> dict[str, str]:
    return {
        "yes_ask_source": "direct"
        if bracket.get("yes_ask_cents") is not None
        else ("implied_from_no_bid" if bracket.get("no_bid_cents") is not None else "missing"),
        "no_ask_source": "direct"
        if bracket.get("no_ask_cents") is not None
        else ("implied_from_yes_bid" if bracket.get("yes_bid_cents") is not None else "missing"),
    }


def _debug_effective_ask(bracket: dict[str, Any], side: str) -> float | None:
    if side == "YES":
        ask = _float_or_none(bracket.get("yes_ask_cents"))
        if ask is not None:
            return ask
        no_bid = _float_or_none(bracket.get("no_bid_cents"))
        return 100 - no_bid if no_bid is not None else None
    if side == "NO":
        ask = _float_or_none(bracket.get("no_ask_cents"))
        if ask is not None:
            return ask
        yes_bid = _float_or_none(bracket.get("yes_bid_cents"))
        return 100 - yes_bid if yes_bid is not None else None
    return None


def _debug_bracket_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    observed_high = _float_or_none(context.get("observed_high_so_far_f"))
    rows: list[dict[str, Any]] = []
    for bracket in context.get("market_brackets") or []:
        label = _canonical_bracket_label(
            bracket.get("bracket_label"),
            lower_f=bracket.get("lower_f"),
            upper_f=bracket.get("upper_f"),
        )
        upper = _float_or_none(bracket.get("upper_f"))
        eliminated = bool(observed_high is not None and upper is not None and observed_high > upper)
        rows.append(
            {
                "label": label,
                "raw_label": bracket.get("bracket_label"),
                "lower_f": _float_or_none(bracket.get("lower_f")),
                "upper_f": upper,
                "lower_inclusive": True,
                "upper_inclusive": True,
                "contract_ticker": bracket.get("contract_ticker"),
                "event_ticker": bracket.get("event_ticker"),
                "active": True,
                "observation_eliminated": eliminated,
                "elimination_status": "confirmed" if eliminated else ("unknown" if observed_high is None else "not_eliminated"),
            }
        )
    return rows


def _debug_market_rows(context: dict[str, Any], risk_config: EdgeRiskConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bracket in context.get("market_brackets") or []:
        label = _canonical_bracket_label(
            bracket.get("bracket_label"),
            lower_f=bracket.get("lower_f"),
            upper_f=bracket.get("upper_f"),
        )
        yes_bid = _float_or_none(bracket.get("yes_bid_cents"))
        yes_ask = _debug_effective_ask(bracket, "YES")
        no_bid = _float_or_none(bracket.get("no_bid_cents"))
        no_ask = _debug_effective_ask(bracket, "NO")
        sources = _debug_quote_sources(bracket)
        yes_spread = None if yes_bid is None or yes_ask is None else max(0.0, yes_ask - yes_bid)
        no_spread = None if no_bid is None or no_ask is None else max(0.0, no_ask - no_bid)
        rows.append(
            {
                "bracket": label,
                "yes_bid_cents": yes_bid,
                "yes_ask_cents": yes_ask,
                "no_bid_cents": no_bid,
                "no_ask_cents": no_ask,
                "yes_ask_source": sources["yes_ask_source"],
                "no_ask_source": sources["no_ask_source"],
                "yes_spread_cents": yes_spread,
                "no_spread_cents": no_spread,
                "missing_yes_quote": yes_bid is None or yes_ask is None,
                "missing_no_quote": no_bid is None or no_ask is None,
                "wide_yes_spread": yes_spread is not None and yes_spread > risk_config.max_spread_cents,
                "wide_no_spread": no_spread is not None and no_spread > risk_config.max_spread_cents,
                "best_executable_yes_price_cents": yes_ask,
                "best_executable_no_price_cents": no_ask,
                "contract_ticker": bracket.get("contract_ticker"),
            }
        )
    return rows


def _debug_model_section(context: dict[str, Any]) -> dict[str, Any]:
    bins = context.get("probability_bins") or []
    probabilities = [
        {
            "bracket": _canonical_bracket_label(row.get("bracket_label"), lower_f=row.get("lower_f"), upper_f=row.get("upper_f")),
            "p_yes": _float_or_none(row.get("probability")),
            "fair_yes_cents": None if _float_or_none(row.get("probability")) is None else 100 * float(row.get("probability") or 0),
            "fair_no_cents": None if _float_or_none(row.get("probability")) is None else 100 * (1 - float(row.get("probability") or 0)),
        }
        for row in bins
    ]
    prob_sum = sum(float(row.get("p_yes") or 0) for row in probabilities)
    temps = [_float_or_none(row.get("high_f")) for row in context.get("model_estimates") or []]
    temps = [value for value in temps if value is not None]
    top = max(probabilities, key=lambda row: float(row.get("p_yes") or 0), default={})
    return {
        "top_bracket": top.get("bracket"),
        "probability_sum": prob_sum,
        "probability_sum_error": abs(1.0 - prob_sum),
        "distribution_valid": abs(1.0 - prob_sum) <= 0.02,
        "blend_temperature_f": temps[0] if temps else None,
        "model_temperatures": context.get("model_estimates") or [],
        "model_dispersion_f": round(max(temps) - min(temps), 4) if len(temps) >= 2 else None,
        "probabilities": probabilities,
        "calibration_correction": None,
        "outliers": [],
    }


def _debug_data_freshness(context: dict[str, Any], risk_config: EdgeRiskConfig) -> dict[str, Any]:
    now = _parse_utc_datetime(context.get("current_time_utc")) or datetime.now(timezone.utc)
    model_times = [
        parsed
        for parsed in (_parse_utc_datetime(row.get("generated_at_utc")) for row in context.get("model_estimates") or [])
        if parsed is not None
    ]
    model_ts = max(model_times).isoformat() if model_times else context.get("current_time_utc")
    observation_ts = context.get("latest_observation_time_utc")
    observed_high = _float_or_none(context.get("observed_high_so_far_f"))
    report = assess_freshness(
        now=now,
        market_ts=context.get("current_time_utc"),
        model_ts=model_ts,
        observation_ts=observation_ts,
        config=FreshnessConfig(
            max_market_age_seconds=risk_config.max_market_age_seconds,
            max_model_age_seconds=risk_config.max_model_age_seconds,
            max_observation_age_seconds=risk_config.max_observation_age_seconds,
        ),
    )
    metadata = report.as_candidate_metadata()
    observation_available = observed_high is not None and observation_ts is not None
    if not observation_available:
        metadata["observation_age_seconds"] = None
        metadata["observation_stale"] = True
    observation_stale = bool(metadata.get("observation_stale"))
    return {
        **metadata,
        "market_timestamp": context.get("current_time_utc"),
        "model_timestamp": model_ts,
        "observation_timestamp": observation_ts,
        "market_age_minutes": None if metadata.get("market_age_seconds") is None else round(float(metadata["market_age_seconds"]) / 60.0, 2),
        "model_age_minutes": None if metadata.get("model_age_seconds") is None else round(float(metadata["model_age_seconds"]) / 60.0, 2),
        "observation_age_minutes": None if metadata.get("observation_age_seconds") is None else round(float(metadata["observation_age_seconds"]) / 60.0, 2),
        "stale_blocked_candidates": [],
        "station": context.get("station"),
        "target_date": context.get("market_date"),
        "observation_available": observation_available,
        "observation_status": "available" if observation_available and not observation_stale else ("stale" if observation_available else "missing"),
        "observation_source": context.get("official_settlement_source"),
        "observation_station_matches_settlement": True,
        "observed_high_so_far_f": observed_high,
        "latest_observation_time_utc": observation_ts,
        "observation_elimination_allowed": observation_available and not observation_stale,
    }


def _max_acceptable_price(candidate: dict[str, Any], risk_config: EdgeRiskConfig) -> float | None:
    fair = _float_or_none(candidate.get("fair_value_cents"))
    if fair is None:
        return None
    side = str(candidate.get("side") or "").upper()
    min_edge = risk_config.min_no_edge_cents if side == "NO" else risk_config.min_yes_edge_cents
    fee = _float_or_none(candidate.get("fee_cents_per_contract")) or 0.0
    slip = _float_or_none(candidate.get("slippage_cents")) or 0.0
    tail = _float_or_none(candidate.get("tail_risk_padding_cents")) or 0.0
    return round(fair - min_edge - fee - slip - tail, 4)


def _candidate_audit_rows(
    rules_payload: dict[str, Any] | None,
    *,
    context: dict[str, Any],
    risk_config: EdgeRiskConfig,
    selected_candidate_id: str | None,
) -> list[dict[str, Any]]:
    source = (rules_payload or {}).get("candidate_board") or []
    rows: list[dict[str, Any]] = []
    sorted_source = sorted(
        source,
        key=lambda row: (
            0 if row.get("eligible") else 1,
            -float(((row.get("metadata") or {}).get("risk_control_priority_score")) or row.get("net_edge_cents") or -9999),
            -float(row.get("raw_edge_cents") or -9999),
        ),
    )
    for rank, candidate in enumerate(sorted_source, start=1):
        side = str(candidate.get("side") or "").upper()
        label = _canonical_bracket_label(candidate.get("bracket_label"))
        price = _float_or_none(candidate.get("price_cents"))
        metadata = candidate.get("metadata") or {}
        action = str(candidate.get("action") or "").upper()
        is_cancel = action == "CANCEL"
        max_price = _float_or_none(metadata.get("max_acceptable_price_cents"))
        if max_price is None:
            max_price = _max_acceptable_price(candidate, risk_config)
        rejection = _candidate_rejection_message(candidate, risk_config)
        code = _candidate_rejection_code(candidate.get("rejection_reason"))
        if is_cancel and candidate.get("eligible"):
            code = "eligible"
            rejection = "--"
        current_bid = _float_or_none(metadata.get("current_bid_cents"))
        current_ask = _float_or_none(metadata.get("current_ask_cents"))
        taker_price = _float_or_none(metadata.get("executable_taker_price_cents"))
        passive_limit = _float_or_none(metadata.get("passive_limit_price_cents"))
        risk_priority = metadata.get("risk_control_priority_score")
        candidate_score = None if is_cancel else candidate.get("net_edge_cents")
        selection_priority = risk_priority if risk_priority is not None else candidate_score
        deterministic_note = (
            "risk-control cancel"
            if is_cancel
            else (
                "edge passed"
                if candidate.get("eligible")
                else _deterministic_rejection_note(str(candidate.get("rejection_reason") or ""))
            )
        )
        row = {
            "candidate_id": candidate.get("candidate_id"),
            "candidate_type": metadata.get("candidate_type") or _canonical_candidate_type(candidate),
            "bracket": label,
            "side": side or None,
            "order_style": candidate.get("order_type"),
            "quantity_proposed": candidate.get("quantity"),
            "price_proposed_cents": price,
            "current_bid_cents": current_bid,
            "current_ask_cents": current_ask,
            "touch_bid_cents": _float_or_none(metadata.get("touch_bid_cents")),
            "touch_ask_cents": _float_or_none(metadata.get("touch_ask_cents")),
            "executable_taker_price_cents": taker_price,
            "executable_ask_cents": taker_price,
            "selected_execution_style": metadata.get("selected_execution_style"),
            "entry_price_source": metadata.get("entry_price_source"),
            "entry_price_cents": metadata.get("entry_price_cents") if metadata.get("entry_price_cents") is not None else price,
            "eligible_edge_field": metadata.get("eligible_edge_field"),
            "fair_value_cents": candidate.get("fair_value_cents"),
            "market_probability": candidate.get("market_probability"),
            "model_probability": candidate.get("model_probability"),
            "p_model_yes": metadata.get("p_model_yes") if metadata.get("p_model_yes") is not None else candidate.get("model_probability"),
            "p_market_yes": metadata.get("p_market_yes") if metadata.get("p_market_yes") is not None else candidate.get("market_probability"),
            "p_used_yes": metadata.get("p_used_yes") if metadata.get("p_used_yes") is not None else candidate.get("model_probability"),
            "p_used_source": metadata.get("p_used_source"),
            "raw_edge_cents": candidate.get("raw_edge_cents"),
            "passive_raw_edge_cents": metadata.get("passive_raw_edge_cents"),
            "passive_net_edge_cents": metadata.get("passive_net_edge_cents"),
            "taker_raw_edge_cents": metadata.get("taker_raw_edge_cents"),
            "taker_net_edge_cents": metadata.get("taker_net_edge_cents"),
            "estimated_fee_cents": candidate.get("fee_cents_per_contract"),
            "fee_cents_per_contract": candidate.get("fee_cents_per_contract"),
            "spread_cost_cents": None if candidate.get("spread_cents") is None else round(float(candidate.get("spread_cents") or 0) / 2.0, 4),
            "spread_cents": candidate.get("spread_cents"),
            "slippage_cents": candidate.get("slippage_cents"),
            "base_tail_risk_padding_cents": metadata.get("base_tail_risk_padding_cents"),
            "dispersion_penalty_cents": metadata.get("dispersion_penalty_cents"),
            "credible_outlier_penalty_cents": metadata.get("credible_outlier_penalty_cents"),
            "model_uncertainty_penalty_cents": metadata.get("model_uncertainty_penalty_cents"),
            "final_tail_risk_padding_cents": metadata.get("final_tail_risk_padding_cents"),
            "model_uncertainty_block_reason": metadata.get("model_uncertainty_block_reason"),
            "tail_risk_padding_cents": candidate.get("tail_risk_padding_cents"),
            "net_edge_cents": candidate.get("net_edge_cents"),
            "min_required_edge_cents": risk_config.min_no_edge_cents if side == "NO" else risk_config.min_yes_edge_cents,
            "upside_cents": candidate.get("upside_cents"),
            "max_acceptable_price_cents": max_price,
            "passive_limit_price_cents": passive_limit,
            "distance_below_best_bid_cents": metadata.get("distance_below_best_bid_cents"),
            "distance_from_ask_cents": metadata.get("distance_from_ask_cents"),
            "price_actionability": "not_applicable" if is_cancel else metadata.get("price_actionability"),
            "price_actionability_reason": "not_applicable" if is_cancel else metadata.get("price_actionability_reason"),
            "risk_control_priority_score": metadata.get("risk_control_priority_score"),
            "selection_priority": selection_priority,
            "candidate_score": candidate_score,
            "eligible": bool(candidate.get("eligible")),
            "selectable": bool(metadata.get("candidate_selectable", True)) and bool(candidate.get("eligible")),
            "selected": bool(candidate.get("candidate_id") and candidate.get("candidate_id") == selected_candidate_id),
            "selected_rank": rank if candidate.get("candidate_id") == selected_candidate_id else None,
            "risk_dollars": candidate.get("max_loss_dollars"),
            "rejection_code": code,
            "rejection_reason": candidate.get("rejection_reason"),
            "rejection_message": rejection,
            "all_rejection_reasons": [] if candidate.get("eligible") else [rejection],
            "risk_filter_result": "passed" if candidate.get("eligible") or code not in {"cash_limit", "exposure_limit", "risk_limit", "scale_in_blocked", "cooldown_active"} else code,
            "pricing_filter_result": "passed" if candidate.get("eligible") or code not in {"net_edge_below_threshold", "no_upside_below_minimum", "spread_too_wide", "missing_or_invalid_price"} else code,
            "freshness_filter_result": "stale_data" if code == "stale_data" else "passed",
            "scale_in_filter_result": "scale_in_blocked" if code == "scale_in_blocked" else "passed",
            "cooldown_filter_result": "cooldown_active" if code == "cooldown_active" else "passed",
            "deterministic_note": deterministic_note,
            "contract_ticker": metadata.get("contract_ticker"),
            "raw": candidate,
        }
        row = _normalize_candidate_audit_row(row, is_cancel=is_cancel)
        rows.append(row)
    return rows


def _portfolio_reconciliation(portfolio: dict[str, Any]) -> dict[str, Any]:
    cash = _float_or_none(portfolio.get("cash_value"))
    position_value = _float_or_none(portfolio.get("position_value"))
    equity = _float_or_none(portfolio.get("equity_value") or portfolio.get("total_value"))
    expected = None if cash is None or position_value is None else round(cash + position_value, 4)
    diff = None if expected is None or equity is None else round(equity - expected, 4)
    return {
        "formula": "equity = cash + marked_position_value",
        "cash_value": cash,
        "position_value": position_value,
        "reported_equity": equity,
        "expected_equity": expected,
        "difference": diff,
        "reconciles": diff is None or abs(diff) <= 0.02,
        "open_pnl_definition": "lifetime unrealized mark-to-market P/L for open fake positions versus resumed cost basis",
    }


def _position_audit_rows(context: dict[str, Any], positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions:
        quantity = int(position.get("quantity") or 0)
        entry = _float_or_none(position.get("avg_entry_price_cents")) or 0.0
        mark = _market_exit_price_for_position(context, position)
        cost_basis = round(quantity * entry / 100.0, 4)
        market_value = None if mark is None else round(quantity * mark / 100.0, 4)
        unrealized = None if mark is None else round((mark - entry) * quantity / 100.0, 4)
        rows.append(
            {
                "bracket": _canonical_bracket_label(position.get("bracket_label")),
                "side": position.get("side"),
                "quantity": quantity,
                "avg_cost_cents": entry,
                "current_mark_cents": mark,
                "cost_basis_dollars": cost_basis,
                "market_value_dollars": market_value,
                "unrealized_pnl_dollars": unrealized,
                "contract_ticker": position.get("contract_ticker"),
            }
        )
    return rows


def _order_audit_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for order in orders:
        created = _parse_utc_datetime(order.get("created_at_utc"))
        quantity = int(order.get("quantity") or 0)
        limit_price = _float_or_none(order.get("limit_price_cents")) or 0.0
        rows.append(
            {
                "bracket": _canonical_bracket_label(order.get("bracket_label")),
                "side": order.get("side"),
                "quantity": quantity,
                "limit_price_cents": limit_price,
                "reserved_cash_dollars": round(quantity * limit_price / 100.0, 4),
                "age_minutes": None if created is None else round((now - created).total_seconds() / 60.0, 2),
                "status": order.get("status"),
                "contract_ticker": order.get("contract_ticker"),
            }
        )
    return rows


def _build_decision_audit(
    payload: dict[str, Any],
    *,
    race_id: str,
    journal_path: str,
    risk_limits: RiskLimits,
    risk_config: EdgeRiskConfig,
    cost_config: EdgeCostConfig,
    starting_cash: float,
    loaded_existing_portfolio: bool,
    implicit_resume_warning: bool,
    initial_portfolio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = payload.get("context") or {}
    rules_payload = payload.get("rules_engine") or {}
    decision = payload.get("approved_action") or payload.get("decision") or {}
    portfolio = payload.get("portfolio") or {}
    candidates = _candidate_audit_rows(
        rules_payload,
        context=context,
        risk_config=risk_config,
        selected_candidate_id=decision.get("selected_candidate_id"),
    )
    rejection_summary = dict(Counter(row["rejection_code"] for row in candidates if not row.get("eligible")))
    model = _debug_model_section(context)
    if rules_payload.get("model_consensus"):
        consensus_payload = rules_payload.get("model_consensus") or {}
        model["consensus"] = consensus_payload
        model.update(
            {
                key: consensus_payload.get(key)
                for key in [
                    "raw_model_count",
                    "family_count",
                    "consensus_model_count",
                    "excluded_model_count",
                    "consensus_center_f",
                    "consensus_spread_f",
                    "full_model_min_f",
                    "full_model_max_f",
                    "full_model_spread_f",
                    "full_model_std_f",
                    "model_disagreement_level",
                    "model_cluster_status",
                    "model_confidence_level",
                    "clustered_but_disputed",
                    "credible_outliers",
                    "excluded_outliers",
                    "family_internal_metrics",
                    "conflicted_families",
                    "family_internal_conflict",
                    "credible_internal_outliers",
                ]
            }
        )
    floor_payload = None
    for candidate in (rules_payload.get("candidate_board") or []):
        metadata = candidate.get("metadata") or {}
        if metadata.get("probability_floor"):
            floor_payload = metadata.get("probability_floor")
            break
    if floor_payload:
        model["probability_floor_debug"] = floor_payload
        model.update(floor_payload)
    data_freshness = _debug_data_freshness(context, risk_config)
    for row in candidates:
        if row.get("rejection_code") == "stale_data":
            data_freshness["stale_blocked_candidates"].append(row.get("candidate_id"))
    warnings_list: list[str] = []
    if model["probability_sum_error"] > 0.02:
        warnings_list.append(f"probability sum {model['probability_sum']:.4f} differs from 1.0")
    reconciliation = _portfolio_reconciliation(portfolio)
    if not reconciliation["reconciles"]:
        warnings_list.append("portfolio equity does not reconcile to cash + marked position value")
    if implicit_resume_warning:
        warnings_list.append("starting_cash ignored because existing journal portfolio was loaded")
    for warning in payload.get("warnings") or []:
        if warning:
            warnings_list.append(str(warning))
    model_source_payload = payload.get("model_source") or payload.get("model_source_diagnostics") or {}
    for warning in model_source_payload.get("warnings") or []:
        if warning:
            warnings_list.append(str(warning))
    final_action = _trader_action_label(payload)
    eligible = [row for row in candidates if row.get("eligible")]
    paper_execution = payload.get("paper_execution") or {}
    cancel_reason = None
    cancel_execution_message = None
    if final_action == "CANCEL" or paper_execution.get("action") == "CANCEL":
        selected_cancel = next((row for row in candidates if row.get("selected")), None)
        cancel_reasons = ((selected_cancel or {}).get("raw") or {}).get("metadata", {}).get("cancel_reasons", [])
        cancel_reason = "; ".join(cancel_reasons) if cancel_reasons else paper_execution.get("reason")
        cancel_execution_message = (
            f"canceled fake passive order {paper_execution.get('order_id')}"
            if paper_execution.get("executed")
            else paper_execution.get("reason") or "cancel requested"
        )
    return {
        "run": {
            "iteration": payload.get("iteration"),
            "time_utc": context.get("current_time_utc"),
            "time_local": _trader_local_time(payload),
            "mode": "fake_money_only",
            "fake_money_only": True,
            "live_trading_enabled": False,
            "real_orders_available": False,
            "decision_mode": payload.get("decision_mode"),
            "strategy": payload.get("strategy"),
            "order_style": payload.get("order_style"),
            "series": context.get("series"),
            "station": context.get("station"),
            "target_date": context.get("market_date"),
            "race_id": race_id,
            "journal_path": journal_path,
            "loaded_existing_paper_portfolio": loaded_existing_portfolio,
            "starting_cash_requested": starting_cash,
            "actual_portfolio_cash_at_run_start": (initial_portfolio or {}).get("cash_value"),
            "starting_cash_ignored_warning": implicit_resume_warning,
            "lifecycle_active_profile": payload.get("lifecycle_active_profile"),
            "trader_active_profile": payload.get("trader_active_profile"),
            "profile_mode": payload.get("profile_mode"),
            "profile_reason": payload.get("profile_reason"),
            "target_date_relation": payload.get("target_date_relation"),
            "profile_overrides_applied": payload.get("profile_overrides_applied") or {},
            "dynamic_overrides_applied": payload.get("dynamic_overrides_applied") or {},
            **{
                key: value
                for key, value in (payload.get("runtime_diagnostics") or {}).items()
                if key
                in {
                    "iteration_started_at_utc",
                    "iteration_ended_at_utc",
                    "iteration_elapsed_seconds",
                    "model_fetch_elapsed_seconds",
                    "fast_model_fetch_elapsed_seconds",
                    "noaa_fetch_elapsed_seconds",
                    "open_meteo_fetch_elapsed_seconds",
                    "market_fetch_elapsed_seconds",
                    "weather_fetch_elapsed_seconds",
                    "context_fetch_elapsed_seconds",
                    "journal_write_elapsed_seconds",
                    "debug_write_elapsed_seconds",
                }
            },
        },
        "profile": payload.get("profile") or rules_payload.get("profile") or {},
        "model_source": model_source_payload,
        "lifecycle_active_profile": payload.get("lifecycle_active_profile"),
        "trader_active_profile": payload.get("trader_active_profile"),
        "profile_mode": payload.get("profile_mode"),
        "profile_reason": payload.get("profile_reason"),
        "target_date_relation": payload.get("target_date_relation"),
        "effective_risk_config": payload.get("effective_risk_config") or {},
        "profile_overrides_applied": payload.get("profile_overrides_applied") or {},
        "dynamic_overrides_applied": payload.get("dynamic_overrides_applied") or {},
        "data_freshness": data_freshness,
        "brackets": _debug_bracket_rows(context),
        "models": model,
        "market": {
            "brackets": _debug_market_rows(context, risk_config),
            "snapshot_timestamp": context.get("current_time_utc"),
            "market_distribution": rules_payload.get("market_distribution") or payload.get("market_distribution"),
        },
        "probability_blending": rules_payload.get("probability_blending") or payload.get("probability_blending"),
        "portfolio": {
            **portfolio,
            "reconciliation": reconciliation,
            "open_positions_table": _position_audit_rows(context, payload.get("open_positions") or []),
            "open_orders_table": _order_audit_rows(payload.get("open_orders") or []),
            "risk_limits": risk_limits.to_dict(),
            "fees_paid_cents": sum(
                (_float_or_none(row.get("fee_cents")) or 0.0) * (_float_or_none(row.get("quantity")) or 0.0)
                for row in _journal_analysis_rows([payload])
            ),
        },
        "settlement_scenarios": payload.get("settlement_scenarios") or {},
        "thesis_exposure": payload.get("thesis_exposure") or {},
        "clv_summary": payload.get("clv_summary") or {},
        "runtime_diagnostics": payload.get("runtime_diagnostics") or {},
        "debug_cleanup_status": {
            "cancel_net_edge_null": all(row.get("net_edge_cents") is None for row in candidates if row.get("candidate_type") == "CANCEL"),
            "cancel_pricing_not_applicable": all(row.get("pricing_filter_result") == "not_applicable" for row in candidates if row.get("candidate_type") == "CANCEL"),
            "buy_notes_use_final_net_edge": True,
            "fake_money_only": True,
        },
        "risk_limits": risk_limits.to_dict(),
        "cost_config": asdict(cost_config),
        "risk_config": asdict(risk_config),
        "candidates": candidates,
        "eligible_candidates": eligible,
        "rejection_summary": rejection_summary,
        "selected_decision": decision,
        "execution": {
            "final_action": final_action,
            "paper_order": payload.get("paper_order"),
            "paper_execution": payload.get("paper_execution"),
            "paper_order_status": payload.get("paper_order_status"),
            "pending_order_executions": payload.get("pending_order_executions") or [],
            "cancel_reason": cancel_reason,
            "cancel_execution_message": cancel_execution_message,
            "simulated_fill_reason": _debug_execution_reason(payload),
        },
        "warnings": warnings_list,
        "raw_context": context,
    }


def _debug_execution_reason(payload: dict[str, Any]) -> str | None:
    execution = payload.get("paper_execution") or {}
    if _trader_action_label(payload) == "CANCEL" or execution.get("action") == "CANCEL":
        return None
    if execution.get("executed"):
        fill = execution.get("fill") or {}
        return f"simulated fill at {fill.get('price_cents')}c using current market crossed limit"
    if execution.get("status") == "open":
        return f"posted passive fake limit order: {execution.get('reason')}"
    if execution.get("reason"):
        return str(execution.get("reason"))
    return "no fake order requested"


def _top_rejected_candidates(candidates: list[dict[str, Any]], *, key: str, limit: int) -> list[dict[str, Any]]:
    rejected = [row for row in candidates if not row.get("eligible")]
    return sorted(rejected, key=lambda row: _float_or_none(row.get(key)) or -9999, reverse=True)[:limit]


def _decision_audit_text(
    audit: dict[str, Any],
    *,
    show_rejections: str = "summary",
    candidate_table: bool = False,
    candidate_table_limit: int = 12,
    audit_pricing: bool = False,
    audit_portfolio: bool = False,
    audit_data: bool = False,
    show_pricing_table: bool = False,
    show_risk_table: bool = False,
) -> str:
    candidates = audit.get("candidates") or []
    eligible = audit.get("eligible_candidates") or []
    final = (audit.get("execution") or {}).get("final_action") or "HOLD"
    reason = (audit.get("selected_decision") or {}).get("no_trade_reason") or "selected by rules"
    lines = [
        "",
        f"Decision Audit {audit.get('run', {}).get('time_local') or '--'}",
        f"Generated: {len(candidates)} candidates | Eligible: {len(eligible)} | Final: {final} | Reason: {reason}",
    ]
    if audit.get("warnings"):
        lines.extend(["Warnings", *[f"- {warning}" for warning in audit["warnings"]]])
    if audit_data:
        fresh = audit.get("data_freshness") or {}
        lines.extend(
            [
                "",
                "Data freshness",
                (
                    f"market {fresh.get('market_age_minutes')}m stale={fresh.get('market_stale')} | "
                    f"model {fresh.get('model_age_minutes')}m stale={fresh.get('model_stale')} | "
                    f"obs {fresh.get('observation_age_minutes')}m stale={fresh.get('observation_stale')}"
                ),
            ]
        )
    if show_rejections != "none":
        lines.extend(_debug_rejection_text_lines(audit, mode=show_rejections, limit=candidate_table_limit))
    if candidate_table:
        lines.extend(["", "Candidate table", _debug_candidate_table_header()])
        for row in candidates[:candidate_table_limit]:
            lines.append(_debug_candidate_table_row(row))
    if audit_pricing or show_pricing_table:
        lines.extend(_debug_pricing_table_lines(audit, limit=candidate_table_limit if not show_pricing_table else None))
    if audit_portfolio or show_risk_table:
        lines.extend(_debug_portfolio_lines(audit, include_tables=show_risk_table))
    return "\n".join(lines)


def _debug_rejection_text_lines(audit: dict[str, Any], *, mode: str, limit: int) -> list[str]:
    lines = ["", "Rejection summary"]
    summary = audit.get("rejection_summary") or {}
    if not summary:
        lines.append("- none")
    else:
        for reason, count in sorted(summary.items()):
            lines.append(f"{reason}: {count}")
    if mode in {"top", "all"}:
        rejected = [row for row in audit.get("candidates") or [] if not row.get("eligible")]
        if mode == "top":
            rejected = _top_rejected_candidates(rejected, key="net_edge_cents", limit=limit)
        lines.extend(["", "Top rejected candidates", _debug_candidate_table_header()])
        for row in rejected:
            lines.append(_debug_candidate_table_row(row))
    return lines


def _debug_candidate_table_header() -> str:
    return (
        "Bracket  Side  Type            Bid  Ask  Fair  MaxPx  Limit  Dist  "
        "PNet   TNet   Fee  Slip  Tail  Net    Actn                  Rejection"
    )


def _debug_candidate_table_row(row: dict[str, Any]) -> str:
    return (
        f"{_fit_cell(row.get('bracket'), 7)}  "
        f"{_fit_cell(row.get('side') or '-', 4)}  "
        f"{_fit_cell(row.get('candidate_type') or '-', 14)} "
        f"{_fit_cell(_fmt_cents(row.get('current_bid_cents')), 4)} "
        f"{_fit_cell(_fmt_cents(row.get('current_ask_cents')), 4)} "
        f"{_fit_cell(_fmt_cents(row.get('fair_value_cents')), 6)} "
        f"{_fit_cell(_fmt_cents(row.get('max_acceptable_price_cents')), 5)} "
        f"{_fit_cell(_fmt_cents(row.get('passive_limit_price_cents') or row.get('price_proposed_cents')), 5)} "
        f"{_fit_cell(_fmt_cents(row.get('distance_below_best_bid_cents')), 5)} "
        f"{_fit_cell(_fmt_edge(row.get('passive_net_edge_cents')), 6)} "
        f"{_fit_cell(_fmt_edge(row.get('taker_net_edge_cents')), 6)} "
        f"{_fit_cell(_fmt_edge(row.get('estimated_fee_cents')), 4)} "
        f"{_fit_cell(_fmt_edge(row.get('slippage_cents')), 5)} "
        f"{_fit_cell(_fmt_edge(row.get('final_tail_risk_padding_cents') or row.get('tail_risk_padding_cents')), 5)} "
        f"{_fit_cell(_fmt_edge(row.get('net_edge_cents')), 6)} "
        f"{_fit_cell(row.get('price_actionability') or '-', 21)} "
        f"{row.get('rejection_message') or '--'}"
    )


def _debug_pricing_table_lines(audit: dict[str, Any], *, limit: int | None) -> list[str]:
    rows = audit.get("candidates") or []
    if limit is not None:
        rows = rows[:limit]
    lines = [
        "",
        "Pricing audit",
        "Bracket  Side  Bid  Ask  Fair  MktProb  PRaw  PNet  TRaw  TNet  Fee  SpreadCost  Slip  BaseTail  Disp  FinalTail  Net  MinEdge  MaxPx  Limit  Actionability  Eligible",
    ]
    for row in rows:
        lines.append(
            f"{_fit_cell(row.get('bracket'), 7)}  {_fit_cell(row.get('side') or '-', 4)}  "
            f"{_fit_cell(_fmt_cents(row.get('current_bid_cents')), 4)} "
            f"{_fit_cell(_fmt_cents(row.get('current_ask_cents')), 4)} "
            f"{_fit_cell(_fmt_cents(row.get('fair_value_cents')), 5)} "
            f"{_fit_cell(_fmt_percent(row.get('market_probability')), 7)} "
            f"{_fit_cell(_fmt_edge(row.get('passive_raw_edge_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('passive_net_edge_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('taker_raw_edge_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('taker_net_edge_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('estimated_fee_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('spread_cost_cents')), 10)} "
            f"{_fit_cell(_fmt_edge(row.get('slippage_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('base_tail_risk_padding_cents')), 8)} "
            f"{_fit_cell(_fmt_edge(row.get('dispersion_penalty_cents')), 5)} "
            f"{_fit_cell(_fmt_edge(row.get('final_tail_risk_padding_cents')), 9)} "
            f"{_fit_cell(_fmt_edge(row.get('net_edge_cents')), 6)} "
            f"{_fit_cell(_fmt_edge(row.get('min_required_edge_cents')), 8)} "
            f"{_fit_cell(_fmt_cents(row.get('max_acceptable_price_cents')), 5)} "
            f"{_fit_cell(_fmt_cents(row.get('passive_limit_price_cents')), 5)} "
            f"{_fit_cell(row.get('price_actionability') or '-', 14)} "
            f"{row.get('eligible')}"
        )
    return lines


def _debug_portfolio_lines(audit: dict[str, Any], *, include_tables: bool) -> list[str]:
    portfolio = audit.get("portfolio") or {}
    recon = portfolio.get("reconciliation") or {}
    lines = [
        "",
        "Portfolio audit",
        (
            f"cash {portfolio.get('cash')} | reserved {portfolio.get('open_order_exposure_value')} | "
            f"available {portfolio.get('cash_available')} | equity {portfolio.get('equity')} | "
            f"open P/L {portfolio.get('open_pnl')}"
        ),
        (
            f"groups positions={portfolio.get('open_position_groups', 0)} | "
            f"orders={portfolio.get('open_order_groups', 0)} | "
            f"total risk groups={portfolio.get('total_open_risk_groups', 0)}"
        ),
        f"equity formula: {recon.get('formula')} | reconciles={recon.get('reconciles')} | diff={recon.get('difference')}",
        f"Open P/L definition: {recon.get('open_pnl_definition')}",
    ]
    if include_tables:
        lines.extend(["", "Per-bracket exposure"])
        for bracket, exposure in (portfolio.get("exposure_by_bracket") or {}).items():
            lines.append(f"- {_canonical_bracket_label(bracket)}: {_fmt_dollars(exposure)}")
        lines.extend(["", "Open positions"])
        for row in portfolio.get("open_positions_table") or []:
            lines.append(
                f"- {row['bracket']} {row['side']} qty {row['quantity']} avg {_fmt_cents(row['avg_cost_cents'])} "
                f"mark {_fmt_cents(row['current_mark_cents'])} uPnL {_fmt_signed_dollars(row['unrealized_pnl_dollars'])}"
            )
        lines.extend(["", "Open orders"])
        for row in portfolio.get("open_orders_table") or []:
            lines.append(
                f"- {row['bracket']} {row['side']} qty {row['quantity']} limit {_fmt_cents(row['limit_price_cents'])} "
                f"reserved {_fmt_dollars(row['reserved_cash_dollars'])} status {row['status']}"
            )
    return lines


def _trader_debug_last_text(audit: dict[str, Any]) -> str:
    lines = [
        "Kalshi Weather Trader Debug Last",
        "================================",
        (
            f"Run: {audit.get('run', {}).get('race_id')} | Iteration: {audit.get('run', {}).get('iteration')} | "
            f"Time: {audit.get('run', {}).get('time_local')}"
        ),
        (
            f"Final: {(audit.get('execution') or {}).get('final_action')} | "
            f"Decision mode: {audit.get('run', {}).get('decision_mode')} | "
            f"Strategy: {audit.get('run', {}).get('strategy')}"
        ),
        "",
        "Top candidates",
        _debug_candidate_table_header(),
    ]
    for row in (audit.get("candidates") or [])[:8]:
        lines.append(_debug_candidate_table_row(row))
    lines.extend(["", "Rejection summary"])
    for reason, count in sorted((audit.get("rejection_summary") or {}).items()):
        lines.append(f"{reason}: {count}")
    portfolio = audit.get("portfolio") or {}
    recon = portfolio.get("reconciliation") or {}
    lines.extend(
        [
            "",
            "Portfolio",
            f"Cash: {portfolio.get('cash')} | Equity: {portfolio.get('equity')} | Open P/L: {portfolio.get('open_pnl')}",
            f"Reconciles: {recon.get('reconciles')} | Difference: {recon.get('difference')}",
            "",
            "Warnings",
        ]
    )
    lines.extend([f"- {warning}" for warning in audit.get("warnings") or []] or ["- none"])
    return "\n".join(lines)


def _trader_audit_journal_text(payload: dict[str, Any]) -> str:
    snapshot = payload.get("snapshot") or {}
    counts = payload.get("event_counts") or {}
    lines = [
        "Kalshi Weather Trader Journal Audit",
        "===================================",
        f"Journal: {payload.get('journal_path')}",
        f"Resumed state exists: {payload.get('resumed_state_exists')}",
        f"First iteration: {payload.get('first_iteration_time') or '--'}",
        f"Last iteration: {payload.get('last_iteration_time') or '--'}",
        "",
        (
            f"Cash: {snapshot.get('cash')} | Equity: {snapshot.get('equity')} | "
            f"Realized P/L: {snapshot.get('closed_pnl')} | Unrealized P/L: {snapshot.get('open_pnl')}"
        ),
        f"Exposure: {snapshot.get('open_exposure')} | Contracts: {snapshot.get('total_contracts')}",
        f"Fees estimate: {_fmt_edge(payload.get('fees_paid_cents'))}",
        f"Reconciles: {payload.get('reconciliation', {}).get('reconciles')} | Diff: {payload.get('reconciliation', {}).get('difference')}",
        "",
        "Events",
    ]
    for key in ["BUY", "CLOSE", "CANCEL", "HOLD", "REJECT"]:
        lines.append(f"- {key}: {counts.get(key, 0)}")
    lines.extend(["", "Open positions"])
    for row in payload.get("open_positions") or []:
        lines.append(
            f"- {_canonical_bracket_label(row.get('bracket_label'))} {row.get('side')} "
            f"qty {row.get('quantity')} avg {_fmt_cents(row.get('avg_entry_price_cents'))}"
        )
    lines.extend(["", "Open orders"])
    for row in payload.get("open_orders") or []:
        lines.append(
            f"- {_canonical_bracket_label(row.get('bracket_label'))} {row.get('side')} "
            f"qty {row.get('quantity')} limit {_fmt_cents(row.get('limit_price_cents'))} status {row.get('status')}"
        )
    return "\n".join(lines)


def _trader_paper_settlement_text(payload: dict[str, Any]) -> str:
    before = payload.get("portfolio_before") or {}
    after = payload.get("portfolio_after") or {}
    settlement = payload.get("settlement") or {}
    status = "dry run" if payload.get("dry_run") else "executed"
    if settlement.get("blocked"):
        status = f"blocked: {settlement.get('reason') or 'already finalized'}"
    if not settlement.get("settled_positions"):
        status = status if settlement.get("blocked") else "no open positions"
    lines = [
        "Kalshi Weather Paper Settlement",
        "================================",
        "Mode: fake_money_only | Live trading: DISABLED | Real orders: NOT AVAILABLE",
        f"Journal: {payload.get('journal_path')}",
        (
            f"Series: {payload.get('series')} | Station: {payload.get('station')} | "
            f"Target: {payload.get('target_date')}"
        ),
        (
            f"Final high: {_fmt_f(payload.get('final_high_f'))} | "
            f"Winning bracket: {payload.get('winning_bracket') or '--'} | "
            f"Settlement mode: {payload.get('settlement_mode') or '--'} | Status: {status}"
        ),
        "",
        (
            f"Positions settled: {settlement.get('positions_settled', 0)} | "
            f"Contracts: {settlement.get('contracts_settled', 0)} | "
            f"Open orders canceled: {settlement.get('open_orders_canceled', 0)}"
        ),
        (
            f"Settlement value: {_fmt_dollars(settlement.get('settlement_value_dollars'))} | "
            f"Settlement P/L: {_fmt_signed_dollars(settlement.get('realized_pnl_dollars'))}"
        ),
        (
            f"Cash: {before.get('cash') or '--'} -> {after.get('cash') or '--'} | "
            f"Equity: {before.get('equity') or '--'} -> {after.get('equity') or '--'}"
        ),
        (
            f"Closed P/L: {before.get('closed_pnl') or '--'} -> {after.get('closed_pnl') or '--'} | "
            f"Open P/L: {before.get('open_pnl') or '--'} -> {after.get('open_pnl') or '--'}"
        ),
        "",
        "Settled positions",
    ]
    rows = settlement.get("settled_positions") or []
    if not rows:
        lines.append("- none")
    for row in rows:
        result = "win" if int(row.get("settled_result") or 0) else "loss"
        lines.append(
            f"- {row.get('quantity')} {row.get('side')} {row.get('bracket')} "
            f"avg {_fmt_cents(row.get('avg_entry_price_cents'))} -> "
            f"{result} @ {_fmt_cents(row.get('settlement_price_cents'))} | "
            f"P/L {_fmt_signed_dollars(row.get('realized_pnl_dollars'))}"
        )
    return "\n".join(lines)


def _write_debug_audit_files(
    audit: dict[str, Any],
    *,
    debug_output_dir: str | None,
    debug_jsonl: str | None,
    debug_csv: str | None,
) -> dict[str, str]:
    written: dict[str, str] = {}
    safe_audit = safe_console_payload(audit)
    if debug_output_dir:
        out_dir = Path(debug_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        iteration = int((audit.get("run") or {}).get("iteration") or 0)
        iteration_path = out_dir / f"iteration_{iteration:06d}.json"
        write_json_report(iteration_path, safe_audit)
        write_json_report(out_dir / "latest.json", safe_audit)
        written["iteration_json"] = str(iteration_path)
        written["latest_json"] = str(out_dir / "latest.json")
    if debug_jsonl:
        jsonl_path = Path(debug_jsonl)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_audit, sort_keys=True) + "\n")
        written["jsonl"] = str(jsonl_path)
    if debug_csv:
        csv_path = Path(debug_csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        rows = audit.get("candidates") or []
        fieldnames = [
            "iteration",
            "timestamp_utc",
            "time_utc",
            "lifecycle_active_profile",
            "trader_active_profile",
            "profile_mode",
            "active_profile",
            "profile_reason",
            "target_date_relation",
            "effective_risk_config",
            "profile_overrides_applied",
            "dynamic_overrides_applied",
            "candidate_id",
            "candidate_type",
            "contract_ticker",
            "bracket",
            "side",
            "order_style",
            "quantity_proposed",
            "price_proposed_cents",
            "selected",
            "selected_rank",
            "eligible",
            "selectable",
            "candidate_score",
            "selection_priority",
            "risk_control_priority_score",
            "current_bid_cents",
            "current_ask_cents",
            "touch_bid_cents",
            "touch_ask_cents",
            "executable_taker_price_cents",
            "executable_ask_cents",
            "selected_execution_style",
            "entry_price_source",
            "entry_price_cents",
            "eligible_edge_field",
            "passive_limit_price_cents",
            "distance_below_best_bid_cents",
            "distance_from_ask_cents",
            "fair_value_cents",
            "market_probability",
            "model_probability",
            "p_model_yes",
            "p_market_yes",
            "p_used_yes",
            "p_used_source",
            "raw_model_probability",
            "calibrated_model_probability",
            "market_implied_probability",
            "final_trade_probability",
            "model_weight",
            "market_weight",
            "probability_blend_reason",
            "station_lead_time_skill_score",
            "normalized_market_probability",
            "market_probability_sum",
            "overround_or_gap",
            "raw_edge_cents",
            "passive_raw_edge_cents",
            "passive_net_edge_cents",
            "taker_raw_edge_cents",
            "taker_net_edge_cents",
            "estimated_fee_cents",
            "fee_cents_per_contract",
            "slippage_cents",
            "spread_cost_cents",
            "spread_cents",
            "base_tail_risk_padding_cents",
            "final_tail_risk_padding_cents",
            "tail_risk_padding_cents",
            "dispersion_penalty_cents",
            "credible_outlier_penalty_cents",
            "model_uncertainty_penalty_cents",
            "family_internal_conflict_penalty_cents",
            "net_edge_cents",
            "min_required_edge_cents",
            "upside_cents",
            "max_acceptable_price_cents",
            "price_actionability",
            "price_actionability_reason",
            "pricing_filter_result",
            "freshness_filter_result",
            "risk_filter_result",
            "scale_in_filter_result",
            "cooldown_filter_result",
            "profile_allows_candidate",
            "profile_block_reason",
            "candidate_blocked_by_profile",
            "candidate_blocked_by_profile_reason",
            "profile_preferred_action",
            "close_only_blocked_new_buy",
            "late_day_entry_limit_reason",
            "primary_rejection_code",
            "primary_rejection_message",
            "all_rejection_reasons",
            "rejection_stage",
            "rejection_code",
            "rejection_reason",
            "rejection_message",
            "deterministic_note",
            "model_disagreement_level",
            "model_confidence_level",
            "model_cluster_status",
            "consensus_center_f",
            "consensus_spread_f",
            "full_model_spread_f",
            "observation_status",
            "observation_available",
            "observation_stale",
            "observation_elimination_allowed",
            "observed_high_so_far_f",
            "entry_thesis",
            "current_thesis",
            "position_quality",
            "position_state",
            "market_moved_against_but_model_still_valid",
            "take_profit_target_1_cents",
            "take_profit_target_2_cents",
            "take_profit_reached",
            "take_profit_fraction",
            "take_profit_reason",
            "realized_pnl_if_taken",
            "thesis_label",
            "thesis_direction",
            "thesis_exposure_score",
            "incremental_thesis_risk",
            "thesis_allowed",
            "thesis_rejection_reason",
        ]
        exists = csv_path.exists()
        run_time_utc = (audit.get("run") or {}).get("time_utc")
        profile_payload = audit.get("profile") or {}
        candidate_profile_defaults = {
            "lifecycle_active_profile": audit.get("lifecycle_active_profile") or (audit.get("run") or {}).get("lifecycle_active_profile"),
            "trader_active_profile": audit.get("trader_active_profile") or profile_payload.get("active_profile"),
            "profile_mode": audit.get("profile_mode") or profile_payload.get("profile_mode"),
            "active_profile": profile_payload.get("active_profile"),
            "profile_reason": audit.get("profile_reason") or profile_payload.get("profile_reason"),
            "target_date_relation": audit.get("target_date_relation") or profile_payload.get("target_date_relation"),
            "effective_risk_config": audit.get("effective_risk_config") or profile_payload.get("effective_risk_config"),
            "profile_overrides_applied": audit.get("profile_overrides_applied") or profile_payload.get("profile_overrides_applied"),
            "dynamic_overrides_applied": audit.get("dynamic_overrides_applied") or profile_payload.get("dynamic_overrides_applied"),
        }
        with csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "iteration": (audit.get("run") or {}).get("iteration"),
                        "timestamp_utc": run_time_utc,
                        "time_utc": run_time_utc,
                        **{
                            key: row.get(key, candidate_profile_defaults.get(key))
                            for key in fieldnames
                            if key not in {"iteration", "timestamp_utc", "time_utc"}
                        },
                    }
                )
        written["csv"] = str(csv_path)
    return written


def _short_label(value: Any, *, max_len: int = 42) -> str:
    text = str(value or "--").replace("**", "").replace("\n", " ")
    text = text.replace("Will the high temp in LA be ", "")
    if " on " in text and text.endswith("?"):
        text = text.rsplit(" on ", 1)[0]
    return text if len(text) <= max_len else text[: max_len - 1] + "~"


def _fmt_cents(value: Any) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{number:.0f}c"


def _fmt_edge(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):+.1f}c"
    except (TypeError, ValueError):
        return "--"


def _fmt_dollars(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


def _fmt_temp(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.1f}F"
    except (TypeError, ValueError):
        return "--"


def _trader_context_text(context: Any) -> str:
    eligible = [
        candidate
        for candidate in context.candidate_trades
        if candidate.eligible and candidate.action != "HOLD"
    ]
    top = sorted(eligible, key=lambda c: c.fee_adjusted_edge_cents, reverse=True)[:5]
    lines = [
        "KALSHI WEATHER LLM TRADER CONTEXT - FAKE MONEY ONLY",
        f"{context.series} {context.station} | Target: {context.market_date}",
        (
            f"Observed high: {_fmt_temp(context.observed_high_so_far_f)} | "
            f"Latest obs: {context.latest_observation_time_utc or '--'}"
        ),
        (
            f"Models: {len(context.model_estimates)} | Brackets: {len(context.market_brackets)} | "
            f"Candidate trades: {len(context.candidate_trades)} | Eligible: {len(eligible)}"
        ),
        (
            "Risk: "
            f"min edge {_fmt_edge(context.risk_limits.min_edge_cents)} | "
            f"max contracts {context.risk_limits.max_contracts_per_trade} | "
            f"max risk {_fmt_dollars(context.risk_limits.max_risk_dollars_per_trade)}"
        ),
        "Live trading: DISABLED | Real orders: NOT AVAILABLE",
        "",
        "Model Estimates",
    ]
    for estimate in context.model_estimates[:9]:
        lines.append(f"- {_short_label(estimate.provider, max_len=28):28} {_fmt_temp(estimate.high_f)}")
    lines.extend(["", "Market Brackets"])
    lines.append("Bracket                                  P(YES)   YES bid/ask   NO bid/ask")
    for bracket, prob in zip(context.market_brackets, context.probability_bins, strict=False):
        yes = f"{_fmt_cents(bracket.yes_bid_cents)}/{_fmt_cents(bracket.yes_ask_cents)}"
        no = f"{_fmt_cents(bracket.no_bid_cents)}/{_fmt_cents(bracket.no_ask_cents)}"
        lines.append(f"{_short_label(bracket.bracket_label, max_len=38):38} {prob.probability:6.1%}   {yes:11}   {no}")
    lines.extend(["", "Open Fake Positions"])
    if context.positions:
        for position in context.positions:
            lines.append(
                f"- {position.quantity} {position.side} {_short_label(position.bracket_label)} "
                f"@ {_fmt_cents(position.avg_entry_price_cents)}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "Open Fake Limit Orders"])
    if context.open_orders:
        for order in context.open_orders:
            lines.append(
                f"- #{order.get('order_id')} {order.get('action')} {order.get('quantity')} "
                f"{order.get('side')} {_short_label(order.get('bracket_label'))} "
                f"limit {_fmt_cents(order.get('limit_price_cents'))}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "Top Eligible Trades"])
    if top:
        for index, candidate in enumerate(top, start=1):
            price = candidate.entry_price_cents if candidate.action == "BUY" else candidate.exit_price_cents
            lines.append(
                f"{index}. {candidate.action} {candidate.side} {_short_label(candidate.bracket_label)} "
                f"@ {_fmt_cents(price)} | fair {_fmt_cents(candidate.model_fair_cents)} | "
                f"edge {_fmt_edge(candidate.fee_adjusted_edge_cents)} | max {candidate.max_contracts}"
            )
    else:
        lines.append("- none; fallback is HOLD")
    return "\n".join(lines)


def _trader_top_candidates_text(context: dict[str, Any], *, limit: int = 5) -> list[str]:
    candidates = [
        row
        for row in context.get("candidate_trades", [])
        if row.get("eligible") is True and row.get("action") != "HOLD"
    ]
    candidates.sort(key=lambda row: float(row.get("fee_adjusted_edge_cents") or 0), reverse=True)
    lines: list[str] = []
    for index, candidate in enumerate(candidates[:limit], start=1):
        price = candidate.get("entry_price_cents")
        if candidate.get("action") == "CLOSE":
            price = candidate.get("exit_price_cents")
        lines.append(
            f"{index}. {candidate.get('action')} {candidate.get('side')} "
            f"{_short_label(candidate.get('bracket_label'))} @ {_fmt_cents(price)} | "
            f"fair {_fmt_cents(candidate.get('model_fair_cents'))} | "
            f"edge {_fmt_edge(candidate.get('fee_adjusted_edge_cents'))} | "
            f"max {candidate.get('max_contracts')}"
        )
    return lines


def _trader_positions_text(context: dict[str, Any]) -> list[str]:
    positions = context.get("positions") or context.get("open_positions") or []
    if not positions:
        return ["- none"]
    return [
        (
            f"- {position.get('quantity')} {position.get('side')} "
            f"{_short_label(position.get('bracket_label'))} "
            f"@ {_fmt_cents(position.get('avg_entry_price_cents'))}"
        )
        for position in positions
    ]


def _trader_orders_text(context: dict[str, Any]) -> list[str]:
    orders = context.get("open_orders") or []
    if not orders:
        return ["- none"]
    return [
        (
            f"- #{order.get('order_id')} {order.get('action')} {order.get('quantity')} "
            f"{order.get('side')} {_short_label(order.get('bracket_label'))} "
            f"limit {_fmt_cents(order.get('limit_price_cents'))}"
        )
        for order in orders
    ]


def _paper_execution_text(execution: dict[str, Any] | None, status: str | None) -> list[str]:
    if not execution:
        return [f"Status: {status or 'no_fake_order'}"]
    if execution.get("executed"):
        fill = execution.get("fill") or {}
        return [
            "Status: executed fake-money order",
            (
                f"Fill: {fill.get('quantity')} {fill.get('side')} "
                f"{_short_label(fill.get('bracket_label'))} @ {_fmt_cents(fill.get('price_cents'))} "
                f"gross {_fmt_dollars(fill.get('gross_value_dollars'))}"
            ),
        ]
    if execution.get("status") == "open":
        return [
            "Status: pending fake limit order",
            (
                f"Order #{execution.get('order_id')} | limit {_fmt_cents(execution.get('limit_price_cents'))} | "
                f"market {_fmt_cents(execution.get('market_price_cents'))} | {execution.get('reason') or '--'}"
            ),
        ]
    return [f"Status: not executed | {execution.get('reason') or status or '--'}"]


def _trader_result_text(payload: dict[str, Any]) -> str:
    context = payload.get("context") or {}
    decision = payload.get("decision") or {}
    validation = payload.get("validation") or {}
    paper_order = payload.get("paper_order")
    pending = payload.get("pending_order_executions") or []
    no_trade_reason = decision.get("no_trade_reason")
    lines = [
        "KALSHI WEATHER LLM TRADER - FAKE MONEY ONLY",
        f"{context.get('series', '--')} {context.get('station', '--')} | Target: {context.get('market_date') or '--'}",
        (
            f"Observed high: {_fmt_temp(context.get('observed_high_so_far_f'))} | "
            f"Latest obs: {context.get('latest_observation_time_utc') or '--'}"
        ),
        "Live trading: DISABLED | Real orders: NOT AVAILABLE",
        "",
        "Decision",
        f"Action: {decision.get('action') or '--'}",
        f"Candidate: {decision.get('selected_candidate_id') or '--'}",
        f"Confidence: {decision.get('confidence') or '--'} | Edge: {_fmt_edge(decision.get('estimated_edge_cents'))}",
        f"Thesis: {decision.get('trader_thesis') or '--'}",
    ]
    if no_trade_reason:
        lines.append(f"No-trade reason: {no_trade_reason}")
    lines.extend(
        [
            "",
            "Validation",
            f"Passed: {validation.get('valid')}",
            f"Rejection: {validation.get('rejection_reason') or '--'}",
            "",
            "Paper Execution",
            *_paper_execution_text(payload.get("paper_execution"), payload.get("paper_order_status")),
            f"Order requested: {'yes' if paper_order else 'no'}",
        ]
    )
    if pending:
        lines.extend(["", "Pending Order Fills"])
        for row in pending:
            lines.append(
                f"- order #{row.get('order_id')} {row.get('action')} "
                f"{'filled' if row.get('executed') else 'not filled'} | "
                f"{row.get('reason') or '--'}"
            )
    ledger_context = {
        **context,
        "positions": payload.get("open_positions") or context.get("positions") or [],
        "open_orders": payload.get("open_orders") or context.get("open_orders") or [],
    }
    lines.extend(["", "Open Fake Positions", *_trader_positions_text(ledger_context)])
    lines.extend(["", "Open Fake Limit Orders", *_trader_orders_text(ledger_context)])
    portfolio = payload.get("portfolio") or {}
    if portfolio:
        lines.extend(
            [
                "",
                "Portfolio",
                (
                    f"Cash: {portfolio.get('cash') or '--'} | "
                    f"Position value: {portfolio.get('position_value') or '--'} | "
                    f"Equity: {portfolio.get('equity') or portfolio.get('total') or '--'} | "
                    f"Exposure: {portfolio.get('open_exposure') or '--'} | "
                    f"Contracts: {portfolio.get('total_contracts', '--')} | "
                    f"Open P/L: {portfolio.get('open_pnl') or '--'} | "
                    f"Closed P/L: {portfolio.get('closed_pnl') or '--'}"
                ),
            ]
        )
    top = _trader_top_candidates_text(context)
    lines.extend(["", "Top Eligible Trades", *(top or ["- none; fallback is HOLD"])])
    lines.extend(["", "Full JSON: rerun with --json or use --output path.json"])
    return "\n".join(lines)


_TRADER_OUTPUT_STYLES = {"table", "combined", "compact", "readable", "verbose", "json-lines"}
_TRADER_TABLE_COLUMNS = [
    ("time", "Time", 7, "left"),
    ("action", "Action", 6, "left"),
    ("side", "Side", 4, "left"),
    ("bracket", "Bracket", 7, "left"),
    ("px", "Px", 4, "left"),
    ("qty", "Qty", 3, "right"),
    ("edge", "Edge", 6, "right"),
    ("conf", "Conf", 4, "left"),
    ("top", "Top", 6, "left"),
    ("cash", "Cash", 9, "right"),
    ("equity", "Equity", 9, "right"),
    ("pos", "Pos", 3, "right"),
    ("contracts", "Ctr", 5, "right"),
    ("exposure", "Exposure", 8, "right"),
    ("open_pnl", "Open P/L", 8, "right"),
    ("note", "Note", 30, "left"),
]
_TRADER_COMBINED_TABLE_COLUMNS = [
    ("time", "Time", 7, "left"),
    ("action", "Action", 6, "left"),
    ("trade", "Trade", 13, "left"),
    ("order", "Order", 10, "right"),
    ("edge", "Edge", 6, "right"),
    ("conf", "Conf", 4, "left"),
    ("top", "Top", 6, "left"),
    ("cash", "Cash", 9, "right"),
    ("equity", "Equity", 9, "right"),
    ("pos", "Pos", 3, "right"),
    ("contracts", "Ctr", 5, "right"),
    ("exposure", "Exposure", 8, "right"),
    ("open_pnl", "Open P/L", 8, "right"),
    ("note", "Note", 36, "left"),
]


def _fmt_signed_dollars(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "--"
    if abs(number) < 0.005:
        return "$0.00"
    sign = "+" if number > 0 else "-"
    return f"{sign}${abs(number):.2f}"


def _fmt_duration_label(duration_minutes: float | None, max_iterations: int | None) -> str:
    if duration_minutes is not None:
        return f"{duration_minutes:g}m"
    if max_iterations is not None:
        return f"{max_iterations} iteration" + ("" if max_iterations == 1 else "s")
    return "until stopped"


def _fit_cell(value: Any, width: int, *, align: str = "left") -> str:
    text = str(value if value not in (None, "") else "-")
    text = text.replace("\n", " ")
    if len(text) > width:
        text = text[: max(1, width - 1)] + "~"
    if align == "right":
        return f"{text:>{width}}"
    return f"{text:<{width}}"


def _compact_confidence(value: Any) -> str:
    text = str(value or "-").lower()
    if text == "medium":
        return "med"
    if text in {"low", "high"}:
        return text
    return "-"


def _simple_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "--")
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _canonical_bracket_label(value: Any, *, lower_f: Any = None, upper_f: Any = None) -> str:
    if isinstance(value, dict):
        lower_f = value.get("lower_f", lower_f)
        upper_f = value.get("upper_f", upper_f)
        value = value.get("bracket_label") or value.get("bracket") or value.get("label")

    lower_number = _float_or_none(lower_f)
    upper_number = _float_or_none(upper_f)
    if lower_number is None and upper_number is not None:
        return f"<{_simple_number(upper_number + 1)}"
    if lower_number is not None and upper_number is None:
        return f">{_simple_number(lower_number - 1)}"
    if lower_number is not None and upper_number is not None:
        return f"{_simple_number(lower_number)}-{_simple_number(upper_number)}"

    text = _short_label(value, max_len=80)
    cleaned = (
        text.replace("degrees", "")
        .replace("degree", "")
        .replace("deg", "")
        .replace("F", "")
        .replace("f", "")
        .replace("°", "")
        .replace("–", "-")
        .replace("—", "-")
        .strip()
    )
    lower = cleaned.lower()
    if "or below" in lower or "or less" in lower:
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            return f"<{_simple_number(float(match.group(0)) + 1)}"
    if "or above" in lower or "or higher" in lower or "or more" in lower:
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            return f">{_simple_number(float(match.group(0)) - 1)}"
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", cleaned)
    if range_match:
        return f"{_simple_number(range_match.group(1))}-{_simple_number(range_match.group(2))}"
    if cleaned.startswith("<="):
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            return f"<{_simple_number(float(match.group(0)) + 1)}"
    if cleaned.startswith(">="):
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            return f">{_simple_number(float(match.group(0)) - 1)}"
    if cleaned.startswith("<") or cleaned.startswith(">"):
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            prefix = "<" if cleaned.startswith("<") else ">"
            return f"{prefix}{_simple_number(match.group(0))}"
    if cleaned.endswith("+"):
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match:
            return f">{_simple_number(float(match.group(0)) - 1)}"
    ticker_match = re.search(r"\bB(\d+)\.5\b", cleaned, flags=re.IGNORECASE)
    if ticker_match:
        lower_bound = int(ticker_match.group(1))
        return f"{lower_bound}-{lower_bound + 1}"
    return _short_label(cleaned or text, max_len=40)


def _compact_bracket(value: Any, *, max_len: int = 7) -> str:
    return _short_label(_canonical_bracket_label(value), max_len=max_len)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _trader_local_time(payload: dict[str, Any]) -> str:
    value = (payload.get("context") or {}).get("current_time_utc")
    parsed = _parse_utc_datetime(value)
    if parsed is not None:
        return parsed.astimezone(ZoneInfo(LAX_TIMEZONE)).strftime("%H:%M")
    return datetime.now(ZoneInfo(LAX_TIMEZONE)).strftime("%H:%M")


def _trader_top_probability_bracket(context: dict[str, Any]) -> str:
    bins = context.get("probability_bins") or []
    if not bins:
        return "-"
    top = max(bins, key=lambda row: float(row.get("probability") or 0))
    return _canonical_bracket_label(top)


def _selected_trade_candidate(context: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any] | None:
    selected_id = decision.get("selected_candidate_id")
    candidates = context.get("candidate_trades") or []
    if selected_id:
        for candidate in candidates:
            if candidate.get("candidate_id") == selected_id:
                return candidate
    for candidate in candidates:
        if (
            candidate.get("contract_ticker") == decision.get("contract_ticker")
            and candidate.get("side") == decision.get("side")
            and candidate.get("bracket_label") == decision.get("bracket")
        ):
            return candidate
    return None


def _market_exit_price_for_position(context: dict[str, Any], position: dict[str, Any]) -> float | None:
    ticker = position.get("contract_ticker")
    label = position.get("bracket_label")
    side = position.get("side")
    for bracket in context.get("market_brackets") or []:
        if ticker and bracket.get("contract_ticker") != ticker:
            continue
        if not ticker and label and bracket.get("bracket_label") != label:
            continue
        if side == "YES":
            bid = _float_or_none(bracket.get("yes_bid_cents"))
            if bid is not None:
                return bid
            no_ask = _float_or_none(bracket.get("no_ask_cents"))
            return 100 - no_ask if no_ask is not None else None
        if side == "NO":
            bid = _float_or_none(bracket.get("no_bid_cents"))
            if bid is not None:
                return bid
            yes_ask = _float_or_none(bracket.get("yes_ask_cents"))
            return 100 - yes_ask if yes_ask is not None else None
    return None


def _trader_open_position_cost(open_positions: list[dict[str, Any]]) -> float:
    cost = 0.0
    for position in open_positions:
        quantity = int(position.get("quantity") or 0)
        entry = _float_or_none(position.get("avg_entry_price_cents")) or 0.0
        cost += (quantity * entry) / 100.0
    return round(cost, 4)


def _trader_open_pnl(context: dict[str, Any], open_positions: list[dict[str, Any]]) -> float:
    pnl = 0.0
    for position in open_positions:
        quantity = int(position.get("quantity") or 0)
        entry = _float_or_none(position.get("avg_entry_price_cents"))
        mark = _market_exit_price_for_position(context, position)
        if entry is None or mark is None:
            continue
        pnl += ((mark - entry) * quantity) / 100.0
    return round(pnl, 4)


def _market_side_bid_ask(
    context: dict[str, Any],
    *,
    contract_ticker: str | None,
    bracket_label: str | None,
    side: str | None,
) -> tuple[float | None, float | None]:
    for bracket in context.get("market_brackets") or []:
        if contract_ticker and bracket.get("contract_ticker") != contract_ticker:
            continue
        if not contract_ticker and bracket_label and bracket.get("bracket_label") != bracket_label:
            continue
        if side == "YES":
            bid = _float_or_none(bracket.get("yes_bid_cents"))
            ask = _float_or_none(bracket.get("yes_ask_cents"))
            no_bid = _float_or_none(bracket.get("no_bid_cents"))
            no_ask = _float_or_none(bracket.get("no_ask_cents"))
            return (
                bid if bid is not None else (100 - no_ask if no_ask is not None else None),
                ask if ask is not None else (100 - no_bid if no_bid is not None else None),
            )
        if side == "NO":
            bid = _float_or_none(bracket.get("no_bid_cents"))
            ask = _float_or_none(bracket.get("no_ask_cents"))
            yes_bid = _float_or_none(bracket.get("yes_bid_cents"))
            yes_ask = _float_or_none(bracket.get("yes_ask_cents"))
            return (
                bid if bid is not None else (100 - yes_ask if yes_ask is not None else None),
                ask if ask is not None else (100 - yes_bid if yes_bid is not None else None),
            )
    return None, None


def _market_side_mid_cents(
    context: dict[str, Any],
    *,
    contract_ticker: str | None,
    bracket_label: str | None,
    side: str | None,
) -> float | None:
    bid, ask = _market_side_bid_ask(
        context,
        contract_ticker=contract_ticker,
        bracket_label=bracket_label,
        side=side,
    )
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return bid if bid is not None else ask


def _trader_clv_samples(
    context: dict[str, Any],
    fills: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now_utc or _parse_utc_datetime(context.get("current_time_utc")) or datetime.now(timezone.utc)
    samples: list[dict[str, Any]] = []
    for fill in fills:
        if str(fill.get("action") or "").upper() != "BUY":
            continue
        entry = _float_or_none(fill.get("price_cents"))
        created = _fill_created_at(fill)
        mark = _market_side_mid_cents(
            context,
            contract_ticker=str(fill.get("contract_ticker") or "") or None,
            bracket_label=str(fill.get("bracket_label") or "") or None,
            side=str(fill.get("side") or "").upper() or None,
        )
        elapsed_minutes = None
        if created is not None:
            elapsed_minutes = max(0.0, (now - created).total_seconds() / 60.0)
        row: dict[str, Any] = {
            "fill_id": fill.get("fill_id"),
            "selected_candidate_id": fill.get("selected_candidate_id"),
            "contract_ticker": fill.get("contract_ticker"),
            "bracket_label": _canonical_bracket_label(fill.get("bracket_label")),
            "side": fill.get("side"),
            "entry_price_cents": entry,
            "current_side_mid_cents": mark,
            "elapsed_minutes": None if elapsed_minutes is None else round(elapsed_minutes, 2),
            "final_pre_settlement_mid": None,
            "settlement_value": None,
            "clv_final_cents": None,
        }
        latest_clv = round(mark - entry, 4) if mark is not None and entry is not None else None
        row["latest_mark_cents"] = mark
        row["latest_clv_cents"] = latest_clv
        row["adverse_selection_flag"] = bool(latest_clv is not None and latest_clv < 0)
        for minutes in (5, 15, 30, 60):
            ready = elapsed_minutes is not None and elapsed_minutes >= minutes and mark is not None
            row[f"market_mid_after_{minutes}_min"] = mark if ready else None
            row[f"clv_{minutes}m_cents"] = round(mark - entry, 4) if ready and entry is not None else None
        row["mark_after_5m_cents"] = row.get("market_mid_after_5_min")
        row["mark_after_15m_cents"] = row.get("market_mid_after_15_min")
        row["mark_after_30m_cents"] = row.get("market_mid_after_30_min")
        row["mark_after_60m_cents"] = row.get("market_mid_after_60_min")
        samples.append(row)
    return samples


def _fill_created_at(fill: dict[str, Any]) -> datetime | None:
    return _parse_utc_datetime(fill.get("created_at_utc"))


def _same_position_key(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("contract_ticker") and right.get("contract_ticker"):
        return left.get("contract_ticker") == right.get("contract_ticker") and left.get("side") == right.get("side")
    return left.get("bracket_label") == right.get("bracket_label") and left.get("side") == right.get("side")


def _trader_recent_buy_for_candidate(
    fills: list[dict[str, Any]],
    selected_candidate_id: str | None,
    *,
    cooldown_minutes: float,
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    if not selected_candidate_id or cooldown_minutes <= 0:
        return None
    now = now_utc or datetime.now(timezone.utc)
    cutoff_seconds = cooldown_minutes * 60
    for fill in reversed(fills):
        if fill.get("action") != "BUY" or fill.get("selected_candidate_id") != selected_candidate_id:
            continue
        created = _fill_created_at(fill)
        if created is None:
            return fill
        if (now - created).total_seconds() <= cutoff_seconds:
            return fill
    return None


def _trader_portfolio_values(
    context: dict[str, Any],
    open_positions: list[dict[str, Any]],
    *,
    starting_cash: float | None,
    closed_pnl_dollars: float = 0.0,
) -> dict[str, float | None]:
    open_cost = _trader_open_position_cost(open_positions)
    open_pnl = _trader_open_pnl(context, open_positions)
    position_value = round(open_cost + open_pnl, 4)
    cash_value = None
    total_value = None
    if starting_cash is not None:
        cash_value = round(float(starting_cash) - open_cost + closed_pnl_dollars, 4)
        total_value = round(cash_value + position_value, 4)
    return {
        "cash_value": cash_value,
        "position_value": position_value,
        "total_value": total_value,
        "open_pnl_value": open_pnl,
        "closed_pnl_value": round(closed_pnl_dollars, 4),
    }


def _trader_portfolio_snapshot(
    context: dict[str, Any],
    open_positions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    *,
    starting_cash: float | None,
) -> dict[str, Any]:
    open_cost = _trader_open_position_cost(open_positions)
    open_orders = context.get("open_orders") or []
    open_order_cost = _trader_open_order_cost(open_orders)
    open_pnl = _trader_open_pnl(context, open_positions)
    position_value = round(open_cost + open_pnl, 4)
    buy_spend = sum(float(fill.get("gross_value_dollars") or 0) for fill in fills if fill.get("action") == "BUY")
    close_proceeds = sum(float(fill.get("gross_value_dollars") or 0) for fill in fills if fill.get("action") == "CLOSE")
    closed_pnl = sum(float(fill.get("realized_pnl_dollars") or 0) for fill in fills)
    fill_price_improvement = round(
        sum(float(fill.get("fill_price_improvement_dollars") or 0) for fill in fills),
        4,
    )
    fallback_cash = None if starting_cash is None else round(float(starting_cash) - open_cost + closed_pnl, 4)
    cash = None
    if starting_cash is not None:
        cash = round(float(starting_cash) - buy_spend + close_proceeds, 4)
        if not fills and open_positions:
            cash = fallback_cash
    cash_available = None if cash is None else round(cash - open_order_cost, 4)
    equity = None if cash is None else round(cash + position_value, 4)
    conservative_equity = None if equity is None else round(equity - fill_price_improvement, 4)
    exposure_by_bracket: dict[str, float] = defaultdict(float)
    exposure_by_side: dict[str, float] = defaultdict(float)
    contracts_by_bracket: dict[str, int] = defaultdict(int)
    contracts_by_side: dict[str, int] = defaultdict(int)
    for position in open_positions:
        quantity = int(position.get("quantity") or 0)
        entry = _float_or_none(position.get("avg_entry_price_cents")) or 0.0
        exposure = round((quantity * entry) / 100.0, 4)
        bracket = str(position.get("bracket_label") or position.get("contract_ticker") or "--")
        side = str(position.get("side") or "--")
        exposure_by_bracket[bracket] += exposure
        exposure_by_side[side] += exposure
        contracts_by_bracket[bracket] += quantity
        contracts_by_side[side] += quantity
    for order in open_orders:
        if str(order.get("action") or "") != "PLACE_FAKE_LIMIT_BUY":
            continue
        quantity = int(order.get("quantity") or 0)
        price = _float_or_none(order.get("limit_price_cents")) or 0.0
        exposure = round((quantity * price) / 100.0, 4)
        bracket = str(order.get("bracket_label") or (order.get("metadata") or {}).get("bracket_label") or order.get("contract_ticker") or "--")
        side = str(order.get("side") or "--")
        exposure_by_bracket[bracket] += exposure
        exposure_by_side[side] += exposure
        contracts_by_bracket[bracket] += quantity
        contracts_by_side[side] += quantity
    drawdown = None if starting_cash is None or equity is None else round(float(starting_cash) - equity, 4)
    open_position_contracts = sum(int(position.get("quantity") or 0) for position in open_positions)
    open_order_contracts = sum(
        int(order.get("quantity") or 0)
        for order in open_orders
        if str(order.get("action") or "") == "PLACE_FAKE_LIMIT_BUY"
    )
    open_position_groups = len(
        {
            (
                str(position.get("contract_ticker") or position.get("bracket_label") or ""),
                str(position.get("side") or ""),
            )
            for position in open_positions
            if int(position.get("quantity") or 0) > 0
        }
    )
    open_order_groups = len(
        {
            (
                str(order.get("contract_ticker") or order.get("bracket_label") or ""),
                str(order.get("side") or ""),
            )
            for order in open_orders
            if str(order.get("action") or "") == "PLACE_FAKE_LIMIT_BUY"
        }
    )
    return {
        "starting_cash": starting_cash,
        "cash_value": cash,
        "cash_available_value": cash_available,
        "equity_value": equity,
        "reported_equity": equity,
        "conservative_equity": conservative_equity,
        "optimistic_fill_benefit_dollars": fill_price_improvement,
        "fill_price_improvement_dollars": fill_price_improvement,
        "position_value": position_value,
        "open_pnl_value": open_pnl,
        "closed_pnl_value": round(closed_pnl, 4),
        "open_exposure_value": round(open_cost + open_order_cost, 4),
        "open_position_exposure_value": round(open_cost, 4),
        "open_order_exposure_value": round(open_order_cost, 4),
        "total_contracts": open_position_contracts + open_order_contracts,
        "position_contracts": open_position_contracts,
        "open_order_contracts": open_order_contracts,
        "position_groups": len(open_positions),
        "open_positions_count": len(open_positions),
        "open_orders_count": len([order for order in open_orders if str(order.get("action") or "") == "PLACE_FAKE_LIMIT_BUY"]),
        "open_position_groups": open_position_groups,
        "open_order_groups": open_order_groups,
        "total_open_risk_groups": open_position_groups + open_order_groups,
        "exposure_by_bracket": {key: round(value, 4) for key, value in exposure_by_bracket.items()},
        "exposure_by_side": {key: round(value, 4) for key, value in exposure_by_side.items()},
        "contracts_by_bracket": dict(contracts_by_bracket),
        "contracts_by_side": dict(contracts_by_side),
        "drawdown_value": drawdown,
        "cash": _fmt_dollars(cash) if cash is not None else "--",
        "cash_available": _fmt_dollars(cash_available) if cash_available is not None else "--",
        "equity": _fmt_dollars(equity) if equity is not None else "--",
        "position_value_text": _fmt_dollars(position_value),
        "open_pnl": _fmt_signed_dollars(open_pnl),
        "closed_pnl": _fmt_signed_dollars(closed_pnl),
        "open_exposure": _fmt_dollars(open_cost + open_order_cost),
        "drawdown": _fmt_signed_dollars(-(drawdown or 0)) if drawdown is not None else "--",
    }


def _scenario_bracket_bounds(label: str) -> tuple[float | None, float | None]:
    text = _canonical_bracket_label(label)
    if text.startswith("<"):
        return None, _float_or_none(text[1:])
    if text.startswith(">"):
        return _float_or_none(text[1:]), None
    match = re.match(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$", text)
    if not match:
        return None, None
    return _float_or_none(match.group(1)), _float_or_none(match.group(2))


def _scenario_probability_for_label(context: dict[str, Any], label: str) -> float | None:
    canonical = _canonical_bracket_label(label)
    for row in context.get("bracket_probabilities") or context.get("probabilities") or []:
        if not isinstance(row, dict):
            continue
        row_label = _canonical_bracket_label(
            row.get("bracket_label") or row.get("label") or row.get("bracket"),
            lower_f=row.get("lower_f"),
            upper_f=row.get("upper_f"),
        )
        if row_label != canonical:
            continue
        for key in ("probability", "probability_pct", "p", "model_probability"):
            value = _float_or_none(row.get(key))
            if value is None:
                continue
            return value / 100.0 if key == "probability_pct" or value > 1 else value
    return None


def _scenario_status_for_context(label: str, context: dict[str, Any]) -> str:
    canonical = _canonical_bracket_label(label)
    final_high = _float_or_none(
        context.get("final_high_f")
        or context.get("official_high_f")
        or ((context.get("official_outcome") or {}).get("official_high_f") if isinstance(context.get("official_outcome"), dict) else None)
    )
    if final_high is not None:
        winning = _telemetry_bracket_for_temperature(final_high, context.get("market_brackets") or [])
        if winning is not None:
            return "official_winner" if _canonical_bracket_label(winning) == canonical else "confirmed_eliminated"
    high_so_far = _float_or_none(
        context.get("observed_high_so_far_f")
        or context.get("high_so_far_f")
        or context.get("observed_high_f")
    )
    _lower, upper = _scenario_bracket_bounds(canonical)
    if high_so_far is not None and upper is not None and high_so_far > upper:
        return "confirmed_eliminated"
    probability = _scenario_probability_for_label(context, canonical)
    if probability is not None and probability < 0.02:
        return "probable_eliminated"
    if high_so_far is None and probability is None:
        return "theoretical"
    return "still_possible"


def _settlement_scenario_payload(
    context: dict[str, Any],
    portfolio: dict[str, Any],
    open_positions: list[dict[str, Any]],
    *,
    starting_cash: float,
) -> dict[str, Any]:
    brackets = [
        _canonical_bracket_label(row.get("bracket_label"), lower_f=row.get("lower_f"), upper_f=row.get("upper_f"))
        for row in context.get("market_brackets") or []
    ]
    positions = [
        SettlementPosition(
            bracket=_canonical_bracket_label(position.get("bracket_label")),
            side=str(position.get("side") or ""),
            quantity=int(position.get("quantity") or 0),
            avg_cost_cents=float(position.get("avg_entry_price_cents") or 0.0),
        )
        for position in open_positions
        if int(position.get("quantity") or 0) > 0
    ]
    report = settlement_report(
        brackets,
        positions,
        cash_dollars=float(portfolio.get("cash_value") or 0.0),
        starting_cash_dollars=float(starting_cash),
        current_equity_dollars=float(portfolio.get("equity_value") or portfolio.get("total_value") or starting_cash),
    )
    scenario_rows: list[dict[str, Any]] = []
    for row in report.scenarios:
        winning_positions: list[dict[str, Any]] = []
        losing_positions: list[dict[str, Any]] = []
        for position in open_positions:
            side = str(position.get("side") or "").upper()
            bracket = _canonical_bracket_label(position.get("bracket_label"))
            wins = (side == "YES" and bracket == row.scenario_label) or (side == "NO" and bracket != row.scenario_label)
            position_payload = {
                "contract_ticker": position.get("contract_ticker"),
                "bracket": bracket,
                "side": side,
                "quantity": int(position.get("quantity") or 0),
                "avg_entry_price_cents": _float_or_none(position.get("avg_entry_price_cents")),
            }
            if wins:
                winning_positions.append(position_payload)
            else:
                losing_positions.append(position_payload)
        scenario_rows.append(
            {
                **asdict(row),
                "settling_bracket": row.scenario_label,
                "scenario_status": _scenario_status_for_context(row.scenario_label, context),
                "cash": float(portfolio.get("cash_value") or 0.0),
                "position_settlement_value": row.settlement_value_dollars,
                "final_equity": row.final_equity_dollars,
                "winning_positions": winning_positions,
                "losing_positions": losing_positions,
            }
        )
    return {
        "scenarios": scenario_rows,
        "best_case_scenario": report.best_case_scenario,
        "worst_case_scenario": report.worst_case_scenario,
        "best_case_gain_dollars": report.best_case_gain_dollars,
        "worst_case_loss_dollars": report.worst_case_loss_dollars,
        "downside_concentration_score": report.downside_concentration_score,
        "correlated_thesis_exposure": _thesis_exposure_payload(context, open_positions),
        "scenarios_breaching_loss_limits": [
            row for row in scenario_rows if float(row.get("pnl_vs_starting_cash") or 0.0) < -100
        ],
        "scenarios_breaching_loss_limit": [
            row for row in scenario_rows if float(row.get("pnl_vs_starting_cash") or 0.0) < -100
        ],
    }


def _settlement_scenario_text(payload: dict[str, Any], *, style: str = "compact") -> str:
    report = payload.get("settlement_scenarios") or {}
    scenarios = report.get("scenarios") or []
    if not scenarios:
        return "Settlement Scenarios: none"
    best_label = report.get("best_case_scenario") or "--"
    worst_label = report.get("worst_case_scenario") or "--"
    best_gain = _fmt_signed_dollars(report.get("best_case_gain_dollars"))
    worst_loss = _fmt_signed_dollars(report.get("worst_case_loss_dollars"))
    concentration = _float_or_none(report.get("downside_concentration_score"))
    concentration_text = "--" if concentration is None else f"{concentration:.2f}"
    if style == "compact":
        return (
            "Settlement Scenarios: "
            f"best {best_label} {best_gain} | worst {worst_label} {worst_loss} | "
            f"downside concentration {concentration_text}"
        )

    lines = [
        "Settlement Scenarios",
        "--------------------",
        "Bracket  Status                Settle value  Final equity  P/L start  P/L current",
        "-------  --------------------  ------------  ------------  ---------  -----------",
    ]
    for row in scenarios:
        lines.append(
            f"{_fit_cell(row.get('scenario_label'), 7)}  "
            f"{_fit_cell(row.get('scenario_status'), 20)}  "
            f"{_fit_cell(_fmt_dollars(row.get('settlement_value_dollars')), 12, align='right')}  "
            f"{_fit_cell(_fmt_dollars(row.get('final_equity_dollars')), 12, align='right')}  "
            f"{_fit_cell(_fmt_signed_dollars(row.get('pnl_vs_starting_cash')), 9, align='right')}  "
            f"{_fit_cell(_fmt_signed_dollars(row.get('pnl_vs_current_equity')), 11, align='right')}"
        )
    lines.append(f"Best: {best_label} {best_gain} | Worst: {worst_label} {worst_loss}")
    lines.append(f"Downside concentration: {concentration_text}")
    if style == "full":
        breaches = report.get("scenarios_breaching_loss_limits") or []
        lines.append(f"Loss-limit breach scenarios: {len(breaches)}")
        for row in breaches:
            lines.append(
                f"- {row.get('scenario_label')}: "
                f"{_fmt_signed_dollars(row.get('pnl_vs_starting_cash'))}"
            )
    return "\n".join(lines)


def _thesis_exposure_payload(
    context: dict[str, Any],
    open_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    top = _trader_top_probability_bracket(context) or ""
    positions = [
        ThesisPosition(
            bracket=_canonical_bracket_label(position.get("bracket_label")),
            side=str(position.get("side") or ""),
            risk_dollars=(
                int(position.get("quantity") or 0)
                * float(position.get("avg_entry_price_cents") or 0.0)
                / 100.0
            ),
        )
        for position in open_positions
        if int(position.get("quantity") or 0) > 0
    ]
    exposures = evaluate_thesis_exposure(positions, top_bracket=top)
    return {
        "top_bracket": top,
        "exposures": [asdict(row) for row in exposures],
        "blocked_theses": [asdict(row) for row in exposures if not row.thesis_allowed],
    }


def _clv_summary_payload(context: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[CLVRecord] = []
    for fill in fills:
        if fill.get("action") != "BUY":
            continue
        mark = _market_exit_price_for_position(
            context,
            {
                "contract_ticker": fill.get("contract_ticker"),
                "bracket_label": fill.get("bracket_label"),
                "side": fill.get("side"),
            },
        )
        record = CLVRecord(
            fill_id=str(fill.get("fill_id") or fill.get("selected_candidate_id") or ""),
            bracket=_canonical_bracket_label(fill.get("bracket_label")),
            side=str(fill.get("side") or ""),
            entry_price_cents=float(fill.get("price_cents") or 0.0),
        )
        if mark is not None:
            record.marks["latest"] = float(mark)
        records.append(record)
    return {
        "latest": summarize_clv(records, horizon="latest"),
        "records": [
            {
                "fill_id": record.fill_id,
                "bracket": record.bracket,
                "side": record.side,
                "entry_price_cents": record.entry_price_cents,
                "marks": dict(record.marks),
            }
            for record in records
        ],
    }


def _trader_open_order_cost(open_orders: list[dict[str, Any]]) -> float:
    total = 0.0
    for order in open_orders:
        if str(order.get("action") or "") != "PLACE_FAKE_LIMIT_BUY":
            continue
        quantity = int(order.get("quantity") or 0)
        price = _float_or_none(order.get("limit_price_cents")) or 0.0
        total += quantity * price / 100.0
    return round(total, 4)


def _candidate_order_cost(candidate: dict[str, Any], quantity: int) -> tuple[float, float, float]:
    price_cents = _float_or_none(candidate.get("entry_price_cents")) or 0.0
    fee_cents = _float_or_none(candidate.get("fee_cents")) or 0.0
    trade_cost = (price_cents * quantity) / 100.0
    fee_cost = (fee_cents * quantity) / 100.0
    return round(trade_cost + fee_cost, 4), round(trade_cost, 4), round(fee_cost, 4)


def _portfolio_risk_reject(reason: str) -> ValidationResult:
    return ValidationResult(
        valid=False,
        approved_action=TraderDecision.hold(reason).to_dict(),
        fallback_action="HOLD",
        rejection_reason=reason,
    )


def _validate_paper_buy_against_portfolio(
    *,
    decision: dict[str, Any],
    candidate: dict[str, Any],
    context: dict[str, Any],
    portfolio: dict[str, Any],
    open_positions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    risk_limits: RiskLimits,
) -> ValidationResult | None:
    if decision.get("action") not in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY"}:
        return None
    quantity = int(decision.get("max_contracts") or 0)
    if quantity <= 0:
        return _portfolio_risk_reject("max_contracts must be positive for BUY")
    required_cash, trade_cost, _fee_cost = _candidate_order_cost(candidate, quantity)
    available_cash = portfolio.get("cash_available_value", portfolio.get("cash_value"))
    if available_cash is not None:
        if available_cash < 0 and not risk_limits.allow_negative_cash:
            return _portfolio_risk_reject("insufficient fake cash")
        if required_cash > available_cash and not risk_limits.allow_negative_cash:
            return _portfolio_risk_reject("insufficient fake cash")
    if trade_cost > risk_limits.max_risk_dollars_per_trade:
        return _portfolio_risk_reject("max trade risk")
    if -(portfolio.get("open_pnl_value") or 0.0) > risk_limits.max_open_loss_dollars:
        return _portfolio_risk_reject("max open loss")
    drawdown = portfolio.get("drawdown_value")
    if drawdown is not None and drawdown > risk_limits.max_total_drawdown_dollars:
        return _portfolio_risk_reject("max total drawdown")

    bracket = str(candidate.get("bracket_label") or candidate.get("contract_ticker") or "--")
    side = str(candidate.get("side") or "--")
    current_exposure = float(portfolio.get("open_exposure_value") or 0.0)
    bracket_exposure = float((portfolio.get("exposure_by_bracket") or {}).get(bracket, 0.0))
    bracket_contracts = int((portfolio.get("contracts_by_bracket") or {}).get(bracket, 0))
    side_contracts = int((portfolio.get("contracts_by_side") or {}).get(side, 0))
    if current_exposure + trade_cost > risk_limits.max_total_exposure_dollars:
        return _portfolio_risk_reject("exposure cap")
    if bracket_exposure + trade_cost > risk_limits.max_exposure_dollars_per_bracket:
        return _portfolio_risk_reject("bracket exposure cap")
    if bracket_contracts + quantity > risk_limits.max_contracts_per_bracket:
        return _portfolio_risk_reject("bracket contract cap")
    if side_contracts + quantity > risk_limits.max_contracts_per_side:
        return _portfolio_risk_reject("side contract cap")

    order_position = {
        "contract_ticker": candidate.get("contract_ticker"),
        "bracket_label": candidate.get("bracket_label"),
        "side": candidate.get("side"),
    }
    already_positioned = any(_same_position_key(position, order_position) for position in open_positions)
    if already_positioned and not risk_limits.allow_scale_in:
        return _portfolio_risk_reject("already positioned; scale-in disabled")
    if already_positioned and risk_limits.allow_scale_in:
        edge = float(candidate.get("fee_adjusted_edge_cents") or 0.0)
        required_edge = risk_limits.min_edge_cents + risk_limits.scale_in_edge_buffer_cents
        if edge < required_edge:
            return _portfolio_risk_reject("scale-in edge buffer")
    if _trader_recent_buy_for_candidate(
        fills,
        str(candidate.get("candidate_id") or decision.get("selected_candidate_id") or ""),
        cooldown_minutes=risk_limits.same_candidate_cooldown_minutes,
    ) and not risk_limits.allow_scale_in:
        return _portfolio_risk_reject("same candidate cooldown")
    new_group = not already_positioned
    if new_group and int(portfolio.get("position_groups") or 0) >= risk_limits.max_open_positions:
        return _portfolio_risk_reject("max open positions")
    open_orders_count = int(portfolio.get("open_orders_count") or 0)
    if new_group and open_orders_count >= risk_limits.max_open_orders:
        return _portfolio_risk_reject("max open orders")
    total_groups = int(portfolio.get("total_open_risk_groups") or 0)
    max_total_groups = (
        risk_limits.max_total_open_risk_groups
        if risk_limits.max_total_open_risk_groups is not None
        else risk_limits.max_open_positions
    )
    if new_group and total_groups >= max_total_groups:
        return _portfolio_risk_reject("max total open risk groups")
    return None


def _trader_action_label(payload: dict[str, Any]) -> str:
    decision = payload.get("decision") or {}
    validation = payload.get("validation") or {}
    if validation.get("valid") is False:
        return "REJECT"
    pending = payload.get("pending_order_executions") or []
    paper_execution = payload.get("paper_execution") or {}
    if decision.get("action") == "HOLD":
        pending_fill = next((row for row in pending if row.get("executed")), None)
        if pending_fill:
            return "CLOSE" if pending_fill.get("action") == "CLOSE" else "FILL"
    if paper_execution.get("executed"):
        if (
            payload.get("order_style") == "passive"
            and str((payload.get("paper_order") or {}).get("action") or "") == "PLACE_FAKE_LIMIT_BUY"
            and str(paper_execution.get("action") or "") == "BUY"
        ):
            return "FILL"
        return str(paper_execution.get("action") or "BUY")
    if paper_execution.get("status") == "open" or paper_execution.get("action") == "OPEN_LIMIT_ORDER":
        return "POST"
    action = decision.get("action")
    if action in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY"}:
        return "BUY"
    if action == "PLACE_FAKE_LIMIT_SELL":
        return "SELL"
    if action == "CLOSE_FAKE_POSITION":
        return "CLOSE"
    if action == "CANCEL_FAKE_ORDER":
        return "CANCEL"
    return "HOLD"


def _trader_row_note(payload: dict[str, Any], action: str, *, max_len: int = 48) -> str:
    decision = payload.get("decision") or {}
    validation = payload.get("validation") or {}
    paper_execution = payload.get("paper_execution") or {}
    pending = payload.get("pending_order_executions") or []
    if action == "REJECT":
        reason = validation.get("rejection_reason") or decision.get("no_trade_reason") or "validator rejected"
        return _short_label(_deterministic_rejection_note(str(reason)), max_len=max_len)
    pending_fill = next((row for row in pending if row.get("executed")), None)
    if pending_fill and decision.get("action") == "HOLD":
        return _short_label("pending fill", max_len=max_len)
    if paper_execution.get("executed"):
        if action == "CLOSE":
            realized = paper_execution.get("realized_pnl_dollars")
            if realized is not None:
                return _short_label(f"closed; realized {_fmt_signed_dollars(realized)}", max_len=max_len)
            return "closed"
        edge = _float_or_none(decision.get("estimated_edge_cents")) or 0.0
        return _short_label("huge edge" if edge >= 20 else "edge passed", max_len=max_len)
    if paper_execution.get("status") == "open":
        return _short_label("pending limit", max_len=max_len)
    if paper_execution.get("reason"):
        return _short_label(_deterministic_rejection_note(str(paper_execution.get("reason"))), max_len=max_len)
    if decision.get("no_trade_reason"):
        return _short_label(_deterministic_rejection_note(str(decision.get("no_trade_reason"))), max_len=max_len)
    return _short_label("fallback HOLD" if action == "HOLD" else "edge passed", max_len=max_len)


def _deterministic_rejection_note(reason: str) -> str:
    lower = reason.lower()
    if "cash" in lower:
        return "insufficient fake cash"
    if "close_only_new_buy_blocked" in lower:
        return "close-only blocks new buys"
    if "profile_blocks_new_entries" in lower:
        return "profile blocks new entries"
    if "late_day_new_entry_not_clean_enough" in lower:
        return "late-day entry not clean"
    if "no clean edge" in lower or "no_valid_candidate" in lower or "no candidates" in lower:
        return "no clean edge"
    if "spread" in lower:
        return "spread cap"
    if "upside" in lower:
        return "upside too small"
    if "probability" in lower:
        return "probability filter"
    if "market_stale" in lower or "market stale" in lower:
        return "market stale"
    if "model_stale" in lower or "model stale" in lower:
        return "model stale"
    if "observation_stale" in lower or "observation stale" in lower:
        return "observation stale"
    if "order_already_open" in lower or "order already open" in lower:
        return "order already open"
    if "scale-in disabled" in lower or "already positioned" in lower:
        return "scale-in blocked"
    if "cooldown" in lower:
        return "same candidate cooldown"
    if "bracket exposure" in lower:
        return "bracket exposure cap"
    if "exposure" in lower:
        return "exposure cap"
    if "bracket contract" in lower:
        return "bracket contract cap"
    if "side contract" in lower:
        return "side contract cap"
    if "drawdown" in lower:
        return "drawdown guard"
    if "open loss" in lower:
        return "open loss guard"
    if "json" in lower or "parse" in lower or "malformed" in lower:
        return "malformed JSON"
    if "max open positions" in lower:
        return "position cap"
    if "risk" in lower:
        return "risk cap"
    if "edge" in lower:
        return "edge too low"
    return "fallback HOLD"


def _snapshot_model_display_name(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    mapping = {
        "current_weighted_blend": "Blend",
        "best_match": "Best Match",
        "gfs013": "GFS013",
        "gfs_global": "GFS Global",
        "gfs_seamless": "GFS Seamless",
        "hrrr": "HRRR",
        "nbm": "NBM",
        "gfs": "GFS",
        "rap": "RAP",
    }
    return mapping.get(text, text.replace("_", " ").title() if text else "--")


def _snapshot_temp(value: Any) -> str:
    number = _float_or_none(value)
    return "--" if number is None else f"{number:.1f}F"


def _snapshot_quote(value: Any) -> str:
    number = _float_or_none(value)
    return "--" if number is None else f"{number:.0f}c"


def _snapshot_bid_ask(bid: Any, ask: Any) -> str:
    return f"{_snapshot_quote(bid)}/{_snapshot_quote(ask)}"


def _snapshot_probability_by_label(context: dict[str, Any]) -> dict[str, float]:
    probabilities: dict[str, float] = {}
    for row in context.get("probability_bins") or []:
        label = _canonical_bracket_label(row.get("bracket_label") or row.get("label") or row.get("bracket"))
        probability = _float_or_none(row.get("probability") or row.get("p") or row.get("model_probability"))
        if label and probability is not None:
            probabilities[label] = probability
    return probabilities


def _snapshot_best_edge_by_label(context: dict[str, Any]) -> dict[str, str]:
    best: dict[str, tuple[float, str]] = {}
    for candidate in context.get("candidate_trades") or []:
        if str(candidate.get("action") or "").upper() != "BUY":
            continue
        label = _canonical_bracket_label(candidate.get("bracket_label") or candidate.get("bracket"))
        side = str(candidate.get("side") or "").upper()
        edge = _float_or_none(
            candidate.get("fee_adjusted_edge_cents")
            or candidate.get("net_edge_cents")
            or candidate.get("edge_cents")
        )
        if not label or side not in {"YES", "NO"} or edge is None:
            continue
        if not bool(candidate.get("eligible", True)) and edge <= 0:
            continue
        current = best.get(label)
        if current is None or edge > current[0]:
            best[label] = (edge, f"{side} {_fmt_edge(edge)}")
    return {label: value for label, (_edge, value) in best.items()}


def _snapshot_market_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    probabilities = _snapshot_probability_by_label(context)
    best_edges = _snapshot_best_edge_by_label(context)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bracket in context.get("market_brackets") or []:
        label = _canonical_bracket_label(bracket.get("bracket_label") or bracket.get("label") or bracket.get("bracket"))
        if not label or label in seen:
            continue
        seen.add(label)
        probability = probabilities.get(label)
        rows.append(
            {
                "label": label,
                "yes_bid": _float_or_none(bracket.get("yes_bid_cents")),
                "yes_ask": _float_or_none(bracket.get("yes_ask_cents")),
                "no_bid": _float_or_none(bracket.get("no_bid_cents")),
                "no_ask": _float_or_none(bracket.get("no_ask_cents")),
                "probability": probability,
                "fair_yes": None if probability is None else probability * 100.0,
                "best_edge": best_edges.get(label, "none"),
            }
        )
    return rows


def _trader_snapshot_state(context: dict[str, Any]) -> dict[str, Any]:
    models: dict[str, float] = {}
    for estimate in context.get("model_estimates") or []:
        name = str(estimate.get("provider") or estimate.get("model_id") or estimate.get("model") or "")
        high = _float_or_none(estimate.get("high_f") or estimate.get("future_high_f") or estimate.get("settlement_high_estimate_f"))
        if name and high is not None:
            models[name] = high
    market: dict[str, tuple[float | None, float | None, float | None, float | None]] = {}
    for row in _snapshot_market_rows(context):
        market[row["label"]] = (row.get("yes_bid"), row.get("yes_ask"), row.get("no_bid"), row.get("no_ask"))
    best_candidate = None
    best_edge = None
    for candidate in context.get("candidate_trades") or []:
        if str(candidate.get("action") or "").upper() != "BUY":
            continue
        edge = _float_or_none(candidate.get("fee_adjusted_edge_cents") or candidate.get("net_edge_cents"))
        if edge is None:
            continue
        if best_edge is None or edge > best_edge:
            best_edge = edge
            best_candidate = candidate.get("candidate_id") or f"{candidate.get('side')}:{candidate.get('bracket_label')}"
    return {
        "top": _trader_top_probability_bracket(context),
        "models": models,
        "market": market,
        "best_candidate": best_candidate,
    }


def _snapshot_state_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    if previous.get("top") != current.get("top"):
        return True
    if previous.get("best_candidate") != current.get("best_candidate"):
        return True
    previous_models = previous.get("models") or {}
    current_models = current.get("models") or {}
    for key, value in current_models.items():
        old = previous_models.get(key)
        if old is None or abs(float(value) - float(old)) >= 0.5:
            return True
    previous_market = previous.get("market") or {}
    current_market = current.get("market") or {}
    for key, values in current_market.items():
        old_values = previous_market.get(key)
        if old_values is None:
            return True
        for value, old in zip(values, old_values):
            if value is None or old is None:
                if value != old:
                    return True
                continue
            if abs(float(value) - float(old)) >= 3.0:
                return True
    return False


def _trader_snapshot_should_print(
    payload: dict[str, Any],
    row: dict[str, Any],
    *,
    iteration: int,
    show_snapshot: str,
    snapshot_every: int,
    previous_state: dict[str, Any] | None,
    seen_rejection_reasons: set[str],
) -> tuple[bool, dict[str, Any]]:
    context = payload.get("context") or {}
    current_state = _trader_snapshot_state(context)
    mode = str(show_snapshot or "changed").strip().lower()
    if mode == "never":
        return False, current_state
    if previous_state is None:
        return True, current_state
    if mode == "every" and snapshot_every > 0 and iteration % snapshot_every == 0:
        return True, current_state
    action = str(row.get("action") or "").upper()
    if action in {"BUY", "POST", "FILL", "SELL", "CLOSE", "CANCEL"}:
        return True, current_state
    validation = payload.get("validation") or {}
    reason = validation.get("rejection_reason") or row.get("note")
    if action == "REJECT" and reason:
        reason_text = str(reason)
        if reason_text not in seen_rejection_reasons:
            seen_rejection_reasons.add(reason_text)
            return True, current_state
    if mode == "changed" and _snapshot_state_changed(previous_state, current_state):
        return True, current_state
    return False, current_state


def _snapshot_chunked_lines(parts: list[str], *, max_len: int = 120) -> list[str]:
    lines: list[str] = []
    current = ""
    for part in parts:
        candidate = part if not current else f"{current} | {part}"
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = part
    if current:
        lines.append(current)
    return lines or ["--"]


def _trader_snapshot_text(
    payload: dict[str, Any],
    *,
    style: str = "compact",
    show_models: bool = True,
    show_market: bool = True,
) -> str:
    context = payload.get("context") or {}
    time_label = _trader_local_time(payload)
    lines = [f"Snapshot {time_label}", "--------------"]
    model_source_payload = payload.get("model_source") or payload.get("model_source_diagnostics") or {}
    rules_payload = payload.get("rules_engine") or {}
    risk_config_payload = rules_payload.get("risk_config") or {}
    probability_blend_payload = rules_payload.get("probability_blending") or {}
    order_style = str(payload.get("order_style") or "passive").lower()
    execution_style = "TAKER" if order_style == "taker" else "PASSIVE"
    resting_passive = "disabled" if order_style == "taker" else "enabled"
    passive_cleanup = payload.get("cancel_existing_passive_orders_on_taker_start")
    model_authoritative = bool(risk_config_payload.get("model_authoritative"))
    model_weight = probability_blend_payload.get("model_weight")
    market_weight = probability_blend_payload.get("market_weight")
    lines.extend(
        [
            "",
            "Execution",
            (
                f"Style {execution_style} | BUY entry {'ask' if order_style == 'taker' else 'passive limit'} | "
                f"CLOSE exit bid | resting passive {resting_passive} | "
                f"passive cleanup {'on' if passive_cleanup else 'off'}"
            ),
            "Model source",
            (
                f"{model_source_payload.get('model_source_mode') or '--'} | cached models "
                f"{'ON' if model_source_payload.get('use_cached_models') else 'OFF'} | "
                f"force recompute {'ON' if model_source_payload.get('force_model_recompute_every_iteration') else 'OFF'} | "
                f"NOAA mode {model_source_payload.get('noaa_model_mode') or '--'}"
            ),
            "Model trust",
            (
                f"{'authoritative' if model_authoritative else 'blended'} | p_used "
                f"{payload.get('probability_blend_mode') or rules_payload.get('probability_blend_mode') or '--'} | "
                f"model_weight {model_weight if model_weight is not None else '--'} | "
                f"market_weight {market_weight if market_weight is not None else '--'}"
            ),
        ]
    )
    model_rows = []
    for estimate in context.get("model_estimates") or []:
        high = _float_or_none(estimate.get("high_f") or estimate.get("future_high_f") or estimate.get("settlement_high_estimate_f"))
        name = _snapshot_model_display_name(estimate.get("provider") or estimate.get("model_id") or estimate.get("model"))
        if high is None:
            continue
        model_rows.append({"name": name, "high": high})
    if show_models:
        lines.extend(["", "Models"])
        if style == "table":
            blend = model_rows[0]["high"] if model_rows else None
            lines.extend(["Model                 Temp     Delta   Note", "--------------------  -------  ------  ----------------"])
            for row in model_rows:
                delta = "--" if blend is None else f"{row['high'] - blend:+.1f}F"
                note = "center" if blend is not None and abs(row["high"] - blend) < 0.05 else "near blend"
                if blend is not None and abs(row["high"] - blend) >= 3.0:
                    note = "outlier"
                lines.append(f"{row['name']:<20}  {_snapshot_temp(row['high']):>7}  {delta:>6}  {note}")
        else:
            parts = [f"{row['name']} {_snapshot_temp(row['high'])}" for row in model_rows]
            parts.append(f"Top {_trader_top_probability_bracket(context)}")
            lines.extend(_snapshot_chunked_lines(parts, max_len=120))
    market_rows = _snapshot_market_rows(context)
    if show_market:
        lines.extend(["", "Market"])
        if style == "table":
            lines.extend(
                [
                    "Bracket  YES bid/ask  NO bid/ask   Model P  Fair YES  Best Edge",
                    "--------  -----------  -----------  -------  --------  ---------",
                ]
            )
            for row in market_rows:
                probability = row.get("probability")
                probability_text = "--" if probability is None else f"{probability * 100:>5.1f}%"
                fair_text = "--" if row.get("fair_yes") is None else f"{row['fair_yes']:>6.1f}c"
                lines.append(
                    f"{row['label']:<8}  {_snapshot_bid_ask(row.get('yes_bid'), row.get('yes_ask')):<11}  "
                    f"{_snapshot_bid_ask(row.get('no_bid'), row.get('no_ask')):<11}  "
                    f"{probability_text:>7}  {fair_text:>8}  {row.get('best_edge') or 'none'}"
                )
        else:
            parts = [
                f"{row['label']} Y {_snapshot_bid_ask(row.get('yes_bid'), row.get('yes_ask'))} "
                f"N {_snapshot_bid_ask(row.get('no_bid'), row.get('no_ask'))}"
                for row in market_rows
            ]
            lines.extend(_snapshot_chunked_lines(parts, max_len=120))
    return "\n".join(lines)
def _trader_table_row(
    payload: dict[str, Any],
    *,
    starting_cash: float | None,
    closed_pnl_dollars: float = 0.0,
) -> dict[str, Any]:
    context = payload.get("context") or {}
    decision = payload.get("decision") or {}
    candidate = _selected_trade_candidate(context, decision) or {}
    paper_order = payload.get("paper_order") or {}
    paper_execution = payload.get("paper_execution") or {}
    fill = paper_execution.get("fill") or {}
    open_positions = payload.get("open_positions") or context.get("positions") or []
    action = _trader_action_label(payload)
    is_hold = action == "HOLD"
    side = fill.get("side") or decision.get("side") or paper_order.get("side")
    bracket = (
        fill.get("bracket_label")
        or decision.get("bracket")
        or (paper_order.get("metadata") or {}).get("bracket_label")
        or candidate.get("bracket_label")
    )
    price = fill.get("price_cents") or paper_order.get("limit_price_cents") or decision.get("limit_price_cents")
    quantity = fill.get("quantity") or paper_order.get("quantity") or decision.get("max_contracts")
    edge = decision.get("estimated_edge_cents")
    if edge in (None, 0, 0.0) and candidate:
        edge = candidate.get("fee_adjusted_edge_cents")
    portfolio = payload.get("portfolio")
    if not portfolio:
        values = _trader_portfolio_values(
            context,
            open_positions,
            starting_cash=starting_cash,
            closed_pnl_dollars=closed_pnl_dollars,
        )
        portfolio = {
            "cash_value": values["cash_value"],
            "equity_value": values["total_value"],
            "open_pnl_value": values["open_pnl_value"],
            "open_exposure_value": _trader_open_position_cost(open_positions),
            "total_contracts": sum(int(position.get("quantity") or 0) for position in open_positions),
        }
    cash_value = portfolio.get("cash_value")
    equity_value = portfolio.get("equity_value", portfolio.get("total_value"))
    open_pnl_value = portfolio.get("open_pnl_value") or 0.0
    exposure_value = portfolio.get("open_exposure_value") or 0.0
    total_contracts = portfolio.get("total_contracts") or sum(
        int(position.get("quantity") or 0) for position in open_positions
    )
    return {
        "time": _trader_local_time(payload),
        "action": action,
        "side": "-" if is_hold else side or "-",
        "bracket": "-" if is_hold else _compact_bracket(bracket),
        "px": "-" if is_hold else _fmt_cents(price),
        "qty": "-" if is_hold else quantity or "-",
        "edge": _fmt_edge(edge),
        "conf": _compact_confidence(decision.get("confidence")),
        "top": _trader_top_probability_bracket(context),
        "cash": _fmt_dollars(cash_value) if cash_value is not None else "--",
        "total": _fmt_dollars(equity_value) if equity_value is not None else "--",
        "equity": _fmt_dollars(equity_value) if equity_value is not None else "--",
        "pos": len(open_positions),
        "contracts": int(total_contracts),
        "exposure": _fmt_dollars(exposure_value),
        "open_pnl": _fmt_signed_dollars(open_pnl_value),
        "note": _trader_row_note(payload, action),
        "note_full": _trader_row_note(payload, action, max_len=1000),
        "cash_value": cash_value,
        "position_value": portfolio.get("position_value"),
        "equity_value": equity_value,
        "total_value": equity_value,
        "open_exposure_value": exposure_value,
        "open_pnl_value": open_pnl_value,
        "closed_pnl_value": portfolio.get("closed_pnl_value"),
    }


def _format_trader_table_header() -> str:
    header = "  ".join(_fit_cell(title, width) for _, title, width, _ in _TRADER_TABLE_COLUMNS)
    separator = "  ".join("-" * width for _, _, width, _ in _TRADER_TABLE_COLUMNS)
    return f"{header}\n{separator}"


def _format_trader_table_row(row: dict[str, Any]) -> str:
    return "  ".join(
        _fit_cell(row.get(key), width, align=align)
        for key, _, width, align in _TRADER_TABLE_COLUMNS
    )


def _trader_combined_table_row(row: dict[str, Any]) -> dict[str, Any]:
    trade = "-"
    if row.get("side") != "-" or row.get("bracket") != "-":
        trade = f"{row.get('side', '-')} {row.get('bracket', '-')}".strip()
    order = "-"
    if row.get("qty") != "-" or row.get("px") != "-":
        order = f"{row.get('qty', '-')} @ {row.get('px', '-')}"
    return {
        "time": row.get("time"),
        "action": row.get("action"),
        "trade": trade,
        "order": order,
        "edge": row.get("edge"),
        "conf": row.get("conf"),
        "top": row.get("top"),
        "cash": row.get("cash"),
        "equity": row.get("equity") or row.get("total"),
        "pos": row.get("pos"),
        "contracts": row.get("contracts"),
        "exposure": row.get("exposure"),
        "open_pnl": row.get("open_pnl"),
        "note": row.get("note_full") or row.get("note"),
    }


def _format_trader_combined_table_header() -> str:
    fixed_columns = [column for column in _TRADER_COMBINED_TABLE_COLUMNS if column[0] != "note"]
    header = "  ".join(_fit_cell(title, width) for _, title, width, _ in fixed_columns)
    separator = "  ".join("-" * width for _, _, width, _ in fixed_columns)
    header = f"{header}  Note"
    separator = f"{separator}  {'-' * 44}"
    return f"{header}\n{separator}"


def _format_trader_combined_table_row(row: dict[str, Any]) -> str:
    combined = _trader_combined_table_row(row)
    fixed_columns = [column for column in _TRADER_COMBINED_TABLE_COLUMNS if column[0] != "note"]
    prefix = "  ".join(
        _fit_cell(combined.get(key), width, align=align)
        for key, _, width, align in fixed_columns
    )
    note = str(combined.get("note") or "-").replace("\n", " ")
    return f"{prefix}  {note}"


def _trader_paper_run_header(
    *,
    series: str,
    station: str,
    decision_mode: str = "rules",
    strategy: str = "hybrid",
    starting_cash: float | None,
    interval_seconds: int,
    duration_minutes: float | None,
    max_iterations: int | None,
    loaded_existing_portfolio: bool = False,
    portfolio: dict[str, Any] | None = None,
    implicit_resume_warning: bool = False,
) -> str:
    portfolio = portfolio or {}
    return "\n".join(
        [
            "Kalshi Weather Trader Paper Run",
            "================================",
            f"Mode: fake_money_only | Decision: {decision_mode} | Strategy: {strategy}",
            f"Series: {series} | Station: {station}",
            "Live trading: DISABLED | Real orders: NOT AVAILABLE",
            (
                f"Starting cash: {_fmt_dollars(starting_cash)} | Interval: {interval_seconds}s | "
                f"Duration: {_fmt_duration_label(duration_minutes, max_iterations)}"
            ),
            f"Loaded existing paper portfolio: {'yes' if loaded_existing_portfolio else 'no'}",
            f"Portfolio cash at run start: {portfolio.get('cash') or '--'}",
            f"Portfolio total value at run start: {portfolio.get('equity') or '--'}",
            f"Open exposure at run start: {portfolio.get('open_exposure') or '--'}",
            f"Open contracts at run start: {portfolio.get('total_contracts', 0)}",
            "WARNING: Resuming existing paper portfolio from journal." if implicit_resume_warning else "",
            "",
        ]
    )


def _trader_table_should_print(row: dict[str, Any], *, quiet: bool) -> bool:
    if not quiet:
        return True
    return row.get("action") in {"POST", "FILL", "BUY", "SELL", "CLOSE", "CANCEL", "REJECT"}


def _trader_compact_line(row: dict[str, Any]) -> str:
    action = row["action"]
    if action == "HOLD":
        return (
            f"{row['time']} HOLD | top {row['top']} | edge {row['edge']} | "
            f"cash {row['cash']} | total {row['total']} | pos {row['pos']} | {row['note']}"
        )
    return (
        f"{row['time']} {action} {row['side']} {row['bracket']} @ {row['px']} x{row['qty']} | "
        f"edge {row['edge']} | cash {row['cash']} | total {row['total']} | pos {row['pos']} | {row['note']}"
    )


def _trader_json_line(row: dict[str, Any], payload: dict[str, Any]) -> str:
    compact = {
        "type": "iteration",
        "iteration": payload.get("iteration"),
        "time": row["time"],
        "action": row["action"],
        "side": row["side"],
        "bracket": row["bracket"],
        "price_cents": None if row["px"] == "-" else row["px"].rstrip("c"),
        "quantity": None if row["qty"] == "-" else row["qty"],
        "edge_cents": row["edge"],
        "confidence": row["conf"],
        "top": row["top"],
        "cash": row["cash"],
        "equity": row["equity"],
        "open_positions": row["pos"],
        "contracts": row["contracts"],
        "exposure": row["exposure"],
        "open_pnl": row["open_pnl"],
        "note": row["note"],
        "paper_order_status": payload.get("paper_order_status"),
    }
    return json.dumps(compact, separators=(",", ":"), sort_keys=True)


def _trader_full_trade_board_text(context: dict[str, Any]) -> str:
    lines = [
        "Full Candidate Trade Board",
        "Action  Side  Bracket  Px    Fair   Edge   Max  Eligible  Money    Reason",
        "------  ----  -------  ----  -----  -----  ---  --------  -------  ------------------------------",
    ]
    for candidate in context.get("candidate_trades") or []:
        price = candidate.get("entry_price_cents")
        if candidate.get("action") in {"CLOSE", "SELL"}:
            price = candidate.get("exit_price_cents")
        lines.append(
            (
                f"{_fit_cell(candidate.get('action'), 6)}  "
                f"{_fit_cell(candidate.get('side'), 4)}  "
                f"{_fit_cell(_compact_bracket(candidate.get('bracket_label')), 7)}  "
                f"{_fit_cell(_fmt_cents(price), 4)}  "
                f"{_fit_cell(_fmt_cents(candidate.get('model_fair_cents')), 5)}  "
                f"{_fit_cell(_fmt_edge(candidate.get('fee_adjusted_edge_cents')), 5, align='right')}  "
                f"{_fit_cell(candidate.get('max_contracts'), 3, align='right')}  "
                f"{_fit_cell(candidate.get('eligible'), 8)}  "
                f"{_fit_cell(_fmt_dollars(candidate.get('risk_dollars')), 7, align='right')}  "
                f"{_short_label(candidate.get('ineligible_reason') or candidate.get('notes') or '', max_len=30)}"
            )
        )
    return "\n".join(lines)


def _trader_reasoning_text(payload: dict[str, Any]) -> str:
    decision = payload.get("decision") or {}
    exit_plan = decision.get("exit_plan") or {}
    lines = [
        "LLM Reasoning",
        f"Thesis: {decision.get('trader_thesis') or '--'}",
        f"Why this trade: {decision.get('why_this_trade') or '--'}",
        f"Why not likely bracket: {decision.get('why_not_most_likely_bracket') or '--'}",
        f"Why not other side: {decision.get('why_not_other_side') or '--'}",
        f"Risk notes: {decision.get('risk_notes') or '--'}",
        f"Exit plan: {json.dumps(exit_plan, sort_keys=True)}",
    ]
    return "\n".join(lines)


def _trader_prompt_text(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt")
    if prompt is None:
        return "Prompt\n-- not captured for this run"
    return "Prompt\n" + json.dumps(safe_console_payload(prompt), indent=2, sort_keys=True)


def _trader_optional_sections(
    payload: dict[str, Any],
    *,
    show_trade_board: bool,
    show_prompt: bool,
    show_llm_reasoning: bool,
) -> str:
    sections: list[str] = []
    if show_trade_board:
        sections.append(_trader_full_trade_board_text(payload.get("context") or {}))
    if show_llm_reasoning:
        sections.append(_trader_reasoning_text(payload))
    if show_prompt:
        sections.append(_trader_prompt_text(payload))
    return "\n\n".join(sections)


def _trader_verbose_paper_text(payload: dict[str, Any]) -> str:
    sections = [
        _trader_result_text(payload),
        _trader_full_trade_board_text(payload.get("context") or {}),
        _trader_reasoning_text(payload),
        _trader_prompt_text(payload),
        "Full Context JSON\n" + json.dumps(safe_console_payload(payload.get("context") or {}), indent=2, sort_keys=True),
    ]
    return "\n\n".join(sections)


def _trader_readable_paper_text(payload: dict[str, Any], *, show_prompt: bool = False) -> str:
    sections = [
        _trader_result_text(payload),
        _trader_snapshot_text(payload, style="table"),
        _trader_full_trade_board_text(payload.get("context") or {}),
        _trader_reasoning_text(payload),
    ]
    if show_prompt:
        sections.append(_trader_prompt_text(payload))
    return "\n\n".join(sections)


def _unique_report_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")


def _trader_paper_summary_text(
    *,
    iterations: int,
    approved_actions: int,
    rejected: int,
    fake_orders: int,
    cash: Any,
    equity: Any,
    open_positions: int,
    open_pnl: Any,
    closed_pnl: float,
    journal_json_path: Path,
) -> str:
    return "\n".join(
        [
            "",
            "Paper Run Complete",
            "==================",
            f"Iterations: {iterations} | Approved actions: {approved_actions} | Rejected: {rejected} | Fake orders: {fake_orders}",
            (
                f"Cash: {cash or '--'} | Equity: {equity or '--'} | Open positions: {open_positions} | "
                f"Open P/L: {open_pnl or '--'} | Closed P/L: {_fmt_signed_dollars(closed_pnl)}"
            ),
            f"Journal: {journal_json_path}",
        ]
    )


def _trader_limit_breaches(snapshot: dict[str, Any], risk_limits: RiskLimits) -> list[str]:
    breaches: list[str] = []
    if float(snapshot.get("open_exposure_value") or 0) > risk_limits.max_total_exposure_dollars:
        breaches.append("max_total_exposure_dollars")
    if -(float(snapshot.get("open_pnl_value") or 0)) > risk_limits.max_open_loss_dollars:
        breaches.append("max_open_loss_dollars")
    drawdown = snapshot.get("drawdown_value")
    if drawdown is not None and float(drawdown) > risk_limits.max_total_drawdown_dollars:
        breaches.append("max_total_drawdown_dollars")
    for bracket, exposure in (snapshot.get("exposure_by_bracket") or {}).items():
        if float(exposure) > risk_limits.max_exposure_dollars_per_bracket:
            breaches.append(f"max_exposure_dollars_per_bracket:{_compact_bracket(bracket, max_len=20)}")
    for bracket, contracts in (snapshot.get("contracts_by_bracket") or {}).items():
        if int(contracts) > risk_limits.max_contracts_per_bracket:
            breaches.append(f"max_contracts_per_bracket:{_compact_bracket(bracket, max_len=20)}")
    for side, contracts in (snapshot.get("contracts_by_side") or {}).items():
        if int(contracts) > risk_limits.max_contracts_per_side:
            breaches.append(f"max_contracts_per_side:{side}")
    return breaches


def _trader_portfolio_audit_text(payload: dict[str, Any]) -> str:
    snapshot = payload.get("snapshot") or {}
    lines = [
        "Kalshi Weather LLM Trader Portfolio Audit",
        "=========================================",
        f"Race ID: {payload.get('race_id') or '--'} | Journal: {payload.get('journal_path')}",
        f"Cash: {snapshot.get('cash') or '--'} | Equity: {snapshot.get('equity') or '--'}",
        (
            f"Open P/L: {snapshot.get('open_pnl') or '--'} | Closed P/L: {snapshot.get('closed_pnl') or '--'} | "
            f"Exposure: {snapshot.get('open_exposure') or '--'}"
        ),
        (
            f"Open position groups: {snapshot.get('position_groups', 0)} | "
            f"Total contracts: {snapshot.get('total_contracts', 0)}"
        ),
        "",
        "Open Positions",
    ]
    positions = payload.get("positions") or []
    if not positions:
        lines.append("- none")
    else:
        lines.append("Side  Bracket              Qty    Avg    Mark   Max loss")
        lines.append("----  -------------------  -----  -----  -----  --------")
        context = payload.get("context") or {}
        for position in positions:
            mark = _market_exit_price_for_position(context, position)
            max_loss = (int(position.get("quantity") or 0) * (_float_or_none(position.get("avg_entry_price_cents")) or 0)) / 100.0
            lines.append(
                f"{_fit_cell(position.get('side'), 4)}  "
                f"{_fit_cell(_compact_bracket(position.get('bracket_label'), max_len=19), 19)}  "
                f"{_fit_cell(position.get('quantity'), 5, align='right')}  "
                f"{_fit_cell(_fmt_cents(position.get('avg_entry_price_cents')), 5)}  "
                f"{_fit_cell(_fmt_cents(mark), 5)}  "
                f"{_fit_cell(_fmt_dollars(max_loss), 8, align='right')}"
            )
    lines.extend(["", "Exposure By Bracket"])
    by_bracket = snapshot.get("exposure_by_bracket") or {}
    if by_bracket:
        for bracket, exposure in by_bracket.items():
            contracts = (snapshot.get("contracts_by_bracket") or {}).get(bracket, 0)
            lines.append(f"- {_compact_bracket(bracket, max_len=28)}: {_fmt_dollars(exposure)} | {contracts} contracts")
    else:
        lines.append("- none")
    lines.extend(["", "Exposure By Side"])
    by_side = snapshot.get("exposure_by_side") or {}
    if by_side:
        for side, exposure in by_side.items():
            contracts = (snapshot.get("contracts_by_side") or {}).get(side, 0)
            lines.append(f"- {side}: {_fmt_dollars(exposure)} | {contracts} contracts")
    else:
        lines.append("- none")
    breaches = payload.get("limit_breaches") or []
    lines.extend(["", f"Limits breached: {'yes' if breaches else 'no'}"])
    if breaches:
        lines.extend(f"- {breach}" for breach in breaches)
    return "\n".join(lines)


def _trader_paper_settlement_text(payload: dict[str, Any]) -> str:
    before = payload.get("portfolio_before") or {}
    after = payload.get("portfolio_after") or {}
    settlement = payload.get("settlement") or {}
    status = "dry run" if payload.get("dry_run") else "executed"
    if not settlement.get("settled_positions"):
        status = "no open positions"
    lines = [
        "Kalshi Weather Paper Settlement",
        "================================",
        "Mode: fake_money_only | Live trading: DISABLED | Real orders: NOT AVAILABLE",
        f"Journal: {payload.get('journal_path')}",
        (
            f"Series: {payload.get('series')} | Station: {payload.get('station')} | "
            f"Target: {payload.get('target_date')}"
        ),
        (
            f"Final high: {_fmt_f(payload.get('final_high_f'))} | "
            f"Winning bracket: {payload.get('winning_bracket') or '--'} | Status: {status}"
        ),
        "",
        (
            f"Positions settled: {settlement.get('positions_settled', 0)} | "
            f"Contracts: {settlement.get('contracts_settled', 0)} | "
            f"Open orders canceled: {settlement.get('open_orders_canceled', 0)}"
        ),
        (
            f"Settlement value: {_fmt_dollars(settlement.get('settlement_value_dollars'))} | "
            f"Settlement P/L: {_fmt_signed_dollars(settlement.get('realized_pnl_dollars'))}"
        ),
        (
            f"Cash: {before.get('cash') or '--'} -> {after.get('cash') or '--'} | "
            f"Equity: {before.get('equity') or '--'} -> {after.get('equity') or '--'}"
        ),
        (
            f"Closed P/L: {before.get('closed_pnl') or '--'} -> {after.get('closed_pnl') or '--'} | "
            f"Open P/L: {before.get('open_pnl') or '--'} -> {after.get('open_pnl') or '--'}"
        ),
        "",
        "Settled positions",
    ]
    rows = settlement.get("settled_positions") or []
    if not rows:
        lines.append("- none")
    for row in rows:
        result = "win" if int(row.get("settled_result") or 0) else "loss"
        lines.append(
            f"- {row.get('quantity')} {row.get('side')} {row.get('bracket')} "
            f"avg {_fmt_cents(row.get('avg_entry_price_cents'))} -> "
            f"{result} @ {_fmt_cents(row.get('settlement_price_cents'))} | "
            f"P/L {_fmt_signed_dollars(row.get('realized_pnl_dollars'))}"
        )
    return "\n".join(lines)


def _clv_records_from_journal_runs(runs: list[dict[str, Any]]) -> tuple[list[CLVRecord], list[dict[str, Any]]]:
    latest_by_fill: dict[str, dict[str, Any]] = {}
    for run in runs:
        for sample in run.get("clv_samples") or []:
            fill_key = str(sample.get("fill_id") or sample.get("selected_candidate_id") or "")
            if not fill_key:
                continue
            existing = latest_by_fill.get(fill_key)
            elapsed = _float_or_none(sample.get("elapsed_minutes")) or -1.0
            existing_elapsed = _float_or_none((existing or {}).get("elapsed_minutes")) or -1.0
            if existing is None or elapsed >= existing_elapsed:
                latest_by_fill[fill_key] = sample

    records: list[CLVRecord] = []
    rows: list[dict[str, Any]] = []
    for sample in latest_by_fill.values():
        entry = _float_or_none(sample.get("entry_price_cents"))
        if entry is None:
            continue
        record = CLVRecord(
            fill_id=str(sample.get("fill_id") or sample.get("selected_candidate_id") or ""),
            bracket=_canonical_bracket_label(sample.get("bracket_label")),
            side=str(sample.get("side") or ""),
            entry_price_cents=entry,
        )
        horizon_fields = {
            "5m": "market_mid_after_5_min",
            "15m": "market_mid_after_15_min",
            "30m": "market_mid_after_30_min",
            "60m": "market_mid_after_60_min",
            "latest": "current_side_mid_cents",
            "final": "final_pre_settlement_mid",
        }
        for horizon, field_name in horizon_fields.items():
            mark = _float_or_none(sample.get(field_name))
            if mark is not None:
                record.marks[horizon] = mark
        records.append(record)
        rows.append(
            {
                "fill_id": record.fill_id,
                "selected_candidate_id": sample.get("selected_candidate_id"),
                "bracket": record.bracket,
                "side": record.side,
                "entry_price_cents": record.entry_price_cents,
                "latest_mark_cents": record.marks.get("latest"),
                "elapsed_minutes": sample.get("elapsed_minutes"),
                "clv_5m_cents": record.clv("5m"),
                "clv_15m_cents": record.clv("15m"),
                "clv_30m_cents": record.clv("30m"),
                "clv_60m_cents": record.clv("60m"),
                "clv_latest_cents": record.clv("latest"),
                "clv_final_cents": record.clv("final"),
                "adverse_selection_flag": record.adverse_selection("latest"),
            }
        )
    rows.sort(key=lambda row: str(row.get("fill_id") or ""))
    return records, rows


def _trader_clv_report_text(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("fills") or []
    lines = [
        "Kalshi Weather Trader CLV Report",
        "================================",
        f"Journal: {payload.get('journal_path')}",
        (
            f"Fills: {summary.get('fills_count', 0)} | Observed: {summary.get('observed_count', 0)} | "
            f"Avg CLV: {_fmt_edge(summary.get('avg_clv_cents'))} | "
            f"Positive: {100.0 * float(summary.get('percent_positive_clv') or 0.0):.1f}%"
        ),
        "",
        "Fill        Side  Bracket  Entry  Latest  CLV 5m  CLV 15m  CLV 30m  CLV 60m  CLV latest",
        "----------  ----  -------  -----  ------  ------  -------  -------  -------  ----------",
    ]
    for row in rows[:50]:
        lines.append(
            f"{_fit_cell(row.get('fill_id'), 10)}  "
            f"{_fit_cell(row.get('side'), 4)}  "
            f"{_fit_cell(row.get('bracket'), 7)}  "
            f"{_fit_cell(_fmt_cents(row.get('entry_price_cents')), 5)}  "
            f"{_fit_cell(_fmt_cents(row.get('latest_mark_cents')), 6)}  "
            f"{_fit_cell(_fmt_edge(row.get('clv_5m_cents')), 6, align='right')}  "
            f"{_fit_cell(_fmt_edge(row.get('clv_15m_cents')), 7, align='right')}  "
            f"{_fit_cell(_fmt_edge(row.get('clv_30m_cents')), 7, align='right')}  "
            f"{_fit_cell(_fmt_edge(row.get('clv_60m_cents')), 7, align='right')}  "
            f"{_fit_cell(_fmt_edge(row.get('clv_latest_cents')), 10, align='right')}"
        )
    if len(rows) > 50:
        lines.append(f"... {len(rows) - 50} more fill(s)")
    return "\n".join(lines)


@app.command("trader-context")
def trader_context(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("trader_agent", "--race-id"),
    model_key: str | None = typer.Option(None, "--model-key"),
    min_edge_cents: float = typer.Option(3.0, "--min-edge-cents"),
    max_contracts_per_trade: int = typer.Option(100, "--max-contracts-per-trade"),
    max_risk_dollars_per_trade: float = typer.Option(50.0, "--max-risk-dollars-per-trade"),
    max_total_exposure_dollars: float = typer.Option(250.0, "--max-total-exposure-dollars"),
    max_exposure_dollars_per_bracket: float = typer.Option(100.0, "--max-exposure-dollars-per-bracket"),
    max_open_positions: int = typer.Option(4, "--max-open-positions"),
    min_volume: int = typer.Option(0, "--min-volume"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    journal_path: str = typer.Option("reports/trader_agent/trader_runs.sqlite", "--journal-path"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Build and print the full fake-money LLM trader context without requiring an LLM."""
    _ = (dry_run, verbose)
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    risk_limits = _trader_risk_limits(
        min_edge_cents=min_edge_cents,
        max_contracts_per_trade=max_contracts_per_trade,
        max_risk_dollars_per_trade=max_risk_dollars_per_trade,
        max_total_exposure_dollars=max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=max_exposure_dollars_per_bracket,
        max_open_positions=max_open_positions,
        min_volume=min_volume,
    )
    context = _trader_context_for_cli(
        settings=settings,
        store_obj=_store(settings),
        series=series,
        station=station,
        target_date=resolved_target_date,
        race_id=race_id,
        model_key=model_key,
        risk_limits=risk_limits,
        journal_path=journal_path,
    )
    payload = context.to_dict()
    text = _trader_context_text(context)
    _emit_report(payload, json_output=json_output, output=output, text=text)


@app.command("trader-recommend")
def trader_recommend(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("trader_agent", "--race-id"),
    model_key: str | None = typer.Option(None, "--model-key"),
    decision_mode: str = typer.Option("rules", "--decision-mode"),
    strategy: str = typer.Option("hybrid", "--strategy"),
    order_style: str = typer.Option("passive", "--order-style"),
    paper_fill_price_mode: str = typer.Option("conservative", "--paper-fill-price-mode"),
    profile_mode: str = typer.Option("fixed", "--profile-mode"),
    profile_config: str | None = typer.Option(None, "--profile-config"),
    lifecycle_active_profile: str | None = typer.Option(None, "--lifecycle-active-profile"),
    model_authoritative: bool = typer.Option(False, "--model-authoritative/--no-model-authoritative"),
    probability_blend_mode: str = typer.Option("raw", "--probability-blend-mode"),
    probability_blend_config: str | None = typer.Option(None, "--probability-blend-config"),
    model_weight: float | None = typer.Option(None, "--model-weight"),
    market_weight: float | None = typer.Option(None, "--market-weight"),
    use_market_implied_probability_as_prior: str = typer.Option("true", "--use-market-implied-probability-as-prior"),
    llm_provider: str = typer.Option("dry-run", "--llm-provider"),
    model: str | None = typer.Option(None, "--model", "--llm-model"),
    llm_host: str | None = typer.Option(None, "--llm-host"),
    llm_timeout_seconds: int = typer.Option(60, "--llm-timeout-seconds"),
    llm_temperature: float = typer.Option(0.0, "--llm-temperature"),
    min_edge_cents: float = typer.Option(3.0, "--min-edge-cents"),
    max_contracts_per_trade: int = typer.Option(100, "--max-contracts-per-trade"),
    max_risk_dollars_per_trade: float = typer.Option(50.0, "--max-risk-dollars-per-trade"),
    max_total_exposure_dollars: float = typer.Option(250.0, "--max-total-exposure-dollars"),
    max_exposure_dollars_per_bracket: float = typer.Option(100.0, "--max-exposure-dollars-per-bracket"),
    max_open_positions: int = typer.Option(4, "--max-open-positions"),
    min_volume: int = typer.Option(0, "--min-volume"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    journal_path: str = typer.Option("reports/trader_agent/trader_runs.sqlite", "--journal-path"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Ask the LLM trader for one validated fake-money recommendation."""
    _ = verbose
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    store_obj = _store(settings)
    risk_limits = _trader_risk_limits(
        min_edge_cents=min_edge_cents,
        max_contracts_per_trade=max_contracts_per_trade,
        max_risk_dollars_per_trade=max_risk_dollars_per_trade,
        max_total_exposure_dollars=max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=max_exposure_dollars_per_bracket,
        max_open_positions=max_open_positions,
        min_volume=min_volume,
    )
    context = _trader_context_for_cli(
        settings=settings,
        store_obj=store_obj,
        series=series,
        station=station,
        target_date=resolved_target_date,
        race_id=race_id,
        model_key=model_key,
        risk_limits=risk_limits,
        journal_path=journal_path,
    )
    agent = TraderAgent(
        llm_client=_trader_llm_client(
            llm_provider=llm_provider,
            model=model,
            llm_host=llm_host,
            timeout_seconds=llm_timeout_seconds,
            temperature=llm_temperature,
            dry_run=dry_run,
        )
    )
    result = agent.recommend(context)
    paper_order = decision_to_paper_order(result.decision, result.validation)
    payload = result.to_dict()
    payload["paper_order"] = paper_order.to_dict() if paper_order else None
    payload["paper_order_status"] = "validated_fake_order_preview" if paper_order else "no_fake_order"
    _trader_journal(journal_path).record_run(payload)
    _emit_report(payload, json_output=json_output, output=output, text=_trader_result_text(payload))


@app.command("trader-paper-run")
def trader_paper_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("trader_agent", "--race-id"),
    debug_run_id: str | None = typer.Option(None, "--debug-run-id"),
    model_key: str | None = typer.Option(None, "--model-key"),
    decision_mode: str = typer.Option("rules", "--decision-mode"),
    strategy: str = typer.Option("hybrid", "--strategy"),
    order_style: str = typer.Option("passive", "--order-style"),
    cancel_existing_passive_orders_on_taker_start: bool = typer.Option(True, "--cancel-existing-passive-orders-on-taker-start/--no-cancel-existing-passive-orders-on-taker-start"),
    paper_fill_price_mode: str = typer.Option("conservative", "--paper-fill-price-mode"),
    profile_mode: str = typer.Option("fixed", "--profile-mode"),
    profile_config: str | None = typer.Option(None, "--profile-config"),
    lifecycle_active_profile: str | None = typer.Option(None, "--lifecycle-active-profile"),
    model_authoritative: bool = typer.Option(False, "--model-authoritative/--no-model-authoritative"),
    probability_blend_mode: str = typer.Option("raw", "--probability-blend-mode"),
    probability_blend_config: str | None = typer.Option(None, "--probability-blend-config"),
    model_weight: float | None = typer.Option(None, "--model-weight"),
    market_weight: float | None = typer.Option(None, "--market-weight"),
    use_market_implied_probability_as_prior: str = typer.Option("true", "--use-market-implied-probability-as-prior"),
    llm_provider: str = typer.Option("dry-run", "--llm-provider"),
    model: str | None = typer.Option(None, "--model", "--llm-model"),
    llm_host: str | None = typer.Option(None, "--llm-host"),
    llm_timeout_seconds: int = typer.Option(60, "--llm-timeout-seconds"),
    llm_temperature: float = typer.Option(0.0, "--llm-temperature"),
    interval_seconds: int = typer.Option(60, "--interval-seconds"),
    model_refresh_seconds: int = typer.Option(0, "--model-refresh-seconds"),
    market_refresh_seconds: int = typer.Option(60, "--market-refresh-seconds"),
    fast_model_refresh_seconds: int = typer.Option(300, "--fast-model-refresh-seconds"),
    noaa_model_refresh_seconds: int = typer.Option(900, "--noaa-model-refresh-seconds"),
    observation_refresh_seconds: int = typer.Option(300, "--observation-refresh-seconds"),
    noaa_model_mode: str = typer.Option("full_recompute_each_iteration", "--noaa-model-mode"),
    use_cached_models: bool = typer.Option(False, "--use-cached-models/--no-use-cached-models"),
    force_model_recompute_every_iteration: bool = typer.Option(
        True,
        "--force-model-recompute-every-iteration/--no-force-model-recompute-every-iteration",
    ),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    until_local_time: str | None = typer.Option(None, "--until-local-time"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    min_edge_cents: float = typer.Option(3.0, "--min-edge-cents"),
    max_contracts_per_trade: int = typer.Option(100, "--max-contracts-per-trade"),
    max_risk_dollars_per_trade: float = typer.Option(50.0, "--max-risk-dollars-per-trade"),
    max_total_exposure_dollars: float = typer.Option(250.0, "--max-total-exposure-dollars"),
    max_exposure_dollars_per_bracket: float = typer.Option(100.0, "--max-exposure-dollars-per-bracket"),
    max_contracts_per_bracket: int = typer.Option(500, "--max-contracts-per-bracket"),
    max_contracts_per_side: int = typer.Option(1000, "--max-contracts-per-side"),
    max_open_positions: int = typer.Option(4, "--max-open-positions"),
    max_open_orders: int = typer.Option(4, "--max-open-orders"),
    max_total_open_risk_groups: int | None = typer.Option(None, "--max-total-open-risk-groups"),
    allow_negative_cash: bool = typer.Option(False, "--allow-negative-cash"),
    allow_scale_in: bool = typer.Option(False, "--allow-scale-in/--no-allow-scale-in"),
    scale_in_edge_buffer_cents: float = typer.Option(0.0, "--scale-in-edge-buffer-cents"),
    same_candidate_cooldown_minutes: float = typer.Option(15.0, "--same-candidate-cooldown-minutes"),
    max_open_loss_dollars: float = typer.Option(100.0, "--max-open-loss-dollars"),
    max_total_drawdown_dollars: float = typer.Option(150.0, "--max-total-drawdown-dollars"),
    min_yes_edge_cents: float | None = typer.Option(None, "--min-yes-edge-cents"),
    min_no_edge_cents: float | None = typer.Option(None, "--min-no-edge-cents"),
    min_no_upside_cents: float = typer.Option(8.0, "--min-no-upside-cents"),
    max_no_bin_probability: float = typer.Option(0.20, "--max-no-bin-probability"),
    no_probability_filter_mode: str | None = typer.Option(None, "--no-probability-filter-mode"),
    no_probability_penalty_start: float | None = typer.Option(None, "--no-probability-penalty-start"),
    no_probability_penalty_factor: float = typer.Option(0.30, "--no-probability-penalty-factor"),
    absolute_no_bin_probability_cap: float = typer.Option(0.60, "--absolute-no-bin-probability-cap"),
    max_spread_cents: int = typer.Option(4, "--max-spread-cents"),
    edge_comparison_epsilon_cents: float = typer.Option(0.001, "--edge-comparison-epsilon-cents"),
    slippage_cents: float = typer.Option(0.5, "--slippage-cents"),
    tail_risk_padding_cents: float = typer.Option(2.0, "--tail-risk-padding-cents"),
    passive_improvement_cents: int = typer.Option(1, "--passive-improvement-cents"),
    allow_lowball_passive_orders: bool = typer.Option(False, "--allow-lowball-passive-orders"),
    max_passive_distance_from_bid_cents: float = typer.Option(1.0, "--max-passive-distance-from-bid-cents"),
    max_passive_order_age_minutes: float = typer.Option(15.0, "--max-passive-order-age-minutes"),
    model_consensus_enabled: bool = typer.Option(True, "--model-consensus-enabled/--no-model-consensus"),
    consensus_method: str = typer.Option("family_weighted_iqr", "--consensus-method"),
    outlier_threshold_f: float = typer.Option(4.0, "--outlier-threshold-f"),
    consensus_max_spread_f: float = typer.Option(3.0, "--consensus-max-spread-f"),
    full_spread_high_threshold_f: float = typer.Option(5.0, "--full-spread-high-threshold-f"),
    full_spread_extreme_threshold_f: float = typer.Option(8.0, "--full-spread-extreme-threshold-f"),
    min_non_eliminated_bin_probability: float = typer.Option(0.005, "--min-non-eliminated-bin-probability"),
    min_tail_probability_when_disputed: float = typer.Option(0.01, "--min-tail-probability-when-disputed"),
    block_high_confidence_no_on_extreme_spread: bool = typer.Option(
        False,
        "--block-high-confidence-no-on-extreme-spread/--no-block-high-confidence-no-on-extreme-spread",
    ),
    extreme_spread_no_block_threshold_f: float = typer.Option(8.0, "--extreme-spread-no-block-threshold-f"),
    block_no_on_model_source_degraded: bool = typer.Option(
        False,
        "--block-no-on-model-source-degraded/--no-block-no-on-model-source-degraded",
    ),
    high_spread_reduce_size_factor: float = typer.Option(0.5, "--high-spread-reduce-size-factor"),
    clustered_disputed_extra_edge_cents: float = typer.Option(2.0, "--clustered-disputed-extra-edge-cents"),
    degraded_model_edge_buffer_cents: float = typer.Option(2.0, "--degraded-model-edge-buffer-cents"),
    degraded_model_size_factor: float = typer.Option(0.5, "--degraded-model-size-factor"),
    model_authoritative_tight_spread_f: float = typer.Option(3.0, "--model-authoritative-tight-spread-f"),
    model_authoritative_wide_spread_f: float = typer.Option(5.0, "--model-authoritative-wide-spread-f"),
    model_authoritative_extreme_spread_f: float = typer.Option(7.0, "--model-authoritative-extreme-spread-f"),
    allow_cheap_ask_yes_with_missing_bid: bool = typer.Option(
        False,
        "--allow-cheap-ask-yes-with-missing-bid/--no-allow-cheap-ask-yes-with-missing-bid",
    ),
    cheap_ask_yes_max_cents: float = typer.Option(2.0, "--cheap-ask-yes-max-cents"),
    cheap_ask_yes_min_net_edge_cents: float = typer.Option(8.0, "--cheap-ask-yes-min-net-edge-cents"),
    cheap_ask_yes_max_contracts: int = typer.Option(25, "--cheap-ask-yes-max-contracts"),
    reset_paper_portfolio: bool = typer.Option(False, "--reset-paper-portfolio"),
    new_paper_portfolio: bool = typer.Option(False, "--new-paper-portfolio"),
    resume_paper_portfolio: bool = typer.Option(False, "--resume-paper-portfolio"),
    min_volume: int = typer.Option(0, "--min-volume"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    journal_path: str | None = typer.Option(None, "--journal-path"),
    json_output: bool = typer.Option(False, "--json"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
    output_style: str = typer.Option("table", "--output-style"),
    quiet: bool = typer.Option(False, "--quiet"),
    verbose: bool = typer.Option(False, "--verbose"),
    show_snapshot: str = typer.Option("changed", "--show-snapshot"),
    snapshot_every: int = typer.Option(5, "--snapshot-every"),
    snapshot_style: str = typer.Option("compact", "--snapshot-style"),
    show_models: bool = typer.Option(True, "--show-models/--hide-models"),
    show_market: bool = typer.Option(True, "--show-market/--hide-market"),
    no_snapshot: bool = typer.Option(False, "--no-snapshot"),
    show_market_snapshot: bool = typer.Option(False, "--show-market-snapshot"),
    show_trade_board: bool = typer.Option(False, "--show-trade-board"),
    show_prompt: bool = typer.Option(False, "--show-prompt"),
    show_llm_reasoning: bool = typer.Option(False, "--show-llm-reasoning"),
    debug_decision: bool = typer.Option(False, "--debug-decision"),
    explain_hold: bool = typer.Option(False, "--explain-hold"),
    candidate_table: bool = typer.Option(False, "--candidate-table"),
    candidate_table_limit: int = typer.Option(12, "--candidate-table-limit"),
    audit_pricing: bool = typer.Option(False, "--audit-pricing"),
    audit_portfolio: bool = typer.Option(False, "--audit-portfolio"),
    audit_data: bool = typer.Option(False, "--audit-data"),
    debug_output_dir: str | None = typer.Option(None, "--debug-output-dir"),
    debug_jsonl: str | None = typer.Option(None, "--debug-jsonl"),
    debug_csv: str | None = typer.Option(None, "--debug-csv"),
    model_cache_path: str | None = typer.Option(None, "--model-cache-path"),
    use_canonical_paths: bool = typer.Option(True, "--use-canonical-paths/--no-use-canonical-paths"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
    auto_package_run: bool = typer.Option(True, "--auto-package-run/--no-auto-package-run"),
    dump_decision_context: str | None = typer.Option(None, "--dump-decision-context"),
    show_rejections: str = typer.Option("summary", "--show-rejections"),
    show_pricing_table: bool = typer.Option(False, "--show-pricing-table"),
    show_risk_table: bool = typer.Option(False, "--show-risk-table"),
    show_settlement_scenarios: bool = typer.Option(False, "--show-settlement-scenarios"),
    settlement_scenario_style: str = typer.Option("compact", "--settlement-scenario-style"),
    fresh_journal: bool = typer.Option(False, "--fresh-journal"),
    i_understand_this_deletes_paper_state: bool = typer.Option(
        False,
        "--i-understand-this-deletes-paper-state",
    ),
) -> None:
    """Run deterministic or LLM-reviewed fake-money paper trader actions only."""
    decision_mode = _normalize_trader_decision_mode(decision_mode)
    strategy = _normalize_trader_strategy(strategy)
    order_style = _normalize_trader_order_style(order_style)
    paper_fill_price_mode = paper_fill_price_mode.strip().lower()
    if paper_fill_price_mode not in {"limit", "market", "conservative"}:
        raise typer.BadParameter("--paper-fill-price-mode must be limit, market, or conservative.")
    noaa_model_mode = noaa_model_mode.strip().lower()
    if noaa_model_mode not in {"full_recompute_each_iteration", "scheduled", "off", "always"}:
        raise typer.BadParameter(
            "--noaa-model-mode must be full_recompute_each_iteration or scheduled."
        )
    if (
        force_model_recompute_every_iteration
        or not use_cached_models
        or model_refresh_seconds <= 0
    ) and noaa_model_mode != "off":
        noaa_model_mode = "full_recompute_each_iteration"
    if model_refresh_seconds < 0:
        raise typer.BadParameter("--model-refresh-seconds must be 0 or greater.")
    for option_name, option_value in {
        "--market-refresh-seconds": market_refresh_seconds,
        "--fast-model-refresh-seconds": fast_model_refresh_seconds,
        "--noaa-model-refresh-seconds": noaa_model_refresh_seconds,
        "--observation-refresh-seconds": observation_refresh_seconds,
    }.items():
        if option_value < 1:
            raise typer.BadParameter(f"{option_name} must be at least 1.")
    if degraded_model_size_factor <= 0 or degraded_model_size_factor > 1:
        raise typer.BadParameter("--degraded-model-size-factor must be in (0, 1].")
    if model_authoritative:
        probability_blend_mode = "model_only"
        model_weight = 1.0 if model_weight is None else model_weight
        market_weight = 0.0 if market_weight is None else market_weight
        if no_probability_filter_mode is None:
            no_probability_filter_mode = "soft_penalty"
        use_market_implied_probability_as_prior = "false"
    if order_style == "taker" or model_authoritative:
        use_cached_models = False
        force_model_recompute_every_iteration = True
        model_refresh_seconds = 0
        if noaa_model_mode != "off":
            noaa_model_mode = "full_recompute_each_iteration"
    if no_probability_filter_mode is None:
        no_probability_filter_mode = "hard"
    no_probability_filter_mode = no_probability_filter_mode.strip().lower()
    if no_probability_filter_mode not in {"hard", "soft_penalty", "off"}:
        raise typer.BadParameter("--no-probability-filter-mode must be hard, soft_penalty, or off.")
    if no_probability_penalty_start is not None and (no_probability_penalty_start < 0 or no_probability_penalty_start > 1):
        raise typer.BadParameter("--no-probability-penalty-start must be between 0 and 1.")
    if no_probability_penalty_factor < 0:
        raise typer.BadParameter("--no-probability-penalty-factor must be 0 or greater.")
    if absolute_no_bin_probability_cap < 0 or absolute_no_bin_probability_cap > 1:
        raise typer.BadParameter("--absolute-no-bin-probability-cap must be between 0 and 1.")
    if model_weight is not None and (model_weight < 0 or model_weight > 1):
        raise typer.BadParameter("--model-weight must be between 0 and 1.")
    if market_weight is not None and (market_weight < 0 or market_weight > 1):
        raise typer.BadParameter("--market-weight must be between 0 and 1.")
    use_market_prior = _parse_bool_option(
        use_market_implied_probability_as_prior,
        option_name="--use-market-implied-probability-as-prior",
    )
    profile_mode = profile_mode.strip().lower()
    if profile_mode not in {"fixed", "fixed_test", "auto"}:
        raise typer.BadParameter("--profile-mode must be fixed, fixed_test, or auto.")
    probability_blend_mode = probability_blend_mode.strip().lower()
    if probability_blend_mode not in {"raw", "blend", "model_only"}:
        raise typer.BadParameter("--probability-blend-mode must be raw, blend, or model_only.")
    settlement_scenario_style = settlement_scenario_style.strip().lower()
    if settlement_scenario_style not in {"compact", "table", "full"}:
        raise typer.BadParameter("--settlement-scenario-style must be compact, table, or full.")
    output_style = output_style.strip().lower()
    if json_output:
        output_style = "json-lines"
    if verbose:
        output_style = "verbose"
        show_trade_board = True
        show_prompt = True
        show_llm_reasoning = True
    if output_style not in _TRADER_OUTPUT_STYLES:
        raise typer.BadParameter("--output-style must be table, combined, compact, readable, verbose, or json-lines.")
    show_snapshot = "never" if no_snapshot else show_snapshot.strip().lower()
    snapshot_style = snapshot_style.strip().lower()
    if show_market_snapshot and not no_snapshot and show_snapshot == "never":
        show_snapshot = "changed"
    if show_snapshot not in {"never", "every", "changed"}:
        raise typer.BadParameter("--show-snapshot must be never, every, or changed.")
    if snapshot_style not in {"compact", "table", "full"}:
        raise typer.BadParameter("--snapshot-style must be compact, table, or full.")
    if snapshot_every < 1:
        raise typer.BadParameter("--snapshot-every must be at least 1.")
    show_rejections = show_rejections.strip().lower()
    if show_rejections not in {"none", "summary", "top", "all"}:
        raise typer.BadParameter("--show-rejections must be none, summary, top, or all.")
    if candidate_table_limit < 1:
        raise typer.BadParameter("--candidate-table-limit must be at least 1.")
    consensus_method = consensus_method.strip().lower()
    if consensus_method not in {"family_weighted_iqr", "family_weighted_mad", "simple_iqr"}:
        raise typer.BadParameter("--consensus-method must be family_weighted_iqr, family_weighted_mad, or simple_iqr.")
    canonical_debug_run_id = sanitize_run_id(debug_run_id or race_id)
    if use_canonical_paths:
        ensure_canonical_dirs()
    run_dir_path = _resolve_cli_output_path(
        output_dir,
        get_run_dir(canonical_debug_run_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    debug_output_dir_path = _resolve_cli_output_path(
        debug_output_dir,
        run_dir_path,
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    journal_path_path = _resolve_cli_output_path(
        journal_path,
        get_journal_path(race_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    debug_jsonl_path = _resolve_cli_output_path(
        debug_jsonl,
        get_decisions_jsonl_path(canonical_debug_run_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    debug_csv_path = _resolve_cli_output_path(
        debug_csv,
        get_candidates_csv_path(canonical_debug_run_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    model_cache_path_path = _resolve_cli_output_path(
        model_cache_path,
        debug_output_dir_path / "model_refresh_cache.json",
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    output_dir = str(run_dir_path)
    debug_output_dir = str(debug_output_dir_path)
    journal_path = str(journal_path_path)
    debug_jsonl = str(debug_jsonl_path)
    debug_csv = str(debug_csv_path)
    model_cache_path = (
        str(model_cache_path_path)
        if model_cache_path and use_cached_models and not force_model_recompute_every_iteration and model_refresh_seconds > 0
        else None
    )
    profile_config = _resolve_repo_path_text(profile_config)
    probability_blend_config = _resolve_repo_path_text(probability_blend_config)
    profile_config_payload = _load_yaml_config(profile_config)
    probability_blend_config_payload = _load_yaml_config(probability_blend_config)
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    event_ticker = f"{series}-{resolved_target_date.strftime('%y%b%d').upper()}"
    store_obj = _store(settings)
    if until_local_time:
        parts = until_local_time.split(":", 1)
        if len(parts) != 2:
            raise typer.BadParameter("--until-local-time must be HH:MM.")
        hour, minute = (int(part) for part in parts)
        tz = ZoneInfo(LAX_TIMEZONE)
        now_local = datetime.now(tz)
        end_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now_local >= end_local:
            raise typer.BadParameter(f"--until-local-time {until_local_time} is already past in station local time.")
        duration_minutes = max(1, math.ceil((end_local - now_local).total_seconds() / 60.0))
    if max_iterations is None and duration_minutes is not None:
        max_iterations = max(1, math.ceil((duration_minutes * 60) / interval_seconds))
    max_iterations = max_iterations or 1
    risk_limits = _trader_risk_limits(
        min_edge_cents=min_edge_cents,
        max_contracts_per_trade=max_contracts_per_trade,
        max_risk_dollars_per_trade=max_risk_dollars_per_trade,
        max_total_exposure_dollars=max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=max_exposure_dollars_per_bracket,
        max_contracts_per_bracket=max_contracts_per_bracket,
        max_contracts_per_side=max_contracts_per_side,
        max_open_positions=max_open_positions,
        max_open_orders=max_open_orders,
        max_total_open_risk_groups=(
            max_total_open_risk_groups if max_total_open_risk_groups is not None else max_open_positions
        ),
        min_volume=min_volume,
        allow_negative_cash=allow_negative_cash,
        allow_scale_in=allow_scale_in,
        scale_in_edge_buffer_cents=scale_in_edge_buffer_cents,
        same_candidate_cooldown_minutes=same_candidate_cooldown_minutes,
        max_open_loss_dollars=max_open_loss_dollars,
        max_total_drawdown_dollars=max_total_drawdown_dollars,
        allow_lowball_passive_orders=allow_lowball_passive_orders,
        max_passive_distance_from_bid_cents=max_passive_distance_from_bid_cents,
        max_passive_order_age_minutes=max_passive_order_age_minutes,
        block_high_confidence_no_on_extreme_spread=block_high_confidence_no_on_extreme_spread,
        extreme_spread_no_block_threshold_f=extreme_spread_no_block_threshold_f,
        block_no_on_model_source_degraded=block_no_on_model_source_degraded,
        high_spread_reduce_size_factor=high_spread_reduce_size_factor,
        clustered_disputed_extra_edge_cents=clustered_disputed_extra_edge_cents,
    )
    edge_cost_config = _edge_cost_config(
        risk_limits=risk_limits,
        slippage_cents=slippage_cents,
        tail_risk_padding_cents=tail_risk_padding_cents,
        passive_improvement_cents=passive_improvement_cents,
    )
    edge_risk_config = _edge_risk_config(
        risk_limits=risk_limits,
        min_yes_edge_cents=min_yes_edge_cents,
        min_no_edge_cents=min_no_edge_cents,
        min_no_upside_cents=min_no_upside_cents,
        max_no_bin_probability=max_no_bin_probability,
        max_spread_cents=max_spread_cents,
        edge_comparison_epsilon_cents=edge_comparison_epsilon_cents,
        no_probability_filter_mode=no_probability_filter_mode,
        no_probability_penalty_start=no_probability_penalty_start,
        no_probability_penalty_factor=no_probability_penalty_factor,
        absolute_no_bin_probability_cap=absolute_no_bin_probability_cap,
        model_authoritative=model_authoritative,
        allow_cheap_ask_yes_with_missing_bid=allow_cheap_ask_yes_with_missing_bid,
        cheap_ask_yes_max_cents=cheap_ask_yes_max_cents,
        cheap_ask_yes_min_net_edge_cents=cheap_ask_yes_min_net_edge_cents,
        cheap_ask_yes_max_contracts=cheap_ask_yes_max_contracts,
        model_authoritative_tight_spread_f=model_authoritative_tight_spread_f,
        model_authoritative_wide_spread_f=model_authoritative_wide_spread_f,
        model_authoritative_extreme_spread_f=model_authoritative_extreme_spread_f,
    )
    effective_config_path = Path(debug_output_dir) / "effective_config.json"
    effective_config_payload: dict[str, Any] = {
        "run_id": canonical_debug_run_id,
        "race_id": race_id,
        "target_date": resolved_target_date.isoformat(),
        "series": series,
        "station": station,
        "event_ticker": event_ticker,
        "decision_mode": decision_mode,
        "strategy": strategy,
        "order_style": order_style,
        "paper_fill_price_mode": paper_fill_price_mode,
        "cancel_existing_passive_orders_on_taker_start": cancel_existing_passive_orders_on_taker_start,
        "execution": {
            "style": "TAKER" if order_style == "taker" else "PASSIVE",
            "buy_entry_price_source": "ask" if order_style == "taker" else "passive_limit",
            "close_execution_style": "taker",
            "close_exit_price_source": "bid",
            "resting_passive_disabled": order_style == "taker",
            "passive_cleanup_on_start": cancel_existing_passive_orders_on_taker_start if order_style == "taker" else False,
        },
        "interval_seconds": interval_seconds,
        "duration_minutes": duration_minutes,
        "max_iterations": max_iterations,
        "refresh_cadence": {
            "model_refresh_seconds": model_refresh_seconds,
            "market_refresh_seconds": market_refresh_seconds,
            "fast_model_refresh_seconds": fast_model_refresh_seconds,
            "noaa_model_refresh_seconds": noaa_model_refresh_seconds,
            "observation_refresh_seconds": observation_refresh_seconds,
            "noaa_model_mode": noaa_model_mode,
            "use_cached_models": use_cached_models,
            "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
            "model_cache_path": model_cache_path,
        },
        "model_source": {
            "model_source_mode": "fresh_recompute_each_iteration",
            "model_cache_used": False,
            "fast_model_cache_used": False,
            "noaa_cache_used": False,
            "noaa_model_mode": noaa_model_mode,
            "noaa_cache_age_seconds": None,
            "noaa_last_refresh_utc": None,
            "noaa_next_refresh_utc": None,
            "model_fetch_elapsed_seconds": None,
            "noaa_fetch_elapsed_seconds": None,
            "open_meteo_fetch_elapsed_seconds": None,
            "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
            "use_cached_models": use_cached_models,
            "model_recomputed_this_iteration": True,
            "cached_model_violation": False,
            "cached_model_violation_message": None,
        },
        "profile": {
            "mode": profile_mode,
            "config_path": profile_config,
            "config": profile_config_payload,
            "lifecycle_active_profile": lifecycle_active_profile,
        },
        "probability_blend": {
            "mode": probability_blend_mode,
            "config_path": probability_blend_config,
            "config": probability_blend_config_payload,
            "model_authoritative": model_authoritative,
            "model_weight": model_weight,
            "market_weight": market_weight,
            "use_market_implied_probability_as_prior": use_market_prior,
            "fair_value_source": "model_consensus_probability" if model_authoritative else "probability_blend",
            "no_probability_filter_mode": no_probability_filter_mode,
            "no_probability_penalty_start": edge_risk_config.no_probability_penalty_start,
            "no_probability_penalty_factor": no_probability_penalty_factor,
            "absolute_no_bin_probability_cap": absolute_no_bin_probability_cap,
            "allow_cheap_ask_yes_with_missing_bid": allow_cheap_ask_yes_with_missing_bid,
        },
        "risk_limits": risk_limits.to_dict(),
        "effective_risk_config": asdict(edge_risk_config),
        "cost_config": asdict(edge_cost_config),
        "fake_money_safety": {
            "fake_money_only": True,
            "live_trading_enabled": False,
            "real_orders_available": False,
            "allow_negative_cash": allow_negative_cash,
            "allow_scale_in": allow_scale_in,
            "degraded_model_edge_buffer_cents": degraded_model_edge_buffer_cents,
            "degraded_model_size_factor": degraded_model_size_factor,
            "block_no_on_model_source_degraded": block_no_on_model_source_degraded,
            "paper_orders_enabled": True,
            "llm_trade_selection_enabled": decision_mode in {"llm", "llm-review"},
        },
    }
    write_json_report(effective_config_path, safe_console_payload(effective_config_payload))
    llm_client = (
        _trader_llm_client(
            llm_provider=llm_provider,
            model=model,
            llm_host=llm_host,
            timeout_seconds=llm_timeout_seconds,
            temperature=llm_temperature,
            dry_run=dry_run,
        )
        if decision_mode in {"llm", "llm-review"}
        else None
    )
    journal = _trader_journal(journal_path)
    if (reset_paper_portfolio or new_paper_portfolio) and resume_paper_portfolio:
        raise typer.BadParameter("Use either --resume-paper-portfolio or reset/new portfolio, not both.")
    if reset_paper_portfolio or new_paper_portfolio:
        if not i_understand_this_deletes_paper_state:
            raise typer.BadParameter(
                "--reset-paper-portfolio/--new-paper-portfolio requires "
                "--i-understand-this-deletes-paper-state."
            )
        journal.reset_portfolio()
    pre_existing_positions = journal.load_open_positions()
    pre_existing_orders = journal.load_open_orders()
    pre_existing_fills = journal.load_fills()
    if fresh_journal and (pre_existing_positions or pre_existing_orders or pre_existing_fills):
        raise typer.BadParameter("--fresh-journal requested but the journal already has paper portfolio state.")
    taker_passive_cleanup_results: list[dict[str, Any]] = []
    if order_style == "taker" and cancel_existing_passive_orders_on_taker_start:
        for open_order in pre_existing_orders:
            if str(open_order.get("action") or "") != "PLACE_FAKE_LIMIT_BUY":
                continue
            cleanup_result = journal.execute_paper_order(
                {
                    "action": "CANCEL_FAKE_ORDER",
                    "contract_ticker": open_order.get("contract_ticker"),
                    "side": open_order.get("side"),
                    "limit_price_cents": open_order.get("limit_price_cents"),
                    "quantity": open_order.get("quantity"),
                    "metadata": {
                        "decision_id": f"taker_cleanup:{canonical_debug_run_id}",
                        "selected_candidate_id": f"{open_order.get('order_id')}:CANCEL",
                        "bracket_label": open_order.get("bracket_label"),
                        "fake_money_only": True,
                        "cleanup_reason": "canceling existing passive fake order because taker mode is active",
                    },
                }
            )
            taker_passive_cleanup_results.append(
                {
                    **cleanup_result,
                    "order_id": open_order.get("order_id"),
                    "reason": "canceling existing passive fake order because taker mode is active",
                }
            )
        if taker_passive_cleanup_results:
            pre_existing_orders = journal.load_open_orders()
    Path(debug_output_dir).mkdir(parents=True, exist_ok=True)
    run_started_at_utc = datetime.now(timezone.utc)
    run_runtime_iterations: list[dict[str, Any]] = []
    initial_runtime_diagnostics = _runtime_diagnostics_summary(
        run_runtime_iterations,
        requested_duration_minutes=duration_minutes,
        requested_interval_seconds=interval_seconds,
        expected_iterations=max_iterations,
        run_started_at_utc=run_started_at_utc,
    )
    trader_refresh_cache: dict[str, Any] = _model_refresh_cache_from_disk(model_cache_path)
    write_latest_run_pointer(canonical_debug_run_id, debug_output_dir, journal_path=journal_path)
    write_run_metadata(
        run_id=canonical_debug_run_id,
        race_id=race_id,
        debug_run_id=canonical_debug_run_id,
        event_ticker=event_ticker,
        target_date=resolved_target_date.isoformat(),
        series=series,
        station=station,
        journal_path=journal_path,
        latest_json_path=Path(debug_output_dir) / "latest.json",
        decisions_jsonl_path=debug_jsonl,
        candidates_csv_path=debug_csv,
        terminal_output_path=Path(debug_output_dir) / "terminal_output.txt",
        profile_config_path=profile_config,
        probability_blend_config_path=probability_blend_config,
        run_dir=debug_output_dir,
        extra={
            "trading_race_id": race_id,
            "debug_run_id": canonical_debug_run_id,
            "original_run_id": race_id if debug_run_id else None,
            "reused_journal_path": journal_path,
            "continuation_debug_dir": debug_output_dir if debug_run_id else None,
            "decision_mode": decision_mode,
            "strategy": strategy,
            "order_style": order_style,
            "paper_fill_price_mode": paper_fill_price_mode,
        "cancel_existing_passive_orders_on_taker_start": cancel_existing_passive_orders_on_taker_start,
        "execution": {
            "style": "TAKER" if order_style == "taker" else "PASSIVE",
            "buy_entry_price_source": "ask" if order_style == "taker" else "passive_limit",
            "close_execution_style": "taker",
            "close_exit_price_source": "bid",
            "resting_passive_disabled": order_style == "taker",
            "passive_cleanup_on_start": cancel_existing_passive_orders_on_taker_start if order_style == "taker" else False,
        },
            "noaa_model_mode": noaa_model_mode,
            "use_cached_models": use_cached_models,
            "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
            "model_refresh_seconds": model_refresh_seconds,
            "market_refresh_seconds": market_refresh_seconds,
            "fast_model_refresh_seconds": fast_model_refresh_seconds,
            "noaa_model_refresh_seconds": noaa_model_refresh_seconds,
            "observation_refresh_seconds": observation_refresh_seconds,
            "model_cache_path": model_cache_path,
            "effective_config_path": str(effective_config_path),
            "runtime_diagnostics": initial_runtime_diagnostics,
        },
    )
    initial_context, _ = _trader_context_and_pending_for_cli(
        settings=settings,
        store_obj=store_obj,
        series=series,
        station=station,
        target_date=resolved_target_date,
        race_id=race_id,
        model_key=model_key,
        risk_limits=risk_limits,
        journal_path=journal_path,
        process_pending_orders=False,
        model_payload=_trader_cached_model_payload(
            settings=settings,
            series=series,
            station=station,
            target_date=resolved_target_date,
            cache={},
            noaa_model_mode=noaa_model_mode,
            market_refresh_seconds=market_refresh_seconds,
            fast_model_refresh_seconds=fast_model_refresh_seconds,
            noaa_model_refresh_seconds=noaa_model_refresh_seconds,
            observation_refresh_seconds=observation_refresh_seconds,
            use_cached_models=use_cached_models,
            force_model_recompute_every_iteration=force_model_recompute_every_iteration,
            model_refresh_seconds=model_refresh_seconds,
        ),
    )
    initial_positions = journal.load_open_positions()
    initial_fills = journal.load_fills()
    initial_portfolio = _trader_portfolio_snapshot(
        initial_context.to_dict(),
        initial_positions,
        initial_fills,
        starting_cash=starting_cash,
    )
    loaded_existing_portfolio = bool(initial_positions or journal.load_open_orders() or initial_fills)
    implicit_resume_warning = loaded_existing_portfolio and not resume_paper_portfolio and not (
        reset_paper_portfolio or new_paper_portfolio
    )
    if dump_decision_context:
        dump_context_payload = initial_context.to_dict()
        dump_rules_payload: dict[str, Any] | None = None
        if decision_mode in {"rules", "llm-review"}:
            dump_result, dump_rules_payload = _rule_decision_for_context(
                initial_context,
                strategy=strategy,
                decision_mode=decision_mode,
                order_style=order_style,
                risk_limits=risk_limits,
                cost_config=edge_cost_config,
                risk_config=edge_risk_config,
                portfolio_state=_edge_portfolio_state_from_context(
                    initial_context,
                    fills=initial_fills,
                    portfolio=initial_portfolio,
                ),
                model_consensus_enabled=model_consensus_enabled,
                consensus_method=consensus_method,
                outlier_threshold_f=outlier_threshold_f,
                consensus_max_spread_f=consensus_max_spread_f,
                full_spread_high_threshold_f=full_spread_high_threshold_f,
                full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
                min_non_eliminated_bin_probability=min_non_eliminated_bin_probability,
                min_tail_probability_when_disputed=min_tail_probability_when_disputed,
                probability_blend_mode=probability_blend_mode,
                probability_blend_config=probability_blend_config_payload,
            )
            dump_context_payload = dump_result.context.to_dict()
        dump_payload = {
            "context": dump_context_payload,
            "rules_engine": dump_rules_payload,
            "portfolio": initial_portfolio,
            "risk_limits": risk_limits.to_dict(),
            "cost_config": asdict(edge_cost_config),
            "risk_config": asdict(edge_risk_config),
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
        write_json_report(dump_decision_context, safe_console_payload(dump_payload))
        console.print(f"Decision context written: {dump_decision_context}")
        return
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_started_local = datetime.now(ZoneInfo(LAX_TIMEZONE))
    journal_json_path = _unique_report_path(
        out_dir / f"trader_paper_run_{run_started_local.strftime('%Y%m%d_%H%M')}.json"
    )
    if output_style in {"table", "combined", "compact"}:
        console.print(
            _trader_paper_run_header(
                series=series,
                station=station,
                decision_mode=decision_mode,
                strategy=strategy,
                starting_cash=starting_cash,
                interval_seconds=interval_seconds,
                duration_minutes=duration_minutes,
                max_iterations=max_iterations,
                loaded_existing_portfolio=loaded_existing_portfolio,
                portfolio=initial_portfolio,
                implicit_resume_warning=implicit_resume_warning,
            )
        )
        if output_style == "table":
            console.print(_format_trader_table_header())
        elif output_style == "combined":
            console.print(_format_trader_combined_table_header())
    outputs: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    printed_table_rows = 0
    approved_actions = 0
    rejected = 0
    fake_orders = 0
    closed_pnl_dollars = 0.0
    prompt_builder = TraderPromptBuilder()
    last_snapshot_state: dict[str, Any] | None = None
    seen_snapshot_rejection_reasons: set[str] = set()
    previous_profile: str | None = None
    for iteration in range(1, max_iterations + 1):
        iteration_started_at_utc = datetime.now(timezone.utc)
        iteration_perf_start = time.perf_counter()
        context_fetch_perf_start = time.perf_counter()
        model_payload = _trader_cached_model_payload(
            settings=settings,
            series=series,
            station=station,
            target_date=resolved_target_date,
            cache=trader_refresh_cache,
            noaa_model_mode=noaa_model_mode,
            market_refresh_seconds=market_refresh_seconds,
            fast_model_refresh_seconds=fast_model_refresh_seconds,
            noaa_model_refresh_seconds=noaa_model_refresh_seconds,
            observation_refresh_seconds=observation_refresh_seconds,
            use_cached_models=use_cached_models,
            force_model_recompute_every_iteration=force_model_recompute_every_iteration,
            model_refresh_seconds=model_refresh_seconds,
        )
        if use_cached_models and not force_model_recompute_every_iteration and model_refresh_seconds > 0:
            _write_model_refresh_cache(model_cache_path, trader_refresh_cache)
        model_source_diagnostics = model_payload.get("model_source_diagnostics") or {}
        context, pending_order_executions = _trader_context_and_pending_for_cli(
            settings=settings,
            store_obj=store_obj,
            series=series,
            station=station,
            target_date=resolved_target_date,
            race_id=race_id,
            model_key=model_key,
            risk_limits=risk_limits,
            journal_path=journal_path,
            process_pending_orders=True,
            starting_cash=starting_cash,
            paper_fill_price_mode=paper_fill_price_mode,
            model_payload=model_payload,
        )
        context_fetch_elapsed_seconds = round(time.perf_counter() - context_fetch_perf_start, 4)
        context_payload = context.to_dict()
        fills_before = journal.load_fills()
        portfolio_before = _trader_portfolio_snapshot(
            context_payload,
            journal.load_open_positions(),
            fills_before,
            starting_cash=starting_cash,
        )
        profile_decision = _profile_decision_for_context(
            context,
            portfolio=portfolio_before,
            risk_limits=risk_limits,
            risk_config=edge_risk_config,
            profile_mode=profile_mode,
            profile_config=profile_config_payload,
            previous_profile=previous_profile,
            forced_active_profile=lifecycle_active_profile,
        )
        profile_payload = _profile_payload(profile_decision, mode=profile_mode, config_path=profile_config)
        previous_profile = profile_payload.get("active_profile") or previous_profile
        effective_risk_limits = (
            _risk_limits_with_profile(risk_limits, profile_decision.effective_risk_config)
            if profile_decision is not None
            else risk_limits
        )
        effective_edge_risk_config = (
            _edge_risk_config_with_profile(edge_risk_config, profile_decision.effective_risk_config)
            if profile_decision is not None
            else edge_risk_config
        )
        effective_risk_limits = _risk_limits_with_model_source_degradation(
            effective_risk_limits,
            model_source_diagnostics,
            edge_buffer_cents=degraded_model_edge_buffer_cents,
            size_factor=degraded_model_size_factor,
        )
        effective_edge_risk_config = _edge_risk_config_with_model_source_degradation(
            effective_edge_risk_config,
            model_source_diagnostics,
            edge_buffer_cents=degraded_model_edge_buffer_cents,
            size_factor=degraded_model_size_factor,
        )
        if model_source_diagnostics.get("model_source_degraded"):
            profile_payload["model_source_degraded_risk_adjustment"] = {
                "edge_buffer_cents": degraded_model_edge_buffer_cents,
                "size_factor": degraded_model_size_factor,
                "reason": model_source_diagnostics.get("model_source_degraded_reason"),
                "effective_min_edge_cents": effective_risk_limits.min_edge_cents,
                "effective_min_no_edge_cents": effective_edge_risk_config.min_no_edge_cents,
                "effective_max_contracts_per_trade": effective_risk_limits.max_contracts_per_trade,
            }
        if profile_decision is not None or effective_risk_limits != risk_limits:
            context = replace(context, risk_limits=effective_risk_limits)
            context_payload = context.to_dict()
        captured_prompt = None
        rules_payload: dict[str, Any] | None = None
        llm_review_payload: dict[str, Any] | None = None
        if decision_mode == "rules":
            result, rules_payload = _rule_decision_for_context(
                context,
                strategy=strategy,
                decision_mode=decision_mode,
                order_style=order_style,
                risk_limits=effective_risk_limits,
                cost_config=edge_cost_config,
                risk_config=effective_edge_risk_config,
                portfolio_state=_edge_portfolio_state_from_context(
                    context,
                    fills=fills_before,
                    portfolio=portfolio_before,
                ),
                model_consensus_enabled=model_consensus_enabled,
                consensus_method=consensus_method,
                outlier_threshold_f=outlier_threshold_f,
                consensus_max_spread_f=consensus_max_spread_f,
                full_spread_high_threshold_f=full_spread_high_threshold_f,
                full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
                min_non_eliminated_bin_probability=min_non_eliminated_bin_probability,
                min_tail_probability_when_disputed=min_tail_probability_when_disputed,
                profile_decision=profile_decision,
                probability_blend_mode=probability_blend_mode,
                probability_blend_config=probability_blend_config_payload,
            )
        elif decision_mode == "llm-review":
            result, rules_payload = _rule_decision_for_context(
                context,
                strategy=strategy,
                decision_mode=decision_mode,
                order_style=order_style,
                risk_limits=effective_risk_limits,
                cost_config=edge_cost_config,
                risk_config=effective_edge_risk_config,
                portfolio_state=_edge_portfolio_state_from_context(
                    context,
                    fills=fills_before,
                    portfolio=portfolio_before,
                ),
                model_consensus_enabled=model_consensus_enabled,
                consensus_method=consensus_method,
                outlier_threshold_f=outlier_threshold_f,
                consensus_max_spread_f=consensus_max_spread_f,
                full_spread_high_threshold_f=full_spread_high_threshold_f,
                full_spread_extreme_threshold_f=full_spread_extreme_threshold_f,
                min_non_eliminated_bin_probability=min_non_eliminated_bin_probability,
                min_tail_probability_when_disputed=min_tail_probability_when_disputed,
                profile_decision=profile_decision,
                probability_blend_mode=probability_blend_mode,
                probability_blend_config=probability_blend_config_payload,
            )
            captured_prompt = prompt_builder.build(result.context) if (show_prompt or output_style == "verbose") else None
            review = TraderAgent(llm_client=llm_client, prompt_builder=prompt_builder).recommend(result.context)
            llm_review_payload = review.to_dict()
            rules_selected_id = result.decision.selected_candidate_id
            review_selected_id = review.decision.selected_candidate_id
            if review.decision.action == "HOLD":
                veto = TraderDecision.hold(review.decision.no_trade_reason or "LLM review veto")
                result = TraderRunResult(
                    context=result.context,
                    raw_llm_output=review.raw_llm_output,
                    decision=veto,
                    validation=ValidationResult(
                        valid=False,
                        approved_action=veto.to_dict(),
                        rejection_reason="llm-review veto",
                    ),
                    approved_action=veto.to_dict(),
                )
            elif review_selected_id != rules_selected_id:
                veto = TraderDecision.hold("LLM review attempted to select a different candidate")
                result = TraderRunResult(
                    context=result.context,
                    raw_llm_output=review.raw_llm_output,
                    decision=veto,
                    validation=ValidationResult(
                        valid=False,
                        approved_action=veto.to_dict(),
                        rejection_reason="llm-review cannot invent trades",
                    ),
                    approved_action=veto.to_dict(),
                )
        else:
            captured_prompt = prompt_builder.build(context) if (show_prompt or output_style == "verbose") else None
            result = TraderAgent(llm_client=llm_client, prompt_builder=prompt_builder).recommend(context)
        context_payload = result.context.to_dict()
        final_validation = result.validation
        approved_decision = final_validation.approved_action if final_validation.valid else result.decision.to_dict()
        selected_candidate = _selected_trade_candidate(context_payload, approved_decision) or {}
        portfolio_rejection = None
        if final_validation.valid and approved_decision.get("action") in {"PLACE_FAKE_LIMIT_BUY", "EXECUTE_FAKE_TAKER_BUY"}:
            portfolio_rejection = _validate_paper_buy_against_portfolio(
                decision=approved_decision,
                candidate=selected_candidate,
                context=context_payload,
                portfolio=portfolio_before,
                open_positions=journal.load_open_positions(),
                fills=journal.load_fills(),
                risk_limits=effective_risk_limits,
            )
        if portfolio_rejection is not None:
            final_validation = portfolio_rejection
        paper_order = decision_to_paper_order(result.decision, final_validation)
        paper_order_payload = paper_order.to_dict() if paper_order else None
        if paper_order_payload is None and final_validation.valid and approved_decision.get("action") == "CANCEL_FAKE_ORDER":
            paper_order_payload = {
                "action": "CANCEL_FAKE_ORDER",
                "contract_ticker": approved_decision.get("contract_ticker"),
                "side": approved_decision.get("side"),
                "limit_price_cents": approved_decision.get("limit_price_cents"),
                "quantity": approved_decision.get("max_contracts"),
                "metadata": {
                    "decision_id": approved_decision.get("decision_id"),
                    "selected_candidate_id": approved_decision.get("selected_candidate_id"),
                    "bracket_label": approved_decision.get("bracket"),
                    "fake_money_only": True,
                },
            }
        paper_execution = (
            journal.execute_paper_order(
                paper_order_payload,
                market_brackets=context.market_brackets,
                fill_price_mode=paper_fill_price_mode,
            )
            if paper_order_payload
            else None
        )
        payload = result.to_dict()
        payload["iteration"] = iteration
        payload["decision_mode"] = decision_mode
        payload["strategy"] = strategy
        payload["order_style"] = order_style
        payload["paper_fill_price_mode"] = paper_fill_price_mode
        payload["cancel_existing_passive_orders_on_taker_start"] = cancel_existing_passive_orders_on_taker_start
        payload["taker_passive_cleanup_results"] = taker_passive_cleanup_results
        payload["profile"] = profile_payload
        trader_active_profile = profile_payload.get("active_profile")
        profile_warnings: list[str] = []
        if (
            lifecycle_active_profile
            and trader_active_profile
            and lifecycle_active_profile != trader_active_profile
            and profile_mode != "fixed_test"
        ):
            profile_warnings.append(
                f"profile mismatch: lifecycle selected {lifecycle_active_profile} but trader used {trader_active_profile}"
            )
        payload["lifecycle_active_profile"] = lifecycle_active_profile
        payload["trader_active_profile"] = trader_active_profile
        payload["profile_mode"] = profile_mode
        payload["profile_reason"] = profile_payload.get("profile_reason")
        payload["target_date_relation"] = profile_payload.get("target_date_relation")
        payload["effective_risk_config"] = profile_payload.get("effective_risk_config") or effective_risk_limits.to_dict()
        payload["profile_overrides_applied"] = profile_payload.get("profile_overrides_applied") or {}
        payload["dynamic_overrides_applied"] = profile_payload.get("dynamic_overrides_applied") or {}
        payload["warnings"] = profile_warnings
        payload["probability_blend_mode"] = probability_blend_mode
        payload["model_source"] = model_source_diagnostics
        payload["model_source_diagnostics"] = model_source_diagnostics
        for key in (
            "noaa_model_mode",
            "noaa_last_refresh_utc",
            "noaa_next_refresh_utc",
            "noaa_cache_age_seconds",
            "model_cache_used",
            "fast_model_cache_used",
            "noaa_cache_used",
            "model_fetch_elapsed_seconds",
            "noaa_fetch_elapsed_seconds",
            "open_meteo_fetch_elapsed_seconds",
            "fast_model_fetch_elapsed_seconds",
            "market_fetch_elapsed_seconds",
            "model_source_mode",
            "model_source_degraded",
            "model_source_degraded_reason",
            "force_model_recompute_every_iteration",
            "use_cached_models",
            "model_recomputed_this_iteration",
            "cached_model_violation",
            "cached_model_violation_message",
        ):
            payload[key] = model_source_diagnostics.get(key)
        payload["llm_trader_used"] = decision_mode in {"llm", "llm-review"}
        payload["rules_engine"] = rules_payload
        payload["llm_review"] = llm_review_payload
        payload["llm_validation"] = payload.get("validation")
        payload["validation"] = final_validation.to_dict()
        payload["approved_action"] = final_validation.approved_action
        payload["portfolio_before"] = portfolio_before
        payload["pending_order_executions"] = pending_order_executions
        payload["paper_order"] = paper_order_payload
        payload["paper_execution"] = paper_execution
        payload["starting_cash"] = starting_cash
        if paper_execution and paper_execution.get("executed"):
            paper_order_status = "executed_fake_money_order"
        elif paper_execution and paper_execution.get("status") == "open":
            paper_order_status = "pending_fake_limit_order"
        elif paper_order_payload is None:
            paper_order_status = "no_fake_order"
        else:
            paper_order_status = "fake_order_not_executed"
        payload["paper_order_status"] = paper_order_status
        payload["live_trading_enabled"] = False
        payload["real_orders_available"] = False
        if captured_prompt is not None:
            payload["prompt"] = captured_prompt.to_messages()
        payload["open_positions"] = journal.load_open_positions()
        payload["open_orders"] = journal.load_open_orders()
        fills_after = journal.load_fills()
        ledger_context_payload = {**context_payload, "open_orders": payload["open_orders"]}
        payload["clv_samples"] = _trader_clv_samples(
            ledger_context_payload,
            fills_after,
            now_utc=_parse_utc_datetime(ledger_context_payload.get("current_time_utc")),
        )
        portfolio_after = _trader_portfolio_snapshot(
            ledger_context_payload,
            payload["open_positions"],
            fills_after,
            starting_cash=starting_cash,
        )
        payload["settlement_scenarios"] = _settlement_scenario_payload(
            ledger_context_payload,
            portfolio_after,
            payload["open_positions"],
            starting_cash=starting_cash,
        )
        payload["thesis_exposure"] = _thesis_exposure_payload(
            ledger_context_payload,
            payload["open_positions"],
        )
        payload["clv_summary"] = _clv_summary_payload(ledger_context_payload, fills_after)
        payload["fills_count"] = len(fills_after)
        payload["portfolio"] = {
            **portfolio_after,
            "position_value": portfolio_after.get("position_value_text"),
            "total": portfolio_after.get("equity"),
            "total_value": portfolio_after.get("equity_value"),
        }
        iteration_ended_at_utc = datetime.now(timezone.utc)
        iteration_elapsed_seconds = round(time.perf_counter() - iteration_perf_start, 4)
        iteration_runtime = {
            "iteration": iteration,
            "iteration_started_at_utc": iteration_started_at_utc.isoformat(),
            "iteration_ended_at_utc": iteration_ended_at_utc.isoformat(),
            "iteration_elapsed_seconds": iteration_elapsed_seconds,
            "fast_model_fetch_elapsed_seconds": model_source_diagnostics.get("fast_model_fetch_elapsed_seconds"),
            "model_fetch_elapsed_seconds": model_source_diagnostics.get("model_fetch_elapsed_seconds"),
            "noaa_fetch_elapsed_seconds": model_source_diagnostics.get("noaa_fetch_elapsed_seconds"),
            "open_meteo_fetch_elapsed_seconds": model_source_diagnostics.get("open_meteo_fetch_elapsed_seconds"),
            "market_fetch_elapsed_seconds": model_source_diagnostics.get("market_fetch_elapsed_seconds"),
            "weather_fetch_elapsed_seconds": None,
            "context_fetch_elapsed_seconds": context_fetch_elapsed_seconds,
            "total_iteration_elapsed_seconds": iteration_elapsed_seconds,
            "noaa_model_mode": noaa_model_mode,
            "model_cache_used": model_source_diagnostics.get("model_cache_used"),
            "fast_model_cache_used": model_source_diagnostics.get("fast_model_cache_used"),
            "noaa_cache_used": model_source_diagnostics.get("noaa_cache_used"),
            "noaa_cache_age_seconds": model_source_diagnostics.get("noaa_cache_age_seconds"),
            "force_model_recompute_every_iteration": model_source_diagnostics.get("force_model_recompute_every_iteration"),
            "use_cached_models": model_source_diagnostics.get("use_cached_models"),
            "journal_write_elapsed_seconds": None,
            "debug_write_elapsed_seconds": None,
            "slowest_iteration_reason_if_available": (
                "context/weather/market/model fetch and decision processing"
                if iteration_elapsed_seconds >= max(30.0, float(interval_seconds) * 1.5)
                else None
            ),
        }
        if noaa_model_mode == "always" and iteration_elapsed_seconds > float(interval_seconds):
            iteration_runtime["runtime_warning"] = "noaa_always_loop_runtime_exceeded_interval"
            payload["runtime_warning"] = iteration_runtime["runtime_warning"]
        run_runtime_iterations.append(iteration_runtime)
        payload["runtime_diagnostics"] = {
            **iteration_runtime,
            **_runtime_diagnostics_summary(
                run_runtime_iterations,
                requested_duration_minutes=duration_minutes,
                requested_interval_seconds=interval_seconds,
                expected_iterations=max_iterations,
                run_started_at_utc=run_started_at_utc,
            ),
        }
        audit = _build_decision_audit(
            payload,
            race_id=race_id,
            journal_path=journal_path,
            risk_limits=effective_risk_limits,
            risk_config=effective_edge_risk_config,
            cost_config=edge_cost_config,
            starting_cash=starting_cash,
            loaded_existing_portfolio=loaded_existing_portfolio,
            implicit_resume_warning=implicit_resume_warning,
            initial_portfolio=initial_portfolio,
        )
        if debug_output_dir or debug_jsonl or debug_csv:
            debug_write_perf_start = time.perf_counter()
            payload["debug_files"] = _write_debug_audit_files(
                audit,
                debug_output_dir=debug_output_dir,
                debug_jsonl=debug_jsonl,
                debug_csv=debug_csv,
            )
            iteration_runtime["debug_write_elapsed_seconds"] = round(time.perf_counter() - debug_write_perf_start, 4)
        payload["decision_audit"] = audit
        if final_validation.valid and approved_decision.get("action") != "HOLD":
            approved_actions += 1
        if not final_validation.valid:
            rejected += 1
        if paper_execution and (paper_execution.get("executed") or paper_execution.get("status") == "open"):
            fake_orders += 1
        closed_pnl_dollars = float(portfolio_after.get("closed_pnl_value") or 0)
        row = _trader_table_row(payload, starting_cash=starting_cash, closed_pnl_dollars=closed_pnl_dollars)
        journal_write_perf_start = time.perf_counter()
        journal.record_run(payload)
        iteration_runtime["journal_write_elapsed_seconds"] = round(time.perf_counter() - journal_write_perf_start, 4)
        payload["runtime_diagnostics"].update(iteration_runtime)
        outputs.append(payload)
        table_rows.append(row)
        row_printed = False
        if output_style == "verbose":
            console.print(_trader_verbose_paper_text(payload))
        elif output_style == "readable":
            console.print(_trader_readable_paper_text(payload, show_prompt=show_prompt))
        elif output_style == "json-lines":
            print(_trader_json_line(row, payload))
        elif output_style == "compact":
            if _trader_table_should_print(row, quiet=quiet):
                console.print(_trader_compact_line(row))
                row_printed = True
                optional = _trader_optional_sections(
                    payload,
                    show_trade_board=show_trade_board,
                    show_prompt=show_prompt,
                    show_llm_reasoning=show_llm_reasoning,
                )
                if optional:
                    console.print(optional)
        elif output_style == "combined":
            if _trader_table_should_print(row, quiet=quiet):
                if printed_table_rows and printed_table_rows % 20 == 0:
                    console.print(_format_trader_combined_table_header())
                console.print(_format_trader_combined_table_row(row))
                row_printed = True
                printed_table_rows += 1
                optional = _trader_optional_sections(
                    payload,
                    show_trade_board=show_trade_board,
                    show_prompt=show_prompt,
                    show_llm_reasoning=show_llm_reasoning,
                )
                if optional:
                    console.print(optional)
        else:
            if _trader_table_should_print(row, quiet=quiet):
                if printed_table_rows and printed_table_rows % 20 == 0:
                    console.print(_format_trader_table_header())
                console.print(_format_trader_table_row(row))
                row_printed = True
                printed_table_rows += 1
                optional = _trader_optional_sections(
                    payload,
                    show_trade_board=show_trade_board,
                    show_prompt=show_prompt,
                    show_llm_reasoning=show_llm_reasoning,
                )
                if optional:
                    console.print(optional)
        if (
            debug_decision
            or candidate_table
            or audit_pricing
            or audit_portfolio
            or audit_data
            or show_pricing_table
            or show_risk_table
            or (explain_hold and _trader_action_label(payload) == "HOLD")
        ) and output_style != "json-lines":
            console.print(
                _decision_audit_text(
                    audit,
                    show_rejections=show_rejections,
                    candidate_table=candidate_table,
                    candidate_table_limit=candidate_table_limit,
                    audit_pricing=audit_pricing,
                    audit_portfolio=audit_portfolio,
                    audit_data=audit_data,
                    show_pricing_table=show_pricing_table,
                    show_risk_table=show_risk_table,
                )
            )
        if show_settlement_scenarios and output_style != "json-lines" and (
            row_printed or output_style in {"verbose", "readable"}
        ):
            console.print(_settlement_scenario_text(payload, style=settlement_scenario_style))
        if row_printed and output_style in {"table", "combined", "compact"}:
            should_print_snapshot, snapshot_state = _trader_snapshot_should_print(
                payload,
                row,
                iteration=iteration,
                show_snapshot=show_snapshot,
                snapshot_every=snapshot_every,
                previous_state=last_snapshot_state,
                seen_rejection_reasons=seen_snapshot_rejection_reasons,
            )
            if should_print_snapshot:
                console.print(
                    _trader_snapshot_text(
                        payload,
                        style=snapshot_style,
                        show_models=show_models,
                        show_market=show_market,
                    )
                )
                last_snapshot_state = snapshot_state
        if iteration < max_iterations:
            time.sleep(interval_seconds)
    run_ended_at_utc = datetime.now(timezone.utc)
    runtime_diagnostics = _runtime_diagnostics_summary(
        run_runtime_iterations,
        requested_duration_minutes=duration_minutes,
        requested_interval_seconds=interval_seconds,
        expected_iterations=max_iterations,
        run_started_at_utc=run_started_at_utc,
        run_ended_at_utc=run_ended_at_utc,
    )
    last_row = table_rows[-1] if table_rows else {}
    run_summary = {
        "iterations": len(outputs),
        "approved_actions": approved_actions,
        "rejected": rejected,
        "fake_orders": fake_orders,
        "cash": last_row.get("cash"),
        "equity": last_row.get("equity"),
        "open_positions": last_row.get("pos", 0),
        "contracts": last_row.get("contracts", 0),
        "exposure": last_row.get("exposure"),
        "open_pnl": last_row.get("open_pnl"),
        "closed_pnl": _fmt_signed_dollars(closed_pnl_dollars),
        "journal_json_path": str(journal_json_path),
        "runtime_diagnostics": runtime_diagnostics,
    }
    run_report = {
        "run_started_local": run_started_local.isoformat(),
        "series": series,
        "station": station,
        "target_date": str(resolved_target_date),
        "event_ticker": event_ticker,
        "mode": "fake_money_only",
        "live_trading_enabled": False,
        "real_orders_available": False,
        "summary": run_summary,
        "runtime_diagnostics": runtime_diagnostics,
        "table_rows": table_rows,
        "iterations": outputs,
    }
    write_json_report(journal_json_path, safe_console_payload(run_report))
    write_json_report(out_dir / "latest_trader_paper_run.json", safe_console_payload(run_report))
    write_latest_run_pointer(canonical_debug_run_id, debug_output_dir, journal_path=journal_path)
    write_run_metadata(
        run_id=canonical_debug_run_id,
        race_id=race_id,
        debug_run_id=canonical_debug_run_id,
        event_ticker=event_ticker,
        target_date=resolved_target_date.isoformat(),
        series=series,
        station=station,
        journal_path=journal_path,
        latest_json_path=Path(debug_output_dir) / "latest.json",
        decisions_jsonl_path=debug_jsonl,
        candidates_csv_path=debug_csv,
        terminal_output_path=Path(debug_output_dir) / "terminal_output.txt",
        profile_config_path=profile_config,
        probability_blend_config_path=probability_blend_config,
        run_dir=debug_output_dir,
        extra={
            "trading_race_id": race_id,
            "debug_run_id": canonical_debug_run_id,
            "original_run_id": race_id if debug_run_id else None,
            "reused_journal_path": journal_path,
            "continuation_debug_dir": debug_output_dir if debug_run_id else None,
            "decision_mode": decision_mode,
            "strategy": strategy,
            "order_style": order_style,
            "paper_fill_price_mode": paper_fill_price_mode,
        "cancel_existing_passive_orders_on_taker_start": cancel_existing_passive_orders_on_taker_start,
        "execution": {
            "style": "TAKER" if order_style == "taker" else "PASSIVE",
            "buy_entry_price_source": "ask" if order_style == "taker" else "passive_limit",
            "close_execution_style": "taker",
            "close_exit_price_source": "bid",
            "resting_passive_disabled": order_style == "taker",
            "passive_cleanup_on_start": cancel_existing_passive_orders_on_taker_start if order_style == "taker" else False,
        },
            "noaa_model_mode": noaa_model_mode,
            "market_refresh_seconds": market_refresh_seconds,
            "fast_model_refresh_seconds": fast_model_refresh_seconds,
            "noaa_model_refresh_seconds": noaa_model_refresh_seconds,
            "observation_refresh_seconds": observation_refresh_seconds,
            "model_cache_path": model_cache_path,
            "effective_config_path": str(effective_config_path),
            "summary": run_summary,
            "runtime_diagnostics": runtime_diagnostics,
        },
    )
    review_report_paths = write_trader_run_review_reports(
        run_id=canonical_debug_run_id,
        race_id=race_id,
        target_date=resolved_target_date.isoformat(),
        series=series,
        station=station,
        event_ticker=event_ticker,
        journal_path=journal_path,
        debug_dir=debug_output_dir,
        starting_cash=starting_cash,
    )
    run_summary["final_results_path"] = review_report_paths["final_results_path"]
    run_summary["bot_trust_report_path"] = review_report_paths["bot_trust_report_path"]
    latest_iteration_payload = outputs[-1] if outputs else {}
    latest_settlement_scenarios = latest_iteration_payload.get("settlement_scenarios") or {}
    if latest_settlement_scenarios:
        write_json_report(
            Path(debug_output_dir) / "settlement_scenarios.json",
            safe_console_payload(latest_settlement_scenarios),
        )
    effective_config_payload.update(
        {
            "summary": run_summary,
            "runtime_diagnostics": runtime_diagnostics,
            "latest_profile": latest_iteration_payload.get("profile"),
            "lifecycle_active_profile": latest_iteration_payload.get("lifecycle_active_profile"),
            "trader_active_profile": latest_iteration_payload.get("trader_active_profile"),
            "profile_mode": latest_iteration_payload.get("profile_mode"),
            "profile_reason": latest_iteration_payload.get("profile_reason"),
            "target_date_relation": latest_iteration_payload.get("target_date_relation"),
            "profile_overrides_applied": latest_iteration_payload.get("profile_overrides_applied"),
            "dynamic_overrides_applied": latest_iteration_payload.get("dynamic_overrides_applied"),
            "latest_model_source_diagnostics": latest_iteration_payload.get("model_source_diagnostics"),
            "model_source": latest_iteration_payload.get("model_source") or latest_iteration_payload.get("model_source_diagnostics"),
            "latest_effective_risk_config": (latest_iteration_payload.get("profile") or {}).get("effective_risk_config"),
            "final_results_path": review_report_paths["final_results_path"],
            "bot_trust_report_path": review_report_paths["bot_trust_report_path"],
        }
    )
    write_json_report(effective_config_path, safe_console_payload(effective_config_payload))
    terminal_output_path = Path(debug_output_dir) / "terminal_output.txt"
    if not terminal_output_path.exists() or terminal_output_path.stat().st_size == 0:
        write_text_report(
            terminal_output_path,
            "\n".join(
                [
                    "Kalshi Weather Trader Paper Run",
                    "================================",
                    f"Run ID: {canonical_debug_run_id}",
                    f"Series: {series} | Station: {station} | Target: {resolved_target_date.isoformat()}",
                    f"Event: {event_ticker}",
                    "Mode: fake_money_only | Live trading: DISABLED | Real orders: NOT AVAILABLE",
                    f"Iterations: {len(outputs)} | Approved actions: {approved_actions} | Rejected: {rejected}",
                    f"Final results: {review_report_paths['final_results_path']}",
                    f"Bot trust report: {review_report_paths['bot_trust_report_path']}",
                ]
            ),
        )
    if auto_package_run:
        try:
            package_result = create_debug_package(
                run_id=canonical_debug_run_id,
                debug_root=Path(debug_output_dir).parent,
                archive_root=get_archive_root(),
            )
            run_summary["review_package_path"] = str(package_result.archive_path)
            run_report["summary"] = run_summary
            write_json_report(journal_json_path, safe_console_payload(run_report))
            write_json_report(out_dir / "latest_trader_paper_run.json", safe_console_payload(run_report))
            metadata_path = Path(debug_output_dir) / "run_metadata.json"
            metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            metadata_payload["review_package_path"] = str(package_result.archive_path)
            metadata_payload["review_package_created_at_utc"] = package_result.manifest.get("created_at_utc")
            write_json_report(metadata_path, safe_console_payload(metadata_payload))
        except Exception as exc:
            run_summary["review_package_error"] = str(exc)
    if output_style != "json-lines":
        console.print(
            _trader_paper_summary_text(
                iterations=len(outputs),
                approved_actions=approved_actions,
                rejected=rejected,
                fake_orders=fake_orders,
                cash=last_row.get("cash"),
                equity=last_row.get("equity"),
                open_positions=int(last_row.get("pos") or 0),
                open_pnl=last_row.get("open_pnl"),
                closed_pnl=closed_pnl_dollars,
                journal_json_path=journal_json_path,
            )
        )
        if run_summary.get("review_package_path"):
            console.print(f"Review package: {run_summary['review_package_path']}")
        elif run_summary.get("review_package_error"):
            console.print(f"Review package not created: {run_summary['review_package_error']}")


@app.command("trader-settle-paper")
def trader_settle_paper(
    race_id: str = typer.Option("trader_agent", "--race-id", "--run-id"),
    journal_path: str | None = typer.Option(None, "--journal-path"),
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    event_ticker: str | None = typer.Option(None, "--event-ticker"),
    target_date: str | None = typer.Option(None, "--target-date"),
    final_high_f: float | None = typer.Option(None, "--final-high-f", "--official-high-f"),
    winning_bracket: str | None = typer.Option(None, "--winning-bracket"),
    settlement_mode: str = typer.Option("final_official", "--settlement-mode", "--settlement-source-status"),
    force_resettle: bool = typer.Option(False, "--force-resettle"),
    i_understand_resettle: bool = typer.Option(False, "--i-understand-this-can-change-paper-pnl"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    fetch_outcome: bool = typer.Option(True, "--fetch-outcome/--no-fetch-outcome"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
    use_canonical_paths: bool = typer.Option(True, "--use-canonical-paths/--no-use-canonical-paths"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Settle open fake-money paper positions using the official final high."""
    settlement_source_status = settlement_mode.strip().lower()
    settlement_status = (
        "final_official"
        if settlement_source_status in {"official_nws", "kalshi_final"}
        else settlement_source_status
    )
    if settlement_status not in {"final_official", "provisional", "unknown"}:
        raise typer.BadParameter(
            "--settlement-mode/--settlement-source-status must be final_official, official_nws, "
            "kalshi_final, provisional, or unknown."
        )
    if force_resettle and not i_understand_resettle:
        raise typer.BadParameter(
            "--force-resettle requires --i-understand-this-can-change-paper-pnl."
        )
    if settlement_status != "final_official":
        dry_run = True
    if use_canonical_paths:
        ensure_canonical_dirs()
    output_dir_path = _resolve_cli_output_path(
        output_dir,
        get_run_dir(race_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    journal_path_path = _resolve_cli_output_path(
        journal_path,
        get_journal_path(race_id),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    output_dir = str(output_dir_path)
    journal_path = str(journal_path_path)
    settings = load_settings()
    store_obj = _store(settings)
    journal = _trader_journal(journal_path)
    latest_runs = journal.latest(limit=1)
    latest_run = latest_runs[0] if latest_runs else {}
    context = latest_run.get("context") or latest_run.get("raw_context") or {}
    run_meta = latest_run.get("run") or {}
    series = series or context.get("series") or run_meta.get("series") or settings.default_series
    station = station or context.get("station") or run_meta.get("station") or settings.default_station
    target_date_text = target_date or context.get("market_date") or run_meta.get("target_date")
    if not target_date_text:
        raise typer.BadParameter("--target-date is required when the journal does not contain a market date.")
    try:
        resolved_target_date = date.fromisoformat(str(target_date_text))
    except ValueError as exc:
        raise typer.BadParameter("--target-date must use YYYY-MM-DD.") from exc

    outcome_payload: dict[str, Any] | None = None
    if winning_bracket is None:
        if final_high_f is None:
            outcome_payload = _telemetry_official_outcome_payload(
                settings,
                store_obj,
                station,
                resolved_target_date,
                fetch_if_due=fetch_outcome,
            )
            final_high_f = _float_or_none(outcome_payload.get("official_high_f"))
        if final_high_f is None:
            reason = (outcome_payload or {}).get("reason") or "official outcome unavailable"
            raise typer.BadParameter(f"Could not resolve final high: {reason}. Pass --final-high-f or --winning-bracket.")
        raw_winning = _telemetry_bracket_for_temperature(
            final_high_f,
            context.get("market_brackets") or [],
        )
        if raw_winning is None:
            raise typer.BadParameter("Could not map --final-high-f to a market bracket; pass --winning-bracket.")
        winning_bracket = raw_winning
    canonical_winning = _canonical_bracket_label(winning_bracket)

    open_positions_before = journal.load_open_positions()
    open_orders_before = journal.load_open_orders()
    fills_before = journal.load_fills()
    context_before = {**context, "open_orders": open_orders_before}
    portfolio_before = _trader_portfolio_snapshot(
        context_before,
        open_positions_before,
        fills_before,
        starting_cash=starting_cash,
    )
    settlement = journal.settle_open_positions(
        winning_bracket=canonical_winning,
        final_high_f=final_high_f,
        market_date=resolved_target_date.isoformat(),
        source=(outcome_payload or {}).get("source") or "manual",
        source_url=(outcome_payload or {}).get("source_url"),
        dry_run=dry_run,
        settlement_status=settlement_status,
        race_id=race_id,
        event_ticker=event_ticker,
        station=station,
        starting_cash=starting_cash,
        cash_before_settlement=portfolio_before.get("cash_value"),
        force_resettle=force_resettle,
    )
    open_positions_after = journal.load_open_positions()
    open_orders_after = journal.load_open_orders()
    fills_after = journal.load_fills()
    context_after = {**context, "open_orders": open_orders_after}
    portfolio_after = _trader_portfolio_snapshot(
        context_after,
        open_positions_after,
        fills_after,
        starting_cash=starting_cash,
    )
    payload = {
        "race_id": race_id,
        "journal_path": journal_path,
        "series": series,
        "station": station,
        "event_ticker": event_ticker,
        "target_date": resolved_target_date.isoformat(),
        "final_high_f": final_high_f,
        "winning_bracket": settlement.get("winning_bracket") or canonical_winning,
        "outcome": outcome_payload,
        "settlement_mode": settlement_status,
        "settlement_source_status": settlement_source_status,
        "force_resettle": force_resettle,
        "dry_run": dry_run,
        "settlement": settlement,
        "portfolio_before": portfolio_before,
        "portfolio_after": portfolio_after,
        "open_positions_before": open_positions_before,
        "open_positions_after": open_positions_after,
        "open_orders_before": open_orders_before,
        "open_orders_after": open_orders_after,
        "live_trading_enabled": False,
        "real_orders_available": False,
        "fake_money_only": True,
    }
    text = _trader_paper_settlement_text(payload)
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json_report(out / "settlement_report.json", safe_console_payload(payload))
        write_json_report(out / "paper_settlement_report.json", safe_console_payload(payload))
        write_json_report(out / "paper_settlement.json", safe_console_payload(payload))
        write_text_report(out / "settlement_report.txt", text)
        write_text_report(out / "paper_settlement.txt", text)
    settlement_debug_run_id = sanitize_run_id(Path(output_dir).name) if output_dir else sanitize_run_id(race_id)
    review_report_paths = write_trader_run_review_reports(
        run_id=settlement_debug_run_id,
        race_id=race_id,
        target_date=resolved_target_date.isoformat(),
        series=series,
        station=station,
        event_ticker=event_ticker,
        journal_path=journal_path,
        debug_dir=output_dir,
        starting_cash=starting_cash,
        settlement_payload=payload,
    )
    write_run_metadata(
        run_id=settlement_debug_run_id,
        race_id=race_id,
        debug_run_id=settlement_debug_run_id,
        event_ticker=event_ticker,
        target_date=resolved_target_date.isoformat(),
        series=series,
        station=station,
        journal_path=journal_path,
        latest_json_path=Path(output_dir or get_run_dir(settlement_debug_run_id)) / "latest.json",
        decisions_jsonl_path=Path(output_dir or get_run_dir(settlement_debug_run_id)) / "decisions.jsonl",
        candidates_csv_path=Path(output_dir or get_run_dir(settlement_debug_run_id)) / "candidates.csv",
        terminal_output_path=Path(output_dir or get_run_dir(settlement_debug_run_id)) / "terminal_output.txt",
        run_dir=output_dir or get_run_dir(settlement_debug_run_id),
        extra={
            "trading_race_id": race_id,
            "debug_run_id": settlement_debug_run_id,
            "original_run_id": race_id if settlement_debug_run_id != sanitize_run_id(race_id) else None,
            "reused_journal_path": journal_path,
            "continuation_debug_dir": output_dir if settlement_debug_run_id != sanitize_run_id(race_id) else None,
            "settlement_report_path": str(Path(output_dir or get_run_dir(settlement_debug_run_id)) / "settlement_report.json"),
            "paper_settlement_report_path": str(
                Path(output_dir or get_run_dir(settlement_debug_run_id)) / "paper_settlement_report.json"
            ),
            "final_results_path": review_report_paths["final_results_path"],
            "bot_trust_report_path": review_report_paths["bot_trust_report_path"],
            "settlement_summary": {
                "winning_bracket": payload.get("winning_bracket"),
                "final_high_f": payload.get("final_high_f"),
                "settlement_mode": settlement_status,
                "settlement_source_status": settlement_source_status,
            },
        },
    )
    _emit_report(payload, json_output=json_output, output=output, text=text)


@app.command("trader-settle-paper-run")
def trader_settle_paper_run(
    race_id: str = typer.Option("trader_agent", "--race-id", "--run-id"),
    journal_path: str | None = typer.Option(None, "--journal-path"),
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    event_ticker: str | None = typer.Option(None, "--event-ticker"),
    target_date: str | None = typer.Option(None, "--target-date"),
    final_high_f: float | None = typer.Option(None, "--final-high-f", "--official-high-f"),
    winning_bracket: str | None = typer.Option(None, "--winning-bracket"),
    settlement_mode: str = typer.Option("final_official", "--settlement-mode", "--settlement-source-status"),
    force_resettle: bool = typer.Option(False, "--force-resettle"),
    i_understand_resettle: bool = typer.Option(False, "--i-understand-this-can-change-paper-pnl"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    fetch_outcome: bool = typer.Option(True, "--fetch-outcome/--no-fetch-outcome"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    output_dir: str | None = typer.Option(None, "--output-dir"),
    use_canonical_paths: bool = typer.Option(True, "--use-canonical-paths/--no-use-canonical-paths"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Alias for trader-settle-paper used by the market lifecycle runner."""
    trader_settle_paper(
        race_id=race_id,
        journal_path=journal_path,
        series=series,
        station=station,
        event_ticker=event_ticker,
        target_date=target_date,
        final_high_f=final_high_f,
        winning_bracket=winning_bracket,
        settlement_mode=settlement_mode,
        force_resettle=force_resettle,
        i_understand_resettle=i_understand_resettle,
        starting_cash=starting_cash,
        fetch_outcome=fetch_outcome,
        dry_run=dry_run,
        json_output=json_output,
        output=output,
        output_dir=output_dir,
        use_canonical_paths=use_canonical_paths,
        allow_noncanonical_output_paths=allow_noncanonical_output_paths,
    )


def _resolve_cycle_target_date(target_date: str | None, tomorrow: bool) -> date:
    if target_date and tomorrow:
        raise typer.BadParameter("Use either --target-date or --tomorrow, not both.")
    if tomorrow:
        return current_lax_market_date() + timedelta(days=1)
    if target_date:
        try:
            return date.fromisoformat(target_date)
        except ValueError as exc:
            raise typer.BadParameter("--target-date must use YYYY-MM-DD.") from exc
    return current_lax_market_date()


def _market_cycle_run_id(series: str, target_date: date, event_ticker: str | None) -> str:
    event = event_ticker or f"{series}-{target_date.strftime('%y%b%d').upper()}"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", event).strip("_").lower()
    return f"lifecycle_{target_date.strftime('%Y%m%d')}_{slug}"


def _market_cycle_text(payload: dict[str, Any]) -> str:
    timeline = payload.get("timeline") or {}
    lifecycle = payload.get("lifecycle") or {}
    profile = payload.get("profile") or {}
    action = payload.get("cycle_action") or {}
    lines = [
        "Kalshi Weather Market Lifecycle",
        "===============================",
        "Mode: fake_money_only | Live trading: DISABLED | Real orders: NOT AVAILABLE",
        (
            f"Series: {payload.get('series')} | Station: {payload.get('station')} | "
            f"Target: {payload.get('target_date')} | Event: {timeline.get('event_ticker') or '--'}"
        ),
        f"Run ID: {payload.get('race_id')}",
        f"State: {lifecycle.get('lifecycle_state')} | Profile: {profile.get('active_profile')}",
        (
            f"Open UTC: {timeline.get('market_open_time_utc') or '--'} | "
            f"Close UTC: {timeline.get('last_trading_time_utc') or timeline.get('close_time_utc') or '--'}"
        ),
        f"Journal: {payload.get('journal_path')}",
        f"Debug dir: {payload.get('debug_dir')}",
        f"Action: {action.get('action') or '--'} | Status: {action.get('status') or '--'}",
    ]
    if action.get("reason"):
        lines.append(f"Reason: {action.get('reason')}")
    return "\n".join(lines)


def _write_market_cycle_payload(payload: dict[str, Any], debug_dir: Path) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    write_json_report(debug_dir / "lifecycle_state.json", safe_console_payload(payload))
    write_json_report(debug_dir / "latest_lifecycle_state.json", safe_console_payload(payload))
    with (debug_dir / "market_lifecycle.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_console_payload(payload), sort_keys=True) + "\n")


def _market_cycle_paper_command(
    *,
    series: str,
    station: str,
    target_date: date,
    race_id: str,
    journal_path: Path,
    debug_dir: Path,
    decision_mode: str,
    strategy: str,
    order_style: str,
    paper_fill_price_mode: str,
    cancel_existing_passive_orders_on_taker_start: bool,
    profile_mode: str,
    lifecycle_active_profile: str | None,
    profile_config: str | None,
    probability_blend_mode: str,
    probability_blend_config: str | None,
    model_authoritative: bool,
    model_weight: float | None,
    market_weight: float | None,
    use_market_implied_probability_as_prior: str,
    no_probability_filter_mode: str | None,
    no_probability_penalty_start: float | None,
    no_probability_penalty_factor: float,
    absolute_no_bin_probability_cap: float,
    allow_cheap_ask_yes_with_missing_bid: bool,
    cheap_ask_yes_max_cents: float,
    cheap_ask_yes_min_net_edge_cents: float,
    cheap_ask_yes_max_contracts: int,
    starting_cash: float,
    min_edge_cents: float,
    min_no_edge_cents: float | None,
    min_no_upside_cents: float,
    max_no_bin_probability: float,
    journal_exists: bool,
    interval_seconds: int,
    model_refresh_seconds: int,
    market_refresh_seconds: int,
    fast_model_refresh_seconds: int,
    noaa_model_refresh_seconds: int,
    observation_refresh_seconds: int,
    noaa_model_mode: str,
    use_cached_models: bool,
    force_model_recompute_every_iteration: bool,
    model_cache_path: Path | None,
    allow_scale_in: bool,
    model_consensus_enabled: bool,
    consensus_method: str,
    block_high_confidence_no_on_extreme_spread: bool,
    extreme_spread_no_block_threshold_f: float,
    block_no_on_model_source_degraded: bool,
    show_snapshot: str,
    snapshot_every: int,
    snapshot_style: str,
    show_settlement_scenarios: bool,
    settlement_scenario_style: str,
    debug_decision: bool,
    explain_hold: bool,
    audit_pricing: bool,
    audit_portfolio: bool,
    audit_data: bool,
    show_rejections: str,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "kalshi_weather.cli",
        "trader-paper-run",
        "--series",
        series,
        "--station",
        station,
        "--target-date",
        target_date.isoformat(),
        "--race-id",
        race_id,
        "--decision-mode",
        decision_mode,
        "--strategy",
        strategy,
        "--order-style",
        order_style,
        "--paper-fill-price-mode",
        paper_fill_price_mode,
        "--profile-mode",
        profile_mode,
        "--probability-blend-mode",
        probability_blend_mode,
        "--interval-seconds",
        str(interval_seconds),
        "--min-edge-cents",
        str(min_edge_cents),
        "--min-no-upside-cents",
        str(min_no_upside_cents),
        "--max-no-bin-probability",
        str(max_no_bin_probability),
        "--model-refresh-seconds",
        str(model_refresh_seconds),
        "--market-refresh-seconds",
        str(market_refresh_seconds),
        "--fast-model-refresh-seconds",
        str(fast_model_refresh_seconds),
        "--noaa-model-refresh-seconds",
        str(noaa_model_refresh_seconds),
        "--observation-refresh-seconds",
        str(observation_refresh_seconds),
        "--noaa-model-mode",
        noaa_model_mode,
        "--max-iterations",
        "1",
        "--no-auto-package-run",
        "--consensus-method",
        consensus_method,
        "--extreme-spread-no-block-threshold-f",
        str(extreme_spread_no_block_threshold_f),
        "--show-snapshot",
        show_snapshot,
        "--snapshot-every",
        str(snapshot_every),
        "--snapshot-style",
        snapshot_style,
        "--show-rejections",
        show_rejections,
        "--output-dir",
        str(debug_dir),
        "--debug-output-dir",
        str(debug_dir),
        "--debug-jsonl",
        str(debug_dir / "decisions.jsonl"),
        "--debug-csv",
        str(debug_dir / "candidates.csv"),
        "--journal-path",
        str(journal_path),
    ]
    command.append(
        "--cancel-existing-passive-orders-on-taker-start"
        if cancel_existing_passive_orders_on_taker_start
        else "--no-cancel-existing-passive-orders-on-taker-start"
    )
    if lifecycle_active_profile:
        command.extend(["--lifecycle-active-profile", lifecycle_active_profile])
    if model_authoritative:
        command.append("--model-authoritative")
    if model_weight is not None:
        command.extend(["--model-weight", str(model_weight)])
    if market_weight is not None:
        command.extend(["--market-weight", str(market_weight)])
    command.extend(["--use-market-implied-probability-as-prior", str(use_market_implied_probability_as_prior)])
    if no_probability_filter_mode:
        command.extend(["--no-probability-filter-mode", no_probability_filter_mode])
    if no_probability_penalty_start is not None:
        command.extend(["--no-probability-penalty-start", str(no_probability_penalty_start)])
    command.extend(["--no-probability-penalty-factor", str(no_probability_penalty_factor)])
    command.extend(["--absolute-no-bin-probability-cap", str(absolute_no_bin_probability_cap)])
    if allow_cheap_ask_yes_with_missing_bid:
        command.append("--allow-cheap-ask-yes-with-missing-bid")
    command.extend(["--cheap-ask-yes-max-cents", str(cheap_ask_yes_max_cents)])
    command.extend(["--cheap-ask-yes-min-net-edge-cents", str(cheap_ask_yes_min_net_edge_cents)])
    command.extend(["--cheap-ask-yes-max-contracts", str(cheap_ask_yes_max_contracts)])
    if min_no_edge_cents is not None:
        command.extend(["--min-no-edge-cents", str(min_no_edge_cents)])
    if journal_exists:
        command.append("--resume-paper-portfolio")
    else:
        command.extend(["--fresh-journal", "--starting-cash", str(starting_cash)])
    command.append("--use-cached-models" if use_cached_models else "--no-use-cached-models")
    command.append(
        "--force-model-recompute-every-iteration"
        if force_model_recompute_every_iteration
        else "--no-force-model-recompute-every-iteration"
    )
    command.append("--allow-scale-in" if allow_scale_in else "--no-allow-scale-in")
    command.append("--model-consensus-enabled" if model_consensus_enabled else "--no-model-consensus")
    command.append(
        "--block-high-confidence-no-on-extreme-spread"
        if block_high_confidence_no_on_extreme_spread
        else "--no-block-high-confidence-no-on-extreme-spread"
    )
    command.append(
        "--block-no-on-model-source-degraded"
        if block_no_on_model_source_degraded
        else "--no-block-no-on-model-source-degraded"
    )
    if use_cached_models and not force_model_recompute_every_iteration and model_refresh_seconds > 0 and model_cache_path is not None:
        command.extend(["--model-cache-path", str(model_cache_path)])
    if show_settlement_scenarios:
        command.extend(["--show-settlement-scenarios", "--settlement-scenario-style", settlement_scenario_style])
    if debug_decision:
        command.append("--debug-decision")
    if explain_hold:
        command.append("--explain-hold")
    if audit_pricing:
        command.append("--audit-pricing")
    if audit_portfolio:
        command.append("--audit-portfolio")
    if audit_data:
        command.append("--audit-data")
    if profile_config:
        command.extend(["--profile-config", profile_config])
    if probability_blend_config:
        command.extend(["--probability-blend-config", probability_blend_config])
    return command


@app.command("trader-market-cycle")
def trader_market_cycle(
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str | None = typer.Option(None, "--race-id"),
    cycle_mode: str = typer.Option("until-close", "--cycle-mode"),
    max_cycles: int | None = typer.Option(None, "--max-cycles"),
    poll_seconds: int = typer.Option(60, "--poll-seconds"),
    model_refresh_seconds: int = typer.Option(0, "--model-refresh-seconds"),
    market_refresh_seconds: int = typer.Option(60, "--market-refresh-seconds"),
    fast_model_refresh_seconds: int = typer.Option(300, "--fast-model-refresh-seconds"),
    noaa_model_refresh_seconds: int = typer.Option(900, "--noaa-model-refresh-seconds"),
    observation_refresh_seconds: int = typer.Option(300, "--observation-refresh-seconds"),
    noaa_model_mode: str = typer.Option("full_recompute_each_iteration", "--noaa-model-mode"),
    use_cached_models: bool = typer.Option(False, "--use-cached-models/--no-use-cached-models"),
    force_model_recompute_every_iteration: bool = typer.Option(
        True,
        "--force-model-recompute-every-iteration/--no-force-model-recompute-every-iteration",
    ),
    decision_mode: str = typer.Option("rules", "--decision-mode"),
    strategy: str = typer.Option("hybrid", "--strategy"),
    order_style: str = typer.Option("passive", "--order-style"),
    cancel_existing_passive_orders_on_taker_start: bool = typer.Option(True, "--cancel-existing-passive-orders-on-taker-start/--no-cancel-existing-passive-orders-on-taker-start"),
    profile_mode: str = typer.Option("auto", "--profile-mode"),
    profile_config: str | None = typer.Option(None, "--profile-config"),
    probability_blend_mode: str = typer.Option("raw", "--probability-blend-mode"),
    probability_blend_config: str | None = typer.Option(None, "--probability-blend-config"),
    model_authoritative: bool = typer.Option(False, "--model-authoritative/--no-model-authoritative"),
    model_weight: float | None = typer.Option(None, "--model-weight"),
    market_weight: float | None = typer.Option(None, "--market-weight"),
    use_market_implied_probability_as_prior: str = typer.Option("true", "--use-market-implied-probability-as-prior"),
    no_probability_filter_mode: str | None = typer.Option(None, "--no-probability-filter-mode"),
    no_probability_penalty_start: float | None = typer.Option(None, "--no-probability-penalty-start"),
    no_probability_penalty_factor: float = typer.Option(0.30, "--no-probability-penalty-factor"),
    absolute_no_bin_probability_cap: float = typer.Option(0.60, "--absolute-no-bin-probability-cap"),
    allow_cheap_ask_yes_with_missing_bid: bool = typer.Option(
        False,
        "--allow-cheap-ask-yes-with-missing-bid/--no-allow-cheap-ask-yes-with-missing-bid",
    ),
    cheap_ask_yes_max_cents: float = typer.Option(2.0, "--cheap-ask-yes-max-cents"),
    cheap_ask_yes_min_net_edge_cents: float = typer.Option(8.0, "--cheap-ask-yes-min-net-edge-cents"),
    cheap_ask_yes_max_contracts: int = typer.Option(25, "--cheap-ask-yes-max-contracts"),
    market_lifecycle_config: str | None = typer.Option(None, "--market-lifecycle-config"),
    paper_fill_price_mode: str = typer.Option("conservative", "--paper-fill-price-mode"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    min_edge_cents: float = typer.Option(3.0, "--min-edge-cents"),
    min_no_edge_cents: float | None = typer.Option(None, "--min-no-edge-cents"),
    min_no_upside_cents: float = typer.Option(8.0, "--min-no-upside-cents"),
    max_no_bin_probability: float = typer.Option(0.20, "--max-no-bin-probability"),
    allow_scale_in: bool = typer.Option(False, "--allow-scale-in/--no-allow-scale-in"),
    model_consensus_enabled: bool = typer.Option(True, "--model-consensus-enabled/--no-model-consensus"),
    consensus_method: str = typer.Option("family_weighted_iqr", "--consensus-method"),
    block_high_confidence_no_on_extreme_spread: bool = typer.Option(
        False,
        "--block-high-confidence-no-on-extreme-spread/--no-block-high-confidence-no-on-extreme-spread",
    ),
    extreme_spread_no_block_threshold_f: float = typer.Option(8.0, "--extreme-spread-no-block-threshold-f"),
    block_no_on_model_source_degraded: bool = typer.Option(
        False,
        "--block-no-on-model-source-degraded/--no-block-no-on-model-source-degraded",
    ),
    show_snapshot: str = typer.Option("changed", "--show-snapshot"),
    snapshot_every: int = typer.Option(5, "--snapshot-every"),
    snapshot_style: str = typer.Option("compact", "--snapshot-style"),
    show_settlement_scenarios: bool = typer.Option(False, "--show-settlement-scenarios"),
    settlement_scenario_style: str = typer.Option("compact", "--settlement-scenario-style"),
    debug_decision: bool = typer.Option(False, "--debug-decision"),
    explain_hold: bool = typer.Option(False, "--explain-hold"),
    audit_pricing: bool = typer.Option(False, "--audit-pricing"),
    audit_portfolio: bool = typer.Option(False, "--audit-portfolio"),
    audit_data: bool = typer.Option(False, "--audit-data"),
    show_rejections: str = typer.Option("summary", "--show-rejections"),
    debug_root: str | None = typer.Option(None, "--debug-root"),
    journal_root: str | None = typer.Option(None, "--journal-root"),
    settle_when_final: bool = typer.Option(True, "--settle-when-final/--no-settle-when-final"),
    roll_to_next: bool = typer.Option(False, "--roll-to-next/--no-roll-to-next"),
    allow_metadata_fallback_times: bool = typer.Option(False, "--allow-metadata-fallback-times"),
    fake_money_only: bool = typer.Option(True, "--fake-money-only/--no-fake-money-only"),
    use_canonical_paths: bool = typer.Option(True, "--use-canonical-paths/--no-use-canonical-paths"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Market-aware fake-money lifecycle runner for one Kalshi weather event."""
    cycle_mode = cycle_mode.strip().lower()
    if cycle_mode == "continuous":
        cycle_mode = "forever"
    if cycle_mode not in {"once", "until-close", "forever"}:
        raise typer.BadParameter("--cycle-mode must be once, until-close, or forever.")
    if poll_seconds < 1:
        raise typer.BadParameter("--poll-seconds must be at least 1.")
    noaa_model_mode = noaa_model_mode.strip().lower()
    if noaa_model_mode not in {"full_recompute_each_iteration", "scheduled", "off", "always"}:
        raise typer.BadParameter("--noaa-model-mode must be full_recompute_each_iteration or scheduled.")
    if (
        force_model_recompute_every_iteration
        or not use_cached_models
        or model_refresh_seconds <= 0
    ) and noaa_model_mode != "off":
        noaa_model_mode = "full_recompute_each_iteration"
    if model_refresh_seconds < 0:
        raise typer.BadParameter("--model-refresh-seconds must be 0 or greater.")
    for option_name, option_value in {
        "--market-refresh-seconds": market_refresh_seconds,
        "--fast-model-refresh-seconds": fast_model_refresh_seconds,
        "--noaa-model-refresh-seconds": noaa_model_refresh_seconds,
        "--observation-refresh-seconds": observation_refresh_seconds,
    }.items():
        if option_value < 1:
            raise typer.BadParameter(f"{option_name} must be at least 1.")
    if snapshot_every < 1:
        raise typer.BadParameter("--snapshot-every must be at least 1.")
    for option_name, option_value in {
        "--min-edge-cents": min_edge_cents,
        "--min-no-upside-cents": min_no_upside_cents,
    }.items():
        if option_value < 0:
            raise typer.BadParameter(f"{option_name} must be 0 or greater.")
    if min_no_edge_cents is not None and min_no_edge_cents < 0:
        raise typer.BadParameter("--min-no-edge-cents must be 0 or greater.")
    if max_no_bin_probability < 0 or max_no_bin_probability > 1:
        raise typer.BadParameter("--max-no-bin-probability must be between 0 and 1.")
    show_snapshot = show_snapshot.strip().lower()
    if show_snapshot not in {"never", "every", "changed"}:
        raise typer.BadParameter("--show-snapshot must be never, every, or changed.")
    snapshot_style = snapshot_style.strip().lower()
    if snapshot_style not in {"compact", "table", "full"}:
        raise typer.BadParameter("--snapshot-style must be compact, table, or full.")
    settlement_scenario_style = settlement_scenario_style.strip().lower()
    if settlement_scenario_style not in {"compact", "full"}:
        raise typer.BadParameter("--settlement-scenario-style must be compact or full.")
    if not fake_money_only:
        raise typer.BadParameter("trader-market-cycle is fake-money-only; --no-fake-money-only is not allowed.")
    if use_canonical_paths:
        ensure_canonical_dirs()
    debug_root_path = _resolve_cli_output_path(
        debug_root,
        get_debug_root(),
        allow_noncanonical=allow_noncanonical_output_paths,
    )
    if model_authoritative:
        probability_blend_mode = "model_only"
        model_weight = 1.0 if model_weight is None else model_weight
        market_weight = 0.0 if market_weight is None else market_weight
        if no_probability_filter_mode is None:
            no_probability_filter_mode = "soft_penalty"
        use_market_implied_probability_as_prior = "false"
    if order_style == "taker" or model_authoritative:
        use_cached_models = False
        force_model_recompute_every_iteration = True
        model_refresh_seconds = 0
        if noaa_model_mode != "off":
            noaa_model_mode = "full_recompute_each_iteration"
    if no_probability_filter_mode is None:
        no_probability_filter_mode = "hard"
    no_probability_filter_mode = no_probability_filter_mode.strip().lower()
    if no_probability_filter_mode not in {"hard", "soft_penalty", "off"}:
        raise typer.BadParameter("--no-probability-filter-mode must be hard, soft_penalty, or off.")
    if no_probability_penalty_start is not None and (no_probability_penalty_start < 0 or no_probability_penalty_start > 1):
        raise typer.BadParameter("--no-probability-penalty-start must be between 0 and 1.")
    if no_probability_penalty_factor < 0:
        raise typer.BadParameter("--no-probability-penalty-factor must be 0 or greater.")
    if absolute_no_bin_probability_cap < 0 or absolute_no_bin_probability_cap > 1:
        raise typer.BadParameter("--absolute-no-bin-probability-cap must be between 0 and 1.")
    probability_blend_mode = probability_blend_mode.strip().lower()
    if probability_blend_mode not in {"raw", "blend", "model_only"}:
        raise typer.BadParameter("--probability-blend-mode must be raw, blend, or model_only.")
    profile_mode = profile_mode.strip().lower()
    if profile_mode not in {"fixed", "fixed_test", "auto"}:
        raise typer.BadParameter("--profile-mode must be fixed, fixed_test, or auto.")
    profile_config = _resolve_repo_path_text(profile_config)
    probability_blend_config = _resolve_repo_path_text(probability_blend_config)
    market_lifecycle_config = _resolve_repo_path_text(market_lifecycle_config)
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_cycle_target_date(target_date, tomorrow)
    cycles = 0
    latest_payload: dict[str, Any] | None = None
    while True:
        cycles += 1
        store_obj = _store(settings)
        timeline = None
        if not (dry_run and allow_metadata_fallback_times):
            timeline = MarketCalendarProvider(_kalshi(settings)).discover_timeline(
                series_ticker=series,
                target_date=resolved_target_date,
            )
        if timeline is None:
            if allow_metadata_fallback_times:
                timeline = fallback_weather_market_timeline(
                    series_ticker=series,
                    target_date=resolved_target_date,
                )
            else:
                timeline = incomplete_timeline(
                    series_ticker=series,
                    event_ticker=f"{series}-{resolved_target_date.strftime('%y%b%d').upper()}",
                    target_date=resolved_target_date,
                )
        run_id = race_id or _market_cycle_run_id(series, resolved_target_date, timeline.event_ticker)
        debug_dir = debug_root_path / sanitize_run_id(run_id)
        journal_base = (
            _resolve_cli_output_path(
                journal_root,
                debug_dir,
                allow_noncanonical=allow_noncanonical_output_paths,
            )
            if journal_root
            else debug_dir
        )
        journal_path = journal_base / "diagnostic.sqlite"
        model_cache_path = (
            debug_dir / "model_refresh_cache.json"
            if use_cached_models and not force_model_recompute_every_iteration and model_refresh_seconds > 0
            else None
        )
        debug_dir.mkdir(parents=True, exist_ok=True)
        write_latest_run_pointer(sanitize_run_id(run_id), debug_dir, journal_path=journal_path)
        write_run_metadata(
            run_id=run_id,
            race_id=run_id,
            debug_run_id=sanitize_run_id(run_id),
            event_ticker=timeline.event_ticker,
            target_date=resolved_target_date.isoformat(),
            series=series,
            station=station,
            journal_path=journal_path,
            latest_json_path=debug_dir / "latest.json",
            decisions_jsonl_path=debug_dir / "decisions.jsonl",
            candidates_csv_path=debug_dir / "candidates.csv",
            terminal_output_path=debug_dir / "terminal_output.txt",
            profile_config_path=profile_config,
            probability_blend_config_path=probability_blend_config,
            market_lifecycle_config_path=market_lifecycle_config,
            run_dir=debug_dir,
            extra={
                "market_lifecycle_path": str(debug_dir / "market_lifecycle.jsonl"),
                "market_lifecycle_config_path": market_lifecycle_config,
                "decision_mode": decision_mode,
                "strategy": strategy,
                "order_style": order_style,
                "paper_fill_price_mode": paper_fill_price_mode,
        "cancel_existing_passive_orders_on_taker_start": cancel_existing_passive_orders_on_taker_start,
        "execution": {
            "style": "TAKER" if order_style == "taker" else "PASSIVE",
            "buy_entry_price_source": "ask" if order_style == "taker" else "passive_limit",
            "close_execution_style": "taker",
            "close_exit_price_source": "bid",
            "resting_passive_disabled": order_style == "taker",
            "passive_cleanup_on_start": cancel_existing_passive_orders_on_taker_start if order_style == "taker" else False,
        },
                "model_refresh_seconds": model_refresh_seconds,
                "market_refresh_seconds": market_refresh_seconds,
                "fast_model_refresh_seconds": fast_model_refresh_seconds,
                "noaa_model_refresh_seconds": noaa_model_refresh_seconds,
                "observation_refresh_seconds": observation_refresh_seconds,
                "noaa_model_mode": noaa_model_mode,
                "use_cached_models": use_cached_models,
                "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
                "model_cache_path": str(model_cache_path) if model_cache_path is not None else None,
            },
        )
        outcome_payload = _telemetry_official_outcome_payload(
            settings,
            store_obj,
            station,
            resolved_target_date,
            fetch_if_due=settle_when_final and not dry_run,
        )
        official_high = _float_or_none(outcome_payload.get("official_high_f"))
        snapshot = lifecycle_snapshot(
            utc_now(),
            timeline,
            official_result_available=official_high is not None,
        )
        profile = profile_for_lifecycle_state(snapshot.state)
        journal_exists = journal_path.exists()
        start_mode = "resume" if journal_exists else "fresh"
        starting_cash_used = None if journal_exists else starting_cash
        starting_cash_ignored_warning = "journal exists; resuming paper portfolio without explicit starting cash" if journal_exists else None
        cycle_action: dict[str, Any] = {"action": "none", "status": "skipped"}
        if snapshot.state in {
            LifecycleState.TRADE_OPEN_MARKET,
            LifecycleState.LATE_DAY_RISK_MANAGE,
            LifecycleState.CLOSE_ONLY,
        }:
            command = _market_cycle_paper_command(
                series=series,
                station=station,
                target_date=resolved_target_date,
                race_id=run_id,
                journal_path=journal_path,
                debug_dir=debug_dir,
                decision_mode=decision_mode,
                strategy=strategy,
                order_style=order_style,
                paper_fill_price_mode=paper_fill_price_mode,
                cancel_existing_passive_orders_on_taker_start=cancel_existing_passive_orders_on_taker_start,
                profile_mode=profile_mode,
                lifecycle_active_profile=profile.name,
                profile_config=profile_config,
                probability_blend_mode=probability_blend_mode,
                probability_blend_config=probability_blend_config,
                model_authoritative=model_authoritative,
                model_weight=model_weight,
                market_weight=market_weight,
                use_market_implied_probability_as_prior=use_market_implied_probability_as_prior,
                no_probability_filter_mode=no_probability_filter_mode,
                no_probability_penalty_start=no_probability_penalty_start,
                no_probability_penalty_factor=no_probability_penalty_factor,
                absolute_no_bin_probability_cap=absolute_no_bin_probability_cap,
                allow_cheap_ask_yes_with_missing_bid=allow_cheap_ask_yes_with_missing_bid,
                cheap_ask_yes_max_cents=cheap_ask_yes_max_cents,
                cheap_ask_yes_min_net_edge_cents=cheap_ask_yes_min_net_edge_cents,
                cheap_ask_yes_max_contracts=cheap_ask_yes_max_contracts,
                starting_cash=starting_cash,
                min_edge_cents=min_edge_cents,
                min_no_edge_cents=min_no_edge_cents,
                min_no_upside_cents=min_no_upside_cents,
                max_no_bin_probability=max_no_bin_probability,
                journal_exists=journal_exists,
                interval_seconds=market_refresh_seconds,
                model_refresh_seconds=model_refresh_seconds,
                market_refresh_seconds=market_refresh_seconds,
                fast_model_refresh_seconds=fast_model_refresh_seconds,
                noaa_model_refresh_seconds=noaa_model_refresh_seconds,
                observation_refresh_seconds=observation_refresh_seconds,
                noaa_model_mode=noaa_model_mode,
                use_cached_models=use_cached_models,
                force_model_recompute_every_iteration=force_model_recompute_every_iteration,
                model_cache_path=model_cache_path,
                allow_scale_in=allow_scale_in,
                model_consensus_enabled=model_consensus_enabled,
                consensus_method=consensus_method,
                block_high_confidence_no_on_extreme_spread=block_high_confidence_no_on_extreme_spread,
                extreme_spread_no_block_threshold_f=extreme_spread_no_block_threshold_f,
                block_no_on_model_source_degraded=block_no_on_model_source_degraded,
                show_snapshot=show_snapshot,
                snapshot_every=snapshot_every,
                snapshot_style=snapshot_style,
                show_settlement_scenarios=show_settlement_scenarios,
                settlement_scenario_style=settlement_scenario_style,
                debug_decision=debug_decision,
                explain_hold=explain_hold,
                audit_pricing=audit_pricing,
                audit_portfolio=audit_portfolio,
                audit_data=audit_data,
                show_rejections=show_rejections,
            )
            if dry_run:
                cycle_action = {
                    "action": "would_run_trader_paper_run",
                    "status": "dry_run",
                    "command": command,
                    "journal_exists": journal_exists,
                    "start_mode": start_mode,
                    "starting_cash_used": starting_cash_used,
                    "starting_cash_ignored_warning": starting_cash_ignored_warning,
                    "lifecycle_active_profile": profile.name,
                }
            else:
                completed = subprocess.run(command, check=False)  # noqa: S603
                cycle_action = {
                    "action": "trader_paper_run",
                    "status": "ok" if completed.returncode == 0 else "failed",
                    "returncode": completed.returncode,
                    "command": command,
                    "journal_exists": journal_exists,
                    "start_mode": start_mode,
                    "starting_cash_used": starting_cash_used,
                    "starting_cash_ignored_warning": starting_cash_ignored_warning,
                    "lifecycle_active_profile": profile.name,
                }
        elif snapshot.state == LifecycleState.SETTLE_PAPER_PORTFOLIO and settle_when_final:
            if official_high is None:
                cycle_action = {
                    "action": "wait_for_official_settlement",
                    "status": "pending",
                    "reason": outcome_payload.get("reason") or outcome_payload.get("status"),
                }
            else:
                latest = _trader_journal(str(journal_path)).latest(limit=1)
                context = (latest[0].get("context") if latest else {}) or {}
                winning = _telemetry_bracket_for_temperature(
                    official_high,
                    context.get("market_brackets") or [],
                )
                if winning is None:
                    cycle_action = {
                        "action": "settlement_blocked",
                        "status": "blocked",
                        "reason": "could not map official high to market bracket",
                    }
                elif dry_run:
                    cycle_action = {
                        "action": "would_settle_paper_portfolio",
                        "status": "dry_run",
                        "winning_bracket": winning,
                    }
                else:
                    trader_settle_paper(
                        race_id=run_id,
                        journal_path=str(journal_path),
                        series=series,
                        station=station,
                        event_ticker=timeline.event_ticker,
                        target_date=resolved_target_date.isoformat(),
                        final_high_f=official_high,
                        winning_bracket=winning,
                        settlement_mode="final_official",
                        force_resettle=False,
                        i_understand_resettle=False,
                        starting_cash=starting_cash,
                        fetch_outcome=False,
                        dry_run=False,
                        json_output=False,
                        output=None,
                        output_dir=str(debug_dir),
                        use_canonical_paths=use_canonical_paths,
                        allow_noncanonical_output_paths=allow_noncanonical_output_paths,
                    )
                    cycle_action = {
                        "action": "settle_paper_portfolio",
                        "status": "ok",
                        "winning_bracket": winning,
                    }
        else:
            cycle_action = {
                "action": "wait",
                "status": "ok",
                "reason": profile.reason,
            }
        rollover = should_roll_to_next_event(
            current_settled=snapshot.state == LifecycleState.SETTLED,
            next_event_exists=roll_to_next,
            next_event_open=roll_to_next,
            current_target_date=resolved_target_date,
        )
        payload = {
            "series": series,
            "station": station,
            "target_date": resolved_target_date.isoformat(),
            "race_id": run_id,
            "cycle": cycles,
            "cycle_mode": cycle_mode,
            "debug_dir": str(debug_dir),
            "journal_path": str(journal_path),
            "timeline": timeline.to_debug_dict(utc_now()),
            "lifecycle": snapshot.to_dict(),
            "profile": profile.to_dict(),
            "outcome": outcome_payload,
            "cycle_action": cycle_action,
            "journal_exists": journal_exists,
            "start_mode": start_mode,
            "starting_cash_used": starting_cash_used,
            "starting_cash_ignored_warning": starting_cash_ignored_warning,
            "lifecycle_active_profile": profile.name,
            "rollover": rollover.to_dict(),
            "market_lifecycle_config_path": market_lifecycle_config,
            "refresh_cadence": {
                "model_refresh_seconds": model_refresh_seconds,
                "market_refresh_seconds": market_refresh_seconds,
                "fast_model_refresh_seconds": fast_model_refresh_seconds,
                "noaa_model_refresh_seconds": noaa_model_refresh_seconds,
                "observation_refresh_seconds": observation_refresh_seconds,
                "noaa_model_mode": noaa_model_mode,
                "use_cached_models": use_cached_models,
                "force_model_recompute_every_iteration": force_model_recompute_every_iteration,
                "model_cache_path": str(model_cache_path) if model_cache_path is not None else None,
            },
            "live_trading_enabled": False,
            "real_orders_available": False,
            "fake_money_only": True,
        }
        _write_market_cycle_payload(payload, debug_dir)
        latest_payload = payload
        if cycle_mode != "once" and not json_output:
            console.print(_market_cycle_text(payload))
            console.print("")
        should_stop_until_close = False
        if cycle_mode == "until-close":
            if snapshot.state == LifecycleState.SETTLED:
                should_stop_until_close = True
            elif snapshot.state == LifecycleState.SETTLE_PAPER_PORTFOLIO and (
                not settle_when_final or cycle_action.get("status") == "ok"
            ):
                should_stop_until_close = True
            elif not settle_when_final and snapshot.state in {
                LifecycleState.MARKET_CLOSED_NO_TRADING,
                LifecycleState.WAIT_FOR_OFFICIAL_SETTLEMENT,
            }:
                should_stop_until_close = True
        if cycle_mode == "once" or should_stop_until_close or (max_cycles is not None and cycles >= max_cycles):
            break
        if rollover.should_roll:
            resolved_target_date = rollover.next_target_date
            race_id = None
        time.sleep(market_refresh_seconds)
    assert latest_payload is not None
    _emit_report(
        latest_payload,
        json_output=json_output,
        output=output,
        text=_market_cycle_text(latest_payload),
    )


@app.command("trader-portfolio")
def trader_portfolio(
    race_id: str = typer.Option("trader_agent", "--race-id"),
    journal_path: str | None = typer.Option(None, "--journal-path"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    max_total_exposure_dollars: float = typer.Option(250.0, "--max-total-exposure-dollars"),
    max_exposure_dollars_per_bracket: float = typer.Option(100.0, "--max-exposure-dollars-per-bracket"),
    max_contracts_per_bracket: int = typer.Option(500, "--max-contracts-per-bracket"),
    max_contracts_per_side: int = typer.Option(1000, "--max-contracts-per-side"),
    max_open_positions: int = typer.Option(4, "--max-open-positions"),
    max_open_loss_dollars: float = typer.Option(100.0, "--max-open-loss-dollars"),
    max_total_drawdown_dollars: float = typer.Option(150.0, "--max-total-drawdown-dollars"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Audit the fake-money LLM trader portfolio ledger."""
    selected_run_id = _latest_run_id_or(race_id) if journal_path is None else race_id
    journal_path = str(
        _resolve_cli_output_path(
            journal_path,
            get_journal_path(selected_run_id),
            allow_noncanonical=allow_noncanonical_output_paths,
        )
    )
    journal = _trader_journal(journal_path)
    latest = journal.latest(limit=1)
    context = (latest[0].get("context") if latest else {}) or {}
    positions = journal.load_open_positions()
    fills = journal.load_fills()
    snapshot = _trader_portfolio_snapshot(context, positions, fills, starting_cash=starting_cash)
    risk_limits = RiskLimits(
        max_total_exposure_dollars=max_total_exposure_dollars,
        max_exposure_dollars_per_bracket=max_exposure_dollars_per_bracket,
        max_contracts_per_bracket=max_contracts_per_bracket,
        max_contracts_per_side=max_contracts_per_side,
        max_open_positions=max_open_positions,
        max_open_loss_dollars=max_open_loss_dollars,
        max_total_drawdown_dollars=max_total_drawdown_dollars,
    )
    payload = {
        "race_id": race_id,
        "journal_path": journal_path,
        "context": context,
        "positions": positions,
        "fill_count": len(fills),
        "snapshot": snapshot,
        "limit_breaches": _trader_limit_breaches(snapshot, risk_limits),
        "live_trading_enabled": False,
        "real_orders_available": False,
    }
    _emit_report(payload, json_output=json_output, output=output, text=_trader_portfolio_audit_text(payload))


@app.command("trader-clv-report")
def trader_clv_report(
    journal_path: str | None = typer.Option(None, "--journal-path"),
    horizon: str = typer.Option("latest", "--horizon"),
    limit: int = typer.Option(100000, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Summarize fake-money fill CLV from a trader paper-run journal."""
    normalized_horizon = horizon.strip().lower()
    if normalized_horizon not in {"5m", "15m", "30m", "60m", "latest", "final"}:
        raise typer.BadParameter("--horizon must be 5m, 15m, 30m, 60m, latest, or final.")
    selected_run_id = _latest_run_id_or() if journal_path is None else "custom"
    journal_path = str(
        _resolve_cli_output_path(
            journal_path,
            get_journal_path(selected_run_id),
            allow_noncanonical=allow_noncanonical_output_paths,
        )
    )
    journal = _trader_journal(journal_path)
    runs = list(reversed(journal.latest(limit=limit)))
    records, rows = _clv_records_from_journal_runs(runs)
    payload = {
        "journal_path": journal_path,
        "horizon": normalized_horizon,
        "run_count": len(runs),
        "summary": summarize_clv(records, horizon=normalized_horizon),
        "summaries": {
            key: summarize_clv(records, horizon=key)
            for key in ("5m", "15m", "30m", "60m", "latest", "final")
        },
        "fills": rows,
        "live_trading_enabled": False,
        "real_orders_available": False,
    }
    output_path = output
    if output_path is None and selected_run_id != "custom":
        output_path = str(get_clv_report_path(selected_run_id))
    _emit_report(payload, json_output=json_output, output=output_path, text=_trader_clv_report_text(payload))


@app.command("trader-debug-last")
def trader_debug_last(
    debug_output_dir: str | None = typer.Option(None, "--debug-output-dir"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Read latest decision-audit JSON and print the useful debugging summary."""
    selected_run_id = _latest_run_id_or()
    debug_output_dir = str(
        _resolve_cli_output_path(
            debug_output_dir,
            get_run_dir(selected_run_id),
            allow_noncanonical=allow_noncanonical_output_paths,
        )
    )
    latest_path = Path(debug_output_dir) / "latest.json"
    if not latest_path.exists():
        raise typer.BadParameter(f"latest.json not found in debug output dir: {debug_output_dir}")
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    _emit_report(payload, json_output=json_output, output=output, text=_trader_debug_last_text(payload))


@app.command("trader-zip-run")
def trader_zip_run(
    run_id: str | None = typer.Option(None, "--run-id"),
    latest: bool = typer.Option(False, "--latest"),
    debug_root: str | None = typer.Option(None, "--debug-root"),
    archive_root: str | None = typer.Option(None, "--archive-root"),
    include_sqlite: bool = typer.Option(True, "--include-sqlite/--no-include-sqlite"),
    include_terminal_log: bool = typer.Option(True, "--include-terminal-log/--no-include-terminal-log"),
    include_configs: bool = typer.Option(True, "--include-configs/--no-include-configs"),
    include_reports: bool = typer.Option(True, "--include-reports/--no-include-reports"),
    include_final_reports: bool = typer.Option(True, "--include-final-reports/--no-include-final-reports"),
    open_folder: bool = typer.Option(False, "--open-folder"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Create a review ZIP package for a canonical trader debug run."""
    try:
        result = create_debug_package(
            run_id=run_id,
            latest=latest,
            debug_root=debug_root or get_debug_root(),
            archive_root=archive_root or get_archive_root(),
            include_sqlite=include_sqlite,
            include_terminal_log=include_terminal_log,
            include_configs=include_configs,
            include_reports=include_reports,
            include_final_reports=include_final_reports,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = {
        "run_id": result.run_id,
        "archive_path": str(result.archive_path),
        "manifest": result.manifest,
    }
    if open_folder:
        try:
            os.startfile(str(result.archive_path.parent))  # type: ignore[attr-defined]
        except Exception:
            pass
    text = (
        "Complete review package created:\n"
        f"{result.archive_path}\n\n"
        "Upload this file:\n"
        f"{result.archive_path}"
    )
    _emit_report(payload, json_output=json_output, text=text)


@app.command("trader-audit-journal")
def trader_audit_journal(
    journal_path: str | None = typer.Option(None, "--journal-path"),
    starting_cash: float = typer.Option(1000.0, "--starting-cash"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    allow_noncanonical_output_paths: bool = typer.Option(False, "--allow-noncanonical-output-paths"),
) -> None:
    """Audit the fake-money trader journal ledger and event history."""
    selected_run_id = _latest_run_id_or() if journal_path is None else "custom"
    journal_path = str(
        _resolve_cli_output_path(
            journal_path,
            get_journal_path(selected_run_id),
            allow_noncanonical=allow_noncanonical_output_paths,
        )
    )
    journal = _trader_journal(journal_path)
    runs = list(reversed(journal.latest(limit=100000)))
    latest_context = (runs[-1].get("context") if runs else {}) or {}
    positions = journal.load_open_positions()
    orders = journal.load_open_orders()
    fills = journal.load_fills()
    snapshot = _trader_portfolio_snapshot(latest_context, positions, fills, starting_cash=starting_cash)
    counts: Counter[str] = Counter()
    for run in runs:
        action = _trader_action_label(run)
        counts[action] += 1
    fees_paid = 0.0
    for run in runs:
        for row in _journal_analysis_rows([run]):
            fees_paid += (_float_or_none(row.get("fee_cents")) or 0.0) * (_float_or_none(row.get("quantity")) or 0.0)
    payload = {
        "journal_path": journal_path,
        "snapshot": snapshot,
        "reconciliation": _portfolio_reconciliation(snapshot),
        "fees_paid_cents": fees_paid,
        "open_positions": positions,
        "open_orders": orders,
        "exposure": snapshot.get("open_exposure_value"),
        "resumed_state_exists": bool(positions or orders or fills),
        "first_iteration_time": (runs[0].get("context") or {}).get("current_time_utc") if runs else None,
        "last_iteration_time": (runs[-1].get("context") or {}).get("current_time_utc") if runs else None,
        "event_counts": dict(counts),
        "live_trading_enabled": False,
        "real_orders_available": False,
    }
    _emit_report(payload, json_output=json_output, output=output, text=_trader_audit_journal_text(payload))


@app.command("trader-replay")
def trader_replay(
    input_path: str = typer.Option("reports/trader_agent/contexts.jsonl", "--input"),
    series: str | None = typer.Option(None, "--series"),
    station: str | None = typer.Option(None, "--station"),
    replay_date: str | None = typer.Option(None, "--date"),
    target_date: str | None = typer.Option(None, "--target-date"),
    race_id: str = typer.Option("trader_agent", "--race-id"),
    model_key: str | None = typer.Option(None, "--model-key"),
    llm_provider: str = typer.Option("mock", "--llm-provider"),
    model: str | None = typer.Option(None, "--model", "--llm-model"),
    llm_host: str | None = typer.Option(None, "--llm-host"),
    llm_timeout_seconds: int = typer.Option(60, "--llm-timeout-seconds"),
    llm_temperature: float = typer.Option(0.0, "--llm-temperature"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Replay saved trader contexts, or build one current context for --series/--station/--date."""
    path = Path(input_path)
    contexts = load_contexts_from_jsonl(path) if path.exists() else []
    if not contexts and (series or station or replay_date or target_date):
        settings = load_settings()
        resolved_series = series or settings.default_series
        resolved_station = station or settings.default_station
        resolved_date = _resolve_target_market_date(target_date or replay_date, tomorrow=False)
        context = _trader_context_for_cli(
            settings=settings,
            store_obj=_store(settings),
            series=resolved_series,
            station=resolved_station,
            target_date=resolved_date,
            race_id=race_id,
            model_key=model_key,
            risk_limits=RiskLimits(),
            journal_path=None,
        )
        contexts = [context]
    agent = TraderAgent(
        llm_client=_trader_llm_client(
            llm_provider=llm_provider,
            model=model,
            llm_host=llm_host,
            timeout_seconds=llm_timeout_seconds,
            temperature=llm_temperature,
            dry_run=dry_run,
        )
    )
    results = [result.to_dict() for result in replay_contexts(contexts, agent)]
    payload = {
        "input": str(path),
        "series": series,
        "station": station,
        "date": target_date or replay_date,
        "context_count": len(contexts),
        "results": results,
    }
    _emit_report(payload, json_output=json_output, output=output, text=json.dumps(safe_console_payload(payload), indent=2))


def _journal_analysis_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for payload in runs:
        context = payload.get("context") or {}
        validation = payload.get("validation") or {}
        decision = payload.get("approved_action") or payload.get("decision") or {}
        selected = _selected_trade_candidate(context, decision) or {}
        paper_execution = payload.get("paper_execution") or {}
        action = str(decision.get("action") or "HOLD")
        side = selected.get("side") or decision.get("side")
        clv_match = _best_clv_sample_for_candidate(
            payload.get("clv_samples") or [],
            str(decision.get("selected_candidate_id") or selected.get("candidate_id") or ""),
        )
        realized_pnl = _float_or_none(paper_execution.get("realized_pnl_dollars"))
        trade_taken = bool(
            validation.get("valid")
            and action != "HOLD"
            and (paper_execution.get("executed") or paper_execution.get("status") == "open")
        )
        rows.append(
            {
                "run_id": payload.get("iteration"),
                "strategy": payload.get("strategy"),
                "decision_mode": payload.get("decision_mode"),
                "action": action,
                "side": side,
                "bracket": _canonical_bracket_label(selected.get("bracket_label") or decision.get("bracket")),
                "trade_taken_by_rule": trade_taken,
                "failure_reason": validation.get("rejection_reason")
                or selected.get("ineligible_reason")
                or (decision.get("no_trade_reason") if action == "HOLD" else None),
                "model_probability": _float_or_none(selected.get("model_fair_cents")) / 100.0
                if _float_or_none(selected.get("model_fair_cents")) is not None
                else None,
                "net_edge_cents": _float_or_none(selected.get("fee_adjusted_edge_cents")),
                "fee_cents": _float_or_none(selected.get("fee_cents")),
                "spread_cents": _float_or_none(selected.get("spread_cents")),
                "quantity": _float_or_none(
                    paper_execution.get("quantity")
                    or (payload.get("paper_order") or {}).get("quantity")
                    or decision.get("max_contracts")
                ),
                "net_pnl_cents": None if realized_pnl is None else realized_pnl * 100.0,
                "clv_5m_cents": clv_match.get("clv_5m_cents") if clv_match else None,
                "clv_15m_cents": clv_match.get("clv_15m_cents") if clv_match else None,
                "clv_30m_cents": clv_match.get("clv_30m_cents") if clv_match else None,
                "clv_final_cents": clv_match.get("clv_final_cents") if clv_match else None,
            }
        )
    return rows


def _best_clv_sample_for_candidate(samples: list[dict[str, Any]], selected_candidate_id: str) -> dict[str, Any] | None:
    if not selected_candidate_id:
        return None
    matches = [row for row in samples if str(row.get("selected_candidate_id") or "") == selected_candidate_id]
    if not matches:
        return None
    return max(matches, key=lambda row: _float_or_none(row.get("elapsed_minutes")) or 0.0)


def _journal_group_summary(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(group_by) or "--")
        bucket = grouped.setdefault(
            key,
            {"group": key, "runs": 0, "trades": 0, "net_pnl_cents": 0.0, "fees_cents": 0.0, "spread_cost_cents": 0.0},
        )
        bucket["runs"] += 1
        if row.get("trade_taken_by_rule"):
            bucket["trades"] += 1
        bucket["net_pnl_cents"] += _float_or_none(row.get("net_pnl_cents")) or 0.0
        quantity = _float_or_none(row.get("quantity")) or 0.0
        bucket["fees_cents"] += (_float_or_none(row.get("fee_cents")) or 0.0) * quantity
        bucket["spread_cost_cents"] += ((_float_or_none(row.get("spread_cents")) or 0.0) / 2.0) * quantity
    return sorted(grouped.values(), key=lambda row: (str(row["group"])))


def _journal_analysis_text(payload: dict[str, Any]) -> str:
    report = payload.get("strategy_report") or {}
    attribution = payload.get("attribution") or []
    groups = payload.get("groups") or []
    lines = [
        "Kalshi Weather Trader Journal Analysis",
        "======================================",
        f"Runs: {payload.get('run_count', 0)} | Group by: {payload.get('group_by')}",
        (
            f"Trades: {report.get('trades', 0)} | Hold rate: {payload.get('hold_rate_pct', 0):.1f}% | "
            f"Net P&L: {_fmt_edge(report.get('net_pnl_cents'))}"
        ),
        (
            f"Avg net edge: {_fmt_edge(report.get('avg_net_edge_cents'))} | "
            f"Avg CLV: {_fmt_edge(report.get('avg_clv_cents'))} | "
            f"Fees paid: {_fmt_edge(payload.get('fees_paid_cents'))} | "
            f"Spread cost proxy: {_fmt_edge(payload.get('spread_cost_cents'))}"
        ),
        f"Brier score: {report.get('brier') if report.get('brier') is not None else '--'}",
        "",
        "Groups",
        "Group                          Runs  Trades  Net P&L  Fees   Spread",
        "-----------------------------  ----  ------  -------  -----  ------",
    ]
    for row in groups[:20]:
        lines.append(
            f"{_short_label(row.get('group'), max_len=29):<29}  "
            f"{int(row.get('runs') or 0):>4}  "
            f"{int(row.get('trades') or 0):>6}  "
            f"{_fmt_edge(row.get('net_pnl_cents')):>7}  "
            f"{_fmt_edge(row.get('fees_cents')):>5}  "
            f"{_fmt_edge(row.get('spread_cost_cents')):>6}"
        )
    lines.extend(["", "Attribution"])
    for row in attribution[:10]:
        lines.append(
            f"- {row.get('failure_reason')}: trades {row.get('trades')} | "
            f"PnL {_fmt_edge(row.get('net_pnl_cents'))} | "
            f"avg CLV {_fmt_edge(row.get('avg_clv_cents'))}"
        )
    return "\n".join(lines)


@trader_journal_app.callback()
def trader_journal(
    ctx: typer.Context,
    journal_path: str = typer.Option("reports/trader_agent/trader_runs.sqlite", "--journal-path"),
    limit: int = typer.Option(20, "--limit", "--latest"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Show recent LLM trader-agent journal entries."""
    if ctx.invoked_subcommand is not None:
        return
    journal = _trader_journal(journal_path)
    rows = journal.latest(limit=limit)
    payload = {
        "journal_path": journal_path,
        "count": len(rows),
        "open_positions": journal.load_open_positions(),
        "open_orders": journal.load_open_orders(),
        "runs": rows,
    }
    _emit_report(payload, json_output=json_output, output=output, text=json.dumps(safe_console_payload(payload), indent=2))


@trader_journal_app.command("analyze")
def trader_journal_analyze(
    journal_path: str = typer.Option("reports/trader_agent/trader_runs.sqlite", "--journal-path"),
    group_by: str = typer.Option("failure_reason", "--group-by"),
    limit: int = typer.Option(5000, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Analyze fake-money trader journal P&L, CLV, fees, and rejection reasons."""
    journal = _trader_journal(journal_path)
    runs = list(reversed(journal.latest(limit=limit)))
    rows = _journal_analysis_rows(runs)
    report = build_strategy_report(rows)
    groups = _journal_group_summary(rows, group_by)
    attribution = [asdict(row) for row in summarize_attribution(rows)]
    hold_count = sum(1 for row in rows if row.get("action") == "HOLD")
    fees_paid = sum((_float_or_none(row.get("fee_cents")) or 0.0) * (_float_or_none(row.get("quantity")) or 0.0) for row in rows)
    spread_cost = sum(((_float_or_none(row.get("spread_cents")) or 0.0) / 2.0) * (_float_or_none(row.get("quantity")) or 0.0) for row in rows)
    payload = {
        "journal_path": journal_path,
        "run_count": len(rows),
        "group_by": group_by,
        "hold_rate_pct": 0.0 if not rows else 100.0 * hold_count / len(rows),
        "fees_paid_cents": fees_paid,
        "spread_cost_cents": spread_cost,
        "strategy_report": asdict(report),
        "groups": groups,
        "attribution": attribution,
    }
    _emit_report(payload, json_output=json_output, output=output, text=_journal_analysis_text(payload))


def _research_backtest_records_from_snapshots(
    snapshots: list[dict[str, Any]],
    *,
    series: str,
    station: str,
    start_date: str | None,
    end_date: str | None,
    model_key: str | None,
) -> list[EdgeBacktestRecord]:
    records: list[EdgeBacktestRecord] = []
    for payload in snapshots:
        target_date = str(payload.get("target_date") or "")
        if series and str(payload.get("series") or "") != series:
            continue
        if station and str(payload.get("station") or "") != station:
            continue
        if start_date and target_date < start_date:
            continue
        if end_date and target_date > end_date:
            continue
        final_high = _snapshot_final_high(payload)
        if final_high is None:
            continue
        market_rows = (payload.get("market") or {}).get("brackets") or []
        official_final_bracket = _telemetry_bracket_for_temperature(final_high, market_rows)
        if official_final_bracket is None:
            continue
        selected_model_key = _research_probability_model_key(payload, model_key)
        probabilities = _research_probabilities_for_model(payload, selected_model_key)
        if not probabilities:
            continue
        quotes = [
            EdgeMarketQuote(
                bracket_label=_canonical_bracket_label(
                    row.get("bracket_label"),
                    lower_f=row.get("bracket_lower_f"),
                    upper_f=row.get("bracket_upper_f"),
                ),
                yes_bid_cents=_int_cents_or_none(row.get("yes_bid_cents")),
                yes_ask_cents=_int_cents_or_none(row.get("yes_ask_cents")),
                no_bid_cents=_int_cents_or_none(row.get("no_bid_cents")),
                no_ask_cents=_int_cents_or_none(row.get("no_ask_cents")),
            )
            for row in market_rows
        ]
        records.append(
            EdgeBacktestRecord(
                target_date=target_date,
                station=str(payload.get("station") or station),
                run_timestamp=str(payload.get("generated_at_utc") or payload.get("bucket_start_utc") or ""),
                official_final_high=float(final_high),
                official_final_bracket=official_final_bracket,
                probabilities=probabilities,
                quotes=quotes,
            )
        )
    return records


def _snapshot_final_high(payload: dict[str, Any]) -> float | None:
    final_high = payload.get("final_high") or {}
    for value in (
        final_high.get("official_high_f") if isinstance(final_high, dict) else None,
        final_high.get("final_high_f") if isinstance(final_high, dict) else None,
        payload.get("official_final_high"),
        payload.get("final_high_f"),
    ):
        number = _float_or_none(value)
        if number is not None:
            return number
    return None


def _research_probability_model_key(payload: dict[str, Any], requested: str | None) -> str | None:
    available = {
        f"{row.get('provider')}:{row.get('model_id')}"
        for row in payload.get("probabilities") or []
    }
    if requested:
        return requested if requested in available else None
    if "current:current_weighted_blend" in available:
        return "current:current_weighted_blend"
    return sorted(available)[0] if available else None


def _research_probabilities_for_model(payload: dict[str, Any], model_key: str | None) -> dict[str, float]:
    if not model_key:
        return {}
    probabilities: dict[str, float] = {}
    for row in payload.get("probabilities") or []:
        key = f"{row.get('provider')}:{row.get('model_id')}"
        if key != model_key:
            continue
        label = _canonical_bracket_label(
            row.get("bracket_label"),
            lower_f=row.get("bracket_lower_f"),
            upper_f=row.get("bracket_upper_f"),
        )
        probability = _float_or_none(row.get("p_yes"))
        if probability is not None:
            probabilities[label] = probability
    return probabilities


def _write_research_backtest_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    extra_columns = sorted({key for row in rows for key in row} - set(REQUIRED_BACKTEST_COLUMNS))
    columns = [*REQUIRED_BACKTEST_COLUMNS, *extra_columns]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _research_backtest_text(payload: dict[str, Any]) -> str:
    report = payload.get("strategy_report") or {}
    return "\n".join(
        [
            "Kalshi Weather Rule Backtest",
            "============================",
            f"Rows: {payload.get('row_count', 0)} | Source records: {payload.get('record_count', 0)}",
            f"Strategy: {payload.get('strategy')} | Decision mode: {payload.get('decision_mode')}",
            f"Trades: {report.get('trades', 0)} | Net P&L: {_fmt_edge(report.get('net_pnl_cents'))}",
            f"Avg net edge: {_fmt_edge(report.get('avg_net_edge_cents'))} | Avg CLV: {_fmt_edge(report.get('avg_clv_cents'))}",
            f"Brier: {report.get('brier') if report.get('brier') is not None else '--'}",
            f"Output: {payload.get('output')}",
        ]
    )


@app.command("research-backtest")
def research_backtest(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    strategy: str = typer.Option("hybrid", "--strategy"),
    decision_mode: str = typer.Option("rules", "--decision-mode"),
    model_key: str | None = typer.Option(None, "--model-key"),
    include_fees: bool = typer.Option(True, "--include-fees/--no-include-fees"),
    journal_path: str = typer.Option("journals/lax_model_validation.sqlite", "--journal-path"),
    output: str = typer.Option("reports/research/klax_rule_backtest.csv", "--output"),
    min_edge_cents: float = typer.Option(8.0, "--min-edge-cents"),
    min_no_edge_cents: float | None = typer.Option(None, "--min-no-edge-cents"),
    min_yes_edge_cents: float | None = typer.Option(None, "--min-yes-edge-cents"),
    min_no_upside_cents: float = typer.Option(8.0, "--min-no-upside-cents"),
    max_no_bin_probability: float = typer.Option(0.20, "--max-no-bin-probability"),
    max_spread_cents: int = typer.Option(4, "--max-spread-cents"),
    slippage_cents: float = typer.Option(0.5, "--slippage-cents"),
    tail_risk_padding_cents: float = typer.Option(2.0, "--tail-risk-padding-cents"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Backtest deterministic rule candidates from record-only validation snapshots."""
    strategy = _normalize_trader_strategy(strategy)
    decision_mode = _normalize_trader_decision_mode(decision_mode)
    if decision_mode not in {"rules", "llm-review"}:
        raise typer.BadParameter("research-backtest only supports rules or llm-review decision modes.")
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    snapshots = ValidationJournal(journal_path).load_snapshots()
    records = _research_backtest_records_from_snapshots(
        snapshots,
        series=series,
        station=station,
        start_date=start_date,
        end_date=end_date,
        model_key=model_key,
    )
    cost_config = EdgeCostConfig(
        include_fees=include_fees,
        slippage_cents=slippage_cents,
        tail_risk_padding_cents=tail_risk_padding_cents,
    )
    risk_config = EdgeRiskConfig(
        min_edge_cents=min_edge_cents,
        min_yes_edge_cents=min_edge_cents if min_yes_edge_cents is None else min_yes_edge_cents,
        min_no_edge_cents=min_edge_cents if min_no_edge_cents is None else min_no_edge_cents,
        min_no_upside_cents=min_no_upside_cents,
        max_no_bin_probability=max_no_bin_probability,
        max_spread_cents=max_spread_cents,
    )
    rows = run_synthetic_backtest(
        records,
        series=series,
        cost_config=cost_config,
        risk_config=risk_config,
        strategy_config=EdgeStrategyConfig(strategy=strategy, decision_mode=decision_mode, order_style="taker"),
    )
    _write_research_backtest_csv(output, rows)
    report = build_strategy_report(rows)
    payload = {
        "series": series,
        "station": station,
        "start_date": start_date,
        "end_date": end_date,
        "strategy": strategy,
        "decision_mode": decision_mode,
        "model_key": model_key,
        "journal_path": journal_path,
        "output": output,
        "record_count": len(records),
        "row_count": len(rows),
        "required_columns": REQUIRED_BACKTEST_COLUMNS,
        "strategy_report": asdict(report),
        "live_trading_enabled": False,
        "real_orders_available": False,
    }
    _emit_report(payload, json_output=json_output, output=None, text=_research_backtest_text(payload))


@app.command("paper-model-race-once")
def paper_model_race_once(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("default", "--race-id"),
    race_mode: str = typer.Option("independent", "--race-mode"),
    block_outlier_models: bool = typer.Option(False, "--block-outlier-models"),
    exclude_models: str | None = typer.Option(None, "--exclude-models"),
    starting_cash_per_model: float = typer.Option(100.0, "--starting-cash-per-model"),
    base_hurdle: float = typer.Option(0.09, "--base-hurdle"),
    max_risk_per_trade: float = typer.Option(5.0, "--max-risk-per-trade"),
    max_exposure_per_model: float = typer.Option(25.0, "--max-exposure-per-model"),
    max_exposure_per_bracket: float = typer.Option(10.0, "--max-exposure-per-bracket"),
    max_daily_fake_loss_per_model: float = typer.Option(10.0, "--max-daily-fake-loss-per-model"),
    no_daily_loss_limit: bool = typer.Option(False, "--no-daily-loss-limit"),
    profit_target_cents: int = typer.Option(10, "--profit-target-cents"),
    stop_loss_cents: int = typer.Option(6, "--stop-loss-cents"),
    cooldown_after_stop_minutes: int = typer.Option(30, "--cooldown-after-stop-minutes"),
    max_hold_minutes: int = typer.Option(45, "--max-hold-minutes"),
    max_open_positions_per_model: int = typer.Option(1, "--max-open-positions-per-model"),
    force_flat_time_local: str = typer.Option("17:55", "--force-flat-time-local"),
    stale_model_minutes: int = typer.Option(45, "--stale-model-minutes"),
    require_exit_bid: bool = typer.Option(True, "--require-exit-bid/--no-require-exit-bid"),
    max_spread_cents: float = typer.Option(15.0, "--max-spread-cents"),
    allow_penny_contracts: bool = typer.Option(False, "--allow-penny-contracts"),
    minimum_top_book_size: float = typer.Option(1.0, "--minimum-top-book-size"),
    synthetic_zero_exit: bool = typer.Option(False, "--synthetic-zero-exit"),
    advisor_mode: str = typer.Option("off", "--advisor-mode"),
    advisor_required: bool = typer.Option(True, "--advisor-required/--no-advisor-required"),
    advisor_log_dir: str = typer.Option("reports/llm_trade_advisor", "--advisor-log-dir"),
    advisor_min_score: int = typer.Option(75, "--advisor-min-score"),
    advisor_provider_config: str | None = typer.Option(None, "--advisor-provider-config"),
    advisor_output_json: bool = typer.Option(False, "--advisor-output-json"),
    use_llm_advisor: bool = typer.Option(False, "--use-llm-advisor"),
    llm_provider: str = typer.Option("ollama", "--llm-provider"),
    llm_model: str = typer.Option(DEFAULT_LLM_MODEL, "--llm-model"),
    llm_host: str | None = typer.Option(None, "--llm-host"),
    llm_timeout_seconds: int = typer.Option(60, "--llm-timeout-seconds"),
    llm_max_retries: int = typer.Option(2, "--llm-max-retries"),
    llm_temperature: float = typer.Option(0.0, "--llm-temperature"),
    llm_decision_log: str = typer.Option("reports/llm_advisor_decisions", "--llm-decision-log"),
    llm_rule_only: bool = typer.Option(False, "--llm-rule-only"),
    llm_dry_run: bool = typer.Option(False, "--llm-dry-run"),
    llm_show_prompt: bool = typer.Option(False, "--llm-show-prompt"),
    llm_show_raw_response: bool = typer.Option(False, "--llm-show-raw-response"),
    llm_fallback_action: str = typer.Option("wait", "--llm-fallback-action"),
    llm_first: bool = typer.Option(False, "--llm-first"),
    debug_decisions: bool = typer.Option(False, "--debug-decisions"),
    reset: bool = typer.Option(False, "--reset"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run one fake-money model race update. This command never trades live."""
    _ = verbose
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    store_obj = _store(settings)
    config = _model_race_config(
        race_id=race_id,
        race_mode=race_mode,
        starting_cash_per_model=starting_cash_per_model,
        base_hurdle=base_hurdle,
        max_risk_per_trade=max_risk_per_trade,
        max_exposure_per_model=max_exposure_per_model,
        max_exposure_per_bracket=max_exposure_per_bracket,
        max_daily_fake_loss_per_model=(
            None if no_daily_loss_limit else max_daily_fake_loss_per_model
        ),
        profit_target_cents=profit_target_cents,
        stop_loss_cents=stop_loss_cents,
        cooldown_after_stop_minutes=cooldown_after_stop_minutes,
        max_hold_minutes=max_hold_minutes,
        max_open_positions_per_model=max_open_positions_per_model,
        force_flat_time_local=force_flat_time_local,
        stale_model_minutes=stale_model_minutes,
        require_exit_bid=require_exit_bid,
        max_spread_cents=max_spread_cents,
        allow_penny_contracts=allow_penny_contracts,
        minimum_top_book_size=minimum_top_book_size,
        synthetic_zero_exit_on_force_flat=synthetic_zero_exit,
        block_outlier_models=block_outlier_models,
        advisor_mode=advisor_mode,
        advisor_required=advisor_required,
        advisor_log_dir=advisor_log_dir,
        advisor_min_score=advisor_min_score,
        advisor_provider_config=advisor_provider_config,
        advisor_output_json=advisor_output_json,
        use_llm_advisor=use_llm_advisor,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_host=llm_host,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_max_retries=llm_max_retries,
        llm_temperature=llm_temperature,
        llm_decision_log=llm_decision_log,
        llm_rule_only=llm_rule_only,
        llm_dry_run=llm_dry_run,
        llm_show_prompt=llm_show_prompt,
        llm_show_raw_response=llm_show_raw_response,
        llm_fallback_action=llm_fallback_action,
        llm_first=llm_first,
    )
    config = _model_race_config_with_exclusions(config, exclude_models)
    model_payload = _model_race_model_payload(settings, series, station, resolved_target_date)
    payload = run_model_race_once(store_obj, model_payload, config, reset=reset)
    text = _model_race_terminal_text(payload, debug_decisions=debug_decisions)
    _write_model_race_latest_outputs(store_obj, payload, text=text)
    _emit_report(payload, json_output=json_output, output=output, text=text)


@app.command("paper-model-race-run")
def paper_model_race_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    target_date: str | None = typer.Option(None, "--target-date"),
    tomorrow: bool = typer.Option(False, "--tomorrow"),
    race_id: str = typer.Option("default", "--race-id"),
    race_mode: str = typer.Option("independent", "--race-mode"),
    block_outlier_models: bool = typer.Option(False, "--block-outlier-models"),
    exclude_models: str | None = typer.Option(None, "--exclude-models"),
    starting_cash_per_model: float = typer.Option(100.0, "--starting-cash-per-model"),
    interval_seconds: int = typer.Option(900, "--interval-seconds"),
    entry_interval_seconds: int | None = typer.Option(None, "--entry-interval-seconds"),
    exit_interval_seconds: int = typer.Option(60, "--exit-interval-seconds"),
    model_refresh_interval_seconds: int | None = typer.Option(None, "--model-refresh-interval-seconds"),
    model_worker_mode: bool = typer.Option(False, "--model-worker-mode/--batch-model-mode"),
    model_worker_count: int = typer.Option(4, "--model-worker-count"),
    exit_monitor_only: bool = typer.Option(False, "--exit-monitor-only"),
    entry_only: bool = typer.Option(False, "--entry-only"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    max_entry_iterations: int | None = typer.Option(None, "--max-entry-iterations"),
    max_exit_iterations: int | None = typer.Option(None, "--max-exit-iterations"),
    base_hurdle: float = typer.Option(0.09, "--base-hurdle"),
    max_risk_per_trade: float = typer.Option(5.0, "--max-risk-per-trade"),
    max_exposure_per_model: float = typer.Option(25.0, "--max-exposure-per-model"),
    max_exposure_per_bracket: float = typer.Option(10.0, "--max-exposure-per-bracket"),
    max_daily_fake_loss_per_model: float = typer.Option(10.0, "--max-daily-fake-loss-per-model"),
    no_daily_loss_limit: bool = typer.Option(False, "--no-daily-loss-limit"),
    profit_target_cents: int = typer.Option(10, "--profit-target-cents"),
    stop_loss_cents: int = typer.Option(6, "--stop-loss-cents"),
    cooldown_after_stop_minutes: int = typer.Option(30, "--cooldown-after-stop-minutes"),
    max_hold_minutes: int = typer.Option(45, "--max-hold-minutes"),
    max_open_positions_per_model: int = typer.Option(1, "--max-open-positions-per-model"),
    force_flat_time_local: str = typer.Option("17:55", "--force-flat-time-local"),
    stale_model_minutes: int = typer.Option(45, "--stale-model-minutes"),
    require_exit_bid: bool = typer.Option(True, "--require-exit-bid/--no-require-exit-bid"),
    max_spread_cents: float = typer.Option(15.0, "--max-spread-cents"),
    allow_penny_contracts: bool = typer.Option(False, "--allow-penny-contracts"),
    minimum_top_book_size: float = typer.Option(1.0, "--minimum-top-book-size"),
    force_flat_at_end: bool = typer.Option(False, "--force-flat-at-end"),
    synthetic_zero_exit: bool = typer.Option(False, "--synthetic-zero-exit"),
    advisor_mode: str = typer.Option("off", "--advisor-mode"),
    advisor_required: bool = typer.Option(True, "--advisor-required/--no-advisor-required"),
    advisor_log_dir: str = typer.Option("reports/llm_trade_advisor", "--advisor-log-dir"),
    advisor_min_score: int = typer.Option(75, "--advisor-min-score"),
    advisor_provider_config: str | None = typer.Option(None, "--advisor-provider-config"),
    advisor_output_json: bool = typer.Option(False, "--advisor-output-json"),
    use_llm_advisor: bool = typer.Option(False, "--use-llm-advisor"),
    llm_provider: str = typer.Option("ollama", "--llm-provider"),
    llm_model: str = typer.Option(DEFAULT_LLM_MODEL, "--llm-model"),
    llm_host: str | None = typer.Option(None, "--llm-host"),
    llm_timeout_seconds: int = typer.Option(60, "--llm-timeout-seconds"),
    llm_max_retries: int = typer.Option(2, "--llm-max-retries"),
    llm_temperature: float = typer.Option(0.0, "--llm-temperature"),
    llm_decision_log: str = typer.Option("reports/llm_advisor_decisions", "--llm-decision-log"),
    llm_rule_only: bool = typer.Option(False, "--llm-rule-only"),
    llm_dry_run: bool = typer.Option(False, "--llm-dry-run"),
    llm_show_prompt: bool = typer.Option(False, "--llm-show-prompt"),
    llm_show_raw_response: bool = typer.Option(False, "--llm-show-raw-response"),
    llm_fallback_action: str = typer.Option("wait", "--llm-fallback-action"),
    llm_first: bool = typer.Option(False, "--llm-first"),
    debug_decisions: bool = typer.Option(False, "--debug-decisions"),
    json_output: bool = typer.Option(False, "--json"),
    output_dir: str = typer.Option("reports/model_race", "--output-dir"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run a fake-money model race loop. This command never trades live."""
    _ = verbose
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    resolved_target_date = _resolve_target_market_date(target_date, tomorrow)
    store_obj = _store(settings)
    config = _model_race_config(
        race_id=race_id,
        race_mode=race_mode,
        starting_cash_per_model=starting_cash_per_model,
        base_hurdle=base_hurdle,
        max_risk_per_trade=max_risk_per_trade,
        max_exposure_per_model=max_exposure_per_model,
        max_exposure_per_bracket=max_exposure_per_bracket,
        max_daily_fake_loss_per_model=(
            None if no_daily_loss_limit else max_daily_fake_loss_per_model
        ),
        profit_target_cents=profit_target_cents,
        stop_loss_cents=stop_loss_cents,
        cooldown_after_stop_minutes=cooldown_after_stop_minutes,
        max_hold_minutes=max_hold_minutes,
        max_open_positions_per_model=max_open_positions_per_model,
        force_flat_time_local=force_flat_time_local,
        force_flat_at_end=force_flat_at_end,
        stale_model_minutes=stale_model_minutes,
        require_exit_bid=require_exit_bid,
        max_spread_cents=max_spread_cents,
        allow_penny_contracts=allow_penny_contracts,
        minimum_top_book_size=minimum_top_book_size,
        synthetic_zero_exit_on_force_flat=synthetic_zero_exit,
        block_outlier_models=block_outlier_models,
        advisor_mode=advisor_mode,
        advisor_required=advisor_required,
        advisor_log_dir=advisor_log_dir,
        advisor_min_score=advisor_min_score,
        advisor_provider_config=advisor_provider_config,
        advisor_output_json=advisor_output_json,
        use_llm_advisor=use_llm_advisor,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_host=llm_host,
        llm_timeout_seconds=llm_timeout_seconds,
        llm_max_retries=llm_max_retries,
        llm_temperature=llm_temperature,
        llm_decision_log=llm_decision_log,
        llm_rule_only=llm_rule_only,
        llm_dry_run=llm_dry_run,
        llm_show_prompt=llm_show_prompt,
        llm_show_raw_response=llm_show_raw_response,
        llm_fallback_action=llm_fallback_action,
        llm_first=llm_first,
    )
    config = _model_race_config_with_exclusions(config, exclude_models)
    iterations: list[dict[str, Any]] = []
    entry_interval = model_refresh_interval_seconds or entry_interval_seconds or interval_seconds
    if max_entry_iterations is None:
        max_entry_iterations = max_iterations
    if duration_minutes is not None:
        max_entry_iterations = max_entry_iterations or max(1, math.ceil((duration_minutes * 60) / entry_interval))
        max_exit_iterations = max_exit_iterations or max(1, math.ceil((duration_minutes * 60) / exit_interval_seconds))
    max_entry_iterations = 0 if exit_monitor_only else (max_entry_iterations or 1)
    if max_exit_iterations is None:
        if entry_only:
            max_exit_iterations = 0
        elif exit_monitor_only:
            max_exit_iterations = max_iterations or 1
        else:
            max_exit_iterations = max(0, max_entry_iterations * max(1, math.ceil(entry_interval / exit_interval_seconds)))
    if model_worker_mode and not exit_monitor_only:
        if config.race_mode != "independent":
            raise typer.BadParameter("model-worker-mode currently requires --race-mode independent.")
        _run_model_race_worker_loop(
            settings=settings,
            series=series,
            station=station,
            target_date=resolved_target_date,
            store_obj=store_obj,
            config=config,
            entry_interval=entry_interval,
            exit_interval_seconds=exit_interval_seconds,
            max_entry_iterations=max_entry_iterations,
            max_exit_iterations=max_exit_iterations,
            entry_only=entry_only,
            force_flat_at_end=force_flat_at_end,
            json_output=json_output,
            output_dir=output_dir,
            model_worker_count=model_worker_count,
            debug_decisions=debug_decisions,
        )
        return
    session_dir = timestamped_report_dir(output_dir, "model_race")
    latest_model_payload: dict[str, Any] | None = None
    entry_count = 0
    exit_count = 0

    def emit(payload: dict[str, Any], kind: str, number: int) -> None:
        payload["iteration"] = number
        payload["iteration_kind"] = kind
        iterations.append(payload)
        text = _model_race_terminal_text(payload, debug_decisions=debug_decisions)
        _write_model_race_latest_outputs(store_obj, payload, output_dir=output_dir, text=text)
        if json_output:
            console.print(safe_console_payload(payload))
        else:
            console.print(text)

    while entry_count < max_entry_iterations or exit_count < max_exit_iterations:
        if entry_count < max_entry_iterations and not exit_monitor_only:
            entry_count += 1
            try:
                latest_model_payload = _model_race_model_payload(settings, series, station, resolved_target_date)
            except Exception as exc:  # noqa: BLE001
                console.print(f"entry refresh skipped: {exc}")
                console.file.flush()
                if entry_only and entry_count < max_entry_iterations:
                    time.sleep(entry_interval)
                if entry_only:
                    continue
                latest_model_payload = None
            else:
                emit(run_model_race_once(store_obj, latest_model_payload, config), "entry", entry_count)
        elif entry_count < max_entry_iterations and exit_monitor_only:
            entry_count = max_entry_iterations
        if entry_only:
            if entry_count < max_entry_iterations:
                time.sleep(entry_interval)
            continue
        exit_ticks_this_entry = (
            max_exit_iterations - exit_count
            if exit_monitor_only
            else min(max_exit_iterations - exit_count, max(1, math.ceil(entry_interval / exit_interval_seconds)))
        )
        for _ in range(exit_ticks_this_entry):
            if exit_count >= max_exit_iterations:
                break
            if iterations:
                time.sleep(exit_interval_seconds)
            exit_count += 1
            try:
                exit_payload = _model_race_exit_payload(settings, series, station, race_id=config.race_id)
            except Exception as exc:  # noqa: BLE001
                console.print(f"exit monitor skipped: {exc}")
                console.file.flush()
                continue
            latest_model_payload = exit_payload
            emit(run_model_race_exit_monitor(store_obj, exit_payload, config), "exit", exit_count)
        if exit_monitor_only:
            continue
    if force_flat_at_end and latest_model_payload is not None:
        force_flat_model_race(store_obj, race_id, latest_model_payload, config)
    _write_model_race_session_outputs(session_dir, iterations, store_obj, race_id)


@app.command("paper-model-race-exit-monitor")
def paper_model_race_exit_monitor(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    race_id: str = typer.Option("default", "--race-id"),
    race_mode: str = typer.Option("independent", "--race-mode"),
    block_outlier_models: bool = typer.Option(False, "--block-outlier-models"),
    interval_seconds: int = typer.Option(60, "--interval-seconds"),
    duration_minutes: float | None = typer.Option(None, "--duration-minutes"),
    max_iterations: int | None = typer.Option(None, "--max-iterations"),
    force_flat_at_end: bool = typer.Option(False, "--force-flat-at-end"),
    synthetic_zero_exit: bool = typer.Option(False, "--synthetic-zero-exit"),
    debug_decisions: bool = typer.Option(False, "--debug-decisions"),
    json_output: bool = typer.Option(False, "--json"),
    output_dir: str = typer.Option("reports/model_race", "--output-dir"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Monitor exits for existing fake model-race positions without refreshing slow models."""
    _ = verbose
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    store_obj = _store(settings)
    config = _model_race_config(
        race_id=race_id,
        race_mode=race_mode,
        starting_cash_per_model=100.0,
        base_hurdle=0.09,
        max_risk_per_trade=5.0,
        max_exposure_per_model=25.0,
        max_daily_fake_loss_per_model=10.0,
        profit_target_cents=10,
        stop_loss_cents=6,
        max_hold_minutes=45,
        force_flat_time_local="17:55",
        force_flat_at_end=force_flat_at_end,
        synthetic_zero_exit_on_force_flat=synthetic_zero_exit,
        block_outlier_models=block_outlier_models,
    )
    if max_iterations is None and duration_minutes is not None:
        max_iterations = max(1, math.ceil((duration_minutes * 60) / interval_seconds))
    max_iterations = max_iterations or 1
    latest_payload: dict[str, Any] | None = None
    for iteration in range(1, max_iterations + 1):
        latest_payload = _model_race_exit_payload(settings, series, station, race_id=race_id)
        payload = run_model_race_exit_monitor(store_obj, latest_payload, config)
        payload["iteration"] = iteration
        text = _model_race_terminal_text(payload, debug_decisions=debug_decisions)
        _write_model_race_latest_outputs(store_obj, payload, output_dir=output_dir, text=text)
        if json_output:
            console.print(safe_console_payload(payload))
        else:
            console.print(text)
        if iteration < max_iterations:
            time.sleep(interval_seconds)
    if force_flat_at_end and latest_payload is not None:
        force_flat_model_race(store_obj, race_id, latest_payload, config)


def _flatten_report_text(payload: dict[str, Any]) -> str:
    lines = [
        f"PAPER MODEL RACE FLATTEN - {payload.get('race_id')}",
        "Fake money only. No live orders were placed.",
        "",
        f"Positions closed: {payload.get('positions_closed')}",
        f"Positions blocked by no bid: {payload.get('positions_blocked_no_bid')}",
        f"Remaining open positions: {payload.get('remaining_open_positions')}",
        f"Realized P/L this flatten: {payload.get('realized_pnl')}",
    ]
    if payload.get("blocked"):
        lines.append("")
        lines.append("Blocked positions:")
        for pos in payload["blocked"]:
            lines.append(
                f"- {pos.get('model_key')}: {pos.get('quantity')} {str(pos.get('side')).upper()} "
                f"on {pos.get('bracket_label_display') or pos.get('bracket_label')} | no exit bid"
            )
    return "\n".join(lines)


def _advisor_synthetic_text(summary: dict[str, Any]) -> str:
    lines = [
        "LLM TRADE ADVISOR SYNTHETIC TEST",
        f"Scenario count: {summary.get('scenario_count')}",
        f"Passed: {summary.get('passed_count')}",
        f"Failed: {summary.get('failed_count')}",
        f"Output report: {summary.get('report_path')}",
        "Network used: false",
        "Live trading enabled: false",
    ]
    failed = summary.get("failed_scenarios") or []
    if failed:
        lines.append("Failed scenarios: " + ", ".join(failed))
    return "\n".join(lines)


def _advisor_dry_run_text(payload: dict[str, Any]) -> str:
    lines = [
        "LLM TRADE ADVISOR DRY RUN",
        f"Race ID: {payload.get('race_id')}",
        f"Series/station/date: {payload.get('series')} / {payload.get('station')} / {payload.get('market_date')}",
        f"Advisor mode: {payload.get('advisor_mode')}",
        "Fake trade executed: false",
        "Live trading enabled: false",
        "",
        "Model                    Signal      Advisor   Score  Validator  Final",
    ]
    for row in payload.get("rows", []):
        advisor = row.get("advisor") or {}
        lines.append(
            f"{str(row.get('model_key')):<24} {str(row.get('signal_action')):<11} "
            f"{str(advisor.get('advisor_decision') or '--'):<9} "
            f"{str(advisor.get('trade_quality_score') if advisor.get('trade_quality_score') is not None else '--'):<6} "
            f"{str(advisor.get('validator_status') or '--'):<10} "
            f"{str(advisor.get('final_action') or '--')}"
        )
    return "\n".join(lines)


def _advisor_decision_report_text(payload: dict[str, Any]) -> str:
    lines = [
        "LLM TRADE ADVISOR DECISION REPORT",
        f"Race ID: {payload.get('race_id') or 'all'}",
        f"Decisions: {payload.get('decision_count')}",
        f"Average score: {payload.get('average_score')}",
        "",
        "Advisor decisions:",
    ]
    for key, count in sorted((payload.get("advisor_decision_counts") or {}).items()):
        lines.append(f"- {key}: {count}")
    lines.append("")
    lines.append("Final actions:")
    for key, count in sorted((payload.get("final_action_counts") or {}).items()):
        lines.append(f"- {key}: {count}")
    vetoes = payload.get("veto_reason_counts") or {}
    if vetoes:
        lines.append("")
        lines.append("Common veto reasons:")
        for key, count in sorted(vetoes.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {key}: {count}")
    return "\n".join(lines)


def _advisor_export_text(payload: dict[str, Any]) -> str:
    return (
        "LLM TRADE ADVISOR TRAINING EXPORT\n"
        f"Race ID: {payload.get('race_id') or 'all'}\n"
        f"Decisions exported: {payload.get('decision_count')}\n"
        f"Output dir: {payload.get('output_dir')}\n"
        "Live trading enabled: false"
    )


def _write_advisor_training_exports(output_dir: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
    files = {
        "advisor_inputs": output_dir / "advisor_inputs.jsonl",
        "advisor_decisions": output_dir / "advisor_decisions.jsonl",
        "validator_results": output_dir / "validator_results.jsonl",
        "labeled_examples": output_dir / "labeled_examples.jsonl",
    }
    handles = {name: path.open("w", encoding="utf-8") for name, path in files.items()}
    try:
        for row in rows:
            input_payload = _loads_json(row.get("input_json"), {})
            output_payload = _loads_json(row.get("output_json"), {})
            final_payload = _loads_json(row.get("final_json"), {})
            label = {
                "race_id": row.get("race_id"),
                "model_key": row.get("model_key"),
                "market_ticker": row.get("market_ticker"),
                "advisor_decision": row.get("advisor_decision"),
                "final_action": row.get("final_action"),
                "validator_approved": bool(row.get("validator_approved")),
                "trade_quality_score": row.get("trade_quality_score"),
                "veto_reasons": _loads_json(row.get("veto_reasons_json"), []),
            }
            handles["advisor_inputs"].write(json.dumps(input_payload, default=str) + "\n")
            handles["advisor_decisions"].write(json.dumps(output_payload, default=str) + "\n")
            handles["validator_results"].write(json.dumps(final_payload, default=str) + "\n")
            handles["labeled_examples"].write(
                json.dumps({"input": input_payload, "decision": output_payload, "label": label}, default=str) + "\n"
            )
    finally:
        for handle in handles.values():
            handle.close()
    return {name: str(path) for name, path in files.items()}


def _loads_json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


@app.command("paper-model-race-flatten")
def paper_model_race_flatten(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    race_id: str = typer.Option("default", "--race-id"),
    confirm: bool = typer.Option(False, "--confirm"),
    market_only: bool = typer.Option(False, "--market-only"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
    synthetic_zero_exit: bool = typer.Option(False, "--synthetic-zero-exit"),
) -> None:
    """Safely flatten open fake model-race positions at available bids. Requires --confirm."""
    _ = market_only
    if not confirm:
        raise typer.BadParameter("Flatten requires --confirm.")
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    store_obj = _store(settings)
    config = _model_race_config(
        race_id=race_id,
        starting_cash_per_model=100.0,
        base_hurdle=0.09,
        max_risk_per_trade=5.0,
        max_exposure_per_model=25.0,
        max_daily_fake_loss_per_model=10.0,
        profit_target_cents=10,
        stop_loss_cents=6,
        max_hold_minutes=45,
        force_flat_time_local="17:55",
    )
    model_payload = _model_race_exit_payload(settings, series, station, race_id=race_id)
    payload = flatten_model_race(
        store_obj,
        race_id,
        model_payload,
        config,
        synthetic_zero_exit=synthetic_zero_exit,
    )
    text = _flatten_report_text(payload)
    report_path = Path("reports/model_race/flatten_report.txt")
    write_text_report(report_path, text)
    if output:
        _emit_report(payload, json_output=json_output, output=output, text=text)
    elif json_output:
        console.print(safe_console_payload(payload))
    else:
        console.print(text)


@app.command("paper-model-race-report")
def paper_model_race_report(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    race_id: str = typer.Option("default", "--race-id"),
    json_output: bool = typer.Option(False, "--json"),
    csv_output: bool = typer.Option(False, "--csv"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Report fake-money model race leaderboard and trades."""
    _ = (series, station)
    settings = load_settings()
    payload = model_race_report_payload(_store(settings), race_id)
    if csv_output or (output and output.lower().endswith(".csv")):
        rows = safe_console_payload(payload.get("leaderboard", []))
        if output:
            _write_csv(Path(output), rows)
        else:
            writer = csv.DictWriter(
                console.file,
                fieldnames=list(rows[0]) if rows else ["message"],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows or [{"message": "no rows"}])
        return
    _emit_report(payload, json_output=json_output, output=output, text=model_race_report_text(payload))


@app.command("paper-model-race-reset")
def paper_model_race_reset(
    race_id: str = typer.Option("default", "--race-id"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Reset fake model race accounts. Requires --confirm."""
    if not confirm:
        raise typer.BadParameter("Reset requires --confirm.")
    settings = load_settings()
    event_id = _store(settings).reset_model_race(race_id, "manual paper model race reset")
    console.print(f"Reset fake-money model race '{race_id}' (event_id={event_id}).")


@app.command("synthetic-scenarios-build")
def synthetic_scenarios_build(
    scenario_set: str = typer.Option("model_race_edge_cases", "--scenario-set"),
    output_dir: str = typer.Option("data/synthetic_scenarios/model_race_edge_cases", "--output-dir"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    seed: int | None = typer.Option(None, "--seed"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build offline synthetic Kalshi-like edge-case scenarios. No network is used."""
    _ = seed
    if scenario_set != "model_race_edge_cases":
        raise typer.BadParameter("Only scenario-set 'model_race_edge_cases' is currently implemented.")
    manifest = build_default_scenario_set(Path(output_dir), overwrite=overwrite)
    text = (
        "SYNTHETIC SCENARIOS BUILD\n"
        f"Scenario set: {manifest.get('scenario_set')}\n"
        f"Scenario count: {manifest.get('scenario_count')}\n"
        f"Output dir: {output_dir}\n"
        "Network used: false\n"
        "Live trading enabled: false"
    )
    _emit_report(manifest, json_output=json_output, text=text)


@app.command("synthetic-scenarios-list")
def synthetic_scenarios_list(
    scenario_dir: str = typer.Option("data/synthetic_scenarios/model_race_edge_cases", "--scenario-dir"),
    build_if_missing: bool = typer.Option(True, "--build-if-missing/--no-build-if-missing"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List offline synthetic edge-case scenarios."""
    directory = Path(scenario_dir)
    if build_if_missing:
        load_or_build_default_scenario_dir(directory)
    rows = scenario_index(directory)
    if json_output:
        console.print_json(data=safe_console_payload(rows))
        return
    table = Table(title="Synthetic Edge-Case Scenarios")
    table.add_column("scenario_id")
    table.add_column("category")
    table.add_column("expected")
    table.add_column("name")
    for row in rows:
        table.add_row(
            str(row.get("scenario_id")),
            str(row.get("category")),
            str(row.get("expected_key_action")),
            str(row.get("name")),
        )
    console.print(table)


@app.command("synthetic-scenario-run")
def synthetic_scenario_run(
    scenario_id: str = typer.Option(..., "--scenario-id"),
    scenario_dir: str = typer.Option("data/synthetic_scenarios/model_race_edge_cases", "--scenario-dir"),
    output_dir: str = typer.Option("reports/synthetic_scenarios", "--output-dir", "--output"),
    charts: bool = typer.Option(False, "--charts"),
    race_mode: str | None = typer.Option(None, "--race-mode"),
    starting_cash_per_model: float = typer.Option(100.0, "--starting-cash-per-model"),
    fail_on_mismatch: bool = typer.Option(False, "--fail-on-mismatch"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Run one offline synthetic scenario through the fake-money model-race logic."""
    _ = verbose
    directory = Path(scenario_dir)
    load_or_build_default_scenario_dir(directory)
    scenario_path = Path(scenario_id)
    if not scenario_path.exists():
        scenario_path = directory / f"{scenario_id}.json"
    scenario = load_scenario(scenario_path)
    run_dir = Path(output_dir) / scenario.scenario_id
    result = run_synthetic_scenario(
        scenario,
        output_dir=run_dir,
        charts=charts,
        race_mode=race_mode,
        starting_cash_per_model=starting_cash_per_model,
    )
    text = (
        "SYNTHETIC SCENARIO RUN\n"
        f"Scenario: {result['scenario_id']}\n"
        f"Passed: {str(result['passed']).lower()}\n"
        f"Mismatches: {len(result.get('mismatches', []))}\n"
        f"Final-state mismatches: {len(result.get('final_state_mismatches', []))}\n"
        f"Output dir: {run_dir}\n"
        "Network used: false\n"
        "Live trading enabled: false"
    )
    _emit_report(result, json_output=json_output, text=text)
    if fail_on_mismatch and not result["passed"]:
        raise typer.Exit(1)


@app.command("synthetic-algo-test")
def synthetic_algo_test(
    scenario_dir: str = typer.Option("data/synthetic_scenarios/model_race_edge_cases", "--scenario-dir"),
    output_dir: str = typer.Option("reports/synthetic_scenarios/summary", "--output-dir", "--output"),
    charts: bool = typer.Option(True, "--charts/--no-charts"),
    race_mode: str | None = typer.Option(None, "--race-mode"),
    starting_cash_per_model: float = typer.Option(100.0, "--starting-cash-per-model"),
    fail_on_mismatch: bool = typer.Option(False, "--fail-on-mismatch"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run all offline synthetic scenarios and write a results report. No network is used."""
    directory = Path(scenario_dir)
    load_or_build_default_scenario_dir(directory)
    summary = run_synthetic_algo_test(
        directory,
        output_dir=Path(output_dir),
        charts=charts,
        race_mode=race_mode,
        starting_cash_per_model=starting_cash_per_model,
    )
    text = (
        "SYNTHETIC ALGO TEST\n"
        f"Scenario count: {summary['scenario_count']}\n"
        f"Passed: {summary['passed_count']}\n"
        f"Failed: {summary['failed_count']}\n"
        f"Output dir: {summary['output_dir']}\n"
        "Network used: false\n"
        "Live trading enabled: false"
    )
    if summary["failed_scenarios"]:
        text += "\nFailed scenarios: " + ", ".join(summary["failed_scenarios"])
    _emit_report(summary, json_output=json_output, text=text)
    if fail_on_mismatch and not summary["passed"]:
        raise typer.Exit(1)


@app.command("model-estimate-score")
def model_estimate_score(
    station: str | None = typer.Option(None),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    json_output: bool = typer.Option(False, "--json"),
    output: str | None = typer.Option(None, "--output"),
) -> None:
    """Score stored comparison-model high estimates against official outcomes."""
    settings = load_settings()
    station = station or settings.default_station
    payload = _model_estimate_score_payload(_store(settings), station, start_date, end_date)
    _emit_report(
        payload,
        json_output=json_output,
        output=output,
        text=_model_estimate_score_text(payload),
    )


@app.command("validation-run")
def validation_run(
    series: str | None = typer.Option(None),
    station: str | None = typer.Option(None),
    collect: bool = typer.Option(False, "--collect"),
    skip_collect: bool = typer.Option(False, "--skip-collect"),
    after_settlement: bool = typer.Option(False, "--after-settlement"),
    reports_dir: str = typer.Option("reports/validation_runs", "--reports-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run a safe collect/report validation workflow. This command never trades."""
    settings = load_settings()
    series = series or settings.default_series
    station = station or settings.default_station
    store_obj = _store(settings)
    report_dir = timestamped_report_dir(reports_dir, "validation")
    summary: dict[str, Any] = {
        "series": series,
        "station": station,
        "report_dir": str(report_dir),
        "paper_trading": False,
        "live_trading_enabled": settings.kalshi_enable_real_orders,
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

    if collect and not skip_collect:
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
    if after_settlement:
        capture(
            "fetch_missing_outcomes",
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
        capture("join_outcomes", lambda: store_obj.join_predictions_to_outcomes(station=station, overwrite=True))
        capture("calibration_report", lambda: _calibration_report_or_empty(store_obj, station))
        capture("residual_report", lambda: _residual_report_payload(store_obj, station))
    health_payload = validation_reports.model_health_payload(
        store_obj,
        settings,
        series,
        station,
        reports_dir=reports_dir,
        paper_replay=_paper_replay_report(store_obj, min_edge=settings.min_edge),
    )
    summary["steps"]["model_health"] = {"status": "ok", "payload": health_payload}
    write_json_report(report_dir / "model_health.json", health_payload)
    write_text_report(report_dir / "model_health.txt", validation_reports.model_health_text(health_payload))
    summary["next_action"] = health_payload["next_action"]
    write_json_report(report_dir / "summary.json", summary)
    _emit_report(summary, json_output=json_output, text=validation_reports.model_health_text(health_payload))


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
    return validation_reports.calibration_readiness_payload(store_obj, station, settings)


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
    stored_dates = [row["date"] for row in rows if row["status"] == "stored"]
    parsed_not_stored_dates = [row["date"] for row in rows if row["status"] == "dry-run"]
    skipped_unsettled_dates = [row["date"] for row in rows if row["status"] == "skipped_unsettled"]
    failed_dates = [row["date"] for row in rows if row["status"] == "unavailable"]
    next_commands = [
        f"kalshi-weather fetch-missing-outcomes --station {station}",
        f"kalshi-weather join-outcomes --station {station} --overwrite",
        f"kalshi-weather calibration-readiness --station {station}",
    ]
    if failed_dates:
        next_commands.insert(
            1,
            f"kalshi-weather record-outcome --station {station} --date YYYY-MM-DD --official-high-f NN --source manual",
        )
    return {
        "station": station,
        "start_date": dates[0].isoformat() if dates else None,
        "end_date": dates[-1].isoformat() if dates else None,
        "status": "no_dates_to_attempt" if not dates else "complete",
        "dry_run": dry_run,
        "overwrite": overwrite,
        "settlement_buffer_hours": settlement_buffer_hours,
        "allow_unsettled_store": allow_unsettled_store,
        "latest_settled_market_date": latest_settled.isoformat(),
        "attempted_dates": [d.isoformat() for d in dates],
        "skipped_unsettled_dates": skipped_unsettled_dates,
        "parsed_not_stored_dates": parsed_not_stored_dates,
        "stored_dates": stored_dates,
        "failed_dates": failed_dates,
        "stored_count": sum(1 for row in rows if row["status"] == "stored"),
        "dry_run_success_count": sum(1 for row in rows if row["status"] == "dry-run"),
        "skipped_unsettled_count": skipped_unsettled,
        "unavailable_count": unavailable_count,
        "parse_error_count": unavailable_count,
        "per_date_results": rows,
        "next_commands": next_commands,
        "explanation": _outcome_backfill_explanation(rows, latest_settled),
    }


def _outcome_backfill_explanation(rows: list[dict[str, Any]], latest_settled: date) -> str:
    if not rows:
        return (
            "No prediction dates needed an outcome fetch. Either there are no predictions, "
            "all outcomes already exist, or unsettled dates were skipped."
        )
    if all(row["status"] == "skipped_unsettled" for row in rows):
        return f"All candidate dates are later than latest settled market date {latest_settled}."
    if any(row["status"] == "unavailable" for row in rows):
        return "One or more official NWS climate products were unavailable or could not be parsed."
    if any(row["status"] == "dry-run" for row in rows):
        return "NWS outcome values were parsed but not stored because --dry-run was used."
    if any(row["status"] == "stored" for row in rows):
        return "Official outcomes were stored; run join-outcomes next."
    return "Outcome workflow completed with no stored outcomes."


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    app()
