"""Verify the complete offline weather archive for the friend-method test."""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path


UTC = timezone.utc
EXPECTED_LEADS = {
    "gfs": (12, 18, 24, 30, 36),
    "nam": tuple(range(9, 31, 3)),
    "nbm": tuple(range(7, 32)),
}
PRIMARY_GFS = (18, 24, 30)
NBM_KALSHI = tuple(range(8, 32))
NBM_FIXTURE = tuple(range(7, 31))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AssertionError("timezone missing")
    return parsed.astimezone(UTC)


def sealed_hash(value: dict) -> str:
    payload = {key: item for key, item in value.items() if key != "self_sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify(project: Path) -> dict:
    root = project / "data/raw/friend_method_weather_v1"
    manifest_path = root / "manifest.json"
    manifest = read_json(manifest_path)
    assert manifest["schema"] == "friend-method-historical-weather-manifest-v1"
    assert manifest["self_sha256"] == sealed_hash(manifest)
    assert manifest["date_start"] == "2026-06-01"
    assert manifest["date_end"] == "2026-08-31"
    assert manifest["calendar_days"] == manifest["complete_days"] == 92
    assert manifest["failed_tasks"] == {}
    assert manifest["models_requested"] == ["gfs", "gfs_seamless", "nam", "nbm"]
    assert manifest["effective_source_groups"] == [["gfs", "gfs_seamless"], ["nam"], ["nbm"]]
    assert not manifest["live_or_current_feed_used"]
    assert not manifest["protected_confirmation_labels_read"]
    assert not manifest["actual_orders_placed"]

    expected_dates = []
    cursor = date(2026, 6, 1)
    while cursor <= date(2026, 8, 31):
        expected_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert [row["climate_date"] for row in manifest["days"]] == expected_dates

    point_count = 0
    source_bytes = 0
    temperature_min = math.inf
    temperature_max = -math.inf
    provider_counts: dict[str, int] = {}
    for day_row, day_text in zip(manifest["days"], expected_dates):
        day = date.fromisoformat(day_text)
        daily_path = root / f"date={day_text}" / "daily.json"
        assert day_row["complete"] and day_row["missing_task_count"] == 0
        assert day_row["sha256"] == sha256(daily_path)
        daily = read_json(daily_path)
        assert daily["complete"] and daily["missing_tasks"] == []
        assert daily["climate_date"] == day_text
        assert parse_utc(daily["decision_time_utc"]) == datetime.combine(day, time(18), UTC)
        assert not daily["contains_settlement_label"]
        assert not daily["contains_market_price"]
        assert not daily["actual_orders_placed"]
        assert set(daily["models"]) == {"gfs", "gfs_seamless", "nam", "nbm"}
        assert daily["models"]["gfs_seamless"]["duplicate_of"] == "gfs"
        assert daily["models"]["gfs_seamless"]["daily_high_f"] == daily["models"]["gfs"]["daily_high_f"]

        model_values: dict[str, list[dict]] = {}
        for model, leads in EXPECTED_LEADS.items():
            rows = []
            for lead in leads:
                folder = root / f"date={day_text}" / f"model={model}" / f"lead={lead:03d}"
                point_path = folder / "point.json"
                point = read_json(point_path)
                rows.append(point)
                point_count += 1
                assert point["model"] == model and point["climate_date"] == day_text
                assert point["lead_hours"] == lead
                initialized = datetime.combine(day, time(0), UTC)
                assert parse_utc(point["cycle_time_utc"]) == initialized
                assert parse_utc(point["valid_time_utc"]) == initialized + timedelta(hours=lead)
                assert parse_utc(point["decision_time_utc"]) == datetime.combine(day, time(18), UTC)
                value = float(point["temperature_f"])
                assert math.isfinite(value) and -100 < value < 160
                temperature_min = min(temperature_min, value)
                temperature_max = max(temperature_max, value)
                assert point["as_of_decision_safe"] is True
                provider = point["source_provider"]
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
                source_bytes += int(point["source_bytes"])
                if model in {"gfs", "nbm"}:
                    suffix = "tmax.grib2" if model == "gfs" else "tmp.grib2"
                    raw_path = folder / suffix
                    index_path = folder / "source.idx"
                    assert raw_path.stat().st_size == point["source_bytes"]
                    assert sha256(raw_path) == point["source_sha256"]
                    assert sha256(index_path) == point["index_sha256"]
                    assert point["source_range_end"] - point["source_range_start"] + 1 == point["source_bytes"]
                    with raw_path.open("rb") as stream:
                        assert stream.read(4) == b"GRIB"
                        stream.seek(-4, 2)
                        assert stream.read(4) == b"7777"
                else:
                    raw_path = folder / "point.csv"
                    assert raw_path.stat().st_size == point["source_bytes"]
                    assert sha256(raw_path) == point["source_sha256"]
            assert [row["lead_hours"] for row in rows] == list(leads)
            model_values[model] = rows
        gfs_primary = [row["temperature_f"] for row in model_values["gfs"] if row["lead_hours"] in PRIMARY_GFS]
        assert math.isclose(daily["models"]["gfs"]["daily_high_f"], max(gfs_primary), abs_tol=1e-12)
        assert math.isclose(
            daily["models"]["nam"]["daily_high_f"],
            max(row["temperature_f"] for row in model_values["nam"]),
            abs_tol=1e-12,
        )
        assert math.isclose(
            daily["models"]["nbm"]["daily_high_f"],
            max(row["temperature_f"] for row in model_values["nbm"] if row["lead_hours"] in NBM_KALSHI),
            abs_tol=1e-12,
        )
        assert math.isclose(
            daily["models"]["nbm"]["fixture_compatible_daily_high_f"],
            max(row["temperature_f"] for row in model_values["nbm"] if row["lead_hours"] in NBM_FIXTURE),
            abs_tol=1e-12,
        )

    expected_point_count = 92 * sum(len(leads) for leads in EXPECTED_LEADS.values())
    assert point_count == expected_point_count == 3496
    verification = {
        "schema": "friend-method-historical-weather-verification-v1",
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "manifest_path": "data/raw/friend_method_weather_v1/manifest.json",
        "manifest_file_sha256": sha256(manifest_path),
        "manifest_self_sha256": manifest["self_sha256"],
        "calendar_days": 92,
        "complete_days": 92,
        "point_records": point_count,
        "source_bytes_referenced": source_bytes,
        "provider_counts": provider_counts,
        "temperature_range_f": [temperature_min, temperature_max],
        "gfs_seamless_duplicate_policy_verified": True,
        "chronology_verified": True,
        "raw_hashes_verified": True,
        "no_market_or_outcome_data_in_weather_archive": True,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    verification["self_sha256"] = sealed_hash(verification)
    output = project / "data/manifests/friend_method_weather_v1_verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verification


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    result = verify(Path(args.project_root).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
