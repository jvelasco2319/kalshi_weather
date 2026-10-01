"""Finish and close the authorized 92-day V5A historical source window.

This wrapper reuses the bounded V5P collectors but deliberately stops after
June 1 through August 31, 2026.  It never starts the older 335-day backfill.
The source recovery ledger remains resumable and all charged request and byte
counters are preserved.
"""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

from v5.acquire_probability_evidence import (
    DEFAULT_RUN_ID,
    RecoveryStore,
    SharedRequestGate,
    _acquire_clilax,
    _acquire_weather,
    _attempt_kalshi_phase,
    _file_hash,
    _initial_state,
    _load_object,
    _phase_plan,
    _utc_now,
    _verify_hash_bound,
)


START = date(2026, 6, 1)
END = date(2026, 8, 31)
CONFIG_PATH = Path("configs/v5a_paid_depth_readiness.json")
CLOSURE_PATH = Path("data/manifests/v5a_priority_acquisition_closure.json")
SCHEMA = "klax-v5a-priority-acquisition-closure-v1"


class V5APriorityAcquisitionError(RuntimeError):
    pass


def _days() -> list[str]:
    return [
        (START + timedelta(days=offset)).isoformat()
        for offset in range((END - START).days + 1)
    ]


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def _closure(root: Path, run_id: str) -> dict[str, Any]:
    recovery_path = root / "runs/v5p_acquisition" / run_id / "recovery-state.json"
    recovery = _load_object(recovery_path)
    _verify_hash_bound(recovery, "recovery_sha256")
    targets = set(_days())
    completed = sorted(targets & set(recovery["weather"]["completed_dates"]))
    unavailable = sorted(targets & set(recovery["weather"]["unavailable_dates"]))
    missing = sorted(targets - set(completed) - set(unavailable))
    if missing or unavailable or len(completed) != 92:
        raise V5APriorityAcquisitionError(
            f"92-day weather window is incomplete: {len(completed)} complete, "
            f"{len(unavailable)} unavailable, {len(missing)} missing"
        )
    market_months = set(recovery["kalshi"]["candle_months_complete"]) & set(
        recovery["kalshi"]["trade_months_complete"]
    )
    if not {"2026-06", "2026-07", "2026-08"} <= market_months:
        raise V5APriorityAcquisitionError("priority Kalshi months are incomplete")
    if recovery["clilax"].get("archive_complete") is not True:
        raise V5APriorityAcquisitionError("CLILAX archive is incomplete")
    config_path = root / CONFIG_PATH
    body = {
        "schema_version": SCHEMA,
        "status": "CLOSED_92_DAY_WINDOW",
        "date_start": START.isoformat(),
        "date_end": END.isoformat(),
        "calendar_days": 92,
        "weather_complete_dates": completed,
        "weather_complete_date_count": len(completed),
        "weather_unavailable_dates": unavailable,
        "kalshi_complete_months": sorted(market_months & {"2026-06", "2026-07", "2026-08"}),
        "clilax_archive_complete": True,
        "request_starts_preserved": int(recovery["charged_request_starts"]),
        "weather_transfer_bytes_preserved": int(recovery["weather"]["new_transfer_bytes"]),
        "source_recovery": {
            "path": recovery_path.relative_to(root).as_posix(),
            "sha256": _file_hash(recovery_path),
            "recovery_sha256": recovery["recovery_sha256"],
        },
        "registration": {
            "path": CONFIG_PATH.as_posix(),
            "sha256": _file_hash(config_path),
        },
        "older_backfill_started_by_v5a": False,
        "protected_confirmation_labels_read": False,
        "live_or_current_endpoint_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def run(root: Path | str, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    workspace = Path(root).resolve()
    run_dir = workspace / "runs/v5p_acquisition" / run_id
    state_path = run_dir / "recovery-state.json"
    store = RecoveryStore(state_path, _initial_state(workspace, run_id))

    def mark_running(state: dict[str, Any]) -> None:
        state["status"] = "RUNNING"
        state.setdefault("started_at_utc", _utc_now())
        state["process_id"] = os.getpid()
        state.pop("failure_type", None)
        state.pop("failure_message", None)

    store.update(mark_running)
    gate = SharedRequestGate(store)
    phases = _phase_plan()
    try:
        for name in ("june", "july", "august"):
            months = phases[f"{name}_market_months"]
            month_names = {first.strftime("%Y-%m") for first, _ in months}
            snapshot = store.snapshot()
            complete_months = set(snapshot["kalshi"]["candle_months_complete"]) & set(
                snapshot["kalshi"]["trade_months_complete"]
            )
            if not month_names <= complete_months:
                _attempt_kalshi_phase(
                    workspace, store, gate, months,
                    phase=f"V5A_{name.upper()}_MARKET", finish_lane=False,
                )
            if not store.snapshot()["clilax"]["archive_complete"]:
                _acquire_clilax(workspace, store, gate)
            target_days = phases[f"{name}_weather_days"]
            done = set(store.snapshot()["weather"]["completed_dates"]) | set(
                store.snapshot()["weather"]["unavailable_dates"]
            )
            if not {day.isoformat() for day in target_days} <= done:
                _acquire_weather(
                    workspace, store, gate, target_days, finish_lane=False,
                )

        def close_priority(state: dict[str, Any]) -> None:
            state["status"] = "PRIORITY_COMPLETE"
            state["priority_completed_at_utc"] = _utc_now()
            state["protected_confirmation_labels_read_by_planner"] = False
            state["live_or_current_endpoint_used"] = False
            state["actual_orders_placed"] = False

        store.update(close_priority)
        closure = _closure(workspace, run_id)
        _write_json(workspace / CLOSURE_PATH, closure)
        return closure
    except Exception as exc:
        def fail(state: dict[str, Any]) -> None:
            state["status"] = "FAILED"
            state["failure_type"] = type(exc).__name__
            state["failure_message"] = str(exc)
            state["actual_orders_placed"] = False

        store.update(fail)
        raise


def status(root: Path | str, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    workspace = Path(root).resolve()
    recovery_path = workspace / "runs/v5p_acquisition" / run_id / "recovery-state.json"
    recovery = _load_object(recovery_path)
    _verify_hash_bound(recovery, "recovery_sha256")
    targets = set(_days())
    completed = targets & set(recovery["weather"]["completed_dates"])
    unavailable = targets & set(recovery["weather"]["unavailable_dates"])
    closure_path = workspace / CLOSURE_PATH
    return {
        "schema_version": "klax-v5a-priority-acquisition-status-v1",
        "source_status": recovery["status"],
        "process_id": recovery.get("process_id"),
        "weather_complete_date_count": len(completed),
        "weather_unavailable_date_count": len(unavailable),
        "weather_remaining_date_count": 92 - len(completed) - len(unavailable),
        "last_complete_date": recovery["weather"].get("last_complete_date"),
        "closure_exists": closure_path.is_file(),
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status"))
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args()
    value = run(args.project_root, args.run_id) if args.action == "run" else status(
        args.project_root, args.run_id
    )
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
