"""Validate and register the one-shot Probalytics UI confirmation export."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from acquire_v5b_untouched_confirmation_labels import (
    LABELS,
    MANIFEST,
    OUTPUT_ROOT,
    SELECTION_FREEZE,
    UNIVERSE,
    canonical_hash,
    file_hash,
    load,
    registered_tickers,
)

WINNERS = OUTPUT_ROOT / "winning-sides.csv"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    labels_path = root / LABELS
    manifest_path = root / MANIFEST
    if labels_path.exists() or manifest_path.exists():
        raise SystemExit("one-shot confirmation labels already exist")

    selection, universe, tickers = registered_tickers(root)
    with (root / WINNERS).open(newline="", encoding="utf-8-sig") as handle:
        source_rows = list(csv.DictReader(handle))
    if len(source_rows) != len(tickers):
        raise RuntimeError("UI export row count differs from frozen partition")

    winners: dict[str, str] = {}
    for row in source_rows:
        ticker = str(row.get("platform_id", "")).strip()
        side = str(row.get("winning_side", "")).strip().upper()
        if ticker in winners or side not in {"YES", "NO"}:
            raise RuntimeError("UI export contains a duplicate or invalid winner")
        winners[ticker] = side
    if set(winners) != set(tickers):
        raise RuntimeError("UI export differs from registered frozen markets")

    by_date: dict[str, list[str]] = {}
    ticker_date = {
        row["market_ticker"]: row["climate_date"]
        for row in universe["records"] if row["contract_side"] == "YES"
    }
    for ticker, side in winners.items():
        by_date.setdefault(ticker_date[ticker], []).append(side)
    if any(len(sides) != 6 or sides.count("YES") != 1 for sides in by_date.values()):
        raise RuntimeError("each selected date must have one winning bracket among six")

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for ticker in sorted(tickers):
        side = winners[ticker]
        normalized.append({
            "platform_id": ticker,
            "contract_sides": [
                {"id": "YES", "platform_id": "yes", "name": "Yes", "index": 0},
                {"id": "NO", "platform_id": "no", "name": "No", "index": 1},
            ],
            "resolution_type": "STANDARD",
            "resolution_winning_outcome_id": side,
            "status": "RESOLVED",
        })
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in normalized
    ).encode("utf-8")
    labels_path.write_bytes(payload)

    manifest: dict[str, Any] = {
        "schema_version": "klax-v5b-untouched-confirmation-labels-v1",
        "status": "ONE_SHOT_LABEL_OPEN_COMPLETE",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Probalytics historical ClickHouse markets table via authenticated SQL UI",
        "source_url": "https://app.probalytics.io/sql",
        "source_query_result_count": len(source_rows),
        "source_query_result_shape": "six exhaustive KXHIGHLAX contracts for each frozen selected date",
        "source_semantic_projection": "platform_id and winning YES/NO side",
        "source_export": {
            "path": WINNERS.as_posix(),
            "bytes": (root / WINNERS).stat().st_size,
            "sha256": file_hash(root / WINNERS),
        },
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
        "resolved_market_count": len(normalized),
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
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": manifest["status"],
        "selected_day_count": manifest["selected_day_count"],
        "resolved_market_count": manifest["resolved_market_count"],
        "labels_sha256": manifest["artifact"]["sha256"],
        "manifest_self_sha256": manifest["self_sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
