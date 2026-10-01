"""Acquire public KXHIGHLAX order-book records from a pinned Hugging Face revision.

This tool is deliberately read-only with respect to exchanges.  It accepts no
credentials, opens no live feed, and writes only filtered historical market-data
records plus content-addressed manifests beneath the V5P acquisition namespace.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import zlib

import requests


DATASET = "lerchen3/kalshi-orderbook-alpha"
REVISION = "50d081abd394db128fc4557d2f0d76f3ec65c060"
PREFIX = "KXHIGHLAX"
FORBIDDEN_KEYS = {
    "confirmation_label",
    "final_result",
    "resolved_outcome",
    "settled_value",
    "settlement_value",
    "winner",
}
ALLOWED_BOOK_KEYS = {
    "schema_version",
    "record_type",
    "received_ts_ns",
    "received_time",
    "exchange_ts_ms",
    "receive_latency_ms",
    "ticker",
    "series_ticker",
    "shard_id",
    "sid",
    "seq",
    "selection_sources",
    "selection_volume_48h",
    "ignore_qty",
    "book",
    "snapshot_yes_bids",
    "snapshot_no_bids",
}


def _canonical_hash(value: dict[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "self_sha256"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_record(value: dict[str, Any]) -> dict[str, Any] | None:
    forbidden = FORBIDDEN_KEYS.intersection(value)
    if forbidden:
        raise ValueError(f"protected confirmation field encountered: {sorted(forbidden)}")

    record_type = value.get("record_type")
    if record_type == "universe":
        markets = [
            {
                key: market.get(key)
                for key in ("ticker", "series_ticker", "sources", "volume_48h")
                if key in market
            }
            for market in value.get("markets", [])
            if str(market.get("ticker", "")).startswith(PREFIX)
        ]
        if not markets:
            return None
        return {
            "schema_version": value.get("schema_version"),
            "record_type": "universe",
            "received_ts_ns": value.get("received_ts_ns"),
            "received_time": value.get("received_time"),
            "market_count": len(markets),
            "markets": markets,
        }

    if not str(value.get("ticker", "")).startswith(PREFIX):
        return None
    return {key: value.get(key) for key in ALLOWED_BOOK_KEYS if key in value}


def _scan_one(source: dict[str, Any], output_dir: Path, timeout: int) -> dict[str, Any]:
    source_path = source["path"]
    source_name = Path(source_path).name
    url = (
        f"https://huggingface.co/datasets/{DATASET}/resolve/"
        f"{REVISION}/{source_path}"
    )
    response = requests.get(url, stream=True, timeout=(30, timeout))
    response.raise_for_status()

    compressed_hash = hashlib.sha256()
    compressed_bytes = 0
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    line_buffer = b""
    records: list[bytes] = []
    record_types: dict[str, int] = {}
    tickers: set[str] = set()
    first_received: str | None = None
    last_received: str | None = None

    def consume(payload: bytes) -> None:
        nonlocal line_buffer, first_received, last_received
        line_buffer += payload
        while b"\n" in line_buffer:
            line, line_buffer = line_buffer.split(b"\n", 1)
            if PREFIX.encode("ascii") not in line:
                continue
            value = json.loads(line)
            safe = _safe_record(value)
            if safe is None:
                continue
            record_type = str(safe.get("record_type"))
            record_types[record_type] = record_types.get(record_type, 0) + 1
            if record_type == "universe":
                tickers.update(str(item["ticker"]) for item in safe["markets"])
            else:
                tickers.add(str(safe["ticker"]))
            received = safe.get("received_time")
            if received:
                first_received = first_received or str(received)
                last_received = str(received)
            records.append(
                json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )

    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        compressed_hash.update(chunk)
        compressed_bytes += len(chunk)
        consume(decompressor.decompress(chunk))
    consume(decompressor.flush())
    if line_buffer and PREFIX.encode("ascii") in line_buffer:
        safe = _safe_record(json.loads(line_buffer))
        if safe is not None:
            records.append(
                json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )

    actual_sha256 = compressed_hash.hexdigest()
    expected_sha256 = source.get("lfs", {}).get("oid")
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError(
            f"source hash mismatch for {source_name}: {actual_sha256} != {expected_sha256}"
        )
    if compressed_bytes != int(source["size"]):
        raise ValueError(
            f"source length mismatch for {source_name}: {compressed_bytes} != {source['size']}"
        )

    output_path: str | None = None
    output_sha256: str | None = None
    output_bytes = 0
    if records:
        output = output_dir / source_name
        pending = output.with_suffix(output.suffix + ".pending")
        with pending.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as archive:
                for record in records:
                    archive.write(record)
        os.replace(pending, output)
        payload = output.read_bytes()
        output_bytes = len(payload)
        output_sha256 = hashlib.sha256(payload).hexdigest()
        output_path = output.as_posix()

    return {
        "source_path": source_path,
        "source_url": url,
        "source_bytes": compressed_bytes,
        "source_sha256": actual_sha256,
        "source_hash_valid": True,
        "target_records": len(records),
        "record_types": dict(sorted(record_types.items())),
        "tickers": sorted(tickers),
        "first_received_time": first_received,
        "last_received_time": last_received,
        "output_path": output_path,
        "output_bytes": output_bytes,
        "output_sha256": output_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        raise SystemExit("workers must be in [1, 8]")

    tree = json.loads(args.tree.read_text(encoding="utf-8"))
    sources = [item for item in tree if str(item.get("path", "")).endswith(".jsonl.gz")]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_source = {
            pool.submit(_scan_one, source, args.output_dir, args.timeout): source
            for source in sources
        }
        for future in concurrent.futures.as_completed(future_to_source):
            rows.append(future.result())
    rows.sort(key=lambda item: item["source_path"])

    target_rows = [row for row in rows if row["target_records"]]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "campaign": "v5p-partial-execution-evidence",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_dataset": DATASET,
        "source_revision": REVISION,
        "source_license": "other",
        "target_series": PREFIX,
        "requested_date_start": "2025-07-01",
        "requested_date_end": "2026-08-31",
        "registered_decision_time_utc": "18:00:00",
        "files_scanned": len(rows),
        "source_bytes_scanned": sum(row["source_bytes"] for row in rows),
        "files_with_target_records": len(target_rows),
        "target_records": sum(row["target_records"] for row in target_rows),
        "target_tickers": sorted({ticker for row in target_rows for ticker in row["tickers"]}),
        "first_received_time": min(
            (row["first_received_time"] for row in target_rows if row["first_received_time"]),
            default=None,
        ),
        "last_received_time": max(
            (row["last_received_time"] for row in target_rows if row["last_received_time"]),
            default=None,
        ),
        "decision_window_covered": False,
        "decision_window_reason": "source ends at 2026-07-19T14:26:53Z, before 18:00 UTC",
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "credentials_used": False,
        "live_feed_opened": False,
        "files": rows,
    }
    manifest["self_sha256"] = _canonical_hash(manifest)
    pending = args.manifest.with_suffix(args.manifest.suffix + ".pending")
    pending.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(pending, args.manifest)
    print(json.dumps({key: manifest[key] for key in (
        "files_scanned", "source_bytes_scanned", "files_with_target_records",
        "target_records", "first_received_time", "last_received_time", "self_sha256"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
