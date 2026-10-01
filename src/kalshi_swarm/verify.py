from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .engine import config_dir


def _load_and_verify(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = value.get("self_sha256")
    body = {key: item for key, item in value.items() if key != "self_sha256"}
    actual = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if expected != actual:
        raise ValueError(f"frozen artifact hash differs: {path}")
    return value


def verify_frozen() -> dict[str, str]:
    v5b = _load_and_verify(config_dir() / "frozen_v5b.json")
    v8 = _load_and_verify(config_dir() / "frozen_v8.json")
    v10 = _load_and_verify(config_dir() / "frozen_v10.json")
    if v5b["parameters"]["allowed_sides"] != ["NO"] or v5b["ten_percent_confirmed"] is not False:
        raise ValueError("V5B identity or scientific status differs")
    if v8["strategy_id"] != "v8-klax-primary-rolling-confusion-no-v1" or v8["probability_model"]["conditional_confusion_weight"] != 0.75:
        raise ValueError("V8 identity differs")
    if v10["candidate_id"] != "V10-pressure_and_flow-W75" or v10["promotion_or_online_use_authorized"] is not False:
        raise ValueError("V10 identity or scientific status differs")
    return {"V5B": v5b["self_sha256"], "V8": v8["self_sha256"], "V10": v10["self_sha256"]}


def main() -> int:
    hashes = verify_frozen()
    print("Frozen configurations verified: " + ", ".join(f"{name}={digest[:12]}" for name, digest in hashes.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
