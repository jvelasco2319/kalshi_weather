from __future__ import annotations

from decimal import Decimal

import pytest

from kalshi_weather.model_registry import select_model_keys
from kalshi_weather.strategy_current.config import (
    StrategyConfig,
    load_strategy_config,
    strategy_config_from_mapping,
)
from kalshi_weather.strategy_current.registry import (
    CANONICAL_MODEL_KEYS,
    STRATEGY_ID,
    canonical_strategy_model_keys,
    canonicalize_model_key,
    source_history_key,
    strategy_model_by_key,
    strategy_model_keys,
    strategy_models,
    validate_exact_strategy_model_set,
)


def test_strategy_registry_is_exact_five_models_without_changing_legacy_registry() -> None:
    assert strategy_model_keys() == (
        "ecmwf_ifs",
        "gfs013",
        "gfs_seamless",
        "nam",
        "nbm",
    )
    assert tuple(model.key for model in strategy_models()) == CANONICAL_MODEL_KEYS
    assert "best_match" in select_model_keys(model_set="current")
    assert "best_match" not in strategy_model_keys()
    assert "gfs_global" not in strategy_model_keys()
    assert "current_weighted_blend" not in strategy_model_keys()


def test_nam_conus_alias_deduplicates_to_nam() -> None:
    assert canonicalize_model_key("nam_conus") == "nam"
    assert canonicalize_model_key("noaa_herbie:nam_conus") == "nam"
    assert canonical_strategy_model_keys(["nam", "nam_conus", "noaa_herbie:nam_conus"]) == (
        "nam",
    )
    assert validate_exact_strategy_model_set(
        ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nam_conus", "nbm"]
    ) == CANONICAL_MODEL_KEYS


def test_shadow_strategy_config_defaults_are_safe() -> None:
    config = load_strategy_config()

    assert isinstance(config, StrategyConfig)
    assert config.strategy_id == STRATEGY_ID
    assert config.mode == "shadow"
    assert config.live_trading_enabled is False
    assert config.canary_enabled is False
    assert config.taker_enabled is False
    assert config.order_submission_reachable is False
    assert config.canonical_order == CANONICAL_MODEL_KEYS
    assert config.source_preferences["nam"] == "noaa_herbie:nam"
    assert config.source_preferences["nbm"] == "noaa_herbie:nbm"
    assert config.aliases["noaa_herbie:nam_conus"] == "nam"
    assert config.prior_weights["gfs013"] == Decimal("0.25")
    assert config.family_caps["GFS"] == Decimal("0.45")
    assert len(config.config_hash) == 64


def test_unsafe_strategy_config_is_rejected() -> None:
    with pytest.raises(ValueError, match="live trading"):
        strategy_config_from_mapping({"live_trading_enabled": True})
    with pytest.raises(ValueError, match="shadow"):
        strategy_config_from_mapping({"mode": "live"})
    with pytest.raises(ValueError, match="candles"):
        strategy_config_from_mapping(
            {"market_data": {"candles_eligible_for_fill_simulation": True}}
        )


def test_source_history_keys_separate_provider_variants_but_not_aliases() -> None:
    canonical = source_history_key("nbm")
    substituted = source_history_key("nbm", "open_meteo:nbm")
    assert canonical == f"{STRATEGY_ID}:nbm:noaa_herbie:nbm"
    assert substituted == f"{STRATEGY_ID}:nbm:open_meteo:nbm"
    assert substituted != canonical

    nam = source_history_key("nam")
    nam_alias = source_history_key("nam", "noaa_herbie:nam_conus")
    assert nam_alias == nam
    assert strategy_model_by_key("noaa_herbie:nam_conus").key == "nam"
