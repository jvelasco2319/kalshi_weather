from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from kalshi_weather.config import load_settings
from kalshi_weather.model_registry import get_model_source
from kalshi_weather.validation_recorder import (
    apply_stale_model_carry_forward,
    awc_metars_to_frame,
    build_record_snapshot,
    resolve_record_target_date,
)
from kalshi_weather.validation_journal import ValidationJournal


def test_nbm_timeout_carries_forward_recent_success(tmp_path: Path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    ValidationJournal(journal_path).insert_snapshot(
        {
            "schema_version": "record_weather_market_v1",
            "experiment_id": "lax_model_validation",
            "captured_utc": "2026-07-13T04:40:00+00:00",
            "captured_local": "2026-07-12T21:40:00-07:00",
            "timezone": "America/Los_Angeles",
            "bucket_start_utc": "2026-07-13T04:30:00+00:00",
            "series": "KXHIGHLAX",
            "station": "KLAX",
            "target_date": "2026-07-13",
            "models": [_recorder_model_row("nbm", "ok", 74.25)],
            "markets": [],
            "observation": {},
        }
    )
    payload = {
        "experiment_id": "lax_model_validation",
        "captured_utc": "2026-07-13T04:45:00+00:00",
        "target_date": "2026-07-13",
        "models": [
            _recorder_model_row("nam", "ok", 71.5),
            _recorder_model_row("nbm", "error", None, "Herbie model nbm timed out after 60s"),
        ],
        "model_counts": {"ok": 1, "error": 1, "missing": 0},
    }

    apply_stale_model_carry_forward(payload, journal_path=journal_path, model_keys={"nbm"})

    by_key = {row["model_key"]: row for row in payload["models"]}
    assert by_key["nbm"]["fetch_status"] == "ok"
    assert by_key["nbm"]["estimated_high_f"] == 74.25
    assert by_key["nbm"]["raw"]["carried_forward"] is True
    assert by_key["nbm"]["raw"]["current_error_message"] == "Herbie model nbm timed out after 60s"
    assert payload["model_counts"]["ok"] == 2
    assert payload["model_counts"]["error"] == 0


def test_nbm_carry_forward_uses_jsonl_when_sqlite_bucket_was_replaced(tmp_path: Path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    jsonl_path = tmp_path / "validation.jsonl"
    previous_payload = {
        "experiment_id": "lax_model_validation",
        "captured_utc": "2026-07-13T04:40:00+00:00",
        "target_date": "2026-07-13",
        "models": [_recorder_model_row("nbm", "ok", 74.25)],
    }
    jsonl_path.write_text(json.dumps(previous_payload) + "\n", encoding="utf-8")
    payload = {
        "experiment_id": "lax_model_validation",
        "captured_utc": "2026-07-13T04:45:00+00:00",
        "target_date": "2026-07-13",
        "models": [_recorder_model_row("nbm", "error", None, "Herbie model nbm timed out after 60s")],
        "model_counts": {"ok": 0, "error": 1, "missing": 0},
    }

    apply_stale_model_carry_forward(
        payload,
        journal_path=journal_path,
        jsonl_path=jsonl_path,
        model_keys={"nbm"},
    )

    row = payload["models"][0]
    assert row["fetch_status"] == "ok"
    assert row["estimated_high_f"] == 74.25
    assert row["raw"]["carried_forward"] is True
    assert row["raw"]["source_captured_utc"] == "2026-07-13T04:40:00+00:00"


def _recorder_model_row(
    model_key: str,
    status: str,
    estimate: float | None,
    error: str | None = None,
) -> dict:
    return {
        **get_model_source(model_key).to_dict(),
        "fetch_status": status,
        "estimated_high_f": estimate,
        "estimated_bracket": "74-75" if estimate is not None else None,
        "uncertainty_spread_f": None,
        "error_message": error,
        "raw": {"provider": "test"},
    }


def test_resolve_record_target_date_accepts_auto_and_iso() -> None:
    assert resolve_record_target_date("2026-06-26").isoformat() == "2026-06-26"
    assert resolve_record_target_date("auto").isoformat()


def test_awc_metar_parser_computes_temperatures() -> None:
    frame = awc_metars_to_frame(
        [
            {"obsTime": "2026-06-26T16:00:00Z", "temp": 20, "rawOb": "one"},
            {"obsTime": "2026-06-26T17:00:00Z", "temp": 22, "rawOb": "two"},
        ]
    )

    assert len(frame) == 2
    assert round(float(frame["temp_f"].max()), 1) == 71.6


def test_build_record_snapshot_records_partial_failures_without_trading(monkeypatch) -> None:
    import kalshi_weather.validation_recorder as recorder

    @dataclass
    class FakeForecast:
        model_maxes_f: dict
        failed_models: dict
        failed_variable_requests: dict

    class FakeOpenMeteo:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def forecast_hourly_by_model(self, **_kwargs):
            assert "nam" not in _kwargs["models"]
            assert "nbm" not in _kwargs["models"]
            return FakeForecast(
                model_maxes_f={
                    "temperature_2m__best_match": 70.4,
                    "temperature_2m__gfs013": 69.8,
                },
                failed_models={},
                failed_variable_requests={},
            )

    class FakeHerbie:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def fetch_results(self, **kwargs):
            assert kwargs["models"] == ["nam", "nbm", "hrrr"]
            return [
                recorder.HerbieModelResult(
                    model_id="nam",
                    successful=True,
                    future_high_f=72.2,
                    source_url="memory://nam",
                ),
                recorder.HerbieModelResult(
                    model_id="nbm",
                    successful=True,
                    future_high_f=68.4,
                    source_url="memory://nbm",
                ),
                recorder.HerbieModelResult(
                    model_id="hrrr",
                    successful=False,
                    future_high_f=None,
                    error_message="not available in test",
                ),
            ]

    class FakeKalshi:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_markets(self, _series):
            return [
                {
                    "ticker": "KXHIGHLAX-26JUN26-B70.5",
                    "subtitle": "70-71 degrees",
                    "status": "open",
                }
            ]

        def get_orderbook(self, _ticker, depth=5):
            return {"orderbook": {"yes": [[55, 10]], "no": [[45, 10]]}}

    monkeypatch.setattr(recorder, "OpenMeteoClient", FakeOpenMeteo)
    monkeypatch.setattr(recorder, "HerbieModelClient", FakeHerbie)
    monkeypatch.setattr(recorder, "KalshiPublicClient", FakeKalshi)
    monkeypatch.setattr(
        recorder,
        "_observation_payload",
        lambda *_args, **_kwargs: {
            "target_date": "2026-06-26",
            "station": "KLAX",
            "source": "test",
            "latest_temp_f": 69.0,
            "latest_observation_utc": "2026-06-26T16:00:00+00:00",
            "high_so_far_f": 70.0,
            "final_high_f": None,
            "observation_count": 1,
            "error_message": None,
            "raw": {},
        },
    )

    payload = build_record_snapshot(
        load_settings(),
        series="KXHIGHLAX",
        station="KLAX",
        target_date="2026-06-26",
        timezone_name="America/Los_Angeles",
        experiment_id="test",
        refresh_recent_days=1,
        model_set="current",
        models="best_match,gfs013,nam,nbm,hrrr,current_weighted_blend",
        skip_models=None,
        include_raw=True,
    )

    by_key = {row["model_key"]: row for row in payload["models"]}
    assert by_key["best_match"]["fetch_status"] == "ok"
    assert by_key["gfs013"]["estimated_bracket"] == "70-71"
    assert by_key["nam"]["fetch_status"] == "ok"
    assert by_key["nam"]["estimated_bracket"] == "72-73"
    assert by_key["nam"]["provider"] == "NOAA/Herbie"
    assert by_key["nbm"]["fetch_status"] == "ok"
    assert by_key["nbm"]["estimated_bracket"] == "68-69"
    assert by_key["hrrr"]["fetch_status"] == "error"
    assert by_key["current_weighted_blend"]["fetch_status"] == "ok"
    assert payload["market_top"]["bracket_label"] == "70-71"
    assert payload["no_trading"]["fake_orders_placed"] is False
    assert all("°" not in str(row.get("estimated_bracket")) for row in payload["models"])
