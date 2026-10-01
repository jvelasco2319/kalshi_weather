"""Read-only integrity census for the calendar-2025 HRRR/GEFS study inputs."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.weather_sources_v3 import build_revised_daily_plans, verify_cache_only
from scripts.audit_v7y_weather_history import policy_registration_binding, repair_prestate_binding
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT,
    POLICY_ID,
    validate_normalized_day,
    verify_policy_registration,
)
from v5.acquire_probability_evidence import build_v5_daily_weather_plans


START = date(2025, 1, 1)
MARKET_START = date(2025, 1, 5)
LEGACY_END = date(2025, 6, 30)
END = date(2025, 12, 31)
LEGACY_POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dates() -> list[date]:
    return [START + timedelta(days=offset) for offset in range((END - START).days + 1)]


def _paths(root: Path, target: date) -> tuple[Path, Path]:
    day = target.isoformat()
    if target <= LEGACY_END:
        folder = root / "data/normalized/v3_weather/selection" / f"date={day}"
        return folder, folder / "normalization_manifest.json"
    folder = root / OUTPUT_ROOT / f"date={day}"
    return folder, folder / "manifest.json"


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an offset")
    return parsed.astimezone(UTC)


def _eligible_times(value: Any) -> list[str]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError("eligible decision times must be an ordered string list")
    return list(value)


def _verify_row_timing(row: dict[str, Any], target: date, expected_policy_id: str) -> None:
    nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
    reference = _parse_utc(row.get("forecast_reference_time_utc"), "forecast reference time")
    if nominal != reference:
        raise ValueError("nominal and decoded cycles differ")
    modified_raw = row.get("source_last_modified_at_utc")
    modified = None if modified_raw in (None, "") else _parse_utc(modified_raw, "Last-Modified")
    expected_available = max(
        value for value in (nominal + timedelta(hours=6), modified) if value is not None
    )
    recorded_available = _parse_utc(
        row.get("effective_information_available_at_utc"), "effective availability time"
    )
    eligible = [
        f"{hour:02d}:00"
        for hour in (12, 15, 18)
        if expected_available <= datetime.combine(target, time(hour), UTC)
    ]
    if (
        row.get("availability_policy_id") != expected_policy_id
        or row.get("availability_delay_hours") != 6
        or recorded_available != expected_available
        or recorded_available > datetime.combine(target, time(18), UTC)
        or _eligible_times(row.get("eligible_decision_times_utc")) != eligible
        or "18:00" not in eligible
    ):
        raise ValueError("18:00 UTC availability binding differs")


def _verify_normalized(root: Path, target: date) -> tuple[str, list[str]]:
    folder, manifest_path = _paths(root, target)
    if not manifest_path.is_file():
        return "missing", []
    problems: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return "corrupt", [f"manifest:{type(exc).__name__}:{exc}"]
    day = target.isoformat()
    if manifest.get("climate_date") != day:
        problems.append("manifest_date")
    if target <= LEGACY_END:
        if manifest.get("status") != "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND":
            problems.append("manifest_status")
        expected_rows = {
            "hrrr_points.parquet": 24,
            "gefs_summary_points.parquet": 16,
        }
    else:
        try:
            validate_normalized_day(root, target, manifest)
        except Exception as exc:
            problems.append(f"manifest_validation:{type(exc).__name__}:{exc}")
        expected_rows = {
            "hrrr_points.parquet": 24,
            "gefs_summary_points.parquet": 16,
        }
    outputs = {Path(item["path"]).name: item for item in manifest.get("outputs", [])}
    for name, expected_count in expected_rows.items():
        path = folder / name
        record = outputs.get(name)
        if record is None:
            problems.append(f"{name}:manifest_record")
            continue
        if not path.is_file():
            problems.append(f"{name}:missing")
            continue
        if path.stat().st_size != record.get("bytes"):
            problems.append(f"{name}:bytes")
        if _hash(path) != record.get("sha256"):
            problems.append(f"{name}:sha256")
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            problems.append(f"{name}:parquet:{type(exc).__name__}")
            continue
        model = "hrrr" if name.startswith("hrrr") else "gefs"
        if len(frame) != expected_count or record.get("rows") != expected_count:
            problems.append(f"{name}:rows")
        if set(frame["climate_date"].astype(str)) != {day}:
            problems.append(f"{name}:date")
        if set(frame["model"].astype(str)) != {model}:
            problems.append(f"{name}:model")
        if set(frame["point_id"].astype(str)) != {"KLAX"}:
            problems.append(f"{name}:point")
        if "contains_settlement_label" in frame and frame["contains_settlement_label"].astype(bool).any():
            problems.append(f"{name}:label_flag")
        if not frame["as_of_validated"].astype(bool).all():
            problems.append(f"{name}:asof")
        policy_id = LEGACY_POLICY_ID if target <= LEGACY_END else POLICY_ID
        try:
            for row in frame.to_dict(orient="records"):
                _verify_row_timing(row, target, policy_id)
        except Exception as exc:
            problems.append(f"{name}:18z_timing:{type(exc).__name__}:{exc}")
    return ("valid" if not problems else "corrupt"), problems


def _verify_raw(root: Path, target: date) -> tuple[str, list[str]]:
    if target <= LEGACY_END:
        cache = root / "data/raw/weather_v3/compatibility"
        plans = build_revised_daily_plans(target, today=date(2026, 9, 29))
    else:
        cache = root / "data/raw/v5p/weather"
        plans = build_v5_daily_weather_plans(target)
    expected = (4, 16)
    problems: list[str] = []
    for plan, count in zip(plans, expected):
        try:
            result = verify_cache_only(plan, cache)
            if result.get("objects_verified") != count:
                problems.append(f"{plan.purpose}:object_count")
        except Exception as exc:
            problems.append(f"{plan.purpose}:{type(exc).__name__}:{exc}")
    if not problems:
        return "valid", []
    if all("FileNotFoundError" in item or "missing" in item.lower() for item in problems):
        return "missing", problems
    return "incomplete_or_corrupt", problems


def run(root: Path, output: Path | None) -> dict[str, Any]:
    snapshot_started = datetime.now(UTC)
    registration = verify_policy_registration(root)
    repair_prestate = repair_prestate_binding(root, registration)
    rows: list[dict[str, Any]] = []
    for target in _dates():
        if target < MARKET_START:
            rows.append({
                "date": target.isoformat(),
                "role": "outside_market_universe",
                "raw": "not_registered",
                "normalized": "not_registered",
                "raw_problems": [],
                "normalized_problems": [],
            })
            continue
        normalized, normalized_problems = _verify_normalized(root, target)
        raw, raw_problems = _verify_raw(root, target)
        rows.append({
            "date": target.isoformat(),
            "role": "calibration_overlap" if target < date(2025, 2, 4) else "post_calibration_primary",
            "raw": raw,
            "normalized": normalized,
            "raw_problems": raw_problems,
            "normalized_problems": normalized_problems,
        })
    registered = [row for row in rows if row["role"] != "outside_market_universe"]
    result = {
        "schema_version": "v7y-hrrr-gefs-coverage-audit-v2",
        "snapshot_started_at_utc": snapshot_started.isoformat(),
        "snapshot_completed_at_utc": datetime.now(UTC).isoformat(),
        "network_used": False,
        "settlement_labels_read": False,
        "availability_policy_id": POLICY_ID,
        "availability_policy_registration": policy_registration_binding(registration),
        "availability_repair_prestate": repair_prestate,
        "target_year_days": len(rows),
        "outside_market_universe_days": sum(row["role"] == "outside_market_universe" for row in rows),
        "registered_market_days": len(registered),
        "raw_valid_days": sum(row["raw"] == "valid" for row in registered),
        "raw_missing_days": [row["date"] for row in registered if row["raw"] == "missing"],
        "raw_incomplete_or_corrupt": [row for row in registered if row["raw"] == "incomplete_or_corrupt"],
        "normalized_valid_days": sum(row["normalized"] == "valid" for row in registered),
        "normalized_missing_days": [row["date"] for row in registered if row["normalized"] == "missing"],
        "normalized_corrupt": [row for row in registered if row["normalized"] == "corrupt"],
        "rows": rows,
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    result = run(root, root / args.output if args.output else None)
    compact = {key: value for key, value in result.items() if key != "rows"}
    compact["raw_incomplete_or_corrupt"] = [row["date"] for row in compact["raw_incomplete_or_corrupt"]]
    compact["normalized_corrupt"] = [row["date"] for row in compact["normalized_corrupt"]]
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
