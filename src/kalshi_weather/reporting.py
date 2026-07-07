from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


def report_json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def safe_console_payload(payload: Any) -> Any:
    """Convert nested objects to JSON-safe primitives for CLI/report output."""
    return json.loads(json.dumps(payload, default=report_json_default))


def timestamped_report_dir(base_dir: str | Path, prefix: str | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dirname = f"{prefix}_{stamp}" if prefix else stamp
    path = Path(base_dir) / dirname
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_report(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(safe_console_payload(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


def write_text_report(path: str | Path, text: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "report"
