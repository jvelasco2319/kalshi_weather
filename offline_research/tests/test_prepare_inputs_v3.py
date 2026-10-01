from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from klax_lab.dataset import _write_parquet
from klax_lab.prepare_inputs_v3 import (
    DEVELOPMENT_SETTLEMENT_CORRECTION_INVARIANTS,
    DEVELOPMENT_SETTLEMENT_KEYS,
    _project_path,
    prepare_weather_training_targets,
)
from klax_lab.provenance import sha256_file, write_json


def _selected_settlement_component(root: Path) -> None:
    target = root / "data/normalized/v3_development/selection/labels/settlement_targets.parquet"
    exclusions = target.with_name("settlement_target_exclusions.json")
    _write_parquet(target, [{"climate_date": "2025-01-05", "reported_high_f": 65}])
    write_json(exclusions, [])
    source = root / "data/normalized/selection/labels/climate_versions.parquet"
    _write_parquet(source, [{"climate_date": "2025-01-05"}])
    component = {
        "schema_version": 1,
        "built_at_utc": "2026-09-25T18:28:54+00:00",
        "component": "settlement_reconciliation",
        "status": "DEVELOPMENT_COMPLETE",
        "network_used": False,
        "protected_final_read": False,
        "eligible_target_count": 1,
        "excluded_date_count": 0,
        "inputs": [{
            "table": "selection_climate_versions",
            "path": source.relative_to(root).as_posix(),
            "sha256": sha256_file(source),
        }],
        "partitions": {
            "selection": {
                "eligible_targets": 1,
                "excluded_dates": 0,
                "target_path": target.relative_to(root).as_posix(),
                "target_sha256": sha256_file(target),
                "exclusions_path": exclusions.relative_to(root).as_posix(),
                "exclusions_sha256": sha256_file(exclusions),
            },
            "weather_training": {
                "eligible_targets": 0,
                "excluded_dates": 0,
                "target_path": "data/normalized/weather_training/labels/old.parquet",
                "target_sha256": "0" * 64,
                "exclusions_path": "data/normalized/weather_training/labels/old.json",
                "exclusions_sha256": "0" * 64,
            },
        },
        "rules": ["selection settlement rule", "complete intervals", "exact outcome mapping"],
    }
    write_json(root / "data/manifests/v3_settlement_reconciliation.json", component)
    write_json(root / "data/normalized/v3_development/manifest.json", {
        key: component[key] for key in (
            "schema_version", "built_at_utc", "network_used", "partitions", "inputs", "rules"
        ) if key in component
    })


def test_training_targets_bind_into_settlement_without_kalshi_training_contracts(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _selected_settlement_component(root)
    climate = root / "data/normalized/weather_training/labels/climate.parquet"
    _write_parquet(climate, [{
        "climate_date": "2024-01-01",
        "partition": "weather_training",
        "label_role": "NWS_CLILAX_archival_copy",
        "tmax_f": 64,
        "issued_at": "2024-01-02T09:00:00+00:00",
        "available_at": "2024-01-02T09:00:00+00:00",
        "source_member": "CLILAX_202401020900.txt",
        "source_sha256": "1" * 64,
        "availability_status": "archive_member_timestamp_proxy",
        "archive_sha256": "2" * 64,
    }])

    report = prepare_weather_training_targets(root)

    assert report["target_count"] == 1
    assert report["exclusion_count"] == 365
    assert report["protected_final_read"] is False
    rows = pq.read_table(root / report["target_path"]).to_pylist()
    assert rows[0]["label_role"] == "weather_model_fitting_only"
    component = json.loads(
        (root / "data/manifests/v3_settlement_reconciliation.json").read_text(encoding="utf-8"))
    assert component["eligible_target_count"] == 2
    assert component["excluded_date_count"] == 365
    assert component["partitions"]["weather_training"]["eligible_targets"] == 1
    assert component["partitions"]["selection"]["eligible_targets"] == 1
    assert sha256_file(root / component["weather_training_target_manifest_path"]) == (
        component["weather_training_target_manifest_sha256"])
    mirror = json.loads(
        (root / "data/normalized/v3_development/manifest.json").read_text(encoding="utf-8"))
    assert mirror["partitions"] == component["partitions"]
    assert mirror["inputs"] == component["inputs"]
    assert mirror["rules"] == component["rules"]

    prepare_weather_training_targets(root)
    repeated = json.loads(
        (root / "data/manifests/v3_settlement_reconciliation.json").read_text(encoding="utf-8"))
    assert repeated == component
    assert sum(record["table"] == "weather_training_target_manifest"
               for record in repeated["inputs"]) == 1


def test_training_target_binding_rejects_a_divergent_adjacent_manifest(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _selected_settlement_component(root)
    climate = root / "data/normalized/weather_training/labels/climate.parquet"
    _write_parquet(climate, [{
        "climate_date": "2024-01-01",
        "partition": "weather_training",
        "label_role": "NWS_CLILAX_archival_copy",
        "tmax_f": 64,
        "issued_at": "2024-01-02T09:00:00+00:00",
        "available_at": "2024-01-02T09:00:00+00:00",
        "source_member": "CLILAX_202401020900.txt",
        "source_sha256": "1" * 64,
        "availability_status": "archive_member_timestamp_proxy",
        "archive_sha256": "2" * 64,
    }])
    mirror_path = root / "data/normalized/v3_development/manifest.json"
    mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
    mirror["partitions"]["selection"]["eligible_targets"] = 2
    write_json(mirror_path, mirror)
    component_path = root / "data/manifests/v3_settlement_reconciliation.json"
    before = component_path.read_bytes()

    with pytest.raises(ValueError, match="differs from its authoritative component"):
        prepare_weather_training_targets(root)

    assert component_path.read_bytes() == before


def test_training_target_binding_verifies_optional_correction_provenance(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    _selected_settlement_component(root)
    climate = root / "data/normalized/weather_training/labels/climate.parquet"
    _write_parquet(climate, [{
        "climate_date": "2024-01-01",
        "partition": "weather_training",
        "label_role": "NWS_CLILAX_archival_copy",
        "tmax_f": 64,
        "issued_at": "2024-01-02T09:00:00+00:00",
        "available_at": "2024-01-02T09:00:00+00:00",
        "source_member": "CLILAX_202401020900.txt",
        "source_sha256": "1" * 64,
        "availability_status": "archive_member_timestamp_proxy",
        "archive_sha256": "2" * 64,
    }])
    prepare_weather_training_targets(root)
    component_path = root / "data/manifests/v3_settlement_reconciliation.json"
    mirror_path = root / "data/normalized/v3_development/manifest.json"
    snapshot_path = root / "data/manifests/v3_development_manifest.pre-training-target-binding.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text('{"stale":true}\n', encoding="utf-8")
    correction_path = (
        root / "data/manifests/v3_development_manifest_synchronization_correction.json"
    )
    write_json(correction_path, {
        "schema_version": 1,
        "component": "v3_development_adjacent_manifest_synchronization_correction",
        "status": "STALE_ADJACENT_MANIFEST_CORRECTED",
        "network_used": False,
        "protected_final_read": False,
        "projection_keys": list(DEVELOPMENT_SETTLEMENT_KEYS),
        "authoritative_component": {
            "path": "data/manifests/v3_settlement_reconciliation.json",
            "bytes": component_path.stat().st_size,
            "sha256": sha256_file(component_path),
        },
        "pre_repair_snapshot": {
            "path": snapshot_path.relative_to(root).as_posix(),
            "bytes": snapshot_path.stat().st_size,
            "sha256": sha256_file(snapshot_path),
        },
        "corrected_manifest": {
            "path": "data/normalized/v3_development/manifest.json",
            "bytes": mirror_path.stat().st_size,
            "sha256": sha256_file(mirror_path),
        },
        "invariants": DEVELOPMENT_SETTLEMENT_CORRECTION_INVARIANTS,
    })

    prepare_weather_training_targets(root)

    correction = json.loads(correction_path.read_text(encoding="utf-8"))
    correction["corrected_manifest"]["sha256"] = "0" * 64
    write_json(correction_path, correction)
    with pytest.raises(ValueError, match="correction hash differs"):
        prepare_weather_training_targets(root)


@pytest.mark.parametrize("value", [
    "data/normalized/protected_final/labels/x.parquet",
    "data/normalized/holdout/labels/x.parquet",
    "../outside.json",
    "C:/outside.json",
])
def test_project_path_rejects_protected_or_external_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        _project_path(tmp_path.resolve(), value, "test")
