from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any

from .engine import score_record
from .io import write_json
from .live_report import render_live
from .public_data import KALSHI_API, live_record


def snapshot(*, target: date | None = None, output_dir: str | Path = "artifacts/online", api_base: str = KALSHI_API) -> dict[str, Any]:
    record = live_record(target=target, api_base=api_base)
    result = {"schema_version": "kalshi-swarm-read-only-live-snapshot-v1", "record": record, "scored": score_record(record), "orders_enabled": False, "order_attempts": 0}
    output = Path(output_dir)
    write_json(output / "latest.json", result)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "journal.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, separators=(",", ":")) + "\n")
    render_live(result, output / "dashboard.html")
    return result


def watch(*, target: date | None, output_dir: str | Path, api_base: str, interval_seconds: int) -> None:
    while True:
        result = snapshot(target=target, output_dir=output_dir, api_base=api_base)
        print(f"{result['record']['live']['captured_at_utc']} refreshed; orders=0", flush=True)
        time.sleep(max(30, interval_seconds))
