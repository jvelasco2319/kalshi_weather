from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CREATED_AT_UTC = "2026-09-27T20:00:00Z"


def dates(first: str, last: str) -> list[str]:
    current = date.fromisoformat(first)
    stop = date.fromisoformat(last)
    out: list[str] = []
    while current <= stop:
        out.append(current.isoformat())
        current += timedelta(days=1)
    return out


def with_self_hash(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("self_sha256", None)
    canonical = json.dumps(
        result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    result["self_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


pmxt_candidate_dates = dates("2026-05-14", "2026-06-10")
cryptostruct_catalogued_dates = dates("2026-08-13", "2026-08-31")

common_no = {
    "usable_18utc_dates": [],
    "protected_confirmation_labels_read": False,
    "actual_orders_placed": False,
}

sources: list[dict[str, Any]] = [
    {
        "source_id": "kalshi_official_historical_api",
        "provider": "Kalshi",
        "source_class": "official_api",
        "urls": [
            "https://docs.kalshi.com/api-reference/historical-api/historical-markets",
            "https://docs.kalshi.com/api-reference/market/get-market-candlesticks",
            "https://docs.kalshi.com/api-reference/market/get-trades",
            "https://docs.kalshi.com/websockets/orderbook-updates",
        ],
        "requested_date_coverage": "Historical market metadata, candles, and trades vary by endpoint; no official historical L2 endpoint is documented.",
        "kxhighlax_proof": "Official APIs can identify the series and contracts, but do not expose a past full-depth book.",
        "decision_window_proof": "No historical 18:00 UTC L2 replay is available from the documented API.",
        "l2_fields": "Current WebSocket snapshot/delta has price levels and quantities; it is a current feed, not a historical archive.",
        "timestamp_semantics": "Current event timestamps only; historical candle/trade times are not order-book event times.",
        "sequence_gap_semantics": "Current WebSocket has per-market sequencing; no historical event chain is exposed.",
        "license_access": "Official API terms; some endpoints or versions require authentication.",
        "price": "API access; no historical L2 product listed.",
        "sample_schema_proof": "Official current-feed schema only.",
        "status": "not_usable_no_historical_l2",
        **common_no,
    },
    {
        "source_id": "pmxt_official_kalshi_archive",
        "provider": "PMXT",
        "source_class": "public_archive",
        "urls": [
            "https://archive.pmxt.dev/Kalshi",
            "https://r2.pmxt.dev/kalshi_orderbook_2026-06-10T18.parquet",
        ],
        "requested_date_coverage": {
            "catalog_start_hour": "2026-05-14T14:00:00Z",
            "catalog_end_hour": "2026-06-11T03:00:00Z",
            "catalogued_18utc_dates": pmxt_candidate_dates,
            "observed_18utc_file_sizes_mb": {
                "2026-05-25": 127.2,
                "2026-05-26": 94.1,
                "2026-06-07": 29.1,
                "2026-06-08": 34.5,
                "2026-06-09": 100.5,
                "2026-06-10": 18.4,
            },
        },
        "kxhighlax_proof": "The catalog is venue-wide. No accessible file was byte-inspected, so KXHIGHLAX presence in an 18:00 file is unproved.",
        "decision_window_proof": "Twenty-eight 18:00 hourly filenames are catalogued, but none was locally opened.",
        "l2_fields": "Independent h5i-db venue adapter documents yes_bids and no_bids snapshots plus signed deltas; NO bids map to YES asks at 1-price.",
        "timestamp_semantics": "Venue timestamp is microseconds but null on snapshots. timestamp_received is a flush stamp reported 8-34 minutes behind venue time with unstable offset.",
        "sequence_gap_semantics": "No sequence numbers. Independent adapter notes incomplete hourly deltas and only about half of later snapshot reconciliations succeed; snapshots are seed-only, not reliable re-seeds.",
        "license_access": "CC BY 4.0 according to the PMXT catalog. Direct host access failed from this environment (TLS/proxy error or timeout).",
        "price": "Free public archive when reachable.",
        "sample_schema_proof": "No PMXT Kalshi file opened locally. Schema and quality evidence comes from h5i-db's PMXT Kalshi adapter source, not a mirror of the data.",
        "status": "catalogued_but_inaccessible_and_continuity_unproved",
        "usable_18utc_dates": [],
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    },
    {
        "source_id": "cryptostruct_kxhighlax",
        "provider": "CryptoStruct",
        "source_class": "commercial_daily_archive",
        "urls": [
            "https://cryptostruct.com/prediction-markets/kalshi-kxhighlax",
            "https://cryptostruct.com/prediction-markets/documentation/protocol",
            "https://cryptostruct.com/prediction-markets/licensing",
        ],
        "requested_date_coverage": {
            "advertised": "Approximately March through September 2026, 192 trading days and about 5.59 GB on the page version inspected.",
            "publicly_catalogued_in_scope_dates": cryptostruct_catalogued_dates,
            "public_catalog_example": {
                "date": "2026-08-31",
                "series_day_size_mb": 56.8,
                "contract_files": 18,
                "l2_updates": 2200000,
            },
        },
        "kxhighlax_proof": "The official product page is series-specific and lists KXHIGHLAX daily bundles.",
        "decision_window_proof": "A complete UTC-day bundle should span 18:00, but no in-scope paid raw file was inspected.",
        "l2_fields": "Per-contract zstd JSONL preserves every trade and L2 update. Protocol documents side, price, quantity, ordercount, full snapshots, and book updates.",
        "timestamp_semantics": "Protocol documents adapterTimestamp in nanoseconds and exchangeTimestamp in nanoseconds or zero; Kalshi-specific capability flags must be checked in a sample header.",
        "sequence_gap_semantics": "Protocol uses prevEventId/eventId and declares adapter ordering capabilities. Kalshi-specific gap behavior still requires sample inspection.",
        "license_access": "Paid download permits internal research, backtests, and models; raw redistribution prohibited; derived results allowed. Purchase/login was not attempted.",
        "price": "EUR 1 per KXHIGHLAX series-day bundle.",
        "sample_schema_proof": "A free, no-account, schema-identical 13.1 MB sample exists for KXHIGHLAX-26SEP03-B78.5, but 2026-09-03 is outside the requested window and was not counted as evidence for an in-scope date.",
        "status": "best_catalogued_in_scope_vendor_but_purchase_required",
        **common_no,
    },
    {
        "source_id": "probalytics_orderbooks",
        "provider": "Probalytics",
        "source_class": "commercial_archive",
        "urls": [
            "https://www.probalytics.io/",
            "https://www.probalytics.io/architecture",
            "https://www.probalytics.io/pricing",
        ],
        "requested_date_coverage": "Kalshi binary order books advertised from 2026-05-09 through August 2026, within the requested interval.",
        "kxhighlax_proof": "Provider states full Kalshi market-set subscription, but no public KXHIGHLAX sample or coverage manifest was found.",
        "decision_window_proof": "Potential full-day coverage; no KXHIGHLAX 18:00 row was publicly verified.",
        "l2_fields": "Full bids/asks price-size arrays on every change, indexed_at, hash, state, continuity, and path_index.",
        "timestamp_semantics": "Millisecond timestamp plus index time; collectors may be seconds behind and quiet markets have sparse rows.",
        "sequence_gap_semantics": "Four collectors, per-market Kalshi sequence-gap detection, automatic resnapshot, and merge on resulting-book hash are advertised.",
        "license_access": "Commercial subscription; research/trading use advertised, raw redistribution restricted. No login/trial used.",
        "price": "Orderbook plan advertised at USD 249/month; trial covers only the latest seven days.",
        "sample_schema_proof": "Public sample demonstrates the general table shape but the visible example is Polymarket, not KXHIGHLAX.",
        "status": "credible_continuity_design_but_no_kxhighlax_sample_proof",
        **common_no,
    },
    {
        "source_id": "probsights_historical_orderbook_api",
        "provider": "ProbSights",
        "source_class": "commercial_api",
        "urls": ["https://probsights.com/p/kalshi-historical-data-api"],
        "requested_date_coverage": "Sixty-day rolling history on the documented Builder plan; only a late-July/August 2026 slice might remain as of 2026-09-27.",
        "kxhighlax_proof": "No public KXHIGHLAX sample or indexed-universe manifest found.",
        "decision_window_proof": "One-minute or five-minute sampling can bracket 18:00 but is not raw event replay.",
        "l2_fields": "yes_bids, no_bids, best_yes_bid/ask, mid, and spread.",
        "timestamp_semantics": "Interval timestamps in Unix seconds.",
        "sequence_gap_semantics": "Periodic snapshots; no raw per-event sequence or gap semantics documented.",
        "license_access": "Plan/API access and authentication required; not used.",
        "price": "Builder-plan price was not publicly verified in this pass.",
        "sample_schema_proof": "Documentation example only; no KXHIGHLAX response inspected.",
        "status": "coarse_and_unproved_for_ticker",
        **common_no,
    },
    {
        "source_id": "hf_lerchen3_kalshi_orderbook_alpha",
        "provider": "lerchen3 on Hugging Face",
        "source_class": "public_dataset",
        "urls": ["https://huggingface.co/datasets/lerchen3/kalshi-orderbook-alpha"],
        "requested_date_coverage": "Pinned revision 50d081abd394db128fc4557d2f0d76f3ec65c060; 2026-07-19 approximately 02:20:23-14:26:53 UTC.",
        "kxhighlax_proof": "Local bounded probes contain KXHIGHLAX records.",
        "decision_window_proof": "Capture ends at 14:26:53 UTC, before 18:00.",
        "l2_fields": "Snapshots and state derived after deltas, including top-of-book depth levels; README describes a top-200 rolling-volume universe.",
        "timestamp_semantics": "Receipt and exchange timing fields are present in the probed format.",
        "sequence_gap_semantics": "Sequence/state fields exist, but the README does not guarantee continuity for every market.",
        "license_access": "Public Hugging Face dataset; license metadata is 'other'.",
        "price": "Free.",
        "sample_schema_proof": "Yes. Existing local bounded probes verify schema and KXHIGHLAX rows, but not the decision hour.",
        "status": "verified_kxhighlax_schema_but_no_18utc",
        **common_no,
    },
    {
        "source_id": "prediction_market_bench",
        "provider": "PredictionMarketBench authors",
        "source_class": "academic_dataset",
        "urls": ["https://github.com/declare-lab/PredictionMarketBench"],
        "requested_date_coverage": "Public benchmark episodes; inspected weather example is KXHIGHNY-26JAN20.",
        "kxhighlax_proof": "No KXHIGHLAX episode found.",
        "decision_window_proof": "None for KXHIGHLAX.",
        "l2_fields": "Benchmark observations may include market context, not a verified KXHIGHLAX raw event chain.",
        "timestamp_semantics": "Episode times only.",
        "sequence_gap_semantics": "Not a replay-quality order-book archive.",
        "license_access": "MIT repository.",
        "price": "Free.",
        "sample_schema_proof": "Weather sample exists for another series only.",
        "status": "wrong_series",
        **common_no,
    },
    {
        "source_id": "hf_rhinot_prediction_market_analysis",
        "provider": "rhinot on Hugging Face",
        "source_class": "public_dataset",
        "urls": ["https://huggingface.co/datasets/rhinot/prediction-market-analysis"],
        "requested_date_coverage": "Large approximately 36 GB compressed corpus; repository tree exposes tar archives.",
        "kxhighlax_proof": "README names Kalshi market/trade directories, not an order-book archive; no KXHIGHLAX L2 proof.",
        "decision_window_proof": "None.",
        "l2_fields": "No Kalshi L2 fields documented.",
        "timestamp_semantics": "Market/trade data only.",
        "sequence_gap_semantics": "None for L2.",
        "license_access": "Public Hugging Face repository; bulk archive was intentionally not downloaded.",
        "price": "Free.",
        "sample_schema_proof": "No relevant sample.",
        "status": "not_l2_and_bulk_irrelevant",
        **common_no,
    },
    {
        "source_id": "github_jdkatz21_prediction_markets_public",
        "provider": "jdkatz21",
        "source_class": "academic_repository",
        "urls": ["https://github.com/jdkatz21/Prediction_Markets_Public"],
        "requested_date_coverage": "README references data/orderbook_data, but the recursive repository tree contains no data directory.",
        "kxhighlax_proof": "No KXHIGHLAX data. Scraper targets KXCPIYOY.",
        "decision_window_proof": "None.",
        "l2_fields": "The misleadingly named script collects daily candlestick bid/ask OHLC, volume, and open interest, not L2.",
        "timestamp_semantics": "Daily candle periods.",
        "sequence_gap_semantics": "None.",
        "license_access": "Public GitHub code; advertised dataset absent.",
        "price": "Free.",
        "sample_schema_proof": "Source code proves the data is daily candle output, not depth.",
        "status": "data_absent_and_wrong_grain",
        **common_no,
    },
    {
        "source_id": "kaggle_search",
        "provider": "Kaggle search",
        "source_class": "repository_search",
        "urls": ["https://www.kaggle.com/search?q=KXHIGHLAX", "https://www.kaggle.com/search?q=Kalshi+orderbook"],
        "requested_date_coverage": "No relevant KXHIGHLAX L2 dataset located by explicit searches.",
        "kxhighlax_proof": "None.",
        "decision_window_proof": "None.",
        "l2_fields": "None.",
        "timestamp_semantics": "None.",
        "sequence_gap_semantics": "None.",
        "license_access": "No candidate found; no login used.",
        "price": "Not applicable.",
        "sample_schema_proof": "None.",
        "status": "no_relevant_result",
        **common_no,
    },
    {
        "source_id": "zenhodl_kalshi_orderbook",
        "provider": "ZenHodl",
        "source_class": "public_or_vendor_archive",
        "urls": ["https://zenhodl.com/kalshi/orderbooks"],
        "requested_date_coverage": "Capture advertised from 2026-06-21 with roughly 28-second snapshots.",
        "kxhighlax_proof": "Documented ticker families are sports; no KXHIGHLAX.",
        "decision_window_proof": "None for requested series.",
        "l2_fields": "Up to 20 levels.",
        "timestamp_semantics": "Periodic observations.",
        "sequence_gap_semantics": "No event-chain replay semantics.",
        "license_access": "Public/vendor page; no account used.",
        "price": "Not material because series is absent.",
        "sample_schema_proof": "Schema does not prove weather coverage.",
        "status": "wrong_ticker_families",
        **common_no,
    },
    {
        "source_id": "depthfeed_and_kalshipricedata",
        "provider": "DepthFeed / KalshiPriceData",
        "source_class": "commercial_archive",
        "urls": ["https://depthfeed.com/", "https://kalshipricedata.com/"],
        "requested_date_coverage": "Historical full depth is advertised for selected Kalshi products.",
        "kxhighlax_proof": "Documented series are crypto families such as KX{ASSET}15M, not KXHIGHLAX.",
        "decision_window_proof": "None for requested series.",
        "l2_fields": "Up to 100 depth levels with millisecond observation times.",
        "timestamp_semantics": "Recorder observation time; exact venue-time semantics not verified.",
        "sequence_gap_semantics": "Not verified for requested series.",
        "license_access": "Commercial; no purchase/login.",
        "price": "Not evaluated because series is absent.",
        "sample_schema_proof": "No KXHIGHLAX sample.",
        "status": "wrong_ticker_families",
        **common_no,
    },
    {
        "source_id": "kalshibacktest",
        "provider": "KalshiBackTest",
        "source_class": "public_archive",
        "urls": ["https://kalshibacktest.com/"],
        "requested_date_coverage": "Free full L2 snapshots advertised for supported series.",
        "kxhighlax_proof": "Supported products are BTC, ETH, SOL, DOGE, and XRP; no weather.",
        "decision_window_proof": "None.",
        "l2_fields": "Up to 50 levels at 100 ms snapshots.",
        "timestamp_semantics": "Snapshot observation times.",
        "sequence_gap_semantics": "Periodic snapshots, not a requested-series event chain.",
        "license_access": "Public; no account used.",
        "price": "Free for advertised crypto sets.",
        "sample_schema_proof": "No KXHIGHLAX sample.",
        "status": "wrong_ticker_families",
        **common_no,
    },
    {
        "source_id": "apify_and_pipeworx",
        "provider": "Apify actors / Pipeworx",
        "source_class": "current_scraper_or_api",
        "urls": ["https://apify.com/store?search=kalshi", "https://pipeworx.io/"],
        "requested_date_coverage": "Current/live scraping or API products; no documented retrospective KXHIGHLAX L2 archive found.",
        "kxhighlax_proof": "No historical KXHIGHLAX depth sample.",
        "decision_window_proof": "None.",
        "l2_fields": "Current market fields may exist; historical replay not demonstrated.",
        "timestamp_semantics": "Current scrape/API time.",
        "sequence_gap_semantics": "No historical gap contract.",
        "license_access": "Would require service/API use; prohibited live collection was not attempted.",
        "price": "Not evaluated.",
        "sample_schema_proof": "None for the historical need.",
        "status": "not_historical_and_out_of_scope",
        **common_no,
    },
    {
        "source_id": "pmxt_alternate_mirrors",
        "provider": "PendulumFlow and Hugging Face mirrors",
        "source_class": "mirror_search",
        "urls": [
            "https://archive.pendulumflow.com/",
            "https://huggingface.co/datasets/SamuelTinnerholm/pmxt-data-archive",
            "https://huggingface.co/datasets/phobia76/pmxt-l2-dump",
            "https://huggingface.co/datasets/grkkaya/pmxt-l2-dump",
            "https://huggingface.co/datasets/Joseph3222/polymarket-orderbook",
        ],
        "requested_date_coverage": "Repositories and catalogs inspected are Polymarket-only; no Kalshi PMXT files found.",
        "kxhighlax_proof": "None.",
        "decision_window_proof": "None.",
        "l2_fields": "Polymarket schemas are irrelevant to Kalshi KXHIGHLAX.",
        "timestamp_semantics": "Not applicable.",
        "sequence_gap_semantics": "Not applicable.",
        "license_access": "Public listings; no bulk download.",
        "price": "Free where public.",
        "sample_schema_proof": "Repository trees prove these are not alternate Kalshi mirrors.",
        "status": "no_alternate_kalshi_mirror_found",
        **common_no,
    },
    {
        "source_id": "internet_archive_and_common_crawl",
        "provider": "Internet Archive / Common Crawl",
        "source_class": "web_archive_search",
        "urls": ["https://web.archive.org/", "https://index.commoncrawl.org/"],
        "requested_date_coverage": "Exact PMXT filenames and domains were searched; no independent downloadable object was found.",
        "kxhighlax_proof": "None.",
        "decision_window_proof": "None.",
        "l2_fields": "No archived parquet body inspected.",
        "timestamp_semantics": "Not applicable.",
        "sequence_gap_semantics": "Not applicable.",
        "license_access": "Index/API probes returned 503/404/400 from this environment. This is unresolved, not proof that no capture exists.",
        "price": "Free if a capture exists.",
        "sample_schema_proof": "None.",
        "status": "unresolved_no_verified_capture",
        **common_no,
    },
    {
        "source_id": "hf_price_and_journal_datasets",
        "provider": "tsedbroo / openthomas on Hugging Face",
        "source_class": "public_dataset",
        "urls": [
            "https://huggingface.co/datasets/tsedbroo/kalshi-forecast-alpha",
            "https://huggingface.co/datasets/openthomas/journal",
        ],
        "requested_date_coverage": "Forecast, price, journal, or outcome rows; openthomas includes KXHIGHLAX around 2026-07-08.",
        "kxhighlax_proof": "Ticker presence in non-L2 data only.",
        "decision_window_proof": "No full-depth 18:00 state.",
        "l2_fields": "None.",
        "timestamp_semantics": "Prediction/price timestamps, not book events.",
        "sequence_gap_semantics": "None.",
        "license_access": "Public Hugging Face repositories.",
        "price": "Free.",
        "sample_schema_proof": "Schemas prove these are not L2 archives.",
        "status": "not_l2",
        **common_no,
    },
    {
        "source_id": "papers_and_methodology",
        "provider": "Academic papers",
        "source_class": "literature",
        "urls": [
            "https://arxiv.org/search/?query=Kalshi+limit+order+book&searchtype=all",
            "https://scholar.google.com/scholar?q=Kalshi+order+book+data",
        ],
        "requested_date_coverage": "Papers found discuss reconstruction, market makers/takers, calibration, or prices/trades; no downloadable KXHIGHLAX L2 archive was located.",
        "kxhighlax_proof": "None.",
        "decision_window_proof": "None.",
        "l2_fields": "Methodology only or trade/price data.",
        "timestamp_semantics": "Study-specific.",
        "sequence_gap_semantics": "No reusable raw archive.",
        "license_access": "Public abstracts/papers.",
        "price": "Free where open access.",
        "sample_schema_proof": "No relevant downloadable sample.",
        "status": "methodology_only",
        **common_no,
    },
    {
        "source_id": "anonymous_depth_vendor_claim",
        "provider": "Unverified individual post",
        "source_class": "unverified_claim",
        "urls": ["https://www.reddit.com/search/?q=Kalshi%20historical%20orderbook%20data"],
        "requested_date_coverage": "Claimed ability to sell historical Kalshi depth under a distribution license; dates not evidenced.",
        "kxhighlax_proof": "None.",
        "decision_window_proof": "None.",
        "l2_fields": "Claim only.",
        "timestamp_semantics": "Not documented.",
        "sequence_gap_semantics": "Not documented.",
        "license_access": "Identity, authority, license, terms, and chain of custody are unverified. No contact or purchase attempted.",
        "price": "Unknown.",
        "sample_schema_proof": "None.",
        "status": "reject_unverified_provenance",
        **common_no,
    },
    {
        "source_id": "h5i_db_pmxt_adapter",
        "provider": "h5i-db / h5i-db-venues",
        "source_class": "open_source_schema_and_quality_evidence",
        "urls": [
            "https://github.com/Koukyosyumei/h5i-db",
            "https://raw.githubusercontent.com/Koukyosyumei/h5i-db/main/crates/h5i-db-python/python/h5i_db/venues/_archive.py",
        ],
        "requested_date_coverage": "No data mirror; adapter source describes PMXT Kalshi behavior.",
        "kxhighlax_proof": "No ticker-specific data.",
        "decision_window_proof": "None.",
        "l2_fields": "Documents yes_bids/no_bids snapshots and signed resting-size deltas.",
        "timestamp_semantics": "Documents microsecond venue timestamps, null snapshot venue times, and delayed flush-style receive stamps.",
        "sequence_gap_semantics": "Explicitly documents no sequence numbers, incomplete hourly deltas, and poor snapshot reconciliation.",
        "license_access": "Public GitHub/Python package source.",
        "price": "Free.",
        "sample_schema_proof": "Strong independent schema/quality evidence, but not a data archive.",
        "status": "quality_evidence_only",
        **common_no,
    },
]

matrix = with_self_hash(
    {
        "schema_version": "klax-v5p-kxhighlax-l2-source-matrix-v1",
        "created_at_utc": CREATED_AT_UTC,
        "scope": {
            "series": "KXHIGHLAX",
            "decision_time_utc": "18:00:00",
            "requested_start_date": "2025-07-01",
            "requested_end_date": "2026-08-31",
            "priority_dates": "2026-06-01..2026-08-31",
            "required_content": "Historical L2 price and quantity sufficient to reconstruct or verify the executable book at the registered decision time.",
        },
        "evidence_policy": {
            "usable_definition": "A date is usable only after a byte-inspected in-scope source proves KXHIGHLAX depth across 18:00 UTC, with prices and quantities, timing semantics, and an explicit continuity/gap disposition. Catalog claims alone do not qualify.",
            "catalogued_definition": "A public catalog or vendor page names the relevant venue/series/date but the actual in-scope file has not been inspected.",
            "no_assumed_fills": True,
            "outcome_labels_required_for_search": False,
        },
        "exact_verified_usable_18utc_dates": [],
        "catalogued_but_not_verified_candidates": {
            "pmxt_18utc_hour_files": pmxt_candidate_dates,
            "cryptostruct_in_scope_daily_rows_visible_in_public_catalog": cryptostruct_catalogued_dates,
            "probalytics_advertised_possible_interval": "2026-05-09..2026-08-31",
        },
        "headline_findings": [
            "No requested date currently has a locally byte-verified KXHIGHLAX L2 book spanning 18:00 UTC.",
            "PMXT is the best free lead by filename coverage, but its host was unreachable and independent adapter evidence shows no sequence numbers, incomplete deltas, unstable receive stamps, and unreliable snapshot reconciliation.",
            "CryptoStruct has the clearest series-specific public catalog and EUR 1 daily access, but using an in-window file would require a purchase that was not authorized or attempted.",
            "Probalytics advertises the strongest continuity controls, but no public KXHIGHLAX sample or export manifest proves ticker/date coverage.",
            "The public Hugging Face alpha sample proves KXHIGHLAX schema but stops before 18:00 UTC.",
        ],
        "sources": sources,
        "search_limits": {
            "multi_gb_irrelevant_sets_downloaded": False,
            "purchases_made": False,
            "logins_or_credentials_used": False,
            "current_or_live_feeds_accessed": False,
            "internet_archive_common_crawl_negative_is_conclusive": False,
        },
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
)

plan = with_self_hash(
    {
        "schema_version": "klax-v5p-kxhighlax-l2-acquisition-plan-v1",
        "created_at_utc": CREATED_AT_UTC,
        "source_matrix_path": "v5p/execution_acquisition/l2_search/source-matrix.json",
        "source_matrix_sha256": matrix["self_sha256"],
        "current_status": "blocked_no_verified_18utc_l2_file",
        "exact_verified_usable_18utc_dates": [],
        "best_next_option": {
            "option": "bounded_pmxt_single_file_reachability_and_schema_probe",
            "why": "It is the only free lead with explicit 18:00 hourly filenames in the priority period, and the smallest observed candidate is only 18.4 MB.",
            "first_object": "https://r2.pmxt.dev/kalshi_orderbook_2026-06-10T18.parquet",
            "first_date": "2026-06-10",
            "estimated_first_object_mb": 18.4,
            "stop_after_first_file": True,
            "steps": [
                "Retry the exact object only when the PMXT origin or a provenance-preserving mirror is reachable; never substitute a current feed.",
                "Stream to a new V5P raw cache, enforce a 25 MB first-probe byte limit, compute SHA-256, and retain response headers and retrieval time.",
                "Inspect Parquet metadata before row materialization. Confirm KXHIGHLAX identifiers, yes_bids/no_bids or equivalent ladders, signed-delta conventions, and time units.",
                "Filter only KXHIGHLAX rows and test whether venue-time evidence spans 17:55:00-18:05:00 UTC.",
                "Attempt snapshot-plus-delta reconstruction without forward filling across a gap. Reconcile the last reconstructed state with a later snapshot where possible.",
                "Stop after this file if KXHIGHLAX is absent, 18:00 cannot be tied to venue time, a seed is missing, deltas are incomplete, or continuity cannot be bounded.",
                "If and only if the first file passes, acquire the remaining 27 18:00 files one at a time under an approximately 2.5 GB total byte ceiling and repeat identical gates.",
            ],
            "candidate_dates_if_first_probe_passes": pmxt_candidate_dates,
            "six_observed_file_mean_total_estimate_gb": 1.884,
            "important_limitation": "PMXT's documented lack of sequences and incomplete deltas means even an accessible file may be diagnostic only and may fail the no-assumed-fill gate.",
        },
        "schema_proof_before_any_purchase": {
            "source": "CryptoStruct free KXHIGHLAX-26SEP03-B78.5 sample",
            "size_mb": 13.1,
            "purpose": "Prove the Kalshi-specific file header, event IDs, timestamp capabilities, L2 fields, and parser against a schema-identical free sample.",
            "scientific_limit": "The sample date 2026-09-03 is outside the requested interval and must never be counted as campaign evidence.",
        },
        "best_purchase_contingency_requires_new_user_authorization": {
            "source": "CryptoStruct",
            "single_day": "2026-08-31",
            "advertised_size_mb": 56.8,
            "advertised_contract_files": 18,
            "advertised_l2_updates": 2200000,
            "advertised_price_eur": 1,
            "reason": "The public catalog directly names the series and date, a full-day bundle should include 18:00, and a one-day test bounds cost and data volume.",
            "action_in_current_task": "Do not purchase, log in, or download a paid object.",
        },
        "vendor_sample_request_contingency": {
            "source": "Probalytics",
            "request_before_subscription": "A no-login sample or signed coverage manifest for one KXHIGHLAX contract/date at 17:55-18:05 UTC, including sequence-gap and resnapshot markers.",
            "why": "Its advertised sequence-gap handling is stronger than PMXT, but a USD 249/month subscription is unjustified without ticker-level evidence.",
        },
        "rejection_or_promotion_gates_per_date": [
            {
                "gate": "provenance",
                "pass": "Immutable URL/object identifier, retrieval timestamp, license/terms, byte count, and SHA-256 are recorded.",
            },
            {
                "gate": "ticker",
                "pass": "Rows unambiguously identify the intended KXHIGHLAX event/market contracts.",
            },
            {
                "gate": "window",
                "pass": "Venue-time or independently bounded time evidence spans 17:55:00-18:05:00 UTC, including the registered 18:00 decision instant.",
            },
            {
                "gate": "book_content",
                "pass": "Both executable sides can be represented with price and resting quantity at required depth; trade-only, OHLC, or midpoint data fail.",
            },
            {
                "gate": "seed_and_chain",
                "pass": "A valid snapshot exists before the decision and every applied delta's semantics are known.",
            },
            {
                "gate": "continuity",
                "pass": "Per-market sequence continuity is proven, or an explicit no-gap bound and reconciliation test passes. Missing sequence with unexplained flush gaps fails.",
            },
            {
                "gate": "time_semantics",
                "pass": "Venue event time and recorder/receipt time are distinguished; no delayed flush timestamp is treated as executable time.",
            },
            {
                "gate": "staleness",
                "pass": "The last valid state at 18:00 is within the frozen campaign staleness tolerance. The acquisition plan does not weaken that tolerance.",
            },
            {
                "gate": "no_assumed_fill",
                "pass": "A proposed fill is bounded by visible price and quantity with the frozen latency/queue policy; otherwise the date may support diagnostics but not promotion.",
            },
        ],
        "fallbacks_not_recommended": [
            "ProbSights one-minute snapshots cannot prove event-level queue or gap continuity.",
            "Current/live scrapers cannot reconstruct past dates and are outside the offline campaign scope.",
            "Trade, candle, midpoint, forecast, or outcome datasets must not be relabeled as L2.",
            "Anonymous sellers without verifiable license, schema sample, coverage manifest, and chain of custody are rejected.",
            "Large Polymarket archives and unrelated Kalshi crypto/sports archives should not be downloaded.",
        ],
        "expected_outputs_after_a_successful_probe": [
            "raw-object-manifest.json with URL, headers, byte count, SHA-256, and license",
            "schema-verification.json with exact field and unit mappings",
            "kxhighlax-window-census.json listing only byte-verified dates",
            "continuity-audit.json with seed, sequence/gap, resnapshot, and reconciliation evidence",
            "promotion-readiness.json that fails closed when any required gate is unknown",
        ],
        "budget_and_safety": {
            "first_probe_byte_limit": 25000000,
            "conditional_pmxt_total_byte_limit": 2500000000,
            "max_concurrent_downloads": 1,
            "no_live_or_current_feeds": True,
            "no_credentials": True,
            "no_orders": True,
            "no_protected_outcome_label_reads": True,
            "no_purchase_without_separate_explicit_authorization": True,
        },
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
)

write_json(ROOT / "source-matrix.json", matrix)
write_json(ROOT / "acquisition-plan.json", plan)

readme = f"""# KXHIGHLAX historical Level-2 archive search

This directory records the V5P search for historical KXHIGHLAX order books at the frozen 18:00 UTC decision time. It is an acquisition audit, not a claim of fills or profitability.

## Conclusion

**Exact byte-verified usable 18:00 UTC dates: 0.**

No requested date currently has an inspected KXHIGHLAX full-depth book spanning 18:00 UTC with adequate timestamp and continuity evidence. The difference between a filename or vendor catalog and usable execution evidence is deliberate: a catalogued file is not promoted until its bytes, ticker, time window, quantities, and gap semantics pass the gates in `acquisition-plan.json`.

## Strongest leads

| Rank | Lead | What is proven | What is missing | Current disposition |
|---|---|---|---|---|
| 1 | PMXT | 28 public 18:00 filenames from 2026-05-14 through 2026-06-10; CC BY 4.0 | Host access, KXHIGHLAX presence, and continuity | Best free bounded probe, but likely diagnostic-only because independent adapter evidence reports no sequences and incomplete deltas |
| 2 | CryptoStruct | Series-specific daily catalog; public rows for 2026-08-13 through 2026-08-31; documented full L2 protocol | An inspected in-window paid file | Best low-cost contingency after a free schema sample; purchase was not attempted |
| 3 | Probalytics | Strong advertised per-market gap detection, resnapshot, and book-hash merge | Public KXHIGHLAX sample or ticker/date coverage manifest | Technically credible but too expensive to subscribe without ticker-level proof |
| 4 | Hugging Face alpha | Actual local KXHIGHLAX rows and L2 schema | Capture stops at 14:26:53 UTC on 2026-07-19 | Useful parser/schema fixture only; no decision-time evidence |

## Most important data-quality finding

The open-source `h5i-db` PMXT adapter independently documents that PMXT Kalshi files have no sequence numbers, can contain incomplete hourly deltas, use delayed flush-style receive timestamps, and reconcile successfully to later snapshots only about half the time. PMXT must therefore pass a strict per-file reconstruction and reconciliation test. Accessibility alone would not make its rows execution-grade.

## Files

- `source-matrix.json`: source-by-source coverage, schema, timing, gap, license, price, and sample evidence. Self SHA-256: `{matrix['self_sha256']}`.
- `acquisition-plan.json`: bounded next steps and fail-closed gates. Self SHA-256: `{plan['self_sha256']}`.
- `build_search_artifacts.py`: deterministic artifact builder.

## Safety state

No purchase, account login, credential, current/live feed, order, or protected confirmation label was used. No multi-gigabyte irrelevant dataset was downloaded.
"""
(ROOT / "README.md").write_text(readme, encoding="utf-8")

print(json.dumps({
    "source_matrix": str(ROOT / "source-matrix.json"),
    "source_matrix_sha256": matrix["self_sha256"],
    "acquisition_plan": str(ROOT / "acquisition-plan.json"),
    "acquisition_plan_sha256": plan["self_sha256"],
    "source_count": len(sources),
    "verified_usable_dates": 0,
}, indent=2))
