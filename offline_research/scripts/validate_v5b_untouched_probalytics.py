from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data/raw/v5b_untouched/probalytics"
NORMALIZED_ROOT = PROJECT_ROOT / "data/normalized/v5b_untouched_probalytics"
OFFSETS = (0, 5, 30, 60)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top(levels: list[dict[str, object]]) -> tuple[float | None, float | None]:
    if not levels:
        return None, None
    return float(levels[0]["price"]), float(levels[0]["size"])


def main() -> None:
    errors: list[str] = []
    state_path = RAW_ROOT / "recovery-state.json"
    acquisition = json.loads(state_path.read_text(encoding="utf-8"))
    if acquisition.get("status") != "COMPLETE":
        errors.append("acquisition is not complete")
    if acquisition.get("credentials_persisted") is not False:
        errors.append("credential boundary differs")
    if acquisition.get("live_feed_used") is not False:
        errors.append("live-feed boundary differs")
    if acquisition.get("actual_orders_placed") is not False:
        errors.append("order boundary differs")
    if acquisition.get("settlement_outcomes_read") is not False:
        errors.append("settlement-outcome boundary differs")
    if acquisition.get("protected_confirmation_labels_read") is not False:
        errors.append("protected-label boundary differs")

    registered = list(acquisition.get("registered_dates", []))
    completed = list(acquisition.get("completed_dates", []))
    if registered != completed:
        errors.append("registered and completed date lists differ")

    all_rows: list[dict[str, object]] = []
    per_date: dict[str, dict[str, object]] = {}
    artifact_bytes = 0
    for day in registered:
        date.fromisoformat(day)
        day_root = RAW_ROOT / f"date={day}"
        manifest_path = day_root / "manifest.json"
        if not manifest_path.exists():
            errors.append(f"missing day manifest: {day}")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("climate_date") != day:
            errors.append(f"day identity differs: {day}")
        if manifest.get("contains_settlement_outcomes") is not False:
            errors.append(f"outcome boundary differs: {day}")
        for artifact in manifest.get("artifacts", []):
            path = day_root / artifact["path"]
            if not path.exists():
                errors.append(f"missing artifact: {day}/{artifact['path']}")
                continue
            if path.stat().st_size != int(artifact["bytes"]):
                errors.append(f"artifact size differs: {day}/{artifact['path']}")
            if sha256(path) != artifact["sha256"]:
                errors.append(f"artifact hash differs: {day}/{artifact['path']}")
            artifact_bytes += path.stat().st_size
        target_path = day_root / "target_books.jsonl"
        day_rows = [
            json.loads(line)
            for line in target_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(day_rows) != int(manifest["target_rows"]):
            errors.append(f"target row count differs: {day}")
        all_rows.extend(day_rows)
        coverage = manifest["coverage"]
        per_date[day] = {
            "snapshots": int(coverage["snapshots"]),
            "contracts": int(coverage["contracts"]),
            "verified_snapshots": int(coverage["verified"]),
            "intermediate_snapshots": int(coverage["intermediate"]),
            "contiguous_snapshots": int(coverage["contiguous"]),
            "reset_snapshots": int(coverage["resets"]),
            "unknown_continuity_snapshots": int(coverage["unknown"]),
            "target_rows": len(day_rows),
        }

    keys = [
        (
            str(row["climate_date"]),
            str(row["market_platform_id"]),
            str(row["outcome_name"]),
            int(row["target_offset_s"]),
        )
        for row in all_rows
    ]
    if len(keys) != len(set(keys)):
        errors.append("duplicate target key")

    before_state: Counter[str] = Counter()
    after_state: Counter[str] = Counter()
    before_continuity: Counter[str] = Counter()
    after_continuity: Counter[str] = Counter()
    missing_before = 0
    missing_after = 0
    crossed_books = 0
    invalid_levels = 0
    unsorted_books = 0
    top_rows: list[dict[str, object]] = []

    for row in all_rows:
        offset = int(row["target_offset_s"])
        if offset not in OFFSETS:
            errors.append("unexpected target offset")
        day = str(row["climate_date"])
        encoded = date.fromisoformat(day).strftime("%y%b%d").upper()
        if not str(row["market_platform_id"]).startswith(f"KXHIGHLAX-{encoded}-"):
            errors.append(f"ticker/date mismatch: {day}")
        target = pd.Timestamp(row["target_ts"], tz="UTC")
        has_before = int(row["before_observations"]) > 0
        has_after = int(row["after_observations"]) > 0
        before_age_ms = None
        after_delay_ms = None
        if has_before:
            before = pd.Timestamp(row["before_timestamp"], tz="UTC")
            before_age_ms = (target - before).total_seconds() * 1000
            if before_age_ms < 0:
                errors.append("before timestamp exceeds target")
            before_state[str(row["before_state"])] += 1
            before_continuity[str(row["before_continuity"])] += 1
        else:
            missing_before += 1
        if has_after:
            after = pd.Timestamp(row["after_timestamp"], tz="UTC")
            after_delay_ms = (after - target).total_seconds() * 1000
            if after_delay_ms < 0:
                errors.append("after timestamp precedes target")
            after_state[str(row["after_state"])] += 1
            after_continuity[str(row["after_continuity"])] += 1
        else:
            missing_after += 1

        for prefix, available in (("before", has_before), ("after", has_after)):
            if not available:
                continue
            bids = row[f"{prefix}_bids"]
            asks = row[f"{prefix}_asks"]
            bid_prices = [float(level["price"]) for level in bids]
            ask_prices = [float(level["price"]) for level in asks]
            if bid_prices != sorted(bid_prices, reverse=True) or ask_prices != sorted(ask_prices):
                unsorted_books += 1
            if bid_prices and ask_prices and bid_prices[0] >= ask_prices[0]:
                crossed_books += 1
            for level in bids + asks:
                if not (0 < float(level["price"]) < 1 and float(level["size"]) > 0):
                    invalid_levels += 1

        before_bid, before_bid_size = top(row["before_bids"] if has_before else [])
        before_ask, before_ask_size = top(row["before_asks"] if has_before else [])
        after_bid, after_bid_size = top(row["after_bids"] if has_after else [])
        after_ask, after_ask_size = top(row["after_asks"] if has_after else [])
        strict_ready = bool(
            offset == 5
            and has_before
            and before_age_ms is not None
            and before_age_ms <= 5000
            and row["before_state"] == "VERIFIED"
            and row["before_continuity"] == "CONTIGUOUS"
            and before_ask is not None
        )
        top_rows.append(
            {
                "climate_date": day,
                "market_id": row["market_id"],
                "market_platform_id": row["market_platform_id"],
                "outcome_id": row["outcome_id"],
                "outcome_platform_id": row["outcome_platform_id"],
                "outcome_name": row["outcome_name"],
                "outcome_index": int(row["outcome_index"]),
                "target_offset_s": offset,
                "target_ts": row["target_ts"],
                "before_timestamp": row["before_timestamp"] if has_before else None,
                "before_age_ms": before_age_ms,
                "before_state": row["before_state"] if has_before else None,
                "before_continuity": row["before_continuity"] if has_before else None,
                "before_best_bid": before_bid,
                "before_best_bid_size": before_bid_size,
                "before_best_ask": before_ask,
                "before_best_ask_size": before_ask_size,
                "before_spread": before_ask - before_bid if before_ask is not None and before_bid is not None else None,
                "after_timestamp": row["after_timestamp"] if has_after else None,
                "after_delay_ms": after_delay_ms,
                "after_state": row["after_state"] if has_after else None,
                "after_continuity": row["after_continuity"] if has_after else None,
                "after_best_bid": after_bid,
                "after_best_bid_size": after_bid_size,
                "after_best_ask": after_ask,
                "after_best_ask_size": after_ask_size,
                "after_spread": after_ask - after_bid if after_ask is not None and after_bid is not None else None,
                "strict_execution_ready_5s": strict_ready,
            }
        )

    for day, metrics in per_date.items():
        day_ready = [
            row for row in top_rows if row["climate_date"] == day and row["strict_execution_ready_5s"]
        ]
        metrics["strict_ready_outcome_rows_at_5s"] = len(day_ready)
        metrics["strict_ready_markets_at_5s"] = len({row["market_platform_id"] for row in day_ready})
        metrics["has_any_strict_ready_market_at_5s"] = bool(day_ready)

    if crossed_books:
        errors.append("crossed books detected")
    if invalid_levels:
        errors.append("invalid price or size levels detected")
    if unsorted_books:
        errors.append("unsorted books detected")

    NORMALIZED_ROOT.mkdir(parents=True, exist_ok=True)
    parquet_rows = []
    for row in all_rows:
        converted = dict(row)
        converted["before_hash"] = str(converted["before_hash"])
        converted["after_hash"] = str(converted["after_hash"])
        parquet_rows.append(converted)
    books_path = NORMALIZED_ROOT / "target_books.parquet"
    top_path = NORMALIZED_ROOT / "target_top_of_book.parquet"
    pq.write_table(pa.Table.from_pylist(parquet_rows), books_path, compression="zstd")
    pq.write_table(pa.Table.from_pylist(top_rows), top_path, compression="zstd")

    zero_dates = [day for day, metrics in per_date.items() if metrics["snapshots"] == 0]
    strict_dates = [
        day for day, metrics in per_date.items() if metrics["has_any_strict_ready_market_at_5s"]
    ]
    outputs = [
        {
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "rows": pq.read_metadata(path).num_rows,
        }
        for path in (books_path, top_path)
    ]
    summary = {
        "schema_version": "v5b-untouched-probalytics-validation-v1",
        "valid": not errors,
        "errors": sorted(set(errors)),
        "calendar_dates": len(registered),
        "dates_with_snapshots": len(registered) - len(zero_dates),
        "dates_without_snapshots": zero_dates,
        "dates_with_any_strict_ready_market_at_5s": strict_dates,
        "strict_ready_date_count": len(strict_dates),
        "snapshot_count": sum(int(metrics["snapshots"]) for metrics in per_date.values()),
        "date_contract_pairs": sum(int(metrics["contracts"]) for metrics in per_date.values()),
        "target_row_count": len(all_rows),
        "unique_target_key_count": len(set(keys)),
        "missing_before_rows": missing_before,
        "missing_after_rows": missing_after,
        "before_state_counts": dict(sorted(before_state.items())),
        "after_state_counts": dict(sorted(after_state.items())),
        "before_continuity_counts": dict(sorted(before_continuity.items())),
        "after_continuity_counts": dict(sorted(after_continuity.items())),
        "crossed_books": crossed_books,
        "invalid_price_or_size_levels": invalid_levels,
        "unsorted_books": unsorted_books,
        "artifact_bytes_verified": artifact_bytes,
        "credentials_persisted": False,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "live_feed_used": False,
        "orders_placed": False,
        "per_date": per_date,
        "normalized_outputs": outputs,
    }
    validation_path = RAW_ROOT / "validation.json"
    validation_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (NORMALIZED_ROOT / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "v5b-untouched-probalytics-normalization-v1",
                "source_validation": str(validation_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "source_validation_sha256": sha256(validation_path),
                "campaign_evidence_candidate": True,
                "outputs": outputs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if errors:
        raise SystemExit("validation failed")


if __name__ == "__main__":
    main()
