from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.config import load_settings
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.lax_high_temp import latest_settled_lax_market_date
from kalshi_weather.validation import model_vs_market_payload


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _set_store_env(monkeypatch, base: Path) -> None:
    monkeypatch.setenv("SQLITE_PATH", str(base / "paper.sqlite"))
    monkeypatch.setenv("SNAPSHOT_DIR", str(base / "snapshots"))


def _prediction(
    idx: int,
    market_date: date,
    p_yes: float,
    lower: int,
    upper: int,
    yes_bid: str | None = "0.45",
    yes_ask: str | None = "0.55",
) -> dict[str, object]:
    return {
        "asof_utc": f"{market_date.isoformat()}T18:00:00+00:00",
        "series": "KXHIGHLAX",
        "market_ticker": f"KXHIGHLAX-{market_date:%Y%m%d}-{idx}",
        "station": "KLAX",
        "market_date": market_date,
        "bracket_label": f"{lower}-{upper}",
        "bracket_lower_f": lower,
        "bracket_upper_f": upper,
        "bracket_type": "range",
        "p_yes": p_yes,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": "0.45" if yes_bid is not None else None,
        "no_ask": "0.55" if yes_ask is not None else None,
        "observed_high_so_far_f": 69.0,
        "model_future_high_f": 70.0,
        "residual_sigma_f": 1.0,
        "monte_carlo_samples": 100,
        "model_version": "test-model",
    }


def _populate_joined(store: SQLiteStore, model_good: bool = True, missing_market: bool = False) -> None:
    start = latest_settled_lax_market_date() - timedelta(days=10)
    idx = 0
    for day_offset in range(5):
        market_date = start + timedelta(days=day_offset)
        store.save_official_outcome("KLAX", market_date, "official_high_f", 70.0, "manual")
        for row_offset in range(6):
            yes_outcome = row_offset % 2 == 0
            if model_good:
                p_yes = 0.9 if yes_outcome else 0.1
            else:
                p_yes = 0.1 if yes_outcome else 0.9
            lower, upper = (70, 70) if yes_outcome else (60, 60)
            store.save_prediction(
                _prediction(
                    idx,
                    market_date,
                    p_yes,
                    lower,
                    upper,
                    yes_bid=None if missing_market else "0.45",
                    yes_ask=None if missing_market else "0.55",
                )
            )
            idx += 1
    store.join_predictions_to_outcomes(station="KLAX", overwrite=True)


def test_external_summary_script_is_not_required(monkeypatch) -> None:
    base = _scratch("no-external-summary")
    try:
        _set_store_env(monkeypatch, base)
        removed_script = "_".join(["summarize", "results"]) + "_" + "ol" + "lama_cloud.ps1"
        script = Path("scripts") / removed_script

        result = CliRunner().invoke(app, ["model-health", "--station", "KLAX"])

        assert result.exit_code == 0
        assert not script.exists()
        assert "KALSHI WEATHER MODEL HEALTH" in result.output
    finally:
        rmtree(base, ignore_errors=True)


def test_model_health_empty_db(monkeypatch) -> None:
    base = _scratch("health-empty")
    try:
        _set_store_env(monkeypatch, base)

        result = CliRunner().invoke(app, ["model-health", "--series", "KXHIGHLAX", "--station", "KLAX"])

        assert result.exit_code == 0
        assert "NOT READY TO JUDGE" in result.output
        assert "No statistical conclusion" in result.output or "not enough" in result.output.lower()
    finally:
        rmtree(base, ignore_errors=True)


def test_model_health_predictions_zero_outcomes(monkeypatch) -> None:
    base = _scratch("health-predictions")
    try:
        _set_store_env(monkeypatch, base)
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_prediction(_prediction(1, date.today(), 0.5, 69, 71))

        result = CliRunner().invoke(app, ["model-health", "--station", "KLAX", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["overall_status"] == "NOT READY TO JUDGE"
        assert payload["data_readiness"]["total_predictions"] == 1
    finally:
        rmtree(base, ignore_errors=True)


def test_model_health_joined_rows_has_calibration(monkeypatch) -> None:
    base = _scratch("health-joined")
    try:
        _set_store_env(monkeypatch, base)
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        _populate_joined(store, model_good=True)

        result = CliRunner().invoke(app, ["model-health", "--station", "KLAX", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["calibration"]["joined_row_count"] == 30
        assert payload["model_vs_market"]["model_minus_market_brier"] > 0
    finally:
        rmtree(base, ignore_errors=True)


def test_model_health_json_output_file(monkeypatch) -> None:
    base = _scratch("health-json")
    try:
        _set_store_env(monkeypatch, base)
        output = base / "health.json"

        result = CliRunner().invoke(app, ["model-health", "--station", "KLAX", "--json", "--output", str(output)])

        assert result.exit_code == 0
        assert json.loads(output.read_text(encoding="utf-8"))["station"] == "KLAX"
    finally:
        rmtree(base, ignore_errors=True)


def test_model_vs_market_model_better_and_market_better() -> None:
    base = _scratch("market-benchmark")
    try:
        good_store = SQLiteStore(base / "good.sqlite", base / "good_snapshots")
        bad_store = SQLiteStore(base / "bad.sqlite", base / "bad_snapshots")
        _populate_joined(good_store, model_good=True)
        _populate_joined(bad_store, model_good=False)

        good = model_vs_market_payload(good_store, "KLAX", series="KXHIGHLAX")
        bad = model_vs_market_payload(bad_store, "KLAX", series="KXHIGHLAX")

        assert good["model_minus_market_brier"] > 0
        assert bad["model_minus_market_brier"] < 0
    finally:
        rmtree(base, ignore_errors=True)


def test_model_vs_market_missing_market_prices() -> None:
    base = _scratch("market-missing")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        _populate_joined(store, model_good=True, missing_market=True)

        payload = model_vs_market_payload(store, "KLAX", series="KXHIGHLAX")

        assert payload["status"] == "NOT_AVAILABLE"
        assert payload["rows_skipped_missing_market"] == 30
    finally:
        rmtree(base, ignore_errors=True)


def test_calibration_readiness_states(monkeypatch) -> None:
    base = _scratch("readiness")
    try:
        _set_store_env(monkeypatch, base)
        empty = CliRunner().invoke(app, ["calibration-readiness", "--station", "KLAX", "--json"])
        assert empty.exit_code == 0
        assert json.loads(empty.output)["readiness_level"] == "PLUMBING_ONLY"

        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_prediction(_prediction(1, date.today() + timedelta(days=365), 0.5, 69, 71))
        waiting = CliRunner().invoke(app, ["calibration-readiness", "--station", "KLAX", "--json"])
        assert json.loads(waiting.output)["readiness_level"] == "WAITING_FOR_SETTLEMENT"
    finally:
        rmtree(base, ignore_errors=True)


def test_outcome_workflow_unsettled_skip_message(monkeypatch) -> None:
    base = _scratch("unsettled-outcome")
    try:
        _set_store_env(monkeypatch, base)
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_prediction(_prediction(1, date.today() + timedelta(days=365), 0.5, 69, 71))

        result = CliRunner().invoke(app, ["fetch-missing-outcomes", "--station", "KLAX", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["skipped_unsettled_dates"]
        assert "settlement-eligible" in payload["explanation"]
    finally:
        rmtree(base, ignore_errors=True)


def test_automation_scripts_exist_and_are_report_only() -> None:
    required = [
        "run_collect_session_lax.ps1",
        "run_after_settlement_lax.ps1",
        "run_model_health_lax.ps1",
        "install_windows_tasks_lax.ps1",
        "uninstall_windows_tasks_lax.ps1",
    ]
    for name in required:
        path = Path("scripts") / name
        text = path.read_text(encoding="utf-8")
        assert path.exists()
        assert "create-order" not in text
        assert "submit_order" not in text
        assert "place_order" not in text


def test_how_to_read_results_doc_exists() -> None:
    path = Path("docs/HOW_TO_READ_RESULTS.md")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    for phrase in [
        "What the system is trying to prove",
        "How to read model-health",
        "What Brier score means",
        "Do not consider real-money trading",
    ]:
        assert phrase in text


def test_daily_maintenance_skip_collect_writes_model_health(monkeypatch) -> None:
    base = _scratch("daily-maintenance")
    try:
        _set_store_env(monkeypatch, base)
        reports = base / "reports"
        result = CliRunner().invoke(
            app,
            [
                "daily-maintenance",
                "--series",
                "KXHIGHLAX",
                "--station",
                "KLAX",
                "--skip-collect",
                "--reports-dir",
                str(reports),
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert "model_health" in result.output
        assert list(reports.glob("daily_*/*model_health.json"))
        assert list(reports.glob("daily_*/*calibration_readiness.txt"))
    finally:
        rmtree(base, ignore_errors=True)


def test_safety_defaults_still_disable_real_orders() -> None:
    settings = load_settings()
    assert settings.kalshi_enable_real_orders is False
