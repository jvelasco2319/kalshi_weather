"""Register the one-time attempt-4 completion-metrics correction.

The acquisition writer preserved the per-day transfer metrics but dropped their
sum when it wrote the terminal attempt record.  This utility is deliberately
state-specific: it refuses to run unless the terminal progress and journal are
the exact affected shapes, preserves the original journal bytes, and binds both
source artifacts into an immutable correction record before updating the live
journal.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


JOURNAL = Path("data/manifests/v3_weather_acquisition_attempts.json")
SNAPSHOT = Path(
    "data/manifests/v3_weather_acquisition_attempts.pre-completion-correction.json")
PROGRESS = Path("data/manifests/v3_weather_bulk_progress.json")
CORRECTION = Path(
    "data/manifests/v3_weather_acquisition_attempt4_completion_correction.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected one JSON object: {path}")
    return value


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def register(root: Path) -> dict[str, Any]:
    root = root.resolve()
    journal_path = root / JOURNAL
    snapshot_path = root / SNAPSHOT
    progress_path = root / PROGRESS
    correction_path = root / CORRECTION
    if snapshot_path.exists() or correction_path.exists():
        raise RuntimeError("Completion-metrics correction evidence already exists")

    journal = _read(journal_path)
    progress = _read(progress_path)
    attempts = journal.get("attempts")
    days = progress.get("days")
    if (journal.get("schema_version") != 3
            or journal.get("protected_final_read") is not False
            or not isinstance(attempts, list) or len(attempts) != 4
            or not isinstance(days, list) or len(days) != 543
            or progress.get("status") != "FULL_RAW_RANGE_ACQUISITION_COMPLETE"
            or progress.get("coverage_complete") is not True
            or progress.get("protected_final_read") is not False):
        raise RuntimeError("Live acquisition evidence is not the affected terminal state")
    final = attempts[-1]
    if (final.get("attempt") != 4 or final.get("pid") != 25852
            or final.get("terminal_status") != "COMPLETE_543_DAYS_CACHE_VERIFIED"
            or final.get("committed_days") != 543
            or final.get("network_requests") != 10_232
            or final.get("new_transfer_bytes") != 0):
        raise RuntimeError("Attempt 4 does not match the registered affected state")

    for row in days:
        if (not isinstance(row, dict)
                or type(row.get("network_requests")) is not int
                or row["network_requests"] < 0
                or type(row.get("new_transfer_bytes")) is not int
                or row["new_transfer_bytes"] < 0):
            raise RuntimeError("Progress has an invalid daily completion metric")
    request_sum = sum(row["network_requests"] for row in days)
    byte_sum = sum(row["new_transfer_bytes"] for row in days)
    if request_sum != 10_232 or byte_sum != 6_448_502_823:
        raise RuntimeError("Authoritative daily completion-metric sums differ")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(journal_path, snapshot_path)
    snapshot_hash = _sha256(snapshot_path)
    progress_hash = _sha256(progress_path)
    correction = {
        "schema_version": 1,
        "component": "v3_weather_acquisition_attempt4_completion_metrics_correction",
        "status": "REGISTERED_POST_COMPLETION_FROM_IMMUTABLE_DAILY_PROGRESS",
        "registered_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="microseconds").replace("+00:00", "Z"),
        "attempt": 4,
        "pid": 25852,
        "pre_correction_journal": {
            "path": SNAPSHOT.as_posix(),
            "sha256": snapshot_hash,
        },
        "authoritative_final_progress": {
            "path": PROGRESS.as_posix(),
            "sha256": progress_hash,
        },
        "prior_metrics": {
            "network_requests": final["network_requests"],
            "new_transfer_bytes": final["new_transfer_bytes"],
        },
        "corrected_metrics": {
            "network_requests": request_sum,
            "new_transfer_bytes": byte_sum,
        },
        "daily_records": {
            "rows": len(days),
            "network_requests_sum": request_sum,
            "new_transfer_bytes_sum": byte_sum,
        },
        "reason": (
            "The terminal combined progress manifest removed the phase-local "
            "top-level new_transfer_bytes field; the authoritative completion "
            "metrics are the sums of its immutable daily records."
        ),
        "scope_changed": False,
        "scientific_or_evaluation_policy_changed": False,
        "gate_changed": False,
        "protected_final_read": False,
    }
    _atomic_json(correction_path, correction)

    final["network_requests"] = request_sum
    final["new_transfer_bytes"] = byte_sum
    journal["completion_metrics_correction"] = {
        "path": CORRECTION.as_posix(),
        "sha256": _sha256(correction_path),
    }
    _atomic_json(journal_path, journal)
    return correction


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    correction = register(args.root)
    print(json.dumps(correction["corrected_metrics"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
