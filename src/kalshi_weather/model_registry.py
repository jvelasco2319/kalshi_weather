from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelSource:
    model_key: str
    display_name: str
    enabled_by_default: bool
    priority: int
    provider: str
    fetcher_type: str
    endpoint_family: str
    model_family: str
    independence_group: str
    source_type: str
    model_param_candidates: list[str | None] = field(default_factory=list)
    is_blend: bool = False
    is_ensemble: bool = False
    is_direct_model: bool = False
    is_station_guidance: bool = False
    is_observation_or_analysis: bool = False
    is_synthetic: bool = False
    is_optional: bool = False
    expected_update_frequency: str | None = None
    forecast_horizon_hint: str | None = None
    preferred_variables: list[str] = field(default_factory=lambda: ["temperature_2m"])
    fallback_variables: list[str] = field(default_factory=lambda: ["temperature_2m"])
    raw_model_name: str | None = None
    raw_endpoint: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source(**kwargs: Any) -> ModelSource:
    return ModelSource(**kwargs)


MODEL_SOURCES: dict[str, ModelSource] = {
    "current_weighted_blend": _source(
        model_key="current_weighted_blend",
        display_name="Current Blend",
        enabled_by_default=True,
        priority=10,
        provider="internal",
        fetcher_type="internal_blend",
        endpoint_family="internal",
        model_family="Blend",
        independence_group="InternalBlend",
        source_type="synthetic_blend",
        is_blend=True,
        is_synthetic=True,
        expected_update_frequency="per recorder run",
        notes="Existing project weighted blend.",
    ),
    "best_match": _source(
        model_key="best_match",
        display_name="Open-Meteo Best",
        enabled_by_default=True,
        priority=20,
        provider="open_meteo",
        fetcher_type="open_meteo",
        endpoint_family="forecast",
        model_param_candidates=["best_match", "auto", None],
        model_family="BestMatch",
        independence_group="OpenMeteoBestMatch",
        source_type="provider_selection",
        is_blend=True,
        expected_update_frequency="varies by selected model",
    ),
    "gfs_seamless": _source(
        model_key="gfs_seamless",
        display_name="GFS Seamless",
        enabled_by_default=True,
        priority=30,
        provider="open_meteo",
        fetcher_type="open_meteo_gfs",
        endpoint_family="gfs",
        model_param_candidates=["gfs_seamless"],
        model_family="GFS",
        independence_group="GFS",
        source_type="deterministic_or_provider_seamless",
        is_direct_model=True,
    ),
    "gfs013": _source(
        model_key="gfs013",
        display_name="GFS 0.13",
        enabled_by_default=True,
        priority=31,
        provider="open_meteo",
        fetcher_type="open_meteo_gfs",
        endpoint_family="gfs",
        model_param_candidates=["gfs013", "gfs_013", "gfs_0p13", "gfs_0p11"],
        model_family="GFS",
        independence_group="GFS",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "gfs_global": _source(
        model_key="gfs_global",
        display_name="GFS Global",
        enabled_by_default=True,
        priority=32,
        provider="open_meteo",
        fetcher_type="open_meteo_gfs",
        endpoint_family="gfs",
        model_param_candidates=["gfs_global"],
        model_family="GFS",
        independence_group="GFS",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "nam": _source(
        model_key="nam",
        display_name="NAM",
        enabled_by_default=True,
        priority=40,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["nam"],
        model_family="NAM",
        independence_group="NAM",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "nam_conus": _source(
        model_key="nam_conus",
        display_name="NAM Conus",
        enabled_by_default=True,
        priority=41,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["nam_conus"],
        model_family="NAM",
        independence_group="NAM",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "ecmwf_ifs": _source(
        model_key="ecmwf_ifs",
        display_name="ECMWF IFS",
        enabled_by_default=True,
        priority=50,
        provider="open_meteo",
        fetcher_type="open_meteo_ecmwf",
        endpoint_family="ecmwf",
        model_param_candidates=["ecmwf_ifs", "ifs025", "ecmwf_ifs025"],
        model_family="ECMWF",
        independence_group="ECMWF_IFS",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "aifs": _source(
        model_key="aifs",
        display_name="AIFS",
        enabled_by_default=True,
        priority=51,
        provider="open_meteo",
        fetcher_type="open_meteo_ecmwf",
        endpoint_family="ecmwf",
        model_param_candidates=["aifs", "ecmwf_aifs025"],
        model_family="AIFS",
        independence_group="AIFS",
        source_type="deterministic_ai",
        is_direct_model=True,
    ),
    "hrrr": _source(
        model_key="hrrr",
        display_name="HRRR Direct",
        enabled_by_default=True,
        priority=60,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["hrrr"],
        model_family="HRRR",
        independence_group="HRRR",
        source_type="deterministic",
        is_direct_model=True,
        expected_update_frequency="hourly",
    ),
    "nbm": _source(
        model_key="nbm",
        display_name="NBM Direct",
        enabled_by_default=True,
        priority=70,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["nbm"],
        model_family="NBM",
        independence_group="NBM",
        source_type="calibrated_blend",
        is_blend=True,
        is_direct_model=True,
        expected_update_frequency="hourly",
        notes="Calibrated blend, not an independent raw dynamical model.",
    ),
    "gfs": _source(
        model_key="gfs",
        display_name="GFS Direct",
        enabled_by_default=True,
        priority=80,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["gfs"],
        model_family="GFS",
        independence_group="GFS",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "rap": _source(
        model_key="rap",
        display_name="RAP Direct",
        enabled_by_default=True,
        priority=90,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["rap"],
        model_family="RAP",
        independence_group="RAP",
        source_type="deterministic",
        is_direct_model=True,
    ),
    "gefs_mean": _source(
        model_key="gefs_mean",
        display_name="GEFS Mean",
        enabled_by_default=False,
        priority=100,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble",
        model_param_candidates=["gfs_seamless", "gfs025"],
        model_family="GEFS",
        independence_group="GEFS",
        source_type="ensemble_mean",
        is_ensemble=True,
        is_direct_model=True,
        notes="Fetched from Open-Meteo ensemble members and averaged by recorder.",
    ),
    "gefs_spread": _source(
        model_key="gefs_spread",
        display_name="GEFS Spread",
        enabled_by_default=False,
        priority=101,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble",
        model_param_candidates=["gfs_seamless", "gfs025"],
        model_family="GEFS",
        independence_group="GEFS",
        source_type="ensemble_spread",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "ecmwf_ens_mean": _source(
        model_key="ecmwf_ens_mean",
        display_name="ECMWF ENS Mean",
        enabled_by_default=False,
        priority=110,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble_mean",
        model_param_candidates=["ecmwf_ifs025"],
        model_family="ECMWF",
        independence_group="ECMWF_ENSEMBLE",
        source_type="ensemble_mean",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "ecmwf_ens_spread": _source(
        model_key="ecmwf_ens_spread",
        display_name="ECMWF ENS Spread",
        enabled_by_default=False,
        priority=111,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble_mean",
        model_param_candidates=["ecmwf_ifs025"],
        model_family="ECMWF",
        independence_group="ECMWF_ENSEMBLE",
        source_type="ensemble_spread",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "aifs_ens_mean": _source(
        model_key="aifs_ens_mean",
        display_name="AIFS ENS Mean",
        enabled_by_default=False,
        priority=120,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble_mean",
        model_param_candidates=["ecmwf_aifs025"],
        model_family="AIFS",
        independence_group="AIFS_ENSEMBLE",
        source_type="ensemble_mean",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "aifs_ens_spread": _source(
        model_key="aifs_ens_spread",
        display_name="AIFS ENS Spread",
        enabled_by_default=False,
        priority=121,
        provider="open_meteo",
        fetcher_type="open_meteo_ensemble_mean",
        endpoint_family="ensemble_mean",
        model_param_candidates=["ecmwf_aifs025"],
        model_family="AIFS",
        independence_group="AIFS_ENSEMBLE",
        source_type="ensemble_spread",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "href_mean": _source(
        model_key="href_mean",
        display_name="HREF Mean",
        enabled_by_default=False,
        priority=130,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["href"],
        model_family="HREF",
        independence_group="HREF",
        source_type="ensemble_mean",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "href_p50": _source(
        model_key="href_p50",
        display_name="HREF P50",
        enabled_by_default=False,
        priority=131,
        provider="noaa_herbie",
        fetcher_type="herbie",
        endpoint_family="herbie",
        model_param_candidates=["href"],
        model_family="HREF",
        independence_group="HREF",
        source_type="ensemble_percentile",
        is_ensemble=True,
        is_direct_model=True,
    ),
    "nws_hourly": _source(
        model_key="nws_hourly",
        display_name="NWS Hourly",
        enabled_by_default=False,
        priority=140,
        provider="nws",
        fetcher_type="nws_api",
        endpoint_family="nws_points",
        model_family="NWS",
        independence_group="NWS",
        source_type="official_forecast",
        is_station_guidance=True,
    ),
    "nws_grid_high": _source(
        model_key="nws_grid_high",
        display_name="NWS Grid High",
        enabled_by_default=False,
        priority=141,
        provider="nws",
        fetcher_type="nws_api",
        endpoint_family="nws_grid",
        model_family="NWS",
        independence_group="NWS",
        source_type="official_forecast",
        is_station_guidance=True,
    ),
    "lamp": _source(
        model_key="lamp",
        display_name="LAMP",
        enabled_by_default=False,
        priority=150,
        provider="noaa_mdl",
        fetcher_type="mdl_text",
        endpoint_family="mdl_text",
        model_family="MOS_LAMP",
        independence_group="MOS_LAMP",
        source_type="station_guidance",
        is_station_guidance=True,
    ),
    "gfs_mos": _source(
        model_key="gfs_mos",
        display_name="GFS MOS",
        enabled_by_default=False,
        priority=151,
        provider="noaa_mdl",
        fetcher_type="mdl_text",
        endpoint_family="mdl_text",
        model_family="MOS_LAMP",
        independence_group="MOS_LAMP",
        source_type="station_guidance",
        is_station_guidance=True,
    ),
    "nam_mos": _source(
        model_key="nam_mos",
        display_name="NAM MOS",
        enabled_by_default=False,
        priority=152,
        provider="noaa_mdl",
        fetcher_type="mdl_text",
        endpoint_family="mdl_text",
        model_family="MOS_LAMP",
        independence_group="MOS_LAMP",
        source_type="station_guidance",
        is_station_guidance=True,
    ),
}

for key, family, group, provider, fetcher, candidates in [
    ("gfs_graphcast", "GFS", "GFS_GRAPHCAST", "open_meteo", "open_meteo_gfs", ["gfs_graphcast025", "gfs_graphcast", "graphcast025", "graphcast"]),
    ("aigfs", "AIGFS", "AIGFS", "open_meteo", "open_meteo_gfs", ["aigfs025", "aigfs"]),
    ("aigefs_mean", "AIGEFS", "AIGEFS", "open_meteo", "open_meteo_ensemble_mean", ["aigefs025", "aigefs"]),
    ("aigefs_spread", "AIGEFS", "AIGEFS", "open_meteo", "open_meteo_ensemble_mean", ["aigefs025", "aigefs"]),
    ("hgefs_mean", "HGEFS", "HGEFS", "open_meteo", "open_meteo_ensemble_mean", ["hgefs025", "hgefs"]),
    ("gem_global", "GEM", "GEM", "open_meteo", "open_meteo", ["gem_global"]),
    ("gem_regional", "GEM", "GEM", "open_meteo", "open_meteo", ["gem_regional"]),
    ("gem_hrdps", "GEM", "GEM_HRDPS", "open_meteo", "open_meteo", ["gem_hrdps"]),
    ("ukmo_global", "UKMO", "UKMO", "open_meteo", "open_meteo", ["ukmo_global"]),
    ("icon_global", "ICON", "ICON", "open_meteo", "open_meteo", ["icon_global"]),
    ("rrfs", "RRFS", "RRFS", "noaa_herbie", "herbie", ["rrfs"]),
    ("rtma_analysis", "RTMA", "RTMA", "noaa_herbie", "herbie", ["rtma"]),
    ("urma_analysis", "URMA", "URMA", "noaa_herbie", "herbie", ["urma"]),
]:
    is_ensemble = key.endswith("_mean") or key.endswith("_spread")
    MODEL_SOURCES[key] = _source(
        model_key=key,
        display_name=key.replace("_", " ").title(),
        enabled_by_default=False,
        priority=200 + len(MODEL_SOURCES),
        provider=provider,
        fetcher_type=fetcher,
        endpoint_family="extended",
        model_param_candidates=candidates,
        model_family=family,
        independence_group=group,
        source_type="ensemble_spread" if key.endswith("_spread") else "ensemble_mean" if is_ensemble else "deterministic",
        is_ensemble=is_ensemble,
        is_direct_model=not key.endswith("_analysis"),
        is_observation_or_analysis=key.endswith("_analysis"),
        is_optional=True,
        notes="Optional extended validation source. Record-only mode attempts this source when selected.",
    )

CURRENT_MODEL_KEYS = [
    "current_weighted_blend",
    "best_match",
    "gfs_seamless",
    "gfs013",
    "gfs_global",
    "nam",
    "nam_conus",
    "ecmwf_ifs",
    "aifs",
    "hrrr",
    "nbm",
    "gfs",
    "rap",
]

CORE_ADDITION_KEYS = [
    "gefs_mean",
    "gefs_spread",
    "ecmwf_ens_mean",
    "ecmwf_ens_spread",
    "aifs_ens_mean",
    "aifs_ens_spread",
    "href_mean",
    "href_p50",
    "nws_hourly",
    "nws_grid_high",
    "lamp",
    "gfs_mos",
    "nam_mos",
]

EXTENDED_ADDITION_KEYS = [
    key
    for key, source in MODEL_SOURCES.items()
    if source.is_optional and key not in CURRENT_MODEL_KEYS and key not in CORE_ADDITION_KEYS
]


def all_model_sources() -> list[ModelSource]:
    return sorted(MODEL_SOURCES.values(), key=lambda source: (source.priority, source.model_key))


def get_model_source(model_key: str) -> ModelSource:
    try:
        return MODEL_SOURCES[model_key]
    except KeyError as exc:
        raise ValueError(f"Unknown model key: {model_key}") from exc


def model_set_keys(model_set: str) -> list[str]:
    normalized = model_set.strip().lower()
    if normalized == "current":
        return list(CURRENT_MODEL_KEYS)
    if normalized == "core":
        return list(dict.fromkeys([*CURRENT_MODEL_KEYS, *CORE_ADDITION_KEYS]))
    if normalized == "extended":
        return list(dict.fromkeys([*CURRENT_MODEL_KEYS, *CORE_ADDITION_KEYS, *EXTENDED_ADDITION_KEYS]))
    if normalized == "all":
        return [source.model_key for source in all_model_sources()]
    raise ValueError("--model-set must be current, core, extended, or all")


def select_model_keys(
    *,
    model_set: str = "current",
    models: str | None = None,
    skip_models: str | None = None,
) -> list[str]:
    if models and models.strip():
        keys = [part.strip() for part in models.split(",") if part.strip()]
    else:
        keys = model_set_keys(model_set)
    skip = {part.strip() for part in (skip_models or "").split(",") if part.strip()}
    unknown = [key for key in keys if key not in MODEL_SOURCES]
    if unknown:
        raise ValueError(f"Unknown model key(s): {', '.join(unknown)}")
    selected = [key for key in keys if key not in skip]
    return sorted(dict.fromkeys(selected), key=lambda key: (MODEL_SOURCES[key].priority, key))


def provider_model_options(model_keys: list[str]) -> tuple[str, str]:
    providers: list[str] = []
    model_ids: list[str] = []
    for key in model_keys:
        source = get_model_source(key)
        provider = "current" if source.fetcher_type == "internal_blend" else source.provider
        if provider in {"internal", "nws", "noaa_mdl"}:
            continue
        if provider not in providers:
            providers.append(provider)
        if key not in model_ids:
            model_ids.append(key)
    if "current_weighted_blend" in model_keys and "current" not in providers:
        providers.insert(0, "current")
        model_ids.insert(0, "current_weighted_blend")
    return ",".join(providers), ",".join(model_ids)


def registry_rows(model_keys: list[str] | None = None) -> list[dict[str, Any]]:
    keys = model_keys or [source.model_key for source in all_model_sources()]
    return [get_model_source(key).to_dict() for key in keys]
