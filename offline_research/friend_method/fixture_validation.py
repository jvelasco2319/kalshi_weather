"""Validation for the legacy four-model fixture supplied with the friend method."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any


REQUIRED_MODELS = ("gfs", "gfs_seamless", "nam", "nbm")


def _numeric_signature(model: dict[str, Any]) -> str:
    """Hash numerical content while ignoring source path names."""
    high = model.get("daily_high", {})
    samples = [
        {
            "valid_time": sample.get("valid_time"),
            "temperature_f": sample.get("temperature_f"),
            "grid_latitude": sample.get("grid_latitude"),
            "grid_longitude": sample.get("grid_longitude"),
        }
        for sample in high.get("samples", [])
    ]
    payload = {
        "mean": model.get("mean"),
        "std": model.get("std"),
        "distribution": model.get("distribution"),
        "daily_high_f": high.get("daily_high_f"),
        "samples": samples,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_fixture(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    fixture = json.loads(raw.decode("utf-8-sig"))
    errors: list[str] = []
    warnings: list[str] = []
    metadata = fixture.get("metadata", {})
    models = fixture.get("models", {})

    if metadata.get("variable") != "daily_high_temperature_f":
        errors.append("unsupported_variable")
    try:
        datetime.fromisoformat(str(metadata["initialization"]))
        datetime.fromisoformat(str(metadata["target_date"]))
    except (KeyError, TypeError, ValueError):
        errors.append("invalid_target_or_initialization_time")

    missing = [name for name in REQUIRED_MODELS if name not in models]
    if missing:
        errors.append("missing_models:" + ",".join(missing))

    model_reports: dict[str, dict[str, Any]] = {}
    signatures: dict[str, list[str]] = {}
    for name in REQUIRED_MODELS:
        if name not in models:
            continue
        model = models[name]
        mean = model.get("mean")
        std = model.get("std")
        if not isinstance(mean, (int, float)) or not math.isfinite(mean):
            errors.append(f"{name}:invalid_mean")
        if not isinstance(std, (int, float)) or not math.isfinite(std) or std <= 0:
            errors.append(f"{name}:invalid_std")

        pmf = model.get("distribution", {})
        mass = 0.0
        for key, value in pmf.items():
            try:
                int(key)
            except (TypeError, ValueError):
                errors.append(f"{name}:invalid_pmf_bin")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                errors.append(f"{name}:invalid_pmf_mass")
            else:
                mass += float(value)
        if abs(mass - 1.0) > 1e-6:
            errors.append(f"{name}:pmf_mass_{mass:.12f}")

        high = model.get("daily_high", {})
        samples = high.get("samples", [])
        seen: set[tuple[Any, ...]] = set()
        duplicate_samples = 0
        for sample in samples:
            identity = (
                sample.get("valid_time"), sample.get("temperature_f"),
                sample.get("grid_latitude"), sample.get("grid_longitude"),
            )
            if identity in seen:
                duplicate_samples += 1
            seen.add(identity)
            try:
                datetime.fromisoformat(str(sample["valid_time"]))
            except (KeyError, TypeError, ValueError):
                errors.append(f"{name}:invalid_sample_time")
        if duplicate_samples:
            errors.append(f"{name}:duplicate_samples_{duplicate_samples}")

        signature = _numeric_signature(model)
        signatures.setdefault(signature, []).append(name)
        model_reports[name] = {
            "mean_f": mean,
            "std_f": std,
            "pmf_bin_count": len(pmf),
            "pmf_mass": mass,
            "sample_count": len(samples),
            "numeric_signature": signature,
        }

    groups = list(signatures.values())
    groups.sort(key=lambda group: REQUIRED_MODELS.index(group[0]))
    if ["gfs", "gfs_seamless"] not in groups:
        errors.append("gfs_gfs_seamless_duplicate_not_detected")

    location = metadata.get("location", {})
    if "station_id" not in location:
        warnings.append("settlement_station_identity_missing")
    warnings.extend([
        "forecast_availability_time_missing",
        "raw_grib_provenance_not_verified",
        "reporting_window_coverage_not_verified",
        "fixed_two_degree_uncertainty_is_uncalibrated",
        "single_target_date_cannot_fit_or_evaluate_strategy",
        "no_historical_order_book_or_settlement_label_in_fixture",
    ])

    expected_counts = {"gfs": 3, "gfs_seamless": 3, "nam": 8, "nbm": 24}
    counts_match = all(model_reports.get(name, {}).get("sample_count") == count for name, count in expected_counts.items())
    if not counts_match:
        errors.append("unexpected_sample_counts")

    return {
        "schema": "friend-method-fixture-validation-v1",
        "fixture_path": path.name,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "fixture_bytes": len(raw),
        "target_date": metadata.get("target_date"),
        "initialization": metadata.get("initialization"),
        "model_reports": model_reports,
        "effective_source_groups": groups,
        "effective_source_count": len(groups),
        "expected_sample_counts_match": counts_match,
        "errors": errors,
        "warnings": warnings,
        "conformance_passed": not errors,
        "research_only": True,
        "live_eligible": False,
        "profitability_evidence": False,
    }
