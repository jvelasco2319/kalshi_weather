"""Cache-only rolling V5P weather normalization and probability readiness.

This module has no HTTP client and never opens Kalshi raw JSON or CLILAX
product text.  It consumes only completed weather dates from the hash-bound
V5P recovery state, safe Kalshi coverage manifests, and the CLILAX archive
envelope.  Confirmation outcomes remain sealed.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from klax_lab.provenance import sha256_file, write_json
from klax_lab.weather_decode_v3 import (
    KLAX_POINT,
    SpatialPoint,
    _decode_fragment,
    _http_timestamp,
    _utc_iso,
    _write_parquet_atomic,
)
from klax_lab.weather_sources_v3 import (
    HistoricalWeatherPlan,
    _safe_cache_path,
    _verified_cache_file,
    select_index_ranges,
)

from .acquire_probability_evidence import (
    DEFAULT_RUN_ID,
    END,
    START,
    _canonical_hash,
    _load_object,
    _verify_hash_bound,
    build_v5_daily_weather_plans,
)
from .probability import validate_frozen_leader


VERSION = "klax-v5p-probability-cache-readiness-v1"
PARTITION = "v5p_confirmation_features"
POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"
AVAILABILITY_DELAY = timedelta(hours=6)
DECISION_HOURS_UTC = (12, 15, 18)
OUTPUT_ROOT = Path("data/normalized/v5p_probability_features")
PROGRESS_PATH = Path("data/manifests/v5p_probability_cache_readiness.json")
ACQUISITION_ROOT = Path("runs/v5p_acquisition")


class V5PCacheReadinessError(ValueError):
    pass


def _safe_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    relative = resolved.relative_to(root.resolve())
    forbidden = {"labels", "outcomes", "protected_final", "settlement_targets"}
    if forbidden & {part.casefold().replace("-", "_") for part in relative.parts}:
        raise V5PCacheReadinessError("cache readiness cannot access protected outcome storage")
    return relative.as_posix()


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V5PCacheReadinessError("availability timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise V5PCacheReadinessError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V5PCacheReadinessError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V5PCacheReadinessError(f"{field} must include an offset")
    return parsed.astimezone(UTC)


def _v5p_date(value: Any) -> date:
    try:
        result = date.fromisoformat(str(value))
    except ValueError as exc:
        raise V5PCacheReadinessError("invalid V5P climate date") from exc
    if not START <= result <= END:
        raise V5PCacheReadinessError("date is outside the frozen V5P window")
    return result


def _decode_plan(plan: HistoricalWeatherPlan, cache_root: Path,
                 points: Iterable[SpatialPoint] = (KLAX_POINT,)) -> list[dict[str, Any]]:
    """V5-specific copy of the strict decoder loop with a feature-only partition."""
    point_values = tuple(points)
    if not point_values or len({point.point_id for point in point_values}) != len(point_values):
        raise V5PCacheReadinessError("unique extraction points are required")
    rows: list[dict[str, Any]] = []
    for item in plan.objects:
        target = _v5p_date(item.initialized_at.date())
        folder = _safe_cache_path(Path(cache_root).resolve(), item.cache_key)
        index_path = _safe_cache_path(folder, "source.idx")
        index_meta = _verified_cache_file(
            index_path, url=item.index_url, start=None, end=None,
            maximum_bytes=item.index_max_bytes, require_grib=False,
        )
        index_retrieved = _parse_utc(index_meta["retrieved_at_utc"], "index retrieval time")
        index_modified = _http_timestamp(index_meta["last_modified"], "Index Last-Modified")
        if index_modified is not None and index_modified > index_retrieved:
            raise V5PCacheReadinessError("index Last-Modified follows retrieval")
        ranges = select_index_ranges(index_path.read_text(encoding="utf-8"), item)
        selectors = {field.field_id: field for field in item.fields}
        for selected in ranges:
            field_path = _safe_cache_path(folder, selected.field_id + ".grib2")
            field_meta = _verified_cache_file(
                field_path, url=item.url, start=selected.start, end=selected.end,
                maximum_bytes=selectors[selected.field_id].maximum_bytes,
                require_grib=True,
            )
            decoded, point_rows = _decode_fragment(
                field_path, item=item, field_id=selected.field_id,
                target_date=target, points=point_values,
            )
            retrieved = _parse_utc(field_meta["retrieved_at_utc"], "field retrieval time")
            modified = _http_timestamp(field_meta["last_modified"], "Last-Modified")
            if modified is not None and modified > retrieved:
                raise V5PCacheReadinessError("field Last-Modified follows retrieval")
            if retrieved < decoded["forecast_reference_time_utc"]:
                raise V5PCacheReadinessError("retrieval precedes model cycle")
            if modified is not None and modified < decoded["forecast_reference_time_utc"]:
                raise V5PCacheReadinessError("Last-Modified precedes model cycle")
            common = {
                "schema_version": 1, "climate_date": target.isoformat(),
                "partition": PARTITION, "provider": item.provider, "model": item.model,
                "source_id": item.source_id, "member_id": item.member_id,
                "field_id": selected.field_id,
                "forecast_reference_time_utc": _utc_iso(decoded["forecast_reference_time_utc"]),
                "nominal_issue_time_utc": _utc_iso(decoded["forecast_reference_time_utc"]),
                "issue_time_basis": "GRIB_dataDate_dataTime_nominal_model_cycle",
                "cycle_hour_utc": decoded["forecast_reference_time_utc"].hour,
                "lead_hours": item.lead_hours,
                "valid_time_utc": _utc_iso(decoded["valid_time_utc"]),
                "ensemble_statistic": decoded["ensemble_statistic"],
                "ensemble_size": decoded["ensemble_size"],
                "derived_forecast_code": decoded["derived_forecast_code"],
                "parameter_name": decoded["parameter_name"], "short_name": decoded["short_name"],
                "level_type": decoded["level_type"], "level": decoded["level"],
                "grid_type": decoded["grid_type"],
                "number_of_grid_points": decoded["number_of_points"],
                "centre": decoded["centre"], "sub_centre": decoded["sub_centre"],
                "generating_process_identifier": decoded["generating_process_identifier"],
                "generating_process_type": decoded["generating_process_type"],
                "product_template": decoded["product_template"],
                "packing_type": decoded["packing_type"],
                "bitmap_present": decoded["bitmap_present"],
                "extraction_method": "ecCodes_geodesic_nearest_grid_point_no_interpolation",
                "source_path": field_path.as_posix(), "source_url": item.url,
                "source_bytes": field_meta["bytes"], "source_sha256": field_meta["sha256"],
                "source_range_start": selected.start, "source_range_end": selected.end,
                "source_etag": field_meta["etag"],
                "source_retrieved_at_utc": _iso(retrieved),
                "source_last_modified_at_utc": _iso(modified) if modified else None,
                "information_available_at_utc": _iso(modified) if modified else None,
                "information_availability_basis": (
                    "archived_HTTP_Last-Modified_unverified_as_original_publication_time"
                    if modified else "unavailable"
                ),
                "historical_availability_proven": False, "as_of_validated": False,
                "index_path": index_path.as_posix(), "index_url": item.index_url,
                "index_sha256": index_meta["sha256"],
                "index_retrieved_at_utc": _iso(index_retrieved),
                "index_last_modified_at_utc": _iso(index_modified) if index_modified else None,
                "index_line": selected.line,
            }
            rows.extend({**common, **point} for point in point_rows)
    expected = sum(len(item.fields) for item in plan.objects) * len(point_values)
    if len(rows) != expected:
        raise V5PCacheReadinessError("decoded row count differs from frozen plan")
    return rows


def apply_frozen_availability(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        target = _v5p_date(row.get("climate_date"))
        if row.get("model") not in {"hrrr", "gefs"} or row.get("partition") != PARTITION:
            raise V5PCacheReadinessError("weather row identity differs")
        if row.get("historical_availability_proven") is not False or row.get("as_of_validated") is not False:
            raise V5PCacheReadinessError("decoder timing flags differ")
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        reference = _parse_utc(row.get("forecast_reference_time_utc"), "reference time")
        if nominal != reference:
            raise V5PCacheReadinessError("nominal and decoded model cycles differ")
        archived = row.get("source_last_modified_at_utc")
        archived_time = _parse_utc(archived, "Last-Modified") if archived else None
        available = max(value for value in (nominal + AVAILABILITY_DELAY, archived_time) if value)
        eligible = [
            f"{hour:02d}:00" for hour in DECISION_HOURS_UTC
            if available <= datetime.combine(target, time(hour), UTC)
        ]
        if eligible != ["12:00", "15:00", "18:00"]:
            raise V5PCacheReadinessError("row fails the frozen decision-time availability policy")
        row.update({
            "availability_policy_id": POLICY_ID, "availability_delay_hours": 6,
            "effective_information_available_at_utc": _iso(available),
            "effective_information_availability_basis": (
                "max_of_nominal_model_cycle_plus_6_hours_and_archived_HTTP_Last-Modified_if_present"
            ),
            "eligible_decision_times_utc": eligible,
            "historical_availability_proven": False, "as_of_validated": True,
            "as_of_validation_basis": "frozen_conservative_bound_not_publication_proof",
            "contains_settlement_label": False,
        })
        audited.append(row)
    if not audited:
        raise V5PCacheReadinessError("availability audit requires weather rows")
    return audited


def _coverage(rows: list[dict[str, Any]], model: str, target: date) -> dict[str, Any]:
    expected = (
        {(lead, None, field, "KLAX") for lead in (2, 8, 14, 20)
         for field in ("temperature_2m", "total_cloud_cover", "cloud_ceiling",
                       "wind_u_10m", "wind_v_10m", "mean_sea_level_pressure")}
        if model == "hrrr" else
        {(lead, member, "temperature_2m", "KLAX")
         for lead in (9, 12, 15, 18, 21, 24, 27, 30) for member in ("avg", "spr")}
    )
    actual = {(row["lead_hours"], row["member_id"], row["field_id"], row["point_id"])
              for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise V5PCacheReadinessError(f"{model} normalized coverage differs")
    if any(row["model"] != model or row["climate_date"] != target.isoformat()
           or row["partition"] != PARTITION or row["availability_policy_id"] != POLICY_ID
           or row["as_of_validated"] is not True or row["contains_settlement_label"] is not False
           for row in rows):
        raise V5PCacheReadinessError(f"{model} row boundary differs")
    return {"model": model, "rows": len(rows), "complete": True}


def _day_paths(root: Path, target: date) -> tuple[Path, Path, Path]:
    folder = root / OUTPUT_ROOT / f"date={target.isoformat()}"
    return folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet", folder / "manifest.json"


def _normalized_day(root: Path, target: date) -> dict[str, Any]:
    h_path, g_path, manifest_path = _day_paths(root, target)
    if manifest_path.is_file() and h_path.is_file() and g_path.is_file():
        manifest = _load_object(manifest_path)
        for record, path in zip(manifest.get("outputs", []), (h_path, g_path), strict=True):
            if record.get("sha256") != sha256_file(path) or record.get("bytes") != path.stat().st_size:
                raise V5PCacheReadinessError("normalized output hash differs")
        return manifest
    h_plan, g_plan = build_v5_daily_weather_plans(target)
    cache = root / "data/raw/v5p/weather"
    h_rows = apply_frozen_availability(_decode_plan(h_plan, cache))
    g_rows = apply_frozen_availability(_decode_plan(g_plan, cache))
    h_cover, g_cover = _coverage(h_rows, "hrrr", target), _coverage(g_rows, "gefs", target)
    h_path.parent.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(h_path, h_rows)
    _write_parquet_atomic(g_path, g_rows)
    outputs = [
        {"model": "hrrr", "path": _safe_relative(root, h_path), "rows": len(h_rows),
         "bytes": h_path.stat().st_size, "sha256": sha256_file(h_path)},
        {"model": "gefs", "path": _safe_relative(root, g_path), "rows": len(g_rows),
         "bytes": g_path.stat().st_size, "sha256": sha256_file(g_path)},
    ]
    manifest = {
        "version": VERSION, "status": "NORMALIZED_FEATURES_ONLY",
        "climate_date": target.isoformat(), "partition": PARTITION,
        "availability_policy_id": POLICY_ID, "decision_time_utc": "18:00",
        "coverage": {"hrrr": h_cover, "gefs": g_cover}, "outputs": outputs,
        "network_used": False, "refit_performed": False,
        "protected_confirmation_labels_read": False, "actual_orders_placed": False,
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    write_json(manifest_path, manifest)
    return manifest


def _safe_manifest(root: Path, path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not path.is_file():
        return None, None
    _safe_relative(root, path)
    value = _load_object(path)
    return value, {"path": _safe_relative(root, path), "bytes": path.stat().st_size,
                   "sha256": sha256_file(path)}


def _fixed_fold(target: date) -> int:
    boundaries = (date(2025, 9, 24), date(2025, 12, 19), date(2026, 3, 14), date(2026, 6, 7), END)
    return next(index for index, end in enumerate(boundaries, 1) if target <= end)


def run_cache_only(root: Path, run_id: str = DEFAULT_RUN_ID, *, normalize: bool = True) -> dict[str, Any]:
    root = Path(root).resolve()
    leader = validate_frozen_leader(root)
    recovery_path = root / ACQUISITION_ROOT / run_id / "recovery-state.json"
    recovery, recovery_record = _safe_manifest(root, recovery_path)
    if recovery is None:
        completed, unavailable, acquisition_status = set(), set(), "NOT_STARTED"
    else:
        _verify_hash_bound(recovery, "recovery_sha256")
        completed = {_v5p_date(value) for value in recovery.get("weather", {}).get("completed_dates", [])}
        unavailable = {_v5p_date(value) for value in recovery.get("weather", {}).get("unavailable_dates", [])}
        acquisition_status = str(recovery.get("status"))

    normalized: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    if normalize:
        for target in sorted(completed):
            try:
                normalized.append(_normalized_day(root, target))
            except Exception as exc:
                failures.append({"date": target.isoformat(), "error": type(exc).__name__, "message": str(exc)})
    else:
        for target in sorted(completed):
            _, _, manifest_path = _day_paths(root, target)
            if manifest_path.is_file():
                normalized.append(_load_object(manifest_path))

    normalized_dates = {date.fromisoformat(item["climate_date"]) for item in normalized}
    kalshi_base = root / "data/raw/v5p/kalshi_workspace/data/manifests"
    coverage, coverage_record = _safe_manifest(root, kalshi_base / "kalshi_coverage.json")
    metadata_dates = {
        _v5p_date(row["climate_date"]) for row in (coverage or {}).get("contracts", [])
        if isinstance(row, dict) and row.get("station_identity_screen") is True
        and isinstance(row.get("climate_date"), str)
    }
    candle_dates, trade_dates, market_records = set(), set(), []
    for pattern, destination in (("kalshi_candles_1m_*.json", candle_dates), ("kalshi_trades_*.json", trade_dates)):
        for path in sorted(kalshi_base.glob(pattern)):
            value, record = _safe_manifest(root, path)
            if value is None or value.get("status") != "complete":
                continue
            market_records.append(record)
            destination.update(
                _v5p_date(row["climate_date"]) for row in value.get("contracts", [])
                if isinstance(row, dict) and isinstance(row.get("climate_date"), str)
                and int(row.get("rows", 0)) >= 0
            )
    climate_base = root / "data/raw/v5p/climate_workspace/data/manifests"
    climate_files = sorted(climate_base.glob("climate_*.json"))
    climate, climate_record = _safe_manifest(root, climate_files[-1]) if climate_files else (None, None)
    climate_dates: set[date] = set()
    if climate:
        first = max(START, date.fromisoformat(climate["start_inclusive"]))
        last = min(END + timedelta(days=1), date.fromisoformat(climate["end_exclusive"]))
        climate_dates = {first + timedelta(days=i) for i in range(max(0, (last - first).days))}

    score_ready_dates = normalized_dates & metadata_dates & climate_dates
    folds = {str(index): 0 for index in range(1, 6)}
    for target in score_ready_dates:
        folds[str(_fixed_fold(target))] += 1
    gates = {
        "minimum_120_score_ready_days": len(score_ready_dates) >= 120,
        "minimum_20_score_ready_days_each_fold": all(value >= 20 for value in folds.values()),
        "normalization_integrity": not failures,
        "frozen_leader_identity_valid": leader.get("known_hash_validation_passed") is True,
        "confirmation_labels_sealed": True,
    }
    promotion_ready = all(gates.values())
    result = {
        "version": VERSION, "run_id": run_id,
        "status": "PROBABILITY_INPUTS_READY" if promotion_ready else "PARTIAL_CACHE_ONLY_READINESS",
        "promotion_ready": promotion_ready, "acquisition_status": acquisition_status,
        "frozen_leader_validation_sha256": leader["validation_sha256"],
        "recovery_state": recovery_record,
        "weather": {
            "acquisition_complete_days": len(completed), "unavailable_days": len(unavailable),
            "normalized_feature_days": len(normalized_dates), "normalization_failures": failures,
            "pending_calendar_days": 427 - len(completed) - len(unavailable),
            "availability_policy_id": POLICY_ID, "decision_time_utc": "18:00",
        },
        "safe_source_coverage": {
            "kalshi_metadata_days": len(metadata_dates), "one_minute_candle_days": len(candle_dates),
            "public_trade_days": len(trade_dates), "clilax_archive_envelope_days": len(climate_dates),
            "score_ready_days": len(score_ready_dates), "score_ready_days_per_fold": folds,
        },
        "safe_manifest_bindings": {
            "kalshi_coverage": coverage_record, "market_batches": market_records,
            "clilax_envelope": climate_record,
        },
        "gates": gates,
        "network_used": False, "refit_performed": False,
        "raw_kalshi_responses_read": False, "clilax_product_text_read": False,
        "protected_confirmation_labels_read": False, "actual_orders_placed": False,
    }
    result["readiness_sha256"] = _canonical_hash(result)
    return result


def write_cache_only(root: Path, run_id: str = DEFAULT_RUN_ID) -> Path:
    root = Path(root).resolve()
    result = run_cache_only(root, run_id, normalize=True)
    path = root / PROGRESS_PATH
    write_json(path, result)
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    args = parser.parse_args(argv)
    result = (run_cache_only(args.project_root, args.run_id, normalize=False)
              if args.command == "status" else str(write_cache_only(args.project_root, args.run_id)))
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
