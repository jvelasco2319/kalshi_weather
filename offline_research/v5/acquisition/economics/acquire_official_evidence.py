"""Acquire bounded, outcome-blind V5 economics evidence from official sources.

This program is intentionally separate from the offline economics audit.  It
performs a finite public acquisition, retains only event metadata needed to
bind fees and settlement rules, and never requests nested markets, outcomes,
orders, or fills.  Every saved object is content addressed in the source
manifest so a later offline audit can run with network access disabled.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen


SCHEMA_VERSION = "klax-v5-economics-official-acquisition-v1"
BASE = "https://external-api.kalshi.com/trade-api/v2"
USER_AGENT = "klax-v5-economics-acquisition/1.0 (finite historical research)"
START = "2025-01-01"
END = "2026-08-31"
_EVENT = re.compile(r"^KXHIGHLAX-(\d{2})([A-Z]{3})(\d{2})$")
_MONTH = {name: i for i, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
)}

# The archived documents are immutable captures of the primary Kalshi PDF.
# The CFTC and assets.kalshi.com documents are direct primary sources.
DOCUMENTS = (
    ("cftc-fee-schedule-2022.pdf", "cftc_filing",
     "https://www.cftc.gov/sites/default/files/filings/orgrules/22/09/rule091222kexdcm003.pdf"),
    ("cftc-volume-rebate-filing-2025-01-13.pdf", "cftc_filing",
     "https://www.cftc.gov/sites/default/files/filings/orgrules/25/01/rules01132513688.pdf"),
    ("cftc-vip-termination-and-replacement-2025-08-31.pdf", "cftc_filing",
     "https://www.cftc.gov/filings/orgrules/rules08312529801.pdf"),
    ("cftc-vip-update-2026-08-04.pdf", "cftc_filing",
     "https://www.cftc.gov/filings/orgrules/rules08042613748.pdf"),
    ("cftc-laxhigh-certification-2024-12-04.pdf", "cftc_product_certification",
     "https://www.cftc.gov/sites/default/files/filings/ptc/24/12/ptc12042410310.pdf"),
    ("kalshi-globaltemperature-certification-2025-12-08.pdf", "kalshi_product_certification",
     "https://assets.kalshi.com/regulatory/product-certifications/GLOBALTEMPERATURE.pdf"),
    ("kalshi-globaltemperature-contract-terms-current.pdf", "kalshi_contract_terms",
     "https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf"),
    ("kalshi-fee-schedule-effective-2026-07-07.pdf", "kalshi_official_current",
     "https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
    ("kalshi-fee-schedule-capture-2025-07-08.pdf", "archived_kalshi_primary",
     "https://web.archive.org/web/20250708150126id_/https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
    ("kalshi-fee-schedule-capture-2025-09-17.pdf", "archived_kalshi_primary",
     "https://web.archive.org/web/20250917164753id_/https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
    ("kalshi-fee-schedule-capture-2025-10-08.pdf", "archived_kalshi_primary",
     "https://web.archive.org/web/20251008232930id_/https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
    ("kalshi-fee-schedule-capture-2026-02-14.pdf", "archived_kalshi_primary",
     "https://web.archive.org/web/20260214014036id_/https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
)

DOC_PAGES = (
    ("fee-rounding.html", "kalshi_api_docs", "https://docs.kalshi.com/getting_started/fee_rounding"),
    ("market-settlement.html", "kalshi_api_docs", "https://docs.kalshi.com/getting_started/market_settlement"),
    ("series-fee-changes.html", "kalshi_api_docs", "https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes"),
    ("event-fee-changes.html", "kalshi_api_docs", "https://docs.kalshi.com/api-reference/events/get-event-fee-changes"),
    ("get-series.html", "kalshi_api_docs", "https://docs.kalshi.com/api-reference/market/get-series"),
    ("get-events.html", "kalshi_api_docs", "https://docs.kalshi.com/api-reference/events/get-events"),
)


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _get(url: str, attempts: int = 4) -> tuple[bytes, dict[str, str]]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urlopen(req, timeout=120) as response:
                data = response.read()
                return data, {
                    "content_type": response.headers.get("Content-Type", ""),
                    "final_url": response.geturl(),
                    "status": str(response.status),
                }
        except Exception as exc:  # finite retry is recorded in final failures
            last = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def _write(path: Path, data: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != data:
        raise RuntimeError(f"refusing to replace different source: {path}")
    path.write_bytes(data)
    return {"path": path.as_posix(), "bytes": len(data), "sha256": sha256(data).hexdigest()}


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def _event_day(ticker: str) -> str | None:
    match = _EVENT.fullmatch(ticker)
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return f"20{year}-{_MONTH[month]:02d}-{int(day):02d}"
    except (KeyError, ValueError):
        return None


def _api_json(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    raw, headers = _get(url)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"API response is not an object: {url}")
    return value, headers


def _event_record(value: dict[str, Any]) -> dict[str, Any]:
    """Whitelisted event metadata; deliberately excludes markets and results."""
    ticker = str(value.get("event_ticker", ""))
    return {
        "event_ticker": ticker,
        "climate_date": _event_day(ticker),
        "series_ticker": value.get("series_ticker"),
        "title": value.get("title"),
        "sub_title": value.get("sub_title"),
        "mutually_exclusive": value.get("mutually_exclusive"),
        "collateral_return_type": value.get("collateral_return_type"),
        "settlement_sources": value.get("settlement_sources", []),
        "last_updated_ts": value.get("last_updated_ts"),
    }


def _fetch_events() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cursor = ""
    events: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    while True:
        url = f"{BASE}/events?series_ticker=KXHIGHLAX&limit=200"
        if cursor:
            url += "&cursor=" + quote(cursor, safe="")
        value, headers = _api_json(url)
        rows = value.get("events")
        if not isinstance(rows, list):
            raise RuntimeError("events response is malformed")
        pages.append({"url": url, "count": len(rows), "status": headers["status"]})
        events.extend(_event_record(row) for row in rows if isinstance(row, dict))
        cursor = str(value.get("cursor") or "")
        if not cursor:
            break
    bounded = [row for row in events if row["climate_date"] and START <= row["climate_date"] <= END]
    bounded.sort(key=lambda row: (row["climate_date"], row["event_ticker"]))
    if len({row["event_ticker"] for row in bounded}) != len(bounded):
        raise RuntimeError("duplicate bounded event identity")
    return bounded, pages


def _fetch_event_fee_changes(event_ticker: str) -> dict[str, Any]:
    url = f"{BASE}/events/fee_changes?event_ticker={quote(event_ticker, safe='')}&limit=1000"
    value, headers = _api_json(url)
    rows = value.get("event_fee_change_arr")
    if rows is None:
        # The current API schema names this response event_fee_change_arr.
        rows = value.get("event_fee_changes")
    if not isinstance(rows, list):
        raise RuntimeError(f"event fee response malformed: {event_ticker}: {sorted(value)}")
    safe_rows = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError(f"event fee row malformed: {event_ticker}")
        safe_rows.append({key: row.get(key) for key in (
            "id", "event_ticker", "scheduled_ts", "fee_type", "fee_multiplier"
        )})
    return {"event_ticker": event_ticker, "url": url, "status": headers["status"],
            "fee_changes": safe_rows}


def acquire(output_root: Path, workers: int = 8) -> dict[str, Any]:
    retrieved = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    sources_dir = output_root / "sources"
    manifests_dir = output_root / "manifests"
    sources: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for filename, authority, url in (*DOCUMENTS, *DOC_PAGES):
        try:
            raw, headers = _get(url)
            if filename.endswith(".pdf") and not raw.startswith(b"%PDF"):
                raise RuntimeError("response is not a PDF")
            record = _write(sources_dir / filename, raw)
            sources.append({**record, "path": str(Path("sources") / filename),
                            "authority": authority, "url": url, **headers})
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})

    series_url = f"{BASE}/series/KXHIGHLAX"
    series, series_headers = _api_json(series_url)
    series_safe = {key: series.get("series", {}).get(key) for key in (
        "ticker", "fee_type", "fee_multiplier", "settlement_sources",
        "contract_url", "contract_terms_url", "last_updated_ts"
    )}
    record = _write(manifests_dir / "kxhiglax-series-current.json", _json_bytes({
        "retrieved_at_utc": retrieved, "url": series_url, "series": series_safe,
        "protected_confirmation_labels_read": False, "actual_orders_placed": False,
    }))
    sources.append({**record, "path": "manifests/kxhiglax-series-current.json",
                    "authority": "kalshi_public_api", "url": series_url, **series_headers})

    series_fee_url = f"{BASE}/series/fee_changes?series_ticker=KXHIGHLAX&show_historical=true"
    series_fees, series_fee_headers = _api_json(series_fee_url)
    record = _write(manifests_dir / "kxhiglax-series-fee-changes.json", _json_bytes({
        "retrieved_at_utc": retrieved, "url": series_fee_url,
        "series_fee_change_arr": series_fees.get("series_fee_change_arr", []),
        "protected_confirmation_labels_read": False, "actual_orders_placed": False,
    }))
    sources.append({**record, "path": "manifests/kxhiglax-series-fee-changes.json",
                    "authority": "kalshi_public_api", "url": series_fee_url,
                    **series_fee_headers})

    events, pages = _fetch_events()
    event_manifest = {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at_utc": retrieved,
        "series_ticker": "KXHIGHLAX",
        "date_start": START,
        "date_end": END,
        "request_policy": "event metadata only; with_nested_markets omitted",
        "page_requests": pages,
        "event_count": len(events),
        "events": events,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    event_manifest["self_sha256"] = canonical_hash(event_manifest)
    record = _write(manifests_dir / "kxhiglax-event-metadata.json", _json_bytes(event_manifest))
    sources.append({**record, "path": "manifests/kxhiglax-event-metadata.json",
                    "authority": "kalshi_public_api", "url": pages[0]["url"]})

    fee_rows: list[dict[str, Any]] = []
    fee_failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        pending = {executor.submit(_fetch_event_fee_changes, row["event_ticker"]): row["event_ticker"]
                   for row in events}
        for future in as_completed(pending):
            ticker = pending[future]
            try:
                fee_rows.append(future.result())
            except Exception as exc:
                fee_failures.append({"event_ticker": ticker, "error": str(exc)})
    fee_rows.sort(key=lambda row: row["event_ticker"])
    fee_failures.sort(key=lambda row: row["event_ticker"])
    event_fee_manifest = {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at_utc": retrieved,
        "series_ticker": "KXHIGHLAX",
        "date_start": START,
        "date_end": END,
        "event_count": len(events),
        "successful_event_request_count": len(fee_rows),
        "failed_event_request_count": len(fee_failures),
        "events_with_fee_changes": sum(bool(row["fee_changes"]) for row in fee_rows),
        "event_fee_changes": fee_rows,
        "failures": fee_failures,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    event_fee_manifest["self_sha256"] = canonical_hash(event_fee_manifest)
    record = _write(manifests_dir / "kxhiglax-event-fee-changes.json", _json_bytes(event_fee_manifest))
    sources.append({**record, "path": "manifests/kxhiglax-event-fee-changes.json",
                    "authority": "kalshi_public_api",
                    "url": f"{BASE}/events/fee_changes?event_ticker=<bounded-event>&limit=1000"})

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "retrieved_at_utc": retrieved,
        "scope": {"series_ticker": "KXHIGHLAX", "date_start": START, "date_end": END},
        "source_count": len(sources),
        "sources": sorted(sources, key=lambda row: row["path"]),
        "download_failures": failures,
        "event_fee_request_failures": fee_failures,
        "network_used_for_finite_acquisition": True,
        "historical_replay_network_allowed": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    manifest["self_sha256"] = canonical_hash(manifest)
    _write(manifests_dir / "source-manifest.json", _json_bytes(manifest))
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="v5/acquisition/economics")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    manifest = acquire(Path(args.output_root), args.workers)
    print(json.dumps({
        "source_count": manifest["source_count"],
        "download_failure_count": len(manifest["download_failures"]),
        "event_fee_request_failure_count": len(manifest["event_fee_request_failures"]),
        "self_sha256": manifest["self_sha256"],
    }, sort_keys=True))
    return 0 if not manifest["download_failures"] and not manifest["event_fee_request_failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
