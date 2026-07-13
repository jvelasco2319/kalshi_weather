#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    schema_paths = list((ROOT / "schemas").glob("*.json"))
    for path in schema_paths:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    config = yaml.safe_load((ROOT / "config/strategy_current.shadow.yaml").read_text(encoding="utf-8"))
    assert config["strategy_id"] == "klax-current-five-model-2026-07-11"
    assert config["mode"] == "shadow"
    assert config["live_trading_enabled"] is False
    assert config["canary_enabled"] is False
    assert config["taker_enabled"] is False
    assert config["order_submission_reachable"] is False
    assert config["models"]["canonical_order"] == ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]

    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(ROOT / "reference/test_strategy_current_reference.py")],
        cwd=ROOT / "reference",
        check=True,
    )

    manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file(), item["path"]
        assert path.stat().st_size == item["size_bytes"], item["path"]
        assert sha256(path) == item["sha256"], item["path"]

    print(json.dumps({
        "status": "PASS",
        "schemas": len(schema_paths),
        "reference_tests": "PASS",
        "manifest_files": len(manifest["files"]),
        "strategy_id": config["strategy_id"],
    }, indent=2))


if __name__ == "__main__":
    main()
