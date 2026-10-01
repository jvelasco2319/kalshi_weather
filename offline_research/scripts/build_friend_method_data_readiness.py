"""Build an outcome-blind join-readiness record for the exact friend-method test."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def filehash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seal(value: dict) -> dict:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {**value, "self_sha256": hashlib.sha256(payload).hexdigest()}


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    weather_manifest_path = project / "data/raw/friend_method_weather_v1/manifest.json"
    weather_verification_path = project / "data/manifests/friend_method_weather_v1_verification.json"
    market_path = project / "data/normalized/v5p_probalytics_full_history_20260601_20260831/target_top_of_book.parquet"
    market_validation_path = project / "data/raw/v5p/probalytics/full_history_20260601_20260831/validation.json"
    development_features_path = project / "data/development/v5b_next/weather_features.json"

    weather_manifest = read(weather_manifest_path)
    weather_verification = read(weather_verification_path)
    market_validation = read(market_validation_path)
    development_features = read(development_features_path)["features"]
    weather_dates = {row["climate_date"] for row in weather_manifest["days"]}
    market_dates = set(pq.read_table(market_path, columns=["climate_date"])["climate_date"].to_pylist())
    development_dates = {row["climate_date"] for row in development_features}
    strict_dates = set(market_validation["dates_with_any_strict_ready_market_at_5s"])

    assert weather_verification["complete_days"] == 92
    assert weather_verification["point_records"] == 3496
    assert market_validation["valid"] and market_validation["calendar_dates"] == 92
    assert market_validation["dates_with_snapshots"] == len(market_dates) == 83
    assert market_dates <= weather_dates
    assert development_dates <= weather_dates and len(development_dates) == 64
    assert not market_validation["live_feed_used"] and not market_validation["orders_placed"]

    result = seal({
        "schema": "friend-method-exact-data-readiness-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "weather": {
            "calendar_days": 92,
            "complete_days": 92,
            "point_records": 3496,
            "manifest_path": "data/raw/friend_method_weather_v1/manifest.json",
            "manifest_sha256": filehash(weather_manifest_path),
            "verification_path": "data/manifests/friend_method_weather_v1_verification.json",
            "verification_sha256": filehash(weather_verification_path),
        },
        "historical_market": {
            "path": "data/normalized/v5p_probalytics_full_history_20260601_20260831/target_top_of_book.parquet",
            "sha256": filehash(market_path),
            "rows": market_validation["target_row_count"],
            "calendar_days_requested": 92,
            "dates_with_snapshots": len(market_dates),
            "dates_without_snapshots": sorted(weather_dates - market_dates),
            "strict_ready_dates_at_5s": len(strict_dates),
            "validation_path": "data/raw/v5p/probalytics/full_history_20260601_20260831/validation.json",
            "validation_sha256": filehash(market_validation_path),
        },
        "joins": {
            "weather_market_overlap_dates": len(weather_dates & market_dates),
            "market_dates_without_weather": sorted(market_dates - weather_dates),
            "exposed_development_dates": len(development_dates),
            "development_dates_with_exact_weather": len(development_dates & weather_dates),
            "development_strict_ready_dates_at_5s": len(development_dates & strict_dates),
        },
        "ready_for_exact_development_test": True,
        "independent_confirmation_ready": False,
        "independent_confirmation_reason": "The 64-date cohort is previously exposed development history; the protected confirmation labels were not opened.",
        "protected_confirmation_labels_read": False,
        "live_or_current_feed_used": False,
        "actual_orders_placed": False,
    })
    output = project / "data/manifests/friend_method_exact_data_readiness.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
