"""Acquire the remaining free/public V5P fee and rule evidence.

The program never requests nested markets, market results, account data,
orders, fills, or live feeds.  It is a finite evidence acquisition; all later
research must use the saved hashes offline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from http.cookiejar import CookieJar
import html
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


SCHEMA = "klax-v5p-public-economics-acquisition-v1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) V5P-public-research/1.0"
ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "sources"
MANIFESTS = ROOT / "manifests"

GET_SOURCES = (
    ("kalshi-fee-schedule-effective-2026-07-07.pdf", "kalshi_official",
     "https://kalshi.com/docs/kalshi-fee-schedule.pdf"),
    ("kalshi-fee-rounding-current.html", "kalshi_api_docs",
     "https://docs.kalshi.com/getting_started/fee_rounding"),
    ("kalshi-regulatory-fee-schedule-current.html", "kalshi_official",
     "https://kalshi.com/regulatory/fee-schedule"),
    ("kalshi-fee-schedule-page-current.html", "kalshi_official",
     "https://kalshi.com/fee-schedule"),
    ("kalshi-globaltemperature-certification.pdf", "kalshi_product_certification",
     "https://assets.kalshi.com/regulatory/product-certifications/GLOBALTEMPERATURE.pdf"),
    ("cftc-laxhigh-certification.pdf", "cftc_product_certification",
     "https://www.cftc.gov/sites/default/files/filings/ptc/24/12/ptc12042410310.pdf"),
    ("kxhiglax-series-current.json", "kalshi_public_api",
     "https://external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX"),
    ("kxhiglax-series-fee-history.json", "kalshi_public_api",
     "https://external-api.kalshi.com/trade-api/v2/series/fee_changes?series_ticker=KXHIGHLAX&show_historical=true"),
)

CDX_QUERIES = (
    "kalshi.com/docs/kalshi-fee-schedule.pdf",
    "kalshi.com/fee-schedule",
    "kalshi.com/regulatory/fee-schedule",
    "external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX",
    "trading-api.kalshi.com/trade-api/v2/series/KXHIGHLAX",
)


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def get(url: str, attempts: int = 4, timeout: int = 120) -> tuple[bytes, dict[str, str]]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/pdf,application/json,text/html,*/*",
                "Referer": "https://kalshi.com/regulatory/fee-schedule",
            })
            with urlopen(request, timeout=timeout) as response:
                return response.read(), {
                    "status": str(response.status),
                    "content_type": response.headers.get("Content-Type", ""),
                    "final_url": response.geturl(),
                }
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def save(relative: str, payload: bytes) -> dict[str, Any]:
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_bytes()
        if existing != payload:
            return {"path": relative, "bytes": len(existing),
                    "sha256": sha256(existing).hexdigest(),
                    "preserved_existing_capture": True}
    path.write_bytes(payload)
    return {"path": relative, "bytes": len(payload),
            "sha256": sha256(payload).hexdigest()}


def cftc_rule_search() -> tuple[bytes, list[str]]:
    """Save the authoritative KEX rule-filing search for Dec 2025/Jan 2026."""
    url = "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules"
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    request = Request(url, headers={"User-Agent": USER_AGENT})
    first = opener.open(request, timeout=120).read().decode("utf-8", "replace")
    build = re.search(r'name="form_build_id" value="([^"]+)"', first)
    form = re.search(r'name="form_id" value="([^"]+)"', first)
    if not build or not form:
        raise RuntimeError("CFTC filing search form identity unavailable")
    fields = {
        "Organization": "KEX",
        "Receipt_Date_From": "2025-12-01",
        "Receipt_Date_To": "2026-01-31",
        "Status": "",
        "Date_From": "",
        "Date_To": "",
        "Show_All": "1",
        "op": "Search",
        "form_build_id": html.unescape(build.group(1)),
        "form_id": html.unescape(form.group(1)),
    }
    post = Request(url, data=urlencode(fields).encode(), headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    })
    payload = opener.open(post, timeout=120).read()
    text = payload.decode("utf-8", "replace")
    links = []
    for href in re.findall(r'href="([^"]+)"', text):
        absolute = urljoin(url, html.unescape(href))
        if ("filings/orgrules" in absolute or
                "IndustryFilings/TradingOrganizationRules/" in absolute):
            links.append(absolute)
    return payload, sorted(set(links))


def acquire() -> dict[str, Any]:
    retrieved = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for filename, authority, url in GET_SOURCES:
        try:
            payload, headers = get(url)
            if filename.endswith(".pdf") and not payload.startswith(b"%PDF"):
                raise RuntimeError("response is not PDF")
            record = save(f"sources/{filename}", payload)
            records.append({**record, "authority": authority, "url": url, **headers})
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})

    cdx_results = []
    for target in CDX_QUERIES:
        url = ("https://web.archive.org/cdx/search/cdx?url=" + target +
               "&output=json&filter=statuscode:200&from=2025&to=2026")
        try:
            payload, headers = get(url, attempts=1, timeout=30)
            filename = "cdx-" + re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") + ".json"
            record = save(f"sources/{filename}", payload)
            records.append({**record, "authority": "internet_archive_index",
                            "url": url, **headers})
            cdx_results.append({"target": target, "path": record["path"]})
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})

    cftc_links: list[str] = []
    try:
        payload, cftc_links = cftc_rule_search()
        record = save("sources/cftc-kex-rule-filings-2025-12_to_2026-01.html", payload)
        records.append({**record, "authority": "cftc_filing_index",
                        "url": "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules"})
    except Exception as exc:
        failures.append({"url": "CFTC KEX rule filing search", "error": str(exc)})

    # Only public CFTC rule PDFs discovered by the bounded official search are downloaded.
    for index, url in enumerate(cftc_links):
        if not url.lower().endswith(".pdf"):
            continue
        try:
            payload, headers = get(url)
            if not payload.startswith(b"%PDF"):
                continue
            name = Path(url.split("?", 1)[0]).name or f"cftc-rule-{index}.pdf"
            record = save(f"sources/cftc-rule-{index:02d}-{name}", payload)
            records.append({**record, "authority": "cftc_filing", "url": url, **headers})
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})

    manifest = {
        "schema_version": SCHEMA,
        "retrieved_at_utc": retrieved,
        "campaign": "V5P_PUBLIC_EVIDENCE",
        "scope": ["July 2026 fee/rounding", "historical maker applicability",
                  "KXHIGHLAX contract-rule transition"],
        "sources": sorted(records, key=lambda row: row["path"]),
        "source_count": len(records),
        "failures": failures,
        "cdx_results": cdx_results,
        "cftc_discovered_links": cftc_links,
        "network_used_for_bounded_acquisition": True,
        "historical_replay_network_allowed": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "private_account_data_read": False,
    }
    manifest["self_sha256"] = canonical_hash(manifest)
    save("manifests/source-manifest.json",
         (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return manifest


if __name__ == "__main__":
    result = acquire()
    print(json.dumps({
        "source_count": result["source_count"],
        "failure_count": len(result["failures"]),
        "self_sha256": result["self_sha256"],
    }, sort_keys=True))
