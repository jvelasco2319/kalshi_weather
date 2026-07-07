from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from kalshi_weather import cli
from kalshi_weather.cli import app
from kalshi_weather.schemas import WeatherSnapshot


def _summary_payload(**overrides):
    payload = {
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-20",
        "asof_utc": "2026-06-20T15:31:00+00:00",
        "source": "live_read_only",
        "observed_high_so_far_f": 63.0,
        "latest_observation_utc": "2026-06-20T15:15:00+00:00",
        "current_production_estimate_f": 69.3,
        "consensus_estimate_f": 69.3,
        "model_min_estimate_f": 69.2,
        "model_max_estimate_f": 69.4,
        "model_spread_f": 0.2,
        "model_agreement_status": "HIGH",
        "most_likely_bracket_by_current_model": "69-70",
        "top_probability_by_current_model": 0.679,
        "data_readiness_status": "SMOKE_TEST_ONLY",
        "unique_joined_market_dates": 1,
        "brackets": ["<67", "67-68", "69-70", "71-72", ">74"],
        "models": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "model": "current_weighted_blend",
                "matrix_model": "current_weighted_blend",
                "estimated_high_f": 69.3,
                "most_likely_bracket": "69-70",
                "top_probability": 0.679,
                "probabilities": {"<67": 0.002, "67-68": 0.204, "69-70": 0.679, "71-72": 0.114, ">74": 0.0},
                "status": "ok",
                "error": None,
            },
            {
                "provider": "open_meteo",
                "model_id": "gfs013",
                "model": "open_meteo:gfs013",
                "matrix_model": "gfs013",
                "estimated_high_f": 69.2,
                "most_likely_bracket": "69-70",
                "top_probability": 0.669,
                "probabilities": {"<67": 0.003, "67-68": 0.214, "69-70": 0.669, "71-72": 0.113, ">74": 0.001},
                "status": "ok",
                "error": None,
            },
            {
                "provider": "noaa_herbie",
                "model_id": "hrrr",
                "model": "noaa_herbie:hrrr",
                "matrix_model": "noaa_herbie:hrrr",
                "estimated_high_f": None,
                "most_likely_bracket": "--",
                "top_probability": None,
                "probabilities": {},
                "status": "unavailable",
                "error": "Herbie is not installed",
            },
        ],
        "market_view": {"included": False},
        "warnings": [
            "SMOKE_TEST_ONLY - 1 joined market date(s), enough to verify plumbing, not enough to trust edge.",
            "This is analysis-only, not a live trading signal.",
        ],
        "warning_summary": [],
        "next_action": "Continue collecting snapshots and join official outcomes after settlement.",
        "live_trading_enabled": False,
        "paper_trading": False,
    }
    payload.update(overrides)
    return payload


def _patch_summary(monkeypatch, payload=None):
    monkeypatch.setattr(cli, "_simple_summary_payload", lambda *args, **kwargs: payload or _summary_payload())


def test_simple_summary_text_is_concise(monkeypatch):
    _patch_summary(monkeypatch)

    result = CliRunner().invoke(app, ["simple-summary", "--series", "KXHIGHLAX", "--station", "KLAX"])

    assert result.exit_code == 0
    assert "Observed high so far" in result.output
    assert "Current production estimate" in result.output
    assert "MODEL HIGH ESTIMATES" in result.output
    assert "PROBABILITIES BY MODEL" in result.output
    assert "WARNINGS" in result.output
    assert "67.9%" in result.output
    assert "raw_columns" not in result.output
    assert "WeatherSnapshot(" not in result.output


def test_model_summary_alias(monkeypatch):
    _patch_summary(monkeypatch)

    result = CliRunner().invoke(app, ["model-summary", "--series", "KXHIGHLAX", "--station", "KLAX"])

    assert result.exit_code == 0
    assert "KLAX HIGH TEMP MODEL SUMMARY" in result.output


def test_simple_summary_json_schema(monkeypatch):
    _patch_summary(monkeypatch)

    result = CliRunner().invoke(app, ["simple-summary", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["series"] == "KXHIGHLAX"
    assert data["model_agreement_status"] == "HIGH"
    assert data["data_readiness_status"] == "SMOKE_TEST_ONLY"
    assert data["models"][0]["probabilities"]["69-70"] == 0.679


def test_simple_summary_csv(monkeypatch):
    _patch_summary(monkeypatch)

    result = CliRunner().invoke(app, ["simple-summary", "--csv"])

    assert result.exit_code == 0
    assert "model,provider,model_id" in result.output
    assert "current_weighted_blend,current,current_weighted_blend" in result.output
    assert "69-70,0.679,67.9%" in result.output


def test_agreement_status_bands():
    assert cli._agreement_status(0.2) == "HIGH"
    assert cli._agreement_status(1.5) == "MEDIUM"
    assert cli._agreement_status(2.5) == "LOW"


def test_compact_bracket_labels_sort_naturally():
    rows = [
        {"bracket_lower_f": 75, "bracket_upper_f": None, "bracket_label": "75 or above"},
        {"bracket_lower_f": None, "bracket_upper_f": 66, "bracket_label": "66 or below"},
        {"bracket_lower_f": 69, "bracket_upper_f": 70, "bracket_label": "69-70"},
    ]
    labels = [cli._compact_bracket_label(row) for row in sorted(rows, key=cli._bracket_sort_key)]

    assert labels == ["<67", "69-70", ">74"]


def test_show_prices_adds_market_view(monkeypatch):
    payload = _summary_payload(
        market_view={
            "included": True,
            "required_hurdle": "0.08",
            "top_mismatches": [
                {
                    "bracket": "69-70",
                    "side": "YES",
                    "model_probability": 0.679,
                    "market_ask": "0.33",
                    "edge": "0.349",
                    "would_trade": True,
                    "note": "apparent edge",
                }
            ],
        }
    )
    _patch_summary(monkeypatch, payload)

    result = CliRunner().invoke(app, ["simple-summary", "--show-prices", "--show-edges"])

    assert result.exit_code == 0
    assert "OPTIONAL MARKET VIEW" in result.output
    assert "apparent edge" in result.output


def test_collect_session_default_is_concise(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        default_series="KXHIGHLAX",
        default_station="KLAX",
        kalshi_enable_real_orders=False,
    )
    weather = WeatherSnapshot(
        station_id="KLAX",
        timestamp_utc=datetime(2026, 6, 20, 15, 22, tzinfo=timezone.utc),
        observed_high_so_far_f=63.0,
        latest_observation_utc=datetime(2026, 6, 20, 15, 15, tzinfo=timezone.utc),
        observation_count=4,
        model_future_high_f=69.3,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_kalshi", lambda settings: object())
    monkeypatch.setattr(cli, "_nws", lambda settings: object())
    monkeypatch.setattr(cli, "_open_meteo", lambda settings: object())
    monkeypatch.setattr(cli, "_store", lambda settings: object())
    monkeypatch.setattr(cli, "timestamped_report_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(
        cli,
        "collect_once_cycle",
        lambda *args, **kwargs: {
            "weather": weather,
            "latest_observed_temp_f": 62.4,
            "stored_predictions": 6,
            "open_meteo": {"successful_models": ["gfs013"], "failed_models": {}},
        },
    )

    result = CliRunner().invoke(app, ["collect-session", "--max-iterations", "1"])

    assert result.exit_code == 0
    assert "COLLECT SESSION" in result.output
    assert "Stored predictions: 6" in result.output
    assert "WeatherSnapshot(" not in result.output


def test_collect_session_debug_json_preserves_detail(monkeypatch, tmp_path):
    settings = SimpleNamespace(default_series="KXHIGHLAX", default_station="KLAX", kalshi_enable_real_orders=False)
    weather = WeatherSnapshot(
        station_id="KLAX",
        timestamp_utc=datetime(2026, 6, 20, 15, 22, tzinfo=timezone.utc),
        observed_high_so_far_f=63.0,
        latest_observation_utc=None,
        observation_count=4,
        model_future_high_f=69.3,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_kalshi", lambda settings: object())
    monkeypatch.setattr(cli, "_nws", lambda settings: object())
    monkeypatch.setattr(cli, "_open_meteo", lambda settings: object())
    monkeypatch.setattr(cli, "_store", lambda settings: object())
    monkeypatch.setattr(cli, "timestamped_report_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(
        cli,
        "collect_once_cycle",
        lambda *args, **kwargs: {"weather": weather, "stored_predictions": 6, "open_meteo": {}},
    )

    result = CliRunner().invoke(app, ["collect-session", "--max-iterations", "1", "--debug-json"])

    assert result.exit_code == 0
    assert "WeatherSnapshot(" in result.output
    assert '"results"' in result.output


def test_collect_loop_default_is_concise(monkeypatch):
    settings = SimpleNamespace(
        default_series="KXHIGHLAX",
        default_station="KLAX",
        kalshi_enable_real_orders=False,
    )
    weather = WeatherSnapshot(
        station_id="KLAX",
        timestamp_utc=datetime(2026, 6, 22, 13, 55, tzinfo=timezone.utc),
        observed_high_so_far_f=63.0,
        latest_observation_utc=datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc),
        observation_count=73,
        model_future_high_f=70.17,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_kalshi", lambda settings: object())
    monkeypatch.setattr(cli, "_nws", lambda settings: object())
    monkeypatch.setattr(cli, "_open_meteo", lambda settings: object())
    monkeypatch.setattr(cli, "_store", lambda settings: object())
    monkeypatch.setattr(
        cli,
        "collect_once_cycle",
        lambda *args, **kwargs: {
            "weather": weather,
            "latest_observed_temp_f": 62.4,
            "stored_predictions": 6,
            "market_count": 6,
            "open_meteo": {
                "successful_models": ["gfs_seamless"],
                "failed_models": {},
                "model_maxes_f": {"temperature_2m__gfs_seamless": 70.6},
            },
        },
    )

    result = CliRunner().invoke(app, ["collect-loop", "--max-iterations", "1"])

    assert result.exit_code == 0
    assert "iter 1" in result.output
    assert "actual 62.4 F" in result.output
    assert "estimate 70.2 F" in result.output
    assert "gfs_seamless 70.6 F" in result.output
    assert "WeatherSnapshot(" not in result.output
    assert "raw_columns" not in result.output


def test_collect_loop_debug_json_preserves_detail(monkeypatch):
    settings = SimpleNamespace(
        default_series="KXHIGHLAX",
        default_station="KLAX",
        kalshi_enable_real_orders=False,
    )
    weather = WeatherSnapshot(
        station_id="KLAX",
        timestamp_utc=datetime(2026, 6, 22, 13, 55, tzinfo=timezone.utc),
        observed_high_so_far_f=63.0,
        latest_observation_utc=datetime(2026, 6, 22, 13, 30, tzinfo=timezone.utc),
        observation_count=73,
        model_future_high_f=70.17,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "_kalshi", lambda settings: object())
    monkeypatch.setattr(cli, "_nws", lambda settings: object())
    monkeypatch.setattr(cli, "_open_meteo", lambda settings: object())
    monkeypatch.setattr(cli, "_store", lambda settings: object())
    monkeypatch.setattr(
        cli,
        "collect_once_cycle",
        lambda *args, **kwargs: {"weather": weather, "stored_predictions": 6, "open_meteo": {}},
    )

    result = CliRunner().invoke(app, ["collect-loop", "--max-iterations", "1", "--debug-json"])

    assert result.exit_code == 0
    assert "WeatherSnapshot(" in result.output
    assert "'iteration': 1" in result.output


def test_weather_summary_is_concise(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_weather_summary_payload",
        lambda settings, station: {
            "station": station,
            "observed_high_so_far_f": 63.0,
            "latest_observation_utc": "2026-06-20T15:15:00+00:00",
            "current_production_estimate_f": 69.3,
            "open_meteo_models": [{"model": "gfs013", "future_high_f": 69.2}],
            "failed_models": {},
            "fallback_used": False,
            "feature_notes": {
                "low_cloud_max_pct": 100,
                "shortwave_radiation_max": 1026,
                "wind_speed_10m_mean": 10.1,
            },
            "status": "ok",
        },
    )

    result = CliRunner().invoke(app, ["weather-summary", "--station", "KLAX"])

    assert result.exit_code == 0
    assert "WEATHER SUMMARY - KLAX" in result.output
    assert "Open-Meteo models" in result.output
    assert "raw_columns" not in result.output


def test_simple_summary_output_file_by_extension(monkeypatch, tmp_path: Path):
    _patch_summary(monkeypatch)
    output = tmp_path / "summary.csv"

    result = CliRunner().invoke(app, ["simple-summary", "--output", str(output)])

    assert result.exit_code == 0
    assert output.read_text(encoding="utf-8").startswith("model,provider,model_id")
