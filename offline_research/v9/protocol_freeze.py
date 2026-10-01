"""Freeze and verify the V9 meteorological-successor research protocol."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from past7_replay.engine import _file_hash, _read_json, _seal, _verify_seal, _write_immutable_json


CONFIG = Path("configs/v9_meteorological_successor.json")
GOAL = Path("docs/V9_METEOROLOGICAL_SUCCESSOR_GOAL.md")
V8_FREEZE = Path("runs/v8/frozen-primary-strategy/strategy-freeze.json")
V8_REGIME_SUMMARY = Path("runs/v8_meteorological_regimes/summary.json")
IMPLEMENTATION = Path("v9/protocol_freeze.py")
OUTPUT = Path("runs/v9/frozen-research-protocol/protocol-freeze.json")


class V9ProtocolError(ValueError):
    pass


def _verify_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = _read_json(root / CONFIG)
    v8 = _read_json(root / V8_FREEZE, sealed=True)
    regime = _read_json(root / V8_REGIME_SUMMARY)
    _verify_seal(regime)
    if (
        config.get("schema_version") != "klax-v9-meteorological-successor-protocol-v1"
        or config.get("lineage", {}).get("v8_must_remain_unchanged") is not True
        or len(config.get("finite_candidate_catalog", [])) != 6
        or v8.get("self_sha256") != config.get("lineage", {}).get("v8_strategy_freeze_self_sha256")
        or v8.get("probability_model", {}).get("regime_overlay_enabled") is not False
        or regime.get("status") != "COMPLETE_EXPOSED_DEVELOPMENT_ONLY"
        or regime.get("conclusion") != "NO_STABLE_INCREMENTAL_METEOROLOGICAL_BENEFIT"
    ):
        raise V9ProtocolError("V9 lineage or registered protocol differs")
    return config, v8, regime


def build(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config, v8, regime = _verify_inputs(root)
    paths = (CONFIG, GOAL, V8_FREEZE, V8_REGIME_SUMMARY, IMPLEMENTATION)
    return _seal({
        "schema_version": "klax-v9-frozen-research-protocol-v1",
        "protocol_id": "v9-klax-physical-meteorology-v1",
        "status": "FROZEN_BEFORE_ADDITIONAL_METEOROLOGICAL_DATA_ACQUISITION",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_v8_strategy_id": v8["strategy_id"],
        "base_v8_strategy_self_sha256": v8["self_sha256"],
        "v8_remains_primary_and_unchanged": True,
        "candidate_catalog": config["finite_candidate_catalog"],
        "required_additional_data": config["required_additional_data"],
        "fixed_derived_features": config["fixed_derived_features"],
        "development_protocol": config["development_protocol"],
        "economic_confirmation": config["economic_confirmation"],
        "safety": config["safety"],
        "exposed_findings_bound_as_hypotheses_only": config["exposed_physical_findings"],
        "prior_regime_stable_winners": regime["stable_incremental_accuracy_winners"],
        "input_bindings": {path.as_posix(): _file_hash(root / path) for path in paths},
        "additional_data_acquired": False,
        "v9_candidate_fitted": False,
        "v9_promotion_authorized": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    })


def verify(project_root: str | Path, value: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    artifact = value if value is not None else _read_json(root / OUTPUT)
    _verify_seal(artifact)
    if (
        artifact.get("schema_version") != "klax-v9-frozen-research-protocol-v1"
        or artifact.get("status") != "FROZEN_BEFORE_ADDITIONAL_METEOROLOGICAL_DATA_ACQUISITION"
        or artifact.get("v8_remains_primary_and_unchanged") is not True
        or artifact.get("additional_data_acquired") is not False
        or artifact.get("v9_candidate_fitted") is not False
        or artifact.get("v9_promotion_authorized") is not False
        or artifact.get("paper_orders_placed") != 0
        or artifact.get("live_orders_placed") != 0
        or len(artifact.get("candidate_catalog", [])) != 6
    ):
        raise V9ProtocolError("frozen V9 invariant differs")
    for relative, expected in artifact["input_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise V9ProtocolError(f"frozen V9 input changed: {relative}")
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
    args = parser.parse_args()
    value = freeze(args.project_root) if args.action == "freeze" else verify(args.project_root)
    print(value["self_sha256"])


if __name__ == "__main__":
    main()
