"""Finite, resumable HRRR/GEFS backfill for the 2025 calendar-year replay."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
from ctypes import wintypes
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
import os
import socket
from pathlib import Path
import sys
from threading import Lock
from time import sleep
from typing import Any
from contextlib import contextmanager

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.acquire_weather import RateLimitedSession, TransferBudget
from klax_lab.acquire_weather_v3 import HistoricalWeatherUnavailable, _acquire_plan
from klax_lab.weather_sources_v3 import verify_cache_only
from v5.acquire_probability_evidence import build_v5_daily_weather_plans
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT, POLICY_CONFIG_PATH, POLICY_ID,
    normalized_day as _normalized_day,
    validate_normalized_day,
    verify_policy_registration,
)


START = date(2025, 7, 1)
END = date(2025, 12, 31)
STATE = Path("runs/v7y_weather_backfill/recovery-state.json")
CACHE = Path("data/raw/v5p/weather")
MAX_TRANSFER_BYTES = 30_000_000_000
MAX_DATE_ATTEMPTS = 5


def target_dates() -> list[str]:
    return [
        (START + timedelta(days=index)).isoformat()
        for index in range((END - START).days + 1)
    ]


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "recovery_sha256"}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def artifact_hash(value: dict[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_exists(process_id: int) -> bool:
    """Read-only Windows PID check used to refuse a duplicate acquisition."""
    if process_id <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def validate_state(value: dict[str, Any]) -> None:
    expected = set(target_dates())
    if (
        value.get("schema_version") != "v7y-weather-backfill-v1"
        or value.get("campaign_id") != "v7y-calendar-2025"
        or value.get("date_start") != START.isoformat()
        or value.get("date_end") != END.isoformat()
        or value.get("target_date_count") != len(expected)
    ):
        raise ValueError("weather recovery identity differs")
    completed = value.get("completed_dates")
    unavailable = value.get("unavailable_dates")
    failed = value.get("failed_dates")
    if not isinstance(completed, list) or not isinstance(unavailable, list) or not isinstance(failed, dict):
        raise ValueError("weather recovery counters are malformed")
    if len(completed) != len(set(completed)) or len(unavailable) != len(set(unavailable)):
        raise ValueError("weather recovery contains duplicate dates")
    completed_set, unavailable_set, failed_set = set(completed), set(unavailable), set(failed)
    if not (completed_set | unavailable_set | failed_set) <= expected:
        raise ValueError("weather recovery contains an out-of-window date")
    if completed_set & unavailable_set or completed_set & failed_set or unavailable_set & failed_set:
        raise ValueError("weather recovery date states overlap")
    if (
        value.get("protected_labels_read") is not False
        or value.get("paper_orders_placed") != 0
        or value.get("live_orders_placed") != 0
    ):
        raise ValueError("weather recovery safety boundary differs")


@contextmanager
def exclusive_run(root: Path):
    """Atomic local ownership, with dead-owner recovery and no duplicate workers."""
    lock_path = root / "runs/v7y_weather_backfill/normalization.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps({"process_id": os.getpid(), "started_at_utc": now()}))
            break
        except FileExistsError:
            prior = json.loads(lock_path.read_text(encoding="utf-8"))
            if process_exists(int(prior["process_id"])) or attempt:
                raise RuntimeError("V7Y normalization is already owned by another process")
            lock_path.unlink()
    try:
        yield
    finally:
        record = json.loads(lock_path.read_text(encoding="utf-8"))
        if record.get("process_id") == os.getpid():
            lock_path.unlink()


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
            raise ValueError("weather recovery hash mismatch")
        validate_state(value)
        return value
    days = (END - START).days + 1
    value = {
        "schema_version": "v7y-weather-backfill-v1",
        "campaign_id": "v7y-calendar-2025",
        "status": "REGISTERED_NOT_STARTED",
        "date_start": START.isoformat(),
        "date_end": END.isoformat(),
        "target_date_count": days,
        "completed_dates": [],
        "unavailable_dates": [],
        "failed_dates": {},
        "network_used": False,
        "protected_labels_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "created_at_utc": now(),
    }
    write_state(path, value)
    return value


def acquire_one(root: Path, target: date, session: RateLimitedSession,
                budget: TransferBudget) -> dict[str, Any]:
    hrrr, gefs = build_v5_daily_weather_plans(target)
    cache = root / CACHE
    last_error: Exception | None = None
    for attempt in range(1, MAX_DATE_ATTEMPTS + 1):
        before = budget.transferred
        try:
            h_result = _acquire_plan(hrrr, cache, session, budget)
            g_result = _acquire_plan(gefs, cache, session, budget)
            h_verify = verify_cache_only(hrrr, cache)
            g_verify = verify_cache_only(gefs, cache)
            return {
                "date": target.isoformat(),
                "attempts": attempt,
                "network_requests": h_result["network_requests"] + g_result["network_requests"],
                "cache_hits": h_result["cache_hits"] + g_result["cache_hits"],
                "transferred_bytes": budget.transferred - before,
                "hrrr_objects": h_verify["objects_verified"],
                "gefs_objects": g_verify["objects_verified"],
            }
        except HistoricalWeatherUnavailable:
            raise
        except Exception as exc:
            last_error = exc
            if attempt == MAX_DATE_ATTEMPTS:
                break
            sleep(min(30, 2 ** attempt))
    assert last_error is not None
    raise last_error


def cached_one(root: Path, target: date) -> dict[str, Any]:
    plans = build_v5_daily_weather_plans(target)
    verified = [verify_cache_only(plan, root / CACHE) for plan in plans]
    for record, expected_count in zip(verified, (4, 16), strict=True):
        if (record.get("status") != "CACHE_COMPATIBILITY_PASS"
                or record.get("network_used") is not False
                or record.get("protected_final_read") is not False
                or record.get("objects_verified") != expected_count):
            raise ValueError("V7Y cache verification scope differs")
    return {
        "date": target.isoformat(), "attempts": 1,
        "network_requests": 0, "cache_hits": sum(v["objects_verified"] for v in verified),
        "transferred_bytes": 0,
        "hrrr_objects": verified[0]["objects_verified"],
        "gefs_objects": verified[1]["objects_verified"],
    }


@contextmanager
def network_boundary(cache_only: bool):
    if not cache_only:
        yield
        return
    def deny_network(*_args, **_kwargs):
        raise RuntimeError("V7Y cache-only repair denies network access")
    originals = (socket.create_connection, socket.socket.connect, socket.socket.connect_ex)
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    try:
        yield
    finally:
        socket.create_connection, socket.socket.connect, socket.socket.connect_ex = originals


def run(root: Path, workers: int, *, cache_only: bool = False) -> dict[str, Any]:
    with exclusive_run(root), network_boundary(cache_only):
        return _run(root, workers, cache_only=cache_only)


def validate_repair_parent(root: Path, state: dict[str, Any], registration: dict[str, Any]) -> None:
    """Bind the repair to the saved original campaign without refunding history."""
    policy = registration["policy"]
    snapshot_path = root / policy["repair_prestate_path"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if (snapshot.get("snapshot_sha256") != artifact_hash(snapshot, "snapshot_sha256")
            or snapshot["snapshot_sha256"] != policy["repair_prestate_sha256"]):
        raise ValueError("V7Y repair prestate seal differs")
    prior = snapshot["prior_recovery_state"]
    if prior.get("recovery_sha256") != canonical_hash(prior):
        raise ValueError("V7Y saved parent recovery seal differs")
    validate_state(prior)
    if state.get("availability_registration") is None:
        if state["recovery_sha256"] != prior["recovery_sha256"]:
            raise ValueError("V7Y initial repair recovery differs from registered prestate")
    elif state.get("availability_repair_parent_recovery_sha256") != prior["recovery_sha256"]:
        raise ValueError("V7Y resumed repair parent differs from registered prestate")
    if not set(prior["completed_dates"]) <= set(state["completed_dates"]):
        raise ValueError("V7Y repair removed originally completed dates")
    if state["unavailable_dates"] != prior["unavailable_dates"]:
        raise ValueError("V7Y repair reclassified unavailable dates")
    for key in ("created_at_utc", "started_at_utc", "network_used", "transferred_bytes_this_process",
                "cumulative_request_count", "cumulative_transferred_bytes"):
        if key in prior and state.get(key) != prior[key]:
            raise ValueError(f"V7Y repair changed preserved counter: {key}")


def _run(root: Path, workers: int, *, cache_only: bool) -> dict[str, Any]:
    if not 1 <= workers <= 8:
        raise ValueError("workers must be in [1,8]")
    policy = verify_policy_registration(root)
    state_path = root / STATE
    state = load_state(state_path)
    validate_repair_parent(root, state, policy)
    prior_process_id = int(state.get("process_id") or 0)
    if (
        state.get("status") in {"RUNNING", "RUNNING_WITH_RETRYABLE_FAILURES", "NORMALIZING_EXISTING_CACHE"}
        and prior_process_id != os.getpid()
        and process_exists(prior_process_id)
    ):
        raise RuntimeError(f"weather acquisition is already running as PID {prior_process_id}")
    done = set(state["completed_dates"]) | set(state["unavailable_dates"])
    targets = [START + timedelta(days=i) for i in range((END - START).days + 1)]
    pending_targets = [target for target in targets if target.isoformat() not in done]
    registration = {
        "path": POLICY_CONFIG_PATH.as_posix(),
        "sha256": file_hash(root / POLICY_CONFIG_PATH),
        "policy_sha256": policy["policy_sha256"],
    }
    if state.get("availability_registration") not in (None, registration):
        raise ValueError("V7Y recovery availability binding changed")
    if state.get("availability_registration") is None:
        state["availability_repair_parent_recovery_sha256"] = state["recovery_sha256"]
    state["availability_registration"] = registration
    state["availability_policy_id"] = POLICY_ID
    state["normalized_output_root"] = OUTPUT_ROOT.as_posix()
    state["last_repair_cache_only"] = cache_only
    state["status"] = "NORMALIZING_EXISTING_CACHE"
    state["process_id"] = __import__("os").getpid()
    state.setdefault("started_at_utc", now())
    write_state(state_path, state)
    # The 181 existing dates get the same policy as the three repaired dates.
    # Forecast values and legacy artifacts remain untouched in their original root.
    migrated = set(state.get("normalization_completed_dates", []))
    for index, day in enumerate(state["completed_dates"], 1):
        manifest = _normalized_day(root, date.fromisoformat(day))
        validate_normalized_day(root, date.fromisoformat(day), manifest)
        migrated.add(day)
        state["normalization_completed_dates"] = sorted(migrated)
        write_state(state_path, state)
        if index % 30 == 0 or index == len(state["completed_dates"]):
            print(json.dumps({"normalized_existing": index, "existing_count": len(state["completed_dates"])}), flush=True)
    state["status"] = "RUNNING"
    write_state(state_path, state)
    session = None if cache_only else RateLimitedSession(2.0)
    budget = TransferBudget(MAX_TRANSFER_BYTES)
    state_lock = Lock()

    def persist_result(result: dict[str, Any], target: date) -> None:
        nonlocal state
        manifest = _normalized_day(root, target)
        validate_normalized_day(root, target, manifest)
        with state_lock:
            state = load_state(state_path)
            if target.isoformat() not in state["completed_dates"]:
                state["completed_dates"].append(target.isoformat())
                state["completed_dates"].sort()
            state["failed_dates"].pop(target.isoformat(), None)
            normalized = set(state.get("normalization_completed_dates", []))
            normalized.add(target.isoformat())
            state["normalization_completed_dates"] = sorted(normalized)
            state["last_completed_date"] = target.isoformat()
            state["last_result"] = result
            state["last_normalized_manifest_sha256"] = manifest["manifest_sha256"]
            state["network_used"] = state["network_used"] or result["network_requests"] > 0
            state["transferred_bytes_last_repair"] = budget.transferred
            write_state(state_path, state)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                (pool.submit(cached_one, root, target) if cache_only else
                 pool.submit(acquire_one, root, target, session, budget)): target
                for target in pending_targets
            }
            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result()
                    persist_result(result, target)
                    print(json.dumps({
                        "date": target.isoformat(),
                        "complete": len(load_state(state_path)["completed_dates"]),
                        "target": len(targets),
                        "bytes": budget.transferred,
                    }), flush=True)
                except HistoricalWeatherUnavailable as exc:
                    with state_lock:
                        state = load_state(state_path)
                        if target.isoformat() not in state["unavailable_dates"]:
                            state["unavailable_dates"].append(target.isoformat())
                            state["unavailable_dates"].sort()
                        state["last_unavailable_date"] = target.isoformat()
                        state["last_unavailable_reason"] = str(exc)
                        state["failed_dates"].pop(target.isoformat(), None)
                        write_state(state_path, state)
                except Exception as exc:
                    with state_lock:
                        state = load_state(state_path)
                        state["failed_dates"][target.isoformat()] = {
                            "type": type(exc).__name__, "message": str(exc)
                        }
                        state["status"] = "RUNNING_WITH_RETRYABLE_FAILURES"
                        write_state(state_path, state)
                    print(json.dumps({
                        "date": target.isoformat(),
                        "failed": True,
                        "error_type": type(exc).__name__,
                    }), flush=True)
    finally:
        if session is not None:
            session.close()
    state = load_state(state_path)
    if (len(state["completed_dates"]) == len(targets)
            and state.get("normalization_completed_dates") == target_dates()):
        state["status"] = "COMPLETE"
        state["completed_at_utc"] = now()
    elif len(state["completed_dates"]) + len(state["unavailable_dates"]) == len(targets):
        state["status"] = "COMPLETE_WITH_UNAVAILABLE_DATES"
        state["completed_at_utc"] = now()
    else:
        state["status"] = "INCOMPLETE"
    # Preserve previously charged transfer counters during a zero-byte repair.
    state["transferred_bytes_last_repair"] = budget.transferred
    write_state(state_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cache-only", action="store_true", help="Refuse network and repair from verified existing weather cache")
    args = parser.parse_args()
    result = run(Path(args.project_root).resolve(), args.workers, cache_only=args.cache_only)
    print(json.dumps({
        "status": result["status"],
        "completed": len(result["completed_dates"]),
        "unavailable": len(result["unavailable_dates"]),
        "failed": len(result["failed_dates"]),
        "target": result["target_date_count"],
        "recovery_sha256": result["recovery_sha256"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
