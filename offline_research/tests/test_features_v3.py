from copy import deepcopy

import pytest

from klax_lab.features_v3 import (
    diagnostic_regime_seed,
    fit_feature_encoder,
    raw_feature_map,
)


def feature_row(index: int = 0, *, missing_ceiling: bool = False) -> dict:
    forecasts = []
    for model, base in (("gfs", 70.0), ("nbm", 72.0), ("hrrr", 73.0)):
        for lead in (8, 14, 20):
            forecasts.append({
                "model": model, "field_id": "temperature_2m", "member_id": None,
                "lead_hours": lead, "value": base + index * .1 + lead * .05,
                "is_missing": False, "as_of_validated": True,
            })
    for lead in (9, 15, 21, 27):
        forecasts.extend((
            {"model": "gefs", "field_id": "temperature_2m", "member_id": "avg",
             "lead_hours": lead, "value": 71 + index * .1 + lead * .04,
             "is_missing": False, "as_of_validated": True},
            {"model": "gefs", "field_id": "temperature_2m", "member_id": "spr",
             "lead_hours": lead, "value": 2 + (index % 3) * .1,
             "is_missing": False, "as_of_validated": True},
        ))
    for field, value in (
        ("total_cloud_cover", 80 - index % 20),
        ("cloud_ceiling", None if missing_ceiling else 900 + index),
        ("wind_u_10m", 2 + index * .01),
        ("wind_v_10m", 4 + index * .02),
        ("mean_sea_level_pressure", 1012 + index * .01),
    ):
        forecasts.append({
            "model": "hrrr", "field_id": field, "member_id": None,
            "lead_hours": 14, "value": value, "is_missing": value is None,
            "as_of_validated": True,
        })
    observations = []
    for station_index, station in enumerate(("KLAX", "KSMO", "KHHR", "KTOA", "KLGB")):
        observations.append({
            "station": station, "temperature_f": 62 + index * .1 + station_index,
            "dewpoint_f": 56 + station_index, "wind_direction_degrees": 250,
            "wind_speed_kt": 7 + index * .01, "pressure_hpa": 1013 - station_index * .2,
            "cloud_ceiling_ft": None if missing_ceiling else 900 + station_index * 100,
            "visibility_miles": 6, "as_of_validated": True,
        })
    return {
        "partition": "weather_training", "climate_date": f"2024-01-{index % 28 + 1:02d}",
        "decision_time_utc": "15:00", "as_of_join_validated": True,
        "contains_settlement_label": False, "forecasts": forecasts,
        "observations": observations, "contracts": [],
    }


def test_raw_features_honor_source_and_feature_surfaces() -> None:
    temperature = raw_feature_map(
        feature_row(), feature_set="temperature_only",
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
    )
    assert "hrrr.temperature_f.max" in temperature
    assert "gefs.temperature_spread_f.mean" in temperature
    assert not any(name.startswith("obs.") for name in temperature)
    local = raw_feature_map(
        feature_row(), feature_set="full_local_weather",
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
    )
    assert "obs.KLAX.cloud_ceiling_ft" in local
    assert "gradient.KLAX_minus_KSMO.pressure_hpa" in local


def test_encoder_fits_imputation_and_constant_removal_on_training_rows_only() -> None:
    rows = [feature_row(index, missing_ceiling=index % 4 == 0) for index in range(40)]
    encoder = fit_feature_encoder(
        rows, feature_set="full_local_weather",
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
    )
    transformed = encoder.transform((feature_row(41, missing_ceiling=True),))[0]
    assert len(transformed) == len(encoder.feature_names)
    assert all(value == value and abs(value) != float("inf") for value in transformed)
    assert encoder.training_rows == 40
    assert encoder.dropped_constant_features


def test_feature_extractor_rejects_labels_and_unaudited_timing() -> None:
    leaked = feature_row()
    leaked["reported_high_f"] = 75
    with pytest.raises(ValueError, match="labels"):
        raw_feature_map(
            leaked, feature_set="temperature_only",
            forecast_source_set="gfs_nbm_hrrr_gefs_summary",
        )
    unaudited = feature_row()
    unaudited["forecasts"][0]["as_of_validated"] = False
    with pytest.raises(ValueError, match="as-of audit"):
        raw_feature_map(
            unaudited, feature_set="temperature_only",
            forecast_source_set="gfs_nbm_hrrr_gefs_summary",
        )


@pytest.mark.parametrize(("changes", "expected"), [
    ({"pressure": 1005, "wind": 16, "cloud": 90}, "frontal_or_precipitation"),
    ({"direction": 70, "wind": 12}, "offshore_or_santa_ana_flow"),
    ({"ceiling": 600, "visibility": 4, "cloud": 90}, "persistent_marine_layer"),
    ({"ceiling": 600, "visibility": 4, "cloud": 30}, "delayed_marine_layer_burnoff"),
    ({"ceiling": 5000, "cloud": 10, "temperature": 88, "wind": 3}, "heat_or_weak_gradient"),
    ({"ceiling": 5000, "cloud": 10, "temperature": 70, "wind": 8}, "ordinary_sea_breeze"),
])
def test_diagnostic_seed_has_all_six_transparent_paths(changes: dict, expected: str) -> None:
    values = {
        "obs.KLAX.cloud_ceiling_ft": 5000.0,
        "obs.KLAX.visibility_miles": 10.0,
        "obs.KLAX.wind_speed_kt": 5.0,
        "obs.KLAX.wind_direction_degrees": 250.0,
        "obs.KLAX.pressure_hpa": 1015.0,
        "obs.KLAX.temperature_f": 70.0,
        "obs.KLAX.dewpoint_f": 50.0,
        "hrrr.cloud_percent.mean": 10.0,
        "hrrr.temperature_f.max": 75.0,
    }
    mapping = {
        "pressure": "obs.KLAX.pressure_hpa", "wind": "obs.KLAX.wind_speed_kt",
        "cloud": "hrrr.cloud_percent.mean", "direction": "obs.KLAX.wind_direction_degrees",
        "ceiling": "obs.KLAX.cloud_ceiling_ft", "visibility": "obs.KLAX.visibility_miles",
        "temperature": "obs.KLAX.temperature_f",
    }
    for key, value in changes.items():
        values[mapping[key]] = value
    assert diagnostic_regime_seed(values) == expected
