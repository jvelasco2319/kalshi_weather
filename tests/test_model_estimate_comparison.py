from __future__ import annotations

import sys
from types import ModuleType
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pandas as pd
import pytest
from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.config import load_settings
from kalshi_weather.data.herbie_client import (
    HerbieFetchResult,
    HerbieModelClient,
    NOAA_HERBIE_MODELS,
    convert_temperature_to_f,
    extract_nearest_temperature,
    forecast_hours_for_window,
    nearest_temperature_f,
    normalize_longitude_for_grid,
)
from kalshi_weather.data.open_meteo_client import OpenMeteoForecastResult
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.model_estimates import (
    ModelEstimate,
    current_and_open_meteo_estimates,
    probabilities_for_estimate,
)
from kalshi_weather.schemas import Bracket, OrderbookTop, WeatherSnapshot


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _weather() -> WeatherSnapshot:
    return WeatherSnapshot(
        station_id="KLAX",
        timestamp_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        observed_high_so_far_f=63.0,
        latest_observation_utc=datetime(2026, 6, 20, 17, tzinfo=timezone.utc),
        observation_count=4,
        model_future_high_f=69.3,
        model_details={"selected_future_high_f": 69.3},
    )


def test_current_and_open_meteo_estimates_are_side_by_side() -> None:
    estimates = current_and_open_meteo_estimates(
        station="KLAX",
        market_date=date(2026, 6, 20),
        weather=_weather(),
        model_maxes_f={"temperature_2m__gfs013": 69.2, "temperature_2m__gfs_global": 69.1},
        successful_models=["gfs013", "gfs_global"],
        failed_models={},
    )

    keys = {(estimate.provider, estimate.model_id) for estimate in estimates}

    assert ("current", "current_weighted_blend") in keys
    assert ("open_meteo", "gfs013") in keys
    assert ("open_meteo", "gfs_global") in keys
    assert estimates[0].future_high_f == 69.3
    assert estimates[0].settlement_high_estimate_f == 69.3


def test_separate_model_probability_uses_current_probability_logic() -> None:
    estimate = ModelEstimate(
        asof_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        station="KLAX",
        market_date="2026-06-20",
        provider="current",
        model_id="current_weighted_blend",
        model_name="Current",
        model_family="current",
        run_utc=None,
        cycle_utc=None,
        forecast_window_start_utc=None,
        forecast_window_end_utc=None,
        observed_high_so_far_f=63.0,
        future_high_f=70.0,
        settlement_high_estimate_f=70.0,
    )
    brackets = [Bracket("T69", "69-70", 69, 70), Bracket("T71", "71-72", 71, 72)]
    tops = {
        "T69": OrderbookTop("T69", Decimal("0.30"), Decimal("0.65"), Decimal("0.35"), Decimal("0.70")),
        "T71": OrderbookTop("T71", Decimal("0.10"), Decimal("0.85"), Decimal("0.15"), Decimal("0.90")),
    }

    rows = probabilities_for_estimate(
        estimate,
        brackets,
        tops,
        residual_sigma_f=1.0,
        sample_count=5000,
    )

    assert len(rows) == 2
    assert sum(row.p_yes for row in rows) > 0.99
    assert rows[0].method == "normal_residual_same_as_current_model"
    assert rows[0].yes_edge is not None


def test_model_probability_supports_future_market_without_observed_high() -> None:
    estimate = ModelEstimate(
        asof_utc=datetime(2026, 6, 24, 18, tzinfo=timezone.utc),
        station="KLAX",
        market_date="2026-06-25",
        provider="current",
        model_id="current_weighted_blend",
        model_name="Current",
        model_family="current",
        run_utc=None,
        cycle_utc=None,
        forecast_window_start_utc=None,
        forecast_window_end_utc=None,
        observed_high_so_far_f=None,
        future_high_f=70.0,
        settlement_high_estimate_f=70.0,
    )
    brackets = [Bracket("T69", "69-70", 69, 70), Bracket("T71", "71-72", 71, 72)]
    tops = {
        "T69": OrderbookTop("T69", Decimal("0.30"), Decimal("0.65"), Decimal("0.35"), Decimal("0.70")),
        "T71": OrderbookTop("T71", Decimal("0.10"), Decimal("0.85"), Decimal("0.15"), Decimal("0.90")),
    }

    rows = probabilities_for_estimate(
        estimate,
        brackets,
        tops,
        residual_sigma_f=1.0,
        sample_count=5000,
    )

    assert len(rows) == 2
    assert sum(row.p_yes for row in rows) > 0.99
    assert rows[0].yes_edge is not None


def test_model_estimate_storage_insert_load_and_counts() -> None:
    base = _scratch("model-estimates")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        estimate = current_and_open_meteo_estimates(
            station="KLAX",
            market_date="2026-06-20",
            weather=_weather(),
            model_maxes_f={"temperature_2m__gfs013": 69.2},
            successful_models=["gfs013"],
        )[0]
        estimate_id = store.save_model_estimate(estimate)
        probability_id = store.save_model_estimate_probability(
            {
                "estimate_id": estimate_id,
                "asof_utc": estimate.asof_utc,
                "station": "KLAX",
                "market_date": "2026-06-20",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "T69",
                "bracket_label": "69-70",
                "bracket_lower_f": 69,
                "bracket_upper_f": 70,
                "bracket_type": "range",
                "p_yes": 0.7,
                "yes_edge": Decimal("0.10"),
                "method": "normal_residual_same_as_current_model",
                "residual_sigma_f": 1.0,
            }
        )

        assert estimate_id > 0
        assert probability_id > 0
        assert store.count_model_estimates() == 1
        assert store.count_model_estimate_probabilities() == 1
        assert store.load_model_estimates(station="KLAX")[0]["model_id"] == "current_weighted_blend"
        assert store.load_model_estimate_probabilities(station="KLAX")[0]["p_yes"] == 0.7
    finally:
        rmtree(base, ignore_errors=True)


def test_herbie_missing_dependency_returns_graceful_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(HerbieModelClient, "dependency_available", lambda self: False)
    monkeypatch.setattr(
        "kalshi_weather.data.herbie_client.dependency_status",
        lambda: {"herbie-data": False, "xarray": True, "cfgrib": False, "eccodes": False},
    )
    client = HerbieModelClient()

    rows = client.fetch_estimates(
        station="KLAX",
        market_date="2026-06-20",
        observed_high_so_far_f=63.0,
        forecast_window_start_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        forecast_window_end_utc=datetime(2026, 6, 21, 8, tzinfo=timezone.utc),
        latitude=33.9,
        longitude=-118.4,
        models=["hrrr", "nbm"],
    )

    assert len(rows) == 2
    assert all(not row.successful for row in rows)
    assert "Herbie is not installed" in str(rows[0].error_message)


def test_herbie_unit_conversion_and_longitude_helpers() -> None:
    assert round(convert_temperature_to_f(293.15, "K"), 1) == 68.0
    assert round(convert_temperature_to_f(20.0, "C"), 1) == 68.0
    assert convert_temperature_to_f(68.0, "F") == 68.0
    assert normalize_longitude_for_grid(-118.0, [0.0, 180.0, 242.0]) == 242.0


def test_direct_noaa_model_targets_are_configured() -> None:
    assert NOAA_HERBIE_MODELS["hrrr"]["model"] == "hrrr"
    assert NOAA_HERBIE_MODELS["hrrr"]["product"] == "sfc"
    assert NOAA_HERBIE_MODELS["nbm"]["model"] == "nbm"
    assert NOAA_HERBIE_MODELS["nbm"]["product"] == "co"
    assert NOAA_HERBIE_MODELS["gfs"]["model"] == "gfs"
    assert NOAA_HERBIE_MODELS["gfs"]["product"] == "pgrb2.0p25"
    assert NOAA_HERBIE_MODELS["rap"]["model"] == "rap"
    assert NOAA_HERBIE_MODELS["rap"]["product"] == "awp130pgrb"


def test_forecast_hours_for_remaining_window() -> None:
    hours = forecast_hours_for_window(
        datetime(2026, 6, 20, 12, tzinfo=timezone.utc),
        datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        datetime(2026, 6, 20, 21, tzinfo=timezone.utc),
    )

    assert hours == [6, 7, 8, 9]


def test_extract_nearest_temperature_handles_dataarray() -> None:
    xr = pytest.importorskip("xarray")
    data = xr.DataArray(
        [[20.0, 21.0], [22.0, 23.0]],
        coords={"lat": [33.0, 34.0], "lon": [-119.0, -118.0]},
        dims=("lat", "lon"),
        attrs={"units": "C"},
        name="tmp",
    )

    result = extract_nearest_temperature(data, 33.9, -118.1, forecast_hour=3)

    assert result.variable_name == "tmp"
    assert round(result.value_f, 1) == 73.4
    assert result.forecast_hour == 3


def test_nearest_temperature_f_with_mocked_xarray() -> None:
    xr = pytest.importorskip("xarray")
    data = xr.DataArray(
        [[[293.15, 294.15], [295.15, 296.15]]],
        coords={
            "time": [0],
            "latitude": [33.0, 34.0],
            "longitude": [241.0, 242.0],
        },
        dims=("time", "latitude", "longitude"),
        attrs={"units": "K"},
    )
    dataset = xr.Dataset({"t2m": data})

    value, variable_name, units = nearest_temperature_f(dataset, 33.9, -118.1)

    assert variable_name == "t2m"
    assert units == "K"
    assert round(value, 1) == 73.4


def test_herbie_mocked_result_uses_max_over_remaining_window(monkeypatch) -> None:
    xr = pytest.importorskip("xarray")

    class FakeHerbie:
        def __init__(self, _cycle, **kwargs):
            self.fxx = kwargs["fxx"]
            self.grib = f"fake://hrrr/{self.fxx}"

        def xarray(self, _search):
            value_k = 290.0 + self.fxx
            data = xr.DataArray(
                [[value_k]],
                coords={"latitude": [33.9], "longitude": [241.6]},
                dims=("latitude", "longitude"),
                attrs={"units": "K"},
            )
            return xr.Dataset({"t2m": data})

    module = ModuleType("herbie")
    module.Herbie = FakeHerbie
    monkeypatch.setitem(sys.modules, "herbie", module)

    client = HerbieModelClient(max_forecast_hours=2, max_cycles=1)
    result = client.fetch_one_result(
        station="KLAX",
        market_date="2026-06-20",
        observed_high_so_far_f=60.0,
        forecast_window_start_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        forecast_window_end_utc=datetime(2026, 6, 20, 20, tzinfo=timezone.utc),
        latitude=33.9,
        longitude=-118.4,
        model_id="hrrr",
        max_cycles=1,
    )

    assert result.estimate.successful is True
    assert result.estimate.forecast_hours_used == [0, 1, 2]
    assert round(result.estimate.future_high_f or 0, 1) == round((292.0 - 273.15) * 9 / 5 + 32, 1)


def test_one_direct_noaa_model_failure_does_not_block_others(monkeypatch) -> None:
    client = HerbieModelClient()
    monkeypatch.setattr(HerbieModelClient, "dependency_available", lambda self: True)

    def fake_one(**kwargs):
        if kwargs["model_id"] == "hrrr":
            raise RuntimeError("hrrr unavailable")
        estimate = ModelEstimate(
            asof_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
            station="KLAX",
            market_date="2026-06-20",
            provider="noaa_herbie",
            model_id=kwargs["model_id"],
            model_name=kwargs["model_id"].upper(),
            model_family="NBM",
            run_utc=None,
            cycle_utc=None,
            forecast_window_start_utc=kwargs["forecast_window_start_utc"],
            forecast_window_end_utc=kwargs["forecast_window_end_utc"],
            observed_high_so_far_f=60.0,
            future_high_f=70.0,
            settlement_high_estimate_f=70.0,
        )
        return HerbieFetchResult(estimate=estimate)

    monkeypatch.setattr(client, "fetch_one_result", fake_one)
    rows = client.fetch_estimates(
        station="KLAX",
        market_date="2026-06-20",
        observed_high_so_far_f=60.0,
        forecast_window_start_utc=datetime(2026, 6, 20, 18, tzinfo=timezone.utc),
        forecast_window_end_utc=datetime(2026, 6, 20, 20, tzinfo=timezone.utc),
        latitude=33.9,
        longitude=-118.4,
        models=["hrrr", "nbm"],
    )

    assert rows[0].successful is False
    assert rows[1].successful is True


def test_model_estimates_cli_text_and_json(monkeypatch) -> None:
    payload = {
        "station": "KLAX",
        "market_date": "2026-06-20",
        "observed_high_so_far_f": 63.0,
        "forecast_window_end_utc": "2026-06-21T08:00:00+00:00",
        "current_production_estimate_f": 69.3,
        "estimates": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "future_high_f": 69.3,
                "settlement_high_estimate_f": 69.3,
                "successful": True,
                "error_message": None,
            },
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "future_high_f": 70.1,
                "settlement_high_estimate_f": 70.1,
                "successful": True,
                "error_message": None,
            }
        ],
        "probabilities": [],
        "stored_estimate_ids": {},
        "stored_probability_ids": [],
    }
    monkeypatch.setattr("kalshi_weather.cli._model_estimates_payload", lambda *args, **kwargs: payload)

    text_result = CliRunner().invoke(app, ["model-estimates", "--series", "KXHIGHLAX", "--station", "KLAX"])
    json_result = CliRunner().invoke(
        app,
        ["model-estimates", "--series", "KXHIGHLAX", "--station", "KLAX", "--json"],
    )

    assert text_result.exit_code == 0
    assert "MODEL ESTIMATES" in text_result.output
    assert "noaa_herbie" in text_result.output
    assert "hrrr" in text_result.output
    assert json_result.exit_code == 0
    assert "current_weighted_blend" in json_result.output
    assert "noaa_herbie" in json_result.output


def test_model_probabilities_cli_output(monkeypatch) -> None:
    payload = {
        "station": "KLAX",
        "market_date": "2026-06-20",
        "residual_sigma_f": 1.0,
        "estimates": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "future_high_f": 69.3,
                "settlement_high_estimate_f": 69.3,
            },
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "future_high_f": 70.1,
                "settlement_high_estimate_f": 70.1,
            }
        ],
        "probabilities": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "bracket_label": "69-70",
                "p_yes": 0.7,
                "yes_ask": "0.40",
                "no_ask": "0.60",
                "yes_edge": "0.30",
                "no_edge": "-0.30",
            },
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "bracket_label": "69-70",
                "p_yes": 0.55,
                "yes_ask": "0.40",
                "no_ask": "0.60",
                "yes_edge": "0.15",
                "no_edge": "-0.15",
            }
        ],
        "stored_estimate_ids": {},
        "stored_probability_ids": [],
    }
    monkeypatch.setattr("kalshi_weather.cli._model_estimates_payload", lambda *args, **kwargs: payload)

    result = CliRunner().invoke(app, ["model-probabilities", "--series", "KXHIGHLAX", "--station", "KLAX"])

    assert result.exit_code == 0
    assert "MODEL PROBABILITIES" in result.output
    assert "69-70" in result.output
    assert "noaa_herbie:hrrr" in result.output


def test_direct_noaa_check_text_and_json(monkeypatch) -> None:
    payload = {
        "station": "KLAX",
        "dependencies": {
            "herbie-data": True,
            "xarray": True,
            "cfgrib": True,
            "eccodes": True,
        },
        "model_targets": {
            "hrrr": {"model": "hrrr", "product": "sfc"},
            "nbm": {"model": "nbm", "product": "co"},
            "gfs": {"model": "gfs", "product": "pgrb2.0p25"},
            "rap": {"model": "rap", "product": "awp130pgrb"},
        },
        "live_check": [
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "future_high_f": 70.1,
                "settlement_high_estimate_f": 70.1,
                "successful": True,
                "error_message": None,
            }
        ],
    }
    monkeypatch.setattr("kalshi_weather.cli._direct_noaa_check_payload", lambda *args, **kwargs: payload)

    text_result = CliRunner().invoke(app, ["direct-noaa-check", "--station", "KLAX"])
    json_result = CliRunner().invoke(app, ["direct-noaa-check", "--station", "KLAX", "--json"])

    assert text_result.exit_code == 0
    assert "DIRECT NOAA / HERBIE CHECK" in text_result.output
    assert "model=hrrr, product=sfc" in text_result.output
    assert "70.1 F" in text_result.output
    assert json_result.exit_code == 0
    assert '"dependencies"' in json_result.output
    assert '"noaa_herbie"' in json_result.output


def test_model_provider_probe_handles_failures(monkeypatch) -> None:
    payload = {
        "station": "KLAX",
        "provider_probe": [
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "available": False,
                "dependency_available": False,
                "future_high_f": None,
                "error_message": "Herbie is not installed",
                "next_action": "install optional dependencies",
            }
        ],
    }
    monkeypatch.setattr("kalshi_weather.cli._provider_probe_payload", lambda *args, **kwargs: payload)

    result = CliRunner().invoke(app, ["model-provider-probe", "--station", "KLAX"])

    assert result.exit_code == 0
    assert "Herbie is not installed" in result.output


def test_model_estimate_score_empty_and_scored() -> None:
    base = _scratch("model-score")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        from kalshi_weather.cli import _model_estimate_score_payload

        empty = _model_estimate_score_payload(store, "KLAX")
        assert empty["status"] == "no_scored_model_estimates"

        store.save_model_estimate(
            {
                "asof_utc": "2026-06-19T18:00:00+00:00",
                "station": "KLAX",
                "market_date": "2026-06-19",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "model_name": "Current",
                "model_family": "current",
                "observed_high_so_far_f": 63.0,
                "future_high_f": 69.0,
                "settlement_high_estimate_f": 69.0,
                "successful": True,
            }
        )
        store.save_official_outcome("KLAX", "2026-06-19", "official_high_f", 70.0, "manual")

        scored = _model_estimate_score_payload(store, "KLAX")
        assert scored["scored_count"] == 1
        assert scored["by_model"]["current|current_weighted_blend"]["mae"] == 1.0
    finally:
        rmtree(base, ignore_errors=True)


def test_collect_session_default_does_not_store_model_estimates(monkeypatch, tmp_path) -> None:
    settings = replace(
        load_settings(),
        sqlite_path=tmp_path / "paper.sqlite",
        snapshot_dir=tmp_path / "snapshots",
    )
    store = SQLiteStore(settings.sqlite_path, settings.snapshot_dir)
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    monkeypatch.setattr(
        "kalshi_weather.cli.collect_once_cycle",
        lambda *args, **kwargs: {"stored_predictions": 0, "reason": "test"},
    )

    result = CliRunner().invoke(
        app,
        ["collect-session", "--series", "KXHIGHLAX", "--station", "KLAX", "--max-iterations", "1"],
    )

    assert result.exit_code == 0
    assert store.count_model_estimates() == 0


def test_collect_session_include_model_estimates_stores_sidecar(monkeypatch, tmp_path) -> None:
    settings = replace(
        load_settings(),
        sqlite_path=tmp_path / "paper.sqlite",
        snapshot_dir=tmp_path / "snapshots",
    )
    store = SQLiteStore(settings.sqlite_path, settings.snapshot_dir)
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: store)
    monkeypatch.setattr(
        "kalshi_weather.cli.collect_once_cycle",
        lambda *args, **kwargs: {"stored_predictions": 0, "reason": "test"},
    )

    def fake_payload(*args, **kwargs):
        estimate_id = store.save_model_estimate(
            {
                "asof_utc": "2026-06-20T18:00:00+00:00",
                "station": "KLAX",
                "market_date": "2026-06-20",
                "provider": "current",
                "model_id": "current_weighted_blend",
                "model_name": "Current",
                "model_family": "current",
                "observed_high_so_far_f": 63.0,
                "future_high_f": 69.0,
                "settlement_high_estimate_f": 69.0,
                "successful": True,
            }
        )
        return {"stored_estimate_ids": {"current:current_weighted_blend": estimate_id}, "stored_probability_ids": [], "estimates": []}

    monkeypatch.setattr("kalshi_weather.cli._model_estimates_payload", fake_payload)

    result = CliRunner().invoke(
        app,
        [
            "collect-session",
            "--series",
            "KXHIGHLAX",
            "--station",
            "KLAX",
            "--max-iterations",
            "1",
            "--include-model-estimates",
        ],
    )

    assert result.exit_code == 0
    assert store.count_model_estimates() == 1


def test_open_meteo_estimates_extracted_from_forecast_result() -> None:
    forecast = OpenMeteoForecastResult(
        frame=pd.DataFrame(),
        successful_models=["gfs013"],
        failed_models={},
        fallback_used=False,
        model_maxes_f={"temperature_2m__gfs013": 69.2},
        raw_columns=["temperature_2m__gfs013"],
    )
    estimates = current_and_open_meteo_estimates(
        station="KLAX",
        market_date="2026-06-20",
        weather=_weather(),
        model_maxes_f=forecast.model_maxes_f,
        successful_models=forecast.successful_models,
        failed_models=forecast.failed_models,
    )

    assert any(estimate.provider == "open_meteo" and estimate.model_id == "gfs013" for estimate in estimates)
