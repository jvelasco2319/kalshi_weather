"""Acquire V7 settlements once, after all 42 prediction/order freezes exist."""
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

from scripts.acquire_v5b_untouched_confirmation_labels import Client, label_query
from v7_shadow.campaign import CAMPAIGN_ID, STATE, _read_object, _verify_seal


def run(root: Path, client: Client) -> dict:
    state = _read_object(root / STATE)
    _verify_seal(state, "recovery_sha256")
    if len(state.get("completed_prediction_freeze_dates", [])) != 42:
        raise ValueError("all 42 daily freezes are required before settlement acquisition")
    if datetime.now(UTC).date() <= date(2026, 11, 9):
        raise ValueError("V7 settlement acquisition cannot run before November 10, 2026 UTC")
    tickers = []
    for day in state["completed_prediction_freeze_dates"]:
        path = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / day / "prediction-order-freeze.json"
        freeze = json.loads(path.read_text(encoding="utf-8"))
        tickers.extend(row["market_ticker"] for row in freeze["methods"]["v5b_causal_no"]["probabilities"])
    tickers = sorted(set(tickers))
    if len(tickers) != 252:
        raise ValueError(f"expected 252 unique contract tickers, found {len(tickers)}")
    payload = client.query(label_query(tickers))
    raw_rows = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
    by_ticker = {}
    for row in raw_rows:
        ticker = str(row.get("platform_id"))
        if ticker not in tickers or ticker in by_ticker:
            raise RuntimeError("terminal response contains an unknown or duplicate ticker")
        if (
            row.get("status") != "RESOLVED"
            or row.get("resolution_type") != "STANDARD"
            or not row.get("resolution_winning_outcome_id")
            or not row.get("resolution_resolved_at")
        ):
            raise RuntimeError(f"market is not standard-resolved: {ticker}")
        winners = [
            side for side in row.get("contract_sides", [])
            if str(side.get("id")) == str(row["resolution_winning_outcome_id"])
        ]
        if len(winners) != 1 or str(winners[0].get("name")).upper() not in {"YES", "NO"}:
            raise RuntimeError(f"winning side is ambiguous: {ticker}")
        by_ticker[ticker] = row
    if set(by_ticker) != set(tickers):
        raise RuntimeError(f"settlement extract expected 252 rows, found {len(by_ticker)}")
    normalized = "".join(
        json.dumps(by_ticker[ticker], sort_keys=True, separators=(",", ":")) + "\n"
        for ticker in tickers
    ).encode()
    output = root / "runs/campaigns_v7" / CAMPAIGN_ID / "terminal/labels.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    pending.write_bytes(normalized)
    pending.replace(output)
    manifest = {
        "schema_version": "klax-v7-terminal-settlements-v1",
        "campaign_id": CAMPAIGN_ID,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "ticker_count": len(tickers),
        "all_daily_freezes_existed_before_query": True,
        "credentials_persisted": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }
    manifest_path = output.with_name("settlements-manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--username")
    args = parser.parse_args()
    username = args.username or os.environ.get("PROBALYTICS_USERNAME", "").strip()
    password = os.environ.get("PROBALYTICS_PASSWORD") or getpass.getpass("Probalytics ClickHouse password: ")
    if not username or not password:
        raise SystemExit("Probalytics credentials are required for the one terminal query")
    try:
        print(json.dumps(run(Path(args.project_root).resolve(), Client(username, password)), indent=2))
    finally:
        password = ""


if __name__ == "__main__":
    main()

