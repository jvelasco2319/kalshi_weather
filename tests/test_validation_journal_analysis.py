from __future__ import annotations

import json
import sqlite3

from kalshi_weather.validation_analysis import analyze_model_validation, bracket_for_temp
from kalshi_weather.validation_journal import ValidationJournal, append_jsonl


def _payload(bucket: str = "2026-06-26T17:15:00+00:00") -> dict:
    return {
        "schema_version": "record_weather_market_v1",
        "experiment_id": "exp",
        "captured_utc": "2026-06-26T17:16:00+00:00",
        "captured_local": "2026-06-26T10:16:00-07:00",
        "timezone": "America/Los_Angeles",
        "bucket_start_utc": bucket,
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": "2026-06-26",
        "models": [
            {
                "model_key": "hrrr",
                "display_name": "HRRR Direct",
                "provider": "NOAA/Herbie",
                "model_family": "HRRR",
                "independence_group": "HRRR",
                "source_type": "deterministic",
                "fetch_status": "ok",
                "estimated_high_f": 70.2,
                "estimated_bracket": "70-71",
                "uncertainty_spread_f": None,
                "error_message": None,
                "raw": {},
            },
            {
                "model_key": "gefs_mean",
                "display_name": "GEFS Mean",
                "provider": "NOAA/Herbie",
                "model_family": "GEFS",
                "independence_group": "GEFS",
                "source_type": "ensemble_mean",
                "fetch_status": "missing",
                "estimated_high_f": None,
                "estimated_bracket": None,
                "uncertainty_spread_f": None,
                "error_message": "not wired",
                "raw": {},
            },
        ],
        "observation": {
            "target_date": "2026-06-26",
            "station": "KLAX",
            "source": "awc_metar",
            "latest_temp_f": 69.0,
            "latest_observation_utc": "2026-06-26T17:00:00+00:00",
            "high_so_far_f": 70.0,
            "final_high_f": 70.0,
            "observation_count": 12,
            "error_message": None,
            "raw": {},
        },
        "markets": [
            {
                "ticker": "KXHIGHLAX-26JUN26-B70.5",
                "bracket_label": "70-71",
                "yes_bid_cents": 54,
                "yes_ask_cents": 56,
                "no_bid_cents": 44,
                "no_ask_cents": 46,
                "yes_mid_cents": 55,
                "market_status": "open",
                "raw": {},
            }
        ],
        "market_top": {"bracket_label": "70-71", "yes_mid_cents": 55},
        "recent_actuals": [],
        "errors": [],
    }


def test_bracket_for_temp_uses_canonical_labels() -> None:
    assert bracket_for_temp(65.4) == "<66"
    assert bracket_for_temp(66.4) == "66-67"
    assert bracket_for_temp(68.6) == "68-69"
    assert bracket_for_temp(70.4) == "70-71"
    assert bracket_for_temp(72.6) == "72-73"
    assert bracket_for_temp(73.6) == ">73"


def test_validation_journal_schema_idempotency_and_jsonl(tmp_path) -> None:
    db_path = tmp_path / "validation.sqlite"
    jsonl_path = tmp_path / "validation.jsonl"
    journal = ValidationJournal(db_path)
    payload = _payload()

    first = journal.insert_snapshot(payload)
    second = journal.insert_snapshot(payload)
    replaced = journal.insert_snapshot(payload, replace_existing_bucket=True)
    append_jsonl(jsonl_path, payload)

    assert first["status"] == "recorded"
    assert second["status"] == "skipped_duplicate"
    assert replaced["status"] == "recorded"
    assert journal.count_snapshots("exp") == 1
    assert json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])["experiment_id"] == "exp"
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_model_rows").fetchone()[0] == 2
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_market_rows").fetchone()[0] == 1
    assert journal.conn.execute("SELECT COUNT(*) FROM validation_observation_rows").fetchone()[0] == 1


def test_validation_journal_migrates_legacy_tables_missing_bucket(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE validation_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            captured_utc TEXT NOT NULL,
            series TEXT NOT NULL,
            station TEXT NOT NULL,
            target_date TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE validation_model_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            experiment_id TEXT NOT NULL,
            target_date TEXT NOT NULL,
            model_key TEXT NOT NULL,
            fetch_status TEXT NOT NULL,
            estimated_high_f REAL,
            estimated_bracket TEXT
        );
        CREATE TABLE validation_market_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            experiment_id TEXT NOT NULL,
            target_date TEXT NOT NULL,
            bracket_label TEXT NOT NULL,
            yes_bid_cents REAL,
            yes_ask_cents REAL
        );
        CREATE TABLE validation_observation_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            experiment_id TEXT NOT NULL,
            target_date TEXT NOT NULL,
            station TEXT NOT NULL
        );
        """
    )
    conn.close()

    journal = ValidationJournal(db_path)
    result = journal.insert_snapshot(_payload())

    columns = {
        row["name"]
        for row in journal.conn.execute("PRAGMA table_info(validation_snapshots)").fetchall()
    }
    assert "bucket_start_utc" in columns
    assert result["status"] == "recorded"
    assert journal.count_snapshots("exp") == 1


def test_analyze_model_validation_metrics(tmp_path) -> None:
    db_path = tmp_path / "validation.sqlite"
    journal = ValidationJournal(db_path)
    journal.insert_snapshot(_payload())

    result = analyze_model_validation(str(db_path), experiment_id="exp")

    assert result["snapshot_count"] == 1
    assert result["final_day_count"] == 1
    hrrr = next(row for row in result["per_feed"] if row["model"] == "hrrr")
    assert hrrr["snapshots"] == 1
    assert hrrr["bracket_hit_pct"] == 100
    assert hrrr["off_by_one_pct"] == 100
    assert result["market_comparison"][0]["market_top_hit_pct"] == 100
