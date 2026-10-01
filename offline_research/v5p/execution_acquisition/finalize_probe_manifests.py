"""Build immutable manifests for the bounded V5P public-source probes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: dict[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "self_sha256"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_manifest(path: Path, value: dict[str, Any]) -> None:
    value["self_sha256"] = canonical_hash(value)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    probes = sorted(path for path in (ROOT / "probes").rglob("*") if path.is_file())
    files = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in probes
    ]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    probe_manifest: dict[str, Any] = {
        "schema_version": 1,
        "campaign": "v5p-partial-execution-evidence",
        "created_at_utc": now,
        "scope": {
            "series": "KXHIGHLAX",
            "date_start": "2025-07-01",
            "date_end": "2026-08-31",
            "decision_time_utc": "18:00:00",
        },
        "probe_files": files,
        "probe_file_count": len(files),
        "probe_bytes_retained": sum(item["bytes"] for item in files),
        "network_sources": [
            "https://archive.pmxt.dev/Kalshi",
            "https://r2.pmxt.dev/kalshi_orderbook_YYYY-MM-DDTHH.parquet",
            "https://huggingface.co/datasets/lerchen3/kalshi-orderbook-alpha",
            "https://huggingface.co/datasets/SamuelTinnerholm/pmxt-data-archive",
            "https://huggingface.co/datasets/rhinot/prediction-market-analysis",
            "https://github.com/Oddpool/PredictionMarketBench",
        ],
        "credentials_used": False,
        "paid_data_purchased": False,
        "live_feed_opened": False,
        "actual_orders_placed": False,
        "protected_confirmation_labels_read": False,
    }
    write_manifest(ROOT / "manifests" / "public-probe-manifest.json", probe_manifest)

    conclusion: dict[str, Any] = {
        "schema_version": 1,
        "campaign": "v5p-partial-execution-evidence",
        "created_at_utc": now,
        "status": "blocked_no_usable_decision_window_evidence_acquired",
        "promotion_ready": False,
        "usable_dates": [],
        "usable_event_count": 0,
        "pmxt": {
            "catalog_status": "public_catalog_indexed_but_download_host_unreachable",
            "catalog_time_start_utc": "2026-05-14T14:00:00Z",
            "catalog_time_end_utc": "2026-06-11T03:00:00Z",
            "candidate_18utc_dates": 28,
            "observed_size_sample_files": 6,
            "observed_size_sample_mb": [18.4, 29.1, 34.5, 94.1, 100.5, 127.2],
            "observed_size_sample_total_mb": 403.8,
            "estimated_28_file_total_mb": 1884.4,
            "estimate_method": "28 multiplied by 67.3 MB sample mean; not a server-reported total",
            "downloaded_files": 0,
            "validated_kxhighlax_files": 0,
            "blocker": "archive host timeout plus local CUJO network filter on PMXT object domains",
        },
        "hugging_face_alpha": {
            "dataset": "lerchen3/kalshi-orderbook-alpha",
            "revision": "50d081abd394db128fc4557d2f0d76f3ec65c060",
            "data_files": 164,
            "declared_bytes": 3471950518,
            "coverage_start_utc": "2026-07-19T02:20:23.251189Z",
            "coverage_end_utc": "2026-07-19T14:26:53.376876Z",
            "range_probe_bytes": 10747904,
            "range_probe_files": 164,
            "range_prefix_hits": 10,
            "bounded_full_probe_target_records": 62,
            "bounded_full_probe_universe_records": 1,
            "bounded_full_probe_first_target_time_utc": "2026-07-19T02:24:52.415302Z",
            "bounded_full_probe_last_target_time_utc": "2026-07-19T02:25:23.114778Z",
            "bounded_full_probe_tickers": [
                "KXHIGHLAX-26JUL18-B72.5",
                "KXHIGHLAX-26JUL18-B74.5",
                "KXHIGHLAX-26JUL18-B76.5",
                "KXHIGHLAX-26JUL18-B78.5",
                "KXHIGHLAX-26JUL18-T72",
                "KXHIGHLAX-26JUL18-T79",
                "KXHIGHLAX-26JUL19-B73.5",
                "KXHIGHLAX-26JUL19-B75.5",
                "KXHIGHLAX-26JUL19-B77.5",
                "KXHIGHLAX-26JUL19-B79.5",
                "KXHIGHLAX-26JUL19-T73",
                "KXHIGHLAX-26JUL19-T80",
            ],
            "probe_event_dates": ["2026-07-18", "2026-07-19"],
            "decision_window_covered": False,
            "full_corpus_acquired": False,
            "reason_not_acquired": "the entire source ends before the registered 18:00 UTC decision window",
        },
        "benchmark": {
            "repository": "Oddpool/PredictionMarketBench",
            "kxhighlax_present": False,
            "only_temperature_episode": "KXHIGHNY-26JAN20",
            "settlement_file_opened": False,
        },
        "failure_reasons": [
            "No retained public file both contains KXHIGHLAX and spans the 18:00 UTC decision window.",
            "PMXT is the only identified free source with candidate 18:00 UTC files, but its hosts were unreachable from this machine.",
            "The public Hugging Face LAX observations end at 14:26:53 UTC and cannot prove executable depth at 18:00 UTC.",
        ],
        "next_required_action": (
            "Restore public access to archive.pmxt.dev/r2.pmxt.dev, then download the 28 PMXT 18:00 UTC files, "
            "hash them, filter KXHIGHLAX rows, and validate their exchange timestamps and size ladders."
        ),
        "credentials_used": False,
        "paid_data_purchased": False,
        "live_feed_opened": False,
        "actual_orders_placed": False,
        "protected_confirmation_labels_read": False,
    }
    write_manifest(ROOT / "manifests" / "coverage-conclusion.json", conclusion)


if __name__ == "__main__":
    main()
