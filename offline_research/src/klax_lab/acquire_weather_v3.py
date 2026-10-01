"""Bounded online acquisition for the V3 historical weather compatibility pilot.

This module is intentionally separate from offline experiment code.  It can
fetch only the explicit one-day HRRR/GEFS plan produced by
``weather_sources_v3`` and writes immutable range files plus provenance.  Full
GRIB objects, current endpoints, protected-final dates, and open-ended ranges
are unsupported.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .acquire_weather import RateLimitedSession, TransferBudget, _write_immutable, validate_range_response
from .provenance import write_json
from .weather_sources_v3 import (
    ArchiveObjectPlan,
    HistoricalWeatherPlan,
    _verified_cache_file,
    build_compatibility_pilot,
    build_gefs_derived_product_pilot,
    build_revised_daily_plans,
    select_index_ranges,
    verify_cache_only,
)


UTC = timezone.utc


class HistoricalWeatherUnavailable(ValueError):
    """A specifically requested immutable historical archive object is absent."""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _cached(path: Path, *, url: str, start: int | None, end: int | None,
            maximum_bytes: int, require_grib: bool) -> dict[str, Any] | None:
    if not path.exists() and not path.with_suffix(path.suffix + ".json").exists():
        return None
    return _verified_cache_file(
        path, url=url, start=start, end=end,
        maximum_bytes=maximum_bytes, require_grib=require_grib,
    )


def _fetch(session, budget: TransferBudget, *, url: str, path: Path,
           maximum_bytes: int, start: int | None = None,
           end: int | None = None) -> tuple[dict[str, Any], bool]:
    if (start is None) != (end is None):
        raise ValueError("A weather range requires both start and end")
    ranged = start is not None and end is not None
    if ranged:
        expected = end - start + 1
        if expected <= 0 or expected > maximum_bytes:
            raise ValueError("Selected weather range exceeds its registered field budget")
        reservation = expected
    else:
        reservation = maximum_bytes
    cached = _cached(path, url=url, start=start, end=end,
                     maximum_bytes=maximum_bytes, require_grib=ranged)
    if cached is not None:
        return cached, True

    headers = {"User-Agent": "klax-v3-bounded-historical-pilot/1.0", "Accept-Encoding": "identity"}
    if ranged:
        headers["Range"] = f"bytes={start}-{end}"
    with budget.reservation(reservation) as consume, session.get(
            url, headers=headers, stream=True, timeout=(20, 90), allow_redirects=False) as response:
        if ranged:
            validate_range_response(response.status_code, response.headers.get("Content-Range"), start, end)
        elif response.status_code != 200:
            if response.status_code in (403, 404):
                raise HistoricalWeatherUnavailable(
                    f"Historical weather index unavailable, HTTP {response.status_code}: {url}"
                )
            raise ValueError(f"Historical weather index request failed, HTTP {response.status_code}")
        declared = int(response.headers.get("Content-Length", 0))
        if declared < 0 or declared > reservation or (ranged and declared and declared != reservation):
            raise ValueError("Weather response Content-Length exceeds its reservation")
        partial = path.with_suffix(path.suffix + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        count, digest = 0, sha256()
        try:
            with partial.open("wb") as output:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    consume(len(chunk))
                    count += len(chunk)
                    if count > reservation:
                        raise ValueError("Weather response exceeded its registered byte budget")
                    digest.update(chunk)
                    output.write(chunk)
            if count == 0 or (ranged and count != reservation) or (declared and count != declared):
                raise ValueError("Weather response is empty or truncated")
            if ranged and partial.read_bytes()[:4] != b"GRIB":
                raise ValueError("Selected weather range does not begin with a GRIB message")
            metadata = {
                "url": url,
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
                "range_start": start,
                "range_end": end,
                "bytes": count,
                "sha256": digest.hexdigest(),
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "historical_availability_proven": False,
            }
            _write_immutable(path, partial.read_bytes())
            _write_immutable(path.with_suffix(path.suffix + ".json"), _json_bytes(metadata))
        finally:
            if partial.exists():
                partial.unlink()
    return metadata, False


def _acquire_plan(plan: HistoricalWeatherPlan, cache_root: Path, session,
                  budget: TransferBudget) -> dict[str, Any]:
    records, requests, cache_hits = [], 0, 0
    for item in plan.objects:
        folder = cache_root / item.cache_key
        index_path = folder / "source.idx"
        index_meta, was_cached = _fetch(
            session, budget, url=item.index_url, path=index_path,
            maximum_bytes=item.index_max_bytes,
        )
        requests += int(not was_cached)
        cache_hits += int(was_cached)
        ranges = select_index_ranges(index_path.read_text(encoding="utf-8"), item)
        selectors = {field.field_id: field for field in item.fields}
        fields = []
        for selected in ranges:
            path = folder / f"{selected.field_id}.grib2"
            metadata, was_cached = _fetch(
                session, budget, url=item.url, path=path,
                maximum_bytes=selectors[selected.field_id].maximum_bytes,
                start=selected.start, end=selected.end,
            )
            requests += int(not was_cached)
            cache_hits += int(was_cached)
            fields.append({
                "field_id": selected.field_id,
                "bytes": metadata["bytes"],
                "sha256": metadata["sha256"],
                "range_start": selected.start,
                "range_end": selected.end,
            })
        records.append({
            "source_id": item.source_id,
            "model": item.model,
            "initialized_at": item.initialized_at.isoformat(),
            "lead_hours": item.lead_hours,
            "member_id": item.member_id,
            "index_bytes": index_meta["bytes"],
            "index_sha256": index_meta["sha256"],
            "fields": fields,
        })
    return {"network_requests": requests, "cache_hits": cache_hits, "records": records}


def acquire_compatibility_pilot(project_root: Path, target_date: date,
                                *, max_transfer_bytes: int = 304_000_000,
                                today: date | None = None,
                                session_override=None) -> dict[str, Any]:
    """Acquire and cache-verify the exact registered one-day V3 pilot."""
    root = Path(project_root).resolve()
    pilot = build_compatibility_pilot(target_date, today=today)
    if max_transfer_bytes <= 0 or max_transfer_bytes > pilot["budget"]["planned_maximum_bytes"]:
        raise ValueError("Pilot transfer budget must be positive and no larger than 304 MB")
    cache_root = root / "data/raw/weather_v3/compatibility"
    budget = TransferBudget(max_transfer_bytes)
    session = session_override if session_override is not None else RateLimitedSession(2.0)
    try:
        hrrr = _acquire_plan(pilot["hrrr"], cache_root, session, budget)
        gefs = _acquire_plan(pilot["gefs"], cache_root, session, budget)
    finally:
        if session_override is None:
            session.close()
    hrrr_verification = verify_cache_only(pilot["hrrr"], cache_root)
    gefs_verification = verify_cache_only(pilot["gefs"], cache_root)
    report = {
        "schema_version": 1,
        "component": "weather_compatibility_pilot",
        "status": "COMPATIBILITY_PILOT_ACQUIRED_AND_CACHE_VERIFIED",
        "target_date": target_date.isoformat(),
        "network_used_for_acquisition": hrrr["network_requests"] + gefs["network_requests"] > 0,
        "offline_cache_verification_network_used": False,
        "protected_final_read": False,
        "coverage_complete": False,
        "transfer_limit_bytes": max_transfer_bytes,
        "transferred_bytes": budget.transferred,
        "network_requests": hrrr["network_requests"] + gefs["network_requests"],
        "cache_hits": hrrr["cache_hits"] + gefs["cache_hits"],
        "hrrr": hrrr,
        "gefs": gefs,
        "hrrr_cache_verification": hrrr_verification,
        "gefs_cache_verification": gefs_verification,
        "limitations": [
            "This one-day pilot does not establish full historical coverage",
            "Historical publication latency remains unproven",
            "GRIB decoding and KLAX spatial extraction remain separate gates",
        ],
    }
    write_json(root / "data/manifests/v3_weather_compatibility_pilot.json", report)
    return report


def acquire_gefs_derived_product_pilot(project_root: Path, target_date: date,
                                       *, max_transfer_bytes: int = 128_000_000,
                                       today: date | None = None,
                                       session_override=None) -> dict[str, Any]:
    """Acquire the lower-request archived GEFS ensemble mean/spread pilot."""
    root = Path(project_root).resolve()
    plan = build_gefs_derived_product_pilot(target_date, today=today)
    if max_transfer_bytes <= 0 or max_transfer_bytes > plan.limits.maximum_bytes:
        raise ValueError("Derived GEFS pilot budget must be positive and no larger than 128 MB")
    cache_root = root / "data/raw/weather_v3/compatibility"
    budget = TransferBudget(max_transfer_bytes)
    session = session_override if session_override is not None else RateLimitedSession(2.0)
    try:
        acquired = _acquire_plan(plan, cache_root, session, budget)
    finally:
        if session_override is None:
            session.close()
    verification = verify_cache_only(plan, cache_root)
    report = {
        "schema_version": 1,
        "component": "gefs_derived_product_compatibility_pilot",
        "status": "GEFS_DERIVED_PRODUCT_PILOT_ACQUIRED_AND_CACHE_VERIFIED",
        "target_date": target_date.isoformat(),
        "network_used_for_acquisition": acquired["network_requests"] > 0,
        "offline_cache_verification_network_used": False,
        "protected_final_read": False,
        "coverage_complete": False,
        "transfer_limit_bytes": max_transfer_bytes,
        "transferred_bytes": budget.transferred,
        "network_requests": acquired["network_requests"],
        "cache_hits": acquired["cache_hits"],
        "gefs": acquired,
        "cache_verification": verification,
        "limitations": [
            "Ensemble mean and spread do not preserve member-level skew or multimodality",
            "This one-day pilot does not establish historical coverage",
            "Historical publication latency and KLAX extraction remain separate gates",
        ],
    }
    write_json(root / "data/manifests/v3_gefs_derived_product_pilot.json", report)
    return report


def acquire_revised_weather_history(project_root: Path, start_date: date, end_date: date,
                                     *, max_total_source_bytes: int = 35_000_000_000,
                                     today: date | None = None,
                                     session_override=None) -> dict[str, Any]:
    """Acquire a finite subset of the admitted 2024 through June-2025 plan."""
    root = Path(project_root).resolve()
    allowed_start, allowed_end = date(2024, 1, 1), date(2025, 6, 30)
    if (not allowed_start <= start_date <= end_date <= allowed_end
            or (end_date - start_date).days + 1 > 543):
        raise ValueError("Revised weather history must remain inside the registered 543-day scope")
    if not 0 < max_total_source_bytes <= 35_000_000_000:
        raise ValueError("Revised weather source cap must be positive and no larger than 35 GB")
    cache_root = root / "data/raw/weather_v3/compatibility"
    existing_source_bytes = sum(
        path.stat().st_size for path in cache_root.rglob("*")
        if path.is_file() and path.suffix in {".idx", ".grib2"}
    ) if cache_root.exists() else 0
    remaining = max_total_source_bytes - existing_source_bytes
    if remaining <= 0:
        raise RuntimeError("Revised weather source cap is already exhausted")
    budget = TransferBudget(remaining)
    session = session_override if session_override is not None else RateLimitedSession(2.0)
    days = []
    current = start_date
    try:
        while current <= end_date:
            hrrr_plan, gefs_plan = build_revised_daily_plans(current, today=today)
            before = budget.transferred
            try:
                hrrr = _acquire_plan(hrrr_plan, cache_root, session, budget)
                gefs = _acquire_plan(gefs_plan, cache_root, session, budget)
                hrrr_verified = verify_cache_only(hrrr_plan, cache_root)
                gefs_verified = verify_cache_only(gefs_plan, cache_root)
                day = {
                    "date": current.isoformat(),
                    "status": "RAW_RANGES_CACHE_VERIFIED",
                    "network_requests": hrrr["network_requests"] + gefs["network_requests"],
                    "cache_hits": hrrr["cache_hits"] + gefs["cache_hits"],
                    "new_transfer_bytes": budget.transferred - before,
                    "hrrr_objects": hrrr_verified["objects_verified"],
                    "gefs_objects": gefs_verified["objects_verified"],
                }
            except HistoricalWeatherUnavailable as error:
                day = {
                    "date": current.isoformat(),
                    "status": "SOURCE_UNAVAILABLE",
                    "network_requests": None,
                    "cache_hits": None,
                    "new_transfer_bytes": budget.transferred - before,
                    "error": str(error),
                }
            days.append(day)
            progress = {
                "schema_version": 1,
                "status": "RUNNING",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "last_complete_date": current.isoformat(),
                "days_attempted": len(days),
                "days_complete": sum(row["status"] == "RAW_RANGES_CACHE_VERIFIED" for row in days),
                "days_unavailable": sum(row["status"] == "SOURCE_UNAVAILABLE" for row in days),
                "existing_source_bytes_at_start": existing_source_bytes,
                "new_transfer_bytes": budget.transferred,
                "max_total_source_bytes": max_total_source_bytes,
                "days": days,
            }
            write_json(root / "data/manifests/v3_weather_bulk_progress.json", progress)
            print((
                f"{current}: cache verified; {day['network_requests']} requests; "
                f"{day['new_transfer_bytes']:,} new bytes"
                if day["status"] == "RAW_RANGES_CACHE_VERIFIED" else
                f"{current}: source unavailable; {day['new_transfer_bytes']:,} partial new bytes"
            ), flush=True)
            current += timedelta(days=1)
    finally:
        if session_override is None:
            session.close()
    full_scope = start_date == allowed_start and end_date == allowed_end
    unavailable = sum(day["status"] == "SOURCE_UNAVAILABLE" for day in days)
    complete = sum(day["status"] == "RAW_RANGES_CACHE_VERIFIED" for day in days)
    report = {
        "schema_version": 1,
        "component": "revised_weather_raw_history",
        "status": ("FULL_RAW_RANGE_ACQUISITION_COMPLETE" if full_scope and not unavailable
                   else "FULL_SCOPE_ACQUISITION_WITH_GAPS" if full_scope
                   else "PARTIAL_RAW_RANGE_ACQUISITION_COMPLETE" if not unavailable
                   else "PARTIAL_RANGE_ACQUISITION_WITH_GAPS"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days_attempted": len(days),
        "days_complete": complete,
        "days_unavailable": unavailable,
        "protected_final_read": False,
        "coverage_complete": full_scope and unavailable == 0,
        "network_used_for_acquisition": any(day["network_requests"] for day in days),
        "existing_source_bytes_at_start": existing_source_bytes,
        "new_transfer_bytes": budget.transferred,
        "total_source_bytes_after": existing_source_bytes + budget.transferred,
        "max_total_source_bytes": max_total_source_bytes,
        "days": days,
        "remaining_gates": [
            "GRIB decode and KLAX spatial extraction",
            "local METAR/SPECI acquisition and as-of normalization",
            "coverage, gap, availability, and unit audit",
        ],
    }
    write_json(root / "data/manifests/v3_weather_bulk_progress.json", report)
    return report


def acquire_registered_weather_history(project_root: Path,
                                        *, max_total_source_bytes: int = 35_000_000_000,
                                        today: date | None = None) -> dict[str, Any]:
    """Acquire the two registered partitions without crossing the Jan 1-4 gap."""
    root = Path(project_root).resolve()
    training = acquire_revised_weather_history(
        root, date(2024, 1, 1), date(2024, 12, 31),
        max_total_source_bytes=max_total_source_bytes, today=today,
    )
    development = acquire_revised_weather_history(
        root, date(2025, 1, 5), date(2025, 6, 30),
        max_total_source_bytes=max_total_source_bytes, today=today,
    )
    days = training["days"] + development["days"]
    unavailable = sum(day["status"] == "SOURCE_UNAVAILABLE" for day in days)
    complete = sum(day["status"] == "RAW_RANGES_CACHE_VERIFIED" for day in days)
    report = {
        "schema_version": 1,
        "component": "revised_weather_raw_history",
        "status": ("FULL_RAW_RANGE_ACQUISITION_COMPLETE" if unavailable == 0
                   else "FULL_SCOPE_ACQUISITION_WITH_GAPS"),
        "partitions": {
            "weather_training": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
            "selection": {"start_date": "2025-01-05", "end_date": "2025-06-30"},
        },
        "unregistered_gap_dates_acquired": False,
        "days_attempted": len(days),
        "days_complete": complete,
        "days_unavailable": unavailable,
        "protected_final_read": False,
        "coverage_complete": unavailable == 0 and complete == 543,
        "network_used_for_acquisition": any(day["network_requests"] for day in days),
        "total_source_bytes_after": development["total_source_bytes_after"],
        "max_total_source_bytes": max_total_source_bytes,
        "days": days,
        "remaining_gates": development["remaining_gates"],
    }
    write_json(root / "data/manifests/v3_weather_bulk_progress.json", report)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("pilot", "derived-pilot", "bulk", "registered-bulk"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--date", type=date.fromisoformat, default=date(2025, 1, 5))
    parser.add_argument("--max-bytes", type=int)
    args = parser.parse_args(argv)
    if args.command == "pilot":
        result = acquire_compatibility_pilot(
            args.root, args.date,
            max_transfer_bytes=args.max_bytes or 304_000_000,
        )
    elif args.command == "derived-pilot":
        result = acquire_gefs_derived_product_pilot(
            args.root, args.date,
            max_transfer_bytes=args.max_bytes or 128_000_000,
        )
    elif args.command == "bulk":
        if args.start is None or args.end is None:
            parser.error("bulk requires --start and --end")
        result = acquire_revised_weather_history(
            args.root, args.start, args.end,
            max_total_source_bytes=args.max_bytes or 35_000_000_000,
        )
    else:
        result = acquire_registered_weather_history(
            args.root, max_total_source_bytes=args.max_bytes or 35_000_000_000,
        )
    print(json.dumps({key: value for key, value in result.items() if key != "days"}, indent=2))


if __name__ == "__main__":
    main()
