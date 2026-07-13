from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ModelSet = Literal["current", "core", "extended"]


@dataclass(frozen=True)
class ModelSource:
    model_key: str
    display_name: str
    enabled_by_default: bool
    priority: int
    provider: str
    fetcher_type: str
    endpoint_family: str
    model_param_candidates: tuple[str | None, ...]
    model_family: str
    independence_group: str
    source_type: str
    is_blend: bool = False
    is_ensemble: bool = False
    is_direct_model: bool = False
    is_station_guidance: bool = False
    is_observation_or_analysis: bool = False
    is_synthetic: bool = False
    is_optional: bool = False
    expected_update_frequency: str | None = None
    forecast_horizon_hint: str | None = None
    preferred_variables: tuple[str, ...] = ("temperature_2m",)
    fallback_variables: tuple[str, ...] = ()
    raw_model_name: str | None = None
    raw_endpoint: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["model_param_candidates"] = list(self.model_param_candidates)
        payload["preferred_variables"] = list(self.preferred_variables)
        payload["fallback_variables"] = list(self.fallback_variables)
        payload["model_set"] = model_set_for_source(self)
        return payload


def _source(
    model_key: str,
    display_name: str,
    priority: int,
    provider: str,
    fetcher_type: str,
    endpoint_family: str,
    model_family: str,
    independence_group: str,
    source_type: str,
    *,
    model_param_candidates: tuple[str | None, ...] = (),
    enabled_by_default: bool = True,
    is_blend: bool = False,
    is_ensemble: bool = False,
    is_direct_model: bool = False,
    is_station_guidance: bool = False,
    is_observation_or_analysis: bool = False,
    is_synthetic: bool = False,
    is_optional: bool = False,
    expected_update_frequency: str | None = None,
    forecast_horizon_hint: str | None = None,
    raw_model_name: str | None = None,
    raw_endpoint: str | None = None,
    notes: str = "",
) -> ModelSource:
    return ModelSource(
        model_key=model_key,
        display_name=display_name,
        enabled_by_default=enabled_by_default,
        priority=priority,
        provider=provider,
        fetcher_type=fetcher_type,
        endpoint_family=endpoint_family,
        model_param_candidates=model_param_candidates,
        model_family=model_family,
        independence_group=independence_group,
        source_type=source_type,
        is_blend=is_blend,
        is_ensemble=is_ensemble,
        is_direct_model=is_direct_model,
        is_station_guidance=is_station_guidance,
        is_observation_or_analysis=is_observation_or_analysis,
        is_synthetic=is_synthetic,
        is_optional=is_optional,
        expected_update_frequency=expected_update_frequency,
        forecast_horizon_hint=forecast_horizon_hint,
        raw_model_name=raw_model_name,
        raw_endpoint=raw_endpoint,
        notes=notes,
    )


_CURRENT_SOURCES: tuple[ModelSource, ...] = (
    _source(
        "current_weighted_blend",
        "Current Weighted Blend",
        10,
        "internal",
        "internal_blend",
        "internal",
        "Blend",
        "InternalBlend",
        "synthetic_blend",
        is_blend=True,
        is_synthetic=True,
        expected_update_frequency="per recorder run",
        notes="Existing weighted blend from successful model estimates.",
    ),
    _source(
        "best_match",
        "Open-Meteo Best Match",
        20,
        "Open-Meteo",
        "open_meteo",
        "forecast",
        "BestMatch",
        "OpenMeteoBestMatch",
        "provider_selection",
        model_param_candidates=("best_match", None),
        is_blend=True,
        expected_update_frequency="provider-selected",
    ),
    _source(
        "gfs_seamless",
        "GFS Seamless",
        30,
        "Open-Meteo",
        "open_meteo_gfs",
        "gfs",
        "GFS",
        "GFS",
        "deterministic_or_provider_seamless",
        model_param_candidates=("gfs_seamless",),
        is_direct_model=True,
    ),
    _source(
        "gfs013",
        "GFS 0.13/0.11",
        31,
        "Open-Meteo",
        "open_meteo_gfs",
        "gfs",
        "GFS",
        "GFS",
        "deterministic",
        model_param_candidates=("gfs013", "gfs_013", "gfs_0p13", "gfs_0p11"),
        is_direct_model=True,
    ),
    _source(
        "gfs_global",
        "GFS Global",
        32,
        "Open-Meteo",
        "open_meteo_gfs",
        "gfs",
        "GFS",
        "GFS",
        "deterministic",
        model_param_candidates=("gfs_global",),
        is_direct_model=True,
    ),
    _source(
        "nam",
        "NAM",
        40,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "NAM",
        "NAM",
        "deterministic",
        model_param_candidates=("nam",),
        is_direct_model=True,
        notes="Direct NOAA NAM through Herbie.",
    ),
    _source(
        "nam_conus",
        "NAM CONUS",
        41,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "NAM",
        "NAM",
        "deterministic",
        model_param_candidates=("nam_conus",),
        is_direct_model=True,
        notes="Direct NOAA NAM CONUS nest through Herbie; canonical strategy aliases to NAM.",
    ),
    _source(
        "ecmwf_ifs",
        "ECMWF IFS",
        50,
        "Open-Meteo",
        "open_meteo_ecmwf",
        "ecmwf",
        "ECMWF",
        "ECMWF_IFS",
        "deterministic",
        model_param_candidates=("ecmwf_ifs", "ecmwf_ifs025"),
        is_direct_model=True,
        expected_update_frequency="6-hourly",
        notes="Registry entry exists even when this repo has only the GFS endpoint configured.",
    ),
    _source(
        "aifs",
        "AIFS",
        51,
        "Open-Meteo",
        "open_meteo_ecmwf",
        "ecmwf",
        "AIFS",
        "AIFS",
        "deterministic_ai",
        model_param_candidates=("aifs", "ecmwf_aifs025"),
        is_direct_model=True,
        expected_update_frequency="6-hourly",
        notes="Registry entry exists even when this repo has only the GFS endpoint configured.",
    ),
    _source(
        "hrrr",
        "HRRR Direct",
        60,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "HRRR",
        "HRRR",
        "deterministic",
        model_param_candidates=("hrrr",),
        is_direct_model=True,
        expected_update_frequency="hourly",
        notes="Direct NOAA model; recorded missing unless direct NOAA wiring is available.",
    ),
    _source(
        "nbm",
        "NBM Direct",
        61,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "NBM",
        "NBM",
        "calibrated_blend",
        model_param_candidates=("nbm",),
        is_blend=True,
        is_direct_model=True,
        expected_update_frequency="hourly",
        notes="Calibrated blend, not an independent dynamical model.",
    ),
    _source(
        "gfs",
        "GFS Direct",
        62,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "GFS",
        "GFS",
        "deterministic",
        model_param_candidates=("gfs",),
        is_direct_model=True,
        expected_update_frequency="6-hourly",
    ),
    _source(
        "rap",
        "RAP Direct",
        63,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "RAP",
        "RAP",
        "deterministic",
        model_param_candidates=("rap",),
        is_direct_model=True,
        expected_update_frequency="hourly",
    ),
)


_CORE_ADDITIONS: tuple[ModelSource, ...] = (
    _source(
        "gefs_mean",
        "GEFS Mean",
        100,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "GEFS",
        "GEFS",
        "ensemble_mean",
        model_param_candidates=("gefs",),
        enabled_by_default=False,
        is_ensemble=True,
        is_direct_model=True,
        expected_update_frequency="6-hourly",
        notes="High-priority ensemble mean; missing until a GEFS fetcher is wired.",
    ),
    _source(
        "gefs_spread",
        "GEFS Spread",
        101,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "GEFS",
        "GEFS",
        "ensemble_spread",
        model_param_candidates=("gefs",),
        enabled_by_default=False,
        is_ensemble=True,
        is_direct_model=True,
        expected_update_frequency="6-hourly",
    ),
    _source(
        "ecmwf_ens_mean",
        "ECMWF ENS Mean",
        110,
        "Open-Meteo",
        "open_meteo_ensemble_mean",
        "ensemble_mean",
        "ECMWF",
        "ECMWF_ENSEMBLE",
        "ensemble_mean",
        model_param_candidates=("ecmwf_ifs025",),
        enabled_by_default=False,
        is_ensemble=True,
    ),
    _source(
        "ecmwf_ens_spread",
        "ECMWF ENS Spread",
        111,
        "Open-Meteo",
        "open_meteo_ensemble_mean",
        "ensemble_mean",
        "ECMWF",
        "ECMWF_ENSEMBLE",
        "ensemble_spread",
        model_param_candidates=("ecmwf_ifs025",),
        enabled_by_default=False,
        is_ensemble=True,
    ),
    _source(
        "aifs_ens_mean",
        "AIFS ENS Mean",
        112,
        "Open-Meteo",
        "open_meteo_ensemble_mean",
        "ensemble_mean",
        "AIFS",
        "AIFS_ENSEMBLE",
        "ensemble_mean",
        model_param_candidates=("ecmwf_aifs025",),
        enabled_by_default=False,
        is_ensemble=True,
    ),
    _source(
        "aifs_ens_spread",
        "AIFS ENS Spread",
        113,
        "Open-Meteo",
        "open_meteo_ensemble_mean",
        "ensemble_mean",
        "AIFS",
        "AIFS_ENSEMBLE",
        "ensemble_spread",
        model_param_candidates=("ecmwf_aifs025",),
        enabled_by_default=False,
        is_ensemble=True,
    ),
    _source(
        "href_mean",
        "HREF Mean",
        120,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "HREF",
        "HREF",
        "ensemble_mean",
        model_param_candidates=("href",),
        enabled_by_default=False,
        is_ensemble=True,
        is_direct_model=True,
    ),
    _source(
        "href_p50",
        "HREF P50",
        121,
        "NOAA/Herbie",
        "herbie",
        "herbie",
        "HREF",
        "HREF",
        "ensemble_percentile",
        model_param_candidates=("href",),
        enabled_by_default=False,
        is_ensemble=True,
        is_direct_model=True,
    ),
    _source(
        "nws_hourly",
        "NWS Hourly",
        130,
        "NWS API",
        "nws_api",
        "nws_points",
        "NWS",
        "NWSForecast",
        "official_forecast",
        enabled_by_default=False,
        is_station_guidance=True,
    ),
    _source(
        "nws_grid_high",
        "NWS Grid High",
        131,
        "NWS API",
        "nws_api",
        "nws_grid",
        "NWS",
        "NWSForecast",
        "official_grid_forecast",
        enabled_by_default=False,
        is_station_guidance=True,
    ),
    _source(
        "lamp",
        "LAMP",
        140,
        "NOAA/MDL",
        "mdl_text",
        "mdl",
        "LAMP",
        "MOS_LAMP",
        "station_guidance",
        enabled_by_default=False,
        is_station_guidance=True,
    ),
    _source(
        "gfs_mos",
        "GFS MOS",
        141,
        "NOAA/MDL",
        "mdl_text",
        "mdl",
        "MOS",
        "MOS_LAMP",
        "station_guidance",
        enabled_by_default=False,
        is_station_guidance=True,
    ),
    _source(
        "nam_mos",
        "NAM MOS",
        142,
        "NOAA/MDL",
        "mdl_text",
        "mdl",
        "MOS",
        "MOS_LAMP",
        "station_guidance",
        enabled_by_default=False,
        is_station_guidance=True,
    ),
)


_EXTENDED_ADDITIONS: tuple[ModelSource, ...] = (
    _source("gfs_graphcast", "GFS GraphCast", 200, "Open-Meteo", "open_meteo_gfs", "gfs", "GraphCast", "GraphCast", "ai_forecast", model_param_candidates=("gfs_graphcast", "graphcast"), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("aigfs", "AIGFS", 201, "Open-Meteo", "open_meteo_gfs", "gfs", "AIGFS", "AIGFS", "ai_forecast", model_param_candidates=("aigfs", "aigfs025"), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("aigefs_mean", "AIGEFS Mean", 202, "Open-Meteo", "open_meteo_ensemble_mean", "ensemble_mean", "AIGEFS", "AIGEFS", "ensemble_mean", model_param_candidates=("aigefs", "aigefs025"), enabled_by_default=False, is_ensemble=True, is_optional=True),
    _source("aigefs_spread", "AIGEFS Spread", 203, "Open-Meteo", "open_meteo_ensemble_mean", "ensemble_mean", "AIGEFS", "AIGEFS", "ensemble_spread", model_param_candidates=("aigefs", "aigefs025"), enabled_by_default=False, is_ensemble=True, is_optional=True),
    _source("hgefs_mean", "HGEFS Mean", 204, "Open-Meteo", "open_meteo_gfs", "gfs", "HGEFS", "HGEFS", "ensemble_mean", model_param_candidates=("hgefs", "hgefs025"), enabled_by_default=False, is_ensemble=True, is_optional=True),
    _source("gem_global", "GEM Global", 210, "Open-Meteo", "open_meteo", "forecast", "GEM", "GEM", "deterministic", model_param_candidates=("gem_global",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("gem_regional", "GEM Regional", 211, "Open-Meteo", "open_meteo", "forecast", "GEM", "GEM", "deterministic", model_param_candidates=("gem_regional",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("gem_hrdps", "GEM HRDPS", 212, "Open-Meteo", "open_meteo", "forecast", "GEM", "GEM_HRDPS", "deterministic", model_param_candidates=("gem_hrdps",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("ukmo_global", "UKMO Global", 220, "Open-Meteo", "open_meteo", "forecast", "UKMO", "UKMO", "deterministic", model_param_candidates=("ukmo_global",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("icon_global", "ICON Global", 221, "Open-Meteo", "open_meteo", "forecast", "ICON", "ICON", "deterministic", model_param_candidates=("icon_global",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("rrfs", "RRFS", 230, "NOAA/Herbie", "herbie", "herbie", "RRFS", "RRFS", "deterministic", model_param_candidates=("rrfs",), enabled_by_default=False, is_direct_model=True, is_optional=True),
    _source("rtma_analysis", "RTMA Analysis", 240, "NOAA/Herbie", "herbie", "herbie", "RTMA", "RTMA_URMA", "analysis", model_param_candidates=("rtma",), enabled_by_default=False, is_observation_or_analysis=True, is_optional=True),
    _source("urma_analysis", "URMA Analysis", 241, "NOAA/Herbie", "herbie", "herbie", "URMA", "RTMA_URMA", "analysis", model_param_candidates=("urma",), enabled_by_default=False, is_observation_or_analysis=True, is_optional=True),
)

MODEL_SOURCES: dict[str, ModelSource] = {
    source.model_key: source
    for source in (*_CURRENT_SOURCES, *_CORE_ADDITIONS, *_EXTENDED_ADDITIONS)
}


def model_set_for_source(source: ModelSource) -> str:
    if source.model_key in {item.model_key for item in _CURRENT_SOURCES}:
        return "current"
    if source.model_key in {item.model_key for item in _CORE_ADDITIONS}:
        return "core"
    return "extended"


def all_model_sources() -> list[ModelSource]:
    return sorted(MODEL_SOURCES.values(), key=lambda item: item.priority)


def get_model_source(model_key: str) -> ModelSource:
    try:
        return MODEL_SOURCES[model_key]
    except KeyError as exc:
        raise ValueError(f"Unknown model key: {model_key}") from exc


def parse_model_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def model_set_keys(model_set: str) -> list[str]:
    if model_set not in {"current", "core", "extended"}:
        raise ValueError("model_set must be one of: current, core, extended")
    keys = [source.model_key for source in _CURRENT_SOURCES]
    if model_set in {"core", "extended"}:
        keys.extend(source.model_key for source in _CORE_ADDITIONS)
    if model_set == "extended":
        keys.extend(source.model_key for source in _EXTENDED_ADDITIONS)
    return keys


def select_model_keys(
    model_set: str = "current",
    models: str | list[str] | None = None,
    skip_models: str | list[str] | None = None,
) -> list[str]:
    requested = parse_model_list(models) if isinstance(models, str) else models
    skipped = parse_model_list(skip_models) if isinstance(skip_models, str) else skip_models
    keys = list(requested) if requested is not None else model_set_keys(model_set)
    unknown = [key for key in keys if key not in MODEL_SOURCES]
    if unknown:
        raise ValueError(f"Unknown model key(s): {', '.join(unknown)}")
    skip_set = set(skipped or [])
    unknown_skips = [key for key in skip_set if key not in MODEL_SOURCES]
    if unknown_skips:
        raise ValueError(f"Unknown skip model key(s): {', '.join(sorted(unknown_skips))}")
    return [key for key in keys if key not in skip_set]


def open_meteo_model_keys(model_keys: list[str]) -> list[str]:
    """Return model keys this repo can attempt through its existing Open-Meteo client."""
    supported_fetchers = {
        "open_meteo",
        "open_meteo_gfs",
        "open_meteo_ecmwf",
    }
    return [
        key
        for key in model_keys
        if MODEL_SOURCES[key].fetcher_type in supported_fetchers
        and MODEL_SOURCES[key].model_param_candidates
    ]


def open_meteo_params_for_keys(model_keys: list[str]) -> list[str]:
    params: list[str] = []
    for key in open_meteo_model_keys(model_keys):
        for candidate in MODEL_SOURCES[key].model_param_candidates:
            if candidate is None:
                if "best_match" not in params:
                    params.append("best_match")
                continue
            if candidate not in params:
                params.append(candidate)
                break
    return params


def registry_rows(model_keys: list[str] | None = None) -> list[dict[str, object]]:
    keys = model_keys or [source.model_key for source in all_model_sources()]
    return [get_model_source(key).to_dict() for key in keys]
