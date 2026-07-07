from __future__ import annotations

import json

from kalshi_weather.validation_analysis import analyze_model_validation, bracket_for_temp
from kalshi_weather.validation_journal import ValidationJournal, append_jsonl


def _payload() -> dict:
    return {
        "schema_version": "record_weather_market_v1",
        "generated_at_utc": "2026-06-26T17:16:00+00:00",
        "bucket_start_utc": "2026-06-26T17:15:00+00:00",
        "experiment_id": "exp",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "models": [
            {
                "model_key": "hrrr",
                "display_name": "HRRR Direct",
                "provider": "noaa_herbie",
                "model_family": "HRRR",
                "independence_group": "HRRR",
                "source_type": "deterministic",
                "fetch_status": "ok",
                "estimated_high_f": 70.2,
                "estimated_bracket": "70-71",
                "top_probability_bracket": "70-71",
                "top_probability": 0.8,
                "uncertainty_spread_f": None,
                "error_message": None,
            },
            {
                "model_key": "gefs_mean",
                "display_name": "GEFS Mean",
                "provider": "noaa_herbie",
                "model_family": "GEFS",
                "independence_group": "GEFS",
                "source_type": "ensemble_mean",
                "fetch_status": "missing",
                "estimated_high_f": None,
                "estimated_bracket": None,
                "error_message": "not wired",
            },
        ],
        "observation": {
            "source": "awc_metar",
            "latest_temp_f": 69.0,
            "high_so_far_f": 70.0,
            "observations": [
                {
                    "timestamp_utc": "2026-06-26T17:00:00+00:00",
                    "temp_f": 70.0,
                    "raw_message": "TEST",
                }
            ],
        },
        "final_high": {"official_high_f": 70.0},
        "market": {
            "brackets": [
                {
                    "market_ticker": "KXHIGHLAX-26JUN26-B70.5",
                    "bracket_label": "70-71",
                    "yes_bid_cents": 54,
                    "yes_ask_cents": 56,
                    "no_bid_cents": 44,
                    "no_ask_cents": 46,
                }
            ]
        },
    }


def test_validation_bracket_for_temp_uses_canonical_labels() -> None:
    assert bracket_for_temp(65.4) == "<66"
    assert bracket_for_temp(65.9) == "66-67"
    assert bracket_for_temp(66.0) == "66-67"
    assert bracket_for_temp(67.6) == "68-69"
    assert bracket_for_temp(68.0) == "68-69"
    assert bracket_for_temp(70.6) == "70-71"
    assert bracket_for_temp(71.6) == "72-73"
    assert bracket_for_temp(72.5) == "72-73"
    assert bracket_for_temp(74.0) == ">73"


def test_validation_journal_idempotency_and_jsonl(tmp_path) -> None:
    db_path = tmp_path / "validation.sqlite"
    jsonl_path = tmp_path / "validation.jsonl"
    journal = ValidationJournal(db_path)
    payload = _payload()

    first = journal.insert_snapshot(payload)
    duplicate = journal.insert_snapshot(payload)
    replaced = journal.insert_snapshot(payload, replace_existing_bucket=True)
    append_jsonl(jsonl_path, payload)

    assert first["status"] == "recorded"
    assert duplicate["status"] == "skipped_duplicate"
    assert replaced["status"] == "recorded"
    assert journal.count_snapshots("exp") == 1
    assert json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])["experiment_id"] == "exp"
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_model_rows").fetchone()[0] == 2
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_market_rows").fetchone()[0] == 1
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_observation_rows").fetchone()[0] == 1


def test_analyze_model_validation_scores_final_high(tmp_path) -> None:
    db_path = tmp_path / "validation.sqlite"
    journal = ValidationJournal(db_path)
    journal.insert_snapshot(_payload())

    result = analyze_model_validation(journal_path=str(db_path), experiment_id="exp")

    assert result["snapshot_count"] == 1
    hrrr = next(row for row in result["feed_rows"] if row["model_key"] == "hrrr")
    assert hrrr["snapshots"] == 1
    assert hrrr["bracket_hit_rate"] == 1
    assert hrrr["off_by_one_rate"] == 1
    assert result["market_rows"][0]["market_top_hit_rate"] == 1
