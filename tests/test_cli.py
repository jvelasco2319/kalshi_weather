from typer.testing import CliRunner

from kalshi_weather.cli import app


def test_cli_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "markets" in result.output
    assert "weather-snapshot" in result.output
    assert "record-weather-market-once" in result.output
    assert "record-weather-market-loop" in result.output
    assert "analyze-model-validation" in result.output
    assert "run-paper" in result.output
    assert "calibration-report" in result.output


def test_record_weather_market_once_list_models() -> None:
    result = CliRunner().invoke(app, ["record-weather-market-once", "--list-models"])

    assert result.exit_code == 0
    assert "current_weighted_blend" in result.output
    assert "gefs_mean" in result.output
    assert "gfs_graphcast" in result.output


def test_record_weather_market_command_does_not_call_trading(monkeypatch) -> None:
    import kalshi_weather.cli as cli

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("trading path should not be called")

    monkeypatch.setattr(cli, "make_default_broker", fail_if_called)
    monkeypatch.setattr(cli, "run_paper_once", fail_if_called)
    monkeypatch.setattr(cli, "run_paper_loop", fail_if_called)
    monkeypatch.setattr(cli, "opportunity_rows", fail_if_called)

    def fake_record(*_args, **_kwargs):
        return {
            "experiment_id": "test",
            "captured_utc": "2026-06-26T17:15:00+00:00",
            "captured_local": "2026-06-26T10:15:00-07:00",
            "target_date": "2026-06-26",
            "station": "KLAX",
            "series": "KXHIGHLAX",
            "model_counts": {"ok": 1, "missing": 0, "error": 0},
            "observation": {"latest_temp_f": 69.0, "high_so_far_f": 70.0, "source": "test"},
            "market_top": {"bracket_label": "70-71", "yes_mid_cents": 54},
            "journal": {"status": "recorded", "snapshot_id": 1, "journal_path": "test.sqlite"},
            "errors": [],
        }

    monkeypatch.setattr(cli, "record_validation_once", fake_record)

    result = CliRunner().invoke(app, ["record-weather-market-once"])

    assert result.exit_code == 0
    assert "Recorded snapshot" in result.output
