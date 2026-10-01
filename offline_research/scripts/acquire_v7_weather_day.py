"""Acquire and normalize all registered weather inputs for one elapsed V7 date."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
import json
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.acquire_weather import TransferBudget
from klax_lab.acquire_weather_v3 import _acquire_plan
from klax_lab.weather_sources_v3 import verify_cache_only
from v5b_confirmation.weather_acquisition import CACHE_PATH, build_daily_plans
from v5b_confirmation.weather_normalization import normalize_day
from friend_method.exact_evaluation import load_exact_features
from scripts.acquire_friend_method_weather import (
    GFS_LEADS,
    NAM_LEADS,
    NBM_LEADS,
    Task,
    _daily,
    _run_task,
    _write_json,
)
from v7_shadow.campaign import target_dates


class RateLimitedSession:
    def __init__(self) -> None:
        import requests
        self.session = requests.Session()
        self.last = 0.0

    def get(self, *args, **kwargs):
        remaining = 0.5 - (time.monotonic() - self.last)
        if remaining > 0:
            time.sleep(remaining)
        self.last = time.monotonic()
        return self.session.get(*args, **kwargs)

    def close(self) -> None:
        self.session.close()


def run(root: Path, target: date, workers: int = 12) -> dict:
    config = json.loads((root / "configs/v7_six_week_shadow.json").read_text(encoding="utf-8"))
    allowed = {date.fromisoformat(value) for value in target_dates(config)}
    if target not in allowed or target >= datetime.now(UTC).date():
        raise ValueError("V7 weather target must be registered and strictly historical")
    if not 1 <= workers <= 16:
        raise ValueError("workers must be in [1,16]")

    hrrr, gefs = build_daily_plans(target)
    cache = root / CACHE_PATH
    budget = TransferBudget(320_000_000)
    session = RateLimitedSession()
    try:
        h_result = _acquire_plan(hrrr, cache, session, budget)
        g_result = _acquire_plan(gefs, cache, session, budget)
    finally:
        session.close()
    h_verify = verify_cache_only(hrrr, cache)
    g_verify = verify_cache_only(gefs, cache)
    normalized = normalize_day(root, target, allowed)

    friend_root = root / "data/raw/friend_method_weather_v1"
    tasks = (
        [Task("gfs", target, lead) for lead in GFS_LEADS]
        + [Task("nam", target, lead) for lead in NAM_LEADS]
        + [Task("nbm", target, lead) for lead in NBM_LEADS]
    )
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_run_task, friend_root, task): task for task in tasks}
        for future in as_completed(pending):
            task = pending[future]
            try:
                future.result()
            except Exception as exc:
                failures[task.key] = f"{type(exc).__name__}: {exc}"
    daily = _daily(friend_root, target)
    daily_path = friend_root / f"date={target.isoformat()}" / "daily.json"
    _write_json(daily_path, daily)
    if failures or not daily["complete"]:
        raise RuntimeError(f"friend weather incomplete: {failures or daily['missing_tasks']}")
    config_friend = json.loads((root / "configs/friend_method_exact_test.json").read_text(encoding="utf-8"))
    primary = next(window for window in config_friend["weather"]["windows"] if window["role"] == "primary")
    load_exact_features(root, [target.isoformat()], primary)

    manifest = {
        "schema_version": "klax-v7-weather-day-v1",
        "climate_date": target.isoformat(),
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "hrrr_objects": h_verify["objects_verified"],
        "gefs_objects": g_verify["objects_verified"],
        "hrrr_network_requests": h_result["network_requests"],
        "gefs_network_requests": g_result["network_requests"],
        "normalized_manifest": normalized,
        "friend_daily_path": daily_path.relative_to(root).as_posix(),
        "friend_models": sorted(daily["models"]),
        "contains_settlement_labels": False,
        "outcomes_read": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    output = root / "runs/campaigns_v7/v7-shadow-20260929-20261109/daily" / target.isoformat() / "input/weather-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    pending.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    result = run(Path(args.project_root).resolve(), args.date, args.workers)
    print(json.dumps({
        "climate_date": result["climate_date"],
        "hrrr_objects": result["hrrr_objects"],
        "gefs_objects": result["gefs_objects"],
        "friend_models": result["friend_models"],
        "outcomes_read": result["outcomes_read"],
    }, indent=2))


if __name__ == "__main__":
    main()

