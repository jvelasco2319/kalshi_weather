from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pandas as pd
from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.config import load_settings
from kalshi_weather.data.open_meteo_client import OpenMeteoForecastResult
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.lax_high_temp import current_lax_market_date
from kalshi_weather.trading.runner import collect_once


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_phase2_cli_help_lists_new_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "weather-debug" in result.output
    assert "collect-once" in result.output
    assert "join-outcomes" in result.output
    assert "paper-report" in result.output


def test_calibration_report_empty_state(monkeypatch) -> None:
    base = _scratch("calibration-empty")
    try:
        monkeypatch.setenv("SQLITE_PATH", str(base / "paper.sqlite"))
        monkeypatch.setenv("SNAPSHOT_DIR", str(base / "snapshots"))
        result = CliRunner().invoke(app, ["calibration-report"])
        assert result.exit_code == 0
        assert "Need more data" in result.output
        assert "record-outcome" in result.output
    finally:
        rmtree(base, ignore_errors=True)


def test_calibration_report_with_joined_rows(monkeypatch) -> None:
    base = _scratch("calibration-filled")
    try:
        monkeypatch.setenv("SQLITE_PATH", str(base / "paper.sqlite"))
        monkeypatch.setenv("SNAPSHOT_DIR", str(base / "snapshots"))
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        prediction_id = store.save_prediction(
            {
                "market_ticker": "T70",
                "station": "KLAX",
                "market_date": "2026-06-19",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "p_yes": 0.7,
                "model_version": "test-model",
            }
        )
        assert prediction_id
        store.save_official_outcome("KLAX", "2026-06-19", "official_high_f", 70, "manual")
        store.join_predictions_to_outcomes(station="KLAX")

        result = CliRunner().invoke(app, ["calibration-report"])

        assert result.exit_code == 0
        assert "brier_score" in result.output
        assert "by_model_version" in result.output
    finally:
        rmtree(base, ignore_errors=True)


def test_paper_report_empty_and_with_fill(monkeypatch) -> None:
    base = _scratch("paper-report")
    try:
        monkeypatch.setenv("SQLITE_PATH", str(base / "paper.sqlite"))
        monkeypatch.setenv("SNAPSHOT_DIR", str(base / "snapshots"))
        empty = CliRunner().invoke(app, ["paper-report"])
        assert empty.exit_code == 0
        assert "No fake trades were taken" in empty.output

        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_paper_fill(
            {
                "ticker": "T",
                "side": "yes",
                "action": "sell",
                "quantity": Decimal("1"),
                "price": Decimal("0.70"),
                "fee": Decimal("0"),
                "cash_after": Decimal("1000.70"),
                "realized_pnl": Decimal("0.10"),
                "reason": "test",
            }
        )
        filled = CliRunner().invoke(app, ["paper-report"])
        assert filled.exit_code == 0
        assert "'total_paper_fills': 1" in filled.output
    finally:
        rmtree(base, ignore_errors=True)


@dataclass
class FakeKalshi:
    market_ticker: str

    def get_markets(self, _series: str) -> list[dict]:
        today = current_lax_market_date()
        return [
            {
                "ticker": self.market_ticker,
                "title": f"Will the high temp in LA be 70-71 on {today:%b} {today.day}, {today.year}?",
            }
        ]

    def get_multiple_orderbooks(self, tickers: list[str], depth: int = 1) -> dict:
        return {
            ticker: {
                "orderbook_fp": {
                    "yes_dollars": [["0.4000", "1"]],
                    "no_dollars": [["0.5000", "1"]],
                }
            }
            for ticker in tickers
        }


class FakeNWS:
    def station_observations(self, *_args, **_kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "timestamp_utc": [pd.Timestamp(datetime.now(timezone.utc))],
                "temp_f": [70.0],
            }
        )


class FakeOM:
    def forecast_hourly_by_model(self, **_kwargs) -> OpenMeteoForecastResult:
        return OpenMeteoForecastResult(
            frame=pd.DataFrame({"time": [pd.Timestamp("2026-06-19T10:00")], "temperature_2m__m": [71.0]}),
            successful_models=["m"],
            failed_models={},
            fallback_used=False,
            model_maxes_f={"temperature_2m__m": 71.0},
            raw_columns=["time", "temperature_2m__m"],
        )


def test_collect_once_stores_market_weather_and_predictions() -> None:
    base = _scratch("collect-once")
    try:
        settings = load_settings()
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        today = current_lax_market_date()
        ticker = f"KXHIGHLAX-{today.strftime('%y%b').upper()}{today.day}-T70"

        result = collect_once(settings, FakeKalshi(ticker), FakeNWS(), FakeOM(), store, "KXHIGHLAX", "KLAX")

        assert result["stored_predictions"] == 1
        assert store.prediction_count() == 1
        assert store.conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert store.conn.execute("SELECT COUNT(*) FROM weather_snapshots").fetchone()[0] == 1
    finally:
        rmtree(base, ignore_errors=True)
