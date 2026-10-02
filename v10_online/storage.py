"""Hash-bound append-only JSON and raw capture receipts."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path


def digest(value: dict) -> str:
    return sha256(json.dumps({k:v for k,v in value.items() if k != "self_sha256"},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def seal(value: dict) -> dict:
    result = dict(value)
    result["self_sha256"] = digest(result)
    return result


def hash_file(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def read_verified(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("self_sha256") != digest(value):
        raise ValueError(f"Artifact seal differs: {path.name}")
    return value


def write_immutable(path: Path, value: dict) -> dict:
    result = seal(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacement of prior evidence, including by
    # simultaneous captures. A truncated file fails its seal on every read.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True, indent=2, allow_nan=False)+"\n")
        handle.flush()
    return result
