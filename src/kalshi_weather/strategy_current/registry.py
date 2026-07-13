from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

STRATEGY_ID = "klax-current-five-model-2026-07-11"

CANONICAL_MODEL_KEYS: tuple[str, ...] = (
    "ecmwf_ifs",
    "gfs013",
    "gfs_seamless",
    "nam",
    "nbm",
)

STRATEGY_EXCLUDED_MODEL_KEYS: frozenset[str] = frozenset(
    {
        "current_weighted_blend",
        "best_match",
        "gfs_global",
        "nam_conus_as_separate_vote",
        "hrrr",
        "rap",
        "gfs",
        "aifs",
    }
)


@dataclass(frozen=True)
class StrategyModel:
    key: str
    canonical_source_key: str
    provider: str
    family: str
    independence_group: str
    prior_weight: Decimal
    aliases: tuple[str, ...] = ()


_STRATEGY_MODELS: tuple[StrategyModel, ...] = (
    StrategyModel(
        key="ecmwf_ifs",
        canonical_source_key="open_meteo:ecmwf_ifs",
        provider="Open-Meteo",
        family="ECMWF",
        independence_group="ECMWF",
        prior_weight=Decimal("0.20"),
    ),
    StrategyModel(
        key="gfs013",
        canonical_source_key="open_meteo:gfs013",
        provider="Open-Meteo",
        family="GFS",
        independence_group="GFS",
        prior_weight=Decimal("0.25"),
    ),
    StrategyModel(
        key="gfs_seamless",
        canonical_source_key="open_meteo:gfs_seamless",
        provider="Open-Meteo",
        family="GFS",
        independence_group="GFS",
        prior_weight=Decimal("0.20"),
    ),
    StrategyModel(
        key="nam",
        canonical_source_key="noaa_herbie:nam",
        provider="NOAA/Herbie",
        family="NAM",
        independence_group="NAM",
        prior_weight=Decimal("0.15"),
        aliases=("noaa_herbie:nam_conus", "nam_conus"),
    ),
    StrategyModel(
        key="nbm",
        canonical_source_key="noaa_herbie:nbm",
        provider="NOAA/Herbie",
        family="NBM",
        independence_group="NBM",
        prior_weight=Decimal("0.20"),
    ),
)

_MODELS_BY_KEY = {model.key: model for model in _STRATEGY_MODELS}
_SOURCE_TO_MODEL_KEY = {
    source_key: model.key
    for model in _STRATEGY_MODELS
    for source_key in (model.canonical_source_key, *model.aliases)
}
_MODEL_KEY_ALIASES = {"nam_conus": "nam"}


def strategy_models() -> tuple[StrategyModel, ...]:
    return _STRATEGY_MODELS


def strategy_model_keys() -> tuple[str, ...]:
    return CANONICAL_MODEL_KEYS


def strategy_model_by_key(model_key: str) -> StrategyModel:
    key = canonicalize_model_key(model_key)
    return _MODELS_BY_KEY[key]


def canonicalize_model_key(value: str) -> str:
    if value in _MODELS_BY_KEY:
        return value
    if value in _MODEL_KEY_ALIASES:
        return _MODEL_KEY_ALIASES[value]
    if value in _SOURCE_TO_MODEL_KEY:
        return _SOURCE_TO_MODEL_KEY[value]
    if value in STRATEGY_EXCLUDED_MODEL_KEYS:
        raise ValueError(f"{value!r} is excluded from the current strategy")
    raise ValueError(f"{value!r} is not a current-strategy model")


def canonical_strategy_model_keys(values: Iterable[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for value in values:
        key = canonicalize_model_key(value)
        if key not in selected:
            selected.append(key)
    return tuple(selected)


def validate_exact_strategy_model_set(values: Iterable[str]) -> tuple[str, ...]:
    keys = canonical_strategy_model_keys(values)
    if keys != CANONICAL_MODEL_KEYS:
        raise ValueError(f"strategy model set must be exactly {CANONICAL_MODEL_KEYS}")
    return keys


def source_key_for_model(model_key: str) -> str:
    return strategy_model_by_key(model_key).canonical_source_key


def canonicalize_source_key(model_key: str, source_key: str | None = None) -> str:
    model = strategy_model_by_key(model_key)
    if source_key is None:
        return model.canonical_source_key
    if source_key == model.key:
        return model.canonical_source_key
    if source_key in model.aliases:
        return model.canonical_source_key
    if source_key in _SOURCE_TO_MODEL_KEY and _SOURCE_TO_MODEL_KEY[source_key] != model.key:
        other = _SOURCE_TO_MODEL_KEY[source_key]
        raise ValueError(f"source {source_key!r} belongs to {other!r}, not {model.key!r}")
    return source_key


def source_history_key(model_key: str, source_key: str | None = None) -> str:
    """Return the residual/history partition for a model-source variant.

    Provider substitutions intentionally produce a different key. Aliases such
    as NAM CONUS collapse to their canonical model source and do not get a
    second history or vote.
    """
    key = canonicalize_model_key(model_key)
    source = canonicalize_source_key(key, source_key)
    return f"{STRATEGY_ID}:{key}:{source}"
