from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path


class IntegrityError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def filehash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value, *, sealed=True):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body.pop("self_sha256", None)
    if sealed:
        body["self_sha256"] = digest(body)
    pending = target.with_name(target.name + ".pending")
    pending.write_text(json.dumps(body, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(pending, target)
    return body


def checked(path):
    value = read(path)
    body = dict(value)
    expected = body.pop("self_sha256", None)
    if expected != digest(body):
        raise IntegrityError(f"Artifact seal does not match: {path}")
    return value


@contextmanager
def lease(directory):
    """A kernel lock, not a stale PID file; released after process exit."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise IntegrityError("Another campaign writer holds the lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
