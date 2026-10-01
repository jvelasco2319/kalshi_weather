"""Outcome-blind acquisition planning for the V5 probability confirmation.

The module inventories only feature/source coverage.  It never opens weather
settlement labels, CLILAX product text, Kalshi raw responses, or protected
confirmation outcomes, and it has no network or order path.
"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .probability import validate_frozen_leader


VERSION = "klax-v5-probability-acquisition-plan-v2"
START = date(2025, 7, 1)
END = date(2026, 8, 31)
TOTAL_DAYS = (END - START).days + 1
EXISTING_WEATHER_BYTES = 20_602_514_466
EXISTING_WEATHER_DAYS = 543


class AcquisitionPlanError(ValueError):
    pass


def _canonical_hash(value: Any) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionPlanError(f"missing or invalid safe manifest: {path}") from exc
    if not isinstance(value, dict):
        raise AcquisitionPlanError(f"safe manifest must be an object: {path}")
    return value


def _overlap_days(first: date, last: date) -> int:
    left, right = max(first, START), min(last, END)
    return max(0, (right - left).days + 1)


def _fixed_folds() -> list[dict[str, Any]]:
    sizes = [TOTAL_DAYS // 5 + int(index < TOTAL_DAYS % 5) for index in range(5)]
    cursor = START
    folds = []
    for index, size in enumerate(sizes, 1):
        last = cursor + timedelta(days=size - 1)
        folds.append({
            "fold": index, "date_start": cursor.isoformat(),
            "date_end": last.isoformat(), "calendar_days": size,
        })
        cursor = last + timedelta(days=1)
    if cursor != END + timedelta(days=1):
        raise AcquisitionPlanError("fixed fold construction differs")
    return folds


def audit_current_outcome_blind_coverage(root: Path) -> dict[str, Any]:
    """Inspect only safe summaries and filenames; never raw protected data."""
    root = Path(root).resolve()
    validate_frozen_leader(root)

    weather = _load_object(root / "data/manifests/v3_weather_bulk_progress.json")
    weather_dates = {
        row.get("date") for row in weather.get("days", [])
        if isinstance(row, dict) and row.get("status") == "RAW_RANGES_CACHE_VERIFIED"
        and isinstance(row.get("date"), str)
        and START.isoformat() <= row["date"] <= END.isoformat()
    }

    market = _load_object(root / "data/manifests/kalshi_coverage.json")
    safe_contracts = market.get("contracts")
    if not isinstance(safe_contracts, list):
        raise AcquisitionPlanError("outcome-free Kalshi coverage contracts missing")
    market_dates = {
        row.get("climate_date") for row in safe_contracts
        if isinstance(row, dict) and isinstance(row.get("climate_date"), str)
        and START.isoformat() <= row["climate_date"] <= END.isoformat()
    }
    market_contracts = sum(
        isinstance(row, dict) and row.get("climate_date") in market_dates
        for row in safe_contracts
    )

    candle_dates: set[str] = set()
    candle_contracts: set[str] = set()
    for path in sorted((root / "data/manifests").glob("kalshi_candles_1m_*.json")):
        batch = _load_object(path)
        for row in batch.get("contracts", []):
            if not isinstance(row, dict):
                continue
            day, ticker = row.get("climate_date"), row.get("ticker")
            if isinstance(day, str) and START.isoformat() <= day <= END.isoformat():
                candle_dates.add(day)
                if isinstance(ticker, str):
                    candle_contracts.add(ticker)

    trade_dates: set[str] = set()
    trade_contracts: set[str] = set()
    for path in sorted((root / "data/manifests").glob("kalshi_trades_*.json")):
        batch = _load_object(path)
        for row in batch.get("contracts", []):
            if not isinstance(row, dict):
                continue
            day, ticker = row.get("climate_date"), row.get("ticker")
            if isinstance(day, str) and START.isoformat() <= day <= END.isoformat():
                trade_dates.add(day)
                if isinstance(ticker, str):
                    trade_contracts.add(ticker)

    climate_days = 0
    climate_archives = []
    for path in sorted((root / "data/manifests").glob("climate_*.json")):
        if path.name in {"climate_normalization.json", "climate_quarantine.json"}:
            continue
        manifest = _load_object(path)
        try:
            first = date.fromisoformat(manifest["start_inclusive"])
            last_exclusive = date.fromisoformat(manifest["end_exclusive"])
        except (KeyError, TypeError, ValueError):
            continue
        overlap = _overlap_days(first, last_exclusive - timedelta(days=1))
        climate_days = max(climate_days, overlap)
        climate_archives.append({
            "path": path.relative_to(root).as_posix(),
            "start_inclusive": first.isoformat(),
            "end_exclusive": last_exclusive.isoformat(),
            "overlap_days": overlap,
        })

    body = {
        "version": VERSION,
        "record_type": "outcome_blind_local_coverage_audit",
        "confirmation_window": {
            "date_start": START.isoformat(), "date_end": END.isoformat(),
            "calendar_days": TOTAL_DAYS,
        },
        "weather": {
            "hrrr_gefs_complete_days": len(weather_dates),
            "missing_days": TOTAL_DAYS - len(weather_dates),
        },
        "kalshi_safe_metadata": {
            "covered_days": len(market_dates),
            "safe_contract_count": market_contracts,
            "missing_days": TOTAL_DAYS - len(market_dates),
        },
        "kalshi_one_minute_candles": {
            "covered_days": len(candle_dates),
            "covered_contracts": len(candle_contracts),
            "missing_days": TOTAL_DAYS - len(candle_dates),
        },
        "kalshi_public_trades": {
            "covered_days": len(trade_dates),
            "covered_contracts": len(trade_contracts),
            "missing_days": TOTAL_DAYS - len(trade_dates),
        },
        "clilax_archive_envelope": {
            "covered_days": climate_days,
            "missing_days": TOTAL_DAYS - climate_days,
            "safe_manifest_records": climate_archives,
            "product_text_read": False,
        },
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "raw_kalshi_responses_read": False,
        "clilax_product_text_read": False,
        "actual_orders_placed": False,
    }
    body["audit_sha256"] = _canonical_hash(body)
    return body


def build_probability_acquisition_plan(root: Path) -> dict[str, Any]:
    """Build the finite V5 acquisition specification without downloading."""
    coverage = audit_current_outcome_blind_coverage(root)
    estimated_weather_bytes = round(
        EXISTING_WEATHER_BYTES / EXISTING_WEATHER_DAYS * TOTAL_DAYS,
    )
    body = {
        "version": VERSION,
        "record_type": "frozen_probability_confirmation_acquisition_plan",
        "correction": {
            "supersedes": "klax-v5-probability-acquisition-plan-v1",
            "reason": "The limiter allows two request starts per second; the prior plan treated it as one start every two seconds.",
        },
        "status": "PLANNED_NO_DATA_ACQUIRED",
        "confirmation_window": {
            "date_start": START.isoformat(), "date_end": END.isoformat(),
            "calendar_days": TOTAL_DAYS, "chronological_folds": _fixed_folds(),
        },
        "weather_plan": {
            "models": ["hrrr", "gefs"],
            "hrrr_cycle_utc": "06:00",
            "hrrr_leads_hours": [2, 8, 14, 20],
            "gefs_cycle_utc": "00:00",
            "gefs_products": ["avg", "spr"],
            "gefs_leads_hours": [9, 12, 15, 18, 21, 24, 27, 30],
            "requests_per_day": 60,
            "planned_requests": TOTAL_DAYS * 60,
            "request_starts_per_second": 2.0,
            "serial_request_start_floor_hours": TOTAL_DAYS * 60 / 2 / 3600,
            "estimated_raw_bytes_from_v3_observed_average": estimated_weather_bytes,
            "estimated_raw_gib": estimated_weather_bytes / 1024 ** 3,
            "conservative_registered_ceiling_bytes": TOTAL_DAYS * 320_000_000,
            "archive_metadata_to_preserve": ["ETag", "Last-Modified", "retrieved_at_utc"],
            "historical_publication_time_proven": False,
            "required_availability_sensitivities_hours": [8, 12],
            "existing_v3_bulk_command_reusable": False,
            "blocker": "V3 planners deliberately reject dates after 2025-06-30; a V5-bounded wrapper must preserve the same object and field contract in a separate cache.",
        },
        "kalshi_plan": {
            "series": "KXHIGHLAX",
            "required_sources": [
                "historical market metadata", "one-minute historical candlesticks",
                "historical public trades",
            ],
            "existing_2025_h2_hourly_candle_contracts": 1104,
            "historical_depth_available": False,
            "safe_commands_after_v5_metadata_manifest_exists": [
                ".\\.venv\\Scripts\\python.exe -m klax_lab.acquire_kalshi monthly --root . --start 2025-07-01 --end 2026-08-31 --period-minutes 1",
                ".\\.venv\\Scripts\\python.exe -m klax_lab.acquire_kalshi trades --root . --start 2025-07-01 --end 2026-08-31 --trade-page-limit 1000 --max-trade-pages-per-contract 100",
            ],
            "existing_metadata_command_reusable_as_is": False,
            "blockers": [
                "Existing metadata command overwrites the shared kalshi_coverage.json instead of emitting a V5-specific immutable manifest.",
                "Existing raw Kalshi client has a 500 MB lifetime cache cap with about 276 MB already verified; later one-minute candles and trades may exceed the remainder.",
                "Historical candles and public prints do not establish order-book depth or hypothetical fills.",
            ],
        },
        "clilax_plan": {
            "existing_bounded_command": ".\\.venv\\Scripts\\python.exe -m klax_lab.acquire_climate --start 2026-01-08 --end 2026-09-01",
            "command_end_is_exclusive": True,
            "expected_missing_days": 236,
            "existing_source": "Iowa Environmental Mesonet archival copy of NWS CLILAX",
            "required_cross_check": "Official NOAA/NCEI KLAX daily maximum and Kalshi settlement metadata",
            "blocker": "The local CLILAX downloader uses an archival mirror, so exact NWS product completeness and revision history still require independent reconciliation.",
        },
        "current_coverage_audit_sha256": coverage["audit_sha256"],
        "current_coverage": coverage,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "labels_remain_sealed": True,
        "actual_orders_placed": False,
    }
    body["plan_sha256"] = _canonical_hash(body)
    return body


def write_plan(root: Path) -> Path:
    root = Path(root).resolve()
    output = root / "data/manifests/v5_probability_acquisition_plan_v2.json"
    payload = json.dumps(
        build_probability_acquisition_plan(root), indent=2, sort_keys=True,
        ensure_ascii=False, allow_nan=False,
    ) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_text(encoding="utf-8") != payload:
        raise AcquisitionPlanError("immutable acquisition plan differs")
    if not output.exists():
        output.write_text(payload, encoding="utf-8", newline="\n")
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result: Mapping[str, Any] | str = (
        str(write_plan(args.project_root)) if args.write
        else build_probability_acquisition_plan(args.project_root)
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
