"""Deterministic source inventories and frozen data manifests."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n", encoding="utf-8")
    temp.replace(path)


def inventory(root: Path, paths: list[Path]) -> list[dict]:
    base = root.resolve()
    result = []
    for path in sorted(set(paths)):
        absolute = path.resolve()
        relative = absolute.relative_to(base).as_posix()
        result.append({"path": relative, "bytes": absolute.stat().st_size, "sha256": sha256_file(absolute)})
    return result


def freeze_manifest(root: Path, paths: list[Path], policy: dict, destination: Path) -> dict:
    files = inventory(root, paths)
    body = {"files": files, "policy": policy}
    manifest = {**body, "version": canonical_hash(body), "created_at_utc": datetime.now(timezone.utc).isoformat()}
    if destination.exists():
        old = json.loads(destination.read_text(encoding="utf-8"))
        if old["version"] != manifest["version"]:
            raise ValueError("Cannot overwrite a frozen dataset version; choose a new manifest")
        return old
    write_json(destination, manifest)
    return manifest


def verify_manifest(root: Path, manifest: dict) -> None:
    expected = canonical_hash({"files": manifest["files"], "policy": manifest["policy"]})
    if expected != manifest["version"]:
        raise ValueError("Manifest definition was modified")
    for record in manifest["files"]:
        target = (root / record["path"]).resolve()
        target.relative_to(root.resolve())
        if target.stat().st_size != record["bytes"] or sha256_file(target) != record["sha256"]:
            raise ValueError(f"Frozen input changed: {record['path']}")
