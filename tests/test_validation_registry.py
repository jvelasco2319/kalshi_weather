from __future__ import annotations

from kalshi_weather.model_registry import MODEL_SOURCES, get_model_source, select_model_keys


def test_validation_model_registry_contains_current_core_and_extended_sources() -> None:
    current = {
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
    }
    core = {
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
    }
    optional = {"gfs_graphcast", "aigfs", "rrfs", "rtma_analysis", "urma_analysis"}

    assert current <= MODEL_SOURCES.keys()
    assert core <= MODEL_SOURCES.keys()
    assert optional <= MODEL_SOURCES.keys()
    assert all(MODEL_SOURCES[key].enabled_by_default for key in current)
    assert all(not MODEL_SOURCES[key].enabled_by_default for key in core | optional)


def test_validation_model_registry_metadata_groups_related_sources() -> None:
    for source in MODEL_SOURCES.values():
        assert source.provider
        assert source.model_family
        assert source.independence_group
        assert source.source_type

    assert get_model_source("gfs_seamless").independence_group == "GFS"
    assert get_model_source("gfs013").independence_group == "GFS"
    assert get_model_source("gfs_global").independence_group == "GFS"
    assert get_model_source("gfs").independence_group == "GFS"
    assert get_model_source("nbm").is_blend is True
    assert get_model_source("current_weighted_blend").is_synthetic is True
    assert get_model_source("gefs_mean").is_ensemble is True
    assert get_model_source("href_p50").is_ensemble is True
    assert get_model_source("lamp").is_station_guidance is True


def test_extended_open_meteo_sources_have_attemptable_model_candidates() -> None:
    assert get_model_source("gfs_graphcast").model_param_candidates[0] == "gfs_graphcast025"
    assert get_model_source("aigfs").model_param_candidates[:2] == ["aigfs025", "aigfs"]
    assert get_model_source("gem_global").model_param_candidates == ["gem_global"]
    assert get_model_source("gem_regional").model_param_candidates == ["gem_regional"]
    assert get_model_source("icon_global").model_param_candidates == ["icon_global"]


def test_validation_model_set_selection() -> None:
    current = select_model_keys(model_set="current")
    core = select_model_keys(model_set="core")
    extended = select_model_keys(model_set="extended")

    assert "best_match" in current
    assert "gefs_mean" not in current
    assert "gefs_mean" in core
    assert "gfs_graphcast" not in core
    assert "gfs_graphcast" in extended
    assert select_model_keys(model_set="current", models="hrrr,nbm") == ["hrrr", "nbm"]
    assert "gfs013" not in select_model_keys(model_set="current", skip_models="gfs013")
