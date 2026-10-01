"""Normalize the post-freeze V7H settlement export."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/replays/v7h-historical-20260804-20260914"
SOURCE = RUN / "outcome_browser_export"
OUTPUT = RUN / "settlement-outcomes.jsonl"


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    by_ticker = {}
    bindings = {}
    for path in sorted(SOURCE.glob("2026-*.json")):
        bindings[path.relative_to(ROOT).as_posix()] = digest(path)
        for ticker, sides, winner_id, status, resolution_type, resolved_at in json.loads(
            path.read_text(encoding="utf-8-sig")
        ):
            if status != "RESOLVED":
                continue
            winners = [side for side in sides if str(side["id"]) == str(winner_id)]
            if len(winners) != 1:
                raise ValueError(f"ambiguous winning side: {ticker}")
            winner = str(winners[0]["name"]).upper()
            if winner not in {"YES", "NO"}:
                raise ValueError(f"invalid winning side: {ticker}/{winner}")
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
    normalized = sorted(by_ticker.values(), key=lambda row: row["platform_id"])
    if len(normalized) != 252:
        raise ValueError(f"expected 252 unique settled markets, found {len(normalized)}")
    OUTPUT.write_text("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in normalized
    ), encoding="utf-8")
    manifest = {
        "schema_version": "klax-v7h-post-freeze-outcomes-v1",
        "status": "OUTCOMES_OPENED_AFTER_PREDICTION_FREEZE",
        "prediction_freeze_path": "runs/replays/v7h-historical-20260804-20260914/prediction-order-freeze.json",
        "prediction_freeze_sha256": digest(RUN / "prediction-order-freeze.json"),
        "target_date_count": 42,
        "resolved_market_count": 252,
        "source": "Probalytics authenticated SQL UI markets table",
        "source_bindings": bindings,
        "artifact_sha256": digest(OUTPUT),
        "actual_orders_placed": False,
    }
    (RUN / "settlement-outcomes-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
