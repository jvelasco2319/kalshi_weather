"""Convert the five authenticated-browser archive exports for V7I."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/v7i/browser_export"
OUTPUT = ROOT / "data/raw/v7i/probalytics"
FIELDS = (
    "market_id", "market_platform_id", "outcome_id", "outcome_platform_id",
    "outcome_name", "outcome_index", "target_offset_s", "target_ts",
    "before_observations", "before_timestamp", "before_indexed_at", "before_bids",
    "before_asks", "before_hash", "before_state", "before_continuity",
    "before_path_index", "after_observations", "after_timestamp", "after_indexed_at",
    "after_bids", "after_asks", "after_hash", "after_state", "after_continuity",
    "after_path_index",
)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    expected = {f"2026-07-{day:02d}" for day in (30, 31)} | {
        f"2026-08-{day:02d}" for day in (1, 2, 3)
    }
    found = {path.stem for path in SOURCE.glob("2026-*.json")}
    if found != expected:
        raise ValueError(f"V7I browser exports differ: {sorted(found)}")
    converted = []
    for source in sorted(SOURCE.glob("2026-*.json")):
        day = source.stem
        tuples = json.loads(source.read_text(encoding="utf-8-sig"))
        rows = []
        for values in tuples:
            if len(values) != len(FIELDS):
                raise ValueError(f"unexpected tuple width for {day}: {len(values)}")
            row = {field: value for field, value in zip(FIELDS, values)}
            row["climate_date"] = day
            if int(row["target_offset_s"]) not in {0, 5}:
                raise ValueError(f"unexpected offset for {day}")
            rows.append(row)
        rows.sort(key=lambda row: (
            row["market_platform_id"], int(row["outcome_index"]), int(row["target_offset_s"])
        ))
        target_dir = OUTPUT / f"date={day}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "target_books.jsonl"
        target.write_text("".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
        ), encoding="utf-8")
        manifest = {
            "schema_version": "v7i-probalytics-browser-export-v1",
            "climate_date": day,
            "source": "Probalytics authenticated SQL UI",
            "target_offsets_seconds": [0, 5],
            "record_count": len(rows),
            "outcomes_read": False,
            "actual_orders_placed": False,
            "source_sha256": digest(source),
            "artifact_sha256": digest(target),
        }
        (target_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        converted.append({"climate_date": day, "records": len(rows), "sha256": digest(target)})
    print(json.dumps({"converted_dates": len(converted), "dates": converted}, indent=2))


if __name__ == "__main__":
    main()
