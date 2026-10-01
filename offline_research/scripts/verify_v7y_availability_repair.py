"""Outcome-blind final verification of the authorized V7Y cache-only repair."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import pandas as pd
from scripts.acquire_v7y_weather_history import (
    STATE, artifact_hash, file_hash, load_state, target_dates, validate_repair_parent,
)
from scripts.v7y_weather_cache import OUTPUT_ROOT, verify_policy_registration


def run(root: Path) -> dict:
    registration = verify_policy_registration(root)
    snapshot = registration["repair_prestate"]
    state = load_state(root / STATE)
    validate_repair_parent(root, state, registration)
    if (state["status"] != "COMPLETE" or state["completed_dates"] != target_dates()
            or state["normalization_completed_dates"] != target_dates()
            or state["failed_dates"] or state["unavailable_dates"]
            or state.get("last_repair_cache_only") is not True
            or state.get("transferred_bytes_last_repair") != 0):
        raise ValueError("V7Y repair is not complete and cache-only")
    preserved = snapshot["preserved_artifacts"]
    for relative, expected in preserved.items():
        path = root / relative
        if (path.stat().st_size != expected["bytes"]
                or file_hash(path) != expected["sha256"]):
            raise ValueError(f"Preserved artifact changed: {relative}")
    unchanged_rows = 0
    policy_columns = {"partition", "availability_policy_id", "as_of_validation_basis"}
    for day in snapshot["prior_recovery_state"]["completed_dates"]:
        for name in ("hrrr_points.parquet", "gefs_summary_points.parquet"):
            legacy = root / "data/normalized/v5p_probability_features" / f"date={day}" / name
            repaired = root / OUTPUT_ROOT / f"date={day}" / name
            old, new = pd.read_parquet(legacy), pd.read_parquet(repaired)
            if set(old.columns) != set(new.columns):
                raise ValueError(f"Migrated schema changed: {day} {name}")
            columns = sorted(set(old.columns) - policy_columns)
            pd.testing.assert_frame_equal(old[columns], new[columns], check_dtype=False, check_exact=True)
            unchanged_rows += len(old)
    audit_path = root / "runs/v7y_weather_backfill/incremental-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if (audit.get("audit_sha256") != artifact_hash(audit, "audit_sha256")
            or audit.get("status") != "PASS"
            or audit.get("recovery_sha256") != state["recovery_sha256"]
            or audit.get("completed_date_count") != 184
            or audit.get("newly_audited_date_count") != 184
            or audit.get("reused_verified_date_count") != 0
            or audit.get("network_used") is not False
            or audit.get("protected_labels_read") is not False):
        raise ValueError("Full integrity audit differs")
    coverage_path = root / "runs/v7y_weather_backfill/calendar-coverage-post-repair.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    if (coverage.get("registered_market_days") != 361
            or coverage.get("raw_valid_days") != 361
            or coverage.get("normalized_valid_days") != 361
            or coverage.get("raw_missing_days") or coverage.get("raw_incomplete_or_corrupt")
            or coverage.get("normalized_missing_days") or coverage.get("normalized_corrupt")
            or coverage.get("network_used") is not False
            or coverage.get("settlement_labels_read") is not False):
        raise ValueError("Calendar coverage audit differs")
    timing = {}
    for day in ("2025-08-15", "2025-12-18", "2025-12-19"):
        frame = pd.read_parquet(root / OUTPUT_ROOT / f"date={day}/gefs_summary_points.parquet")
        timing[day] = {
            "gefs_rows": len(frame),
            "effective_availability_earliest_utc": min(frame["effective_information_available_at_utc"]),
            "effective_availability_latest_utc": max(frame["effective_information_available_at_utc"]),
            "eligible_decision_times_utc": sorted({tuple(value) for value in frame["eligible_decision_times_utc"]}),
        }
    document = {
        "schema_version": "v7y-availability-repair-verification-v1",
        "status": "PASS", "verified_at_utc": datetime.now(UTC).isoformat(),
        "campaign_id": state["campaign_id"], "recovery_sha256": state["recovery_sha256"],
        "parent_recovery_sha256": state["availability_repair_parent_recovery_sha256"],
        "policy_sha256": registration["policy_sha256"],
        "prestate_sha256": snapshot["snapshot_sha256"],
        "backfill_completed_dates": len(state["completed_dates"]),
        "backfill_target_dates": 184, "failed_dates": [], "unavailable_dates": [],
        "repaired_dates": sorted(snapshot["prior_recovery_state"]["failed_dates"]),
        "preserved_artifacts_verified": len(preserved),
        "migrated_legacy_dates": len(snapshot["prior_recovery_state"]["completed_dates"]),
        "migrated_legacy_rows_unchanged_except_policy_metadata": unchanged_rows,
        "raw_objects_fully_verified": audit["raw_hrrr_object_count"] + audit["raw_gefs_object_count"],
        "backfill_normalized_rows": audit["normalized_hrrr_row_count"] + audit["normalized_gefs_row_count"],
        "registered_calendar_dates_verified": coverage["normalized_valid_days"],
        "outside_market_universe_dates": coverage["outside_market_universe_days"],
        "network_requests_this_repair": 0, "downloaded_bytes_this_repair": 0,
        "protected_labels_read": False, "paper_orders_placed": 0, "live_orders_placed": 0,
        "final_study_freeze_or_score_run": False, "gfs_nam_nbm_track_resumed": False,
        "repaired_gefs_timing": timing,
        "full_audit_sha256": audit["audit_sha256"],
        "calendar_coverage_file_sha256": file_hash(coverage_path),
        "limitations": [
            "Archived Last-Modified and nominal cycles bound availability; original publication time is not independently proven.",
            "Weather readiness does not establish executable fills or profitability.",
            "January 1-4 remain outside the registered market universe; 30 calibration-overlap dates are not primary return-scoring dates.",
        ],
    }
    document["verification_sha256"] = artifact_hash(document, "verification_sha256")
    output = root / "runs/v7y_weather_backfill/repair-verification.json"
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


if __name__ == "__main__":
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("V7Y repair verification denies network access")
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    print(json.dumps(run(ROOT), indent=2, sort_keys=True))
