"""Prepare currently available V3 inputs without reading protected-final data.

This module is deliberately development-only.  It converts the already
audited January-June 2025 one-minute market cache into normalized Parquet and
creates 2024 weather-training targets from the selected complete CLILAX daily
reports.  It performs no network access and has no protected-final entry point.
"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from .dataset import (
    _read_verified, _write_parquet, normalize_candlestick,
    normalize_public_trade,
)
from .provenance import canonical_hash, sha256_file, write_json


TRAINING_START = date(2024, 1, 1)
TRAINING_END = date(2024, 12, 31)
DEVELOPMENT_START = date(2025, 1, 5)
DEVELOPMENT_END = date(2025, 6, 30)
MARKET_OUTPUTS = (
    Path("data/normalized/selection/features/candles_1m.parquet"),
    Path("data/normalized/selection/features/trades.parquet"),
)
DEVELOPMENT_SETTLEMENT_MANIFEST = Path("data/normalized/v3_development/manifest.json")
DEVELOPMENT_SETTLEMENT_COMPONENT = Path("data/manifests/v3_settlement_reconciliation.json")
DEVELOPMENT_SETTLEMENT_CORRECTION = Path(
    "data/manifests/v3_development_manifest_synchronization_correction.json"
)
DEVELOPMENT_SETTLEMENT_KEYS = (
    "schema_version", "built_at_utc", "network_used", "partitions", "inputs", "rules",
)
DEVELOPMENT_SETTLEMENT_CORRECTION_INVARIANTS = {
    "authoritative_manifest_changed": False,
    "development_scope_changed": False,
    "network_used": False,
    "normalized_or_label_data_changed": False,
    "protected_final_read": False,
    "scientific_or_evaluation_policy_changed": False,
    "settlement_exclusions_changed": False,
    "settlement_targets_changed": False,
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"Missing or invalid V3 input: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"V3 input must be an object: {path}")
    return value


def _project_path(root: Path, value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError(f"Invalid {label} path")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the project") from exc
    lowered = {part.casefold().replace("-", "_") for part in path.parts}
    if {"protected_final", "holdout"} & lowered:
        raise ValueError(f"{label} path enters protected-final storage")
    return path


def _development_settlement_projection(component: dict[str, Any]) -> dict[str, Any]:
    """Return the adjacent output manifest recorded by the settlement builder."""
    return {key: component.get(key) for key in DEVELOPMENT_SETTLEMENT_KEYS}


def _verify_development_settlement_mirror(root: Path, component: dict[str, Any]) -> Path:
    """Require the adjacent output manifest to match the authoritative component.

    The settlement builder writes both records from the same in-memory manifest.
    Refusing to bind new training labels when they have diverged prevents a stale
    adjacent manifest from silently surviving another preparation run.
    """
    path = root / DEVELOPMENT_SETTLEMENT_MANIFEST
    mirror = _read_json(path)
    if mirror != _development_settlement_projection(component):
        raise ValueError(
            "Development settlement output manifest differs from its authoritative component"
        )
    correction_path = root / DEVELOPMENT_SETTLEMENT_CORRECTION
    if correction_path.exists():
        correction = _read_json(correction_path)
        expected_records = {
            "authoritative_component": DEVELOPMENT_SETTLEMENT_COMPONENT,
            "corrected_manifest": DEVELOPMENT_SETTLEMENT_MANIFEST,
        }
        if (correction.get("schema_version") != 1
                or correction.get("component")
                != "v3_development_adjacent_manifest_synchronization_correction"
                or correction.get("status") != "STALE_ADJACENT_MANIFEST_CORRECTED"
                or correction.get("projection_keys") != list(DEVELOPMENT_SETTLEMENT_KEYS)
                or correction.get("invariants")
                != DEVELOPMENT_SETTLEMENT_CORRECTION_INVARIANTS
                or correction.get("network_used") is not False
                or correction.get("protected_final_read") is not False):
            raise ValueError("Development settlement manifest correction provenance differs")
        for name, expected_path in expected_records.items():
            record = correction.get(name)
            if not isinstance(record, dict) or record.get("path") != expected_path.as_posix():
                raise ValueError("Development settlement manifest correction binding differs")
            bound = _project_path(root, record["path"], f"{name} correction binding")
            if (record.get("bytes") != bound.stat().st_size
                    or record.get("sha256") != sha256_file(bound)):
                raise ValueError("Development settlement manifest correction hash differs")
        snapshot = correction.get("pre_repair_snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("Development settlement manifest correction lacks its snapshot")
        snapshot_path = _project_path(
            root, snapshot.get("path"), "development manifest pre-repair snapshot",
        )
        if (snapshot.get("bytes") != snapshot_path.stat().st_size
                or snapshot.get("sha256") != sha256_file(snapshot_path)):
            raise ValueError("Development settlement pre-repair snapshot hash differs")
    return path


def _source_index(root: Path) -> tuple[dict[str, dict[str, Any]], str]:
    path = root / "data/manifests/kalshi_downloads.json"
    raw = path.read_bytes()
    body = _read_json(path)
    sources = body.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("Kalshi download manifest lacks its source inventory")
    by_path: dict[str, dict[str, Any]] = {}
    for source in sources.values():
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("Malformed Kalshi source inventory")
        previous = by_path.get(source["path"])
        if previous is not None and previous != source:
            raise ValueError("Conflicting Kalshi source path inventory")
        by_path[source["path"]] = source
    return by_path, hashlib.sha256(raw).hexdigest()


def _verified_batch(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = _project_path(root, record.get("path"), "market batch")
    expected = record.get("sha256")
    if not isinstance(expected, str) or sha256_file(path) != expected:
        raise ValueError("Market batch manifest hash differs from V3 audit")
    return _read_json(path)


def prepare_development_minute_market(root: Path) -> dict[str, Any]:
    """Normalize the exact audited January-June market cache, offline."""
    root = Path(root).resolve()
    audit_path = root / "data/manifests/v3_minute_kalshi.json"
    audit = _read_json(audit_path)
    if (audit.get("status") != "DEVELOPMENT_COMPLETE"
            or audit.get("network_used") is not False
            or audit.get("protected_final_read") is not False
            or audit.get("start_date") != DEVELOPMENT_START.isoformat()
            or audit.get("end_date") != DEVELOPMENT_END.isoformat()):
        raise ValueError("Minute-market audit is not the complete development-only source")
    sources, downloads_sha = _source_index(root)

    candle_entries: dict[str, dict[str, Any]] = {}
    for batch_record in audit.get("candle_batches", []):
        batch = _verified_batch(root, batch_record)
        if batch.get("period_interval") != 1:
            raise ValueError("V3 candle batch is not one-minute history")
        for entry in batch.get("contracts", []):
            day = date.fromisoformat(entry["climate_date"])
            if not DEVELOPMENT_START <= day <= DEVELOPMENT_END:
                raise ValueError("V3 candle batch crossed the development interval")
            if entry.get("status") not in ("downloaded", "cached"):
                raise ValueError("A development one-minute candle source is unavailable")
            previous = candle_entries.get(entry["ticker"])
            if previous is not None and previous != entry:
                raise ValueError("Conflicting one-minute candle entries")
            candle_entries[entry["ticker"]] = entry

    trade_entries: dict[str, dict[str, Any]] = {}
    for batch_record in audit.get("trade_batches", []):
        batch = _verified_batch(root, batch_record)
        for entry in batch.get("contracts", []):
            day = date.fromisoformat(entry["climate_date"])
            if not DEVELOPMENT_START <= day <= DEVELOPMENT_END:
                raise ValueError("V3 trade batch crossed the development interval")
            if entry.get("status") not in ("downloaded", "empty"):
                raise ValueError("A development public-trade source is unavailable")
            previous = trade_entries.get(entry["ticker"])
            if previous is not None and previous != entry:
                raise ValueError("Conflicting public-trade entries")
            trade_entries[entry["ticker"]] = entry

    if (len(candle_entries) != audit.get("one_minute_candle_contracts")
            or len(trade_entries) != audit.get("public_trade_contracts")):
        raise ValueError("Normalized market contract inventory differs from the audit")

    candles: dict[tuple[str, int], dict[str, Any]] = {}
    for ticker, entry in sorted(candle_entries.items()):
        source = sources.get(entry["path"])
        if source is None or source.get("sha256") != entry.get("source_sha256"):
            raise ValueError("One-minute candle provenance is absent or inconsistent")
        if "period_interval=1" not in source.get("url", ""):
            raise ValueError("One-minute candle source endpoint differs")
        payload = _read_verified(root, entry)
        for raw in payload.get("candlesticks", []):
            row = normalize_candlestick(
                ticker, entry["climate_date"], "selection", raw, 1, source)
            key = (ticker, row["end_period_ts"])
            previous = candles.get(key)
            if previous is not None and previous != row:
                raise ValueError("Conflicting normalized one-minute candle")
            candles[key] = row

    trades: dict[str, dict[str, Any]] = {}
    for ticker, entry in sorted(trade_entries.items()):
        for page in entry.get("pages", []):
            source = sources.get(page["path"])
            if source is None or source.get("sha256") != page.get("source_sha256"):
                raise ValueError("Public-trade provenance is absent or inconsistent")
            if "/historical/trades" not in source.get("url", ""):
                raise ValueError("Public-trade source endpoint differs")
            payload = _read_verified(root, page)
            for raw in payload.get("trades", []):
                if raw.get("ticker") != ticker:
                    raise ValueError("Public trade ticker differs from its batch entry")
                row = normalize_public_trade(raw, entry["climate_date"], "selection", source)
                previous = trades.get(row["trade_id"])
                if previous is not None and previous != row:
                    raise ValueError("Conflicting normalized public trade")
                trades[row["trade_id"]] = row

    candle_rows = sorted(candles.values(), key=lambda row: (row["ticker"], row["end_period_ts"]))
    trade_rows = sorted(trades.values(), key=lambda row: (row["created_ts"], row["trade_id"]))
    if (len(candle_rows) != audit.get("one_minute_candle_rows")
            or len(trade_rows) != audit.get("public_trade_rows")):
        raise ValueError("Normalized market row counts differ from the complete audit")
    _write_parquet(root / MARKET_OUTPUTS[0], candle_rows)
    _write_parquet(root / MARKET_OUTPUTS[1], trade_rows)
    report = {
        "schema_version": 1,
        "component": "development_minute_market_normalization",
        "status": "DEVELOPMENT_ONLY_COMPLETE",
        "network_used": False,
        "protected_final_read": False,
        "source_audit_path": audit_path.relative_to(root).as_posix(),
        "source_audit_sha256": sha256_file(audit_path),
        "download_manifest_sha256": downloads_sha,
        "date_range": [DEVELOPMENT_START.isoformat(), DEVELOPMENT_END.isoformat()],
        "candle_rows": len(candle_rows),
        "trade_rows": len(trade_rows),
        "outputs": [
            {"path": path.as_posix(), "sha256": sha256_file(root / path)}
            for path in MARKET_OUTPUTS
        ],
        "limitations": [
            "One-minute candles are aggregate grade-B quote evidence without depth.",
            "Public prints are grade-C evidence and do not prove a hypothetical fill.",
        ],
    }
    report["evidence_sha256"] = canonical_hash(report)
    write_json(root / "data/manifests/v3_market_normalization.json", report)
    return report


def prepare_weather_training_targets(root: Path) -> dict[str, Any]:
    """Create 2024 CLILAX fitting targets and explicit missing-day exclusions."""
    import pyarrow.parquet as pq

    root = Path(root).resolve()
    source_path = root / "data/normalized/weather_training/labels/climate.parquet"
    rows = pq.read_table(source_path).to_pylist()
    targets: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = date.fromisoformat(row.get("climate_date", ""))
        if not TRAINING_START <= day <= TRAINING_END or row.get("partition") != "weather_training":
            raise ValueError("CLILAX training target crossed its registered partition")
        if (row.get("label_role") != "NWS_CLILAX_archival_copy"
                or not str(row.get("source_member", "")).startswith("CLILAX_")
                or type(row.get("tmax_f")) is not int
                or not isinstance(row.get("source_sha256"), str)
                or len(row["source_sha256"]) != 64):
            raise ValueError("Incomplete CLILAX weather-training target")
        if row["climate_date"] in by_day:
            raise ValueError("Duplicate selected CLILAX weather-training target")
        by_day[row["climate_date"]] = row
        targets.append({
            "schema_version": 1,
            "climate_date": row["climate_date"],
            "partition": "weather_training",
            "station": "KLAX",
            "reported_high_f": row["tmax_f"],
            "report_issued_at": row["issued_at"],
            "report_available_at": row["available_at"],
            "source_sha256": row["source_sha256"],
            "source_member": row["source_member"],
            "availability_status": row["availability_status"],
            "archive_sha256": row.get("archive_sha256"),
            "reconciliation_status": "passed",
            "label_role": "weather_model_fitting_only",
        })
    expected = {
        (TRAINING_START + timedelta(days=offset)).isoformat()
        for offset in range((TRAINING_END - TRAINING_START).days + 1)
    }
    missing = sorted(expected - set(by_day))
    exclusions = [{
        "climate_date": value,
        "partition": "weather_training",
        "reason": "no_selected_complete_CLILAX_report_in_frozen_archive",
    } for value in missing]
    if set(by_day) | set(missing) != expected or set(by_day) & set(missing):
        raise AssertionError("Weather-training targets and exclusions are not exhaustive")
    destination = root / "data/normalized/v3_development/weather_training/labels"
    target_path = destination / "settlement_targets.parquet"
    exclusions_path = destination / "settlement_target_exclusions.json"
    _write_parquet(target_path, sorted(targets, key=lambda row: row["climate_date"]))
    write_json(exclusions_path, exclusions)
    report = {
        "schema_version": 1,
        "component": "weather_training_settlement_targets",
        "status": "TRAINING_TARGETS_COMPLETE_WITH_EXPLICIT_EXCLUSIONS",
        "network_used": False,
        "protected_final_read": False,
        "source_path": source_path.relative_to(root).as_posix(),
        "source_sha256": sha256_file(source_path),
        "date_range": [TRAINING_START.isoformat(), TRAINING_END.isoformat()],
        "target_count": len(targets),
        "exclusion_count": len(exclusions),
        "target_path": target_path.relative_to(root).as_posix(),
        "target_sha256": sha256_file(target_path),
        "exclusions_path": exclusions_path.relative_to(root).as_posix(),
        "exclusions_sha256": sha256_file(exclusions_path),
        "limitations": [
            "The target is the selected complete CLILAX integer daily high.",
            "Archive issuance is an availability proxy; receipt latency is unverified.",
        ],
    }
    report["evidence_sha256"] = canonical_hash(report)
    report_path = root / "data/manifests/v3_weather_training_targets.json"
    write_json(report_path, report)
    _bind_weather_training_targets(root, report, report_path)
    return report


def _bind_weather_training_targets(
        root: Path, report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    """Replace the empty Kalshi-derived training slot with exact CLILAX labels.

    Kalshi contracts are required to reconcile selection outcomes but do not
    exist for the 2024 weather-model fitting period.  This binding keeps the
    settlement component exhaustive while preserving the independently
    reconciled 2025 selection partition.
    """
    component_path = root / DEVELOPMENT_SETTLEMENT_COMPONENT
    component = _read_json(component_path)
    if (component.get("component") != "settlement_reconciliation"
            or component.get("status") != "DEVELOPMENT_COMPLETE"
            or component.get("network_used") is not False
            or component.get("protected_final_read") is not False):
        raise ValueError("Settlement component is not a completed development-only source")
    partitions = component.get("partitions")
    if not isinstance(partitions, dict) or not isinstance(partitions.get("selection"), dict):
        raise ValueError("Settlement component lacks the reconciled selection partition")
    mirror_path = _verify_development_settlement_mirror(root, component)
    selection = partitions["selection"]
    for prefix in ("target", "exclusions"):
        path = _project_path(root, selection.get(f"{prefix}_path"), f"selection {prefix}")
        if sha256_file(path) != selection.get(f"{prefix}_sha256"):
            raise ValueError("Reconciled selection target binding changed")
    for path_key, hash_key in (
            ("target_path", "target_sha256"),
            ("exclusions_path", "exclusions_sha256"),
            ("source_path", "source_sha256")):
        path = _project_path(root, report.get(path_key), f"weather-training {path_key}")
        if sha256_file(path) != report.get(hash_key):
            raise ValueError("Weather-training target binding changed")

    inputs = []
    for record in component.get("inputs", []):
        path_value = record.get("path") if isinstance(record, dict) else None
        if not isinstance(path_value, str):
            raise ValueError("Settlement component has a malformed input record")
        if (record.get("table") not in {
                "weather_training_climate_labels", "weather_training_target_manifest"}
                and "/weather_training/" not in "/" + path_value.replace("\\", "/")):
            inputs.append(record)
    inputs.extend([
        {
            "table": "weather_training_climate_labels",
            "path": report["source_path"],
            "sha256": report["source_sha256"],
        },
        {
            "table": "weather_training_target_manifest",
            "path": report_path.relative_to(root).as_posix(),
            "sha256": sha256_file(report_path),
        },
    ])
    partitions["weather_training"] = {
        "eligible_targets": report["target_count"],
        "excluded_dates": report["exclusion_count"],
        "target_path": report["target_path"],
        "target_sha256": report["target_sha256"],
        "exclusions_path": report["exclusions_path"],
        "exclusions_sha256": report["exclusions_sha256"],
        "label_role": "weather_model_fitting_only",
    }
    component["inputs"] = inputs
    component["eligible_target_count"] = sum(
        int(value["eligible_targets"]) for value in partitions.values())
    component["excluded_date_count"] = sum(
        int(value["excluded_dates"]) for value in partitions.values())
    component["weather_training_target_manifest_path"] = report_path.relative_to(root).as_posix()
    component["weather_training_target_manifest_sha256"] = sha256_file(report_path)
    binding_rule = (
        "2024 fitting labels come from one selected complete CLILAX report per day; "
        "Kalshi outcome reconciliation applies to the 2025 selection partition"
    )
    if binding_rule not in component["rules"]:
        component["rules"].append(binding_rule)
    write_json(mirror_path, _development_settlement_projection(component))
    write_json(component_path, component)
    return component


def prepare_available_inputs(root: Path) -> dict[str, Any]:
    training = prepare_weather_training_targets(root)
    market = prepare_development_minute_market(root)
    return {
        "status": "AVAILABLE_V3_INPUTS_PREPARED",
        "network_used": False,
        "protected_final_read": False,
        "weather_training_targets": training,
        "development_minute_market": market,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(prepare_available_inputs(args.root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
