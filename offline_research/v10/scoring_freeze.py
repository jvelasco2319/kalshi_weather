"""Freeze the V10 normalized inputs and evaluator before reading outcomes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from past7_replay.engine import _file_hash, _read_json, _seal, _verify_seal, _write_immutable_json
from v10.protocol_freeze import OUTPUT as ACQUISITION_FREEZE, verify as verify_acquisition_protocol


CONFIG = Path("configs/v10_meteorological_combinations.json")
MANIFEST = Path("data/manifests/v10_meteorology.json")
OBSERVATIONS = Path("data/normalized/v10_meteorology/observations.parquet")
WEATHER = Path("data/normalized/v10_meteorology/weather_features.parquet")
EVALUATOR = Path("v10/evaluate_combinations.py")
IMPLEMENTATION = Path("v10/scoring_freeze.py")
OUTPUT = Path("runs/v10/frozen-scoring-protocol/scoring-freeze.json")


class V10ScoringFreezeError(ValueError):
    """Raised when the V10 scoring boundary differs."""


def _verify_manifest(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    expected = value.get("self_sha256")
    body = dict(value)
    body.pop("self_sha256", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if expected != sha256(encoded).hexdigest():
        raise V10ScoringFreezeError("V10 acquisition manifest seal differs")
    return value


def _verify_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = verify_acquisition_protocol(root)
    manifest = _verify_manifest(root / MANIFEST)
    if (
        protocol.get("status") != "FROZEN_BEFORE_V10_DATA_ACQUISITION"
        or manifest.get("status") != "COMPLETE"
        or manifest.get("weather_rows") != 722
        or manifest.get("hrrr_range_count") != 1444
        or manifest.get("settlement_labels_read") is not False
        or manifest.get("market_data_read") is not False
        or manifest.get("paper_orders_placed") != 0
        or manifest.get("live_orders_placed") != 0
    ):
        raise V10ScoringFreezeError("V10 acquisition readiness differs")
    for relative, expected in manifest.get("outputs", {}).items():
        if _file_hash(root / relative) != expected:
            raise V10ScoringFreezeError(f"V10 normalized input differs: {relative}")
    return protocol, manifest


def build(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    protocol, manifest = _verify_inputs(root)
    paths = (CONFIG, ACQUISITION_FREEZE, MANIFEST, OBSERVATIONS, WEATHER, EVALUATOR, IMPLEMENTATION)
    return _seal({
        "schema_version": "klax-v10-frozen-scoring-protocol-v1",
        "status": "FROZEN_BEFORE_V10_LABEL_READ",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "acquisition_protocol_self_sha256": protocol["self_sha256"],
        "acquisition_manifest_self_sha256": manifest["self_sha256"],
        "candidate_count": 193,
        "feature_block_count": 6,
        "input_bindings": {path.as_posix(): _file_hash(root / path) for path in paths},
        "settlement_labels_read_by_v10": False,
        "market_data_read_by_v10": False,
        "network_allowed_during_scoring": False,
        "catalog_expansion_allowed": False,
        "v10_candidate_scored": False,
        "v10_promotion_authorized": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })


def verify(project_root: str | Path, value: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    artifact = value if value is not None else _read_json(root / OUTPUT)
    _verify_seal(artifact)
    if (
        artifact.get("status") != "FROZEN_BEFORE_V10_LABEL_READ"
        or artifact.get("candidate_count") != 193
        or artifact.get("feature_block_count") != 6
        or artifact.get("settlement_labels_read_by_v10") is not False
        or artifact.get("market_data_read_by_v10") is not False
        or artifact.get("network_allowed_during_scoring") is not False
        or artifact.get("catalog_expansion_allowed") is not False
        or artifact.get("v10_candidate_scored") is not False
        or artifact.get("paper_orders_placed") != 0
        or artifact.get("live_orders_placed") != 0
    ):
        raise V10ScoringFreezeError("frozen V10 scoring invariant differs")
    for relative, expected in artifact["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V10ScoringFreezeError(f"frozen V10 scoring input changed: {relative}")
    _verify_inputs(root)
    return artifact


def freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if (root / OUTPUT).exists():
        return verify(root)
    value = build(root)
    _write_immutable_json(root / OUTPUT, value)
    return verify(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--project-root", default=".")
    arguments = parser.parse_args()
    result = freeze(arguments.project_root) if arguments.action == "freeze" else verify(arguments.project_root)
    print(result["self_sha256"])


if __name__ == "__main__":
    main()
