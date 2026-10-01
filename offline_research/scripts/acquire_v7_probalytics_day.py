"""Acquire one elapsed V7 date of outcome-blind Probalytics books."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
import getpass
import json
import os
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.acquire_v5b_untouched_probalytics import (
    Client,
    coverage_query,
    parse_coverage,
    sha256_bytes,
    target_query,
)
from v7_shadow.campaign import CAMPAIGN_ID, target_dates


def atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_bytes(payload)
    pending.replace(path)


def run(root: Path, target: date, client: Client) -> dict:
    config = json.loads((root / "configs/v7_six_week_shadow.json").read_text(encoding="utf-8"))
    if target.isoformat() not in target_dates(config) or target >= datetime.now(UTC).date():
        raise ValueError("V7 Probalytics target must be registered and strictly historical")
    folder = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / target.isoformat() / "input/probalytics"
    manifest_path = folder / "manifest.json"
    if manifest_path.is_file():
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if value.get("climate_date") != target.isoformat() or value.get("contains_settlement_outcomes") is not False:
            raise ValueError("cached Probalytics manifest boundary differs")
        return value
    coverage_payload = client.query(coverage_query(target))
    targets_payload = client.query(target_query(target))
    rows = [line for line in targets_payload.splitlines() if line.strip()]
    coverage = parse_coverage(coverage_payload)
    if int(coverage["contracts"]) != 6 or len(rows) != 48:
        raise RuntimeError(f"expected six contracts and 48 target rows, got {coverage['contracts']} and {len(rows)}")
    atomic(folder / "coverage.csv", coverage_payload)
    atomic(folder / "target_books.jsonl", targets_payload)
    manifest = {
        "schema_version": "klax-v7-probalytics-day-v1",
        "climate_date": target.isoformat(),
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "coverage": coverage,
        "target_rows": len(rows),
        "artifacts": {
            "coverage.csv": sha256_bytes(coverage_payload),
            "target_books.jsonl": sha256_bytes(targets_payload),
        },
        "contains_settlement_outcomes": False,
        "credentials_persisted": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    atomic(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--username")
    args = parser.parse_args()
    username = args.username or os.environ.get("PROBALYTICS_USERNAME", "").strip()
    password = os.environ.get("PROBALYTICS_PASSWORD") or getpass.getpass("Probalytics ClickHouse password: ")
    if not username or not password:
        raise SystemExit("Probalytics username and password are required for this finite historical query")
    try:
        print(json.dumps(run(Path(args.project_root).resolve(), args.date, Client(username, password)), indent=2))
    finally:
        password = ""


if __name__ == "__main__":
    main()

