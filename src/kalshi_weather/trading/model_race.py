from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from kalshi_weather.advisor.decision_schema import AdvisorInput
from kalshi_weather.advisor.llm_trade_advisor import ADVISOR_MODE_OFF, advisor_for_mode, safe_decide
from kalshi_weather.advisor.risk_validator import validate_advisor_trade
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.llm.decision_log import write_llm_decision_log
from kalshi_weather.llm.json_guard import safe_fallback_decision
from kalshi_weather.llm.ollama_provider import OllamaLLMProvider
from kalshi_weather.llm.prompts import load_system_prompt
from kalshi_weather.llm.schemas import DEFAULT_LLM_MODEL
from kalshi_weather.llm.trade_snapshot import advisor_input_to_trade_snapshot
from kalshi_weather.trading.hard_risk_validator import hard_validator_result
from kalshi_weather.trading.trade_quality import deterministic_trade_quality
from kalshi_weather.time_utils import utc_now

DEFAULT_MODEL_RACE_MODELS = [
    ("current", "current_weighted_blend", "current_blend"),
    ("open_meteo", "best_match", "best_match"),
    ("open_meteo", "gfs013", "gfs013"),
    ("open_meteo", "gfs_global", "gfs_global"),
    ("open_meteo", "gfs_seamless", "gfs_seamless"),
    ("noaa_herbie", "hrrr", "HRRR"),
    ("noaa_herbie", "nbm", "NBM"),
    ("noaa_herbie", "gfs", "GFS direct"),
    ("noaa_herbie", "rap", "RAP"),
]

PACIFIC = ZoneInfo("America/Los_Angeles")
RACE_MODE_INDEPENDENT = "independent"
RACE_MODE_CONSENSUS_GUARDED = "consensus_guarded"
RACE_MODES = {RACE_MODE_INDEPENDENT, RACE_MODE_CONSENSUS_GUARDED}


@dataclass(frozen=True)
class ModelRaceConfig:
    race_id: str = "default"
    race_mode: str = RACE_MODE_INDEPENDENT
    starting_cash_per_model: Decimal = Decimal("100")
    max_risk_per_trade: Decimal = Decimal("5")
    max_exposure_per_model: Decimal = Decimal("25")
    max_exposure_per_bracket: Decimal = Decimal("10")
    max_daily_fake_loss_per_model: Decimal | None = Decimal("10")
    base_hurdle: Decimal = Decimal("0.09")
    profit_target_price_delta: Decimal = Decimal("0.10")
    stop_loss_price_delta: Decimal = Decimal("0.06")
    probability_drop_exit: float = 0.15
    max_hold_minutes: int = 45
    max_open_positions_per_model: int = 1
    force_flat_time_local: str = "17:55"
    one_position_per_event: bool = True
    rotate_probability_margin: float = 0.10
    stale_model_minutes: int = 45
    stale_market_minutes: int = 15
    fee_mode: str = "none"
    include_models: list[str] = field(
        default_factory=lambda: [f"{provider}:{model_id}" for provider, model_id, _ in DEFAULT_MODEL_RACE_MODELS]
    )
    block_outlier_models: bool = False
    outlier_threshold_f: float = 3.0
    force_flat_at_end: bool = False
    require_exit_bid_for_entry: bool = True
    max_spread_cents: Decimal = Decimal("15")
    minimum_top_book_size: Decimal = Decimal("1")
    allow_penny_contract_entries: bool = False
    penny_contract_price_cents: Decimal = Decimal("3")
    missing_bid_mark_mode: str = "na"
    max_missing_bid_warnings_before_block_new_entries: int = 1
    block_new_entries_if_any_open_position_missing_bid: bool = True
    block_new_entries_if_model_spread_gt_f: float = 4.0
    reduce_size_if_spread_gt_f: float = 2.0
    reduced_size_multiplier: Decimal = Decimal("0.5")
    synthetic_zero_exit_on_force_flat: bool = False
    block_outlier_model_entries: bool = False
    cooldown_after_stop_minutes: int = 30
    cooldown_after_illiquid_entry_minutes: int = 60
    cooldown_scope: str = "market_ticker"
    max_entry_price_cents: Decimal = Decimal("80")
    allow_high_price_entries: bool = False
    high_price_override_edge: Decimal = Decimal("0.25")
    advisor_mode: str = ADVISOR_MODE_OFF
    advisor_required: bool = True
    advisor_log_dir: str = "reports/llm_trade_advisor"
    advisor_min_score: int = 75
    advisor_provider_config: str | None = None
    advisor_output_json: bool = False
    advisor_prompt_path: str = "prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md"
    confirmed_edge_required_signal_seen_count: int = 2
    confirmed_edge_min_score_for_small_trade: int = 60
    confirmed_edge_require_market_confirmation: bool = False
    use_llm_advisor: bool = False
    llm_provider: str = "ollama"
    llm_model: str = DEFAULT_LLM_MODEL
    llm_host: str | None = None
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_temperature: float = 0.0
    llm_decision_log: str = "reports/llm_advisor_decisions"
    llm_rule_only: bool = False
    llm_dry_run: bool = False
    llm_show_prompt: bool = False
    llm_show_raw_response: bool = False
    llm_fallback_action: str = "WAIT"
    llm_first: bool = False


@dataclass
class ModelRaceAccountState:
    model_key: str
    provider: str
    model_id: str
    cash: Decimal
    starting_cash: Decimal
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    open_pnl: Decimal = Decimal("0")
    closed_pnl: Decimal = Decimal("0")
    exposure: Decimal = Decimal("0")
    status: str = "active"


@dataclass(frozen=True)
class ModelRaceSignal:
    model_key: str
    best_trade: str | None
    side: str | None
    market_ticker: str | None
    bracket_label: str | None
    bracket_lower_f: float | None
    bracket_upper_f: float | None
    bracket_type: str | None
    p_yes: float | None
    p_no: float | None
    ask: Decimal | None
    bid: Decimal | None
    edge: Decimal | None
    action: str
    reason: str


def model_key(provider: str, model_id: str) -> str:
    return f"{provider}:{model_id}"


def model_specs() -> list[dict[str, str]]:
    return [
        {"provider": provider, "model_id": model_id, "model_key": model_key(provider, model_id), "display_name": name}
        for provider, model_id, name in DEFAULT_MODEL_RACE_MODELS
    ]


def build_model_race_inputs(model_payload: dict[str, Any]) -> dict[str, Any]:
    estimates = {model_key(row["provider"], row["model_id"]): row for row in model_payload.get("estimates", [])}
    probabilities: dict[str, list[dict[str, Any]]] = {}
    for row in model_payload.get("probabilities", []):
        probabilities.setdefault(model_key(row["provider"], row["model_id"]), []).append(row)
    return {
        "generated_at_utc": model_payload.get("generated_at_utc"),
        "series": model_payload.get("series"),
        "station": model_payload.get("station"),
        "market_date": model_payload.get("market_date"),
        "observed_high_so_far_f": model_payload.get("observed_high_so_far_f"),
        "latest_observation_utc": model_payload.get("latest_observation_utc"),
        "current_production_estimate_f": model_payload.get("current_production_estimate_f"),
        "estimates_by_model": estimates,
        "probabilities_by_model": probabilities,
        "market_count": model_payload.get("markets_count"),
        "bracket_count": model_payload.get("bracket_count"),
    }


def run_model_race_once(
    store: SQLiteStore,
    model_payload: dict[str, Any],
    config: ModelRaceConfig,
    *,
    reset: bool = False,
    allow_entries: bool = True,
) -> dict[str, Any]:
    if reset:
        store.reset_model_race(config.race_id, "paper model race reset")
    inputs = build_model_race_inputs(model_payload)
    specs = [spec for spec in model_specs() if spec["model_key"] in set(config.include_models)]
    store.create_model_race(config.race_id, specs, config.starting_cash_per_model)
    accounts = _load_account_states(store, config, specs)
    estimates = inputs["estimates_by_model"]
    probabilities = inputs["probabilities_by_model"]
    agreement = model_agreement(estimates, config.outlier_threshold_f)
    inputs["agreement"] = agreement
    closed_trades: list[dict[str, Any]] = []
    open_positions_output: list[dict[str, Any]] = []
    scoreboard: list[dict[str, Any]] = []
    now = utc_now()
    for spec in specs:
        key = spec["model_key"]
        account = accounts[key]
        estimate = estimates.get(key)
        rows = probabilities.get(key, [])
        open_positions = store.load_open_model_race_positions(config.race_id, key)
        account.open_positions = open_positions
        exit_advisor_results: dict[str, dict[str, Any]] = {}
        exits = update_open_positions(
            store,
            account,
            rows,
            estimate,
            config,
            inputs,
            force_flat=False,
            now=now,
            advisor_results=exit_advisor_results,
        )
        closed_trades.extend(exits)
        account = _load_account_states(store, config, [spec])[key]
        account.open_positions = store.load_open_model_race_positions(config.race_id, key)
        signal = evaluate_model_signal(
            key,
            rows,
            estimate,
            account,
            config,
            inputs,
            is_outlier=key in agreement["outlier_models"],
            store=store,
            now=now,
            allow_entries=allow_entries,
        )
        advisor_result: dict[str, Any] | None = exit_advisor_results.get(key)
        if _should_apply_entry_advisor(config, signal, allow_entries=allow_entries):
            signal, advisor_result = _apply_advisor_to_signal(
                store,
                signal,
                account,
                rows,
                estimate,
                config,
                inputs,
                now=now,
                allow_entries=allow_entries,
            )
        if allow_entries and signal.action == "bought":
            entry = execute_fake_entry(store, account, signal, config, inputs, estimate, now)
            if entry:
                account = _load_account_states(store, config, [spec])[key]
                account.open_positions = store.load_open_model_race_positions(config.race_id, key)
        open_pnl = _mark_open_positions(account.open_positions, rows, config)
        exposure = _position_exposure(account.open_positions)
        account.open_pnl = open_pnl
        account.exposure = exposure
        total_equity = account.cash + open_pnl
        store.save_model_race_equity(
            {
                "race_id": config.race_id,
                "model_key": key,
                "cash": account.cash,
                "open_pnl": open_pnl,
                "closed_pnl": account.closed_pnl,
                "total_equity": total_equity,
                "exposure": exposure,
                "payload_json": {
                    "race_mode": config.race_mode,
                    "model_key": key,
                    "model_estimate_f": estimate.get("settlement_high_estimate_f") if estimate else None,
                    "model_top_bracket": _display_bracket_label(_top_probability_row(rows)),
                    "model_top_probability": (_top_probability_row(rows) or {}).get("p_yes"),
                    "best_trade": signal.best_trade,
                    "edge": signal.edge,
                    "action": signal.action,
                    "action_reason": signal.reason,
                    "blocked_reason": signal.reason if signal.action == "blocked" else None,
                    "model_agreement_status": agreement.get("status"),
                    "model_spread_f": agreement.get("spread_f"),
                    "outlier_models": agreement.get("outlier_models"),
                    "global_spread_block_applied": _global_entry_block_reason(agreement, config) is not None,
                    "outlier_block_applied": _outlier_block_enabled(config),
                    "signal": signal.__dict__,
                    "advisor": advisor_result,
                    "estimate": estimate or {},
                },
            }
        )
        for position in account.open_positions:
            open_positions_output.append(_position_output(position, rows))
        scoreboard.append(
            _scoreboard_row(
                spec,
                estimate,
                rows,
                signal,
                account,
                open_pnl,
                total_equity,
                config,
                is_outlier=key in agreement["outlier_models"],
                advisor_result=advisor_result,
            )
        )
    leaderboard = store.load_model_race_leaderboard(config.race_id)
    payload = {
        "generated_at_utc": now.isoformat(),
        "race_id": config.race_id,
        "series": inputs.get("series"),
        "station": inputs.get("station"),
        "market_date": str(inputs.get("market_date")),
        "fake_money_only": True,
        "live_trading_enabled": False,
        "starting_cash_per_model": str(config.starting_cash_per_model),
        "race_mode": config.race_mode,
        "observed_high_so_far_f": inputs.get("observed_high_so_far_f"),
        "latest_observation_utc": inputs.get("latest_observation_utc"),
        "current_production_estimate_f": inputs.get("current_production_estimate_f"),
        "market_favorite": _market_favorite(model_payload.get("probabilities", [])),
        "agreement": agreement,
        "scoreboard": scoreboard,
        "open_positions": open_positions_output,
        "closed_trades_this_update": closed_trades,
        "leaderboard": leaderboard,
        "config": _config_record(config),
        "entries_enabled": allow_entries,
        "entry_blocked_reason": _global_entry_block_reason(agreement, config),
        "global_spread_block_applied": _global_entry_block_reason(agreement, config) is not None,
        "outlier_block_applied": _outlier_block_enabled(config),
    }
    return payload


def run_model_race_exit_monitor(
    store: SQLiteStore,
    model_payload: dict[str, Any],
    config: ModelRaceConfig,
) -> dict[str, Any]:
    payload = run_model_race_once(store, model_payload, config, allow_entries=False)
    payload["mode"] = "exit_monitor"
    return payload


def advisor_dry_run_payload(
    store: SQLiteStore,
    model_payload: dict[str, Any],
    config: ModelRaceConfig,
) -> dict[str, Any]:
    inputs = build_model_race_inputs(model_payload)
    specs = [spec for spec in model_specs() if spec["model_key"] in set(config.include_models)]
    accounts = _load_account_states(store, config, specs)
    estimates = inputs["estimates_by_model"]
    probabilities = inputs["probabilities_by_model"]
    agreement = model_agreement(estimates, config.outlier_threshold_f)
    inputs["agreement"] = agreement
    rows: list[dict[str, Any]] = []
    now = utc_now()
    advisor_config = config if _advisor_enabled(config) else replace(config, advisor_mode="rule_based")
    for spec in specs:
        key = spec["model_key"]
        account = accounts[key]
        account.open_positions = store.load_open_model_race_positions(config.race_id, key)
        signal = evaluate_model_signal(
            key,
            probabilities.get(key, []),
            estimates.get(key),
            account,
            advisor_config,
            inputs,
            is_outlier=key in agreement["outlier_models"],
            store=store,
            now=now,
            allow_entries=True,
        )
        advisor_result: dict[str, Any] | None = None
        if signal.market_ticker is not None:
            signal, advisor_result = _apply_advisor_to_signal(
                store,
                signal,
                account,
                probabilities.get(key, []),
                estimates.get(key),
                advisor_config,
                inputs,
                now=now,
                allow_entries=False,
            )
        rows.append(
            {
                "model": spec["display_name"],
                "model_key": key,
                "signal_action": signal.action,
                "signal_reason": signal.reason,
                "best_trade": signal.best_trade,
                "edge": str(signal.edge) if signal.edge is not None else None,
                "advisor": advisor_result or {},
                "fake_trade_executed": False,
            }
        )
    return {
        "generated_at_utc": now.isoformat(),
        "race_id": config.race_id,
        "series": inputs.get("series"),
        "station": inputs.get("station"),
        "market_date": str(inputs.get("market_date")),
        "advisor_mode": advisor_config.advisor_mode,
        "fake_money_only": True,
        "live_trading_enabled": False,
        "fake_trade_executed": False,
        "agreement": agreement,
        "rows": rows,
    }


def update_open_positions(
    store: SQLiteStore,
    account: ModelRaceAccountState,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    force_flat: bool,
    now: datetime | None = None,
    advisor_results: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    now = now or utc_now()
    closed: list[dict[str, Any]] = []
    by_ticker = {row["market_ticker"]: row for row in probability_rows}
    for position in account.open_positions:
        row = by_ticker.get(position["market_ticker"])
        mark = _bid_for_position(row, position["side"]) if row else None
        current_prob = _current_probability(row, position["side"]) if row else None
        current_edge = _edge_for_position(row, position["side"]) if row else None
        mark_details = _position_mark_details(position, mark, now, config)
        reason = should_exit_model_position(
            position,
            row,
            config,
            inputs,
            force_flat=force_flat or _force_flat_due(config, now),
            now=now,
        )
        force_flat_requested = force_flat or _force_flat_due(config, now)
        review_reason = reason
        if review_reason is None and config.llm_first and _advisor_enabled(config):
            review_reason = "llm first exit review"
        advisor_result: dict[str, Any] | None = None
        if review_reason and _advisor_enabled(config):
            advisor_result = _apply_advisor_to_exit(
                store,
                position,
                account,
                row,
                estimate,
                config,
                inputs,
                review_reason,
                mark,
                current_prob,
                current_edge,
                mark_details,
                force_flat_requested=force_flat_requested,
                now=now,
            )
            advisor_result["exit_safety_override"] = _exit_safety_override(review_reason)
            advisor_result["exit_allowed"] = _advisor_exit_allows_sell(review_reason, advisor_result, config)
            if advisor_results is not None:
                advisor_results[position["model_key"]] = advisor_result
        if review_reason and mark is not None:
            if reason is not None:
                exit_allowed = advisor_result is None or _advisor_exit_allows_sell(review_reason, advisor_result, config)
            else:
                exit_allowed = bool(advisor_result) and _advisor_exit_allows_sell(review_reason, advisor_result, config)
            if exit_allowed:
                closed.append(
                    execute_fake_exit(
                        store,
                        account,
                        position,
                        mark,
                        reason or "llm sell",
                        current_probability=current_prob,
                        current_edge=current_edge,
                        asof_utc=now,
                        config=config,
                        advisor_result=advisor_result,
                    )
                )
            else:
                _save_held_position_after_exit_review(
                    store,
                    position,
                    mark,
                    mark_details,
                    current_prob,
                    current_edge,
                    now,
                    review_reason,
                )
        elif reason and mark is None and force_flat_requested and config.synthetic_zero_exit_on_force_flat:
            closed.append(
                execute_fake_exit(
                    store,
                    account,
                    position,
                    Decimal("0"),
                    "synthetic zero force flat",
                    current_probability=current_prob,
                    current_edge=current_edge,
                    asof_utc=now,
                    config=config,
                    advisor_result=advisor_result,
                )
            )
        else:
            updated = dict(position)
            updated.update(
                {
                    "current_mark_price": mark,
                    "open_pnl": mark_details["open_pnl_for_storage"],
                    "displayed_open_pnl": mark_details["displayed_open_pnl"],
                    "conservative_open_pnl": mark_details["conservative_open_pnl"],
                    "missing_bid_count": mark_details["missing_bid_count"],
                    "last_bid_seen_utc": mark_details["last_bid_seen_utc"],
                    "liquidity_status": mark_details["liquidity_status"],
                    "exit_blocked_reason": reason if reason and mark is None else None,
                    "current_model_probability": current_prob,
                    "current_edge": current_edge,
                    "last_update_utc": now,
                }
            )
            store.save_model_race_position(updated)
    return closed


def evaluate_model_signal(
    key: str,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    account: ModelRaceAccountState,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    is_outlier: bool = False,
    store: SQLiteStore | None = None,
    now: datetime | None = None,
    allow_entries: bool = True,
) -> ModelRaceSignal:
    now = now or utc_now()
    if not estimate or not estimate.get("successful"):
        return ModelRaceSignal(key, None, None, None, None, None, None, None, None, None, None, None, None, "unavailable", "model unavailable")
    if _estimate_stale(estimate, config):
        return ModelRaceSignal(key, None, None, None, None, None, None, None, None, None, None, None, None, "skip", "model estimate stale")
    best = _best_probability_trade(probability_rows, inputs.get("observed_high_so_far_f"))
    if best is None:
        return ModelRaceSignal(key, None, None, None, None, None, None, None, None, None, None, None, None, "skip", "no tradable bracket")
    if not allow_entries:
        return ModelRaceSignal(
            key,
            best["best_trade"],
            best["side"],
            best["market_ticker"],
            best["bracket_label"],
            best.get("bracket_lower_f"),
            best.get("bracket_upper_f"),
            best.get("bracket_type"),
            best["p_yes"],
            1 - best["p_yes"],
            best["ask"],
            best["bid"],
            best["edge"],
            "skip",
            "exit monitor only",
        )
    if account.open_positions and len(account.open_positions) >= config.max_open_positions_per_model:
        reason = "existing position open"
        if config.max_open_positions_per_model > 1:
            reason = "max open positions per model"
        if _has_no_exit_bid_position(account.open_positions):
            reason = "holding / no exit bid"
        elif is_outlier:
            reason = "holding / outlier"
        return ModelRaceSignal(
            key,
            best["best_trade"],
            best["side"],
            best["market_ticker"],
            best["bracket_label"],
            best.get("bracket_lower_f"),
            best.get("bracket_upper_f"),
            best.get("bracket_type"),
            best["p_yes"],
            1 - best["p_yes"],
            best["ask"],
            best["bid"],
            best["edge"],
            "holding",
            reason,
        )
    global_block = _global_entry_block_reason(inputs.get("agreement", {}), config)
    if global_block:
        return ModelRaceSignal(
            key,
            best["best_trade"],
            best["side"],
            best["market_ticker"],
            best["bracket_label"],
            best.get("bracket_lower_f"),
            best.get("bracket_upper_f"),
            best.get("bracket_type"),
            best["p_yes"],
            1 - best["p_yes"],
            best["ask"],
            best["bid"],
            best["edge"],
            "blocked",
            global_block,
        )
    if is_outlier and _outlier_block_enabled(config):
        return ModelRaceSignal(
            key,
            best["best_trade"],
            best["side"],
            best["market_ticker"],
            best["bracket_label"],
            best.get("bracket_lower_f"),
            best.get("bracket_upper_f"),
            best.get("bracket_type"),
            best["p_yes"],
            1 - best["p_yes"],
            best["ask"],
            best["bid"],
            best["edge"],
            "blocked",
            "model estimate outlier",
        )
    allowed, reason = should_enter_model_trade(best, account, config, inputs, store=store, now=now)
    action = "bought" if allowed else ("blocked" if reason != "edge below hurdle" else "wait")
    return ModelRaceSignal(
        key,
        best["best_trade"],
        best["side"],
        best["market_ticker"],
        best["bracket_label"],
        best.get("bracket_lower_f"),
        best.get("bracket_upper_f"),
        best.get("bracket_type"),
        best["p_yes"],
        1 - best["p_yes"],
        best["ask"],
        best["bid"],
        best["edge"],
        action,
        reason,
    )


def should_enter_model_trade(
    trade: dict[str, Any],
    account: ModelRaceAccountState,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    store: SQLiteStore | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    now = now or utc_now()
    global_block = _global_entry_block_reason(inputs.get("agreement", {}), config)
    if global_block:
        return False, global_block
    if trade["edge"] is None or trade["edge"] <= config.base_hurdle:
        return False, "edge below hurdle"
    if trade["ask"] is None:
        return False, "ask missing"
    if config.require_exit_bid_for_entry and trade.get("bid") is None:
        return False, "exit bid missing"
    spread = _trade_spread(trade)
    if spread is not None and spread > _cents_to_price(config.max_spread_cents):
        return False, "spread too wide"
    top_book = _top_book_size_for_trade(trade)
    if top_book is not None and top_book < config.minimum_top_book_size:
        return False, "top-of-book size too small"
    if (
        not config.allow_penny_contract_entries
        and trade["ask"] <= _cents_to_price(config.penny_contract_price_cents)
    ):
        return False, "penny contract blocked"
    if (
        not config.allow_high_price_entries
        and trade["ask"] > _cents_to_price(config.max_entry_price_cents)
        and (trade["edge"] or Decimal("0")) < config.high_price_override_edge
    ):
        return False, "price too high"
    if _bracket_invalidated(trade, inputs.get("observed_high_so_far_f")):
        return False, "bracket invalidated by observed high"
    if config.block_new_entries_if_any_open_position_missing_bid and _has_no_exit_bid_position(account.open_positions):
        return False, "open position missing bid"
    if store is not None and trade.get("market_ticker") and config.cooldown_after_stop_minutes > 0:
        cooldown = store.load_active_model_race_cooldown(
            config.race_id,
            account.model_key,
            trade.get("market_ticker"),
            now,
        )
        if cooldown:
            return False, _cooldown_reason(cooldown, now)
    if account.exposure >= config.max_exposure_per_model:
        return False, "max exposure per model hit"
    if (
        config.max_daily_fake_loss_per_model is not None
        and account.closed_pnl <= -config.max_daily_fake_loss_per_model
    ):
        return False, "daily fake loss limit hit"
    qty = size_model_trade(
        trade["ask"],
        account,
        config,
        trade["market_ticker"],
        model_spread_f=_model_spread_f(inputs),
    )
    if qty < 1:
        return False, "position size below one contract"
    return True, "edge clears hurdle"


def _advisor_enabled(config: ModelRaceConfig) -> bool:
    return config.use_llm_advisor or config.advisor_mode.strip().lower() != ADVISOR_MODE_OFF


def _should_apply_entry_advisor(config: ModelRaceConfig, signal: ModelRaceSignal, *, allow_entries: bool) -> bool:
    if not _advisor_enabled(config) or signal.market_ticker is None:
        return False
    if signal.action == "bought":
        return True
    return bool(config.llm_first and allow_entries and signal.action in {"wait", "blocked"})


def _apply_advisor_to_signal(
    store: SQLiteStore,
    signal: ModelRaceSignal,
    account: ModelRaceAccountState,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    now: datetime,
    allow_entries: bool,
    advisor_input: AdvisorInput | None = None,
) -> tuple[ModelRaceSignal, dict[str, Any]]:
    if config.use_llm_advisor:
        return _apply_llm_advisor_to_signal(
            store,
            signal,
            account,
            probability_rows,
            estimate,
            config,
            inputs,
            now=now,
            allow_entries=allow_entries,
            advisor_input=advisor_input,
        )
    advisor = advisor_for_mode(
        config.advisor_mode,
        prompt_path=config.advisor_prompt_path,
        log_dir=config.advisor_log_dir,
        provider_config=config.advisor_provider_config,
    )
    if advisor is None:
        return signal, {}
    if advisor_input is None:
        advisor_input = _advisor_input_for_signal(
            store,
            signal,
            account,
            probability_rows,
            estimate,
            config,
            inputs,
            now=now,
        )
    decision = safe_decide(advisor, advisor_input)
    validated = validate_advisor_trade(advisor_input, decision)
    final_signal = signal
    final_status = "approved" if validated.approved else "veto"
    final_reason = validated.final_reason
    if config.advisor_required:
        if (
            validated.approved
            and validated.final_action in {"BUY_YES", "BUY_NO"}
            and (signal.action == "bought" or config.llm_first)
            and allow_entries
        ):
            final_signal = _signal_with_action(
                signal,
                "bought",
                f"advisor approved score {decision.trade_quality_score}",
            )
        elif validated.final_action == "WAIT" or decision.decision == "WAIT":
            final_signal = _signal_with_action(signal, "wait", decision.primary_reason)
        elif signal.action == "holding":
            final_signal = _signal_with_action(signal, "holding", signal.reason)
        else:
            reason = validated.final_reason if not validated.approved else decision.primary_reason
            final_signal = _signal_with_action(signal, "blocked", reason)
    elif not validated.approved and validated.veto_reasons:
        final_signal = _signal_with_action(signal, "blocked", validated.final_reason)
    record = {
        "race_id": config.race_id,
        "advisor_mode": config.advisor_mode,
        "provider": getattr(advisor, "provider_name", config.advisor_mode),
        "model_key": signal.model_key,
        "market_ticker": signal.market_ticker,
        "bracket_label": signal.bracket_label,
        "side": decision.side,
        "strategy_mode": advisor_input.strategy_mode,
        "trade_quality_score": decision.trade_quality_score,
        "advisor_decision": decision.decision,
        "validator_approved": validated.approved,
        "final_action": validated.final_action,
        "primary_reason": decision.primary_reason,
        "risk_flags": decision.risk_flags,
        "hard_veto_flags": decision.hard_veto_flags,
        "veto_reasons": validated.veto_reasons,
        "input": advisor_input.to_dict(),
        "output": decision.to_dict(),
        "final": validated.to_dict(),
    }
    decision_id = store.save_advisor_decision(record)
    return final_signal, {
        "advisor_decision_id": decision_id,
        "advisor_mode": config.advisor_mode,
        "provider": record["provider"],
        "trade_quality_score": decision.trade_quality_score,
        "advisor_decision": decision.decision,
        "validator_status": final_status,
        "validator_approved": validated.approved,
        "final_action": validated.final_action,
        "primary_reason": decision.primary_reason,
        "final_reason": final_reason,
        "veto_reasons": validated.veto_reasons,
        "risk_flags": decision.risk_flags,
        "hard_veto_flags": decision.hard_veto_flags,
        "input": advisor_input.to_dict(),
        "output": decision.to_dict(),
        "final": validated.to_dict(),
    }


def _apply_llm_advisor_to_signal(
    store: SQLiteStore,
    signal: ModelRaceSignal,
    account: ModelRaceAccountState,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    now: datetime,
    allow_entries: bool,
    advisor_input: AdvisorInput | None = None,
) -> tuple[ModelRaceSignal, dict[str, Any]]:
    if advisor_input is None:
        advisor_input = _advisor_input_for_signal(
            store,
            signal,
            account,
            probability_rows,
            estimate,
            config,
            inputs,
            now=now,
        )
    trade_snapshot = advisor_input_to_trade_snapshot(advisor_input)
    trade_quality = deterministic_trade_quality(advisor_input)
    raw_response: dict[str, Any] | None = None
    provider_name = "rule_only" if config.llm_rule_only else config.llm_provider
    if config.llm_rule_only:
        advisor = advisor_for_mode("rule_based")
        decision = safe_decide(advisor, advisor_input) if advisor is not None else None
    elif config.llm_provider.strip().lower() != "ollama":
        decision = safe_fallback_decision(
            f"unsupported LLM provider: {config.llm_provider}",
            fallback_input=advisor_input,
            fallback_action=config.llm_fallback_action,
            hard_veto_flag="llm_provider_unavailable",
        )
        raw_response = {
            "provider": config.llm_provider,
            "model": config.llm_model,
            "success": False,
            "error": f"unsupported LLM provider: {config.llm_provider}",
        }
    else:
        provider = _llm_provider_from_config(config)
        decision, raw = provider.advise_trade_with_response(trade_snapshot)
        raw_response = raw.to_dict()
    if decision is None:
        advisor = advisor_for_mode("rule_based")
        decision = safe_decide(advisor, advisor_input) if advisor is not None else None
    if decision is None:
        return signal, {}
    validated = validate_advisor_trade(advisor_input, decision)
    final_signal = signal if config.llm_dry_run else _signal_from_advisor_validation(
        signal,
        decision,
        validated,
        allow_entries=allow_entries,
        llm_first=config.llm_first,
    )
    final_status = "approved" if validated.approved else "veto"
    hard_result = hard_validator_result(validated)
    decision_log_path = write_llm_decision_log(
        config.llm_decision_log,
        {
            "timestamp": now.astimezone(timezone.utc).isoformat(),
            "race_id": config.race_id,
            "model_key": signal.model_key,
            "market_ticker": signal.market_ticker,
            "bracket_label": signal.bracket_label,
            "side": decision.side,
            "trade_snapshot": trade_snapshot,
            "trade_quality_score": trade_quality.get("score"),
            "trade_quality": trade_quality,
            "llm_raw_response": raw_response,
            "llm_parsed_decision": decision.to_dict(),
            "hard_validator_result": hard_result,
            "final_action": validated.final_action,
            "fake_trade_id": None,
            "dry_run": config.llm_dry_run,
            "error": (raw_response or {}).get("error") if raw_response else None,
        },
    )
    record = {
        "race_id": config.race_id,
        "advisor_mode": "llm_rule_only" if config.llm_rule_only else "llm_ollama",
        "provider": provider_name,
        "model_key": signal.model_key,
        "market_ticker": signal.market_ticker,
        "bracket_label": signal.bracket_label,
        "side": decision.side,
        "strategy_mode": advisor_input.strategy_mode,
        "trade_quality_score": decision.trade_quality_score,
        "advisor_decision": decision.decision,
        "validator_approved": validated.approved,
        "final_action": validated.final_action,
        "primary_reason": decision.primary_reason,
        "risk_flags": decision.risk_flags,
        "hard_veto_flags": decision.hard_veto_flags,
        "veto_reasons": validated.veto_reasons,
        "input": advisor_input.to_dict(),
        "output": decision.to_dict(),
        "final": validated.to_dict(),
    }
    decision_id = store.save_advisor_decision(record)
    result = {
        "advisor_decision_id": decision_id,
        "advisor_mode": record["advisor_mode"],
        "provider": provider_name,
        "llm_model": config.llm_model,
        "trade_quality_score": decision.trade_quality_score,
        "deterministic_trade_quality_score": trade_quality.get("score"),
        "advisor_decision": decision.decision,
        "validator_status": final_status,
        "validator_approved": validated.approved,
        "final_action": validated.final_action,
        "primary_reason": decision.primary_reason,
        "final_reason": validated.final_reason,
        "veto_reasons": validated.veto_reasons,
        "risk_flags": decision.risk_flags,
        "hard_veto_flags": decision.hard_veto_flags,
        "decision_log_path": str(decision_log_path),
        "dry_run": config.llm_dry_run,
        "input": advisor_input.to_dict(),
        "trade_snapshot": trade_snapshot,
        "output": decision.to_dict(),
        "final": validated.to_dict(),
    }
    if config.llm_show_raw_response:
        result["llm_raw_response"] = raw_response
    if config.llm_show_prompt:
        result["llm_system_prompt"] = load_system_prompt(config.advisor_prompt_path)
    return final_signal, result


def _llm_provider_from_config(config: ModelRaceConfig) -> OllamaLLMProvider:
    return OllamaLLMProvider(
        host=config.llm_host,
        model=config.llm_model,
        timeout_seconds=config.llm_timeout_seconds,
        max_retries=config.llm_max_retries,
        temperature=config.llm_temperature,
        fallback_action=config.llm_fallback_action,
    )


def _apply_advisor_to_exit(
    store: SQLiteStore,
    position: dict[str, Any],
    account: ModelRaceAccountState,
    probability_row: dict[str, Any] | None,
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    exit_reason: str,
    mark: Decimal | None,
    current_probability: float | None,
    current_edge: Decimal | None,
    mark_details: dict[str, Any],
    *,
    force_flat_requested: bool,
    now: datetime,
) -> dict[str, Any]:
    signal = _exit_signal_for_position(position, probability_row, exit_reason, mark, current_edge)
    exit_account = replace(
        account,
        open_positions=[position],
        open_pnl=mark_details["open_pnl_for_storage"],
        exposure=_dec(position["quantity"]) * _dec(position["entry_price"]),
    )
    advisor_input = _advisor_input_for_exit(
        store,
        signal,
        position,
        exit_account,
        [probability_row] if probability_row else [],
        estimate,
        config,
        inputs,
        exit_reason,
        mark,
        current_probability,
        current_edge,
        mark_details,
        force_flat_requested=force_flat_requested,
        now=now,
    )
    _, result = _apply_advisor_to_signal(
        store,
        signal,
        exit_account,
        [probability_row] if probability_row else [],
        estimate,
        config,
        inputs,
        now=now,
        allow_entries=False,
        advisor_input=advisor_input,
    )
    if result:
        result["deterministic_exit_reason"] = exit_reason
        result["exit_reviewed"] = True
    return result


def _exit_signal_for_position(
    position: dict[str, Any],
    probability_row: dict[str, Any] | None,
    exit_reason: str,
    mark: Decimal | None,
    current_edge: Decimal | None,
) -> ModelRaceSignal:
    side = str(position.get("side") or "").lower()
    p_yes = float(probability_row.get("p_yes")) if probability_row and probability_row.get("p_yes") is not None else None
    p_no = 1 - p_yes if p_yes is not None else None
    ask = _ask_for_position(probability_row, side)
    return ModelRaceSignal(
        str(position["model_key"]),
        f"{_display_bracket_label(position)} {side.upper()} exit",
        side,
        position.get("market_ticker"),
        position.get("bracket_label"),
        position.get("bracket_lower_f"),
        position.get("bracket_upper_f"),
        position.get("bracket_type"),
        p_yes,
        p_no,
        ask,
        mark,
        current_edge,
        "sell",
        exit_reason,
    )


def _advisor_input_for_exit(
    store: SQLiteStore,
    signal: ModelRaceSignal,
    position: dict[str, Any],
    account: ModelRaceAccountState,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    exit_reason: str,
    mark: Decimal | None,
    current_probability: float | None,
    current_edge: Decimal | None,
    mark_details: dict[str, Any],
    *,
    force_flat_requested: bool,
    now: datetime,
) -> AdvisorInput:
    advisor_input = _advisor_input_for_signal(
        store,
        signal,
        account,
        probability_rows,
        estimate,
        config,
        inputs,
        now=now,
    )
    entry_asof = _parse_dt(position.get("entry_asof_utc"))
    hold_minutes = (now - entry_asof).total_seconds() / 60 if entry_asof else None
    side = "YES" if position.get("side") == "yes" else "NO" if position.get("side") == "no" else "NONE"
    candidate = dict(advisor_input.candidate_trade)
    candidate.update(
        {
            "side": side,
            "entry_price_paid": str(position.get("entry_price")),
            "exit_bid": str(mark) if mark is not None else None,
            "current_probability": current_probability,
            "current_edge": str(current_edge) if current_edge is not None else None,
            "legacy_signal_action": "sell",
            "legacy_signal_reason": exit_reason,
        }
    )
    position_state = dict(advisor_input.position_state)
    position_state.update(
        {
            "has_open_position": True,
            "side": side,
            "quantity": str(position.get("quantity")),
            "entry_price": str(position.get("entry_price")),
            "current_exit_bid": str(mark) if mark is not None else None,
            "current_probability": current_probability,
            "entry_model_probability": position.get("entry_model_probability"),
            "current_edge": str(current_edge) if current_edge is not None else None,
            "open_pnl": str(mark_details["open_pnl_for_storage"]),
            "displayed_open_pnl": (
                str(mark_details["displayed_open_pnl"])
                if mark_details.get("displayed_open_pnl") is not None
                else None
            ),
            "hold_minutes": hold_minutes,
            "exit_reason": exit_reason,
            "stop_loss_triggered": exit_reason == "stop loss",
            "profit_target_triggered": exit_reason == "profit target",
            "edge_disappeared_triggered": exit_reason == "edge disappeared",
            "probability_drop_triggered": exit_reason == "probability drop",
            "max_hold_triggered": exit_reason == "max hold",
            "force_flat_active": force_flat_requested,
            "weather_invalidated": exit_reason == "weather invalidates bracket",
        }
    )
    risk_state = dict(advisor_input.risk_state)
    risk_state["force_flat_active"] = force_flat_requested
    return replace(
        advisor_input,
        candidate_trade=candidate,
        position_state=position_state,
        risk_state=risk_state,
    )


def _advisor_exit_allows_sell(
    exit_reason: str,
    advisor_result: dict[str, Any] | None,
    config: ModelRaceConfig,
) -> bool:
    if config.llm_dry_run:
        return True
    if not advisor_result:
        return True
    if advisor_result.get("validator_approved") and advisor_result.get("final_action") == "SELL":
        return True
    return _exit_safety_override(exit_reason)


def _exit_safety_override(exit_reason: str) -> bool:
    return exit_reason in {"force flat", "synthetic zero force flat", "stop loss", "weather invalidates bracket"}


def _save_held_position_after_exit_review(
    store: SQLiteStore,
    position: dict[str, Any],
    mark: Decimal | None,
    mark_details: dict[str, Any],
    current_probability: float | None,
    current_edge: Decimal | None,
    now: datetime,
    exit_reason: str,
) -> None:
    updated = dict(position)
    updated.update(
        {
            "current_mark_price": mark,
            "open_pnl": mark_details["open_pnl_for_storage"],
            "displayed_open_pnl": mark_details["displayed_open_pnl"],
            "conservative_open_pnl": mark_details["conservative_open_pnl"],
            "missing_bid_count": mark_details["missing_bid_count"],
            "last_bid_seen_utc": mark_details["last_bid_seen_utc"],
            "liquidity_status": mark_details["liquidity_status"],
            "exit_blocked_reason": f"advisor held exit: {exit_reason}",
            "current_model_probability": current_probability,
            "current_edge": current_edge,
            "last_update_utc": now,
        }
    )
    store.save_model_race_position(updated)


def _signal_from_advisor_validation(
    signal: ModelRaceSignal,
    decision: Any,
    validated: Any,
    *,
    allow_entries: bool,
    llm_first: bool = False,
) -> ModelRaceSignal:
    if (
        validated.approved
        and validated.final_action in {"BUY_YES", "BUY_NO"}
        and (signal.action == "bought" or llm_first)
        and allow_entries
    ):
        return _signal_with_action(
            signal,
            "bought",
            f"advisor approved score {decision.trade_quality_score}",
        )
    if signal.action == "holding":
        return _signal_with_action(signal, "holding", signal.reason)
    if not validated.approved:
        return _signal_with_action(signal, "blocked", validated.final_reason)
    if validated.final_action == "WAIT" or decision.decision == "WAIT":
        return _signal_with_action(signal, "wait", decision.primary_reason)
    reason = validated.final_reason if not validated.approved else decision.primary_reason
    return _signal_with_action(signal, "blocked", reason)


def _advisor_input_for_signal(
    store: SQLiteStore,
    signal: ModelRaceSignal,
    account: ModelRaceAccountState,
    probability_rows: list[dict[str, Any]],
    estimate: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    now: datetime,
) -> AdvisorInput:
    row = {item["market_ticker"]: item for item in probability_rows}.get(signal.market_ticker or "")
    side = "YES" if signal.side == "yes" else "NO" if signal.side == "no" else "NONE"
    prior_seen = store.advisor_signal_seen_count(config.race_id, signal.model_key, signal.market_ticker, side)
    cooldown = (
        store.load_active_model_race_cooldown(config.race_id, signal.model_key, signal.market_ticker, now)
        if signal.market_ticker
        else None
    )
    ask = signal.ask
    bid = signal.bid
    spread = ask - bid if ask is not None and bid is not None else None
    top = _top_probability_row(probability_rows)
    open_position = account.open_positions[0] if account.open_positions else None
    current_exit_bid = _bid_for_position(row, open_position["side"]) if row and open_position else None
    return AdvisorInput(
        decision_time_utc=now.astimezone(timezone.utc).isoformat(),
        decision_time_local=now.astimezone(PACIFIC).isoformat(),
        series=str(inputs.get("series") or ""),
        station=str(inputs.get("station") or ""),
        target_date=str(inputs.get("market_date") or ""),
        strategy_mode="microtrade",
        race_mode=config.race_mode,
        current_weather={
            "observed_high_so_far_f": inputs.get("observed_high_so_far_f"),
            "latest_observation_utc": inputs.get("latest_observation_utc"),
        },
        model={
            "model_key": signal.model_key,
            "provider": signal.model_key.split(":", 1)[0] if ":" in signal.model_key else "",
            "estimate_high_f": estimate.get("settlement_high_estimate_f") if estimate else None,
            "top_bracket": _display_bracket_label(top),
            "top_probability": top.get("p_yes") if top else None,
            "model_data_age_seconds": _age_seconds(estimate.get("asof_utc") if estimate else None, now),
            "recent_stop_loss_minutes_ago": _recent_stop_minutes(cooldown, now),
        },
        candidate_trade={
            "market_ticker": signal.market_ticker,
            "bracket_label": signal.bracket_label,
            "bracket_lower_f": signal.bracket_lower_f,
            "bracket_upper_f": signal.bracket_upper_f,
            "bracket_type": signal.bracket_type,
            "side": side,
            "model_probability": signal.p_yes if side == "YES" else signal.p_no,
            "calibrated_probability": signal.p_yes if side == "YES" else signal.p_no,
            "entry_ask": str(ask) if ask is not None else None,
            "exit_bid": str(bid) if bid is not None else None,
            "edge": str(signal.edge) if signal.edge is not None else None,
            "fee_adjusted_edge": str(signal.edge) if signal.edge is not None else None,
            "spread": str(spread) if spread is not None else None,
            "signal_seen_count": prior_seen + 1,
            "market_confirmation": "neutral",
            "liquidity_ok": bid is not None and ask is not None,
            "bracket_invalidated": _bracket_invalidated(signal.__dict__, inputs.get("observed_high_so_far_f")),
            "legacy_signal_action": signal.action,
            "legacy_signal_reason": signal.reason,
        },
        position_state={
            "has_open_position": bool(account.open_positions),
            "current_exit_bid": str(current_exit_bid) if current_exit_bid is not None else None,
            "open_position_count": len(account.open_positions),
            "open_pnl": str(account.open_pnl),
            "stop_loss_triggered": False,
            "profit_target_triggered": False,
            "probability_drop_triggered": False,
            "max_hold_triggered": False,
            "force_flat_active": _force_flat_due(config, now),
        },
        risk_state={
            "cooldown_active": cooldown is not None,
            "daily_loss_limit_hit": (
                config.max_daily_fake_loss_per_model is not None
                and account.closed_pnl <= -config.max_daily_fake_loss_per_model
            ),
            "max_positions_hit": len(account.open_positions) >= config.max_open_positions_per_model,
            "max_exposure_hit": account.exposure >= config.max_exposure_per_model,
            "open_position_missing_bid": _has_no_exit_bid_position(account.open_positions),
            "live_trading_enabled": False,
        },
        market_context={
            "agreement_status": (inputs.get("agreement") or {}).get("status"),
            "model_spread_f": (inputs.get("agreement") or {}).get("spread_f"),
            "market_data_age_seconds": _age_seconds(inputs.get("generated_at_utc"), now),
        },
        recent_history={
            "prior_signal_seen_count": prior_seen,
            "cooldown": cooldown or {},
        },
        configuration={
            "advisor_min_score": config.advisor_min_score,
            "min_score_for_buy": config.advisor_min_score,
            "min_score_for_small_trade": config.confirmed_edge_min_score_for_small_trade,
            "required_signal_seen_count": config.confirmed_edge_required_signal_seen_count,
            "require_exit_bid_for_entry": config.require_exit_bid_for_entry,
            "max_spread_cents": str(config.max_spread_cents),
            "minimum_top_book_size": str(config.minimum_top_book_size),
            "allow_penny_contract_entries": config.allow_penny_contract_entries,
            "max_entry_price_cents": str(config.max_entry_price_cents),
            "high_price_override_edge": str(config.high_price_override_edge),
            "strategy_mode": "microtrade",
            "llm_first": config.llm_first,
            "live_trading_enabled": False,
        },
    )


def _signal_with_action(signal: ModelRaceSignal, action: str, reason: str) -> ModelRaceSignal:
    return ModelRaceSignal(
        signal.model_key,
        signal.best_trade,
        signal.side,
        signal.market_ticker,
        signal.bracket_label,
        signal.bracket_lower_f,
        signal.bracket_upper_f,
        signal.bracket_type,
        signal.p_yes,
        signal.p_no,
        signal.ask,
        signal.bid,
        signal.edge,
        action,
        reason,
    )


def _age_seconds(value: Any, now: datetime) -> float | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _recent_stop_minutes(cooldown: dict[str, Any] | None, now: datetime) -> float | None:
    if not cooldown:
        return None
    created = _parse_dt(cooldown.get("created_utc"))
    if created is None:
        return 0.0
    return max(0.0, (now - created).total_seconds() / 60)


def size_model_trade(
    entry_price: Decimal,
    account: ModelRaceAccountState,
    config: ModelRaceConfig,
    market_ticker: str | None = None,
    *,
    model_spread_f: float | None = None,
) -> int:
    if entry_price <= 0:
        return 0
    max_risk = config.max_risk_per_trade
    if (
        config.race_mode == RACE_MODE_CONSENSUS_GUARDED
        and model_spread_f is not None
        and model_spread_f > config.reduce_size_if_spread_gt_f
    ):
        max_risk *= config.reduced_size_multiplier
    max_contracts = int(math.floor(max_risk / entry_price))
    cash_contracts = int(math.floor(account.cash / entry_price))
    remaining_model_exposure = max(Decimal("0"), config.max_exposure_per_model - account.exposure)
    exposure_contracts = int(math.floor(remaining_model_exposure / entry_price))
    bracket_exposure = Decimal("0")
    for pos in account.open_positions:
        if market_ticker is None or pos["market_ticker"] == market_ticker:
            bracket_exposure += _dec(pos["quantity"]) * _dec(pos["entry_price"])
    remaining_bracket = max(Decimal("0"), config.max_exposure_per_bracket - bracket_exposure)
    bracket_contracts = int(math.floor(remaining_bracket / entry_price))
    return max(0, min(max_contracts, cash_contracts, exposure_contracts, bracket_contracts))


def should_exit_model_position(
    position: dict[str, Any],
    probability_row: dict[str, Any] | None,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    *,
    force_flat: bool = False,
    now: datetime | None = None,
) -> str | None:
    now = now or utc_now()
    mark = _bid_for_position(probability_row, position["side"]) if probability_row else None
    current_edge = _edge_for_position(probability_row, position["side"]) if probability_row else None
    current_prob = _current_probability(probability_row, position["side"]) if probability_row else None
    entry_price = _dec(position["entry_price"])
    if force_flat:
        return "force flat"
    if _position_invalidated(position, inputs.get("observed_high_so_far_f")):
        return "weather invalidates bracket"
    if mark is not None and mark >= entry_price + config.profit_target_price_delta:
        return "profit target"
    if mark is not None and mark <= entry_price - config.stop_loss_price_delta:
        return "stop loss"
    if current_edge is not None and current_edge <= Decimal("0"):
        return "edge disappeared"
    if current_prob is not None and position.get("entry_model_probability") is not None:
        if current_prob <= float(position["entry_model_probability"]) - config.probability_drop_exit:
            return "probability drop"
    max_hold = _parse_dt(position.get("max_hold_until_utc"))
    if max_hold is not None and now >= max_hold:
        return "max hold"
    return None


def execute_fake_entry(
    store: SQLiteStore,
    account: ModelRaceAccountState,
    signal: ModelRaceSignal,
    config: ModelRaceConfig,
    inputs: dict[str, Any],
    estimate: dict[str, Any] | None,
    asof_utc: datetime | None = None,
) -> dict[str, Any] | None:
    if signal.ask is None or signal.market_ticker is None or signal.side is None:
        return None
    qty = size_model_trade(
        signal.ask,
        account,
        config,
        signal.market_ticker,
        model_spread_f=_model_spread_f(inputs),
    )
    if qty < 1:
        return None
    asof = asof_utc or utc_now()
    gross = Decimal(qty) * signal.ask
    fee = _fee(qty, config)
    cash_after = account.cash - gross - fee
    if cash_after < 0:
        return None
    provider, model_id = signal.model_key.split(":", 1)
    store.save_model_race_account(
        {
            "race_id": config.race_id,
            "model_key": signal.model_key,
            "provider": provider,
            "model_id": model_id,
            "starting_cash": account.starting_cash,
            "current_cash": cash_after,
            "realized_pnl": account.closed_pnl,
            "status": "active",
            "payload_json": {"reason": signal.reason},
        }
    )
    position = {
        "race_id": config.race_id,
        "model_key": signal.model_key,
        "market_ticker": signal.market_ticker,
        "event_ticker": _event_ticker(signal.market_ticker),
        "station": inputs.get("station"),
        "market_date": inputs.get("market_date"),
        "bracket_label": signal.bracket_label,
        "bracket_lower_f": signal.bracket_lower_f,
        "bracket_upper_f": signal.bracket_upper_f,
        "bracket_type": signal.bracket_type,
        "side": signal.side,
        "quantity": qty,
        "entry_price": signal.ask,
        "current_mark_price": signal.bid,
        "open_pnl": Decimal("0"),
        "entry_model_probability": signal.p_yes if signal.side == "yes" else signal.p_no,
        "current_model_probability": signal.p_yes if signal.side == "yes" else signal.p_no,
        "entry_edge": signal.edge,
        "current_edge": signal.edge,
        "entry_asof_utc": asof,
        "last_update_utc": asof,
        "max_hold_until_utc": asof + timedelta(minutes=config.max_hold_minutes),
        "missing_bid_count": 0 if signal.bid is not None else 1,
        "last_bid_seen_utc": asof if signal.bid is not None else None,
        "liquidity_status": "ok" if signal.bid is not None else "no_exit_bid",
        "displayed_open_pnl": Decimal("0") if signal.bid is not None else None,
        "conservative_open_pnl": Decimal("0") if signal.bid is not None else -gross,
        "exit_blocked_reason": None,
        "status": "open",
        "payload_json": {"estimate": estimate or {}, "fee_mode": config.fee_mode, "race_mode": config.race_mode},
    }
    position_id = store.save_model_race_position(position)
    fill = {
        "race_id": config.race_id,
        "model_key": signal.model_key,
        "action": "buy",
        "market_ticker": signal.market_ticker,
        "side": signal.side,
        "quantity": qty,
        "price": signal.ask,
        "gross_value": gross,
        "fee": fee,
        "realized_pnl": Decimal("0"),
        "reason": signal.reason,
        "asof_utc": asof,
        "payload_json": {"position_id": position_id, "entry_edge": signal.edge, "race_mode": config.race_mode},
    }
    store.save_model_race_fill(fill)
    return fill


def execute_fake_exit(
    store: SQLiteStore,
    account: ModelRaceAccountState,
    position: dict[str, Any],
    price: Decimal,
    reason: str,
    *,
    current_probability: float | None,
    current_edge: Decimal | None,
    asof_utc: datetime | None = None,
    config: ModelRaceConfig | None = None,
    advisor_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qty = _dec(position["quantity"])
    entry = _dec(position["entry_price"])
    gross = qty * price
    fee = Decimal("0")
    realized = (price - entry) * qty - fee
    cash_after = account.cash + gross - fee
    closed_after = account.closed_pnl + realized
    provider, model_id = position["model_key"].split(":", 1)
    asof = asof_utc or utc_now()
    store.close_model_race_position(
        int(position["id"]),
        mark_price=price,
        open_pnl=realized,
        current_probability=current_probability,
        current_edge=current_edge,
        payload={"exit_reason": reason, "exit_asof_utc": asof.isoformat(), "advisor": advisor_result or {}},
    )
    store.save_model_race_account(
        {
            "race_id": position["race_id"],
            "model_key": position["model_key"],
            "provider": provider,
            "model_id": model_id,
            "starting_cash": account.starting_cash,
            "current_cash": cash_after,
            "realized_pnl": closed_after,
            "status": "active",
            "payload_json": {"exit_reason": reason},
        }
    )
    fill = {
        "race_id": position["race_id"],
        "model_key": position["model_key"],
        "action": "sell",
        "market_ticker": position["market_ticker"],
        "side": position["side"],
        "quantity": qty,
        "price": price,
        "gross_value": gross,
        "fee": fee,
        "realized_pnl": realized,
        "reason": reason,
        "asof_utc": asof,
        "payload_json": {"position_id": position["id"], "entry_price": entry, "advisor": advisor_result or {}},
    }
    store.save_model_race_fill(fill)
    if reason == "stop loss" and config is not None and config.cooldown_after_stop_minutes > 0:
        store.save_model_race_cooldown(
            position["race_id"],
            position["model_key"],
            position.get("market_ticker") if config.cooldown_scope == "market_ticker" else None,
            reason,
            asof + timedelta(minutes=config.cooldown_after_stop_minutes),
            {"position_id": position.get("id"), "exit_price": str(price), "entry_price": str(entry)},
        )
    return _closed_trade_output(position, fill)


def force_flat_model_race(store: SQLiteStore, race_id: str, model_payload: dict[str, Any], config: ModelRaceConfig) -> list[dict[str, Any]]:
    inputs = build_model_race_inputs(model_payload)
    inputs["agreement"] = model_agreement(inputs["estimates_by_model"], config.outlier_threshold_f)
    accounts = _load_account_states(store, config, model_specs())
    closed: list[dict[str, Any]] = []
    for key, rows in inputs["probabilities_by_model"].items():
        account = accounts.get(key)
        if not account:
            continue
        account.open_positions = store.load_open_model_race_positions(race_id, key)
        closed.extend(
            update_open_positions(
                store,
                account,
                rows,
                inputs["estimates_by_model"].get(key),
                config,
                inputs,
                force_flat=True,
            )
        )
    return closed


def flatten_model_race(
    store: SQLiteStore,
    race_id: str,
    model_payload: dict[str, Any],
    config: ModelRaceConfig,
    *,
    synthetic_zero_exit: bool = False,
) -> dict[str, Any]:
    closed = force_flat_model_race(store, race_id, model_payload, config)
    blocked = [
        _position_output(position, model_payload.get("probabilities", []))
        for position in store.load_open_model_race_positions(race_id)
        if position.get("exit_blocked_reason") or position.get("liquidity_status") in {"no_exit_bid", "exit_blocked_no_bid", "illiquid_warning"}
    ]
    synthetic_closed: list[dict[str, Any]] = []
    if synthetic_zero_exit and blocked:
        specs = model_specs()
        accounts = _load_account_states(store, config, specs)
        for position in store.load_open_model_race_positions(race_id):
            if position.get("id") not in {item.get("id") for item in blocked}:
                continue
            account = accounts.get(position["model_key"])
            if not account:
                continue
            synthetic_closed.append(
                execute_fake_exit(
                    store,
                    account,
                    position,
                    Decimal("0"),
                    "synthetic zero flatten",
                    current_probability=None,
                    current_edge=None,
                    config=config,
                )
            )
        blocked = []
    remaining = store.load_open_model_race_positions(race_id)
    realized = sum((_dec(trade.get("realized_pnl")) for trade in closed + synthetic_closed), Decimal("0"))
    return {
        "generated_at_utc": utc_now().isoformat(),
        "race_id": race_id,
        "fake_money_only": True,
        "live_trading_enabled": False,
        "positions_closed": len(closed) + len(synthetic_closed),
        "positions_blocked_no_bid": len(blocked),
        "remaining_open_positions": len(remaining),
        "realized_pnl": str(realized),
        "closed": closed + synthetic_closed,
        "blocked": blocked,
        "synthetic_zero_exit": synthetic_zero_exit,
    }


def model_agreement(estimates: dict[str, dict[str, Any]], outlier_threshold_f: float = 3.0) -> dict[str, Any]:
    successful = {
        key: float(row["settlement_high_estimate_f"])
        for key, row in estimates.items()
        if row.get("successful") and row.get("settlement_high_estimate_f") is not None
    }
    if not successful:
        return {"status": "UNKNOWN", "spread_f": None, "outlier_models": [], "median_f": None}
    values = list(successful.values())
    spread = max(values) - min(values)
    median = statistics.median(values)
    status = "HIGH" if spread <= 1.0 else "MEDIUM" if spread <= 2.0 else "LOW"
    outliers = [key for key, value in successful.items() if abs(value - median) > outlier_threshold_f]
    return {
        "status": status,
        "spread_f": spread,
        "min_f": min(values),
        "max_f": max(values),
        "median_f": median,
        "outlier_models": outliers,
    }


def compact_model_race_text(payload: dict[str, Any]) -> str:
    generated = _parse_dt(payload.get("generated_at_utc")) or utc_now()
    local = generated.astimezone(PACIFIC)
    advisor_enabled = any(row.get("advisor_mode") for row in payload.get("scoreboard", []))
    header = "Model         Est    Top    P(top) Best trade Edge  Action        Cash    Open    Closed"
    width = 86
    if advisor_enabled:
        header += "   LLM      Score Risk   Final  Why"
        width = 128
    lines = [
        f"KALSHI WEATHER MODEL RACE - {payload.get('station')} - FAKE MONEY ONLY",
        (
            f"{local.strftime('%H:%M')} PT | Current estimate: {_fmt_f(payload.get('current_production_estimate_f'))} | "
            f"High so far: {_fmt_f(payload.get('observed_high_so_far_f'))} | "
            f"Market favorite: {payload.get('market_favorite') or '--'} | Data: SMOKE TEST"
        ),
        "",
        header,
        "-" * width,
    ]
    action_notes: list[str] = []
    for row in payload.get("scoreboard", []):
        action = str(row.get("action") or "--")
        action_display = _clip(action, 13)
        if action_display != action:
            action_notes.append(f"- {row.get('model_key') or row.get('model')}: {action}")
        line = (
            f"{_clip(str(row.get('model') or '--'), 13):<13} {_fmt_f_short(row.get('est_high_f')):<6} "
            f"{_clip(str(row.get('top_bracket') or '--'), 6):<6} {_clip(str(row.get('p_top') or '--'), 6):<6} "
            f"{_clip(str(row.get('best_trade') or '--'), 10):<10} {_clip(str(row.get('edge') or '--'), 5):<5} "
            f"{action_display:<13} {_fmt_money(row.get('cash')):<7} "
            f"{_fmt_money(row.get('open_pnl_display', row.get('open_pnl'))):<7} {_fmt_money(row.get('closed_pnl'))}"
        )
        if advisor_enabled:
            line += _advisor_wide_columns(row)
        lines.append(line)
    agreement = payload.get("agreement", {})
    warning = "none"
    if agreement.get("outlier_models"):
        warning = "outlier: " + ", ".join(agreement["outlier_models"])
    entry_block = payload.get("entry_blocked_reason")
    race_mode = str(payload.get("race_mode") or RACE_MODE_INDEPENDENT)
    if race_mode == RACE_MODE_CONSENSUS_GUARDED:
        entry_status = (
            f"Race mode: CONSENSUS_GUARDED - new entries blocked because {entry_block}"
            if entry_block
            else "Race mode: CONSENSUS_GUARDED - model agreement guards active"
        )
    else:
        entry_status = "Race mode: INDEPENDENT - no global spread block"
    if payload.get("entries_enabled") is False:
        entry_status += " | Exit monitor only"
    lines.extend(
        [
            "",
            f"Agreement: {agreement.get('status')} | Spread: {_fmt_f(agreement.get('spread_f'))} | Warning: {warning} | {entry_status}",
            "Live trading: DISABLED | Real orders: NOT AVAILABLE | Mode: fake money only",
        ]
    )
    if action_notes:
        lines.extend(["", "Notes:", *action_notes])
    if payload.get("open_positions"):
        lines.extend(["", "Open positions:"])
        for pos in payload["open_positions"]:
            lines.append(
                f"- {pos['model_key']}: {pos['quantity']} {pos['side'].upper()} contracts on "
                f"{pos.get('bracket_label_display') or pos['bracket_label']} | entry {_fmt_cents(pos['entry_price'])} | "
                f"current bid {_fmt_cents(pos.get('current_mark_price'))} | open P/L {_fmt_money(pos.get('open_pnl'))}"
                f"{_liquidity_suffix(pos)}"
            )
    if payload.get("closed_trades_this_update"):
        lines.extend(["", "Closed trades this update:"])
        for trade in payload["closed_trades_this_update"]:
            lines.append(
                f"- {trade['model_key']}: SOLD {trade.get('bracket_label_display') or trade['bracket_label']} {trade['side'].upper()} "
                f"at {_fmt_cents(trade['price'])} | entry {_fmt_cents(trade['entry_price'])} | "
                f"profit {_fmt_money(trade['realized_pnl'])} | reason: {trade['reason']}"
                f"{_closed_trade_advisor_suffix(trade.get('advisor'))}"
            )
    return "\n".join(lines)


def model_race_debug_text(payload: dict[str, Any]) -> str:
    """Human-readable state dump meant for copy/paste debugging."""
    generated = _parse_dt(payload.get("generated_at_utc")) or utc_now()
    local = generated.astimezone(PACIFIC)
    config = payload.get("config") or {}
    agreement = payload.get("agreement") or {}
    lines = [
        "COPY/PASTE DEBUG",
        (
            f"race_id={payload.get('race_id')} station={payload.get('station')} "
            f"target_date={payload.get('market_date')} time_pt={local.strftime('%Y-%m-%d %H:%M:%S')}"
        ),
        (
            f"iteration={payload.get('iteration', '--')} kind={payload.get('iteration_kind', '--')} "
            f"entries_enabled={payload.get('entries_enabled')} mode={payload.get('mode', '--')}"
        ),
        (
            f"observed_high={_fmt_f(payload.get('observed_high_so_far_f'))} "
            f"current_estimate={_fmt_f(payload.get('current_production_estimate_f'))} "
            f"market_favorite={payload.get('market_favorite') or '--'}"
        ),
        (
            f"agreement={agreement.get('status')} spread={_fmt_f(agreement.get('spread_f'))} "
            f"entry_blocked_reason={payload.get('entry_blocked_reason') or '--'}"
        ),
        (
            "config "
            f"race_mode={payload.get('race_mode')} "
            f"max_open_positions_per_model={config.get('max_open_positions_per_model')} "
            f"max_risk_per_trade={config.get('max_risk_per_trade')} "
            f"max_exposure_per_model={config.get('max_exposure_per_model')} "
            f"max_exposure_per_bracket={config.get('max_exposure_per_bracket')} "
            f"profit_target_delta={config.get('profit_target_price_delta')} "
            f"stop_loss_delta={config.get('stop_loss_price_delta')} "
            f"max_hold_minutes={config.get('max_hold_minutes')} "
            f"force_flat_time={config.get('force_flat_time_local')} "
            f"use_llm_advisor={config.get('use_llm_advisor')} "
            f"llm_first={config.get('llm_first')}"
        ),
        "scoreboard:",
    ]
    for row in payload.get("scoreboard", []):
        lines.append(
            f"- {row.get('model_key')}: action={row.get('action')} reason={row.get('reason')} "
            f"est={_fmt_f(row.get('est_high_f'))} top={row.get('top_bracket') or '--'} "
            f"p_top={row.get('p_top') or '--'} best={row.get('best_trade') or '--'} "
            f"edge={row.get('edge') or '--'} cash={_fmt_money(row.get('cash'))} "
            f"open={_fmt_money(row.get('open_pnl_display', row.get('open_pnl')))} "
            f"closed={_fmt_money(row.get('closed_pnl'))}"
        )
        if row.get("advisor_mode"):
            lines.append(
                f"  advisor mode={row.get('advisor_mode')} decision={row.get('advisor_decision')} "
                f"score={row.get('advisor_score')} validator={row.get('validator')} "
                f"final={row.get('final_action')} why={_advisor_reason_text(row)}"
            )
    open_positions = payload.get("open_positions") or []
    lines.append(f"open_positions_count={len(open_positions)}")
    for pos in open_positions:
        lines.append(
            f"- {pos.get('model_key')}: ticker={pos.get('market_ticker')} side={pos.get('side')} "
            f"qty={pos.get('quantity')} bracket={pos.get('bracket_label_display') or pos.get('bracket_label')} "
            f"entry={_fmt_cents(pos.get('entry_price'))} current_bid={_fmt_cents(pos.get('current_mark_price'))} "
            f"open_pnl={_fmt_money(pos.get('open_pnl'))} liquidity={pos.get('liquidity_status') or '--'} "
            f"exit_blocked={pos.get('exit_blocked_reason') or '--'}"
        )
    closed = payload.get("closed_trades_this_update") or []
    lines.append(f"closed_trades_this_update_count={len(closed)}")
    for trade in closed:
        lines.append(
            f"- {trade.get('model_key')}: ticker={trade.get('market_ticker')} side={trade.get('side')} "
            f"qty={trade.get('quantity')} exit={_fmt_cents(trade.get('price'))} "
            f"entry={_fmt_cents(trade.get('entry_price'))} pnl={_fmt_money(trade.get('realized_pnl'))} "
            f"reason={trade.get('reason')}{_closed_trade_advisor_suffix(trade.get('advisor'))}"
        )
    lines.extend(
        [
            "quick_read:",
            "- if open_positions_count=0, there is nothing currently available to sell.",
            "- if entries_enabled=False, this tick is exit-monitor only and will not buy.",
            "- if advisor lines are absent, the LLM was not asked on that row.",
        ]
    )
    return "\n".join(lines)


def _closed_trade_advisor_suffix(advisor: dict[str, Any] | None) -> str:
    if not advisor:
        return ""
    decision = str(advisor.get("advisor_decision") or "--")
    final = str(advisor.get("final_action") or "--")
    score = advisor.get("trade_quality_score")
    score_text = str(score) if score is not None else "--"
    validator = str(advisor.get("validator_status") or "--")
    why = _clip(_advisor_reason_text(advisor), 24)
    if advisor.get("exit_safety_override") and final != "SELL":
        return f" | LLM {decision} {score_text} {validator} -> SELL safety: {why}"
    return f" | LLM {decision} {score_text} {validator} -> {final}: {why}"


def _advisor_wide_columns(row: dict[str, Any]) -> str:
    score = row.get("advisor_score")
    score_text = str(score) if score is not None else "--"
    validator = str(row.get("validator") or "--")
    decision = str(row.get("advisor_decision") or "--")
    final = str(row.get("final_action") or "--")
    return (
        f"   {_clip(decision, 8):<8} {_clip(score_text, 5):>5} "
        f"{_clip(validator, 6):<6} {_clip(final, 6):<6} "
        f"{_clip(_advisor_reason_text(row), 24)}"
    )


def _advisor_reason_text(row: dict[str, Any]) -> str:
    if row.get("veto_reasons"):
        return ", ".join(str(item).replace("_", " ") for item in row.get("veto_reasons") or [])
    reason = str(row.get("final_reason") or row.get("primary_reason") or "--")
    if reason.lower().startswith("validator veto:"):
        reason = reason.split(":", 1)[1].strip()
    return reason.replace("_", " ")


def model_race_report_payload(store: SQLiteStore, race_id: str) -> dict[str, Any]:
    leaderboard = store.load_model_race_leaderboard(race_id)
    fills = store.load_model_race_fills(race_id)
    open_positions = store.load_open_model_race_positions(race_id)
    best = leaderboard[0]["model_key"] if leaderboard else None
    worst = leaderboard[-1]["model_key"] if leaderboard else None
    return {
        "race_id": race_id,
        "generated_at_utc": utc_now().isoformat(),
        "leaderboard": leaderboard,
        "fills": fills,
        "open_positions": open_positions,
        "best_model": best,
        "worst_model": worst,
        "fake_money_only": True,
        "live_trading_enabled": False,
    }


def model_race_report_text(payload: dict[str, Any]) -> str:
    lines = [
        f"PAPER MODEL RACE REPORT - {payload.get('race_id')}",
        "",
        "Model                    Cash      Open P/L   Closed P/L  Total equity  Trades  Wins  Losses",
    ]
    for row in payload.get("leaderboard", []):
        lines.append(
            f"{str(row.get('model_key')):<24} {_fmt_money(row.get('cash')):<9} "
            f"{_fmt_money(row.get('open_pnl')):<10} {_fmt_money(row.get('closed_pnl')):<11} "
            f"{_fmt_money(row.get('total_equity')):<13} {row.get('trades', 0):<7} "
            f"{row.get('wins', 0):<5} {row.get('losses', 0)}"
        )
    lines.extend(["", f"Best model: {payload.get('best_model') or 'n/a'}", f"Worst model: {payload.get('worst_model') or 'n/a'}"])
    if payload.get("open_positions"):
        lines.extend(["", f"Open positions: {len(payload['open_positions'])}"])
    else:
        lines.extend(["", "Open positions: 0"])
    if not payload.get("fills"):
        lines.append("No fake trades have been taken yet.")
    return "\n".join(lines)


def _load_account_states(
    store: SQLiteStore,
    config: ModelRaceConfig,
    specs: list[dict[str, str]],
) -> dict[str, ModelRaceAccountState]:
    accounts = {row["model_key"]: row for row in store.load_model_race_accounts(config.race_id)}
    states: dict[str, ModelRaceAccountState] = {}
    for spec in specs:
        row = accounts.get(spec["model_key"])
        cash = _dec(row.get("current_cash") if row else config.starting_cash_per_model)
        starting = _dec(row.get("starting_cash") if row else config.starting_cash_per_model)
        closed = _dec(row.get("realized_pnl") if row else 0)
        positions = store.load_open_model_race_positions(config.race_id, spec["model_key"])
        states[spec["model_key"]] = ModelRaceAccountState(
            model_key=spec["model_key"],
            provider=spec["provider"],
            model_id=spec["model_id"],
            cash=cash,
            starting_cash=starting,
            open_positions=positions,
            open_pnl=_mark_open_positions(positions, [], config),
            closed_pnl=closed,
            exposure=_position_exposure(positions),
            status=row.get("status", "active") if row else "active",
        )
    return states


def _best_probability_trade(rows: list[dict[str, Any]], observed_high: float | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        p_yes = float(row.get("p_yes") or 0)
        options = [
            ("yes", _dec_or_none(row.get("yes_ask")), _dec_or_none(row.get("yes_bid")), _dec_or_none(row.get("yes_edge"))),
            ("no", _dec_or_none(row.get("no_ask")), _dec_or_none(row.get("no_bid")), _dec_or_none(row.get("no_edge"))),
        ]
        for side, ask, bid, edge in options:
            if edge is None:
                continue
            candidates.append(
                {
                    **row,
                    "side": side,
                    "ask": ask,
                    "bid": bid,
                    "edge": edge,
                    "p_yes": p_yes,
                    "best_trade": f"{_display_bracket_label(row)} {side.upper()}",
                    "invalidated": _bracket_invalidated({**row, "side": side}, observed_high),
                }
            )
    candidates = [item for item in candidates if not item["invalidated"]]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["edge"])


def _scoreboard_row(
    spec: dict[str, str],
    estimate: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    signal: ModelRaceSignal,
    account: ModelRaceAccountState,
    open_pnl: Decimal,
    total_equity: Decimal,
    config: ModelRaceConfig,
    *,
    is_outlier: bool,
    advisor_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    top = _top_probability_row(rows)
    open_pnl_display = _open_pnl_display(account.open_positions, rows, config=None)
    return {
        "model": spec["display_name"],
        "model_key": spec["model_key"],
        "est_high_f": estimate.get("settlement_high_estimate_f") if estimate else None,
        "future_high_f": estimate.get("future_high_f") if estimate else None,
        "top_bracket": _display_bracket_label(top) if top else None,
        "p_top": _fmt_pct(top.get("p_yes")) if top else None,
        "best_trade": signal.best_trade,
        "edge": _fmt_pct_decimal(signal.edge) if signal.edge is not None else None,
        "action": _action_label(signal, is_outlier=is_outlier, config=config),
        "reason": signal.reason,
        "action_reason": signal.reason,
        "blocked_reason": signal.reason if signal.action == "blocked" else None,
        "cash": str(account.cash),
        "open_pnl": str(open_pnl),
        "open_pnl_display": open_pnl_display,
        "closed_pnl": str(account.closed_pnl),
        "total_equity": str(total_equity),
        "outlier": is_outlier,
        "successful": bool(estimate and estimate.get("successful")),
        "race_mode": config.race_mode,
        "advisor_mode": (advisor_result or {}).get("advisor_mode"),
        "advisor_score": (advisor_result or {}).get("trade_quality_score"),
        "advisor_decision": (advisor_result or {}).get("advisor_decision"),
        "validator": (advisor_result or {}).get("validator_status"),
        "final_action": (advisor_result or {}).get("final_action"),
        "primary_reason": (advisor_result or {}).get("primary_reason"),
        "final_reason": (advisor_result or {}).get("final_reason"),
        "veto_reasons": (advisor_result or {}).get("veto_reasons"),
    }


def _top_probability_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda row: float(row.get("p_yes") or 0), default=None)


def _position_output(position: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    row = {item["market_ticker"]: item for item in rows}.get(position["market_ticker"])
    mark = _bid_for_position(row, position["side"]) if row else None
    if mark is not None:
        open_pnl: str | None = str((mark - _dec(position["entry_price"])) * _dec(position["quantity"]))
        liquidity_status = "ok"
    else:
        open_pnl = None
        liquidity_status = position.get("liquidity_status") or "no_exit_bid"
    return {
        **position,
        "bracket_label_display": _display_bracket_label(position),
        "current_mark_price": str(mark) if mark is not None else None,
        "open_pnl": open_pnl,
        "displayed_open_pnl": open_pnl,
        "conservative_open_pnl_if_no_bid": position.get("conservative_open_pnl"),
        "liquidity_status": liquidity_status,
    }


def _closed_trade_output(position: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any]:
    payload = fill.get("payload_json") if isinstance(fill.get("payload_json"), dict) else {}
    output = {
        "model_key": position["model_key"],
        "market_ticker": position["market_ticker"],
        "bracket_label": position.get("bracket_label"),
        "bracket_label_display": _display_bracket_label(position),
        "side": position["side"],
        "quantity": str(position["quantity"]),
        "entry_price": str(position["entry_price"]),
        "price": str(fill["price"]),
        "realized_pnl": str(fill["realized_pnl"]),
        "reason": fill["reason"],
    }
    if payload.get("advisor"):
        output["advisor"] = payload["advisor"]
    return output


def _market_favorite(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    grouped: dict[str, float] = {}
    labels: dict[str, str] = {}
    for row in rows:
        ticker = row.get("market_ticker")
        if not ticker:
            continue
        grouped[ticker] = max(grouped.get(ticker, 0.0), float(row.get("p_yes") or 0))
        labels[ticker] = _display_bracket_label(row)
    if not grouped:
        return None
    ticker = max(grouped, key=grouped.get)
    return labels[ticker]


def _mark_open_positions(
    positions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    config: ModelRaceConfig | None = None,
) -> Decimal:
    by_ticker = {row["market_ticker"]: row for row in rows}
    total = Decimal("0")
    for position in positions:
        row = by_ticker.get(position["market_ticker"])
        mark = _bid_for_position(row, position["side"]) if row else None
        if mark is None:
            mode = (config.missing_bid_mark_mode if config else "na").lower()
            if mode == "full_loss":
                total -= _dec(position["entry_price"]) * _dec(position["quantity"])
            elif mode == "zero":
                total += Decimal("0")
            else:
                total += Decimal("0")
        else:
            total += (mark - _dec(position["entry_price"])) * _dec(position["quantity"])
    return total


def _position_mark_details(
    position: dict[str, Any],
    mark: Decimal | None,
    now: datetime,
    config: ModelRaceConfig,
) -> dict[str, Any]:
    qty = _dec(position.get("quantity"))
    entry = _dec(position.get("entry_price"))
    if mark is not None:
        open_pnl = (mark - entry) * qty
        return {
            "open_pnl_for_storage": open_pnl,
            "displayed_open_pnl": open_pnl,
            "conservative_open_pnl": open_pnl,
            "missing_bid_count": 0,
            "last_bid_seen_utc": now,
            "liquidity_status": "ok",
        }
    missing = int(position.get("missing_bid_count") or 0) + 1
    conservative = -entry * qty
    if config.missing_bid_mark_mode == "full_loss":
        storage_pnl: Decimal | None = conservative
    else:
        storage_pnl = Decimal("0")
    status = (
        "illiquid_warning"
        if missing > config.max_missing_bid_warnings_before_block_new_entries
        else "no_exit_bid"
    )
    return {
        "open_pnl_for_storage": storage_pnl,
        "displayed_open_pnl": None,
        "conservative_open_pnl": conservative,
        "missing_bid_count": missing,
        "last_bid_seen_utc": position.get("last_bid_seen_utc"),
        "liquidity_status": status,
    }


def _open_pnl_display(
    positions: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    config: ModelRaceConfig | None = None,
) -> str | None:
    _ = config
    if not positions:
        return "0"
    by_ticker = {row["market_ticker"]: row for row in rows}
    total = Decimal("0")
    any_missing = False
    for position in positions:
        row = by_ticker.get(position["market_ticker"])
        mark = _bid_for_position(row, position["side"]) if row else None
        if mark is None:
            any_missing = True
            continue
        total += (mark - _dec(position["entry_price"])) * _dec(position["quantity"])
    return None if any_missing else str(total)


def _has_no_exit_bid_position(positions: list[dict[str, Any]]) -> bool:
    return any(
        (position.get("liquidity_status") in {"no_exit_bid", "illiquid_warning", "exit_blocked_no_bid"})
        or int(position.get("missing_bid_count") or 0) > 0
        or bool(position.get("exit_blocked_reason"))
        for position in positions
    )


def _global_entry_block_reason(agreement: dict[str, Any], config: ModelRaceConfig) -> str | None:
    if config.race_mode != RACE_MODE_CONSENSUS_GUARDED:
        return None
    spread = agreement.get("spread_f")
    if spread is None:
        return None
    if float(spread) > config.block_new_entries_if_model_spread_gt_f:
        return f"spread > {config.block_new_entries_if_model_spread_gt_f:g}F"
    return None


def _outlier_block_enabled(config: ModelRaceConfig) -> bool:
    return bool(config.block_outlier_model_entries or config.block_outlier_models)


def _model_spread_f(inputs: dict[str, Any]) -> float | None:
    agreement = inputs.get("agreement") or {}
    spread = agreement.get("spread_f")
    return float(spread) if spread is not None else None


def _trade_spread(trade: dict[str, Any]) -> Decimal | None:
    ask = trade.get("ask")
    bid = trade.get("bid")
    if ask is None or bid is None:
        return None
    return ask - bid


def _top_book_size_for_trade(trade: dict[str, Any]) -> Decimal | None:
    side = trade.get("side")
    if side not in {"yes", "no"}:
        return None
    for key in (f"{side}_ask_size", f"{side}_ask_quantity", f"{side}_ask_count", "ask_size", "ask_quantity"):
        if trade.get(key) is not None:
            return _dec(trade.get(key))
    details = trade.get("details_json")
    if isinstance(details, dict):
        for key in (f"{side}_ask_size", "ask_size", "top_ask_size"):
            if details.get(key) is not None:
                return _dec(details.get(key))
    return None


def _cents_to_price(cents: Decimal | int | float | str) -> Decimal:
    return _dec(cents) / Decimal("100")


def _cooldown_reason(cooldown: dict[str, Any], now: datetime) -> str:
    until = _parse_dt(cooldown.get("cooldown_until_utc"))
    if until is None:
        return "cooldown active"
    minutes = max(0, math.ceil((until - now).total_seconds() / 60))
    return f"cooldown {minutes}m"


def _action_label(
    signal: ModelRaceSignal,
    *,
    is_outlier: bool = False,
    config: ModelRaceConfig | None = None,
) -> str:
    if signal.action == "blocked":
        if "outlier" in signal.reason:
            return "blocked: outlier"
        if signal.reason == "spread too wide":
            return "blocked: spread too wide"
        if "spread" in signal.reason:
            return "blocked: spread"
        if "cooldown" in signal.reason:
            return f"blocked: {signal.reason}"
        if "price too high" in signal.reason:
            return "blocked: price too high"
        if "bid" in signal.reason or "liquid" in signal.reason:
            return f"blocked: {signal.reason}"
        return f"blocked: {signal.reason}"
    if signal.action == "holding" and signal.reason != "existing position open":
        return signal.reason
    if signal.action == "bought" and is_outlier and (config is None or not _outlier_block_enabled(config)):
        return "bought / outlier watch"
    if signal.action == "skip" and signal.reason == "exit monitor only":
        return "exit monitor"
    return signal.action


def _liquidity_suffix(position: dict[str, Any]) -> str:
    status = position.get("liquidity_status")
    reason = position.get("exit_blocked_reason")
    if reason and position.get("current_mark_price") is None:
        return f" | exit blocked no bid ({reason})"
    if status in {"no_exit_bid", "illiquid_warning", "exit_blocked_no_bid"}:
        return " | no exit bid"
    return ""


def _display_bracket_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "--"
    lower = row.get("bracket_lower_f")
    upper = row.get("bracket_upper_f")
    bracket_type = str(row.get("bracket_type") or "").lower()
    if bracket_type == "below" or (lower is None and upper is not None):
        return f"<={_fmt_bound(upper)}"
    if bracket_type == "above" or (lower is not None and upper is None):
        return f">={_fmt_bound(lower)}"
    if lower is not None and upper is not None:
        return f"{_fmt_bound(lower)}-{_fmt_bound(upper)}"
    label = str(row.get("bracket_label") or "")
    return label if len(label) <= 24 else (row.get("market_ticker") or label[:24])


def _fmt_bound(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _position_exposure(positions: list[dict[str, Any]]) -> Decimal:
    return sum(_dec(pos["quantity"]) * _dec(pos["entry_price"]) for pos in positions)


def _bid_for_position(row: dict[str, Any] | None, side: str) -> Decimal | None:
    if not row:
        return None
    return _dec_or_none(row.get("yes_bid" if side == "yes" else "no_bid"))


def _ask_for_position(row: dict[str, Any] | None, side: str) -> Decimal | None:
    if not row:
        return None
    return _dec_or_none(row.get("yes_ask" if side == "yes" else "no_ask"))


def _edge_for_position(row: dict[str, Any] | None, side: str) -> Decimal | None:
    if not row:
        return None
    return _dec_or_none(row.get("yes_edge" if side == "yes" else "no_edge"))


def _current_probability(row: dict[str, Any] | None, side: str) -> float | None:
    if not row:
        return None
    p_yes = float(row.get("p_yes") or 0)
    return p_yes if side == "yes" else 1 - p_yes


def _bracket_invalidated(row: dict[str, Any], observed_high: float | None) -> bool:
    if observed_high is None:
        return False
    upper = row.get("bracket_upper_f")
    if upper is None:
        return False
    return float(observed_high) > float(upper)


def _position_invalidated(position: dict[str, Any], observed_high: float | None) -> bool:
    if position.get("side") != "yes":
        return False
    return _bracket_invalidated(position, observed_high)


def _estimate_stale(estimate: dict[str, Any], config: ModelRaceConfig) -> bool:
    asof = _parse_dt(estimate.get("asof_utc"))
    if asof is None:
        return False
    return utc_now() - asof > timedelta(minutes=config.stale_model_minutes)


def _force_flat_due(config: ModelRaceConfig, now: datetime) -> bool:
    local_now = now.astimezone(PACIFIC)
    target = _parse_local_time(config.force_flat_time_local)
    return local_now.time() >= target


def _fee(quantity: int, config: ModelRaceConfig) -> Decimal:
    if config.fee_mode == "simple_per_contract":
        return Decimal(quantity) * Decimal("0.01")
    return Decimal("0")


def _event_ticker(market_ticker: str | None) -> str | None:
    if not market_ticker:
        return None
    parts = market_ticker.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else market_ticker


def _config_record(config: ModelRaceConfig) -> dict[str, Any]:
    return {key: str(value) if isinstance(value, Decimal) else value for key, value in config.__dict__.items()}


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _dec(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_local_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _fmt_f(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.1f}F"
    except (TypeError, ValueError):
        return str(value)


def _fmt_f_short(value: Any) -> str:
    return _fmt_f(value)


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "~"


def _fmt_money(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, str) and value.lower() in {"n/a", "na", "none"}:
        return "n/a"
    return f"${_dec(value):.2f}"


def _fmt_cents(value: Any) -> str:
    if value is None:
        return "--"
    return f"{(_dec(value) * 100):.0f}c"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100:.0f}%"


def _fmt_pct_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value * Decimal('100'):.0f}%"
