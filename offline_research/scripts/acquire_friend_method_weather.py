"""Acquire point-in-time GFS/NAM/NBM weather inputs for the friend-method test.

GFS and NBM use NOAA's historical AWS indexes and exact GRIB byte ranges.
NAM uses Iowa State Mesonet's archived NOAA NAM218 NetCDF Subset Service
because NCEI's archived NCSS endpoint currently returns an upstream S3 error.
The script is resumable and never reads market outcomes or places orders.
"""
from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import threading
import time as wall_time
from typing import Any

import requests


UTC = timezone.utc
KLAX = {"station": "KLAX", "latitude": 33.93816, "longitude": -118.3866}
DECISION_HOUR_UTC = 18
GFS_LEADS = (12, 18, 24, 30, 36)
GFS_PRIMARY_LEADS = (18, 24, 30)
NAM_LEADS = tuple(range(9, 31, 3))
NBM_LEADS = tuple(range(7, 32))
NBM_KALSHI_LEADS = tuple(range(8, 32))
NBM_FIXTURE_LEADS = tuple(range(7, 31))
USER_AGENT = "klax-friend-method-research/1.0"
GFS_ROOT = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
NBM_ROOT = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
NAM_NCSS_ROOT = "https://mtarchive.geol.iastate.edu/thredds/ncss/mtarchive"
MAX_INDEX_BYTES = 2_000_000
MAX_FIELD_BYTES = 8_000_000
MAX_CSV_BYTES = 100_000
PRINT_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
ECCODES_LOCK = threading.Lock()
REQUEST_START_LOCK = threading.Lock()
LAST_REQUEST_START = 0.0
REQUEST_START_INTERVAL_SECONDS = 0.5


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_bytes(data)
    pending.replace(path)


def _write_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    _write_bytes(path, encoded)


def _request(url: str, *, headers: dict[str, str] | None = None,
             maximum_bytes: int, attempts: int = 5) -> tuple[bytes, dict[str, str]]:
    global LAST_REQUEST_START
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with REQUEST_START_LOCK:
                remaining = REQUEST_START_INTERVAL_SECONDS - (wall_time.monotonic() - LAST_REQUEST_START)
                if remaining > 0:
                    wall_time.sleep(remaining)
                LAST_REQUEST_START = wall_time.monotonic()
            response = requests.get(url, headers=request_headers, timeout=(20, 180))
            if response.status_code not in ({206} if "Range" in request_headers else {200}):
                raise RuntimeError(f"HTTP {response.status_code} for {url}")
            body = response.content
            if not body or len(body) > maximum_bytes:
                raise RuntimeError(f"Response size {len(body)} violates limit for {url}")
            return body, {key.lower(): value for key, value in response.headers.items()}
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                wall_time.sleep(min(8.0, 0.5 * (2 ** attempt)))
    raise RuntimeError(str(last_error))


def _parse_range(index: str, pattern: re.Pattern[str]) -> tuple[int, int, str]:
    lines = index.splitlines()
    matches = [position for position, line in enumerate(lines) if pattern.search(line)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one index match, found {len(matches)} for {pattern.pattern}")
    position = matches[0]
    parts = lines[position].split(":", 2)
    start = int(parts[1])
    if position + 1 >= len(lines):
        raise RuntimeError("Selected field is last in index; exact end offset unavailable")
    end = int(lines[position + 1].split(":", 2)[1]) - 1
    if start < 0 or end < start or end - start + 1 > MAX_FIELD_BYTES:
        raise RuntimeError(f"Unsafe GRIB range {start}-{end}")
    return start, end, lines[position]


def _decode_grib(path: Path, *, model: str, climate_date: date,
                 lead: int, expected_variable: str) -> dict[str, Any]:
    with path.open("rb") as marker_stream:
        start_marker = marker_stream.read(4)
        marker_stream.seek(-4, 2)
        end_marker = marker_stream.read(4)
    if not (start_marker == b"GRIB" and end_marker == b"7777"):
        raise RuntimeError("GRIB fragment markers are invalid")
    import eccodes

    temporary_path = path
    try:
        with temporary_path.open("rb") as stream:
            handle = eccodes.codes_grib_new_from_file(stream)
            if handle is None:
                raise RuntimeError("GRIB fragment is not decodable")
            try:
                def get(key: str):
                    return eccodes.codes_get(handle, key)

                nearest = eccodes.codes_grib_find_nearest(
                    handle, KLAX["latitude"], KLAX["longitude"] % 360.0, npoints=1,
                )[0]
                kelvin = float(nearest["value"])
                if not 180 <= kelvin <= 340:
                    raise RuntimeError(f"Implausible temperature {kelvin} K")
                initialized = datetime.strptime(
                    f"{int(get('dataDate')):08d}{int(get('dataTime')):04d}", "%Y%m%d%H%M",
                ).replace(tzinfo=UTC)
                valid = datetime.strptime(
                    f"{int(get('validityDate')):08d}{int(get('validityTime')):04d}", "%Y%m%d%H%M",
                ).replace(tzinfo=UTC)
                level = float(get("level"))
                level_type = str(get("typeOfLevel"))
                units = str(get("units"))
                if level != 2 or level_type != "heightAboveGround" or units != "K":
                    raise RuntimeError("GRIB fragment is not two-meter temperature in kelvin")
                if initialized != datetime.combine(climate_date, time(0), UTC):
                    raise RuntimeError("Unexpected model initialization")
                if valid != initialized + timedelta(hours=lead):
                    raise RuntimeError("Unexpected model valid time")
                short_name = str(get("shortName"))
                step_type = str(get("stepType"))
                if expected_variable == "TMAX" and step_type != "max":
                    raise RuntimeError(f"GFS field has unexpected step type {step_type}")
                if expected_variable == "TMP" and step_type != "instant":
                    raise RuntimeError(f"NBM field has unexpected step type {step_type}")
                result = {
                    "model": model,
                    "climate_date": climate_date.isoformat(),
                    "cycle_time_utc": initialized.isoformat(),
                    "lead_hours": lead,
                    "valid_time_utc": valid.isoformat(),
                    "decision_time_utc": datetime.combine(climate_date, time(DECISION_HOUR_UTC), UTC).isoformat(),
                    "variable": expected_variable,
                    "short_name": short_name,
                    "parameter_name": str(get("name")),
                    "step_type": step_type,
                    "start_step": int(get("startStep")),
                    "end_step": int(get("endStep")),
                    "raw_value_kelvin": kelvin,
                    "temperature_f": (kelvin - 273.15) * 1.8 + 32.0,
                    "target_station": KLAX["station"],
                    "target_latitude": KLAX["latitude"],
                    "target_longitude": KLAX["longitude"],
                    "grid_latitude": float(nearest["lat"]),
                    "grid_longitude": (float(nearest["lon"]) + 180.0) % 360.0 - 180.0,
                    "grid_distance_km": float(nearest["distance"]),
                    "grid_index": int(nearest["index"]),
                    "extraction_method": "ecCodes_nearest_grid_point_no_interpolation",
                }
            finally:
                eccodes.codes_release(handle)
            extra = eccodes.codes_grib_new_from_file(stream)
            if extra is not None:
                eccodes.codes_release(extra)
                raise RuntimeError("GRIB fragment contains multiple messages")
    finally:
        pass
    return result


@dataclass(frozen=True)
class Task:
    model: str
    climate_date: date
    lead: int

    @property
    def key(self) -> str:
        return f"{self.climate_date.isoformat()}:{self.model}:f{self.lead:03d}"


def _task_folder(root: Path, task: Task) -> Path:
    return root / f"date={task.climate_date.isoformat()}" / f"model={task.model}" / f"lead={task.lead:03d}"


def _cached_point(folder: Path, task: Task) -> dict[str, Any] | None:
    path = folder / "point.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if (value.get("model") != task.model or value.get("climate_date") != task.climate_date.isoformat()
            or value.get("lead_hours") != task.lead):
        return None
    return value


def _indexed_task(root: Path, task: Task) -> dict[str, Any]:
    if task.model == "gfs":
        stem = f"gfs.{task.climate_date:%Y%m%d}/00/atmos/gfs.t00z.pgrb2.0p25.f{task.lead:03d}"
        url = f"{GFS_ROOT}/{stem}"
        pattern = re.compile(r":TMAX:2 m above ground:")
        variable = "TMAX"
    elif task.model == "nbm":
        stem = f"blend.{task.climate_date:%Y%m%d}/00/core/blend.t00z.core.f{task.lead:03d}.co.grib2"
        url = f"{NBM_ROOT}/{stem}"
        pattern = re.compile(r":TMP:2 m above ground:[^:]*fcst:$")
        variable = "TMP"
    else:
        raise RuntimeError("Indexed task supports only GFS or NBM")

    folder = _task_folder(root, task)
    cached = _cached_point(folder, task)
    if cached is not None:
        return cached
    index_url = url + ".idx"
    index, index_headers = _request(index_url, maximum_bytes=MAX_INDEX_BYTES)
    index_text = index.decode("utf-8")
    start, end, selected_line = _parse_range(index_text, pattern)
    body, field_headers = _request(
        url, headers={"Range": f"bytes={start}-{end}"}, maximum_bytes=MAX_FIELD_BYTES,
    )
    content_range = field_headers.get("content-range", "")
    if not content_range.startswith(f"bytes {start}-{end}/"):
        raise RuntimeError(f"Unexpected Content-Range {content_range}")
    raw_path = folder / f"{variable.lower()}.grib2"
    index_path = folder / "source.idx"
    _write_bytes(index_path, index)
    _write_bytes(raw_path, body)
    with ECCODES_LOCK:
        point = _decode_grib(
            raw_path, model=task.model, climate_date=task.climate_date,
            lead=task.lead, expected_variable=variable,
        )
    point.update({
        "source_provider": "NOAA_NODD_AWS",
        "source_url": url,
        "source_index_url": index_url,
        "source_index_line": selected_line,
        "source_range_start": start,
        "source_range_end": end,
        "source_bytes": len(body),
        "source_sha256": _sha(body),
        "index_bytes": len(index),
        "index_sha256": _sha(index),
        "source_etag": field_headers.get("etag"),
        "source_last_modified": field_headers.get("last-modified"),
        "index_last_modified": index_headers.get("last-modified"),
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "historical_availability_proven": False,
        "as_of_basis": "00Z cycle plus conservative six-hour operational delay precedes 18Z decision",
        "as_of_decision_safe": True,
    })
    _write_json(folder / "point.json", point)
    return point


def _nam_task(root: Path, task: Task) -> dict[str, Any]:
    folder = _task_folder(root, task)
    cached = _cached_point(folder, task)
    if cached is not None:
        return cached
    stem = (f"{task.climate_date:%Y/%m/%d}/grib2/ncep/NAM218/00/"
            f"{task.climate_date:%Y%m%d}0000F{task.lead:03d}.grib2")
    url = f"{NAM_NCSS_ROOT}/{stem}"
    params = (
        "?var=Temperature_height_above_ground"
        f"&latitude={KLAX['latitude']}&longitude={KLAX['longitude']}&accept=csv"
    )
    body, headers = _request(url + params, maximum_bytes=MAX_CSV_BYTES)
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if len(rows) != 1:
        raise RuntimeError(f"Expected one NAM point row, found {len(rows)}")
    row = rows[0]
    temperature_key = next((key for key in row if key.startswith("Temperature_height_above_ground")), None)
    if temperature_key is None:
        raise RuntimeError("NAM response lacks two-meter temperature")
    kelvin = float(row[temperature_key])
    vertical_key = next((key for key in row if key.startswith("vertCoord")), None)
    if vertical_key is None or not math.isclose(float(row[vertical_key]), 2.0, abs_tol=1e-9):
        raise RuntimeError("NAM response is not at two meters")
    valid = datetime.fromisoformat(row["time"].replace("Z", "+00:00")).astimezone(UTC)
    initialized = datetime.combine(task.climate_date, time(0), UTC)
    if valid != initialized + timedelta(hours=task.lead) or not 180 <= kelvin <= 340:
        raise RuntimeError("NAM response has invalid time or value")
    point = {
        "model": "nam",
        "climate_date": task.climate_date.isoformat(),
        "cycle_time_utc": initialized.isoformat(),
        "lead_hours": task.lead,
        "valid_time_utc": valid.isoformat(),
        "decision_time_utc": datetime.combine(task.climate_date, time(DECISION_HOUR_UTC), UTC).isoformat(),
        "variable": "TMP",
        "raw_value_kelvin": kelvin,
        "temperature_f": (kelvin - 273.15) * 1.8 + 32.0,
        "target_station": KLAX["station"],
        "target_latitude": KLAX["latitude"],
        "target_longitude": KLAX["longitude"],
        "grid_latitude": None,
        "grid_longitude": None,
        "grid_distance_km": None,
        "extraction_method": "Iowa_State_Mesonet_NCSS_point_subset",
        "source_provider": "Iowa_State_Mesonet_archive_of_NOAA_NAM218",
        "source_url": url,
        "request_url": url + params,
        "source_bytes": len(body),
        "source_sha256": _sha(body),
        "source_etag": headers.get("etag"),
        "source_last_modified": headers.get("last-modified"),
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "historical_availability_proven": False,
        "as_of_basis": "00Z cycle plus conservative six-hour operational delay precedes 18Z decision",
        "as_of_decision_safe": True,
    }
    _write_bytes(folder / "point.csv", body)
    _write_json(folder / "point.json", point)
    return point


def _run_task(root: Path, task: Task) -> dict[str, Any]:
    return _nam_task(root, task) if task.model == "nam" else _indexed_task(root, task)


def _daily(root: Path, climate_date: date) -> dict[str, Any]:
    samples: dict[str, list[dict[str, Any]]] = {"gfs": [], "nam": [], "nbm": []}
    expected = {"gfs": GFS_LEADS, "nam": NAM_LEADS, "nbm": NBM_LEADS}
    missing = []
    for model, leads in expected.items():
        for lead in leads:
            task = Task(model, climate_date, lead)
            value = _cached_point(_task_folder(root, task), task)
            if value is None:
                missing.append(task.key)
            else:
                samples[model].append(value)
    models: dict[str, Any] = {}
    if all(lead in {row["lead_hours"] for row in samples["gfs"]} for lead in GFS_PRIMARY_LEADS):
        primary = [row for row in samples["gfs"] if row["lead_hours"] in GFS_PRIMARY_LEADS]
        models["gfs"] = {
            "daily_high_f": max(row["temperature_f"] for row in primary),
            "primary_leads": list(GFS_PRIMARY_LEADS),
            "boundary_diagnostic_leads": [lead for lead in GFS_LEADS if lead not in GFS_PRIMARY_LEADS],
            "samples": samples["gfs"],
        }
        models["gfs_seamless"] = {
            "daily_high_f": models["gfs"]["daily_high_f"],
            "duplicate_of": "gfs",
            "duplicate_policy": "one_effective_signal",
            "samples": samples["gfs"],
        }
    if len(samples["nam"]) == len(expected["nam"]):
        models["nam"] = {
            "daily_high_f": max(row["temperature_f"] for row in samples["nam"]),
            "primary_leads": list(expected["nam"]),
            "samples": samples["nam"],
        }
    if len(samples["nbm"]) == len(expected["nbm"]):
        kalshi = [row for row in samples["nbm"] if row["lead_hours"] in NBM_KALSHI_LEADS]
        fixture = [row for row in samples["nbm"] if row["lead_hours"] in NBM_FIXTURE_LEADS]
        models["nbm"] = {
            "daily_high_f": max(row["temperature_f"] for row in kalshi),
            "primary_leads": list(NBM_KALSHI_LEADS),
            "fixture_compatible_daily_high_f": max(row["temperature_f"] for row in fixture),
            "fixture_compatible_leads": list(NBM_FIXTURE_LEADS),
            "boundary_diagnostic_leads": [7, 31],
            "samples": samples["nbm"],
        }
    value = {
        "schema": "friend-method-historical-weather-day-v1",
        "climate_date": climate_date.isoformat(),
        "station": KLAX,
        "decision_time_utc": datetime.combine(climate_date, time(DECISION_HOUR_UTC), UTC).isoformat(),
        "cycle_time_utc": datetime.combine(climate_date, time(0), UTC).isoformat(),
        "settlement_window": {
            "basis": "KLAX fixed PST climate day",
            "start_utc": datetime.combine(climate_date, time(8), UTC).isoformat(),
            "end_utc": datetime.combine(climate_date + timedelta(days=1), time(8), UTC).isoformat(),
        },
        "models": models,
        "effective_source_groups": [["gfs", "gfs_seamless"], ["nam"], ["nbm"]],
        "missing_tasks": missing,
        "complete": not missing and set(models) == {"gfs", "gfs_seamless", "nam", "nbm"},
        "contains_settlement_label": False,
        "contains_market_price": False,
        "actual_orders_placed": False,
    }
    output = root / f"date={climate_date.isoformat()}" / "daily.json"
    _write_json(output, value)
    return value


def _manifest(root: Path, start: date, end: date, failures: dict[str, str]) -> dict[str, Any]:
    days = []
    cursor = start
    while cursor <= end:
        daily = _daily(root, cursor)
        path = root / f"date={cursor.isoformat()}" / "daily.json"
        days.append({
            "climate_date": cursor.isoformat(),
            "complete": daily["complete"],
            "model_count": len(daily["models"]),
            "missing_task_count": len(daily["missing_tasks"]),
            "path": path.relative_to(root.parents[2]).as_posix(),
            "sha256": _file_sha(path),
        })
        cursor += timedelta(days=1)
    raw_files = [path for path in root.rglob("*") if path.is_file() and path.name not in {"manifest.json", "recovery-state.json"}]
    by_suffix: dict[str, dict[str, int]] = {}
    for path in raw_files:
        suffix = path.suffix or "none"
        row = by_suffix.setdefault(suffix, {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += path.stat().st_size
    manifest = {
        "schema": "friend-method-historical-weather-manifest-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "calendar_days": (end - start).days + 1,
        "complete_days": sum(row["complete"] for row in days),
        "station": KLAX,
        "decision_time_utc": "18:00",
        "cycle_hour_utc": 0,
        "models_requested": ["gfs", "gfs_seamless", "nam", "nbm"],
        "effective_source_groups": [["gfs", "gfs_seamless"], ["nam"], ["nbm"]],
        "gfs_duplicate_policy": "archive_one_numerical_signal_and_alias_gfs_seamless",
        "lead_policy": {
            "gfs_tmax_all": list(GFS_LEADS),
            "gfs_tmax_primary_fixture_compatible": list(GFS_PRIMARY_LEADS),
            "nam_temperature": list(NAM_LEADS),
            "nbm_temperature_all": list(NBM_LEADS),
            "nbm_temperature_fixed_pst_window": list(NBM_KALSHI_LEADS),
            "nbm_temperature_supplied_fixture": list(NBM_FIXTURE_LEADS),
        },
        "source_policy": {
            "gfs": "NOAA NODD AWS exact indexed TMAX GRIB ranges",
            "nam": "Iowa State Mesonet NCSS point subsets of archived NOAA NAM218",
            "nbm": "NOAA NODD AWS exact indexed TMP GRIB ranges",
        },
        "days": days,
        "failed_tasks": failures,
        "file_totals_by_suffix": by_suffix,
        "network_used_for_historical_acquisition": True,
        "live_or_current_feed_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "limitations": [
            "Archive presence and 00Z cycle timing support as-of use, but original publication timestamps are not independently proven.",
            "NAM point subsets are served by Iowa State Mesonet rather than directly decoded from NCEI full grids.",
            "GFS six-hour TMAX intervals do not align exactly to fixed-PST climate-day boundaries; f18/f24/f30 reproduce the supplied fixture convention.",
            "NBM retains both the supplied fixture's f007-f030 window and the KLAX fixed-PST f008-f031 window for sensitivity analysis.",
            "GFS Seamless is represented as the duplicate alias established by the supplied fixture and is not downloaded twice.",
        ],
    }
    payload = {key: value for key, value in manifest.items() if key != "self_sha256"}
    manifest["self_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    _write_json(root / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--start-date", default="2026-06-01")
    parser.add_argument("--end-date", default="2026-08-31")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    project = Path(args.project_root).resolve()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if end < start or end >= datetime.now(UTC).date():
        raise SystemExit("Date range must be nonempty and strictly historical")
    days = (end - start).days + 1
    if days > 92 or not 1 <= args.workers <= 16:
        raise SystemExit("Acquisition is capped at 92 days and 16 workers")
    # Initialize ecCodes once on the main thread before concurrent decoding.
    import eccodes
    eccodes.codes_get_api_version()
    root = project / "data/raw/friend_method_weather_v1"
    tasks = []
    cursor = start
    while cursor <= end:
        tasks.extend(Task("gfs", cursor, lead) for lead in GFS_LEADS)
        tasks.extend(Task("nam", cursor, lead) for lead in NAM_LEADS)
        tasks.extend(Task("nbm", cursor, lead) for lead in NBM_LEADS)
        cursor += timedelta(days=1)
    state_path = root / "recovery-state.json"
    completed = 0
    failures: dict[str, str] = {}
    started = datetime.now(UTC)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(_run_task, root, task): task for task in tasks}
        for future in as_completed(pending):
            task = pending[future]
            try:
                future.result()
            except Exception as exc:
                failures[task.key] = str(exc)
            completed += 1
            if completed % 25 == 0 or completed == len(tasks):
                state = {
                    "schema": "friend-method-weather-recovery-v1",
                    "started_at_utc": started.isoformat(),
                    "updated_at_utc": datetime.now(UTC).isoformat(),
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "task_count": len(tasks),
                    "completed_task_count": completed,
                    "failed_task_count": len(failures),
                    "failures": failures,
                    "network_used": True,
                    "protected_confirmation_labels_read": False,
                    "actual_orders_placed": False,
                }
                with STATE_LOCK:
                    _write_json(state_path, state)
                with PRINT_LOCK:
                    print(json.dumps({
                        "completed": completed, "total": len(tasks),
                        "failed": len(failures),
                    }), flush=True)
    manifest = _manifest(root, start, end, failures)
    print(json.dumps({
        "manifest": str((root / "manifest.json").relative_to(project)),
        "calendar_days": manifest["calendar_days"],
        "complete_days": manifest["complete_days"],
        "failed_tasks": len(failures),
        "file_totals_by_suffix": manifest["file_totals_by_suffix"],
    }, indent=2))


if __name__ == "__main__":
    main()
