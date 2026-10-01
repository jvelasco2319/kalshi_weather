"""Build and verify the prospective V4 13:30/15:00/18:00 offline freeze.

This adapter reuses the frozen V3 normalizers and dataset builder without
changing any V3 source or artifact.  It has no acquisition path and publishes
only V4-specific manifests after the new dataset verifies.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping

from klax_lab import freeze_pipeline_v3 as source
from klax_lab.dataset_v3 import (
    build_frozen_development_dataset, verify_frozen_development_dataset,
)
from klax_lab.provenance import canonical_hash, sha256_file
from klax_lab.weather_normalize_v3 import GEFS_MEMBERS, HRRR_FIELDS


DECISION_TIMES_UTC = ("13:30", "15:00", "18:00")
DESTINATION = Path("data/frozen/v4_development_1330_1500_1800")
DATASET_COMPONENT = Path("data/manifests/v4_dataset_1330_1500_1800.json")
FOLD_COMPONENT = Path("data/manifests/v4_five_fold_split_1330_1500_1800.json")
BUNDLE = Path("data/manifests/v4_data_bundle_1330_1500_1800.json")
DECISION_TIME_AMENDMENT = Path("v4/config/decision_time_amendment.json")


class V4DatasetError(ValueError):
    """The V4 prospective freeze or its local source bindings are invalid."""


def _immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    contents = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
                + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != contents:
            raise V4DatasetError(f"Cannot overwrite a different V4 artifact: {path}")
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(contents)
    temporary.replace(path)


def _load_bound_inputs(root: Path) -> tuple[dict[str, list[dict[str, Any]]],
                                              dict[str, dict[str, Any]]]:
    """Load the already-finalized local V3 inputs through their exact manifests."""
    source_manifest, hrrr, gefs = source._require_finalized_source(root)
    forecast_rows, forecast_artifacts = source._load_weather_rows(root, hrrr, gefs)

    training_target_manifest = source._read_json(
        root, source.TRAINING_TARGET_MANIFEST, "weather-training target manifest")
    settlement_manifest = source._read_json(
        root, source.SETTLEMENT_MANIFEST, "settlement manifest")
    market_manifest = source._read_json(
        root, source.MARKET_MANIFEST, "market normalization manifest")
    minute_manifest = source._read_json(
        root, source.MINUTE_SOURCE_MANIFEST, "minute-market source manifest")
    local_manifest = source._read_json(
        root, source.LOCAL_OBSERVATION_MANIFEST, "local-observation manifest")

    if market_manifest.get("source_audit_sha256") != sha256_file(source._inside(
            root, source.MINUTE_SOURCE_MANIFEST, "minute-market source manifest")):
        raise V4DatasetError("Minute-market normalization has a stale source binding")
    downloads = Path("data/manifests/kalshi_downloads.json")
    if market_manifest.get("download_manifest_sha256") != sha256_file(source._inside(
            root, downloads, "Kalshi download manifest")):
        raise V4DatasetError("Minute-market normalization has a stale download binding")

    if (training_target_manifest.get("status")
            != "TRAINING_TARGETS_COMPLETE_WITH_EXPLICIT_EXCLUSIONS"
            or training_target_manifest.get("target_path")
            != source.TRAINING_TARGET.as_posix()
            or training_target_manifest.get("target_count") != 365):
        raise V4DatasetError("Weather-training targets are incomplete or moved")
    training_targets, training_target_artifact = source._load_exact_parquet(
        root, source.TRAINING_TARGET,
        expected_sha256=str(training_target_manifest.get("target_sha256")),
        expected_rows=365,
    )
    selection = settlement_manifest.get("partitions", {}).get("selection", {})
    if (settlement_manifest.get("status") != "DEVELOPMENT_COMPLETE"
            or selection.get("target_path") != source.DEVELOPMENT_TARGET.as_posix()
            or selection.get("eligible_targets") != 175):
        raise V4DatasetError("Development settlement targets are incomplete or moved")
    development_targets, development_target_artifact = source._load_exact_parquet(
        root, source.DEVELOPMENT_TARGET,
        expected_sha256=str(selection.get("target_sha256")), expected_rows=175,
    )

    market_output = {
        item.get("path"): item for item in market_manifest.get("outputs", [])
        if isinstance(item, dict)
    }
    if (market_manifest.get("status") != "DEVELOPMENT_ONLY_COMPLETE"
            or set(market_output) != {
                path.as_posix() for path in source.MARKET_PATHS}):
        raise V4DatasetError("Normalized minute-market inventory differs")
    market_rows: list[dict[str, Any]] = []
    market_artifacts = []
    expected_market_counts = {
        source.MARKET_PATHS[0].as_posix(): market_manifest.get("candle_rows"),
        source.MARKET_PATHS[1].as_posix(): market_manifest.get("trade_rows"),
    }
    for relative in source.MARKET_PATHS:
        record = market_output[relative.as_posix()]
        values, artifact = source._load_exact_parquet(
            root, relative, expected_sha256=str(record.get("sha256")),
            expected_rows=expected_market_counts[relative.as_posix()])
        market_rows.extend(values)
        market_artifacts.append(artifact)

    local_audit = hrrr.get("local_observations")
    if not isinstance(local_audit, dict) or local_audit.get("substantively_complete") is not True:
        raise V4DatasetError("Complete weather component lacks observation coverage")
    local_source = local_audit.get("source_manifest")
    if (not isinstance(local_source, dict)
            or local_source.get("path") != source.LOCAL_OBSERVATION_MANIFEST.as_posix()
            or local_source.get("sha256") != sha256_file(source._inside(
                root, source.LOCAL_OBSERVATION_MANIFEST,
                "local-observation manifest"))):
        raise V4DatasetError("Complete weather component has stale observation binding")
    local_records = {
        item.get("partition"): item for item in local_audit.get("outputs", [])
        if isinstance(item, dict)
    }
    if set(local_records) != set(source.OBSERVATION_PATHS):
        raise V4DatasetError("Local-observation partition inventory differs")
    observation_rows: dict[str, list[dict[str, Any]]] = {}
    observation_artifacts: dict[str, list[dict[str, Any]]] = {}
    for partition, relative in source.OBSERVATION_PATHS.items():
        values, artifact = source._verify_parquet_record(
            root, local_records[partition], expected_path=relative.as_posix(),
            expected_rows=local_records[partition].get("rows"))
        allowed = set(source.REQUIRED_STATIONS) | set(source.OPTIONAL_STATIONS)
        if any(row.get("partition") != partition or row.get("station") not in allowed
               for row in values):
            raise V4DatasetError("Local-observation rows differ from registered stations")
        observation_rows[partition] = values
        observation_artifacts[partition] = [artifact]

    training_exclusions = source._load_exclusions(
        root, source.TRAINING_EXCLUSIONS, "weather_training")
    development_exclusions = source._load_exclusions(
        root, source.DEVELOPMENT_EXCLUSIONS, "selection")
    if (sha256_file(source._inside(root, source.TRAINING_EXCLUSIONS,
                                   "training exclusions"))
            != training_target_manifest.get("exclusions_sha256")
            or sha256_file(source._inside(root, source.DEVELOPMENT_EXCLUSIONS,
                                          "development exclusions"))
            != selection.get("exclusions_sha256")):
        raise V4DatasetError("Settlement exclusion artifacts changed")

    source_record = source._manifest_envelope(
        root, source.SOURCE_MANIFEST, source_manifest)
    hrrr_record = source._manifest_envelope(root, source.HRRR_MANIFEST, hrrr)
    gefs_record = source._manifest_envelope(root, source.GEFS_MANIFEST, gefs)
    training_target_record = source._manifest_envelope(
        root, source.TRAINING_TARGET_MANIFEST, training_target_manifest)
    settlement_record = source._manifest_envelope(
        root, source.SETTLEMENT_MANIFEST, settlement_manifest)
    market_record = source._manifest_envelope(
        root, source.MARKET_MANIFEST, market_manifest)
    minute_record = source._manifest_envelope(
        root, source.MINUTE_SOURCE_MANIFEST, minute_manifest)
    local_record = source._manifest_envelope(
        root, source.LOCAL_OBSERVATION_MANIFEST, local_manifest)

    tables = {
        "training_settlement": training_targets,
        "training_observation": observation_rows["weather_training"],
        "training_forecast": forecast_rows["weather_training"],
        "settlement": development_targets,
        "market": market_rows,
        "observation": observation_rows["selection"],
        "forecast": forecast_rows["selection"],
    }
    bindings = {
        "training_settlement": source._binding(
            "training_settlement", training_targets,
            artifacts=[training_target_artifact], manifests=[training_target_record]),
        "training_observation": source._binding(
            "training_observation", observation_rows["weather_training"],
            artifacts=observation_artifacts["weather_training"],
            manifests=[hrrr_record, local_record]),
        "training_forecast": source._binding(
            "training_forecast", forecast_rows["weather_training"],
            artifacts=forecast_artifacts["weather_training"],
            manifests=[source_record, hrrr_record, gefs_record]),
        "settlement": source._binding(
            "settlement", development_targets,
            artifacts=[development_target_artifact], manifests=[settlement_record]),
        "market": source._binding(
            "market", market_rows, artifacts=market_artifacts,
            manifests=[market_record, minute_record]),
        "observation": source._binding(
            "observation", observation_rows["selection"],
            artifacts=observation_artifacts["selection"],
            manifests=[hrrr_record, local_record]),
        "forecast": source._binding(
            "forecast", forecast_rows["selection"],
            artifacts=forecast_artifacts["selection"],
            manifests=[source_record, hrrr_record, gefs_record]),
    }
    return tables, {
        **bindings,
        "training_exclusions": training_exclusions,
        "development_exclusions": development_exclusions,
    }


def _timestamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise V4DatasetError("Frozen V4 timestamp is timezone-naive")
    return stamp


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def verify_v4_dataset(root: Path | str, destination: Path | str = DESTINATION) -> dict[str, Any]:
    root = Path(root).resolve()
    output = Path(destination)
    output = output.resolve() if output.is_absolute() else (root / output).resolve()
    output.relative_to(root)
    if "protected_final" in {part.casefold() for part in output.parts}:
        raise V4DatasetError("V4 dataset points into protected-final storage")
    verified = verify_frozen_development_dataset(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    feature_names = (
        "weather_training_features.jsonl",
        "development_calibration_features.jsonl",
        "development_evaluation_features.jsonl",
    )
    rows = {name: _read_jsonl(output / name) for name in feature_names}
    expected_counts = {
        "weather_training_features.jsonl": 1095,
        "development_calibration_features.jsonl": 90,
        "development_evaluation_features.jsonl": 435,
    }
    if {name: len(values) for name, values in rows.items()} != expected_counts:
        raise V4DatasetError("V4 feature row counts differ")
    for name, values in rows.items():
        by_day: dict[str, set[str]] = {}
        for row in values:
            if row.get("contains_settlement_label") is not False:
                raise V4DatasetError("A V4 feature row contains a settlement label")
            by_day.setdefault(str(row["climate_date"]), set()).add(
                str(row["decision_time_utc"]))
            decision = _timestamp(str(row["decision_at"]))
            for observation in row.get("observations", []):
                if (_timestamp(observation["observed_at"]) > decision
                        or _timestamp(observation["available_at"]) > decision):
                    raise V4DatasetError("Observation crosses the V4 decision cutoff")
            for forecast in row.get("forecasts", []):
                if (_timestamp(forecast["initialized_at"]) > decision
                        or _timestamp(forecast["available_at"]) > decision):
                    raise V4DatasetError("Forecast crosses the V4 decision cutoff")
            for contract in row.get("contracts", []):
                market = contract["market"]
                candle = market["latest_completed_candle"]
                if datetime.fromtimestamp(int(candle["end_period_ts"]),
                                          tz=decision.tzinfo) > decision:
                    raise V4DatasetError("Candle crosses the V4 decision cutoff")
                trades = market["public_trades_last_60_minutes"]
                if (_timestamp(trades["window_end_inclusive"]) != decision
                        or _timestamp(trades["window_start_exclusive"]) >= decision):
                    raise V4DatasetError("Public-trade window differs from V4 cutoff")
                latest = trades.get("latest_trade")
                if latest is not None and _timestamp(latest["created_time"]) > decision:
                    raise V4DatasetError("Public trade crosses the V4 decision cutoff")
        if any(times != set(DECISION_TIMES_UTC) for times in by_day.values()):
            raise V4DatasetError(f"{name} lacks the exact V4 decision grid")
    v3_id = json.loads((root / "data/manifests/v3_dataset.json").read_text(
        encoding="utf-8"))["dataset_id"]
    if verified["dataset_id"] == v3_id:
        raise V4DatasetError("V4 dataset identity did not change from V3")
    return {
        **verified,
        "decision_times_utc": list(DECISION_TIMES_UTC),
        "dataset_manifest_sha256": sha256_file(output / "manifest.json"),
        "fold_sha256": sha256_file(output / "folds.json"),
        "source_binding_count": len(manifest["input_bindings"]),
        "as_of_revalidated": True,
        "label_separation_revalidated": True,
        "network_used": False,
        "protected_final_read": False,
    }


def _publish_v4_components(root: Path, output: Path,
                           verified: Mapping[str, Any]) -> dict[str, Any]:
    relative = output.relative_to(root)
    manifest_path = output / "manifest.json"
    folds_path = output / "folds.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    folds = json.loads(folds_path.read_text(encoding="utf-8"))
    amendment_path = root / DECISION_TIME_AMENDMENT
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    amendment_body = {
        key: value for key, value in amendment.items()
        if key != "registration_sha256"
    }
    audit_path = root / str(amendment.get("audit_artifact_path", ""))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_body = {key: value for key, value in audit.items()
                  if key != "artifact_sha256"}
    if (amendment.get("registration_sha256") != canonical_hash(amendment_body)
            or amendment.get("prospective_decision_times_utc")
            != list(DECISION_TIMES_UTC)
            or amendment.get("later_retiming_permitted") is not False
            or amendment.get("alternate_minute_fallback_permitted") is not False
            or amendment.get("settlement_labels_read") is not False
            or amendment.get("profit_calculated") is not False
            or amendment.get("protected_final_read") is not False
            or amendment.get("audit_file_sha256") != sha256_file(audit_path)
            or amendment.get("audit_artifact_sha256")
            != audit.get("artifact_sha256")
            or audit.get("artifact_sha256") != canonical_hash(audit_body)):
        raise V4DatasetError("Decision-time amendment or audit binding differs")
    amendment_binding = {
        "path": DECISION_TIME_AMENDMENT.as_posix(),
        "sha256": sha256_file(amendment_path),
        "registration_sha256": amendment["registration_sha256"],
        "audit_artifact_sha256": amendment["audit_artifact_sha256"],
        "change": amendment["change"],
        "later_retiming_permitted": False,
    }
    dataset_component = {
        "schema_version": "klax-v4-frozen-dataset-component-v1",
        "component": "frozen_dataset",
        "status": "V4_TRAINING_CALIBRATION_EVALUATION_FROZEN",
        "dataset_id": verified["dataset_id"],
        "decision_times_utc": list(DECISION_TIMES_UTC),
        "decision_time_amendment": amendment_binding,
        "dataset_manifest_path": manifest_path.relative_to(root).as_posix(),
        "dataset_manifest_sha256": sha256_file(manifest_path),
        "partitions": manifest["partitions"],
        "weather_training": manifest["weather_training"],
        "development_calibration": manifest["development_calibration"],
        "development_evaluation": manifest["development_evaluation"],
        "files": [{**record, "path": (relative / record["path"]).as_posix()}
                  for record in manifest["files"]],
        "network_used": False,
        "protected_final_read": False,
        "ready_for_v4_campaign": False,
    }
    fold_component = {
        "schema_version": "klax-v4-five-fold-component-v1",
        "component": "five_fold_split",
        "status": "V4_EVALUATION_FOLDS_FROZEN",
        "dataset_id": verified["dataset_id"],
        "folds_id": verified["folds_id"],
        "decision_times_utc": list(DECISION_TIMES_UTC),
        "decision_time_amendment": amendment_binding,
        "fold_path": folds_path.relative_to(root).as_posix(),
        "fold_sha256": sha256_file(folds_path),
        "calibration_dates": folds["calibration_dates"],
        "calibration_dates_sha256": folds["calibration_dates_sha256"],
        "evaluation_dates": folds["evaluation_dates"],
        "evaluation_dates_sha256": folds["evaluation_dates_sha256"],
        "folds": folds["folds"],
        "training_rule": folds["training_rule"],
        "network_used": False,
        "protected_final_read": False,
        "ready_for_v4_campaign": False,
    }
    _immutable_json(root / DATASET_COMPONENT, dataset_component)
    _immutable_json(root / FOLD_COMPONENT, fold_component)
    bundle_body = {
        "schema_version": "klax-v4-data-bundle-v1",
        "scope": "weather_training_calibration_and_scored_development_only",
        "dataset_id": verified["dataset_id"],
        "folds_id": verified["folds_id"],
        "decision_times_utc": list(DECISION_TIMES_UTC),
        "decision_time_amendment": amendment_binding,
        "dataset_component": {
            "path": DATASET_COMPONENT.as_posix(),
            "sha256": sha256_file(root / DATASET_COMPONENT),
        },
        "fold_component": {
            "path": FOLD_COMPONENT.as_posix(),
            "sha256": sha256_file(root / FOLD_COMPONENT),
        },
        "frozen_dataset_manifest": {
            "path": manifest_path.relative_to(root).as_posix(),
            "sha256": sha256_file(manifest_path),
        },
        "frozen_fold_artifact": {
            "path": folds_path.relative_to(root).as_posix(),
            "sha256": sha256_file(folds_path),
        },
        "network_used": False,
        "protected_final_read": False,
        "ready_for_v4_campaign": False,
    }
    bundle = {**bundle_body, "bundle_sha256": canonical_hash(bundle_body)}
    _immutable_json(root / BUNDLE, bundle)
    return {
        "dataset_component": dataset_component,
        "fold_component": fold_component,
        "bundle": bundle,
    }


def build_v4_dataset(root: Path | str,
                     destination: Path | str = DESTINATION) -> dict[str, Any]:
    root = source._safe_root(Path(root))
    output = Path(destination)
    output = output.resolve() if output.is_absolute() else (root / output).resolve()
    output.relative_to(root)
    tables, bindings = _load_bound_inputs(root)
    manifest = build_frozen_development_dataset(
        output,
        training_settlement_rows=tables["training_settlement"],
        training_settlement_manifest=bindings["training_settlement"],
        training_observation_rows=tables["training_observation"],
        training_observation_manifest=bindings["training_observation"],
        training_forecast_rows=tables["training_forecast"],
        training_forecast_manifest=bindings["training_forecast"],
        settlement_rows=tables["settlement"],
        settlement_manifest=bindings["settlement"],
        market_rows=tables["market"], market_manifest=bindings["market"],
        observation_rows=tables["observation"],
        observation_manifest=bindings["observation"],
        forecast_rows=tables["forecast"], forecast_manifest=bindings["forecast"],
        decision_times_utc=DECISION_TIMES_UTC,
        required_stations=source.REQUIRED_STATIONS,
        optional_observation_stations=source.OPTIONAL_STATIONS,
        required_forecast_models=("hrrr", "gefs"),
        minimum_forecast_records_per_model={"hrrr": 24, "gefs": 16},
        required_forecast_fields_by_model={
            "hrrr": HRRR_FIELDS, "gefs": ("temperature_2m",)},
        required_forecast_members_by_model={
            "hrrr": (None,), "gefs": GEFS_MEMBERS},
        training_exclusions=bindings["training_exclusions"],
        development_exclusions=bindings["development_exclusions"],
    )
    verified = verify_v4_dataset(root, output)
    if verified["dataset_id"] != manifest["dataset_id"]:
        raise V4DatasetError("Built and independently verified V4 identities differ")
    published = _publish_v4_components(root, output, verified)
    return {
        "status": "V4_DEVELOPMENT_DATASET_FROZEN_AND_VERIFIED",
        "dataset_id": verified["dataset_id"],
        "folds_id": verified["folds_id"],
        "bundle_sha256": published["bundle"]["bundle_sha256"],
        "destination": output.relative_to(root).as_posix(),
        "decision_times_utc": list(DECISION_TIMES_UTC),
        "weather_training_feature_rows": verified["weather_training_feature_rows"],
        "calibration_feature_rows": verified["calibration_feature_rows"],
        "evaluation_feature_rows": verified["evaluation_feature_rows"],
        "network_used": False,
        "protected_final_read": False,
        "canonical_config_changed": False,
        "campaign_or_readiness_ticket_issued": False,
    }
