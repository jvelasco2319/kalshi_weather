"""Cache-only normalization for the V5B untouched May/September dates."""

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

from .weather_acquisition import (
    CACHE_PATH,
    PLAN_PATH,
    STATE_PATH,
    build_daily_plans,
    registered_dates,
)


VERSION = "v5b-untouched-weather-normalization-v1"
PARTITION = "v5b_untouched_features"
POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"
AVAILABILITY_DELAY = timedelta(hours=6)
DECISION_HOURS_UTC = (12, 15, 18)
OUTPUT_ROOT = Path("data/normalized/v5p_probability_features")
PROGRESS_PATH = Path("data/manifests/v5b_untouched_weather_normalization.json")


class ConfirmationNormalizationError(ValueError):
    pass


def _canonical_hash(value: Mapping[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _safe_relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    relative = resolved.relative_to(root.resolve())
    forbidden = {"labels", "outcomes", "protected_final", "settlement_targets"}
    if forbidden & {part.casefold().replace("-", "_") for part in relative.parts}:
        raise ConfirmationNormalizationError("normalization cannot access outcome storage")
    return relative.as_posix()


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfirmationNormalizationError("availability timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ConfirmationNormalizationError(f"{field} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConfirmationNormalizationError(f"{field} must include an offset")
    return parsed.astimezone(UTC)


def _confirmation_date(value: Any, allowed: set[date]) -> date:
    try:
        result = date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfirmationNormalizationError("invalid untouched climate date") from exc
    if result not in allowed:
        raise ConfirmationNormalizationError("date is outside the registered untouched windows")
    return result


def _decode_plan(
    plan: HistoricalWeatherPlan,
    cache_root: Path,
    allowed: set[date],
    points: Iterable[SpatialPoint] = (KLAX_POINT,),
) -> list[dict[str, Any]]:
    point_values = tuple(points)
    if not point_values or len({point.point_id for point in point_values}) != len(point_values):
        raise ConfirmationNormalizationError("unique extraction points are required")
    rows: list[dict[str, Any]] = []
    for item in plan.objects:
        target = _confirmation_date(item.initialized_at.date(), allowed)
        folder = _safe_cache_path(Path(cache_root).resolve(), item.cache_key)
        index_path = _safe_cache_path(folder, "source.idx")
        index_meta = _verified_cache_file(
            index_path,
            url=item.index_url,
            start=None,
            end=None,
            maximum_bytes=item.index_max_bytes,
            require_grib=False,
        )
        index_retrieved = _parse_utc(index_meta["retrieved_at_utc"], "index retrieval time")
        index_modified = _http_timestamp(index_meta["last_modified"], "Index Last-Modified")
        if index_modified is not None and index_modified > index_retrieved:
            raise ConfirmationNormalizationError("index Last-Modified follows retrieval")
        ranges = select_index_ranges(index_path.read_text(encoding="utf-8"), item)
        selectors = {field.field_id: field for field in item.fields}
        for selected in ranges:
            field_path = _safe_cache_path(folder, selected.field_id + ".grib2")
            field_meta = _verified_cache_file(
                field_path,
                url=item.url,
                start=selected.start,
                end=selected.end,
                maximum_bytes=selectors[selected.field_id].maximum_bytes,
                require_grib=True,
            )
            decoded, point_rows = _decode_fragment(
                field_path,
                item=item,
                field_id=selected.field_id,
                target_date=target,
                points=point_values,
            )
            retrieved = _parse_utc(field_meta["retrieved_at_utc"], "field retrieval time")
            modified = _http_timestamp(field_meta["last_modified"], "Last-Modified")
            if modified is not None and modified > retrieved:
                raise ConfirmationNormalizationError("field Last-Modified follows retrieval")
            if retrieved < decoded["forecast_reference_time_utc"]:
                raise ConfirmationNormalizationError("retrieval precedes model cycle")
            if modified is not None and modified < decoded["forecast_reference_time_utc"]:
                raise ConfirmationNormalizationError("Last-Modified precedes model cycle")
            common = {
                "schema_version": 1,
                "climate_date": target.isoformat(),
                "partition": PARTITION,
                "provider": item.provider,
                "model": item.model,
                "source_id": item.source_id,
                "member_id": item.member_id,
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
                "parameter_name": decoded["parameter_name"],
                "short_name": decoded["short_name"],
                "level_type": decoded["level_type"],
                "level": decoded["level"],
                "grid_type": decoded["grid_type"],
                "number_of_grid_points": decoded["number_of_points"],
                "centre": decoded["centre"],
                "sub_centre": decoded["sub_centre"],
                "generating_process_identifier": decoded["generating_process_identifier"],
                "generating_process_type": decoded["generating_process_type"],
                "product_template": decoded["product_template"],
                "packing_type": decoded["packing_type"],
                "bitmap_present": decoded["bitmap_present"],
                "extraction_method": "ecCodes_geodesic_nearest_grid_point_no_interpolation",
                "source_path": field_path.as_posix(),
                "source_url": item.url,
                "source_bytes": field_meta["bytes"],
                "source_sha256": field_meta["sha256"],
                "source_range_start": selected.start,
                "source_range_end": selected.end,
                "source_etag": field_meta["etag"],
                "source_retrieved_at_utc": _iso(retrieved),
                "source_last_modified_at_utc": _iso(modified) if modified else None,
                "information_available_at_utc": _iso(modified) if modified else None,
                "information_availability_basis": (
                    "archived_HTTP_Last-Modified_unverified_as_original_publication_time"
                    if modified
                    else "unavailable"
                ),
                "historical_availability_proven": False,
                "as_of_validated": False,
                "index_path": index_path.as_posix(),
                "index_url": item.index_url,
                "index_sha256": index_meta["sha256"],
                "index_retrieved_at_utc": _iso(index_retrieved),
                "index_last_modified_at_utc": _iso(index_modified) if index_modified else None,
                "index_line": selected.line,
            }
            rows.extend({**common, **point} for point in point_rows)
    expected = sum(len(item.fields) for item in plan.objects) * len(point_values)
    if len(rows) != expected:
        raise ConfirmationNormalizationError("decoded row count differs from frozen plan")
    return rows


def apply_frozen_availability(
    rows: Iterable[Mapping[str, Any]], allowed: set[date]
) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        target = _confirmation_date(row.get("climate_date"), allowed)
        if row.get("model") not in {"hrrr", "gefs"} or row.get("partition") != PARTITION:
            raise ConfirmationNormalizationError("weather row identity differs")
        if row.get("historical_availability_proven") is not False or row.get("as_of_validated") is not False:
            raise ConfirmationNormalizationError("decoder timing flags differ")
        nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
        reference = _parse_utc(row.get("forecast_reference_time_utc"), "reference time")
        if nominal != reference:
            raise ConfirmationNormalizationError("nominal and decoded model cycles differ")
        archived_raw = row.get("source_last_modified_at_utc")
        archived = _parse_utc(archived_raw, "Last-Modified") if archived_raw else None
        available = max(value for value in (nominal + AVAILABILITY_DELAY, archived) if value)
        eligible = [
            f"{hour:02d}:00"
            for hour in DECISION_HOURS_UTC
            if available <= datetime.combine(target, time(hour), UTC)
        ]
        if eligible != ["12:00", "15:00", "18:00"]:
            raise ConfirmationNormalizationError("row fails the frozen availability policy")
        row.update(
            {
                "availability_policy_id": POLICY_ID,
                "availability_delay_hours": 6,
                "effective_information_available_at_utc": _iso(available),
                "effective_information_availability_basis": "max_of_nominal_model_cycle_plus_6_hours_and_archived_HTTP_Last-Modified_if_present",
                "eligible_decision_times_utc": eligible,
                "historical_availability_proven": False,
                "as_of_validated": True,
                "as_of_validation_basis": "frozen_conservative_bound_not_publication_proof",
                "contains_settlement_label": False,
            }
        )
        audited.append(row)
    if not audited:
        raise ConfirmationNormalizationError("availability audit requires weather rows")
    return audited


def _coverage(rows: list[dict[str, Any]], model: str, target: date) -> dict[str, Any]:
    expected = (
        {
            (lead, None, field, "KLAX")
            for lead in (2, 8, 14, 20)
            for field in (
                "temperature_2m",
                "total_cloud_cover",
                "cloud_ceiling",
                "wind_u_10m",
                "wind_v_10m",
                "mean_sea_level_pressure",
            )
        }
        if model == "hrrr"
        else {
            (lead, member, "temperature_2m", "KLAX")
            for lead in (9, 12, 15, 18, 21, 24, 27, 30)
            for member in ("avg", "spr")
        }
    )
    actual = {
        (row["lead_hours"], row["member_id"], row["field_id"], row["point_id"])
        for row in rows
    }
    if actual != expected or len(rows) != len(expected):
        raise ConfirmationNormalizationError(f"{model} normalized coverage differs")
    if any(
        row["model"] != model
        or row["climate_date"] != target.isoformat()
        or row["partition"] != PARTITION
        or row["availability_policy_id"] != POLICY_ID
        or row["as_of_validated"] is not True
        or row["contains_settlement_label"] is not False
        for row in rows
    ):
        raise ConfirmationNormalizationError(f"{model} row boundary differs")
    return {"model": model, "rows": len(rows), "complete": True}


def _day_paths(root: Path, target: date) -> tuple[Path, Path, Path]:
    folder = root / OUTPUT_ROOT / f"date={target.isoformat()}"
    return (
        folder / "hrrr_points.parquet",
        folder / "gefs_summary_points.parquet",
        folder / "manifest.json",
    )


def normalize_day(root: Path, target: date, allowed: set[date]) -> dict[str, Any]:
    h_path, g_path, manifest_path = _day_paths(root, target)
    if manifest_path.is_file() and h_path.is_file() and g_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("manifest_sha256") != _canonical_hash(manifest, "manifest_sha256"):
            raise ConfirmationNormalizationError("normalized manifest hash differs")
        for record, path in zip(manifest.get("outputs", []), (h_path, g_path), strict=True):
            if record.get("sha256") != sha256_file(path) or record.get("bytes") != path.stat().st_size:
                raise ConfirmationNormalizationError("normalized output hash differs")
        return manifest

    h_plan, g_plan = build_daily_plans(target)
    cache = root / CACHE_PATH
    h_rows = apply_frozen_availability(_decode_plan(h_plan, cache, allowed), allowed)
    g_rows = apply_frozen_availability(_decode_plan(g_plan, cache, allowed), allowed)
    h_cover = _coverage(h_rows, "hrrr", target)
    g_cover = _coverage(g_rows, "gefs", target)
    h_path.parent.mkdir(parents=True, exist_ok=True)
    _write_parquet_atomic(h_path, h_rows)
    _write_parquet_atomic(g_path, g_rows)
    outputs = [
        {
            "model": "hrrr",
            "path": _safe_relative(root, h_path),
            "rows": len(h_rows),
            "bytes": h_path.stat().st_size,
            "sha256": sha256_file(h_path),
        },
        {
            "model": "gefs",
            "path": _safe_relative(root, g_path),
            "rows": len(g_rows),
            "bytes": g_path.stat().st_size,
            "sha256": sha256_file(g_path),
        },
    ]
    manifest = {
        "version": VERSION,
        "status": "NORMALIZED_FEATURES_ONLY",
        "climate_date": target.isoformat(),
        "partition": PARTITION,
        "availability_policy_id": POLICY_ID,
        "decision_time_utc": "18:00",
        "coverage": {"hrrr": h_cover, "gefs": g_cover},
        "outputs": outputs,
        "network_used": False,
        "refit_performed": False,
        "protected_confirmation_labels_read": False,
        "contains_settlement_labels": False,
        "actual_orders_placed": False,
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest, "manifest_sha256")
    write_json(manifest_path, manifest)
    return manifest


def run(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    allowed_list = registered_dates(workspace)
    allowed = set(allowed_list)
    state_path = workspace / STATE_PATH
    state = json.loads(state_path.read_text(encoding="utf-8"))
    completed = {date.fromisoformat(value) for value in state.get("completed_dates", [])}
    unavailable = {date.fromisoformat(value) for value in state.get("unavailable_dates", [])}
    if not completed <= allowed or not unavailable <= allowed:
        raise ConfirmationNormalizationError("acquisition state includes an unregistered date")

    normalized: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for target in sorted(completed):
        try:
            normalized.append(normalize_day(workspace, target, allowed))
        except Exception as exc:
            failures.append(
                {"date": target.isoformat(), "error": type(exc).__name__, "message": str(exc)}
            )
    normalized_dates = sorted(item["climate_date"] for item in normalized)
    body = {
        "schema_version": VERSION,
        "status": (
            "COMPLETE"
            if state.get("status") == "COMPLETE"
            and len(normalized_dates) + len(unavailable) == len(allowed)
            and not failures
            else "PARTIAL"
        ),
        "plan_path": PLAN_PATH.as_posix(),
        "plan_sha256": sha256_file(workspace / PLAN_PATH),
        "acquisition_state_path": STATE_PATH.as_posix(),
        "acquisition_state_sha256": sha256_file(state_path),
        "registered_date_count": len(allowed),
        "acquisition_completed_date_count": len(completed),
        "acquisition_unavailable_dates": sorted(day.isoformat() for day in unavailable),
        "normalized_date_count": len(normalized_dates),
        "normalized_dates": normalized_dates,
        "normalization_failures": failures,
        "network_used": False,
        "refit_performed": False,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body, "self_sha256")
    output = workspace / PROGRESS_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, body)
    return body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    print(json.dumps(run(args.project_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
