from __future__ import annotations

from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from kalshi_weather.data.storage import SQLiteStore


def _scratch(name: str) -> Path:
    path = Path(".test-artifacts") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_prediction_storage_and_join_no_duplicates() -> None:
    base = _scratch("storage-phase2")
    try:
        store = SQLiteStore(base / "paper.sqlite", base / "snapshots")
        prediction_id = store.save_prediction(
            {
                "asof_utc": "2026-06-19T18:00:00+00:00",
                "series": "KXHIGHLAX",
                "market_ticker": "T70",
                "station": "KLAX",
                "market_date": "2026-06-19",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "bracket_type": "range",
                "p_yes": 0.8,
                "yes_bid": "0.70",
                "yes_ask": "0.75",
                "model_version": "test-model",
            }
        )
        assert prediction_id > 0
        assert store.prediction_count() == 1
        outcome_id = store.save_official_outcome(
            "KLAX", "2026-06-19", "official_high_f", 71.0, "manual"
        )
        assert outcome_id > 0

        first = store.join_predictions_to_outcomes(station="KLAX")
        second = store.join_predictions_to_outcomes(station="KLAX")

        assert first["joined"] == 1
        assert second["joined"] == 0
        assert second["skipped"] == 1
        rows = store.load_prediction_outcomes()
        assert len(rows) == 1
        assert rows[0]["settled_yes"] == 1
    finally:
        rmtree(base, ignore_errors=True)


def test_old_official_outcomes_table_is_migrated() -> None:
    import sqlite3

    base = _scratch("storage-migration")
    try:
        db = base / "paper.sqlite"
        conn = sqlite3.connect(db)
        conn.execute(
            """
            CREATE TABLE official_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_date TEXT NOT NULL,
                station TEXT NOT NULL,
                official_high_f REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        store = SQLiteStore(db, base / "snapshots")
        columns = {
            row[1] for row in store.conn.execute("PRAGMA table_info(official_outcomes)").fetchall()
        }

        assert "metric" in columns
        assert "source_url" in columns
        assert "source_text" in columns
    finally:
        rmtree(base, ignore_errors=True)
