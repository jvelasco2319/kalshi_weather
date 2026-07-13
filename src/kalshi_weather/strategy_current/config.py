from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from kalshi_weather.strategy_current.registry import (
    CANONICAL_MODEL_KEYS,
    STRATEGY_ID,
    StrategyModel,
    canonicalize_model_key,
    source_key_for_model,
    strategy_models,
    validate_exact_strategy_model_set,
)

DEFAULT_STRATEGY_CONFIG_PATH = Path("config/strategy_current.shadow.yaml")


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    mode: str
    live_trading_enabled: bool
    canary_enabled: bool
    taker_enabled: bool
    order_submission_reachable: bool
    station: str
    series: str
    timezone: str
    canonical_order: tuple[str, ...]
    source_preferences: dict[str, str]
    aliases: dict[str, str]
    minimum_feeds_for_trade_probability: int
    minimum_independence_families: int
    prior_weights: dict[str, Decimal]
    individual_cap: Decimal
    family_caps: dict[str, Decimal]
    nbm_caps_by_completed_dates: dict[str, Decimal]
    launch_expected_roi_hurdle: Decimal
    price_increment_dollars: Decimal
    require_sequence_valid_book: bool
    require_trade_count_fp: bool
    require_exhausted_trade_cursor: bool
    candles_eligible_for_fill_simulation: bool

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(_jsonable(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def models(self) -> tuple[StrategyModel, ...]:
        by_key = {model.key: model for model in strategy_models()}
        return tuple(by_key[key] for key in self.canonical_order)


def load_strategy_config(path: str | Path | None = None) -> StrategyConfig:
    config_path = Path(path) if path is not None else DEFAULT_STRATEGY_CONFIG_PATH
    data = _load_yaml(config_path)
    return strategy_config_from_mapping(data)


def strategy_config_from_mapping(data: dict[str, Any] | None = None) -> StrategyConfig:
    payload = data or {}
    model_data = payload.get("models") or {}
    weight_data = payload.get("model_weights") or {}
    market_data = payload.get("market_data") or {}
    economics_data = payload.get("economics") or {}

    canonical_order = tuple(
        canonicalize_model_key(str(value))
        for value in model_data.get("canonical_order", CANONICAL_MODEL_KEYS)
    )
    validate_exact_strategy_model_set(canonical_order)

    source_preferences = {
        key: str((model_data.get("source_preferences") or {}).get(key, source_key_for_model(key)))
        for key in canonical_order
    }
    aliases = {
        str(alias): canonicalize_model_key(str(target))
        for alias, target in (model_data.get("aliases") or {"nam_conus": "nam"}).items()
    }
    prior_weights = {
        key: _decimal((weight_data.get("prior") or {}).get(key, _default_prior_weight(key)))
        for key in canonical_order
    }

    config = StrategyConfig(
        strategy_id=str(payload.get("strategy_id", STRATEGY_ID)),
        mode=str(payload.get("mode", "shadow")),
        live_trading_enabled=bool(payload.get("live_trading_enabled", False)),
        canary_enabled=bool(payload.get("canary_enabled", False)),
        taker_enabled=bool(payload.get("taker_enabled", False)),
        order_submission_reachable=bool(payload.get("order_submission_reachable", False)),
        station=str(payload.get("station", "KLAX")),
        series=str(payload.get("series", "KXHIGHLAX")),
        timezone=str(payload.get("timezone", "America/Los_Angeles")),
        canonical_order=canonical_order,
        source_preferences=source_preferences,
        aliases=aliases,
        minimum_feeds_for_trade_probability=int(
            model_data.get("minimum_feeds_for_trade_probability", 4)
        ),
        minimum_independence_families=int(model_data.get("minimum_independence_families", 3)),
        prior_weights=prior_weights,
        individual_cap=_decimal(weight_data.get("individual_cap", "0.35")),
        family_caps={
            str(key): _decimal(value)
            for key, value in (weight_data.get("family_caps") or {"GFS": "0.45"}).items()
        },
        nbm_caps_by_completed_dates={
            str(key): _decimal(value)
            for key, value in (
                weight_data.get("nbm_caps_by_completed_dates")
                or {
                    "below_10": "0.00",
                    "10_to_29": "0.10",
                    "30_to_59": "0.20",
                    "60_plus": "0.25",
                }
            ).items()
        },
        launch_expected_roi_hurdle=_decimal(
            economics_data.get("launch_expected_roi_hurdle", "0.15")
        ),
        price_increment_dollars=_decimal(economics_data.get("price_increment_dollars", "0.01")),
        require_sequence_valid_book=bool(market_data.get("require_sequence_valid_book", True)),
        require_trade_count_fp=bool(market_data.get("require_trade_count_fp", True)),
        require_exhausted_trade_cursor=bool(
            market_data.get("require_exhausted_trade_cursor", True)
        ),
        candles_eligible_for_fill_simulation=bool(
            market_data.get("candles_eligible_for_fill_simulation", False)
        ),
    )
    _validate_safe_shadow_config(config)
    return config


def _validate_safe_shadow_config(config: StrategyConfig) -> None:
    if config.strategy_id != STRATEGY_ID:
        raise ValueError(f"strategy_id must be {STRATEGY_ID!r}")
    if config.mode != "shadow":
        raise ValueError("current strategy must run in shadow mode")
    if config.live_trading_enabled:
        raise ValueError("live trading must be disabled")
    if config.canary_enabled:
        raise ValueError("canary must be disabled")
    if config.taker_enabled:
        raise ValueError("taker must be disabled")
    if config.order_submission_reachable:
        raise ValueError("order submission must be unreachable in shadow mode")
    if config.candles_eligible_for_fill_simulation:
        raise ValueError("candles cannot be marked executable for current strategy")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _default_prior_weight(model_key: str) -> Decimal:
    return {model.key: model.prior_weight for model in strategy_models()}[model_key]


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _jsonable(value: Any) -> Any:
    if isinstance(value, StrategyConfig):
        return _jsonable({
            "strategy_id": value.strategy_id,
            "mode": value.mode,
            "live_trading_enabled": value.live_trading_enabled,
            "canary_enabled": value.canary_enabled,
            "taker_enabled": value.taker_enabled,
            "order_submission_reachable": value.order_submission_reachable,
            "station": value.station,
            "series": value.series,
            "timezone": value.timezone,
            "canonical_order": value.canonical_order,
            "source_preferences": value.source_preferences,
            "aliases": value.aliases,
            "minimum_feeds_for_trade_probability": value.minimum_feeds_for_trade_probability,
            "minimum_independence_families": value.minimum_independence_families,
            "prior_weights": value.prior_weights,
            "individual_cap": value.individual_cap,
            "family_caps": value.family_caps,
            "nbm_caps_by_completed_dates": value.nbm_caps_by_completed_dates,
            "launch_expected_roi_hurdle": value.launch_expected_roi_hurdle,
            "price_increment_dollars": value.price_increment_dollars,
            "require_sequence_valid_book": value.require_sequence_valid_book,
            "require_trade_count_fp": value.require_trade_count_fp,
            "require_exhausted_trade_cursor": value.require_exhausted_trade_cursor,
            "candles_eligible_for_fill_simulation": value.candles_eligible_for_fill_simulation,
        })
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
