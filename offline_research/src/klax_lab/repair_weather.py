"""Finite repair of the registered 2024-2025 historical weather acquisition.

This command never runs beside an active weather CLI job. It requires a terminal
full-range manifest, repairs only its incomplete dates with at most two persisted
attempts per date, then verifies the entire registered range with HTTP disabled.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from .acquire_weather import (
    RateLimitedSession, TransferBudget, _cli_job_lock,
    acquire_day, acquire_range,
)

START = date(2024, 1, 1)
END = date(2025, 12, 31)
MODELS = ("gfs", "nbm")
LEADS = [8, 9, 12, 15, 18, 21, 24, 27, 30, 31]
EXPECTED_DAYS = 731
MAX_ATTEMPTS_PER_DATE = 2
WEATHER_BYTE_CAP = 50_000_000_000


class RepairBlocked(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _manifest_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to((root / "data/manifests/weather").resolve()):
        raise ValueError("weather manifest reference escapes the registered manifest directory")
    if not path.is_file():
        raise ValueError("referenced weather manifest is missing")
    return path


def _read_json(path: Path) -> dict:
    if path.stat().st_size > 10_000_000:
        raise ValueError("weather manifest exceeds bounded size")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("weather manifest must be a JSON object")
    return value


def _expected_dates() -> set[str]:
    return {(START + timedelta(days=offset)).isoformat() for offset in range(EXPECTED_DAYS)}


def _range_rows(root: Path, manifest: dict) -> list[dict]:
    if manifest.get("start_date") != START.isoformat() or manifest.get("end_date") != END.isoformat() or sorted(manifest.get("model_sources", [])) != list(MODELS):
        raise ValueError("repair supports only the registered 2024-2025 GFS/NBM range")
    if manifest.get("status") not in ("COMPLETED", "COMPLETED_WITH_GAPS"):
        raise RepairBlocked("the original full-range acquisition is not terminal")
    rows = []
    monthly_paths = manifest.get("monthly_progress", [])
    if not monthly_paths or len(monthly_paths) > 24 or len(set(monthly_paths)) != len(monthly_paths):
        raise ValueError("terminal acquisition needs unique bounded monthly progress paths")
    for reference in monthly_paths:
        monthly = _read_json(_manifest_path(root, reference))
        if monthly.get("run_id") != manifest.get("run_id"):
            raise ValueError("monthly progress belongs to another acquisition run")
        month_rows = monthly.get("days", [])
        if not isinstance(month_rows, list) or len(month_rows) > 31:
            raise ValueError("monthly progress has invalid daily records")
        for row in month_rows:
            if not isinstance(row.get("complete"), bool):
                raise ValueError("day completion must be a boolean")
            if row.get("date", "")[:7] != monthly.get("month"):
                raise ValueError("day record does not belong to its progress month")
            if row["complete"] and row.get("errors"):
                raise ValueError("completed day still contains acquisition errors")
            rows.append(row)
    dates = [row.get("date") for row in rows]
    if len(dates) != EXPECTED_DAYS or len(set(dates)) != len(dates) or set(dates) != _expected_dates():
        raise ValueError("terminal source must contain exactly 731 distinct registered dates")
    complete = sum(row["complete"] for row in rows)
    if manifest.get("days_complete") != complete or manifest.get("days_with_errors") != EXPECTED_DAYS - complete:
        raise ValueError("terminal acquisition counts disagree with monthly progress")
    return sorted(rows, key=lambda row: row["date"])


def select_source(root: Path, source_manifest: Path | None = None) -> tuple[Path, dict, list[dict]]:
    if source_manifest is not None:
        path = _manifest_path(root, source_manifest)
        manifest = _read_json(path)
        return path, manifest, _range_rows(root, manifest)
    # Completed offline verification files must not become a new repair source.
    used = set()
    repair_directory = root / "data/manifests/weather/repair"
    if repair_directory.exists():
        for path in repair_directory.glob("*.json"):
            prior = _read_json(path)
            if prior.get("integrity_manifest"):
                used.add(str(Path(prior["integrity_manifest"]).resolve()))
            for previous in prior.get("integrity_history", []):
                used.add(str(Path(previous["path"]).resolve()))
    candidates = sorted((root / "data/manifests/weather").glob("range_*.json"), reverse=True)
    for path in candidates:
        if str(path.resolve()) in used:
            continue
        manifest = _read_json(path)
        if manifest.get("start_date") != START.isoformat() or manifest.get("end_date") != END.isoformat() or sorted(manifest.get("model_sources", [])) != list(MODELS):
            continue
        return path.resolve(), manifest, _range_rows(root, manifest)
    raise RepairBlocked("no terminal registered full-range acquisition manifest is available")


def _day_complete(report: dict) -> bool:
    return (
        report.get("requested_leads") == LEADS
        and not report.get("errors")
        and set(report.get("models", {})) == set(MODELS)
        and all(item.get("status") == "COMPLETE_SAMPLED_PROXY" for item in report["models"].values())
    )


def repair_weather(root: Path, source_manifest: Path | None = None) -> dict:
    """Perform finite authorized repairs only after exclusive ownership is obtained."""
    root = Path(root).resolve()
    with _cli_job_lock(root):
        source_path, source, rows = select_source(root, source_manifest)
        source_hash = sha256(source_path.read_bytes()).hexdigest()
        source_id = source.get("run_id", "")
        if not source_id or any(character not in "0123456789TZ" for character in source_id):
            raise ValueError("acquisition run identifier is malformed")
        gaps = [row["date"] for row in rows if not row["complete"]]
        journal_path = root / "data/manifests/weather/repair" / f"{source_id}.json"
        if journal_path.exists():
            journal = _read_json(journal_path)
            if journal.get("source_sha256") != source_hash or journal.get("original_gap_dates") != gaps:
                raise ValueError("repair journal disagrees with its immutable source acquisition")
        else:
            journal = {
                "status": "CREATED", "source_run_id": source_id,
                "source_manifest": str(source_path), "source_sha256": source_hash,
                "original_gap_dates": gaps, "max_attempts_per_date": MAX_ATTEMPTS_PER_DATE,
                "attempts": {day: [] for day in gaps}, "started_at": _now(),
                "fixed_start_date": START.isoformat(), "fixed_end_date": END.isoformat(),
                "models": list(MODELS), "leads": LEADS, "request_rate_per_second": 2,
                "inference_or_trading_performed": False,
            }
        # A prior successful journal cannot trigger additional repair requests.
        if journal.get("status") == "COMPLETE":
            integrity_path = _manifest_path(root, journal["integrity_manifest"])
            if sha256(integrity_path.read_bytes()).hexdigest() != journal.get("integrity_manifest_sha256"):
                raise ValueError("completed repair integrity manifest changed")
            integrity = _read_json(integrity_path)
            integrity_rows = _range_rows(root, integrity)
            if integrity.get("new_transfer_bytes") != 0 or not all(row["complete"] for row in integrity_rows):
                raise ValueError("completed repair lacks a clean offline terminal record")
            return _result(journal, journal_path, cached=True)
        for day in gaps:
            attempts = journal.get("attempts", {}).get(day)
            if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS_PER_DATE:
                raise ValueError("persisted repair attempt budget is invalid")
        journal["status"] = "REPAIRING"
        raw = root / "data/raw/weather"
        existing_bytes = sum(path.stat().st_size for path in raw.rglob("*") if path.is_file()) if raw.exists() else 0
        # Include discarded response bytes from earlier repair attempts, rather
        # than resetting the transfer allowance to current storage on each run.
        journal.setdefault("weather_bytes_at_repair_start", existing_bytes)
        previous_transfer = journal.setdefault("repair_transfer_bytes_total", 0)
        if not isinstance(previous_transfer, int) or previous_transfer < 0:
            raise ValueError("persisted repair transfer count is invalid")
        accounted_bytes = max(existing_bytes, journal["weather_bytes_at_repair_start"] + previous_transfer)
        allowance = WEATHER_BYTE_CAP - accounted_bytes
        _save(journal_path, journal)
        budget = TransferBudget(max(1, allowance))  # Unused if no network slots remain.
        session = None
        executor = None
        try:
            for _ in range(MAX_ATTEMPTS_PER_DATE):
                for day in gaps:
                    attempts = journal["attempts"][day]
                    if len(attempts) >= MAX_ATTEMPTS_PER_DATE or any(attempt.get("status") == "COMPLETE" for attempt in attempts):
                        continue
                    if allowance <= 0:
                        raise RepairBlocked("project weather byte budget is exhausted")
                    # Persist dispatch before acquisition. An interrupted attempt
                    # consumes its slot, preventing unbounded retries after restart.
                    attempt = {"attempt": len(attempts) + 1, "status": "STARTED", "started_at": _now()}
                    attempts.append(attempt)
                    _save(journal_path, journal)
                    if session is None:
                        session = RateLimitedSession(2.0)
                        executor = ThreadPoolExecutor(max_workers=4)
                    try:
                        report = acquire_day(
                            root, date.fromisoformat(day), MODELS,
                            include_boundary_samples=True, offline=False,
                            transfer_budget=budget, session_override=session,
                            executor_override=executor, download_workers=4, verbose=False,
                        )
                        attempt.update({"status": "COMPLETE" if _day_complete(report) else "GAPS_REMAIN", "manifest": report.get("manifest_path"), "errors": report.get("errors", []), "new_transfer_bytes": report.get("new_transfer_bytes", 0)})
                    except Exception as exc:
                        attempt.update({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)})
                        if "budget exhausted" in str(exc):
                            raise
                    finally:
                        attempt["finished_at"] = _now()
                        journal["repair_transfer_bytes_this_invocation"] = budget.transferred
                        journal["repair_transfer_bytes_total"] = previous_transfer + budget.transferred
                        _save(journal_path, journal)
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            if session is not None:
                session.close()
        journal["status"] = "OFFLINE_VERIFY"
        _save(journal_path, journal)
        # This path constructs no HTTP session and performs no acquisition.
        integrity = acquire_range(
            root, START, END, MODELS, include_boundary_samples=True,
            offline=True, max_transfer_bytes=1, download_workers=4,
        )
        integrity_path = _manifest_path(root, integrity["manifest_path"])
        integrity_rows = _range_rows(root, integrity)
        remaining = [row["date"] for row in integrity_rows if not row["complete"]]
        if integrity.get("new_transfer_bytes") != 0:
            raise ValueError("offline integrity verification unexpectedly transferred data")
        journal.update({
            "status": "COMPLETE" if not remaining else "INCOMPLETE",
            "remaining_gap_dates": remaining,
            "integrity_manifest": str(integrity_path),
            "integrity_manifest_sha256": sha256(integrity_path.read_bytes()).hexdigest(),
            "integrity_days_complete": integrity["days_complete"],
            "integrity_days_with_errors": integrity["days_with_errors"],
            "integrity_transfer_bytes": integrity["new_transfer_bytes"],
            "finished_at": _now(),
        })
        journal.setdefault("integrity_history", []).append({"path": str(integrity_path), "sha256": journal["integrity_manifest_sha256"]})
        _save(journal_path, journal)
        return _result(journal, journal_path)


def _result(journal: dict, path: Path, *, cached: bool = False) -> dict:
    return {
        "status": journal["status"], "journal_path": str(path),
        "source_manifest": journal["source_manifest"],
        "original_gap_count": len(journal["original_gap_dates"]),
        "remaining_gap_count": len(journal.get("remaining_gap_dates", [])),
        "remaining_gap_dates": journal.get("remaining_gap_dates", []),
        "attempt_count": sum(len(attempts) for attempts in journal["attempts"].values()),
        "integrity_manifest": journal.get("integrity_manifest"),
        "integrity_days_complete": journal.get("integrity_days_complete"),
        "integrity_days_with_errors": journal.get("integrity_days_with_errors"),
        "integrity_days_total": journal.get("integrity_days_complete", 0) + journal.get("integrity_days_with_errors", 0),
        "integrity_transfer_bytes": journal.get("integrity_transfer_bytes"),
        "cached_completion": cached,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            result = repair_weather(args.root, args.source_manifest)
    except RepairBlocked as exc:
        result = {"status": "BLOCKED", "reason": str(exc)}
    except RuntimeError as exc:
        result = {"status": "BLOCKED" if "another historical weather CLI job" in str(exc) else "FAILED", "reason": str(exc)}
    except (OSError, ValueError) as exc:
        result = {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, indent=2))
    verified_terminal = (
        result["status"] in ("COMPLETE", "INCOMPLETE")
        and result.get("integrity_days_total") == EXPECTED_DAYS
        and result.get("integrity_transfer_bytes") == 0
        and bool(result.get("integrity_manifest"))
    )
    return 0 if verified_terminal else 2


if __name__ == "__main__":
    raise SystemExit(main())
