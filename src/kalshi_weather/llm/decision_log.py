from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "bearer",
    "private_key",
    "secret",
    "token",
)


def write_llm_decision_log(log_dir: str | Path, record: dict[str, Any]) -> Path:
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = path / f"llm_advisor_decisions_{day}.jsonl"
    row = _redact(record)
    if "timestamp" not in row:
        row["timestamp"] = datetime.now(timezone.utc).isoformat()
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str, sort_keys=True) + "\n")
    return out


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)

