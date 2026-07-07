from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
from typer.testing import CliRunner

from kalshi_weather.analysis.temperature_estimates import (
    build_temperature_estimate_payload,
    write_temperature_estimate_artifacts,
)
from kalshi_weather.cli import app
from kalshi_weather.config import load_settings
from kalshi_weather.data.storage import SQLiteStore


def test_temperature_estimate_payload_combines_actual_and_model_points(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "paper.sqlite", tmp_path / "snapshots")
    store.save_official_outcome("KLAX", "2026-06-20", "official_high_f", 71.0, "manual")
    store.save_prediction(
        {
            "asof_utc": "2026-06-20T14:40:00+00:00",
            "station": "KLAX",
            "market_date": "2026-06-20",
            "market_ticker": "KXHIGHLAX-26JUN20-B69.5",
            "p_yes": 0.67,
            "observed_high_so_far_f": 62.6,
            "model_future_high_f": 69.7,
            "model_details_json": {
                "future_max_by_model": {
                    "temperature_2m__gfs_seamless": 70.1,
                    "temperature_2m__gfs013": 69.2,
                }
            },
        }
    )
    actual = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(
                ["2026-06-20T14:00:00+00:00", "2026-06-20T15:00:00+00:00"],
                utc=True,
            ),
            "temp_f": [62.0, 64.0],
        }
    )

    payload = build_temperature_estimate_payload(
        store=store,
        station="KLAX",
        market_date=date(2026, 6, 20),
        actual_observations=actual,
    )

    assert payload["official_high_f"] == 71.0
    assert payload["actual_point_count"] == 2
    assert payload["series_counts"]["production_future_high_f"] == 1
    assert payload["series_counts"]["model_gfs_seamless_future_high_f"] == 1
    assert payload["series_counts"]["model_gfs013_future_high_f"] == 1

    write_temperature_estimate_artifacts(payload, tmp_path / "charts")
    assert (tmp_path / "charts" / "temperature_estimate_series.csv").exists()
    assert (tmp_path / "charts" / "temperature_estimate_payload.json").exists()
    assert (tmp_path / "charts" / "temperature_estimate_summary.txt").exists()
    assert (tmp_path / "charts" / "actual_vs_model_temperatures.png").exists() or (
        tmp_path / "charts" / "actual_vs_model_temperatures.txt"
    ).exists()


def test_temperature_estimate_chart_cli_no_fetch_actual(monkeypatch, tmp_path) -> None:
    settings = replace(
        load_settings(),
        sqlite_path=tmp_path / "paper.sqlite",
        snapshot_dir=tmp_path / "snapshots",
    )
    store = SQLiteStore(settings.sqlite_path, settings.snapshot_dir)
    store.save_prediction(
        {
            "asof_utc": "2026-06-20T14:40:00+00:00",
            "station": "KLAX",
            "market_date": "2026-06-20",
            "market_ticker": "KXHIGHLAX-26JUN20-B69.5",
            "p_yes": 0.67,
            "observed_high_so_far_f": 62.6,
            "model_future_high_f": 69.7,
        }
    )
    monkeypatch.setattr("kalshi_weather.cli.load_settings", lambda: settings)

    result = CliRunner().invoke(
        app,
        [
            "temperature-estimate-chart",
            "--station",
            "KLAX",
            "--date",
            "2026-06-20",
            "--no-fetch-actual",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    assert "Actual temperature points: 0" in result.output
    assert (tmp_path / "out" / "2026-06-20" / "temperature_estimate_summary.txt").exists()
