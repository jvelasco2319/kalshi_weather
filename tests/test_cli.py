from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from typer.main import get_command
from typer.testing import CliRunner

from kalshi_weather.cli import (
    _awc_metars_to_observation_rows,
    _add_registry_model_rows,
    _append_record_extra_model_rows,
    _compact_bracket,
    _format_trader_combined_table_header,
    _format_trader_combined_table_row,
    _format_trader_table_header,
    _format_trader_table_row,
    _model_telemetry_text,
    _nws_grid_high,
    _nws_hourly_high,
    _open_meteo_ensemble_stats,
    _parse_mos_tmp_high,
    _provider_model_map,
    _record_model_row_from_telemetry,
    _record_snapshot_text,
    _record_top_probability,
    _record_warnings,
    _telemetry_bracket_for_temperature,
    _trader_context_text,
    _trader_paper_run_header,
    _trader_readable_paper_text,
    _trader_result_text,
    _trader_snapshot_should_print,
    _trader_snapshot_state,
    _trader_snapshot_text,
    _trader_table_row,
    app,
)
from kalshi_weather.trader_agent.journal import SqliteTraderJournal
from test_trader_trade_board import sample_context


def test_cli_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "markets" in result.output
    assert "weather-snapshot" in result.output
    assert "run-paper" in result.output
    assert "calibration-report" in result.output
    assert "trader-portfolio" in result.output


def test_trader_prompt_example_options_are_supported() -> None:
    runner = CliRunner()

    paper = runner.invoke(app, ["trader-paper-run", "--help"])
    replay = runner.invoke(app, ["trader-replay", "--help"])
    journal = runner.invoke(app, ["trader-journal", "--help"])

    assert paper.exit_code == 0
    assert "--starting-cash" in paper.output
    assert "--output-style" in paper.output
    assert "--quiet" in paper.output
    command = get_command(app).commands["trader-paper-run"]
    assert any("--show-market-snapshot" in param.opts for param in command.params)
    assert "--show-snapshot" in paper.output
    assert "--snapshot-every" in paper.output
    assert "--snapshot-style" in paper.output
    assert "--show-models" in paper.output
    assert "--show-market" in paper.output
    assert "--no-snapshot" in paper.output
    assert "--show-trade-board" in paper.output
    assert "--show-prompt" in paper.output
    assert "--show-llm-reasoning" in paper.output
    assert replay.exit_code == 0
    assert "--series" in replay.output
    assert "--station" in replay.output
    assert "--date" in replay.output
    assert journal.exit_code == 0
    assert "--latest" in journal.output


def test_trader_clv_report_reads_journal_samples(tmp_path) -> None:
    journal_path = tmp_path / "trader.sqlite"
    journal = SqliteTraderJournal(journal_path)
    journal.record_run(
        {
            "context": {"series": "KXHIGHLAX", "station": "KLAX", "market_date": "2026-06-30"},
            "decision": {"action": "HOLD"},
            "validation": {"valid": True},
            "clv_samples": [
                {
                    "fill_id": 1,
                    "selected_candidate_id": "KXHIGHLAX-26JUN30-B70.5:YES:BUY",
                    "bracket_label": "70-71",
                    "side": "YES",
                    "entry_price_cents": 40,
                    "current_side_mid_cents": 46,
                    "market_mid_after_5_min": 44,
                    "market_mid_after_15_min": 45,
                    "elapsed_minutes": 16,
                }
            ],
        }
    )

    result = CliRunner().invoke(
        app,
        [
            "trader-clv-report",
            "--journal-path",
            str(journal_path),
            "--allow-noncanonical-output-paths",
        ],
    )

    assert result.exit_code == 0
    assert "Kalshi Weather Trader CLV Report" in result.output
    assert "+6.0c" in result.output
    assert "70-71" in result.output


def test_resume_script_reuses_existing_journal_and_new_debug_dir() -> None:
    script = Path("scripts/resume_existing_to_6pm.ps1").read_text(encoding="utf-8")

    assert "canonical_paths_payload('$ExistingRunId')" in script
    assert '${ExistingRunId}_resume_to_6pm_$stamp' in script
    assert '"--journal-path", $journalPath' in script
    assert '"--debug-run-id", $resumeRunId' in script


def test_resume_script_does_not_fresh_start_or_set_starting_cash() -> None:
    script = Path("scripts/resume_existing_to_6pm.ps1").read_text(encoding="utf-8")

    assert "--fresh-journal" not in script
    assert "--starting-cash" not in script
    assert '"--resume-paper-portfolio"' in script
    assert '"--target-date", $TargetDate' in script
    assert "--tomorrow" not in script


def test_model_telemetry_commands_are_supported() -> None:
    runner = CliRunner()

    once = runner.invoke(app, ["model-telemetry-once", "--help"])
    run = runner.invoke(app, ["model-telemetry-run", "--help"])

    assert once.exit_code == 0
    assert "--include-raw" in once.output
    assert run.exit_code == 0
    assert "--interval-seconds" in run.output
    assert "--duration-days" in run.output
    assert "--json-lines" in run.output
    command = get_command(app).commands["model-telemetry-once"]
    assert any("--finalize-recent-days" in param.opts for param in command.params)


def test_record_weather_market_commands_are_supported() -> None:
    runner = CliRunner()

    once = runner.invoke(app, ["record-weather-market-once", "--help"])
    loop = runner.invoke(app, ["record-weather-market-loop", "--help"])
    analyze = runner.invoke(app, ["analyze-model-validation", "--help"])
    list_models = runner.invoke(app, ["record-weather-market-once", "--list-models"])

    assert once.exit_code == 0
    assert "--model-set" in once.output
    assert "--probe-models" in once.output
    assert loop.exit_code == 0
    assert "--interval-seconds" in loop.output
    assert "--duration-days" in loop.output
    assert "--snapshot-style" in loop.output
    assert "--compact" in loop.output
    assert analyze.exit_code == 0
    assert "--journal-path" in analyze.output
    assert list_models.exit_code == 0
    assert "current_weighted_blend" in list_models.output
    assert "gefs_mean" in list_models.output


def test_record_weather_market_accepts_all_model_set() -> None:
    result = CliRunner().invoke(app, ["record-weather-market-once", "--list-models", "--model-set", "all"])

    assert result.exit_code == 0
    assert "current_weighted_blend" in result.output
    assert "nam_mos" in result.output


def test_record_weather_market_once_does_not_call_trading(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("record-only command touched trading path")

    monkeypatch.setattr("kalshi_weather.cli.make_default_broker", fail_if_called)
    monkeypatch.setattr("kalshi_weather.cli.run_paper_once", fail_if_called)
    monkeypatch.setattr("kalshi_weather.cli.run_paper_loop", fail_if_called)
    monkeypatch.setattr("kalshi_weather.cli.opportunity_rows", fail_if_called)

    payload = {
        "experiment_id": "test",
        "generated_at_utc": "2026-06-26T17:00:00+00:00",
        "bucket_start_utc": "2026-06-26T17:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "successful_model_count": 1,
        "missing_model_count": 0,
        "error_model_count": 0,
        "observation": {"latest_temp_f": 69.0, "high_so_far_f": 70.0},
        "final_high": {"official_high_f": None},
        "market": {"brackets": [{"bracket_label": "70-71", "yes_bid_cents": 54, "yes_ask_cents": 56}]},
    }
    monkeypatch.setattr("kalshi_weather.cli._record_weather_market_payload", lambda *args, **kwargs: dict(payload))

    def fake_write(record_payload, **_kwargs):
        record_payload["journal_status"] = "recorded"
        record_payload["snapshot_id"] = 1
        return record_payload

    monkeypatch.setattr("kalshi_weather.cli._write_record_payload", fake_write)

    result = CliRunner().invoke(app, ["record-weather-market-once"])

    assert result.exit_code == 0
    assert "Snapshot 0001" in result.output
    assert "recorded id=1" in result.output


def test_record_weather_market_loop_prints_readable_snapshot(monkeypatch) -> None:
    calls = []
    payload = {
        "experiment_id": "test",
        "generated_at_utc": "2026-06-26T22:37:00+00:00",
        "timezone": "America/Los_Angeles",
        "bucket_start_utc": "2026-06-26T22:30:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "successful_model_count": 1,
        "missing_model_count": 1,
        "error_model_count": 0,
        "observation": {
            "source": "awc_metar",
            "latest_temp_f": 69.0,
            "latest_observation_utc": "2026-06-26T22:30:00+00:00",
            "high_so_far_f": 70.0,
        },
        "final_high": {"official_high_f": None},
        "models": [
            {
                "model_key": "best_match",
                "fetch_status": "ok",
                "estimated_high_f": 70.4,
                "estimated_bracket": "70-71",
                "top_probability_bracket": "70-71",
                "top_probability": 0.74,
            },
            {
                "model_key": "gefs_mean",
                "fetch_status": "missing",
                "estimated_high_f": None,
                "estimated_bracket": None,
                "error_message": "not fetched by current wiring",
            },
        ],
        "market": {
            "bracket_count": 2,
            "brackets": [
                {
                    "market_ticker": "KXHIGHLAX-26JUN26-B70.5",
                    "bracket_label": "70-71",
                    "yes_bid_cents": 54,
                    "yes_ask_cents": 56,
                    "no_bid_cents": 44,
                    "no_ask_cents": 46,
                },
                {
                    "market_ticker": "KXHIGHLAX-26JUN26-B72.5",
                    "bracket_label": "72-73",
                    "yes_bid_cents": 36,
                    "yes_ask_cents": 38,
                    "no_bid_cents": 62,
                    "no_ask_cents": 64,
                },
            ],
        },
    }

    def fake_payload(*_args, **kwargs):
        calls.append(kwargs)
        return dict(payload)

    monkeypatch.setattr("kalshi_weather.cli._record_weather_market_payload", fake_payload)

    def fake_write(record_payload, **_kwargs):
        record_payload["journal_status"] = "recorded"
        record_payload["snapshot_id"] = 7
        return record_payload

    monkeypatch.setattr("kalshi_weather.cli._write_record_payload", fake_write)

    result = CliRunner().invoke(
        app,
        [
            "record-weather-market-loop",
            "--max-iterations",
            "1",
            "--interval-seconds",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "Snapshot 0001" in result.output
    assert "Observation: source awc_metar" in result.output
    assert "Models" in result.output
    assert "Brkt" in result.output
    assert "MktTop" in result.output
    assert "Source" in result.output
    assert "Spread" in result.output
    assert "best_match" in result.output
    assert "gefs_mean" in result.output
    assert "Market" in result.output
    assert "70-71" in result.output
    assert "Best YES" in result.output
    assert "55c" in result.output
    assert calls[0]["bucket_interval_seconds"] == 1


def _record_display_payload() -> dict:
    return {
        "experiment_id": "test",
        "generated_at_utc": "2026-06-26T22:37:00+00:00",
        "timezone": "America/Los_Angeles",
        "bucket_start_utc": "2026-06-26T22:30:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "successful_model_count": 1,
        "missing_model_count": 0,
        "error_model_count": 0,
        "observation": {
            "source": "nws_station_observations",
            "latest_temp_f": 69.8,
            "latest_observation_utc": "2026-06-26T22:30:00+00:00",
            "high_so_far_f": 71.6,
        },
        "final_high": {"official_high_f": None},
        "models": [],
        "market": {
            "bracket_count": 2,
            "brackets": [
                {
                    "market_ticker": "KXHIGHLAX-26JUN26-B70.5",
                    "bracket_label": "70-71",
                    "yes_bid_cents": 54,
                    "yes_ask_cents": 56,
                },
                {
                    "market_ticker": "KXHIGHLAX-26JUN26-B72.5",
                    "bracket_label": "72-73",
                    "yes_bid_cents": 99,
                    "yes_ask_cents": None,
                },
            ],
        },
    }


def test_record_snapshot_display_columns_and_deterministic_prob() -> None:
    payload = _record_display_payload()
    payload["models"] = [
        {
            "model_key": "gfs_global",
            "provider": "open_meteo",
            "fetch_status": "ok",
            "estimated_high_f": 70.2,
            "estimated_bracket": "70-71",
            "top_probability": 1.0,
            "top_probability_bracket": "70-71",
            "estimate_source_kind": "open_meteo_model",
            "source_type": "deterministic",
        }
    ]

    text = _record_snapshot_text(1, payload)

    assert "Brkt" in text
    assert "MktTop" in text
    assert "Prob" in text
    assert "Spread" in text
    assert "Source" in text
    assert "100.0%" not in text
    assert "om" in text


def test_record_snapshot_merges_ensemble_spread_rows() -> None:
    payload = _record_display_payload()
    payload["models"] = [
        {
            "model_key": "gefs_mean",
            "provider": "open_meteo",
            "fetch_status": "ok",
            "estimated_high_f": 70.4,
            "estimated_bracket": "70-71",
            "estimate_source_kind": "ensemble_mean",
            "source_type": "ensemble_mean",
            "is_ensemble": True,
        },
        {
            "model_key": "gefs_spread",
            "provider": "open_meteo",
            "fetch_status": "ok",
            "estimated_high_f": 70.4,
            "estimated_bracket": "70-71",
            "uncertainty_spread_f": 1.4,
            "estimate_source_kind": "ensemble_spread",
            "source_type": "ensemble_spread",
            "is_ensemble": True,
        },
    ]

    text = _record_snapshot_text(1, payload)

    assert "gefs " in text
    assert "gefs_mean" not in text
    assert "gefs_spread" not in text
    assert "1.4F" in text


def test_record_snapshot_warns_on_duplicate_high_so_far_estimates() -> None:
    payload = _record_display_payload()
    payload["models"] = [
        {
            "model_key": key,
            "provider": "open_meteo",
            "fetch_status": "ok",
            "estimated_high_f": 71.6,
            "estimated_bracket": "70-71",
            "estimate_source_kind": "open_meteo_model",
            "source_type": "deterministic",
        }
        for key in ["best_match", "gfs013", "gfs_global", "gfs_seamless", "ecmwf_ifs"]
    ]
    payload["warnings"] = _record_warnings(payload)

    text = _record_snapshot_text(1, payload)

    codes = {warning["code"] for warning in payload["warnings"]}
    assert "many_identical_model_estimates" in codes
    assert "many_estimates_match_high_so_far" in codes
    assert "Many model estimates are identical" in text
    assert "current KLAX high-so-far" in text


def test_record_market_table_shows_best_yes_for_one_sided_books() -> None:
    payload = _record_display_payload()

    text = _record_snapshot_text(1, payload)

    assert "Market top: 72-73 @ 99c best YES" in text
    assert "Best YES" in text
    assert "*72-73" in text


def test_record_errors_section_contains_full_reason() -> None:
    payload = _record_display_payload()
    full_reason = "Open-Meteo returned no usable data for model=aifs on target date 2026-06-26."
    payload["models"] = [
        {
            "model_key": "aifs",
            "provider": "open_meteo",
            "fetch_status": "error",
            "error_message": "short",
            "full_error_message": full_reason,
            "estimate_source_kind": "unavailable",
        }
    ]

    text = _record_snapshot_text(1, payload, snapshot_style="full")

    assert "Errors" in text
    assert "Full reason" in text
    assert full_reason in text


def test_record_errors_default_table_uses_short_issue() -> None:
    payload = _record_display_payload()
    noisy_reason = (
        "No usable 2-meter temperature values returned by Herbie. "
        "HTTPSConnectionPool(host='data.rda.ucar.edu', port=443): Max retries exceeded "
        "with url: /d084001/2026/gfs.grib2 (Caused by SSLError(SSLCertVerificationError"
        "(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired')))"
    )
    payload["models"] = [
        {
            "model_key": "gfs",
            "provider": "noaa_herbie",
            "fetch_status": "error",
            "error_message": noisy_reason,
            "full_error_message": noisy_reason,
            "estimate_source_kind": "unavailable",
        },
        {
            "model_key": "gfs_graphcast",
            "provider": "open_meteo",
            "fetch_status": "missing",
            "error_message": "not fetched by current wiring",
        },
    ]

    text = _record_snapshot_text(1, payload)

    assert "Issue" in text
    assert "Full reason" not in text
    assert "data source SSL certificate failed" in text
    assert "optional models" in text
    assert "Max retries exceeded" not in text
    assert "Full error details are saved in the journal" in text


def test_record_model_row_keeps_raw_forecast_separate_from_settlement() -> None:
    row = _record_model_row_from_telemetry(
        {
            "provider": "open_meteo",
            "model_id": "gfs013",
            "successful": True,
            "future_high_f": 69.2,
            "settlement_high_estimate_f": 71.6,
            "estimated_bracket": "68-69",
            "settlement_bracket": "70-71",
            "source": "open_meteo",
            "source_url": "https://api.open-meteo.com/v1/gfs",
            "details_json": {"request": {"url": "https://api.open-meteo.com/v1/gfs", "params": {"models": "gfs013"}}},
        }
    )

    assert row["estimated_high_f"] == 69.2
    assert row["settlement_high_estimate_f"] == 71.6
    assert row["estimated_bracket"] == "68-69"
    assert row["settlement_bracket"] == "70-71"
    assert row["estimate_source_kind"] == "open_meteo_model"
    assert row["endpoint_used"] == "https://api.open-meteo.com/v1/gfs"


def test_telemetry_bracket_mapping_uses_canonical_labels() -> None:
    brackets = [
        {"bracket_label": "<66", "bracket_lower_f": None, "bracket_upper_f": 65},
        {"bracket_label": "66-67", "bracket_lower_f": 66, "bracket_upper_f": 67},
        {"bracket_label": "68-69", "bracket_lower_f": 68, "bracket_upper_f": 69},
        {"bracket_label": "70-71", "bracket_lower_f": 70, "bracket_upper_f": 71},
        {"bracket_label": "72-73", "bracket_lower_f": 72, "bracket_upper_f": 73},
        {"bracket_label": ">73", "bracket_lower_f": 74, "bracket_upper_f": None},
    ]

    assert _telemetry_bracket_for_temperature(65.4, brackets) == "<66"
    assert _telemetry_bracket_for_temperature(65.9, brackets) == "66-67"
    assert _telemetry_bracket_for_temperature(67.6, brackets) == "68-69"
    assert _telemetry_bracket_for_temperature(70.6, brackets) == "70-71"
    assert _telemetry_bracket_for_temperature(71.6, brackets) == "72-73"
    assert _telemetry_bracket_for_temperature(74.0, brackets) == ">73"


def test_awc_metar_parser_computes_station_observation_rows() -> None:
    rows = _awc_metars_to_observation_rows(
        [
            {"obsTime": "2026-06-26T16:00:00Z", "temp": 20, "rawOb": "KLAX one"},
            {"obsTime": "2026-06-26T17:00:00Z", "temp": 22, "rawOb": "KLAX two"},
        ],
        start_utc=datetime(2026, 6, 26, 15, tzinfo=timezone.utc),
        end_utc=datetime(2026, 6, 26, 18, tzinfo=timezone.utc),
    )

    assert len(rows) == 2
    assert round(max(row["temp_f"] for row in rows), 1) == 71.6
    assert rows[-1]["raw_message"] == "KLAX two"


def test_failed_record_model_row_does_not_show_stale_estimate() -> None:
    row = _record_model_row_from_telemetry(
        {
            "provider": "noaa_herbie",
            "model_id": "gefs_mean",
            "successful": False,
            "settlement_high_estimate_f": 71.6,
            "estimated_bracket": "70-71",
            "top_probability_bracket": "72-73",
            "top_probability": 1.0,
            "error_message": "Unsupported Herbie model",
        }
    )

    assert row["fetch_status"] == "error"
    assert row["estimated_high_f"] is None
    assert row["estimated_bracket"] is None
    assert row["top_probability_bracket"] is None
    assert row["top_probability"] is None


def test_provider_model_map_allows_open_meteo_optional_models() -> None:
    settings = SimpleNamespace(
        model_estimate_default_providers=["current", "open_meteo", "noaa_herbie"],
        model_estimate_default_models={
            "current": ["current_weighted_blend"],
            "open_meteo": ["gfs_seamless", "best_match"],
            "noaa_herbie": ["hrrr", "nbm", "gfs", "rap"],
        },
        direct_noaa_models={"models": {"hrrr": {}, "nbm": {}, "gfs": {}, "rap": {}, "nam": {}, "nam_conus": {}}},
    )

    providers, model_map = _provider_model_map(
        settings,
        "current,open_meteo,noaa_herbie",
        "current_weighted_blend,gfs_seamless,nam,ecmwf_ifs,aifs,hrrr,nbm,gfs,rap",
    )

    assert providers == ["current", "open_meteo", "noaa_herbie"]
    assert model_map["current"] == ["current_weighted_blend"]
    assert "gfs_seamless" in model_map["open_meteo"]
    assert "ecmwf_ifs" in model_map["open_meteo"]
    assert "aifs" in model_map["open_meteo"]
    assert model_map["noaa_herbie"][:4] == ["hrrr", "nbm", "gfs", "rap"]
    assert "nam" in model_map["noaa_herbie"]
    assert "nam" not in model_map["open_meteo"]
    assert "ecmwf_ifs" not in model_map["noaa_herbie"]


def test_record_extra_rows_retries_optional_open_meteo_and_replaces_missing(monkeypatch) -> None:
    settings = SimpleNamespace(
        open_meteo_base_url="https://api.open-meteo.com/v1/gfs",
        direct_noaa_models={"models": {}},
    )
    payload = _record_display_payload()
    payload["models_by_estimated_high"] = [
        {
            "provider": "open_meteo",
            "model_id": "gfs_graphcast",
            "successful": False,
            "error_message": "not fetched by current wiring",
        }
    ]

    def fake_open_meteo_high(_settings, *, model_key, target_date):
        assert model_key == "gfs_graphcast"
        assert target_date == date(2026, 6, 26)
        return {
            "estimated_high_f": 72.4,
            "source_url": "https://api.open-meteo.com/v1/gfs",
            "raw": {
                "request": {"url": "https://api.open-meteo.com/v1/gfs", "params": {"models": "gfs_graphcast025"}},
                "source_model_id": "gfs_graphcast025",
            },
        }

    monkeypatch.setattr("kalshi_weather.cli._open_meteo_hourly_high_for_model", fake_open_meteo_high)

    _append_record_extra_model_rows(
        settings,
        payload=payload,
        station="KLAX",
        target_date=date(2026, 6, 26),
        model_keys=["gfs_graphcast"],
        include_raw=True,
    )
    _add_registry_model_rows(payload, ["gfs_graphcast"])

    [row] = payload["models"]
    assert row["model_key"] == "gfs_graphcast"
    assert row["fetch_status"] == "ok"
    assert row["estimated_high_f"] == 72.4
    assert row["raw_model_param_used"] == "gfs_graphcast025"


def test_record_extra_rows_mark_unconfigured_optional_herbie_as_error() -> None:
    settings = SimpleNamespace(
        open_meteo_base_url="https://api.open-meteo.com/v1/gfs",
        direct_noaa_models={"models": {}},
    )
    payload = _record_display_payload()
    payload["models_by_estimated_high"] = []

    _append_record_extra_model_rows(
        settings,
        payload=payload,
        station="KLAX",
        target_date=date(2026, 6, 26),
        model_keys=["rrfs"],
        include_raw=False,
    )
    _add_registry_model_rows(payload, ["rrfs"])

    [row] = payload["models"]
    assert row["model_key"] == "rrfs"
    assert row["fetch_status"] == "error"
    assert "not configured" in row["error_message"]
    assert payload["missing_model_count"] == 0
    assert payload["error_model_count"] == 1


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text or "{}"

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)


def test_open_meteo_ensemble_stats_computes_mean_and_spread(monkeypatch) -> None:
    settings = SimpleNamespace()

    def fake_get(url, params, timeout):
        assert url == "https://ensemble-api.open-meteo.com/v1/ensemble"
        assert params["models"] == "gfs_seamless"
        assert timeout == 30
        return _FakeResponse(
            {
                "daily": {
                    "time": ["2026-06-26"],
                    "temperature_2m_max_member01": [70.0],
                    "temperature_2m_max_member02": [72.0],
                    "temperature_2m_max_member03": [74.0],
                }
            }
        )

    monkeypatch.setattr("requests.get", fake_get)

    result = _open_meteo_ensemble_stats(settings, model_key="gefs_mean", target_date=date(2026, 6, 26))

    assert result["estimated_high_f"] == 72.0
    assert round(result["uncertainty_spread_f"], 1) == 2.0
    assert result["raw"]["member_count"] == 3


def test_nws_hourly_and_grid_high_helpers(monkeypatch) -> None:
    settings = SimpleNamespace(user_agent="test", nws_api_base_url="https://api.weather.gov")
    points = {
        "url": "points",
        "response": {
            "properties": {
                "forecastHourly": "https://api.weather.gov/hourly",
                "forecastGridData": "https://api.weather.gov/grid",
            }
        },
    }

    class FakeSession:
        def get(self, url, timeout):
            assert timeout == 30
            if url.endswith("/hourly"):
                return _FakeResponse(
                    {
                        "properties": {
                            "periods": [
                                {
                                    "startTime": "2026-06-26T13:00:00-07:00",
                                    "temperature": 70,
                                    "temperatureUnit": "F",
                                },
                                {
                                    "startTime": "2026-06-26T15:00:00-07:00",
                                    "temperature": 73,
                                    "temperatureUnit": "F",
                                },
                            ]
                        }
                    }
                )
            return _FakeResponse(
                {
                    "properties": {
                        "maxTemperature": {
                            "uom": "wmoUnit:degC",
                            "values": [
                                {"validTime": "2026-06-26T19:00:00+00:00/PT1H", "value": 24},
                                {"validTime": "2026-06-26T21:00:00+00:00/PT1H", "value": 25},
                            ],
                        }
                    }
                }
            )

    monkeypatch.setattr("kalshi_weather.cli._nws", lambda _settings: SimpleNamespace(session=FakeSession()))

    hourly = _nws_hourly_high(settings, target_date=date(2026, 6, 26), points=points)
    grid = _nws_grid_high(settings, target_date=date(2026, 6, 26), points=points)

    assert hourly["estimated_high_f"] == 73
    assert round(grid["estimated_high_f"], 1) == 77.0


def test_parse_mos_tmp_high_uses_target_local_date() -> None:
    text = """
 KLAX   NAM MOS GUIDANCE    6/26/2026  1200 UTC
 DT /JUNE 26/JUNE 27
 HR   18 21 00 03 06 09 12 15 18 21
 N/X                    63          71
 TMP  67 69 68 65 64 63 63 64 67 70

 KBUR   NAM MOS GUIDANCE    6/26/2026  1200 UTC
 HR   18 21
 TMP  80 82
"""

    result = _parse_mos_tmp_high(text, station="KLAX", target_date=date(2026, 6, 26))

    assert result["estimated_high_f"] == 69
    assert result["raw"]["forecast_points"][0]["valid_local"].startswith("2026-06-26")


def test_record_top_probability_uses_canonical_market_brackets() -> None:
    market_brackets = [
        {"market_ticker": "T66", "bracket_label": "<66", "bracket_lower_f": None, "bracket_upper_f": 65},
        {"market_ticker": "B66.5", "bracket_label": "66-67", "bracket_lower_f": 66, "bracket_upper_f": 67},
        {"market_ticker": "B68.5", "bracket_label": "68-69", "bracket_lower_f": 68, "bracket_upper_f": 69},
        {"market_ticker": "B70.5", "bracket_label": "70-71", "bracket_lower_f": 70, "bracket_upper_f": 71},
        {"market_ticker": "B72.5", "bracket_label": "72-73", "bracket_lower_f": 72, "bracket_upper_f": 73},
        {"market_ticker": "T73", "bracket_label": ">73", "bracket_lower_f": 74, "bracket_upper_f": None},
    ]
    settings = SimpleNamespace(model_estimate_probability_residual_sigma_f=0.2, monte_carlo_samples=2000)

    label, probability = _record_top_probability(
        estimated_high_f=70.6,
        observed_high_so_far_f=None,
        market_brackets=market_brackets,
        residual_sigma_f=None,
        settings=settings,
    )

    assert label == "70-71"
    assert probability is not None and probability > 0.9


def test_model_telemetry_text_is_record_only() -> None:
    payload = {
        "generated_at_utc": "2026-06-26T17:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-26",
        "successful_model_count": 1,
        "model_count": 1,
        "observed_high_so_far_f": 64.4,
        "final_high": {"official_high_f": None},
        "market": {"bracket_count": 6},
        "storage_ids": {"telemetry_snapshot_id": 123, "stored_estimate_ids": {"current:a": 1}, "stored_probability_ids": [1]},
        "models_by_estimated_high": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "successful": True,
                "settlement_high_estimate_f": 70.6,
                "estimated_bracket": "70-71",
                "top_probability_bracket": "70-71",
                "top_probability": 0.7,
            }
        ],
    }

    text = _model_telemetry_text(payload)

    assert "Mode: record_only" in text
    assert "Paper orders: DISABLED" in text
    assert "LLM trader: DISABLED" in text
    assert "70-71" in text
    assert "candidate" not in text.lower()
    assert "order requested" not in text.lower()


def test_model_telemetry_run_one_mocked_iteration(monkeypatch) -> None:
    payload = {
        "generated_at_utc": "2026-06-26T17:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-26",
        "successful_model_count": 1,
        "model_count": 1,
        "observed_high_so_far_f": 64.4,
        "final_high": {"official_high_f": None},
        "market": {"bracket_count": 6},
        "storage_ids": {"telemetry_snapshot_id": 123},
    }
    monkeypatch.setattr("kalshi_weather.cli._model_telemetry_payload", lambda *args, **kwargs: payload)

    result = CliRunner().invoke(
        app,
        [
            "model-telemetry-run",
            "--max-iterations",
            "1",
            "--interval-seconds",
            "1",
            "--series",
            "KXHIGHLAX",
            "--station",
            "KLAX",
        ],
    )

    assert result.exit_code == 0
    assert "Kalshi Weather Model Telemetry Run" in result.output
    assert "record_only" in result.output
    assert "snapshot 123" in result.output


def test_trader_result_text_is_human_readable_without_raw_json() -> None:
    payload = {
        "context": sample_context().to_dict(),
        "decision": {
            "action": "HOLD",
            "selected_candidate_id": None,
            "confidence": "low",
            "estimated_edge_cents": 0.0,
            "trader_thesis": "Provider fallback HOLD.",
            "no_trade_reason": "ollama unavailable: model not found",
        },
        "validation": {"valid": True, "rejection_reason": None},
        "paper_order": None,
        "paper_execution": None,
        "paper_order_status": "no_fake_order",
        "open_positions": [
            {
                "quantity": 3,
                "side": "YES",
                "bracket_label": "70-71",
                "avg_entry_price_cents": 42.0,
            }
        ],
    }

    text = _trader_result_text(payload)

    assert "KALSHI WEATHER LLM TRADER - FAKE MONEY ONLY" in text
    assert "No-trade reason: ollama unavailable: model not found" in text
    assert "Top Eligible Trades" in text
    assert "- 3 YES 70-71 @ 42c" in text
    assert "Full JSON: rerun with --json" in text
    assert '"context"' not in text
    assert "{\n" not in text


def test_trader_context_text_is_human_readable_without_raw_json() -> None:
    text = _trader_context_text(sample_context())

    assert "KALSHI WEATHER LLM TRADER CONTEXT - FAKE MONEY ONLY" in text
    assert "Market Brackets" in text
    assert "Top Eligible Trades" in text
    assert '"candidate_trades"' not in text
    assert "{\n" not in text


def test_trader_paper_run_table_row_is_compact() -> None:
    context = sample_context().to_dict()
    candidate = next(
        row
        for row in context["candidate_trades"]
        if row["contract_ticker"] == "T72-T73" and row["side"] == "NO" and row["action"] == "BUY"
    )
    payload = {
        "context": context,
        "decision": {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "selected_candidate_id": candidate["candidate_id"],
            "contract_ticker": candidate["contract_ticker"],
            "bracket": candidate["bracket_label"],
            "side": candidate["side"],
            "limit_price_cents": 66,
            "max_contracts": 50,
            "estimated_edge_cents": 8.4,
            "confidence": "medium",
            "why_this_trade": "overpriced 72-73",
        },
        "validation": {"valid": True, "rejection_reason": None},
        "paper_order": {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "T72-T73",
            "side": "NO",
            "limit_price_cents": 66,
            "quantity": 50,
            "metadata": {"bracket_label": "72-73"},
        },
        "paper_execution": {
            "executed": True,
            "action": "BUY",
            "fill": {
                "quantity": 50,
                "side": "NO",
                "bracket_label": "72-73",
                "price_cents": 66,
            },
        },
        "open_positions": [
            {
                "contract_ticker": "T72-T73",
                "quantity": 50,
                "side": "NO",
                "bracket_label": "72-73",
                "avg_entry_price_cents": 66,
            }
        ],
    }

    row = _trader_table_row(payload, starting_cash=1000.0)
    text = _format_trader_table_header() + "\n" + _format_trader_table_row(row)

    assert "Open P/L" in text
    assert "Equity" in text
    assert "BUY" in text
    assert "NO" in text
    assert "72-73" in text
    assert "66c" in text
    assert "+8.4c" in text
    assert "med" in text
    assert "$967.00" in text
    assert "$999.50" in text
    assert "edge passed" in text


def test_trader_paper_run_combined_table_merges_columns() -> None:
    context = sample_context().to_dict()
    candidate = next(
        row
        for row in context["candidate_trades"]
        if row["contract_ticker"] == "T72-T73" and row["side"] == "NO" and row["action"] == "BUY"
    )
    payload = {
        "context": context,
        "decision": {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "selected_candidate_id": candidate["candidate_id"],
            "contract_ticker": candidate["contract_ticker"],
            "bracket": candidate["bracket_label"],
            "side": candidate["side"],
            "limit_price_cents": 66,
            "max_contracts": 50,
            "estimated_edge_cents": 8.4,
            "confidence": "medium",
            "why_this_trade": "overpriced 72-73 with better EV than the top bracket",
        },
        "validation": {"valid": True, "rejection_reason": None},
        "paper_order": {
            "action": "PLACE_FAKE_LIMIT_BUY",
            "contract_ticker": "T72-T73",
            "side": "NO",
            "limit_price_cents": 66,
            "quantity": 50,
            "metadata": {"bracket_label": "72-73"},
        },
        "paper_execution": {
            "executed": True,
            "action": "BUY",
            "fill": {
                "quantity": 50,
                "side": "NO",
                "bracket_label": "72-73",
                "price_cents": 66,
            },
        },
        "open_positions": [
            {
                "contract_ticker": "T72-T73",
                "quantity": 50,
                "side": "NO",
                "bracket_label": "72-73",
                "avg_entry_price_cents": 66,
            }
        ],
    }

    row = _trader_table_row(payload, starting_cash=1000.0)
    text = _format_trader_combined_table_header() + "\n" + _format_trader_combined_table_row(row)

    assert "Trade" in text
    assert "Order" in text
    assert "Equity" in text
    assert "Ctr" in text
    assert "Exposure" in text
    assert "Open P/L" in text
    assert "NO 72-73" in text
    assert "50 @ 66c" in text
    assert "$967.00" in text
    assert "$999.50" in text
    assert "$33.00" in text
    assert "-$0.50" in text
    assert "edge passed" in text
    assert "overpriced 72-73 with better EV" not in text


def _snapshot_payload() -> dict:
    context = sample_context().to_dict()
    context["current_time_utc"] = "2026-06-26T17:25:00+00:00"
    context["model_estimates"] = [
        {"provider": "current:current_weighted_blend", "high_f": 70.3},
        {"provider": "open_meteo:best_match", "high_f": 70.6},
        {"provider": "open_meteo:gfs013", "high_f": 69.9},
        {"provider": "open_meteo:gfs_global", "high_f": 69.9},
        {"provider": "open_meteo:gfs_seamless", "high_f": 70.6},
        {"provider": "noaa_herbie:hrrr", "high_f": 66.9},
        {"provider": "noaa_herbie:nbm", "high_f": 66.9},
    ]
    return {
        "iteration": 1,
        "context": context,
        "decision": {"action": "HOLD", "confidence": "medium"},
        "validation": {"valid": True, "rejection_reason": None},
        "paper_order": None,
        "paper_execution": None,
    }


def test_bracket_labels_are_canonical_and_top_is_not_truncated() -> None:
    context = sample_context().to_dict()

    assert _compact_bracket("68-69°F") == "68-69"
    assert _compact_bracket("74+") == ">73"
    assert _compact_bracket("65 or below") == "<66"
    assert _compact_bracket("KXHIGHLAX-26JUN26-B68.5") == "68-69"
    assert _trader_table_row({"context": context, "decision": {"action": "HOLD"}}, starting_cash=1000)["top"] == "70-71"


def test_default_snapshot_is_block_not_inline_models_market() -> None:
    text = _trader_snapshot_text(_snapshot_payload(), style="compact")

    assert "Snapshot 10:25" in text
    assert "\nModels\n" in text
    assert "\nMarket\n" in text
    assert "Models:" not in text
    assert "Market:" not in text


def test_snapshot_prints_on_first_iteration() -> None:
    payload = _snapshot_payload()
    should_print, _ = _trader_snapshot_should_print(
        payload,
        {"action": "HOLD", "note": "no clean edge"},
        iteration=1,
        show_snapshot="changed",
        snapshot_every=5,
        previous_state=None,
        seen_rejection_reasons=set(),
    )

    assert should_print is True


def test_snapshot_prints_every_n_iterations_when_requested() -> None:
    payload = _snapshot_payload()
    previous = _trader_snapshot_state(payload["context"])
    should_print, _ = _trader_snapshot_should_print(
        payload,
        {"action": "HOLD", "note": "no clean edge"},
        iteration=5,
        show_snapshot="every",
        snapshot_every=5,
        previous_state=previous,
        seen_rejection_reasons=set(),
    )

    assert should_print is True


def test_snapshot_prints_on_material_model_change() -> None:
    payload = _snapshot_payload()
    previous = _trader_snapshot_state(payload["context"])
    payload["context"]["model_estimates"][0]["high_f"] = 70.9

    should_print, _ = _trader_snapshot_should_print(
        payload,
        {"action": "HOLD", "note": "no clean edge"},
        iteration=2,
        show_snapshot="changed",
        snapshot_every=5,
        previous_state=previous,
        seen_rejection_reasons=set(),
    )

    assert should_print is True


def test_snapshot_prints_on_material_market_price_change() -> None:
    payload = _snapshot_payload()
    previous = _trader_snapshot_state(payload["context"])
    bracket = next(row for row in payload["context"]["market_brackets"] if row["bracket_label"] == "70-71")
    bracket["yes_ask_cents"] += 3

    should_print, _ = _trader_snapshot_should_print(
        payload,
        {"action": "HOLD", "note": "no clean edge"},
        iteration=2,
        show_snapshot="changed",
        snapshot_every=5,
        previous_state=previous,
        seen_rejection_reasons=set(),
    )

    assert should_print is True


def test_compact_snapshot_lines_stay_within_terminal_width() -> None:
    text = _trader_snapshot_text(_snapshot_payload(), style="compact")

    assert all(len(line) <= 120 for line in text.splitlines())


def test_table_snapshot_includes_each_bracket_once() -> None:
    text = _trader_snapshot_text(_snapshot_payload(), style="table")
    market_lines = text.split("Market\n", 1)[1].splitlines()[2:]
    labels = [line.split()[0] for line in market_lines if line and not line.startswith("-")]

    assert labels == ["<66", "66-67", "68-69", "70-71", "72-73", ">73"]


def test_trader_paper_run_header_is_slim() -> None:
    text = _trader_paper_run_header(
        series="KXHIGHLAX",
        station="KLAX",
        starting_cash=1000.0,
        interval_seconds=60,
        duration_minutes=5,
        max_iterations=5,
    )

    assert "Kalshi Weather Trader Paper Run" in text
    assert "Decision: rules | Strategy: hybrid" in text
    assert "Starting cash: $1000.00 | Interval: 60s | Duration: 5m" in text
    assert "candidate_trades" not in text


def test_trader_readable_output_skips_raw_prompt_and_context() -> None:
    context = sample_context().to_dict()
    context["model_estimates"] = [
        {"provider": "current:current_weighted_blend", "high_f": 70.1},
        {"provider": "open_meteo:best_match", "high_f": 70.4},
    ]
    payload = {
        "context": context,
        "decision": {
            "action": "HOLD",
            "selected_candidate_id": None,
            "confidence": "low",
            "estimated_edge_cents": 0.0,
            "trader_thesis": "No clean trade right now.",
            "why_this_trade": "No trade selected.",
            "exit_plan": {},
        },
        "validation": {"valid": True, "rejection_reason": None},
        "paper_order": None,
        "paper_execution": None,
        "paper_order_status": "no_fake_order",
        "prompt": [{"role": "system", "content": "raw prompt"}],
        "portfolio": {
            "cash": "$950.00",
            "position_value": "$49.50",
            "equity": "$999.50",
            "open_exposure": "$50.00",
            "total_contracts": 50,
            "open_pnl": "-$0.50",
            "closed_pnl": "$0.00",
        },
    }

    text = _trader_readable_paper_text(payload)

    assert "Decision" in text
    assert "Snapshot" in text
    assert "Models" in text
    assert "Blend" in text
    assert "70.1F" in text
    assert "Market" in text
    assert "YES bid/ask" in text
    assert "Full Candidate Trade Board" in text
    assert "Eligible  Money" in text
    assert "Portfolio" in text
    assert "Equity: $999.50" in text
    assert "$" in text
    assert "LLM Reasoning" in text
    assert "Prompt" not in text
    assert "Full Context JSON" not in text
