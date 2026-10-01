"""Finite, resumable V5P historical evidence acquisition.

The collector has three bounded lanes, all run in one process behind one
request-start limiter:

* NOAA HRRR and GEFS archive byte ranges for the frozen temperature model;
* Kalshi historical KXHIGHLAX metadata, one-minute candles, and public trades;
* a quarantined archive copy of NWS CLILAX products.

Only historical, date-bounded GET requests exist.  Raw Kalshi responses and
CLILAX product text are quarantined; progress artifacts expose coverage and
hashes without exposing settlement outcomes.  There is no live endpoint,
credential, websocket, order, or model-fitting path.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, Callable, Mapping
import urllib.request

from klax_lab.acquire_climate import acquire as acquire_climate_archive
from klax_lab.acquire_kalshi import (
    HistoricalClient,
    NoRedirects,
    acquire_candles,
    acquire_metadata,
    acquire_trades,
)
from klax_lab.acquire_weather import TransferBudget
from klax_lab.acquire_weather_v3 import HistoricalWeatherUnavailable, _acquire_plan
from klax_lab.weather_sources_v3 import (
    ArchiveObjectPlan,
    FieldSelector,
    GEFS_ARCHIVE,
    GEFS_FIELDS,
    GEFS_TEMPERATURE_FIELD_MAX_BYTES,
    HRRR_ARCHIVE,
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

from .probability import validate_frozen_leader


VERSION = "klax-v5p-partial-evidence-acquisition-v1"
DEFAULT_RUN_ID = "v5p-historical-20250701-20260831"
START = date(2025, 7, 1)
END = date(2026, 8, 31)
PRIORITY_START = date(2026, 6, 1)
PRIORITY_END = END
MAX_REQUEST_STARTS = 60_000
MAX_WEATHER_BYTES = 25_000_000_000
MAX_KALSHI_BYTES = 500_000_000
MAX_CLIMATE_BYTES = 50_000_000
REQUESTS_PER_SECOND = 2.0


class V5PAcquisitionError(RuntimeError):
    """The finite acquisition contract or recovery state failed."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hash_bound(body: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(body)
    result[field] = _canonical_hash(body)
    return result


def _verify_hash_bound(value: Mapping[str, Any], field: str) -> None:
    body = {key: item for key, item in value.items() if key != field}
    if value.get(field) != _canonical_hash(body):
        raise V5PAcquisitionError(f"{field} mismatch")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(payload, encoding="utf-8", newline="\n")
    # Windows can briefly deny replacement while a read-only status process or
    # antivirus scanner still has the destination open.  There is only one
    # registered writer, so bounded retries preserve atomic publication without
    # weakening the single-process invariant or refunding any counters.
    for attempt in range(8):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            sleep(0.05 * (attempt + 1))


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V5PAcquisitionError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V5PAcquisitionError(f"JSON object required: {path}")
    return value


def _day_order() -> list[date]:
    priority = [
        PRIORITY_START + timedelta(days=offset)
        for offset in range((PRIORITY_END - PRIORITY_START).days + 1)
    ]
    backfill = [
        START + timedelta(days=offset)
        for offset in range((PRIORITY_START - START).days)
    ]
    result = priority + backfill
    if len(result) != 427 or len(set(result)) != 427 or min(result) != START or max(result) != END:
        raise V5PAcquisitionError("priority day order differs")
    return result


def _month_order() -> list[tuple[date, date]]:
    months: list[tuple[date, date]] = []
    cursor = START.replace(day=1)
    while cursor <= END:
        following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        months.append((max(cursor, START), min(following - timedelta(days=1), END)))
        cursor = following
    priority = [item for item in months if item[0] >= PRIORITY_START]
    backfill = [item for item in months if item[0] < PRIORITY_START]
    result = priority + backfill
    if len(result) != 14 or result[0] != (date(2026, 6, 1), date(2026, 6, 30)):
        raise V5PAcquisitionError("priority month order differs")
    return result


def _phase_plan() -> dict[str, tuple[Any, ...]]:
    """Return the fixed partial-evidence-first acquisition phase inventory."""
    days = _day_order()
    months = _month_order()
    priority_days = tuple(day for day in days if PRIORITY_START <= day <= PRIORITY_END)
    backfill_days = tuple(day for day in days if day < PRIORITY_START)
    priority_months = tuple(item for item in months if item[0] >= PRIORITY_START)
    backfill_months = tuple(item for item in months if item[0] < PRIORITY_START)
    if (len(priority_days) != 92 or len(backfill_days) != 335
            or len(priority_months) != 3 or len(backfill_months) != 11):
        raise V5PAcquisitionError("acquisition phase inventory differs")
    june_days = tuple(day for day in priority_days if day.month == 6)
    july_days = tuple(day for day in priority_days if day.month == 7)
    august_days = tuple(day for day in priority_days if day.month == 8)
    return {
        "june_market_months": priority_months[:1],
        "june_weather_days": june_days,
        "july_market_months": priority_months[1:2],
        "july_weather_days": july_days,
        "august_market_months": priority_months[2:3],
        "august_weather_days": august_days,
        "backfill_market_months": backfill_months,
        "backfill_weather_days": backfill_days,
    }


def _kalshi_capacity_failure(error: Exception) -> bool:
    return (isinstance(error, RuntimeError)
            and "Historical transfer/storage budget exhausted" in str(error))


def _attempt_kalshi_phase(root: Path, store: RecoveryStore, gate: SharedRequestGate,
                          months: tuple[tuple[date, date], ...], *, phase: str,
                          finish_lane: bool) -> bool:
    """Run one bounded market phase and turn the hard cap into partial evidence."""
    if store.snapshot()["kalshi"].get("byte_cap_exhausted") is True:
        return False
    try:
        _acquire_kalshi(root, store, gate, months, finish_lane=finish_lane)
        return True
    except RuntimeError as exc:
        if not _kalshi_capacity_failure(exc):
            raise

        def capacity_stop(state: dict[str, Any]) -> None:
            state["kalshi"].update({
                "status": "PARTIAL_BYTE_CAP_EXHAUSTED",
                "byte_cap_exhausted": True,
                "byte_cap_failure_phase": phase,
                "byte_cap_failure_message": str(exc),
                "capacity_uncollected_months": [
                    first.strftime("%Y-%m") for first, _ in months
                    if first.strftime("%Y-%m") not in set(
                        state["kalshi"]["candle_months_complete"]
                    ) & set(state["kalshi"]["trade_months_complete"])
                ],
            })

        store.update(capacity_stop)
        return False


class RecoveryStore:
    """Atomic, hash-bound state shared by the single acquisition process."""

    def __init__(self, path: Path, initial: Mapping[str, Any]):
        self.path = Path(path)
        self._lock = Lock()
        if self.path.exists():
            value = _load_object(self.path)
            _verify_hash_bound(value, "recovery_sha256")
            if value.get("version") != VERSION or value.get("run_id") != initial.get("run_id"):
                raise V5PAcquisitionError("recovery identity differs")
            self._value = value
        else:
            self._value = _hash_bound(dict(initial), "recovery_sha256")
            _atomic_json(self.path, self._value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._value))

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self._lock:
            body = {
                key: item for key, item in self._value.items()
                if key != "recovery_sha256"
            }
            mutate(body)
            body["updated_at_utc"] = _utc_now()
            self._value = _hash_bound(body, "recovery_sha256")
            _atomic_json(self.path, self._value)
            return json.loads(json.dumps(self._value))


class SharedRequestGate:
    """One global request-start limiter and nonrefundable request counter."""

    def __init__(self, store: RecoveryStore, *, requests_per_second: float = REQUESTS_PER_SECOND,
                 maximum_starts: int = MAX_REQUEST_STARTS):
        if not 0 < requests_per_second <= 2:
            raise V5PAcquisitionError("request rate must be in (0,2] per second")
        if maximum_starts <= 0:
            raise V5PAcquisitionError("request-start limit must be positive")
        self.store = store
        self.interval = 1.0 / requests_per_second
        self.maximum_starts = maximum_starts
        self._lock = Lock()
        self._last_started: float | None = None

    def wait(self, source: str) -> None:
        with self._lock:
            if self._last_started is not None:
                remaining = self.interval - (monotonic() - self._last_started)
                if remaining > 0:
                    sleep(remaining)
            current = int(self.store.snapshot()["charged_request_starts"])
            if current >= self.maximum_starts:
                raise V5PAcquisitionError("global request-start budget exhausted")

            def charge(state: dict[str, Any]) -> None:
                state["charged_request_starts"] = int(state["charged_request_starts"]) + 1
                state["last_request_source"] = source
                state["last_request_started_at_utc"] = _utc_now()

            self.store.update(charge)
            self._last_started = monotonic()


class GatedRequestsSession:
    def __init__(self, gate: SharedRequestGate):
        import requests
        self.gate = gate
        self.session = requests.Session()

    def get(self, *args, **kwargs):
        self.gate.wait("noaa_weather_archive")
        return self.session.get(*args, **kwargs)

    def close(self) -> None:
        self.session.close()


def _gated_urllib_opener(gate: SharedRequestGate):
    base = urllib.request.build_opener(NoRedirects()).open

    def open_request(request, timeout=45):
        gate.wait("kalshi_historical_api")
        return base(request, timeout=timeout)

    return open_request


def build_v5_daily_weather_plans(target: date) -> tuple[HistoricalWeatherPlan, HistoricalWeatherPlan]:
    """Preserve the exact V3 HRRR/GEFS field contract on V5 dates."""
    if not START <= target <= END or target >= datetime.now(UTC).date():
        raise V5PAcquisitionError("weather target must be inside the historical V5P window")
    limits = HistoricalWeatherLimits(
        maximum_days=1, maximum_requests=320, maximum_bytes=320_000_000,
        maximum_hrrr_leads_per_day=4, maximum_gefs_leads_per_day=8,
        maximum_gefs_members=2,
    )
    hrrr_fields = tuple(
        FieldSelector(name, *HRRR_FIELDS[name], HRRR_FIELD_MAX_BYTES)
        for name in REVISED_HRRR_FIELDS
    )
    hrrr_objects = []
    hrrr_init = datetime.combine(target, datetime_time(6), UTC)
    for lead in REVISED_HRRR_LEADS:
        url = hrrr_url(hrrr_init, lead)
        hrrr_objects.append(ArchiveObjectPlan(
            f"hrrr-{target:%Y%m%d}-t06z-f{lead:02d}", "NOAA_NODD_AWS",
            "hrrr", hrrr_init, lead, None, url, url + ".idx", hrrr_fields,
            INDEX_MAX_BYTES,
        ))
    gefs_objects = []
    gefs_init = datetime.combine(target, datetime_time(0), UTC)
    for member, extra in (("avg", ("ens mean",)), ("spr", ("ens std dev",))):
        fields = (FieldSelector(
            "temperature_2m", *GEFS_FIELDS["temperature_2m"],
            GEFS_TEMPERATURE_FIELD_MAX_BYTES, extra,
        ),)
        for lead in REVISED_GEFS_LEADS:
            url = gefs_url(gefs_init, member, lead)
            gefs_objects.append(ArchiveObjectPlan(
                f"gefs-{target:%Y%m%d}-t00z-{member}-f{lead:03d}",
                "NOAA_NODD_AWS", "gefs", gefs_init, lead, member, url,
                url + ".idx", fields, INDEX_MAX_BYTES,
            ))
    hrrr = HistoricalWeatherPlan(
        "v5p_frozen_hrrr_confirmation", (target,), tuple(hrrr_objects), limits,
        "V5P historical confirmation feature acquisition; no outcome claim",
    )
    gefs = HistoricalWeatherPlan(
        "v5p_frozen_gefs_confirmation", (target,), tuple(gefs_objects), limits,
        "V5P historical confirmation feature acquisition; no outcome claim",
    )
    if hrrr.request_count + gefs.request_count != 60:
        raise V5PAcquisitionError("daily weather request contract differs")
    return hrrr, gefs


def _initial_state(root: Path, run_id: str) -> dict[str, Any]:
    leader = validate_frozen_leader(root)
    return {
        "version": VERSION,
        "run_id": run_id,
        "status": "REGISTERED_NOT_STARTED",
        "created_at_utc": _utc_now(),
        "updated_at_utc": _utc_now(),
        "confirmation_window": {"date_start": str(START), "date_end": str(END)},
        "priority_window": {"date_start": str(PRIORITY_START), "date_end": str(PRIORITY_END)},
        "leader_validation_sha256": leader["validation_sha256"],
        "charged_request_starts": 0,
        "last_request_source": None,
        "last_request_started_at_utc": None,
        "limits": {
            "maximum_request_starts": MAX_REQUEST_STARTS,
            "requests_per_second": REQUESTS_PER_SECOND,
            "maximum_weather_bytes": MAX_WEATHER_BYTES,
            "maximum_kalshi_bytes": MAX_KALSHI_BYTES,
            "maximum_climate_bytes": MAX_CLIMATE_BYTES,
        },
        "kalshi": {
            "status": "PENDING", "metadata_complete": False,
            "candle_months_complete": [], "trade_months_complete": [],
            "safe_covered_days": 0, "safe_contracts": 0,
        },
        "clilax": {"status": "PENDING", "archive_complete": False},
        "weather": {
            "status": "PENDING", "completed_dates": [], "unavailable_dates": [],
            "new_transfer_bytes": 0,
        },
        "network_used_for_finite_historical_acquisition": False,
        "protected_confirmation_labels_read_by_planner": False,
        "raw_outcome_bearing_sources_quarantined": True,
        "live_or_current_endpoint_used": False,
        "actual_orders_placed": False,
    }


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _acquire_kalshi(root: Path, store: RecoveryStore, gate: SharedRequestGate,
                    months: tuple[tuple[date, date], ...], *, finish_lane: bool) -> None:
    workspace = root / "data/raw/v5p/kalshi_workspace"
    client = HistoricalClient(
        workspace, max_bytes=MAX_KALSHI_BYTES, min_interval=0, retries=2,
        opener=_gated_urllib_opener(gate), sleeper=sleep,
    )
    coverage_path = workspace / "data/manifests/kalshi_coverage.json"
    snapshot = store.snapshot()
    if snapshot["kalshi"].get("metadata_complete"):
        coverage = _load_object(coverage_path)
        expected = snapshot["kalshi"].get("coverage_manifest_sha256")
        if expected != _file_hash(coverage_path):
            raise V5PAcquisitionError("cached safe Kalshi coverage manifest changed")
    else:
        coverage = acquire_metadata(client, START, END, max_pages=100, series="KXHIGHLAX")
    safe_dates = sorted({
        row["climate_date"] for row in coverage.get("contracts", [])
        if isinstance(row, dict) and isinstance(row.get("climate_date"), str)
    })

    def metadata_done(state: dict[str, Any]) -> None:
        state["status"] = "RUNNING"
        state["network_used_for_finite_historical_acquisition"] = True
        state["kalshi"].update({
            "status": "METADATA_COMPLETE", "metadata_complete": True,
            "safe_covered_days": len(safe_dates),
            "safe_contracts": len(coverage.get("contracts", [])),
            "coverage_manifest": _relative(root, coverage_path),
            "coverage_manifest_sha256": _file_hash(coverage_path),
        })

    store.update(metadata_done)
    for first, last in months:
        month_id = first.strftime("%Y-%m")
        month_has_contracts = any(
            isinstance(row, dict)
            and isinstance(row.get("climate_date"), str)
            and str(first) <= row["climate_date"] <= str(last)
            and row.get("station_identity_screen") is True
            and row.get("status") in {"finalized", "settled"}
            for row in coverage.get("contracts", [])
        )
        if not month_has_contracts:
            def month_unavailable(state: dict[str, Any], month=month_id) -> None:
                unavailable = state["kalshi"].setdefault("unavailable_months", [])
                if month not in unavailable:
                    unavailable.append(month)
                for field in ("candle_months_complete", "trade_months_complete"):
                    if month not in state["kalshi"][field]:
                        state["kalshi"][field].append(month)

            store.update(month_unavailable)
            continue
        snapshot = store.snapshot()
        if month_id not in snapshot["kalshi"]["candle_months_complete"]:
            result = acquire_candles(client, coverage, first, last, period_minutes=1)
            path = workspace / f"data/manifests/kalshi_candles_1m_{first}_{last}.json"

            def candle_done(state: dict[str, Any], month=month_id, p=path, value=result) -> None:
                completed = state["kalshi"]["candle_months_complete"]
                if month not in completed:
                    completed.append(month)
                state["kalshi"]["last_candle_manifest"] = _relative(root, p)
                state["kalshi"]["last_candle_manifest_sha256"] = _file_hash(p)
                state["kalshi"]["one_minute_candle_rows"] = (
                    int(state["kalshi"].get("one_minute_candle_rows", 0))
                    + int(value.get("total_rows", 0))
                )

            store.update(candle_done)
        snapshot = store.snapshot()
        if month_id not in snapshot["kalshi"]["trade_months_complete"]:
            result = acquire_trades(
                client, coverage, first, last, max_pages_per_contract=100,
                page_limit=1000,
            )
            path = workspace / f"data/manifests/kalshi_trades_{first}_{last}.json"

            def trade_done(state: dict[str, Any], month=month_id, p=path, value=result) -> None:
                completed = state["kalshi"]["trade_months_complete"]
                if month not in completed:
                    completed.append(month)
                state["kalshi"]["last_trade_manifest"] = _relative(root, p)
                state["kalshi"]["last_trade_manifest_sha256"] = _file_hash(p)
                state["kalshi"]["public_trade_rows"] = (
                    int(state["kalshi"].get("public_trade_rows", 0))
                    + int(value.get("total_rows", 0))
                )

            store.update(trade_done)

    def finish(state: dict[str, Any]) -> None:
        state["kalshi"]["status"] = "COMPLETE" if finish_lane else "PRIORITY_COMPLETE"
        if not finish_lane:
            state["kalshi"]["priority_window_complete"] = True

    store.update(finish)


def _acquire_clilax(root: Path, store: RecoveryStore, gate: SharedRequestGate) -> None:
    if store.snapshot()["clilax"]["archive_complete"]:
        return
    workspace = root / "data/raw/v5p/climate_workspace"
    gate.wait("clilax_historical_archive")
    manifest = acquire_climate_archive(workspace, str(START), str(END + timedelta(days=1)))
    archive_path = workspace / manifest["path"]
    if archive_path.stat().st_size > MAX_CLIMATE_BYTES:
        raise V5PAcquisitionError("CLILAX archive exceeds V5P byte limit")
    manifest_path = workspace / "data/manifests" / f"climate_{START}_{END + timedelta(days=1)}.json"

    def finish(state: dict[str, Any]) -> None:
        state["network_used_for_finite_historical_acquisition"] = True
        state["clilax"].update({
            "status": "QUARANTINED_ARCHIVE_COMPLETE", "archive_complete": True,
            "archive_path": _relative(root, archive_path),
            "archive_bytes": archive_path.stat().st_size,
            "archive_sha256": _file_hash(archive_path),
            "safe_manifest": _relative(root, manifest_path),
            "safe_manifest_sha256": _file_hash(manifest_path),
            "product_text_read_by_planner": False,
        })

    store.update(finish)


def _weather_cache_bytes(cache: Path) -> int:
    if not cache.exists():
        return 0
    return sum(
        path.stat().st_size for path in cache.rglob("*")
        if path.is_file() and path.suffix in {".idx", ".grib2"}
    )


def _acquire_weather(root: Path, store: RecoveryStore, gate: SharedRequestGate,
                     target_days: tuple[date, ...], *, finish_lane: bool) -> None:
    cache = root / "data/raw/v5p/weather"
    existing_bytes = _weather_cache_bytes(cache)
    remaining = MAX_WEATHER_BYTES - existing_bytes
    if remaining <= 0:
        raise V5PAcquisitionError("V5P weather byte budget exhausted")
    budget = TransferBudget(remaining)
    session = GatedRequestsSession(gate)
    try:
        for target in target_days:
            day = target.isoformat()
            snapshot = store.snapshot()
            if (day in snapshot["weather"]["completed_dates"]
                    or day in snapshot["weather"]["unavailable_dates"]):
                continue
            hrrr, gefs = build_v5_daily_weather_plans(target)
            before = budget.transferred
            try:
                hrrr_result = _acquire_plan(hrrr, cache, session, budget)
                gefs_result = _acquire_plan(gefs, cache, session, budget)
                hrrr_verify = verify_cache_only(hrrr, cache)
                gefs_verify = verify_cache_only(gefs, cache)
            except HistoricalWeatherUnavailable as exc:
                def unavailable(state: dict[str, Any], value=day, message=str(exc)) -> None:
                    days = state["weather"]["unavailable_dates"]
                    if value not in days:
                        days.append(value)
                    state["weather"].update({
                        "status": "RUNNING", "last_unavailable_date": value,
                        "last_unavailable_reason": message,
                    })

                store.update(unavailable)
                continue
            transferred = budget.transferred - before

            def day_done(state: dict[str, Any], value=day, added=transferred,
                         h_result=hrrr_result, g_result=gefs_result,
                         h_verify=hrrr_verify, g_verify=gefs_verify) -> None:
                completed = state["weather"]["completed_dates"]
                if value not in completed:
                    completed.append(value)
                state["weather"].update({
                    "status": "RUNNING",
                    "last_complete_date": value,
                    "new_transfer_bytes": int(state["weather"]["new_transfer_bytes"]) + added,
                    "last_day_network_requests": h_result["network_requests"] + g_result["network_requests"],
                    "last_day_hrrr_objects": h_verify["objects_verified"],
                    "last_day_gefs_objects": g_verify["objects_verified"],
                    "historical_publication_time_proven": False,
                    "archive_last_modified_preserved": True,
                })
                state["network_used_for_finite_historical_acquisition"] = True

            store.update(day_done)
    finally:
        session.close()

    def finish(state: dict[str, Any]) -> None:
        state["weather"]["status"] = "COMPLETE" if finish_lane else "PRIORITY_COMPLETE"
        if not finish_lane:
            state["weather"]["priority_window_complete"] = True
        state["weather"]["cache_bytes"] = _weather_cache_bytes(cache)

    store.update(finish)


def run_acquisition(root: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    root = Path(root).resolve()
    run_dir = root / "runs/v5p_acquisition" / run_id
    state_path = run_dir / "recovery-state.json"
    store = RecoveryStore(state_path, _initial_state(root, run_id))

    def mark_running(state: dict[str, Any]) -> None:
        if state["status"] != "COMPLETE":
            state["status"] = "RUNNING"
            state.setdefault("started_at_utc", _utc_now())
            state["process_id"] = __import__("os").getpid()
            state.pop("failure_type", None)
            state.pop("failure_message", None)

    store.update(mark_running)
    gate = SharedRequestGate(store)
    phases = _phase_plan()
    try:
        # Finish the free-depth overlap window across all public lanes before
        # older backfill so the first outcome-blind eligible dates arrive early.
        priority_months = phases["june_market_months"]
        snapshot = store.snapshot()
        market_done = set(snapshot["kalshi"]["candle_months_complete"]) & set(
            snapshot["kalshi"]["trade_months_complete"])
        required_priority_months = {first.strftime("%Y-%m") for first, _ in priority_months}
        if not required_priority_months <= market_done:
            _attempt_kalshi_phase(
                root, store, gate, priority_months, phase="JUNE_MARKET",
                finish_lane=False,
            )
        if not store.snapshot()["clilax"]["archive_complete"]:
            _acquire_clilax(root, store, gate)
        snapshot = store.snapshot()
        weather_done = set(snapshot["weather"]["completed_dates"]) | set(
            snapshot["weather"]["unavailable_dates"])
        required_priority_days = {value.isoformat() for value in phases["june_weather_days"]}
        if not required_priority_days <= weather_done:
            _acquire_weather(
                root, store, gate, phases["june_weather_days"], finish_lane=False,
            )
        for name in ("july", "august"):
            market_months = phases[f"{name}_market_months"]
            market_done = set(store.snapshot()["kalshi"]["candle_months_complete"]) & set(
                store.snapshot()["kalshi"]["trade_months_complete"])
            required_months = {first.strftime("%Y-%m") for first, _ in market_months}
            if not required_months <= market_done:
                _attempt_kalshi_phase(
                    root, store, gate, market_months, phase=f"{name.upper()}_MARKET",
                    finish_lane=False,
                )
            snapshot = store.snapshot()
            weather_done = set(snapshot["weather"]["completed_dates"]) | set(
                snapshot["weather"]["unavailable_dates"])
            weather_days = phases[f"{name}_weather_days"]
            if not {value.isoformat() for value in weather_days} <= weather_done:
                _acquire_weather(root, store, gate, weather_days, finish_lane=False)
        if (store.snapshot()["kalshi"]["status"] != "COMPLETE"
                and store.snapshot()["kalshi"].get("byte_cap_exhausted") is not True):
            _attempt_kalshi_phase(
                root, store, gate, phases["backfill_market_months"],
                phase="BACKFILL_MARKET", finish_lane=True,
            )
        if store.snapshot()["weather"]["status"] != "COMPLETE":
            _acquire_weather(
                root, store, gate, phases["backfill_weather_days"], finish_lane=True,
            )

        def complete(state: dict[str, Any]) -> None:
            state["status"] = "COMPLETE"
            state["completed_at_utc"] = _utc_now()
            state["protected_confirmation_labels_read_by_planner"] = False
            state["live_or_current_endpoint_used"] = False
            state["actual_orders_placed"] = False

        return store.update(complete)
    except Exception as exc:
        def fail(state: dict[str, Any]) -> None:
            state["status"] = "FAILED"
            state["failure_type"] = type(exc).__name__
            state["failure_message"] = str(exc)
            state["actual_orders_placed"] = False

        store.update(fail)
        raise


def status(root: Path, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    path = Path(root).resolve() / "runs/v5p_acquisition" / run_id / "recovery-state.json"
    if not path.is_file():
        return {
            "version": VERSION, "run_id": run_id, "status": "NOT_STARTED",
            "recovery_state": _relative(Path(root).resolve(), path),
        }
    value = _load_object(path)
    _verify_hash_bound(value, "recovery_sha256")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)
    result = (
        run_acquisition(args.project_root, args.run_id)
        if args.command == "run" else status(args.project_root, args.run_id)
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
