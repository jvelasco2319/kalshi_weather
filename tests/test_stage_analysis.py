from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from kalshi_weather.strategy_current.stage_analysis import (
    backfill_stage_performance,
    replay_stage_weighting,
)
from kalshi_weather.validation_journal import ValidationJournal


MODELS = ("ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm")


def test_stage_backfill_is_date_level_idempotent_and_replay_is_walk_forward(
    tmp_path: Path,
) -> None:
    journal_path = tmp_path / "stage.sqlite"
    journal = ValidationJournal(journal_path)
    try:
        _record_settled_date(journal, date(2026, 7, 10), offset=0.0)
        _record_settled_date(journal, date(2026, 7, 11), offset=0.5)
    finally:
        journal.conn.close()

    first = backfill_stage_performance(journal_path, code_revision="test")
    second = backfill_stage_performance(journal_path, code_revision="test")

    assert first["settled_target_dates"] == 2
    assert first["source_evaluations"] == 4
    assert first["performance_rows"] == 10
    assert second["performance_rows"] == 10
    with sqlite3.connect(journal_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM strategy_stage_performance").fetchone()[0] == 10
        rows = conn.execute(
            """
            SELECT target_date_local, model_key, evaluation_count
            FROM strategy_stage_performance
            ORDER BY target_date_local, model_key
            """
        ).fetchall()
    assert all(row[2] == 2 for row in rows)

    replay = replay_stage_weighting(
        journal_path,
        bootstrap_samples=100,
        code_revision="test",
    )
    assert replay["settled_target_dates"] == 2
    assert replay["source_evaluations"] == 4
    assert replay["blocked_evaluations"] == 0
    assert {
        row["weighting_mode"] for row in replay["metrics"]
    } == {"fixed_baseline", "stage_prior_only", "stage_reliability"}
    assert all(row["target_date_count"] == 2 for row in replay["metrics"])
    assert all(row["evaluation_count"] == 4 for row in replay["metrics"])
    assert all(
        row["mean_log_loss_ci95"]["lower"]
        <= row["mean_log_loss"]
        <= row["mean_log_loss_ci95"]["upper"]
        for row in replay["metrics"]
    )
    assert all(row["mean_calibration_error"] >= 0 for row in replay["metrics"])
    assert all(row["mean_temperature_mae_f"] >= 0 for row in replay["metrics"])
    assert all(isinstance(row["mean_temperature_bias_f"], float) for row in replay["metrics"])
    assert all(row["candidate_count"] >= 0 for row in replay["metrics"])
    assert all(row["quote_evaluation_count"] == 4 for row in replay["metrics"])
    assert all(row["paper_realized_roi"] is None for row in replay["metrics"])
    assert "No paper ROI" in replay["notes"][-1]


def _record_settled_date(
    journal: ValidationJournal,
    target: date,
    *,
    offset: float,
) -> None:
    local_timezone = ZoneInfo("America/Los_Angeles")
    for minute in (15, 45):
        captured_local = datetime.combine(
            target,
            time(hour=9, minute=minute),
            tzinfo=local_timezone,
        )
        journal.insert_snapshot(
            _payload(
                target,
                captured_local.astimezone(timezone.utc),
                offset=offset + minute / 1000,
                final_high=None,
            )
        )
    settled_local = datetime.combine(
        target,
        time(hour=18),
        tzinfo=local_timezone,
    )
    journal.insert_snapshot(
        _payload(
            target,
            settled_local.astimezone(timezone.utc),
            offset=offset,
            final_high=74.0,
        )
    )


def _payload(
    target: date,
    captured_utc: datetime,
    *,
    offset: float,
    final_high: float | None,
) -> dict[str, object]:
    bucket = captured_utc.replace(second=0, microsecond=0)
    target_code = target.strftime("%y%b%d").upper()
    centers = {
        "ecmwf_ifs": 74.0,
        "gfs013": 75.0,
        "gfs_seamless": 73.5,
        "nam": 74.5,
        "nbm": 73.8,
    }
    return {
        "schema_version": "record_weather_market_v1",
        "experiment_id": "stage_test",
        "captured_utc": captured_utc.isoformat(),
        "captured_local": captured_utc.astimezone(
            ZoneInfo("America/Los_Angeles")
        ).isoformat(),
        "timezone": "America/Los_Angeles",
        "bucket_start_utc": bucket.isoformat(),
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "target_date": target.isoformat(),
        "models": [
            {
                "model_key": key,
                "display_name": key,
                "provider": "test",
                "model_family": key,
                "independence_group": key,
                "source_type": "test",
                "fetch_status": "ok",
                "estimated_high_f": center + offset,
                "estimated_bracket": "74-75",
                "uncertainty_spread_f": 1.0,
                "error_message": None,
                "raw": {},
            }
            for key, center in centers.items()
        ],
        "markets": [
            _market(f"KXHIGHLAX-{target_code}-T71", "<=71"),
            _market(f"KXHIGHLAX-{target_code}-B72", "72-73"),
            _market(f"KXHIGHLAX-{target_code}-B74", "74-75"),
            _market(f"KXHIGHLAX-{target_code}-T76", ">=76"),
        ],
        "observation": {
            "target_date": target.isoformat(),
            "station": "KLAX",
            "source": "test",
            "latest_temp_f": 72.0,
            "latest_observation_utc": captured_utc.isoformat(),
            "high_so_far_f": 72.0 if final_high is None else final_high,
            "final_high_f": final_high,
            "observation_count": 4,
            "error_message": None,
            "raw": {},
        },
    }


def _market(ticker: str, label: str) -> dict[str, object]:
    return {
        "ticker": ticker,
        "bracket_label": label,
        "yes_bid_cents": 20,
        "yes_ask_cents": 25,
        "no_bid_cents": 70,
        "no_ask_cents": 75,
        "yes_mid_cents": 22.5,
        "market_status": "open",
        "raw": {},
    }
