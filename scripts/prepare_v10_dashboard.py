"""Restore small frozen inputs without copying live records or refitting V10."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

MANIFEST = Path("frozen/v10_dashboard/manifest.json")


def _path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("Frozen input escapes the project: " + relative)
    return path


def _verify(path, expected):
    if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Frozen input is missing or changed: " + str(path))


def prepare(root, *, verify_only=False):
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    bindings = manifest["input_bindings"]
    assets = manifest["runtime_assets"]
    missing = []
    # Check the complete bundle and existing inputs before writing anything.
    for relative, expected in bindings.items():
        destination = _path(root, relative)
        if relative in assets:
            asset = assets[relative]
            if asset["sha256"] != expected or asset["source"] != "frozen/v10_dashboard/objects/" + expected + ".json":
                raise ValueError("Invalid frozen asset manifest: " + relative)
            source = _path(root, asset["source"])
            _verify(source, expected)
            if destination.exists():
                _verify(destination, expected)
            elif verify_only:
                raise ValueError("Run setup to restore the frozen input: " + relative)
            else:
                missing.append((source, destination, expected))
        else:
            _verify(destination, expected)
    for source, destination, expected in missing:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(source.read_bytes())
        except FileExistsError:
            _verify(destination, expected)
        _verify(destination, expected)
    # Use the unchanged scientific loader, including its independently pinned
    # artifact seals, before a registration or network capture is possible.
    for folder in (root, root / "src"):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    from v10_online.model import FrozenV10
    model = FrozenV10.load(root)
    return {"verified_inputs": len(bindings), "restored_assets": len(missing),
            "model_bindings": len(model.bindings), "live_records_copied": 0,
            "model_refitted": False, "orders": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, verify_only=args.verify_only), indent=2))
