from __future__ import annotations

from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.data.storage import SQLiteStore
from kalshi_weather.model.lax_high_temp import weighted_future_high
from kalshi_weather.model.registry import get_model_spec, list_model_versions


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_phase4_to_7_cli_help_lists_poc_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in [
        "daily-maintenance",
        "collect-session",
        "research-status",
        "threshold-sweep",
        "calibration-readiness",
        "paper-replay",
        "poc-run",
        "poc-demo",
        "poc-check",
    ]:
        assert command in result.output


def test_weighted_future_high_uses_configured_weights() -> None:
    selected, components = weighted_future_high(
        {"temperature_2m__a": 70.0, "temperature_2m__b": 80.0},
        {"a": 1.0, "b": 3.0},
    )

    assert selected == 77.5
    assert [row["model_id"] for row in components] == ["a", "b"]


def test_model_registry_rejects_unknown_version() -> None:
    assert "v0.3-openmeteo-weighted-normal-residual" in list_model_versions()
    assert get_model_spec("demo-fixture-model").demo_only
    try:
        get_model_spec("missing")
    except ValueError as exc:
        assert "Unknown model version" in str(exc)
    else:
        raise AssertionError("unknown model version should fail")


def test_offline_poc_and_calibration_commands_do_not_need_network(monkeypatch) -> None:
    base = _scratch("poc-offline")
    try:
        monkeypatch.setenv("SQLITE_PATH", str(base / "paper.sqlite"))
        monkeypatch.setenv("SNAPSHOT_DIR", str(base / "snapshots"))
        demo = CliRunner().invoke(app, ["poc-demo", "--station", "KLAX"])
        readiness = CliRunner().invoke(app, ["calibration-readiness", "--station", "KLAX", "--json"])
        replay = CliRunner().invoke(app, ["paper-replay", "--series", "KXHIGHLAX", "--station", "KLAX"])

        assert demo.exit_code == 0
        assert "DEMO DATA" in demo.output
        assert readiness.exit_code == 0
        assert "joined_rows" in readiness.output
        assert replay.exit_code == 0
        assert "No stored predictions" in replay.output
    finally:
        rmtree(base, ignore_errors=True)


def test_opportunity_snapshot_storage_count() -> None:
    base = _scratch("opportunity-storage")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        store.save_opportunity_snapshot("KXHIGHLAX", "KLAX", "2026-06-18", {"rows": []})

        assert store.opportunity_snapshot_count() == 1
        assert store.load_opportunity_snapshots(station="KLAX")[0]["market_date"] == "2026-06-18"
    finally:
        rmtree(base, ignore_errors=True)
