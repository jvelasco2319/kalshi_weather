"""Freeze, then separately score, the fixed HRRR/GEFS model over calendar 2025.

The freeze action is outcome blind and remains unavailable until all 184 new
weather dates pass raw-cache and normalized-feature integrity.  The score
action is the only path that opens the archived CLILAX labels.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable
import zipfile

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from klax_lab.domain import ContractBounds
from scripts.acquire_v7y_weather_history import STATE as BACKFILL_STATE
from scripts.acquire_v7y_weather_history import file_hash, load_state
from scripts.audit_v7y_weather_history import AUDIT as ROLLING_AUDIT
from scripts.audit_v7y_weather_history import SCHEMA as AUDIT_SCHEMA
from scripts.audit_v7y_weather_history import policy_registration_binding
from scripts.audit_v7y_weather_history import run as run_weather_audit
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT as V7Y_WEATHER_OUTPUT_ROOT,
    POLICY_ID as V7Y_WEATHER_POLICY_ID,
    validate_normalized_day as validate_v7y_normalized_day,
    verify_policy_registration,
)
from v5.probability import SOURCE_PATH
from v5b_confirmation.predictions import _bounds


START = date(2025, 1, 5)
END = date(2025, 12, 31)
PRIMARY_START = date(2025, 2, 4)
BACKFILL_START = date(2025, 7, 1)
BACKFILL_END = date(2025, 12, 31)
RUN = Path("runs/replays/v7y-hrrr-gefs-calendar-2025")
WEATHER_AUDIT_SNAPSHOT = RUN / "weather-integrity-freeze.json"
FREEZE = RUN / "prediction-freeze.json"
SCORED = RUN / "scored-days.csv"
PERIODS = RUN / "period-metrics.csv"
SUMMARY = RUN / "summary.json"
REPORT = RUN / "V7Y_HRRR_GEFS_CALENDAR_2025_RESULTS.md"
UNIVERSE = Path("data/manifests/kalshi_coverage.json")
CLIMATE_MANIFEST = Path("data/manifests/climate_2024-01-01_2026-01-08.json")
LEGACY_WEATHER_POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"
LABEL_EXCLUSIONS = {
    "2025-06-10": "no_complete_CLILAX_report_available_at_settlement",
    "2025-06-11": "no_complete_CLILAX_report_available_at_settlement",
}
KNOWN_CLILAX_QUARANTINE = {
    "CLILAX_202501010845.txt": "Uncorrected yesterday report has an inconsistent summary date",
    "CLILAX_202601050929.txt": "Archive issuance and WMO timestamp disagree",
}


class StudyError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise StudyError(f"JSON object required: {path}")
    return value


def _canonical_hash(value: dict[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _seal(value: dict[str, Any], field: str = "self_sha256") -> dict[str, Any]:
    value[field] = _canonical_hash(value, field)
    return value


def _verify_seal(value: dict[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise StudyError(f"{field} mismatch")


def _repair_prestate_binding(root: Path, registration: dict[str, Any]) -> dict[str, Any]:
    relative = registration["policy"].get("repair_prestate_path")
    if not isinstance(relative, str):
        raise StudyError("V7Y repair prestate path is missing")
    path = root / relative
    value = _read(path)
    _verify_seal(value, "snapshot_sha256")
    prior = value.get("prior_recovery_state")
    if (
        registration["policy"].get("repair_prestate_sha256") != value.get("snapshot_sha256")
        or not isinstance(prior, dict)
        or prior.get("recovery_sha256") != _canonical_hash(prior, "recovery_sha256")
    ):
        raise StudyError("V7Y repair prestate binding differs")
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": file_hash(path),
        "snapshot_sha256": value["snapshot_sha256"],
        "prior_recovery_sha256": prior["recovery_sha256"],
    }


def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_bytes() != encoded:
            raise StudyError(f"immutable artifact differs: {path}")
        return
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_bytes(encoded)
    pending.replace(path)


def _days(start: date = START, end: date = END) -> list[str]:
    return [(start + timedelta(days=index)).isoformat() for index in range((end - start).days + 1)]


def _partition(day: str) -> str:
    return "calibration_overlap_diagnostic" if day < PRIMARY_START.isoformat() else "post_calibration_primary"


def _normalize_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Repair legacy null strike metadata from outcome-blind ticker/rule text."""
    result = dict(row)
    if result.get("strike_type") in {"less", "between", "greater"}:
        result["metadata_repaired_from_ticker_rules"] = False
        return result
    match = re.search(r"-(B|T)(-?\d+(?:\.\d+)?)$", str(result.get("ticker", "")))
    if not match:
        raise StudyError("legacy contract ticker cannot repair null strike metadata")
    kind, raw_value = match.groups()
    value = float(raw_value)
    if kind == "B" and value % 1 == 0.5:
        result.update({
            "strike_type": "between",
            "floor_strike": math.floor(value),
            "cap_strike": math.ceil(value),
        })
    elif kind == "T" and value.is_integer():
        rules = str(result.get("rules_primary", "")).casefold()
        subtitle = str(result.get("yes_sub_title", "")).casefold()
        threshold = int(value)
        if "less than" in rules or "or below" in subtitle:
            result.update({"strike_type": "less", "floor_strike": None, "cap_strike": threshold})
        elif "greater than" in rules or "or above" in subtitle:
            result.update({"strike_type": "greater", "floor_strike": threshold, "cap_strike": None})
        else:
            raise StudyError("legacy tail direction cannot be inferred from frozen rules")
    else:
        raise StudyError("legacy contract ticker has unsupported strike encoding")
    result["metadata_repaired_from_ticker_rules"] = True
    return result


def _ordered_contracts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            -10000 if _bounds(row).integer_bounds()[0] is None else _bounds(row).integer_bounds()[0],
            10000 if _bounds(row).integer_bounds()[1] is None else _bounds(row).integer_bounds()[1],
            row["ticker"],
        ),
    )
    if len(ordered) != 6 or len({row["ticker"] for row in ordered}) != 6:
        raise StudyError("each market day must have six unique contracts")
    integer_bounds = [_bounds(row).integer_bounds() for row in ordered]
    if integer_bounds[0][0] is not None or integer_bounds[-1][1] is not None:
        raise StudyError("contract partition must have lower and upper tails")
    for left, right in zip(integer_bounds, integer_bounds[1:]):
        if left[1] is None or right[0] is None or left[1] + 1 != right[0]:
            raise StudyError("contract partition has a gap or overlap")
    return ordered


def _winner_ticker(contracts: list[dict[str, Any]], reported_high_f: int) -> str:
    winners = [row["ticker"] for row in contracts if _bounds(row).contains(reported_high_f)]
    if len(winners) != 1:
        raise StudyError("reported high must map to exactly one contract")
    return winners[0]


def _weather_paths(root: Path, day: str) -> tuple[Path, Path, Path, str]:
    if day <= "2025-06-30":
        folder = root / "data/normalized/v3_weather/selection" / f"date={day}"
        return (
            folder / "hrrr_points.parquet",
            folder / "gefs_summary_points.parquet",
            folder / "normalization_manifest.json",
            "v3_selection",
        )
    folder = root / V7Y_WEATHER_OUTPUT_ROOT / f"date={day}"
    return folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet", folder / "manifest.json", "v7y"


def _expected_tuples(model: str) -> set[tuple[Any, ...]]:
    if model == "hrrr":
        return {
            (lead, None, field, "KLAX")
            for lead in (2, 8, 14, 20)
            for field in (
                "temperature_2m", "total_cloud_cover", "cloud_ceiling",
                "wind_u_10m", "wind_v_10m", "mean_sea_level_pressure",
            )
        }
    return {
        (lead, member, "temperature_2m", "KLAX")
        for lead in (9, 12, 15, 18, 21, 24, 27, 30)
        for member in ("avg", "spr")
    }


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise StudyError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StudyError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StudyError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _eligible_times(value: Any) -> list[str]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise StudyError("eligible decision times must be an ordered string list")
    return list(value)


def _validate_row_timing(row: dict[str, Any], day: str, *, expected_policy_id: str) -> None:
    nominal = _parse_utc(row.get("nominal_issue_time_utc"), "nominal issue time")
    reference = _parse_utc(row.get("forecast_reference_time_utc"), "forecast reference time")
    if nominal != reference:
        raise StudyError(f"nominal and decoded model cycles differ: {day}")
    modified_raw = row.get("source_last_modified_at_utc")
    modified = None if modified_raw in (None, "") else _parse_utc(modified_raw, "source Last-Modified")
    expected_available = max(
        value for value in (nominal + timedelta(hours=6), modified) if value is not None
    )
    recorded_available = _parse_utc(
        row.get("effective_information_available_at_utc"), "effective availability time"
    )
    cutoff = datetime.combine(date.fromisoformat(day), time(18), UTC)
    expected_eligible = [
        f"{hour:02d}:00"
        for hour in (12, 15, 18)
        if expected_available <= datetime.combine(date.fromisoformat(day), time(hour), UTC)
    ]
    if (
        row.get("availability_policy_id") != expected_policy_id
        or row.get("availability_delay_hours") != 6
        or recorded_available != expected_available
        or recorded_available > cutoff
        or _eligible_times(row.get("eligible_decision_times_utc")) != expected_eligible
        or "18:00" not in expected_eligible
    ):
        raise StudyError(f"weather availability binding differs: {day}")


def _validate_rows(
    frame: pd.DataFrame,
    model: str,
    day: str,
    *,
    expected_policy_id: str,
) -> None:
    rows = frame.to_dict(orient="records")
    actual = {(row["lead_hours"], row["member_id"], row["field_id"], row["point_id"]) for row in rows}
    if actual != _expected_tuples(model) or len(rows) != len(actual):
        raise StudyError(f"{model} normalized feature tuple coverage differs: {day}")
    for row in rows:
        if (
            row.get("climate_date") != day
            or row.get("model") != model
            or row.get("as_of_validated") is not True
            or row.get("contains_settlement_label", False) is not False
        ):
            raise StudyError(f"{model} normalized safety boundary differs: {day}")
        _validate_row_timing(row, day, expected_policy_id=expected_policy_id)
    temperatures = [row for row in rows if row["field_id"] == "temperature_2m"]
    if any(
        row.get("is_missing") is not False
        or row.get("units") not in {"degF", "delta_degF"}
        or not math.isfinite(float(row["value"]))
        for row in temperatures
    ):
        raise StudyError(f"{model} temperature values differ: {day}")


def _load_weather(root: Path, day: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    h_path, g_path, manifest_path, family = _weather_paths(root, day)
    for path in (h_path, g_path, manifest_path):
        if not path.is_file():
            raise StudyError(f"weather input missing: {path}")
    manifest = _read(manifest_path)
    if family == "v7y":
        validate_v7y_normalized_day(root, date.fromisoformat(day), manifest)
    else:
        if (
            manifest.get("status") != "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
            or manifest.get("climate_date") != day
            or manifest.get("protected_final_read") is not False
            or manifest.get("network_used") is not False
        ):
            raise StudyError(f"V3 weather manifest boundary differs: {day}")
        outputs = {row.get("model"): row for row in manifest.get("outputs", [])}
        if set(outputs) != {"hrrr", "gefs"}:
            raise StudyError(f"V3 weather output set differs: {day}")
        for model, path, rows in (("hrrr", h_path, 24), ("gefs", g_path, 16)):
            record = outputs[model]
            if (
                record.get("path") != path.relative_to(root).as_posix()
                or record.get("rows") != rows
                or record.get("bytes") != path.stat().st_size
                or record.get("sha256") != file_hash(path)
            ):
                raise StudyError(f"V3 {model} output binding differs: {day}")
    hrrr, gefs = pd.read_parquet(h_path), pd.read_parquet(g_path)
    expected_policy_id = V7Y_WEATHER_POLICY_ID if family == "v7y" else LEGACY_WEATHER_POLICY_ID
    _validate_rows(hrrr, "hrrr", day, expected_policy_id=expected_policy_id)
    _validate_rows(gefs, "gefs", day, expected_policy_id=expected_policy_id)
    return hrrr, gefs, {
        path.relative_to(root).as_posix(): file_hash(path)
        for path in (h_path, g_path, manifest_path)
    }


def _load_universe(root: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    path = root / UNIVERSE
    universe = _read(path)
    if (
        universe.get("series") != "KXHIGHLAX"
        or universe.get("covered_days") != 361
        or universe.get("selected_contracts") != 2166
        or universe.get("station_screen_passes") != 2166
        or universe.get("first_date") != START.isoformat()
        or universe.get("last_date") != END.isoformat()
    ):
        raise StudyError("Kalshi calendar-2025 universe identity differs")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in universe.get("contracts", []):
        if START.isoformat() <= row.get("climate_date", "") <= END.isoformat():
            grouped[row["climate_date"]].append(_normalize_contract(row))
    expected = _days()
    if sorted(grouped) != expected:
        raise StudyError("Kalshi calendar-2025 day coverage differs")
    result = {day: _ordered_contracts(grouped[day]) for day in expected}
    return result, {UNIVERSE.as_posix(): file_hash(path)}


def _weather_integrity_snapshot(root: Path) -> dict[str, Any]:
    snapshot_path = root / WEATHER_AUDIT_SNAPSHOT
    registration = verify_policy_registration(root)
    repair_prestate = _repair_prestate_binding(root, registration)
    state = load_state(root / BACKFILL_STATE)
    expected = _days(BACKFILL_START, BACKFILL_END)
    expected_state_registration = {
        "path": registration["path"],
        "sha256": registration["sha256"],
        "policy_sha256": registration["policy_sha256"],
    }
    if (
        state.get("status") != "COMPLETE"
        or state.get("completed_dates") != expected
        or state.get("normalization_completed_dates") != expected
        or state.get("unavailable_dates") != []
        or state.get("failed_dates") != {}
        or state.get("availability_registration") != expected_state_registration
        or state.get("availability_policy_id") != V7Y_WEATHER_POLICY_ID
        or state.get("normalized_output_root") != V7Y_WEATHER_OUTPUT_ROOT.as_posix()
        or state.get("availability_repair_parent_recovery_sha256")
        != repair_prestate["prior_recovery_sha256"]
    ):
        raise StudyError("all 184 HRRR/GEFS backfill and V7Y normalization dates must complete before freeze")
    if snapshot_path.is_file():
        value = _read(snapshot_path)
        _verify_seal(value, "audit_sha256")
    else:
        value = run_weather_audit(root, full=True)
        if value.get("completed_date_count") != 184 or len(value.get("dates", [])) != 184:
            raise StudyError("full HRRR/GEFS integrity audit coverage differs")
        _write_immutable(snapshot_path, value)
    if (
        value.get("schema_version") != AUDIT_SCHEMA
        or value.get("status") != "PASS"
        or value.get("recovery_sha256") != state["recovery_sha256"]
        or value.get("availability_policy_id") != V7Y_WEATHER_POLICY_ID
        or value.get("availability_policy_registration") != policy_registration_binding(registration)
        or value.get("availability_repair_parent_recovery_sha256")
        != state["availability_repair_parent_recovery_sha256"]
        or value.get("availability_repair_prestate") != repair_prestate
        or value.get("completed_date_count") != 184
        or value.get("normalization_completed_date_count") != 184
        or value.get("raw_hrrr_object_count") != 736
        or value.get("raw_gefs_object_count") != 2944
        or value.get("raw_selected_field_count") != 7360
        or value.get("normalized_hrrr_row_count") != 4416
        or value.get("normalized_gefs_row_count") != 2944
        or value.get("network_used") is not False
        or value.get("protected_labels_read") is not False
        or value.get("paper_orders_placed") != 0
        or value.get("live_orders_placed") != 0
    ):
        raise StudyError("full HRRR/GEFS integrity snapshot differs")
    return value


def freeze(root: Path) -> dict[str, Any]:
    freeze_path = root / FREEZE
    if freeze_path.is_file():
        value = _read(freeze_path)
        _verify_seal(value)
        if value.get("schema_version") != "v7y-hrrr-gefs-calendar-2025-prediction-freeze-v2":
            raise StudyError("existing immutable prediction freeze predates the V7Y availability policy")
        return value
    weather_audit = _weather_integrity_snapshot(root)
    registration = verify_policy_registration(root)
    repair_prestate = _repair_prestate_binding(root, registration)
    universe, bindings = _load_universe(root)
    audit_path = root / WEATHER_AUDIT_SNAPSHOT
    bindings[WEATHER_AUDIT_SNAPSHOT.as_posix()] = file_hash(audit_path)
    bindings[registration["path"]] = registration["sha256"]
    bindings[repair_prestate["path"]] = repair_prestate["sha256"]
    source_path = root / SOURCE_PATH
    source = _read(source_path)
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    if (
        source.get("contains_development_labels_or_outcomes") is not False
        or source.get("protected_final_used_for_fit") is not False
        or model.identity != source.get("fitted_model_sha256")
        or source.get("fit_roles") != [
            "weather_training_through_2024_12_31",
            "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
        ]
    ):
        raise StudyError("fixed HRRR/GEFS model identity differs")
    bindings[SOURCE_PATH.as_posix()] = file_hash(source_path)
    records: list[dict[str, Any]] = []
    for index, day in enumerate(_days(), 1):
        contracts = universe[day]
        hrrr, gefs, weather_bindings = _load_weather(root, day)
        prediction = model.predict({
            "climate_date": day,
            "forecasts": pd.concat([hrrr, gefs], ignore_index=True).to_dict(orient="records"),
            "observations": [],
            "contains_settlement_label": False,
            "as_of_join_validated": True,
        }, tuple(_bounds(row) for row in contracts))
        probabilities = [float(value) for value in prediction.probabilities]
        if (
            len(probabilities) != 6
            or any(not math.isfinite(value) or value < 0 or value > 1 for value in probabilities)
            or not math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-10)
        ):
            raise StudyError(f"probability vector differs: {day}")
        records.append({
            "climate_date": day,
            "partition": _partition(day),
            "contracts": [
                {
                    "ticker": row["ticker"],
                    "strike_type": row["strike_type"],
                    "floor_strike": row["floor_strike"],
                    "cap_strike": row["cap_strike"],
                    "metadata_repaired_from_ticker_rules": row["metadata_repaired_from_ticker_rules"],
                }
                for row in contracts
            ],
            "yes_probabilities": {
                row["ticker"]: value for row, value in zip(contracts, probabilities, strict=True)
            },
            "regime": prediction.regime,
            "pooled_regime_fallback": bool(prediction.pooled_regime_fallback),
            "model_abstains": bool(prediction.should_abstain),
        })
        bindings.update(weather_bindings)
        if index % 25 == 0:
            print(json.dumps({"frozen_dates": index, "target": 361}), flush=True)
    document = _seal({
        "schema_version": "v7y-hrrr-gefs-calendar-2025-prediction-freeze-v2",
        "status": "FROZEN_BEFORE_CLILAX_LABEL_READ",
        "study_year": 2025,
        "target_date_count": 361,
        "calibration_overlap": {"start": "2025-01-05", "end": "2025-02-03", "count": 30},
        "post_calibration_primary": {"start": "2025-02-04", "end": "2025-12-31", "count": 331},
        "model": {
            "name": "fixed_V5B_HRRR_GEFS_probability_model",
            "fitted_model_sha256": model.identity,
            "source_path": SOURCE_PATH.as_posix(),
            "source_sha256": bindings[SOURCE_PATH.as_posix()],
            "refit_performed": False,
            "recalibration_performed": False,
        },
        "contract_metadata_repaired_from_ticker_rules_count": sum(
            row["metadata_repaired_from_ticker_rules"]
            for record in records for row in record["contracts"]
        ),
        "weather_integrity_audit_sha256": weather_audit["audit_sha256"],
        "availability_policy_id": V7Y_WEATHER_POLICY_ID,
        "availability_policy_registration": policy_registration_binding(registration),
        "availability_repair_parent_recovery_sha256": weather_audit[
            "availability_repair_parent_recovery_sha256"
        ],
        "availability_repair_prestate": repair_prestate,
        "records": records,
        "input_bindings": dict(sorted(bindings.items())),
        "outcomes_read": False,
        "clilax_labels_read": False,
        "market_prices_read": False,
        "network_used_by_freeze": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "limitations": [
            "This evaluates forecast probabilities, not fills or trading returns.",
            "The first 30 days were used for bracket calibration and are diagnostic only.",
            "The fixed V5B model was selected in later research, so the replay is retrospective rather than contemporaneously registered in 2025.",
            "Archived HTTP Last-Modified timestamps do not prove original model publication time.",
        ],
    })
    _write_immutable(freeze_path, document)
    return document


def _labels(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any]]:
    # Outcome code and archive access are intentionally imported only by score().
    from klax_lab.climate import parse_product

    manifest_path = root / CLIMATE_MANIFEST
    manifest = _read(manifest_path)
    archive_path = root / manifest["path"]
    if file_hash(archive_path) != manifest.get("sha256"):
        raise StudyError("CLILAX archive binding differs")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    quarantined: dict[str, str] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            try:
                row = parse_product(member.filename, archive.read(member))
            except ValueError as exc:
                quarantined[Path(member.filename).name] = str(exc)
                continue
            if row is not None and START.isoformat() <= row["climate_date"] <= END.isoformat():
                groups[row["climate_date"]].append(row)
    if quarantined != KNOWN_CLILAX_QUARANTINE:
        raise StudyError(f"CLILAX quarantine differs: {quarantined}")
    labels = {day: max(rows, key=lambda row: row["issued_at"]) for day, rows in groups.items()}
    expected = sorted(set(_days()) - set(LABEL_EXCLUSIONS))
    if sorted(labels) != expected:
        missing = sorted(set(expected) - set(labels))
        extra = sorted(set(labels) - set(expected))
        raise StudyError(f"CLILAX calendar-2025 coverage differs: missing={missing[:5]}, extra={extra[:5]}")
    return labels, {
        CLIMATE_MANIFEST.as_posix(): file_hash(manifest_path),
        Path(manifest["path"]).as_posix(): file_hash(archive_path),
    }, {
        "scorable_date_count": len(labels),
        "excluded_dates": LABEL_EXCLUSIONS,
        "quarantined_members": quarantined,
        "exclusions_registered_before_v7y_scoring": True,
    }


def _season(day: str) -> str:
    month = int(day[5:7])
    return "winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5) else "summer" if month in (6, 7, 8) else "fall"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise StudyError("metric cohort cannot be empty")
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": statistics.mean(float(row["multiclass_brier"]) for row in rows),
        "mean_log_loss": statistics.mean(float(row["log_loss"]) for row in rows),
        "modal_bracket_accuracy": statistics.mean(bool(row["modal_bracket_hit"]) for row in rows),
        "mean_true_bracket_probability": statistics.mean(float(row["true_bracket_probability"]) for row in rows),
    }


def _period_metrics(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    partitions = (
        ("all", scored),
        ("calibration_overlap_diagnostic", [row for row in scored if row["partition"] == "calibration_overlap_diagnostic"]),
        ("post_calibration_primary", [row for row in scored if row["partition"] == "post_calibration_primary"]),
    )
    output: list[dict[str, Any]] = []
    for partition, rows in partitions:
        output.append({"partition": partition, "period_type": "overall", "period": "all", **_metrics(rows)})
        for month in sorted({row["month"] for row in rows}):
            cohort = [row for row in rows if row["month"] == month]
            output.append({"partition": partition, "period_type": "month", "period": month, **_metrics(cohort)})
        for season in ("winter", "spring", "summer", "fall"):
            cohort = [row for row in rows if row["season"] == season]
            if cohort:
                output.append({"partition": partition, "period_type": "season", "period": season, **_metrics(cohort)})
    return output


def score(root: Path) -> dict[str, Any]:
    freeze_path = root / FREEZE
    frozen = _read(freeze_path)
    _verify_seal(frozen)
    registration = verify_policy_registration(root)
    repair_prestate = _repair_prestate_binding(root, registration)
    if (
        frozen.get("schema_version") != "v7y-hrrr-gefs-calendar-2025-prediction-freeze-v2"
        or frozen.get("status") != "FROZEN_BEFORE_CLILAX_LABEL_READ"
        or frozen.get("target_date_count") != 361
        or frozen.get("outcomes_read") is not False
        or frozen.get("clilax_labels_read") is not False
        or len(frozen.get("records", [])) != 361
        or frozen.get("availability_policy_id") != V7Y_WEATHER_POLICY_ID
        or frozen.get("availability_policy_registration") != policy_registration_binding(registration)
        or frozen.get("availability_repair_prestate") != repair_prestate
    ):
        raise StudyError("valid outcome-blind 361-day prediction freeze required")
    for relative, expected in frozen["input_bindings"].items():
        path = root / relative
        if not path.is_file() or file_hash(path) != expected:
            raise StudyError(f"frozen input changed: {relative}")
    existing_outputs = [path for path in (SCORED, PERIODS, SUMMARY, REPORT) if (root / path).exists()]
    if existing_outputs:
        raise StudyError(f"existing immutable scored artifact refuses overwrite: {existing_outputs[0]}")
    labels, label_bindings, label_audit = _labels(root)
    scored: list[dict[str, Any]] = []
    for record in frozen["records"]:
        day = record["climate_date"]
        if day in LABEL_EXCLUSIONS:
            continue
        actual = int(labels[day]["tmax_f"])
        winner = _winner_ticker(record["contracts"], actual)
        probabilities = record["yes_probabilities"]
        if set(probabilities) != {row["ticker"] for row in record["contracts"]}:
            raise StudyError(f"probability/contract ticker mapping differs: {day}")
        true_probability = max(float(probabilities[winner]), 1e-15)
        modal = max(probabilities, key=lambda ticker: (float(probabilities[ticker]), ticker))
        scored.append({
            "climate_date": day,
            "month": day[:7],
            "season": _season(day),
            "partition": record["partition"],
            "reported_high_f": actual,
            "winning_ticker": winner,
            "multiclass_brier": sum(
                (float(probability) - (1.0 if ticker == winner else 0.0)) ** 2
                for ticker, probability in probabilities.items()
            ),
            "log_loss": -math.log(true_probability),
            "true_bracket_probability": true_probability,
            "modal_bracket_ticker": modal,
            "modal_bracket_hit": modal == winner,
        })
    periods = _period_metrics(scored)
    by_key = {(row["partition"], row["period_type"], row["period"]): row for row in periods}
    summary = _seal({
        "schema_version": "v7y-hrrr-gefs-calendar-2025-score-v1",
        "status": "COMPLETE",
        "prediction_freeze_sha256": frozen["self_sha256"],
        "frozen_prediction_count": len(frozen["records"]),
        "scored_date_count": len(scored),
        "excluded_date_count": len(LABEL_EXCLUSIONS),
        "label_audit": label_audit,
        "calibration_overlap": by_key[("calibration_overlap_diagnostic", "overall", "all")],
        "post_calibration_primary": by_key[("post_calibration_primary", "overall", "all")],
        "overall": by_key[("all", "overall", "all")],
        "primary_monthly": [row for row in periods if row["partition"] == "post_calibration_primary" and row["period_type"] == "month"],
        "primary_seasonal": [row for row in periods if row["partition"] == "post_calibration_primary" and row["period_type"] == "season"],
        "label_bindings": label_bindings,
        "economic_result": None,
        "economic_result_reason": "This outcome-only study has no historical Level-2 execution evidence and cannot estimate fills or realized return.",
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })
    output = root / RUN
    output.mkdir(parents=True, exist_ok=True)
    with (root / SCORED).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(scored[0]))
        writer.writeheader()
        writer.writerows(scored)
    with (root / PERIODS).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(periods[0]))
        writer.writeheader()
        writer.writerows(periods)
    (root / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    primary = summary["post_calibration_primary"]
    report = f"""# V7Y fixed HRRR/GEFS calendar-2025 probability study

## Result

All **361 predictions were frozen before label access**. The fixed model scored **{primary['date_count']} post-calibration dates**. Its multiclass Brier score was **{primary['mean_multiclass_brier']:.4f}**, log loss was **{primary['mean_log_loss']:.4f}**, modal-bracket accuracy was **{primary['modal_bracket_accuracy']:.1%}**, and average probability assigned to the winning bracket was **{primary['mean_true_bracket_probability']:.1%}**.

The 30 calibration-overlap dates are reported separately. June 10 and June 11 are excluded under the pre-existing exact-settlement source rule because no complete CLILAX report is available. Monthly and seasonal results are in `period-metrics.csv`; daily audit rows are in `scored-days.csv`.

## Interpretation

This evaluates fixed HRRR/GEFS forecast probabilities. It does not estimate fill probability, queue position, fees, or trading return. The model was selected after 2025, so these results measure retrospective stability rather than a contemporaneously registered trading strategy. No paper or live orders were created.
"""
    (root / REPORT).write_text(report, encoding="utf-8")
    return summary


def status(root: Path) -> dict[str, Any]:
    state = load_state(root / BACKFILL_STATE)
    return {
        "backfill_status": state["status"],
        "backfill_completed": len(state["completed_dates"]),
        "backfill_target": state["target_date_count"],
        "backfill_unavailable": len(state["unavailable_dates"]),
        "backfill_failed": len(state["failed_dates"]),
        "weather_integrity_freeze_exists": (root / WEATHER_AUDIT_SNAPSHOT).is_file(),
        "prediction_freeze_exists": (root / FREEZE).is_file(),
        "summary_exists": (root / SUMMARY).is_file(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "freeze", "score"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    result = status(root) if args.action == "status" else freeze(root) if args.action == "freeze" else score(root)
    if args.action == "freeze":
        result = {
            "status": result["status"],
            "target_date_count": result["target_date_count"],
            "outcomes_read": result["outcomes_read"],
            "self_sha256": result["self_sha256"],
        }
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
