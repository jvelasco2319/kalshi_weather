"""Finite historical HRRR/GEFS acquisition for the V5B untouched cohort.

Only the 50 missing dates registered in the untouched confirmation plan are
eligible.  This lane downloads archived weather fields into the existing raw
cache.  It reads no Kalshi outcome, CLILAX product, or protected label.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any

import requests

from klax_lab.acquire_weather import TransferBudget
from klax_lab.acquire_weather_v3 import HistoricalWeatherUnavailable, _acquire_plan
from klax_lab.weather_sources_v3 import (
    ArchiveObjectPlan,
    FieldSelector,
    GEFS_FIELDS,
    GEFS_TEMPERATURE_FIELD_MAX_BYTES,
    HRRR_FIELDS,
    HRRR_FIELD_MAX_BYTES,
    HistoricalWeatherLimits,
    HistoricalWeatherPlan,
    INDEX_MAX_BYTES,
    REVISED_GEFS_LEADS,
    REVISED_HRRR_FIELDS,
    REVISED_HRRR_LEADS,
    gefs_url,
    hrrr_url,
    verify_cache_only,
)


VERSION = "v5b-untouched-weather-acquisition-v1"
PLAN_PATH = Path("configs/v5b_untouched_confirmation_plan.json")
RUN_ROOT = Path("runs/v5b_untouched_acquisition")
STATE_PATH = RUN_ROOT / "weather-recovery-state.json"
CACHE_PATH = Path("data/raw/v5p/weather")
MAX_REQUEST_STARTS = 3_200
MAX_NEW_BYTES = 4_000_000_000
REQUESTS_PER_SECOND = 2.0
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
TRANSIENT_MAX_ATTEMPTS = 5


class ConfirmationWeatherError(RuntimeError):
    pass


def _canonical_hash(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "recovery_sha256"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return sha256(raw).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # Windows readers can briefly hold the destination open.  Keep the same
    # pending file and retry the atomic replacement rather than losing the
    # charged-request counter or killing a finite acquisition.
    for attempt in range(20):
        try:
            pending.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            sleep(0.05 * (attempt + 1))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _date_range(start: str, end: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    result = []
    current = first
    while current <= last:
        result.append(current)
        current += timedelta(days=1)
    return result


def registered_dates(root: Path | str = ".") -> list[date]:
    workspace = Path(root).resolve()
    plan = json.loads((workspace / PLAN_PATH).read_text(encoding="utf-8-sig"))
    roles = {window["role"]: window for window in plan["confirmation_windows"]}
    required = {"pre-development-level-2", "post-development-level-2", "sealed-v5a-holdout"}
    if set(roles) != required:
        raise ConfirmationWeatherError("confirmation window roles differ")
    missing = (
        _date_range(roles["pre-development-level-2"]["date_start"], roles["pre-development-level-2"]["date_end"])
        + _date_range(roles["post-development-level-2"]["date_start"], roles["post-development-level-2"]["date_end"])
    )
    if len(missing) != 50 or len(set(missing)) != 50:
        raise ConfirmationWeatherError("registered weather date inventory differs")
    today = datetime.now(UTC).date()
    if any(target >= today for target in missing):
        raise ConfirmationWeatherError("confirmation weather date is not historical")
    return missing


def build_daily_plans(target: date) -> tuple[HistoricalWeatherPlan, HistoricalWeatherPlan]:
    if target >= datetime.now(UTC).date():
        raise ConfirmationWeatherError("weather target must be historical")
    limits = HistoricalWeatherLimits(
        maximum_days=1,
        maximum_requests=320,
        maximum_bytes=320_000_000,
        maximum_hrrr_leads_per_day=4,
        maximum_gefs_leads_per_day=8,
        maximum_gefs_members=2,
    )
    hrrr_fields = tuple(
        FieldSelector(name, *HRRR_FIELDS[name], HRRR_FIELD_MAX_BYTES)
        for name in REVISED_HRRR_FIELDS
    )
    hrrr_init = datetime.combine(target, datetime_time(6), UTC)
    hrrr_objects = tuple(
        ArchiveObjectPlan(
            f"hrrr-{target:%Y%m%d}-t06z-f{lead:02d}",
            "NOAA_NODD_AWS",
            "hrrr",
            hrrr_init,
            lead,
            None,
            hrrr_url(hrrr_init, lead),
            hrrr_url(hrrr_init, lead) + ".idx",
            hrrr_fields,
            INDEX_MAX_BYTES,
        )
        for lead in REVISED_HRRR_LEADS
    )
    gefs_init = datetime.combine(target, datetime_time(0), UTC)
    gefs_objects = []
    for member, extra in (("avg", ("ens mean",)), ("spr", ("ens std dev",))):
        fields = (
            FieldSelector(
                "temperature_2m",
                *GEFS_FIELDS["temperature_2m"],
                GEFS_TEMPERATURE_FIELD_MAX_BYTES,
                extra,
            ),
        )
        for lead in REVISED_GEFS_LEADS:
            url = gefs_url(gefs_init, member, lead)
            gefs_objects.append(
                ArchiveObjectPlan(
                    f"gefs-{target:%Y%m%d}-t00z-{member}-f{lead:03d}",
                    "NOAA_NODD_AWS",
                    "gefs",
                    gefs_init,
                    lead,
                    member,
                    url,
                    url + ".idx",
                    fields,
                    INDEX_MAX_BYTES,
                )
            )
    hrrr = HistoricalWeatherPlan(
        "v5b_untouched_hrrr",
        (target,),
        hrrr_objects,
        limits,
        "V5B untouched historical acquisition; no outcome access",
    )
    gefs = HistoricalWeatherPlan(
        "v5b_untouched_gefs",
        (target,),
        tuple(gefs_objects),
        limits,
        "V5B untouched historical acquisition; no outcome access",
    )
    if hrrr.request_count + gefs.request_count != 60:
        raise ConfirmationWeatherError("daily request contract differs")
    return hrrr, gefs


class Recovery:
    def __init__(self, path: Path, initial: dict[str, Any]):
        self.path = path
        self.lock = Lock()
        if path.exists():
            self.value = json.loads(path.read_text(encoding="utf-8-sig"))
            if self.value.get("recovery_sha256") != _canonical_hash(self.value):
                raise ConfirmationWeatherError("recovery hash mismatch")
            if self.value.get("version") != VERSION or self.value.get("plan_sha256") != initial["plan_sha256"]:
                raise ConfirmationWeatherError("recovery identity differs")
        else:
            self.value = initial
            self.value["recovery_sha256"] = _canonical_hash(self.value)
            _atomic_json(path, self.value)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.value))

    def update(self, mutate) -> dict[str, Any]:
        with self.lock:
            body = {key: item for key, item in self.value.items() if key != "recovery_sha256"}
            mutate(body)
            body["updated_at_utc"] = _utc_now()
            body["recovery_sha256"] = _canonical_hash(body)
            self.value = body
            _atomic_json(self.path, body)
            return json.loads(json.dumps(body))


class RequestGate:
    def __init__(self, recovery: Recovery):
        self.recovery = recovery
        self.interval = 1.0 / REQUESTS_PER_SECOND
        self.lock = Lock()
        self.last_started: float | None = None

    def wait(self, source: str) -> None:
        with self.lock:
            if self.last_started is not None:
                remaining = self.interval - (monotonic() - self.last_started)
                if remaining > 0:
                    sleep(remaining)
            if int(self.recovery.snapshot()["charged_request_starts"]) >= MAX_REQUEST_STARTS:
                raise ConfirmationWeatherError("request-start budget exhausted")

            def charge(state: dict[str, Any]) -> None:
                state["charged_request_starts"] = int(state["charged_request_starts"]) + 1
                state["last_request_source"] = source

            self.recovery.update(charge)
            self.last_started = monotonic()


class Session:
    def __init__(self, gate: RequestGate):
        self.gate = gate
        self.session = requests.Session()

    def get(self, *args, **kwargs):
        response = None
        for attempt in range(TRANSIENT_MAX_ATTEMPTS):
            self.gate.wait("noaa_weather_archive")
            response = self.session.get(*args, **kwargs)
            if response.status_code not in TRANSIENT_HTTP_STATUSES:
                return response
            if attempt + 1 < TRANSIENT_MAX_ATTEMPTS:
                response.close()
                sleep(float(2 ** attempt))
        return response

    def close(self) -> None:
        self.session.close()


def _initial(root: Path, targets: list[date]) -> dict[str, Any]:
    plan_path = root / PLAN_PATH
    return {
        "version": VERSION,
        "status": "REGISTERED_NOT_STARTED",
        "created_at_utc": _utc_now(),
        "updated_at_utc": _utc_now(),
        "plan_path": PLAN_PATH.as_posix(),
        "plan_sha256": _file_hash(plan_path),
        "registered_dates": [target.isoformat() for target in targets],
        "completed_dates": [],
        "unavailable_dates": [],
        "charged_request_starts": 0,
        "new_transfer_bytes": 0,
        "limits": {
            "maximum_request_starts": MAX_REQUEST_STARTS,
            "maximum_new_bytes": MAX_NEW_BYTES,
            "requests_per_second": REQUESTS_PER_SECOND,
        },
        "network_scope": "finite_historical_weather_acquisition",
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }


def run(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    targets = registered_dates(workspace)
    state_path = workspace / STATE_PATH
    recovery = Recovery(state_path, _initial(workspace, targets))

    def mark_running(state: dict[str, Any]) -> None:
        state["status"] = "RUNNING"
        state["process_id"] = __import__("os").getpid()
        state.setdefault("started_at_utc", _utc_now())
        state.pop("failure_type", None)
        state.pop("failure_message", None)

    recovery.update(mark_running)
    budget = TransferBudget(MAX_NEW_BYTES - int(recovery.snapshot()["new_transfer_bytes"]))
    gate = RequestGate(recovery)
    session = Session(gate)
    cache = workspace / CACHE_PATH
    try:
        for target in targets:
            day = target.isoformat()
            snapshot = recovery.snapshot()
            if day in snapshot["completed_dates"] or day in snapshot["unavailable_dates"]:
                continue
            hrrr, gefs = build_daily_plans(target)
            before = budget.transferred
            try:
                h_result = _acquire_plan(hrrr, cache, session, budget)
                g_result = _acquire_plan(gefs, cache, session, budget)
                h_verify = verify_cache_only(hrrr, cache)
                g_verify = verify_cache_only(gefs, cache)
            except HistoricalWeatherUnavailable as exc:
                def mark_unavailable(state: dict[str, Any], value=day, message=str(exc)) -> None:
                    if value not in state["unavailable_dates"]:
                        state["unavailable_dates"].append(value)
                    state["last_unavailable_date"] = value
                    state["last_unavailable_reason"] = message

                recovery.update(mark_unavailable)
                continue
            transferred = budget.transferred - before

            def mark_complete(
                state: dict[str, Any],
                value=day,
                added=transferred,
                requests=h_result["network_requests"] + g_result["network_requests"],
                h_objects=h_verify["objects_verified"],
                g_objects=g_verify["objects_verified"],
            ) -> None:
                if value not in state["completed_dates"]:
                    state["completed_dates"].append(value)
                state["last_complete_date"] = value
                state["last_day_network_requests"] = requests
                state["last_day_hrrr_objects"] = h_objects
                state["last_day_gefs_objects"] = g_objects
                state["new_transfer_bytes"] = int(state["new_transfer_bytes"]) + added
                state["network_used"] = True

            recovery.update(mark_complete)

        def finish(state: dict[str, Any]) -> None:
            state["status"] = "COMPLETE"
            state["completed_at_utc"] = _utc_now()

        return recovery.update(finish)
    except Exception as exc:
        def fail(state: dict[str, Any]) -> None:
            state["status"] = "FAILED"
            state["failure_type"] = type(exc).__name__
            state["failure_message"] = str(exc)

        recovery.update(fail)
        raise
    finally:
        session.close()


def status(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    path = workspace / STATE_PATH
    if not path.exists():
        return {
            "version": VERSION,
            "status": "NOT_STARTED",
            "registered_date_count": len(registered_dates(workspace)),
            "protected_confirmation_labels_read": False,
            "actual_orders_placed": False,
        }
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("recovery_sha256") != _canonical_hash(value):
        raise ConfirmationWeatherError("recovery hash mismatch")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    result = run(args.project_root) if args.command == "run" else status(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
