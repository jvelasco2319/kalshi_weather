"""Freeze and score the fixed 2025 LAX weather-model comparison.

The freeze command is outcome blind.  The score command refuses to run until
the immutable prediction file exists, then opens the archived CLILAX labels.
This is a forecast-signal study; 2025 Level-2 execution evidence is unavailable.
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
import zipfile
from typing import Any

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.climate import parse_product
from klax_lab.domain import round_fahrenheit
from scripts.acquire_v7y_weather_history import STATE as V7Y_WEATHER_STATE
from scripts.acquire_v7y_weather_history import load_state as load_v7y_weather_state
from scripts.acquire_v7y_weather_history import target_dates as v7y_weather_target_dates
from scripts.audit_v7y_weather_history import policy_registration_binding
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT as V7Y_WEATHER_OUTPUT_ROOT,
    POLICY_ID as V7Y_WEATHER_POLICY_ID,
    validate_normalized_day as validate_v7y_normalized_day,
    verify_policy_registration,
)
from v5.probability import SOURCE_PATH as V5B_MODEL_SOURCE
from v7_shadow.daily import _v5b_probabilities


START = date(2025, 1, 5)
END = date(2025, 12, 31)
PRIMARY_START = date(2025, 2, 4)
RUN = Path("runs/replays/v7y-calendar-2025")
FREEZE = RUN / "prediction-freeze.json"
SCORED = RUN / "scored-days.csv"
SUMMARY = RUN / "summary.json"
MONTHLY = RUN / "monthly-metrics.csv"
PROBABILITY_PERIODS = RUN / "probability-period-metrics.csv"
RELIABILITY = RUN / "probability-reliability.csv"
REPORT = RUN / "V7Y_CALENDAR_2025_RESULTS.md"
UNIVERSE = Path("data/manifests/kalshi_coverage.json")
CLIMATE_MANIFEST = Path("data/manifests/climate_2024-01-01_2026-01-08.json")
FRIEND_ARCHIVE = Path("data/raw/friend_method_weather_v1")
LEGACY_WEATHER_POLICY_ID = "max_nominal_plus_6h_archived_last_modified_v2"

TEMP_MODELS = (
    "hrrr_sample_max_f",
    "gefs_mean_sample_max_f",
    "gfs_tmax_f",
    "nam_sample_max_f",
    "nbm_sample_max_f",
    "gfs_nam_nbm_equal_mean_f",
)

RAW_TEMPERATURE_METHODS: dict[str, dict[str, Any]] = {
    "hrrr_sample_max_f": {
        "family": "HRRR",
        "construction": "maximum of 06Z instantaneous 2m temperatures valid at leads 2, 8, 14, and 20 hours",
        "sampling": "four snapshots across the LAX climate day",
    },
    "gefs_mean_sample_max_f": {
        "family": "GEFS",
        "construction": "maximum of the 00Z ensemble-mean instantaneous 2m temperature at leads 9 through 30 every 3 hours",
        "sampling": "eight ensemble-mean snapshots; not a member-wise daily-maximum distribution",
    },
    "gfs_tmax_f": {
        "family": "GFS",
        "construction": "maximum of 00Z six-hour TMAX products ending at leads 18, 24, and 30 hours",
        "sampling": "accumulation-style maximum field with incomplete climate-day boundary coverage",
    },
    "nam_sample_max_f": {
        "family": "NAM",
        "construction": "maximum of 00Z instantaneous 2m temperatures at leads 9 through 30 every 3 hours",
        "sampling": "eight point snapshots",
    },
    "nbm_sample_max_f": {
        "family": "NBM",
        "construction": "maximum of 00Z instantaneous 2m temperatures at leads 8 through 31 hourly",
        "sampling": "24 hourly point snapshots over the fixed-PST climate day",
    },
    "gfs_nam_nbm_equal_mean_f": {
        "family": "GFS/NAM/NBM synthesis",
        "construction": "unfitted equal mean of the three fixed source-high estimates",
        "sampling": "combines non-equivalent GFS, NAM, and NBM source constructions",
    },
}


class StudyError(ValueError):
    pass


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    value["self_sha256"] = sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return value


def _verify_seal(value: dict[str, Any]) -> None:
    expected = value.get("self_sha256")
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    actual = sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    if expected != actual:
        raise StudyError("prediction freeze seal mismatch")


def _artifact_hash(value: dict[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _repair_prestate_binding(root: Path, registration: dict[str, Any]) -> dict[str, Any]:
    relative = registration["policy"].get("repair_prestate_path")
    if not isinstance(relative, str):
        raise StudyError("V7Y repair prestate path is missing")
    path = root / relative
    value = _read(path)
    prior = value.get("prior_recovery_state")
    if (
        value.get("snapshot_sha256") != _artifact_hash(value, "snapshot_sha256")
        or registration["policy"].get("repair_prestate_sha256") != value.get("snapshot_sha256")
        or not isinstance(prior, dict)
        or prior.get("recovery_sha256") != _artifact_hash(prior, "recovery_sha256")
    ):
        raise StudyError("V7Y repair prestate binding differs")
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _hash(path),
        "snapshot_sha256": value["snapshot_sha256"],
        "prior_recovery_sha256": prior["recovery_sha256"],
    }


def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise StudyError(f"immutable artifact differs: {path}")
        return
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_bytes(encoded)
    pending.replace(path)


def _days() -> list[str]:
    return [
        (START + timedelta(days=index)).isoformat()
        for index in range((END - START).days + 1)
    ]


def _ordered_contracts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -10000 if row["floor_strike"] is None else int(row["floor_strike"]),
            row["ticker"],
        ),
    )


def _normalize_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Recover missing archive bounds from outcome-blind contract text."""
    text = str(row.get("yes_sub_title") or "").strip()
    between = re.fullmatch(r"(-?\d+)° to (-?\d+)°", text)
    below = re.fullmatch(r"(-?\d+)° or below", text)
    above = re.fullmatch(r"(-?\d+)° or above", text)
    if between:
        lower, upper = int(between[1]), int(between[2])
        derived = ("between", lower, upper)
        expected_suffix = f"-B{(lower + upper) / 2:g}"
    elif below:
        upper_inclusive = int(below[1])
        derived = ("less", None, upper_inclusive + 1)
        expected_suffix = f"-T{upper_inclusive + 1}"
    elif above:
        lower_inclusive = int(above[1])
        derived = ("greater", lower_inclusive - 1, None)
        expected_suffix = f"-T{lower_inclusive - 1}"
    else:
        raise StudyError(f"unrecognized Kalshi temperature interval text: {row.get('ticker')}")
    supplied = (row.get("strike_type"), row.get("floor_strike"), row.get("cap_strike"))
    if supplied == (None, None, None):
        source = "parsed_yes_sub_title_due_to_missing_archive_bounds"
    elif supplied == derived:
        source = "kalshi_archive_bounds_cross_checked_to_yes_sub_title"
    else:
        raise StudyError(f"Kalshi bounds disagree with contract text: {row.get('ticker')}")
    if not str(row.get("ticker", "")).endswith(expected_suffix):
        raise StudyError(f"Kalshi ticker disagrees with contract text: {row.get('ticker')}")
    return {
        **row,
        "strike_type": derived[0],
        "floor_strike": derived[1],
        "cap_strike": derived[2],
        "bounds_source": source,
    }


def _weather_paths(root: Path, day: str) -> tuple[Path, Path, Path]:
    if day <= "2025-06-30":
        folder = root / "data/normalized/v3_weather/selection" / f"date={day}"
        manifest = folder / "normalization_manifest.json"
    else:
        folder = root / V7Y_WEATHER_OUTPUT_ROOT / f"date={day}"
        manifest = folder / "manifest.json"
    return folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet", manifest


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
    target = date.fromisoformat(day)
    cutoff = datetime.combine(target, time(18), UTC)
    expected_eligible = [
        f"{hour:02d}:00"
        for hour in (12, 15, 18)
        if expected_available <= datetime.combine(target, time(hour), UTC)
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


def _weather_frames(root: Path, day: str) -> tuple[pd.DataFrame, pd.DataFrame, tuple[Path, Path, Path]]:
    h_path, g_path, manifest_path = _weather_paths(root, day)
    manifest = _read(manifest_path)
    if day <= "2025-06-30":
        safe_manifest = (
            manifest.get("status") == "DAY_NORMALIZED_WITH_CONSERVATIVE_ASOF_BOUND"
            and manifest.get("climate_date") == day
            and manifest.get("partition") == "selection"
            and manifest.get("protected_final_read") is False
            and manifest.get("hrrr_coverage", {}).get("coverage_complete") is True
            and manifest.get("gefs_coverage", {}).get("coverage_complete") is True
            and "18:00" in manifest.get("hrrr_coverage", {}).get("decision_times_utc", [])
            and "18:00" in manifest.get("gefs_coverage", {}).get("decision_times_utc", [])
        )
    else:
        try:
            validate_v7y_normalized_day(root, date.fromisoformat(day), manifest)
            safe_manifest = True
        except Exception as exc:
            raise StudyError(f"unsafe normalized HRRR/GEFS manifest: {day}") from exc
    if not safe_manifest:
        raise StudyError(f"unsafe normalized HRRR/GEFS manifest: {day}")
    outputs = {Path(row["path"]).name: row for row in manifest.get("outputs", [])}
    expected = ((h_path, "hrrr_points.parquet", 24), (g_path, "gefs_summary_points.parquet", 16))
    for path, name, rows in expected:
        record = outputs.get(name)
        if (
            record is None
            or not path.is_file()
            or path.stat().st_size != record.get("bytes")
            or _hash(path) != record.get("sha256")
            or record.get("rows") != rows
        ):
            raise StudyError(f"normalized HRRR/GEFS output binding differs: {day}:{name}")
    hrrr, gefs = pd.read_parquet(h_path), pd.read_parquet(g_path)
    expected_policy_id = (
        LEGACY_WEATHER_POLICY_ID if day <= "2025-06-30" else V7Y_WEATHER_POLICY_ID
    )
    for frame, model, rows in ((hrrr, "hrrr", 24), (gefs, "gefs", 16)):
        if (
            len(frame) != rows
            or set(frame["climate_date"].astype(str)) != {day}
            or set(frame["model"].astype(str)) != {model}
            or set(frame["point_id"].astype(str)) != {"KLAX"}
            or not frame["as_of_validated"].astype(bool).all()
            or (
                "contains_settlement_label" in frame
                and frame["contains_settlement_label"].astype(bool).any()
            )
        ):
            raise StudyError(f"unsafe normalized HRRR/GEFS rows: {day}:{model}")
        for row in frame.to_dict(orient="records"):
            _validate_row_timing(row, day, expected_policy_id=expected_policy_id)
    h_temp = hrrr[(hrrr["field_id"] == "temperature_2m") & (hrrr["is_missing"] == False)]  # noqa: E712
    g_temp = gefs[(gefs["field_id"] == "temperature_2m") & (gefs["is_missing"] == False)]  # noqa: E712
    if (
        len(h_temp) != 4
        or set(int(value) for value in h_temp["lead_hours"]) != {2, 8, 14, 20}
        or len(g_temp) != 16
        or set(int(value) for value in g_temp["lead_hours"]) != {9, 12, 15, 18, 21, 24, 27, 30}
        or set(g_temp["member_id"].astype(str)) != {"avg", "spr"}
    ):
        raise StudyError(f"HRRR/GEFS temperature feature inventory differs: {day}")
    return hrrr, gefs, (h_path, g_path, manifest_path)


def _temperature_values(frame: pd.DataFrame, *, member: str | None = None) -> list[float]:
    selected = frame[
        (frame["field_id"] == "temperature_2m")
        & (frame["is_missing"] == False)  # noqa: E712
        & (frame["point_id"] == "KLAX")
    ]
    if member is not None:
        selected = selected[selected["member_id"] == member]
    return [float(value) for value in selected["value"].tolist()]


def _friend_values(path: Path, day: str) -> dict[str, float]:
    value = _read(path)
    expected_decision = day + "T18:00:00+00:00"
    if (
        value.get("climate_date") != day
        or value.get("complete") is not True
        or value.get("decision_time_utc") != expected_decision
        or value.get("contains_market_price") is not False
        or value.get("contains_settlement_label") is not False
        or value.get("actual_orders_placed") is not False
        or value.get("missing_tasks") != []
    ):
        raise StudyError(f"incomplete GFS/NAM/NBM day: {day}")
    models = value.get("models", {})
    if set(models) != {"gfs", "gfs_seamless", "nam", "nbm"}:
        raise StudyError(f"GFS/NAM/NBM model set differs: {day}")
    gfs = float(models["gfs"]["daily_high_f"])
    if float(models["gfs_seamless"]["daily_high_f"]) != gfs:
        raise StudyError(f"GFS alias differs: {day}")
    nam = float(models["nam"]["daily_high_f"])
    nbm = float(models["nbm"]["daily_high_f"])
    for model in ("gfs", "nam", "nbm"):
        if not models[model].get("samples") or any(
            sample.get("as_of_decision_safe") is not True
            or sample.get("decision_time_utc") != expected_decision
            for sample in models[model]["samples"]
        ):
            raise StudyError(f"unsafe GFS/NAM/NBM sample: {day}:{model}")
    return {
        "gfs_tmax_f": gfs,
        "nam_sample_max_f": nam,
        "nbm_sample_max_f": nbm,
        "gfs_nam_nbm_equal_mean_f": (gfs + nam + nbm) / 3.0,
    }


def _v5b_model_definition(root: Path) -> dict[str, Any]:
    path = root / V5B_MODEL_SOURCE
    value = _read(path)
    model = value.get("model", {})
    state_model = value.get("fitted_model_state", {}).get("model", {})
    encoder = state_model.get("encoder", {})
    expected_features = [
        "calendar.cos_day",
        "calendar.sin_day",
        "gefs.temperature_mean_f.max",
        "gefs.temperature_mean_f.mean",
        "gefs.temperature_mean_f.min",
        "gefs.temperature_spread_f.max",
        "gefs.temperature_spread_f.mean",
        "gefs.temperature_spread_f.min",
        "hrrr.temperature_f.max",
        "hrrr.temperature_f.mean",
        "hrrr.temperature_f.min",
    ]
    if (
        model.get("feature_set") != "temperature_only"
        or model.get("forecast_source_set") != "hrrr_gefs_summary"
        or model.get("probability_family") != "quantile_brackets"
        or model.get("calibration_operator") != "isotonic_bracket"
        or model.get("regime_model") != "pooled"
        or encoder.get("raw_feature_names") != expected_features
        or state_model.get("calibration_rows") != 30
        or state_model.get("distribution", {}).get("location", {}).get("training_rows") != 365
    ):
        raise StudyError("frozen V5B probability-model definition differs")
    if value.get("fit_roles") != [
        "weather_training_through_2024_12_31",
        "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
    ]:
        raise StudyError("frozen V5B fit roles differ")
    return {
        "source_path": V5B_MODEL_SOURCE.as_posix(),
        "source_sha256": _hash(path),
        "fitted_model_sha256": value["fitted_model_sha256"],
        "feature_set": model["feature_set"],
        "forecast_source_set": model["forecast_source_set"],
        "features": expected_features,
        "probability_family": model["probability_family"],
        "calibration_operator": model["calibration_operator"],
        "regime_model": model["regime_model"],
        "weather_training_rows": 365,
        "calibration_rows": 30,
        "fit_roles": value["fit_roles"],
    }


def _validate_prediction_freeze(root: Path, frozen: dict[str, Any]) -> None:
    """Prove prediction completeness and input immutability before label access."""
    _verify_seal(frozen)
    registration = verify_policy_registration(root)
    repair_prestate = _repair_prestate_binding(root, registration)
    expected_days = _days()
    if (
        frozen.get("schema_version") != "klax-v7y-calendar-weather-freeze-v2"
        or frozen.get("status") != "FROZEN_BEFORE_CLILAX_LABEL_READ"
        or frozen.get("outcomes_read") is not False
        or frozen.get("clilax_labels_read") is not False
        or frozen.get("market_prices_read") is not False
        or frozen.get("target_dates") != expected_days
        or frozen.get("target_date_count") != len(expected_days)
        or frozen.get("calibration_overlap") != {
            "start": "2025-01-05", "end": "2025-02-03", "count": 30,
        }
        or frozen.get("primary") != {
            "start": "2025-02-04", "end": "2025-12-31", "count": 331,
        }
        or frozen.get("contract_bounds_provenance") != {
            "kalshi_archive_rows": 2141,
            "text_reconstructed_rows": 25,
            "reconstruction_used_outcomes": False,
        }
        or frozen.get("availability_policy_id") != V7Y_WEATHER_POLICY_ID
        or frozen.get("availability_policy_registration") != policy_registration_binding(registration)
        or frozen.get("availability_repair_prestate") != repair_prestate
    ):
        raise StudyError("complete outcome-blind calendar prediction freeze required")
    records = frozen.get("records", [])
    if len(records) != len(expected_days) or [row.get("climate_date") for row in records] != expected_days:
        raise StudyError("prediction-freeze date coverage differs")
    model_definition = frozen.get("v5b_probability_model")
    if model_definition != _v5b_model_definition(root):
        raise StudyError("prediction-freeze probability-model binding differs")
    if (
        frozen.get("raw_temperature_methods") != RAW_TEMPERATURE_METHODS
        or frozen.get("raw_temperature_comparison_class")
        != "non_equivalent_fixed_source_diagnostics"
    ):
        raise StudyError("prediction-freeze raw-temperature definitions differ")
    expected_bindings = {
        UNIVERSE.as_posix(),
        V5B_MODEL_SOURCE.as_posix(),
        registration["path"],
        repair_prestate["path"],
        V7Y_WEATHER_STATE.as_posix(),
    }
    for day in expected_days:
        h_path, g_path, manifest_path = _weather_paths(root, day)
        friend_path = root / FRIEND_ARCHIVE / f"date={day}" / "daily.json"
        expected_bindings.update(
            path.relative_to(root).as_posix()
            for path in (h_path, g_path, manifest_path, friend_path)
        )
    if set(frozen.get("input_bindings", {})) != expected_bindings:
        raise StudyError("prediction-freeze input-binding inventory differs")
    for expected_day, record in zip(expected_days, records):
        expected_partition = (
            "calibration_overlap_diagnostic"
            if expected_day < PRIMARY_START.isoformat()
            else "post_calibration_primary"
        )
        contracts = record.get("contracts", [])
        probabilities = record.get("v5b_yes_probabilities", {})
        temperatures = record.get("temperatures", {})
        tickers = {row.get("ticker") for row in contracts}
        if (
            record.get("partition") != expected_partition
            or len(contracts) != 6
            or len(tickers) != 6
            or set(probabilities) != tickers
            or set(temperatures) != set(TEMP_MODELS)
            or record.get("v5b_fitted_model_sha256") != model_definition["fitted_model_sha256"]
            or any(
                contract.get("bounds_source") not in {
                    "kalshi_archive_bounds_cross_checked_to_yes_sub_title",
                    "parsed_yes_sub_title_due_to_missing_archive_bounds",
                }
                for contract in contracts
            )
        ):
            raise StudyError(f"prediction-freeze record structure differs: {expected_day}")
        values = [float(probabilities[ticker]) for ticker in tickers]
        if (
            any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in values)
            or not math.isclose(sum(values), 1.0, abs_tol=1e-10)
            or any(not math.isfinite(float(value)) for value in temperatures.values())
        ):
            raise StudyError(f"prediction-freeze numeric content differs: {expected_day}")
    for relative, expected in frozen.get("input_bindings", {}).items():
        path = root / relative
        if not path.is_file() or _hash(path) != expected:
            raise StudyError(f"frozen input changed: {relative}")


def freeze(root: Path) -> dict[str, Any]:
    freeze_path = root / FREEZE
    if freeze_path.is_file():
        value = _read(freeze_path)
        _verify_seal(value)
        if value.get("schema_version") != "klax-v7y-calendar-weather-freeze-v2":
            raise StudyError("existing immutable prediction freeze predates the V7Y availability policy")
        return value
    registration = verify_policy_registration(root)
    repair_prestate = _repair_prestate_binding(root, registration)
    weather_state_path = root / V7Y_WEATHER_STATE
    weather_state = load_v7y_weather_state(weather_state_path)
    expected_weather_dates = v7y_weather_target_dates()
    expected_state_registration = {
        "path": registration["path"],
        "sha256": registration["sha256"],
        "policy_sha256": registration["policy_sha256"],
    }
    if (
        weather_state.get("status") != "COMPLETE"
        or weather_state.get("completed_dates") != expected_weather_dates
        or weather_state.get("normalization_completed_dates") != expected_weather_dates
        or weather_state.get("unavailable_dates") != []
        or weather_state.get("failed_dates") != {}
        or weather_state.get("availability_registration") != expected_state_registration
        or weather_state.get("availability_policy_id") != V7Y_WEATHER_POLICY_ID
        or weather_state.get("normalized_output_root") != V7Y_WEATHER_OUTPUT_ROOT.as_posix()
        or weather_state.get("availability_repair_parent_recovery_sha256")
        != repair_prestate["prior_recovery_sha256"]
    ):
        raise StudyError("all 184 HRRR/GEFS backfill and V7Y normalization dates must complete before freeze")
    universe_path = root / UNIVERSE
    universe = _read(universe_path)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in universe["contracts"]:
        if START.isoformat() <= row["climate_date"] <= END.isoformat():
            if (
                row.get("market_type") != "binary"
                or row.get("status") != "finalized"
                or row.get("station_identity_screen") is not True
                or row.get("event_ticker") != "KXHIGHLAX-" + date.fromisoformat(row["climate_date"]).strftime("%y%b%d").upper()
                or any(key in row for key in ("result", "outcome", "settlement_value", "expiration_value"))
            ):
                raise StudyError(f"unsafe or ineligible Kalshi universe row: {row.get('ticker')}")
            by_day[row["climate_date"]].append(_normalize_contract(row))
    expected_days = _days()
    if sorted(by_day) != expected_days or any(len(rows) != 6 for rows in by_day.values()):
        raise StudyError("2025 Kalshi six-bracket universe differs")
    for day, rows in by_day.items():
        if any(sum(_contains(row, temperature) for row in rows) != 1 for temperature in range(-30, 141)):
            raise StudyError(f"Kalshi contract intervals do not partition integer temperatures: {day}")

    v5b_model = _v5b_model_definition(root)
    bindings = {
        UNIVERSE.as_posix(): _hash(universe_path),
        V5B_MODEL_SOURCE.as_posix(): v5b_model["source_sha256"],
        registration["path"]: registration["sha256"],
        repair_prestate["path"]: repair_prestate["sha256"],
        V7Y_WEATHER_STATE.as_posix(): _hash(weather_state_path),
    }
    records: list[dict[str, Any]] = []
    for index, day in enumerate(expected_days, 1):
        contracts = _ordered_contracts(by_day[day])
        h_path, g_path, manifest_path = _weather_paths(root, day)
        friend_path = root / FRIEND_ARCHIVE / f"date={day}" / "daily.json"
        missing = [path for path in (h_path, g_path, manifest_path, friend_path) if not path.is_file()]
        if missing:
            raise StudyError(f"weather inputs missing for {day}: {missing[0]}")
        hrrr, gefs, _ = _weather_frames(root, day)
        model_contracts = [{**row, "market_ticker": row["ticker"]} for row in contracts]
        probabilities, metadata = _v5b_probabilities(root, day, model_contracts, hrrr, gefs)
        if (
            metadata.get("fitted_model_sha256") != v5b_model["fitted_model_sha256"]
            or metadata.get("source_path") != v5b_model["source_path"]
            or metadata.get("source_sha256") != v5b_model["source_sha256"]
            or metadata.get("target_updates_used") is not False
        ):
            raise StudyError(f"V5B probability-model identity differs: {day}")
        h_values = _temperature_values(hrrr)
        g_values = _temperature_values(gefs, member="avg")
        if len(h_values) != 4 or len(g_values) != 8:
            raise StudyError(f"HRRR/GEFS temperature sample count differs: {day}")
        temperatures = {
            "hrrr_sample_max_f": max(h_values),
            "gefs_mean_sample_max_f": max(g_values),
            **_friend_values(friend_path, day),
        }
        records.append({
            "climate_date": day,
            "partition": "calibration_overlap_diagnostic" if day < PRIMARY_START.isoformat() else "post_calibration_primary",
            "contracts": [
                {
                    "ticker": row["ticker"],
                    "strike_type": row["strike_type"],
                    "floor_strike": row["floor_strike"],
                    "cap_strike": row["cap_strike"],
                    "bounds_source": row["bounds_source"],
                }
                for row in contracts
            ],
            "v5b_yes_probabilities": probabilities,
            "v5b_regime": metadata["regime"],
            "v5b_fitted_model_sha256": metadata["fitted_model_sha256"],
            "temperatures": temperatures,
        })
        for path in (h_path, g_path, manifest_path, friend_path):
            bindings[path.relative_to(root).as_posix()] = _hash(path)
        if index % 25 == 0:
            print(json.dumps({"frozen_dates": index, "target": len(expected_days)}), flush=True)

    document = _seal({
        "schema_version": "klax-v7y-calendar-weather-freeze-v2",
        "status": "FROZEN_BEFORE_CLILAX_LABEL_READ",
        "study_year": 2025,
        "target_dates": expected_days,
        "target_date_count": len(expected_days),
        "calibration_overlap": {"start": "2025-01-05", "end": "2025-02-03", "count": 30},
        "primary": {"start": "2025-02-04", "end": "2025-12-31", "count": 331},
        "contract_bounds_provenance": {
            "kalshi_archive_rows": sum(
                row["bounds_source"] == "kalshi_archive_bounds_cross_checked_to_yes_sub_title"
                for rows in by_day.values() for row in rows
            ),
            "text_reconstructed_rows": sum(
                row["bounds_source"] == "parsed_yes_sub_title_due_to_missing_archive_bounds"
                for rows in by_day.values() for row in rows
            ),
            "reconstruction_used_outcomes": False,
        },
        "v5b_probability_model": v5b_model,
        "raw_temperature_methods": RAW_TEMPERATURE_METHODS,
        "raw_temperature_comparison_class": "non_equivalent_fixed_source_diagnostics",
        "availability_policy_id": V7Y_WEATHER_POLICY_ID,
        "availability_policy_registration": policy_registration_binding(registration),
        "availability_repair_prestate": repair_prestate,
        "weather_recovery_sha256": weather_state["recovery_sha256"],
        "availability_repair_parent_recovery_sha256": weather_state[
            "availability_repair_parent_recovery_sha256"
        ],
        "records": records,
        "input_bindings": dict(sorted(bindings.items())),
        "outcomes_read": False,
        "clilax_labels_read": False,
        "market_prices_read": False,
        "level2_execution_available": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "limitations": [
            "The frozen V5B policy was selected in later research, so this is a retrospective signal replay rather than a contemporaneously registered 2025 trading test.",
            "The first 30 dates were used to fit bracket calibration and are diagnostic only.",
            "Post-calibration means February 4 onward was not used in this fitted state's 30-row isotonic calibration; it does not make the inherited architecture or policy independent of earlier research selection.",
            "Twenty-five source-universe rows omit numeric bounds; those bounds are deterministically reconstructed from the outcome-blind yes-subtitle and cross-checked to the ticker before prediction.",
            "GFS/NAM/NBM rows are fixed raw temperature diagnostics with an equal-weight mean; no 2025 outcome-driven recalibration or regime selection is performed.",
            "Raw HRRR, GEFS, GFS, NAM, and NBM high estimates use different field types, time grids, and climate-day coverage; MAE comparisons rank these fixed constructions only and do not rank the underlying forecast systems on equal inputs.",
            "No 2025 Probalytics Level-2 archive exists, so this study cannot estimate verified fills or realized account returns.",
        ],
    })
    _write_immutable(root / FREEZE, document)
    return document


def _labels(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    manifest_path = root / CLIMATE_MANIFEST
    manifest = _read(manifest_path)
    archive_path = root / manifest["path"]
    if _hash(archive_path) != manifest["sha256"]:
        raise StudyError("CLILAX archive binding differs")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            row = parse_product(member.filename, archive.read(member))
            if row is not None and START.isoformat() <= row["climate_date"] <= END.isoformat():
                groups[row["climate_date"]].append(row)
    labels = {
        day: max(rows, key=lambda row: row["issued_at"])
        for day, rows in groups.items()
    }
    if sorted(labels) != _days():
        missing = sorted(set(_days()) - set(labels))
        raise StudyError(f"CLILAX year coverage differs; missing={missing[:5]}")
    return labels, {
        CLIMATE_MANIFEST.as_posix(): _hash(manifest_path),
        Path(manifest["path"]).as_posix(): _hash(archive_path),
    }


def _contains(contract: dict[str, Any], temperature: float) -> bool:
    rounded = round_fahrenheit(temperature)
    strike = contract["strike_type"]
    if strike == "less":
        return rounded < int(contract["cap_strike"])
    if strike == "between":
        return int(contract["floor_strike"]) <= rounded <= int(contract["cap_strike"])
    if strike == "greater":
        return rounded > int(contract["floor_strike"])
    raise StudyError(f"unsupported strike type: {strike}")


def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def _temperature_summary(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    errors = [float(row[model]) - float(row["actual_high_f"]) for row in rows]
    return {
        "model": model,
        "comparison_class": "non_equivalent_fixed_source_diagnostic",
        "family": RAW_TEMPERATURE_METHODS[model]["family"],
        "construction": RAW_TEMPERATURE_METHODS[model]["construction"],
        "dates": len(errors),
        "bias_f": statistics.mean(errors),
        "mae_f": statistics.mean(abs(error) for error in errors),
        "rmse_f": math.sqrt(statistics.mean(error * error for error in errors)),
        "within_1f": statistics.mean(abs(error) <= 1 for error in errors),
        "within_2f": statistics.mean(abs(error) <= 2 for error in errors),
        "bracket_accuracy": statistics.mean(bool(row[f"{model}_bracket_hit"]) for row in rows),
    }


def _probability_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    uniform_brier = 5.0 / 6.0
    uniform_log_loss = math.log(6.0)
    mean_brier = statistics.mean(float(row["v5b_multiclass_brier"]) for row in rows)
    mean_log_loss = statistics.mean(float(row["v5b_log_loss"]) for row in rows)
    return {
        "dates": len(rows),
        "mean_multiclass_brier": mean_brier,
        "uniform_multiclass_brier": uniform_brier,
        "multiclass_brier_skill_vs_uniform": 1.0 - mean_brier / uniform_brier,
        "mean_log_loss": mean_log_loss,
        "uniform_log_loss": uniform_log_loss,
        "log_loss_improvement_vs_uniform": uniform_log_loss - mean_log_loss,
        "modal_bracket_accuracy": statistics.mean(bool(row["v5b_modal_bracket_hit"]) for row in rows),
        "mean_true_bracket_probability": statistics.mean(float(row["v5b_true_bracket_probability"]) for row in rows),
    }


def _reliability_rows(observations: list[dict[str, Any]], partition: str) -> list[dict[str, Any]]:
    selected = [row for row in observations if row["partition"] == partition]
    result: list[dict[str, Any]] = []
    for index in range(10):
        lower, upper = index / 10.0, (index + 1) / 10.0
        cohort = [
            row for row in selected
            if lower <= float(row["probability"]) < upper
            or (index == 9 and float(row["probability"]) == 1.0)
        ]
        if not cohort:
            continue
        mean_probability = statistics.mean(float(row["probability"]) for row in cohort)
        outcome_rate = statistics.mean(float(row["outcome"]) for row in cohort)
        result.append({
            "partition": partition,
            "bin": index,
            "lower_inclusive": lower,
            "upper_inclusive_only_for_last_bin": upper,
            "contract_day_count": len(cohort),
            "mean_probability": mean_probability,
            "outcome_rate": outcome_rate,
            "calibration_gap": outcome_rate - mean_probability,
            "absolute_calibration_gap": abs(outcome_rate - mean_probability),
        })
    return result


def _expected_calibration_error(rows: list[dict[str, Any]]) -> float:
    total = sum(int(row["contract_day_count"]) for row in rows)
    return sum(
        int(row["contract_day_count"]) * float(row["absolute_calibration_gap"])
        for row in rows
    ) / total


def score(root: Path) -> dict[str, Any]:
    freeze_path = root / FREEZE
    frozen = _read(freeze_path)
    _validate_prediction_freeze(root, frozen)
    scored_outputs = (SCORED, SUMMARY, MONTHLY, PROBABILITY_PERIODS, RELIABILITY, REPORT)
    existing_outputs = [path for path in scored_outputs if (root / path).exists()]
    if existing_outputs:
        raise StudyError(f"existing immutable scored artifact refuses overwrite: {existing_outputs[0]}")
    labels, label_bindings = _labels(root)
    scored: list[dict[str, Any]] = []
    probability_observations: list[dict[str, Any]] = []
    for record in frozen["records"]:
        day = record["climate_date"]
        actual = int(labels[day]["tmax_f"])
        winner = [row for row in record["contracts"] if _contains(row, actual)]
        if len(winner) != 1:
            raise StudyError(f"actual high did not map to one bracket: {day}")
        winner_ticker = winner[0]["ticker"]
        probabilities = record["v5b_yes_probabilities"]
        true_probability = max(float(probabilities[winner_ticker]), 1e-15)
        brier = sum(
            (float(probability) - (1.0 if ticker == winner_ticker else 0.0)) ** 2
            for ticker, probability in probabilities.items()
        )
        modal = max(probabilities, key=lambda ticker: (float(probabilities[ticker]), ticker))
        row: dict[str, Any] = {
            "climate_date": day,
            "month": day[:7],
            "season": _season(int(day[5:7])),
            "partition": record["partition"],
            "actual_high_f": actual,
            "winning_ticker": winner_ticker,
            "v5b_multiclass_brier": brier,
            "v5b_log_loss": -math.log(true_probability),
            "v5b_true_bracket_probability": true_probability,
            "v5b_modal_bracket_hit": modal == winner_ticker,
        }
        row.update(record["temperatures"])
        for ticker, probability in probabilities.items():
            probability_observations.append({
                "climate_date": day,
                "partition": record["partition"],
                "ticker": ticker,
                "probability": float(probability),
                "outcome": 1 if ticker == winner_ticker else 0,
            })
        for model in TEMP_MODELS:
            predicted = float(row[model])
            predicted_contracts = [contract for contract in record["contracts"] if _contains(contract, predicted)]
            row[f"{model}_bracket_hit"] = len(predicted_contracts) == 1 and predicted_contracts[0]["ticker"] == winner_ticker
        scored.append(row)

    primary = [row for row in scored if row["partition"] == "post_calibration_primary"]
    overlap = [row for row in scored if row["partition"] == "calibration_overlap_diagnostic"]
    raw_temperature_primary = sorted(
        (_temperature_summary(primary, model) for model in TEMP_MODELS),
        key=lambda item: (item["mae_f"], item["rmse_f"], item["model"]),
    )
    temperature_period_rows: list[dict[str, Any]] = []
    probability_period_rows: list[dict[str, Any]] = []
    for month in sorted({row["month"] for row in primary}):
        cohort = [row for row in primary if row["month"] == month]
        for model in TEMP_MODELS:
            temperature_period_rows.append({"period_type": "month", "period": month, **_temperature_summary(cohort, model)})
        probability_period_rows.append({"period_type": "month", "period": month, **_probability_summary(cohort)})
    for season in ("winter", "spring", "summer", "fall"):
        cohort = [row for row in primary if row["season"] == season]
        for model in TEMP_MODELS:
            temperature_period_rows.append({"period_type": "season", "period": season, **_temperature_summary(cohort, model)})
        probability_period_rows.append({"period_type": "season", "period": season, **_probability_summary(cohort)})

    primary_reliability = _reliability_rows(probability_observations, "post_calibration_primary")
    overlap_reliability = _reliability_rows(probability_observations, "calibration_overlap_diagnostic")
    primary_probability = {
        **_probability_summary(primary),
        "expected_calibration_error_10_bins": _expected_calibration_error(primary_reliability),
    }
    overlap_probability = {
        **_probability_summary(overlap),
        "expected_calibration_error_10_bins": _expected_calibration_error(overlap_reliability),
    }

    summary = _seal({
        "schema_version": "klax-v7y-calendar-weather-score-v1",
        "status": "COMPLETE",
        "inference_class": "retrospective_post_calibration_stability_study_not_independent_confirmation",
        "prediction_freeze_sha256": frozen["self_sha256"],
        "scored_date_count": len(scored),
        "primary_date_count": len(primary),
        "calibration_overlap_date_count": len(overlap),
        "primary_raw_temperature_diagnostics": raw_temperature_primary,
        "raw_temperature_comparison_class": "non_equivalent_fixed_source_diagnostics",
        "raw_temperature_methods": RAW_TEMPERATURE_METHODS,
        "primary_v5b_probability_metrics": primary_probability,
        "overlap_v5b_probability_metrics": overlap_probability,
        "label_bindings": label_bindings,
        "economic_result": None,
        "economic_result_reason": "2025 historical Level-2 books are unavailable; forecast skill cannot be converted into verified fills or realized return.",
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })

    out = root / RUN
    out.mkdir(parents=True, exist_ok=True)
    with (root / SCORED).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(scored[0]))
        writer.writeheader()
        writer.writerows(scored)
    with (root / MONTHLY).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(temperature_period_rows[0]))
        writer.writeheader()
        writer.writerows(temperature_period_rows)
    with (root / PROBABILITY_PERIODS).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(probability_period_rows[0]))
        writer.writeheader()
        writer.writerows(probability_period_rows)
    with (root / RELIABILITY).open("w", encoding="utf-8", newline="") as stream:
        reliability_rows = primary_reliability + overlap_reliability
        writer = csv.DictWriter(stream, fieldnames=list(reliability_rows[0]))
        writer.writeheader()
        writer.writerows(reliability_rows)
    (root / SUMMARY).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lowest_mae_diagnostic = raw_temperature_primary[0]
    probability = summary["primary_v5b_probability_metrics"]
    report = f"""# V7Y calendar-2025 LAX weather-model study

## Result

The fixed study scored **{len(primary)} post-calibration dates** from February 4 through December 31, 2025. The lowest-MAE raw source construction was **{lowest_mae_diagnostic['model']}**, with **{lowest_mae_diagnostic['mae_f']:.2f}°F MAE**, **{lowest_mae_diagnostic['rmse_f']:.2f}°F RMSE**, and **{lowest_mae_diagnostic['bracket_accuracy']:.1%}** deterministic bracket accuracy. This is a diagnostic comparison of fixed constructions, not an equal-input ranking of the underlying weather systems.

The frozen HRRR/GEFS probability model produced a **{probability['mean_multiclass_brier']:.4f} multiclass Brier score**, **{probability['mean_log_loss']:.4f} log loss**, **{probability['modal_bracket_accuracy']:.1%} modal-bracket accuracy**, and assigned **{probability['mean_true_bracket_probability']:.1%}** average probability to the winning bracket. Its Brier skill versus a fixed uniform six-bracket forecast was **{probability['multiclass_brier_skill_vs_uniform']:.1%}**; its 10-bin expected calibration error was **{probability['expected_calibration_error_10_bins']:.1%}**.

## Raw temperature diagnostics

| Rank | Fixed method | Dates | MAE °F | RMSE °F | Bias °F | Within 2°F | Bracket hit |
|---:|---|---:|---:|---:|---:|---:|---:|
"""
    for index, item in enumerate(raw_temperature_primary, 1):
        report += f"| {index} | {item['model']} | {item['dates']} | {item['mae_f']:.2f} | {item['rmse_f']:.2f} | {item['bias_f']:+.2f} | {item['within_2f']:.1%} | {item['bracket_accuracy']:.1%} |\n"
    report += """

## Interpretation

The raw temperature rows share the same LAX daily-high target and dates, but they do not share the same field type or time sampling. HRRR, GEFS, NAM, and NBM use sampled instantaneous temperatures; GFS uses six-hour TMAX fields; their grids and climate-day boundary coverage also differ. These rows compare the exact fixed inputs available to each method and cannot establish that one underlying numerical weather model is intrinsically superior.

This study measures forecast signals, not trade profitability. Probalytics historical Level-2 coverage starts in May 2026, so no 2025 execution replay can establish fills, queue position, or realized return. The frozen V5B policy was also selected after 2025, making this a retrospective stability study rather than untouched confirmation. Raw temperature monthly and seasonal diagnostics are in `monthly-metrics.csv`; probability month/season metrics are in `probability-period-metrics.csv`; reliability bins are in `probability-reliability.csv`; daily audit rows are in `scored-days.csv`.

The first 30 dates overlap the probability model's bracket calibration and are reported separately. During this run, 2025 outcomes are opened only after all 361 predictions are sealed and are never used to alter source definitions, weights, thresholds, or regimes. The inherited architecture and policy were selected in later research, so “post-calibration” is not the same as independent confirmation. No paper or live orders were created.
"""
    (root / REPORT).write_text(report, encoding="utf-8")
    return summary


def status(root: Path) -> dict[str, Any]:
    hrrr_state = root / "runs/v7y_weather_backfill/recovery-state.json"
    friend_state = root / "runs/v7y_friend_weather_backfill/recovery-state.json"
    return {
        "hrrr_gefs": _read(hrrr_state) if hrrr_state.is_file() else None,
        "gfs_nam_nbm": _read(friend_state) if friend_state.is_file() else None,
        "prediction_freeze_exists": (root / FREEZE).is_file(),
        "summary_exists": (root / SUMMARY).is_file(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "score", "status"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    result = freeze(root) if args.action == "freeze" else score(root) if args.action == "score" else status(root)
    if args.action == "status":
        def compact(state: dict[str, Any] | None) -> Any:
            if state is None:
                return None
            return {
                "status": state.get("status"),
                "completed": len(state.get("completed_dates", [])),
                "unavailable": len(state.get("unavailable_dates", [])),
                "failed": len(state.get("failed_dates", {})),
                "target": state.get("target_date_count"),
                "recovery_sha256": state.get("recovery_sha256"),
            }
        result = {
            "hrrr_gefs": compact(result["hrrr_gefs"]),
            "gfs_nam_nbm": compact(result["gfs_nam_nbm"]),
            "prediction_freeze_exists": result["prediction_freeze_exists"],
            "summary_exists": result["summary_exists"],
        }
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
