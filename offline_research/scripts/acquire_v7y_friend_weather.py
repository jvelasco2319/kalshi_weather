"""Finite, resumable GFS/NAM/NBM temperature backfill for calendar 2025."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import scripts.acquire_friend_method_weather as friend_weather
from scripts.acquire_friend_method_weather import (
    GFS_LEADS, NAM_LEADS, NBM_LEADS, Task, _daily, _run_task, _write_json,
)


START = date(2025, 1, 1)
END = date(2025, 12, 31)
STATE = Path("runs/v7y_friend_weather_backfill/recovery-state.json")
ARCHIVE = Path("data/raw/friend_method_weather_v1")
REQUEST_START_INTERVAL_SECONDS = 1.0
TASKS_PER_DATE = len(GFS_LEADS) + len(NAM_LEADS) + len(NBM_LEADS)
CONFIG = {
    "schema_version": "v7y-friend-weather-backfill-v2",
    "campaign_id": "v7y-calendar-2025",
    "date_start": START.isoformat(),
    "date_end": END.isoformat(),
    "target_date_count": (END - START).days + 1,
    "task_count_per_date": TASKS_PER_DATE,
    "request_start_limit_per_second": 1.0 / REQUEST_START_INTERVAL_SECONDS,
    "archive": ARCHIVE.as_posix(),
    "models": {
        "gfs": list(GFS_LEADS),
        "nam": list(NAM_LEADS),
        "nbm": list(NBM_LEADS),
    },
}
CONFIG_SHA256 = sha256(
    json.dumps(CONFIG, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "recovery_sha256"}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_state(path: Path, value: dict[str, Any]) -> None:
    value["updated_at_utc"] = now()
    value["recovery_sha256"] = canonical_hash(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if value.get("recovery_sha256") != canonical_hash(value):
            raise ValueError("friend-weather recovery hash mismatch")
        if value.get("config_sha256") != CONFIG_SHA256:
            raise ValueError("friend-weather recovery config mismatch")
        return value
    value = {
        **CONFIG,
        "config_sha256": CONFIG_SHA256,
        "status": "REGISTERED_NOT_STARTED",
        "completed_dates": [],
        "failed_dates": {},
        "completed_task_equivalents": 0,
        "network_used": False,
        "protected_labels_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "created_at_utc": now(),
    }
    write_state(path, value)
    return value


def tasks_for(target: date) -> list[Task]:
    return (
        [Task("gfs", target, lead) for lead in GFS_LEADS]
        + [Task("nam", target, lead) for lead in NAM_LEADS]
        + [Task("nbm", target, lead) for lead in NBM_LEADS]
    )


def run(root: Path, workers: int) -> dict[str, Any]:
    if not 1 <= workers <= 16:
        raise ValueError("workers must be in [1,16]")
    state_path = root / STATE
    state = load_state(state_path)
    friend_weather.REQUEST_START_INTERVAL_SECONDS = REQUEST_START_INTERVAL_SECONDS
    state["status"] = "RUNNING"
    state["process_id"] = __import__("os").getpid()
    state.setdefault("started_at_utc", now())
    write_state(state_path, state)
    archive = root / ARCHIVE
    targets = [START + timedelta(days=i) for i in range(365)]
    for target in targets:
        state = load_state(state_path)
        if target.isoformat() in state["completed_dates"]:
            continue
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(_run_task, archive, task): task for task in tasks_for(target)}
            for future in as_completed(pending):
                task = pending[future]
                try:
                    future.result()
                except Exception as exc:
                    failures[task.key] = f"{type(exc).__name__}: {exc}"
        if failures:
            state = load_state(state_path)
            state["failed_dates"][target.isoformat()] = failures
            state["status"] = "FAILED"
            write_state(state_path, state)
            raise RuntimeError(f"friend weather failed for {target}: {failures}")
        daily = _daily(archive, target)
        if not daily["complete"]:
            state = load_state(state_path)
            state["failed_dates"][target.isoformat()] = {
                "daily": "daily output remained incomplete after all tasks returned",
            }
            state["status"] = "FAILED"
            write_state(state_path, state)
            raise RuntimeError(f"friend weather incomplete for {target}")
        daily_path = archive / f"date={target.isoformat()}" / "daily.json"
        _write_json(daily_path, daily)
        state = load_state(state_path)
        state["completed_dates"].append(target.isoformat())
        state["completed_dates"].sort()
        state["completed_task_equivalents"] = len(state["completed_dates"]) * TASKS_PER_DATE
        state["failed_dates"].pop(target.isoformat(), None)
        state["last_completed_date"] = target.isoformat()
        state["last_daily_path"] = daily_path.relative_to(root).as_posix()
        state["network_used"] = True
        write_state(state_path, state)
        print(json.dumps({"date": target.isoformat(), "complete": len(state["completed_dates"]), "target": 365}), flush=True)
    state = load_state(state_path)
    state["status"] = "COMPLETE"
    state["completed_at_utc"] = now()
    write_state(state_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    result = run(Path(args.project_root).resolve(), args.workers)
    print(json.dumps({
        "status": result["status"],
        "completed": len(result["completed_dates"]),
        "failed": len(result["failed_dates"]),
        "target": result["target_date_count"],
        "recovery_sha256": result["recovery_sha256"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
