from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    ROOT / "CODEX_LIVE_SHADOW_SIGNAL_ROOM_PROMPT.md",
    ROOT / "README.md",
    ROOT / "config" / "live_shadow_runtime.example.yaml",
    ROOT / "ui_reference" / "approved_prototype_a.html",
    ROOT / "ui_reference" / "approved_prototype_a.png",
    ROOT / "strategy_spec" / "CODEX_MASTER_PROMPT.md",
    ROOT / "strategy_spec" / "config" / "strategy_current.shadow.yaml",
    ROOT / "schemas" / "live_stack_status.schema.json",
]

missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

with (ROOT / "schemas" / "live_stack_status.schema.json").open("r", encoding="utf-8") as handle:
    json.load(handle)

sha = hashlib.sha256((ROOT / "CODEX_LIVE_SHADOW_SIGNAL_ROOM_PROMPT.md").read_bytes()).hexdigest()
print(f"Package OK. Prompt SHA-256: {sha}")
