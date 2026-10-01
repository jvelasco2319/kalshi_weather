"""Finite historical acquisition, separate from the offline GRIB reader.

Archive URL patterns are adapted from the user's pinned weather_pipeline._url
at commit a82f8aee0afecf568b3ce16f339c0bd7201773b8. The source stays untouched.
Only index files and selected 2-m temperature byte ranges are requested. No
latest endpoints, subscriptions, order methods, or hidden offline fetches exist.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Lock, local
from time import monotonic, sleep
from typing import Iterable
from uuid import uuid4

from .domain import climate_day_bounds
from .grib_reader import extract_temperature, sampled_daily_feature

UTC = timezone.utc
PARENT_COMMIT = "a82f8aee0afecf568b3ce16f339c0bd7201773b8"
PARENT_PIPELINE_SHA256 = "0c3e032b71538efe6b2903fa9fb5e2ec1a1901c6243038cdb38e3393ab3fc4fc"
MODEL_ARCHIVES = {
    "gfs": "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
    "nbm": "https://noaa-nbm-grib2-pds.s3.amazonaws.com",
}
DEFAULT_LEADS = (9, 12, 15, 18, 21, 24, 27, 30)
_ARTIFACT_LOCK = Lock()


def archived_url(model: str, initialization: datetime, lead: int) -> str:
    if model not in MODEL_ARCHIVES:
        raise ValueError("supported sources are gfs and nbm; GFS mirrors are not separate models")
    if initialization.tzinfo is None or initialization.utcoffset() is None:
        raise ValueError("initialization must be timezone-aware")
    init = initialization.astimezone(UTC)
    if init.minute or init.second or init.microsecond or init.hour not in (0, 6, 12, 18):
        raise ValueError("pilot supports exact common 00/06/12/18 UTC cycles")
    if isinstance(lead, bool) or not isinstance(lead, int) or not 1 <= lead <= 120:
        raise ValueError("forecast lead must be a whole hour from 1 through 120")
    if init.date() >= datetime.now(UTC).date():
        raise ValueError("only strictly historical model initializations may be acquired")
    day, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    if model == "gfs":
        return f"{MODEL_ARCHIVES[model]}/gfs.{day}/{cycle}/atmos/gfs.t{cycle}z.pgrb2.0p25.f{lead:03d}"
    return f"{MODEL_ARCHIVES[model]}/blend.{day}/{cycle}/core/blend.t{cycle}z.core.f{lead:03d}.co.grib2"


def planned_leads(target_day: date, initialization: datetime, include_boundary_samples: bool = False) -> list[int]:
    if initialization.tzinfo is None or initialization.utcoffset() is None:
        raise ValueError("initialization must be timezone-aware")
    start, end = climate_day_bounds(target_day)
    init = initialization.astimezone(UTC)
    first = int((start - init).total_seconds() // 3600)
    last_exclusive = int((end - init).total_seconds() // 3600)
    result = [lead for lead in range(max(1, first), last_exclusive) if lead % 3 == 0]
    if include_boundary_samples:
        result = sorted(set(result + [first, last_exclusive - 1]))
    if not result or min(result) < 1 or max(result) > 120:
        raise ValueError("target day is outside supported forecast horizons")
    return result


@dataclass(frozen=True)
class IndexEntry:
    record: str
    start: int
    end: int
    line: str


def select_temperature_range(index_text: str, initialization: datetime, lead: int) -> IndexEntry:
    """Select plain deterministic TMP; reject uncertainty/max/probability fields."""
    rows = []
    for line in index_text.splitlines():
        fields = line.split(":")
        if len(fields) < 6:
            raise ValueError("malformed archive index line")
        try:
            offset = int(fields[1])
        except ValueError as exc:
            raise ValueError("invalid archive index offset") from exc
        rows.append((offset, fields, line))
    if not rows or any(a[0] > b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("archive index offsets are missing or unsorted")
    wanted_init = "d=" + initialization.astimezone(UTC).strftime("%Y%m%d%H")
    matches = []
    for offset, fields, line in rows:
        if fields[2] == wanted_init and fields[3] == "TMP" and fields[4] == "2 m above ground" and fields[5] == f"{lead} hour fcst" and not any(part.strip() for part in fields[6:]):
            later = [candidate[0] for candidate in rows if candidate[0] > offset]
            if not later:
                raise ValueError("selected final index record has no bounded byte range")
            matches.append(IndexEntry(fields[0], offset, min(later) - 1, line))
    if len(matches) != 1:
        raise ValueError(f"expected one deterministic 2-m TMP field, found {len(matches)}")
    return matches[0]


def validate_range_response(status: int, content_range: str | None, start: int, end: int) -> int:
    if status != 206:
        raise ValueError(f"server must honor bounded Range with HTTP 206, received {status}")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range or "")
    if not match or (int(match[1]), int(match[2])) != (start, end) or int(match[3]) <= end:
        raise ValueError("Content-Range does not match requested bytes")
    return int(match[3])


class TransferBudget:
    def __init__(self, byte_limit: int):
        if byte_limit <= 0:
            raise ValueError("transfer limit must be positive")
        self.byte_limit = byte_limit
        self.transferred = 0
        self.reserved = 0
        self._lock = Lock()

    def check(self, required: int) -> None:
        with self._lock:
            if required < 0 or self.transferred + self.reserved + required > self.byte_limit:
                raise RuntimeError("historical download transfer budget exhausted")

    @contextmanager
    def reservation(self, maximum: int):
        """Reserve a response's worst-case bytes before dispatch, across threads."""
        with self._lock:
            if maximum < 0 or self.transferred + self.reserved + maximum > self.byte_limit:
                raise RuntimeError("historical download transfer budget exhausted")
            self.reserved += maximum
        consumed = 0

        def consume(count: int):
            nonlocal consumed
            with self._lock:
                if count < 0 or consumed + count > maximum:
                    raise ValueError("response exceeded its reserved byte range")
                consumed += count
                self.reserved -= count
                self.transferred += count

        try:
            yield consume
        finally:
            with self._lock:
                self.reserved -= maximum - consumed


class RateLimitedSession:
    """One acquisition job's global request-start cap, including index requests."""

    def __init__(self, requests_per_second: float = 2.0):
        import requests
        if not 0 < requests_per_second <= 2:
            raise ValueError("historical weather request rate must be in (0,2]")
        self._session_factory = requests.Session
        self._thread_local = local()
        self._sessions = []
        self._lock = Lock()
        self.interval = 1 / requests_per_second
        self.last_started = None

    def get(self, *args, **kwargs):
        with self._lock:
            if not hasattr(self._thread_local, "session"):
                self._thread_local.session = self._session_factory()
                self._sessions.append(self._thread_local.session)
            if self.last_started is not None:
                remaining = self.interval - (monotonic() - self.last_started)
                if remaining > 0:
                    sleep(remaining)
            self.last_started = monotonic()
        return self._thread_local.session.get(*args, **kwargs)

    def close(self):
        # Called after all request futures have finished; Sessions are not shared
        # across request threads, and the global limiter still governs them all.
        for session in self._sessions:
            session.close()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_immutable(path: Path, contents: bytes) -> None:
    with _ARTIFACT_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != contents:
                raise ValueError(f"immutable artifact already exists with different content: {path}")
            return
        temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
        try:
            with temporary.open("xb") as stream:
                stream.write(contents)
            temporary.rename(path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _load_cached(path: Path, url: str, start: int | None, end: int | None) -> dict | None:
    sidecar = path.with_suffix(path.suffix + ".json")
    if not path.exists():
        if sidecar.exists():
            raise ValueError(f"cache metadata exists without source bytes: {path}")
        return None
    if not sidecar.exists():
        raise ValueError(f"cached raw file lacks provenance: {path}")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if (metadata["url"], metadata["range_start"], metadata["range_end"]) != (url, start, end):
        raise ValueError("cached source identity differs from requested source")
    if metadata["sha256"] != sha256(path.read_bytes()).hexdigest() or metadata["bytes"] != path.stat().st_size:
        raise ValueError("cached source hash or length mismatch")
    return metadata


def _download(session, url: str, path: Path, budget: TransferBudget, *, start: int | None = None, end: int | None = None) -> dict:
    cached = _load_cached(path, url, start, end)
    if cached is not None:
        return cached
    ranged = start is not None and end is not None
    maximum = end - start + 1 if ranged else 2_000_000
    headers = {"User-Agent": "klax-offline-historical-research/0.1", "Accept-Encoding": "identity"}
    if ranged:
        headers["Range"] = f"bytes={start}-{end}"
    with budget.reservation(maximum) as consume, session.get(url, headers=headers, stream=True, timeout=(20, 90), allow_redirects=False) as response:
        if ranged:
            total_source_bytes = validate_range_response(response.status_code, response.headers.get("Content-Range"), start, end)
        else:
            if response.status_code != 200:
                raise ValueError(f"historical index unavailable, HTTP {response.status_code}")
            total_source_bytes = None
        declared = int(response.headers.get("Content-Length", 0))
        if declared > maximum or (ranged and declared and declared != maximum):
            raise ValueError("unexpected response Content-Length; full-file fallback is forbidden")
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".part")
        count, digest = 0, sha256()
        try:
            with partial.open("wb") as output:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    consume(len(chunk))
                    count += len(chunk)
                    if count > maximum:
                        raise ValueError("response exceeded bounded download size")
                    digest.update(chunk)
                    output.write(chunk)
            if not count or (ranged and count != maximum) or (declared and count != declared):
                raise ValueError("response was truncated or empty")
            if ranged:
                with partial.open("rb") as stream:
                    if stream.read(4) != b"GRIB":
                        raise ValueError("selected byte range does not begin with a GRIB message")
            metadata = {
                "url": url, "retrieved_at": datetime.now(UTC).isoformat(),
                "range_start": start, "range_end": end, "bytes": count,
                "sha256": digest.hexdigest(), "full_source_bytes": total_source_bytes,
                "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"),
                "historical_availability_proven": False,
            }
            partial.rename(path)
            _write_immutable(path.with_suffix(path.suffix + ".json"), _json_bytes(metadata))
        except BaseException:
            if partial.exists():
                partial.unlink()
            raise
    return metadata


def _retrieve_field(root: Path, model: str, initialization: datetime, lead: int, session, budget: TransferBudget, offline: bool):
    """HTTP work only; ecCodes decoding remains serial in deterministic order."""
    url = archived_url(model, initialization, lead)
    directory = root / "data/raw/weather" / model / initialization.strftime("%Y%m%d") / "t00z"
    index_path = directory / f"f{lead:03d}.idx"
    if offline:
        index_meta = _load_cached(index_path, url + ".idx", None, None)
        if index_meta is None:
            raise FileNotFoundError(f"offline index missing: {index_path}")
    else:
        index_meta = _download(session, url + ".idx", index_path, budget)
    selected = select_temperature_range(index_path.read_text(encoding="utf-8"), initialization, lead)
    field_path = directory / f"f{lead:03d}.2m_temperature.grib2"
    if offline:
        field_meta = _load_cached(field_path, url, selected.start, selected.end)
        if field_meta is None:
            raise FileNotFoundError(f"offline GRIB field missing: {field_path}")
    else:
        field_meta = _download(session, url, field_path, budget, start=selected.start, end=selected.end)
    return field_path, field_meta, index_meta, selected


def acquire_day(project_root: Path, target_day: date, models: Iterable[str], *, max_transfer_bytes: int = 250_000_000, include_boundary_samples: bool = False, offline: bool = False, leads_override: list[int] | None = None, transfer_budget: TransferBudget | None = None, session_override=None, verbose: bool = True, download_workers: int = 4, executor_override=None) -> dict:
    """Acquire one explicit past day, or replay only its verified cache offline.

    A short leads_override is a compatibility probe and is never labeled complete
    daily coverage. Offline mode imports no HTTP library and makes no HTTP calls.
    """
    root = Path(project_root).resolve()
    if isinstance(download_workers, bool) or not isinstance(download_workers, int) or not 1 <= download_workers <= 4:
        raise ValueError("download concurrency must be from one through four")
    source = root / "external/weather_data_collector/weather_pipeline.py"
    if not source.exists() or sha256(source.read_bytes()).hexdigest() != PARENT_PIPELINE_SHA256:
        raise ValueError("pinned parent collector source is missing or hash changed")
    if target_day >= datetime.now(UTC).date():
        raise ValueError("target day must be strictly in the past")
    models = sorted(models)
    if not models or len(set(models)) != len(models) or any(model not in MODEL_ARCHIVES for model in models):
        raise ValueError("request distinct explicit gfs and/or nbm models")
    initialization = datetime.combine(target_day, time.min, UTC)
    planned = planned_leads(target_day, initialization, include_boundary_samples)
    requested = sorted(leads_override) if leads_override is not None else planned
    if not requested or len(set(requested)) != len(requested) or any(lead not in planned for lead in requested):
        raise ValueError("probe leads must be a nonempty unique subset of the registered daily plan")
    budget = transfer_budget if transfer_budget is not None else TransferBudget(max_transfer_bytes)
    transferred_before = budget.transferred
    session = None
    if not offline:
        session = session_override if session_override is not None else RateLimitedSession()
    report = {
        "target_date": target_day.isoformat(), "parent_collector_commit": PARENT_COMMIT,
        "parent_pipeline_sha256": PARENT_PIPELINE_SHA256, "offline_cache_only": offline,
        "planned_leads": planned, "requested_leads": requested,
        "assumed_available_at": (initialization + timedelta(hours=6)).isoformat(),
        "availability_basis": "conservative init+6h research assumption, not verified historical publication time",
        "historical_availability_proven": False,
        "proposed_decision_time": (initialization + timedelta(hours=14)).isoformat(),
        "models": {}, "errors": [],
        "download_workers": download_workers,
    }
    executor = executor_override if executor_override is not None else ThreadPoolExecutor(max_workers=download_workers)
    futures = {(model, lead): executor.submit(_retrieve_field, root, model, initialization, lead, session, budget, offline) for model in models for lead in requested}
    try:
        for model in models:
            samples, sources = [], []
            for lead in requested:
                try:
                    field_path, field_meta, index_meta, selected = futures[(model, lead)].result()
                    point_path = field_path.with_suffix(".point.json")
                    if point_path.exists():
                        sample = json.loads(point_path.read_text(encoding="utf-8"))
                        from .grib_reader import validate_metadata
                        validate_metadata(sample, initialization, lead, target_day)
                        if sample["source_sha256"] != field_meta["sha256"]:
                            raise ValueError("point cache disagrees with raw source checksum")
                    else:
                        sample = extract_temperature(field_path, expected_init=initialization, expected_lead=lead, target_day=target_day)
                        sample["source_path"] = str(field_path.relative_to(root)).replace("\\", "/")
                        _write_immutable(point_path, _json_bytes(sample))
                    samples.append(sample)
                    sources.append({"index": index_meta, "field": field_meta, "index_line": selected.line})
                    if verbose:
                        print(f"{model} {target_day} f{lead:03d}: validated {field_meta['bytes']} bytes", flush=True)
                except Exception as exc:
                    report["errors"].append({"model": model, "lead": lead, "error": f"{type(exc).__name__}: {exc}"})
                    print(f"{model} {target_day} f{lead:03d}: {type(exc).__name__}: {exc}", flush=True)
                    if "transfer budget exhausted" in str(exc):
                        raise
            complete = len(samples) == len(planned) and set(requested) == set(planned)
            result = {"status": "COMPLETE_SAMPLED_PROXY" if complete else "PARTIAL_COMPATIBILITY_PROBE", "samples": samples, "sources": sources}
            if complete:
                result["daily_feature"] = sampled_daily_feature(samples, target_day, planned)
                result["daily_feature"].update({"model": model, "assumed_available_at": report["assumed_available_at"], "historical_availability_proven": False, "availability_basis": report["availability_basis"]})
                feature_path = root / "data/normalized/weather" / f"{model}_{target_day}_sampled_temperature.json"
                _write_immutable(feature_path, _json_bytes(result["daily_feature"]))
            report["models"][model] = result
    finally:
        if executor_override is None:
            executor.shutdown(wait=True, cancel_futures=True)
        if session is not None and session_override is None:
            session.close()
    report["new_transfer_bytes"] = budget.transferred - transferred_before
    report["transfer_limit_bytes"] = budget.byte_limit
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    report_path = root / "data/manifests/weather" / f"{target_day}_{timestamp}.json"
    _write_immutable(report_path, _json_bytes(report))
    report["manifest_path"] = str(report_path)
    return report


def acquire_range(project_root: Path, start_date: date, end_date: date, models: Iterable[str], *, max_transfer_bytes: int = 50_000_000_000, include_boundary_samples: bool = True, offline: bool = False, download_workers: int = 4) -> dict:
    """Bounded, resumable historical job with shared budget and <=2 requests/sec.

    Raw files, provenance and decoded point caches are reusable. Monthly progress
    summaries are mutable operational state; raw source artifacts stay immutable.
    The 50 GB ceiling covers existing weather raw bytes plus the new transfer cap.
    """
    root = Path(project_root).resolve()
    if end_date < start_date or (end_date - start_date).days > 731:
        raise ValueError("historical range must have 1 through 732 days")
    if end_date >= datetime.now(UTC).date() or not 0 < max_transfer_bytes <= 50_000_000_000:
        raise ValueError("historical dates and a positive <=50GB limit are required")
    raw_directory = root / "data/raw/weather"
    existing = sum(path.stat().st_size for path in raw_directory.rglob("*") if path.is_file()) if raw_directory.exists() else 0
    allowance = min(max_transfer_bytes, 50_000_000_000 - existing)
    if allowance <= 0:
        raise RuntimeError("project weather storage budget exhausted")
    budget = TransferBudget(allowance)
    models = list(models)
    session = None if offline else RateLimitedSession(2.0)
    executor = ThreadPoolExecutor(max_workers=download_workers)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    months = {}
    result = {"run_id": run_id, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "model_sources": models, "existing_weather_bytes": existing, "new_transfer_limit_bytes": allowance, "storage_cap_bytes": 50_000_000_000, "request_rate_per_second": 2, "download_workers": download_workers, "status": "RUNNING", "days_complete": 0, "days_with_errors": 0, "monthly_progress": []}
    current = start_date
    last_attempted = None
    try:
        while current <= end_date:
            last_attempted = current
            report = acquire_day(root, current, models, include_boundary_samples=include_boundary_samples, offline=offline, transfer_budget=budget, session_override=session, verbose=False, download_workers=download_workers, executor_override=executor)
            complete = not report["errors"] and all(item["status"] == "COMPLETE_SAMPLED_PROXY" for item in report["models"].values())
            result["days_complete"] += int(complete)
            result["days_with_errors"] += int(not complete)
            month = current.strftime("%Y-%m")
            months.setdefault(month, []).append({"date": current.isoformat(), "complete": complete, "manifest": report["manifest_path"], "new_transfer_bytes": report["new_transfer_bytes"], "errors": report["errors"]})
            progress = root / "data/manifests/weather/progress" / f"{month}_{run_id}.json"
            progress.parent.mkdir(parents=True, exist_ok=True)
            partial = progress.with_suffix(".part")
            partial.write_bytes(_json_bytes({"run_id": run_id, "month": month, "days": months[month], "cumulative_transfer_bytes": budget.transferred}))
            partial.replace(progress)
            if str(progress) not in result["monthly_progress"]:
                result["monthly_progress"].append(str(progress))
            print(f"{current}: {'complete' if complete else 'gaps'}; job downloaded {budget.transferred:,} bytes", flush=True)
            current += timedelta(days=1)
        result["status"] = "COMPLETED_WITH_GAPS" if result["days_with_errors"] else "COMPLETED"
    except BaseException as exc:
        result["status"] = "INTERRUPTED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        if session is not None:
            session.close()
        result["new_transfer_bytes"] = budget.transferred
        result["last_attempted_date"] = last_attempted.isoformat() if last_attempted else None
        summary = root / "data/manifests/weather" / f"range_{run_id}.json"
        _write_immutable(summary, _json_bytes(result))
        result["manifest_path"] = str(summary)
    return result


@contextmanager
def _cli_job_lock(root: Path):
    """An OS-held advisory lock prevents overlapping weather CLI jobs."""
    import os
    path = Path(root).resolve() / "data/weather_acquisition.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("another historical weather CLI job is running") from exc
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    dates = parser.add_mutually_exclusive_group(required=True)
    dates.add_argument("--date", type=date.fromisoformat)
    dates.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--models", nargs="+", choices=sorted(MODEL_ARCHIVES), required=True)
    parser.add_argument("--max-transfer-mb", type=int, default=250)
    parser.add_argument("--include-boundary-samples", action="store_true")
    parser.add_argument("--probe-leads", nargs="+", type=int)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--download-workers", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    if not 1 <= args.max_transfer_mb <= 50_000:
        parser.error("transfer cap must be from 1 through 50000 MB")
    if args.start_date:
        if args.end_date is None or args.probe_leads:
            parser.error("range requires --end-date and disallows --probe-leads")
        with _cli_job_lock(args.project_root):
            report = acquire_range(args.project_root, args.start_date, args.end_date, args.models, max_transfer_bytes=args.max_transfer_mb * 1_000_000, include_boundary_samples=args.include_boundary_samples, offline=args.offline, download_workers=args.download_workers)
        print(json.dumps(report, indent=2))
        return 1 if report["days_with_errors"] else 0
    if args.end_date:
        parser.error("--end-date requires --start-date")
    with _cli_job_lock(args.project_root):
        report = acquire_day(args.project_root, args.date, args.models, max_transfer_bytes=args.max_transfer_mb * 1_000_000, include_boundary_samples=args.include_boundary_samples, offline=args.offline, leads_override=args.probe_leads, download_workers=args.download_workers)
    print(json.dumps({"manifest": report["manifest_path"], "new_transfer_bytes": report["new_transfer_bytes"], "errors": report["errors"]}, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
