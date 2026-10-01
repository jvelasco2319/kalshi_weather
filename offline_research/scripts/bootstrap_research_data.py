"""Audit and fill the portable KLAX research data cache.

This is an acquisition-only entry point.  It never starts a campaign, opens a
protected holdout, reads settlement labels, or places an order.  Every network
stage delegates to an existing finite historical downloader in this repository.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable


UTC = timezone.utc
STATE_PATH = Path("data/manifests/portable_bootstrap_state.json")
LOG_ROOT = Path("work/portable-bootstrap")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def _manifest(root: Path, name: str) -> dict:
    return _read_json(root / "data/manifests" / name)


def _relative_file_exists(root: Path, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        path = (root / value).resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return path.is_file() and path.stat().st_size > 0


def _climate_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "climate_2024-01-01_2026-01-08.json")
    archive = root / "data/raw/climate/CLILAX_2024-01-01_2026-01-08.zip"
    ready = bool(record.get("status") == "downloaded" and archive.is_file())
    return ready, "CLILAX 2024-01-01 through 2026-01-07"


def _weather_v3_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "v3_weather_bulk_progress.json")
    complete = int(record.get("days_complete", 0) or 0)
    unavailable = int(record.get("days_unavailable", 0) or 0)
    cache = root / "data/raw/weather_v3/compatibility"
    grib = sum(1 for _ in cache.glob("**/*.grib2")) if cache.is_dir() else 0
    indexes = sum(1 for _ in cache.glob("**/*.idx")) if cache.is_dir() else 0
    ready = complete == 543 and unavailable == 0 and grib >= 21_720 and indexes >= 10_860
    return ready, f"{complete}/543 days; {unavailable} unavailable; {grib} GRIB ranges; {indexes} indexes"


def _observations_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "v3_local_observations_partial.json")
    paths = (
        root / "data/normalized/v3_observations/weather_training/features/local_observations.parquet",
        root / "data/normalized/v3_observations/selection/features/local_observations.parquet",
    )
    ready = record.get("status") == "DEVELOPMENT_OBSERVATIONS_NORMALIZED_WITH_ASOF_COVERAGE" and all(
        path.is_file() for path in paths
    )
    return ready, f"IEM observations; {int(record.get('source_bytes', 0) or 0):,} source bytes"


def _weather_v3_normalized_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "v3_weather_normalization_progress.json")
    count = int(record.get("normalized_days", 0) or 0)
    normalized = root / "data/normalized/v3_weather"
    parquets = sum(1 for _ in normalized.glob("**/*.parquet")) if normalized.is_dir() else 0
    manifests = sum(1 for _ in normalized.glob("**/*manifest.json")) if normalized.is_dir() else 0
    ready = (
        record.get("status") == "COMPLETE_COMPONENTS_PUBLISHED"
        and record.get("coverage_complete") is True
        and count == 543
        and parquets == 1_086
        and manifests == 543
    )
    return ready, f"{count}/543 normalized days; {parquets}/1086 Parquet files; {manifests}/543 manifests"


def _weather_and_observations_ready(root: Path) -> tuple[bool, str]:
    weather_ready, weather_detail = _weather_v3_ready(root)
    observations_ready, observations_detail = _observations_ready(root)
    return weather_ready and observations_ready, f"weather: {weather_detail}; observations: {observations_detail}"


def _kalshi_metadata_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "kalshi_coverage.json")
    downloads = _manifest(root, "kalshi_downloads.json")
    days = int(record.get("covered_days", 0) or 0)
    contracts = len(record.get("contracts", [])) if isinstance(record.get("contracts"), list) else 0
    expected_hashes = set(record.get("source_sha256", [])) if isinstance(record.get("source_sha256"), list) else set()
    sources = downloads.get("sources", {}) if isinstance(downloads.get("sources"), dict) else {}
    present_hashes = {
        item.get("sha256")
        for item in sources.values()
        if isinstance(item, dict) and _relative_file_exists(root, item.get("path"))
    }
    source_pages = len(expected_hashes & present_hashes)
    ready = days >= 361 and contracts >= 2166 and expected_hashes and expected_hashes <= present_hashes
    return bool(ready), f"{days} days; {contracts} contracts; {source_pages}/{len(expected_hashes)} raw metadata pages"


def _monthly_ready(root: Path, *, period: int, start_month: int, end_month: int) -> tuple[bool, str]:
    present = 0
    for month in range(start_month, end_month + 1):
        import calendar
        last = calendar.monthrange(2025, month)[1]
        first = 5 if month == 1 else 1
        marker = root / "data/manifests" / (
            f"kalshi_candles_{'1m_' if period == 1 else ''}2025-{month:02d}-{first:02d}_"
            f"2025-{month:02d}-{last:02d}.json"
        )
        batch = _read_json(marker)
        records = batch.get("contracts", []) if isinstance(batch.get("contracts"), list) else []
        sources_present = bool(records) and all(
            isinstance(item, dict) and _relative_file_exists(root, item.get("path"))
            for item in records
        )
        if batch.get("status") == "complete" and sources_present:
            present += 1
    target = end_month - start_month + 1
    return present == target, f"{present}/{target} monthly batches at {period}-minute resolution"


def _kalshi_hourly_ready(root: Path) -> tuple[bool, str]:
    return _monthly_ready(root, period=60, start_month=1, end_month=12)


def _kalshi_minute_ready(root: Path) -> tuple[bool, str]:
    return _monthly_ready(root, period=1, start_month=1, end_month=6)


def _kalshi_trades_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "kalshi_trades_2025-01-05_2025-06-30.json")
    rows = sum(int(item.get("rows", 0) or 0) for item in record.get("contracts", []) if isinstance(item, dict))
    contracts = record.get("contracts", []) if isinstance(record.get("contracts"), list) else []
    pages = [
        page for item in contracts if isinstance(item, dict)
        for page in item.get("pages", []) if isinstance(page, dict)
    ]
    sources_present = bool(pages) and all(_relative_file_exists(root, page.get("path")) for page in pages)
    return record.get("status") == "complete" and sources_present, f"January-June 2025 public trades; {rows:,} rows; {len(pages)} raw pages"


def _friend_weather_ready(root: Path) -> tuple[bool, str]:
    archive = root / "data/raw/friend_method_weather_v1"
    complete = 0
    for path in archive.glob("date=2025-*/daily.json") if archive.is_dir() else ():
        if _read_json(path).get("complete") is True:
            complete += 1
    return complete == 365, f"{complete}/365 GFS/NAM/NBM days"


def _v10_ready(root: Path) -> tuple[bool, str]:
    record = _manifest(root, "v10_meteorology.json")
    outputs = record.get("outputs", {}) if isinstance(record.get("outputs"), dict) else {}
    outputs_present = len(outputs) == 2 and all(_relative_file_exists(root, path) for path in outputs)
    ready = (
        record.get("status") == "COMPLETE"
        and int(record.get("weather_rows", 0) or 0) == 722
        and int(record.get("observation_rows", 0) or 0) > 0
        and outputs_present
    )
    return ready, (
        f"{int(record.get('weather_rows', 0) or 0)} weather rows; "
        f"{int(record.get('observation_rows', 0) or 0)} observation rows"
    )


def _probalytics_ready(root: Path) -> tuple[bool, str]:
    record = _read_json(root / "data/raw/v5b_untouched/probalytics/recovery-state.json")
    completed = record.get("completed_dates", []) if isinstance(record.get("completed_dates"), list) else []
    complete_artifacts = sum(
        all((root / "data/raw/v5b_untouched/probalytics" / f"date={day}" / name).is_file()
            for name in ("manifest.json", "coverage.csv", "target_books.jsonl"))
        for day in completed
    )
    count = len(completed)
    ready = record.get("status") == "COMPLETE" and count == 50 and complete_artifacts == 50
    return ready, f"{complete_artifacts}/50 paid historical-depth dates with artifacts"


def _local_model_ready(root: Path) -> tuple[bool, str]:
    files = list((root / "data/models/gpt-oss-20b").glob("**/*.gguf"))
    model_bytes = sum(path.stat().st_size for path in files if path.is_file())
    inventory = list((root / "external/llama_cpp").glob("**/verified-inventory.json"))
    ready = model_bytes >= 10_000_000_000 and bool(inventory)
    return ready, f"{model_bytes / 1_000_000_000:.2f} GB model; runtime inventory={bool(inventory)}"


@dataclass(frozen=True)
class Stage:
    stage_id: str
    source: str
    description: str
    check: Callable[[Path], tuple[bool, str]]
    command: tuple[str, ...] | None
    access: str = "public"
    prerequisite: Callable[[Path], tuple[bool, str]] | None = None


def _calendar_weather_prerequisite(root: Path) -> tuple[bool, str]:
    first_half = all(
        (root / f"data/normalized/v3_weather/selection/date=2025-{month:02d}-{day:02d}/normalization_manifest.json").is_file()
        for month, day in ((1, 5), (6, 30))
    )
    second_half_count = sum(
        1 for _ in (root / "data/normalized/v7y_weather_features").glob("date=2025-*/manifest.json")
    )
    ready = first_half and second_half_count == 184
    return ready, f"V3 first-half endpoints={first_half}; V7Y second-half={second_half_count}/184"


def stages() -> tuple[Stage, ...]:
    py = sys.executable
    return (
        Stage(
            "clilax", "Iowa Environmental Mesonet / NWS", "Archived CLILAX daily climate reports",
            _climate_ready,
            (py, "-m", "klax_lab.acquire_climate", "--start", "2024-01-01", "--end", "2026-01-08"),
        ),
        Stage(
            "hrrr-gefs-v3", "NOAA historical object archives", "543 registered HRRR/GEFS model days",
            _weather_v3_ready,
            (py, "-m", "klax_lab.acquire_weather_v3", "registered-bulk", "--root", ".", "--max-bytes", "35000000000"),
        ),
        Stage(
            "observations-v3", "Iowa Environmental Mesonet", "Historical KLAX-area METAR/SPECI observations",
            _observations_ready,
            (py, "-m", "klax_lab.acquire_observations_v3", "--root", ".", "--max-bytes", "500000000"),
        ),
        Stage(
            "weather-v3-normalized", "Local cache-only transformation", "Hash-verified normalized V3 HRRR/GEFS features",
            _weather_v3_normalized_ready,
            (py, "-m", "klax_lab.weather_normalize_v3", "--root", "."),
            access="local",
            prerequisite=_weather_and_observations_ready,
        ),
        Stage(
            "kalshi-metadata", "Kalshi public historical API", "KXHIGHLAX historical contract metadata",
            _kalshi_metadata_ready,
            (py, "-m", "klax_lab.acquire_kalshi", "metadata", "--root", ".", "--start", "2024-01-01", "--end", "2025-12-31"),
        ),
        Stage(
            "kalshi-hourly-2025", "Kalshi public historical API", "Calendar-2025 hourly bid/ask candles",
            _kalshi_hourly_ready,
            (py, "-m", "klax_lab.acquire_kalshi", "monthly", "--root", ".", "--start", "2025-01-05", "--end", "2025-12-31", "--period-minutes", "60"),
            prerequisite=_kalshi_metadata_ready,
        ),
        Stage(
            "kalshi-minute-development", "Kalshi public historical API", "January-June 2025 one-minute bid/ask candles",
            _kalshi_minute_ready,
            (py, "-m", "klax_lab.acquire_kalshi", "monthly", "--root", ".", "--start", "2025-01-05", "--end", "2025-06-30", "--period-minutes", "1"),
            prerequisite=_kalshi_metadata_ready,
        ),
        Stage(
            "kalshi-public-trades", "Kalshi public historical API", "January-June 2025 public trade prints",
            _kalshi_trades_ready,
            (py, "-m", "klax_lab.acquire_kalshi", "trades", "--root", ".", "--start", "2025-01-05", "--end", "2025-06-30"),
            prerequisite=_kalshi_metadata_ready,
        ),
        Stage(
            "friend-weather-2025", "NOAA and Iowa Environmental Mesonet", "Calendar-2025 GFS, NAM, and NBM temperatures",
            _friend_weather_ready,
            (py, "scripts/acquire_v7y_friend_weather.py", "--project-root", ".", "--workers", "{workers}"),
        ),
        Stage(
            "v10-meteorology", "Iowa Environmental Mesonet and NOAA", "KLAX/KDAG observations and V10 meteorological fields",
            _v10_ready,
            (py, "-m", "v10.acquire_inputs", "--project-root", "."),
            prerequisite=_calendar_weather_prerequisite,
        ),
        Stage(
            "probalytics-depth", "Probalytics paid historical ClickHouse", "Registered 18:00 UTC Level-2 order-book evidence",
            _probalytics_ready,
            (py, "scripts/acquire_v5b_untouched_probalytics.py", "--username", "{username}"),
            access="paid",
        ),
        Stage(
            "local-model", "Hugging Face and llama.cpp GitHub release", "Pinned local research model and runtime",
            _local_model_ready,
            (py, "-m", "klax_lab.acquire_local_model", "--project-root", ".", "--download-runtime", "--inspect-runtime", "--download-model"),
            access="optional-large",
        ),
    )


def _selected(stage: Stage, args: argparse.Namespace) -> bool:
    if args.only and stage.stage_id not in args.only:
        return False
    if stage.access == "paid" and not args.include_probalytics:
        return False
    if stage.access == "optional-large" and not args.include_local_model:
        return False
    return True


def _status(root: Path, args: argparse.Namespace) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for stage in stages():
        ready, detail = stage.check(root)
        included = _selected(stage, args)
        result.append({
            "stage": stage.stage_id,
            "source": stage.source,
            "description": stage.description,
            "access": stage.access,
            "selected": included,
            "status": "READY" if ready else "MISSING",
            "detail": detail,
        })
    return result


def _run_stage(root: Path, stage: Stage, args: argparse.Namespace) -> dict[str, object]:
    ready, detail = stage.check(root)
    if ready:
        return {"stage": stage.stage_id, "status": "ALREADY_READY", "detail": detail}
    if stage.prerequisite is not None:
        prerequisite_ready, prerequisite_detail = stage.prerequisite(root)
        if not prerequisite_ready:
            return {
                "stage": stage.stage_id,
                "status": "BLOCKED_PREREQUISITE",
                "detail": prerequisite_detail,
                "action": "Restore the exact campaign-bound data from the network mirror, then rerun this command.",
            }
    if stage.command is None:
        return {"stage": stage.stage_id, "status": "BLOCKED", "detail": detail}
    if stage.access == "paid" and not args.probalytics_username:
        return {
            "stage": stage.stage_id,
            "status": "BLOCKED_CREDENTIAL",
            "detail": "Provide --probalytics-username; the downloader will securely prompt for the password.",
        }
    command = [
        value.format(workers=args.workers, username=args.probalytics_username or "")
        for value in stage.command
    ]
    log_dir = root / LOG_ROOT
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{stage.stage_id}.log"
    started = datetime.now(UTC).isoformat()
    safe_command = ["<python>" if index == 0 else value for index, value in enumerate(command)]
    print(json.dumps({"stage": stage.stage_id, "status": "RUNNING", "command": safe_command}), flush=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root / "src"), str(root), env.get("PYTHONPATH", ""))))
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{started}] stage={stage.stage_id}\n")
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdin=None,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    now_ready, now_detail = stage.check(root)
    return {
        "stage": stage.stage_id,
        "status": "READY" if completed.returncode == 0 and now_ready else "FAILED",
        "returncode": completed.returncode,
        "detail": now_detail,
        "log": log_path.relative_to(root).as_posix(),
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
    }


def _print_status(rows: list[dict[str, object]]) -> None:
    width = max(len(str(row["stage"])) for row in rows)
    for row in rows:
        selected = "selected" if row["selected"] else "not selected"
        print(f"{str(row['stage']):<{width}}  {row['status']:<7}  {selected:<12}  {row['detail']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "status", "run"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--include-probalytics", action="store_true")
    parser.add_argument("--probalytics-username", default=os.environ.get("PROBALYTICS_USERNAME", "").strip())
    parser.add_argument("--include-local-model", action="store_true")
    parser.add_argument("--only", action="append", choices=[stage.stage_id for stage in stages()])
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "AGENTS.md").is_file():
        parser.error("--project-root is not the offline_research project")

    rows = _status(root, args)
    if args.command in {"plan", "status"}:
        _print_status(rows)
        return 0

    results: list[dict[str, object]] = []
    for stage in stages():
        if not _selected(stage, args):
            continue
        result = _run_stage(root, stage, args)
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
        if result["status"] == "FAILED":
            break

    final_rows = _status(root, args)
    body = {
        "schema_version": "portable-bootstrap-v1",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "status": "COMPLETE" if all(row["status"] == "READY" for row in final_rows if row["selected"]) else "PARTIAL",
        "network_mirror_checked_by_powershell_launcher": True,
        "protected_holdout_opened": False,
        "settlement_labels_downloaded": False,
        "live_feed_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "results": results,
        "coverage": final_rows,
    }
    _write_json(root / STATE_PATH, body)
    _print_status(final_rows)
    print(f"\nBootstrap status: {body['status']}")
    return 0 if not any(item["status"] == "FAILED" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
