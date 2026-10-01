"""Shared fail-closed helpers for the V5 offline verification campaign."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping


PROTECTED_PARTS = {"protected_final", "holdout", "confirmation_labels"}


class V5Error(RuntimeError):
    """Base V5 failure."""


class V5IntegrityError(V5Error):
    """A frozen identity, schema, hash, or safety rule failed."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        raise V5IntegrityError("timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V5IntegrityError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V5IntegrityError(f"JSON object required: {path}")
    return value


def project_path(
    root: Path, relative: str | Path, *, allow_protected: bool = False,
) -> Path:
    root = Path(root).resolve()
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise V5IntegrityError(f"path escapes project: {relative}")
    lowered = {part.casefold().replace("-", "_") for part in raw.parts}
    if not allow_protected and lowered & PROTECTED_PARTS:
        raise V5IntegrityError(f"protected path denied: {relative}")
    path = (root / raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise V5IntegrityError(f"path escapes project: {relative}") from exc
    return path


def file_record(root: Path, relative: str | Path) -> dict[str, Any]:
    path = project_path(root, relative)
    if not path.is_file():
        raise V5IntegrityError(f"required file missing: {relative}")
    return {
        "path": path.relative_to(Path(root).resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def verify_file_record(root: Path, record: Mapping[str, Any]) -> Path:
    if set(record) != {"path", "bytes", "sha256"}:
        raise V5IntegrityError("file record fields differ")
    path = project_path(root, str(record["path"]))
    if (
        not path.is_file()
        or type(record["bytes"]) is not int
        or path.stat().st_size != record["bytes"]
        or file_sha256(path) != record["sha256"]
    ):
        raise V5IntegrityError(f"file record changed: {record.get('path')}")
    return path


def atomic_write_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n"
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=path.name + ".", suffix=".pending", delete=False,
    ) as handle:
        handle.write(payload)
        pending = Path(handle.name)
    pending.replace(path)


def hash_bound(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != field}
    output = dict(body)
    output[field] = canonical_hash(body)
    return output


def verify_hash_bound(value: Mapping[str, Any], field: str) -> None:
    claimed = value.get(field)
    body = {key: item for key, item in value.items() if key != field}
    if not isinstance(claimed, str) or claimed != canonical_hash(body):
        raise V5IntegrityError(f"{field} mismatch")


def safety_record() -> dict[str, bool]:
    return {
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
    }

