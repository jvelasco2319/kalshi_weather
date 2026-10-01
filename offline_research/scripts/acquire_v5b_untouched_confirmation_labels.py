"""One-shot Probalytics label acquisition after V5B selections are frozen."""

from __future__ import annotations

import argparse
import getpass
from hashlib import sha256
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from acquire_v5b_untouched_probalytics import Client, PROJECT_ROOT


SELECTION_FREEZE = Path("data/manifests/v5b_untouched_selection_freeze.json")
UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
OUTPUT_ROOT = Path("data/raw/v5b_untouched/confirmation")
LABELS = OUTPUT_ROOT / "labels.jsonl"
MANIFEST = OUTPUT_ROOT / "manifest.json"


def canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def label_query(tickers: list[str]) -> str:
    if not tickers or len(tickers) != len(set(tickers)):
        raise ValueError("unique selected-date market tickers are required")
    quoted = ",\n       ".join("'" + ticker.replace("'", "''") + "'" for ticker in tickers)
    return f"""
SELECT DISTINCT
       id,
       platform_id,
       outcomes AS contract_sides,
       resolution_type,
       resolution_winning_outcome_id,
       resolution_outcome_payouts,
       resolution_resolved_at,
       status
FROM markets
WHERE platform = 'KALSHI'
  AND platform_id IN (
       {quoted}
  )
ORDER BY platform_id
FORMAT JSONEachRow
""".strip()


def registered_tickers(root: Path) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    selection = load(root / SELECTION_FREEZE)
    universe = load(root / UNIVERSE)
    if selection.get("self_sha256") != canonical_hash(selection):
        raise ValueError("selection freeze hash mismatch")
    if universe.get("self_sha256") != canonical_hash(universe):
        raise ValueError("universe hash mismatch")
    if (
        selection.get("status") != "FROZEN_BEFORE_OUTCOMES"
        or selection.get("labels_opened") is not False
        or selection.get("outcomes_read") is not False
        or int(selection.get("selected_day_count", 0)) < 30
        or selection.get("universe_self_sha256") != universe.get("self_sha256")
        or universe.get("outcomes_read") is not False
    ):
        raise ValueError("outcome opening is not authorized")
    selected_dates = {row["climate_date"] for row in selection["selections"]}
    tickers = sorted({
        row["market_ticker"] for row in universe["records"]
        if row["climate_date"] in selected_dates and row["contract_side"] == "YES"
    })
    if len(tickers) != len(selected_dates) * 6:
        raise ValueError("selected-date market partition differs")
    return selection, universe, tickers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if (root / MANIFEST).exists() or (root / LABELS).exists():
        raise SystemExit("one-shot confirmation labels already exist")
    selection, universe, tickers = registered_tickers(root)
    username = args.username or os.environ.get("PROBALYTICS_USERNAME", "").strip()
    password = os.environ.get("PROBALYTICS_PASSWORD") or getpass.getpass(
        "Probalytics ClickHouse password: "
    )
    if not username:
        username = input("Probalytics ClickHouse username: ").strip()
    if not username or not password:
        raise SystemExit("Credentials are required")
    payload = Client(username, password).query(label_query(tickers))
    raw_rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        ticker = str(row["platform_id"])
        if ticker not in tickers:
            raise RuntimeError("label response contains an unregistered market")
        prior = by_ticker.get(ticker)
        if prior is not None and prior != row:
            raise RuntimeError("duplicate market label conflicts")
        by_ticker[ticker] = row
    if set(by_ticker) != set(tickers):
        raise RuntimeError("label response is incomplete")
    for ticker, row in by_ticker.items():
        if (
            row.get("status") != "RESOLVED"
            or row.get("resolution_type") != "STANDARD"
            or not row.get("resolution_winning_outcome_id")
            or not row.get("resolution_resolved_at")
        ):
            raise RuntimeError(f"market is not standard-resolved: {ticker}")
        sides = row.get("contract_sides", [])
        winners = [
            side for side in sides
            if str(side.get("id")) == str(row["resolution_winning_outcome_id"])
        ]
        if len(winners) != 1 or str(winners[0].get("name")).upper() not in {"YES", "NO"}:
            raise RuntimeError(f"winning contract side is ambiguous: {ticker}")

    OUTPUT_ROOT_ABS = root / OUTPUT_ROOT
    OUTPUT_ROOT_ABS.mkdir(parents=True, exist_ok=True)
    labels_path = root / LABELS
    normalized_payload = "".join(
        json.dumps(by_ticker[ticker], sort_keys=True, separators=(",", ":")) + "\n"
        for ticker in sorted(by_ticker)
    ).encode("utf-8")
    labels_path.write_bytes(normalized_payload)
    manifest = {
        "schema_version": "klax-v5b-untouched-confirmation-labels-v1",
        "status": "ONE_SHOT_LABEL_OPEN_COMPLETE",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Probalytics historical ClickHouse markets table",
        "source_url": "https://app.probalytics.io/sql",
        "selection_freeze": {
            "path": SELECTION_FREEZE.as_posix(),
            "sha256": file_hash(root / SELECTION_FREEZE),
            "self_sha256": selection["self_sha256"],
        },
        "universe": {
            "path": UNIVERSE.as_posix(),
            "sha256": file_hash(root / UNIVERSE),
            "self_sha256": universe["self_sha256"],
        },
        "selected_day_count": selection["selected_day_count"],
        "registered_market_count": len(tickers),
        "resolved_market_count": len(by_ticker),
        "artifact": {
            "path": LABELS.as_posix(),
            "bytes": labels_path.stat().st_size,
            "sha256": file_hash(labels_path),
        },
        "one_shot_outcome_open_consumed": True,
        "settlement_outcomes_read": True,
        "protected_confirmation_labels_read": True,
        "credentials_persisted": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    manifest["self_sha256"] = canonical_hash(manifest)
    manifest_path = root / MANIFEST
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    password = ""


if __name__ == "__main__":
    main()
