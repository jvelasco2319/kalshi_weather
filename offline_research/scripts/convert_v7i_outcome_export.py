"""Normalize the post-freeze V7I settlement export."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/replays/v7i-historical-20260730-20260927"
SOURCE = RUN / "outcome_browser_export/all-60-outcomes.json"
FREEZE = RUN / "prediction-order-freeze.json"
OUTPUT = RUN / "settlement-outcomes.jsonl"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8-sig"))
    expected = {
        item["market_ticker"]
        for record in freeze["methods"]["v5b_causal_no"]["records"]
        for item in record["probabilities"]
    }
    if len(expected) != 360 or freeze.get("outcomes_read") is not False:
        raise ValueError("valid 360-market pre-outcome freeze required")
    by_ticker = {}
    for ticker, sides, winner_id, status, resolution_type, resolved_at in json.loads(
        SOURCE.read_text(encoding="utf-8-sig")
    ):
        if ticker not in expected or status != "RESOLVED":
            continue
        winners = [side for side in sides if str(side["id"]) == str(winner_id)]
        if len(winners) != 1:
            raise ValueError(f"ambiguous winning side: {ticker}")
        winner = str(winners[0]["name"]).upper()
        if winner not in {"YES", "NO"} or resolution_type != "STANDARD":
            raise ValueError(f"invalid standard settlement: {ticker}/{winner}")
        row = {
            "platform_id": ticker,
            "contract_sides": sides,
            "resolution_winning_outcome_source_id": winner_id,
            "resolution_winning_outcome_id": winner,
            "status": status,
            "resolution_type": resolution_type,
            "resolution_resolved_at": resolved_at,
        }
        prior = by_ticker.get(ticker)
        if prior is not None and prior != row:
            raise ValueError(f"conflicting resolved market rows: {ticker}")
        by_ticker[ticker] = row
    if set(by_ticker) != expected:
        missing = sorted(expected - set(by_ticker))
        raise ValueError(f"settlement coverage differs: {len(by_ticker)}/360; missing {missing[:3]}")
    normalized = sorted(by_ticker.values(), key=lambda row: row["platform_id"])
    OUTPUT.write_text("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in normalized
    ), encoding="utf-8")
    manifest = {
        "schema_version": "klax-v7i-post-freeze-outcomes-v1",
        "status": "OUTCOMES_OPENED_AFTER_PREDICTION_FREEZE",
        "prediction_freeze_path": FREEZE.relative_to(ROOT).as_posix(),
        "prediction_freeze_sha256": digest(FREEZE),
        "target_date_count": 60,
        "resolved_market_count": len(normalized),
        "source": "Probalytics authenticated SQL UI markets table",
        "source_binding": {SOURCE.relative_to(ROOT).as_posix(): digest(SOURCE)},
        "artifact_sha256": digest(OUTPUT),
        "actual_orders_placed": False,
    }
    (RUN / "settlement-outcomes-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
