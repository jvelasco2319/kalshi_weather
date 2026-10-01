"""Freeze and verify the V10 all-combinations acquisition protocol."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from past7_replay.engine import _file_hash, _read_json, _seal, _verify_seal, _write_immutable_json


CONFIG = Path("configs/v10_meteorological_combinations.json")
GOAL = Path("docs/V10_ALL_METEOROLOGICAL_COMBINATIONS_GOAL.md")
V8_FREEZE = Path("runs/v8/frozen-primary-strategy/strategy-freeze.json")
V9_FREEZE = Path("runs/v9/frozen-research-protocol/protocol-freeze.json")
ACQUISITION = Path("v10/acquire_inputs.py")
IMPLEMENTATION = Path("v10/protocol_freeze.py")
OUTPUT = Path("runs/v10/frozen-acquisition-protocol/protocol-freeze.json")


class V10ProtocolError(ValueError):
    """Raised when the frozen V10 acquisition boundary differs."""


def _verify_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = _read_json(root / CONFIG)
    v8 = _read_json(root / V8_FREEZE, sealed=True)
    v9 = _read_json(root / V9_FREEZE, sealed=True)
    lineage = config.get("lineage", {})
    combination = config.get("combination_rule", {})
    safety = config.get("safety", {})
    if (
        config.get("schema_version") != "klax-v10-meteorological-combinations-v1"
        or config.get("status") != "REGISTERED_BEFORE_ADDITIONAL_INPUT_ACQUISITION"
        or lineage.get("v8_strategy_self_sha256") != v8.get("self_sha256")
        or lineage.get("v9_protocol_self_sha256") != v9.get("self_sha256")
        or lineage.get("v8_remains_unchanged") is not True
        or combination.get("subset_count") != 64
        or combination.get("combination_candidate_count") != 192
        or combination.get("total_candidate_count") != 193
        or len(config.get("feature_blocks", [])) != 6
        or safety.get("offline_scoring_only") is not True
        or safety.get("paper_orders_allowed") is not False
        or safety.get("live_orders_allowed") is not False
    ):
        raise V10ProtocolError("V10 lineage, finite catalog, or safety registration differs")
    return config, v8, v9


def build(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config, v8, v9 = _verify_inputs(root)
    paths = (CONFIG, GOAL, V8_FREEZE, V9_FREEZE, ACQUISITION, IMPLEMENTATION)
    return _seal({
        "schema_version": "klax-v10-frozen-acquisition-protocol-v1",
        "protocol_id": "v10-klax-all-meteorological-combinations-v1",
        "status": "FROZEN_BEFORE_V10_DATA_ACQUISITION",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_v8_strategy_self_sha256": v8["self_sha256"],
        "v9_protocol_self_sha256": v9["self_sha256"],
        "v8_remains_primary_and_unchanged": True,
        "feature_blocks": config["feature_blocks"],
        "combination_rule": config["combination_rule"],
        "fixed_feature_states": config["fixed_feature_states"],
        "fixed_thresholds": config["fixed_thresholds"],
        "probability_model": config["probability_model"],
        "selection_and_gates": config["selection_and_gates"],
        "additional_inputs": config["additional_inputs"],
        "input_bindings": {path.as_posix(): _file_hash(root / path) for path in paths},
        "additional_data_acquired": False,
        "settlement_labels_read_by_v10": False,
        "market_data_read_by_v10": False,
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
        artifact.get("schema_version") != "klax-v10-frozen-acquisition-protocol-v1"
        or artifact.get("status") != "FROZEN_BEFORE_V10_DATA_ACQUISITION"
        or artifact.get("v8_remains_primary_and_unchanged") is not True
        or artifact.get("additional_data_acquired") is not False
        or artifact.get("settlement_labels_read_by_v10") is not False
        or artifact.get("market_data_read_by_v10") is not False
        or artifact.get("v10_candidate_scored") is not False
        or artifact.get("v10_promotion_authorized") is not False
        or artifact.get("paper_orders_placed") != 0
        or artifact.get("live_orders_placed") != 0
        or artifact.get("combination_rule", {}).get("total_candidate_count") != 193
    ):
        raise V10ProtocolError("frozen V10 acquisition invariant differs")
    for relative, expected in artifact["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V10ProtocolError(f"frozen V10 acquisition input changed: {relative}")
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
