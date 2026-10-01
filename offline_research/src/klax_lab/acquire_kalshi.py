"""Finite historical Kalshi acquisition. Never imported by offline experiments.

Only public GET requests for archived market metadata, archived candlesticks,
and archived public trades are implemented. Raw JSON responses are immutable;
the resumable manifest records the exact source bytes. Outcome values are
retained only in raw files, never in coverage summaries.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://external-api.kalshi.com/trade-api/v2/historical/markets"
TRADES_BASE = "https://external-api.kalshi.com/trade-api/v2/historical/trades"
SERIES = "KXHIGHLAX"
ALLOWED_SERIES = (SERIES, "HIGHLAX")
ALLOWED_CANDLE_INTERVALS = (1, 60)
MAX_CONTRACT_SPAN_SECONDS = 7 * 86400
DEFAULT_MAX_BYTES = 500_000_000
MONTHS = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def dump_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def event_date(market):
    """Date identifier only; does not inspect settlement labels."""
    match = re.fullmatch(r"(?:KXHIGHLAX|HIGHLAX)-(\d{2})([A-Z]{3})(\d{2})", market.get("event_ticker", ""))
    if not match or match[2] not in MONTHS:
        return None
    try:
        return date(2000 + int(match[1]), MONTHS[match[2]], int(match[3]))
    except ValueError:
        return None


def verified_station(market):
    """Minimal explicit identity screen; not historical rule-version proof."""
    rules = str(market.get("rules_primary", "")).lower()
    return "los angeles" in rules and ("airport" in rules or "klax" in rules) and (
        "climatological" in rules or "climate" in rules)


def safe_market(market):
    fields = ("ticker", "event_ticker", "title", "subtitle", "yes_sub_title", "no_sub_title",
              "market_type", "open_time", "close_time", "strike_type", "floor_strike",
              "cap_strike", "rules_primary", "rules_secondary", "status")
    result = {key: market.get(key) for key in fields}
    day = event_date(market)
    result["climate_date"] = day.isoformat() if day else None
    result["station_identity_screen"] = verified_station(market)
    return result


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Historical acquisition refuses HTTP redirects")


class HistoricalClient:
    def __init__(self, root, max_bytes=DEFAULT_MAX_BYTES, min_interval=0.5, retries=2,
                 opener=None, sleeper=time.sleep):
        if not 1 <= max_bytes <= DEFAULT_MAX_BYTES:
            raise ValueError("Transfer/storage cap must be in (0, 500 MB]")
        if not 0 <= retries <= 2 or min_interval < 0:
            raise ValueError("Invalid retry/throttle policy")
        self.root = Path(root).resolve()
        self.raw = self.root / "data/raw/kalshi"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "data/manifests/kalshi_downloads.json"
        self.max_bytes = max_bytes
        self.min_interval = min_interval
        self.retries = retries
        self.opener = opener or urllib.request.build_opener(NoRedirects()).open
        self.sleeper = sleeper
        self.last_request = 0.0
        self.transferred_this_run = 0
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            self.manifest = {"schema_version": 1, "created_at": utc_now(),
                             "scope": "historical KXHIGHLAX/HIGHLAX read-only", "sources": {}, "failures": []}

    def save(self):
        self.manifest["updated_at"] = utc_now()
        self.manifest["last_run_transfer_bytes"] = self.transferred_this_run
        self.manifest["cached_bytes"] = sum(s["bytes"] for s in self.manifest["sources"].values())
        dump_json(self.manifest_path, self.manifest)

    @staticmethod
    def validate_url(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "external-api.kalshi.com" or parsed.fragment:
            raise ValueError("Only the public historical Kalshi host is allowed")
        markets_path = "/trade-api/v2/historical/markets"
        trades_path = "/trade-api/v2/historical/trades"
        suffix = parsed.path.removeprefix(markets_path)
        is_metadata = parsed.path == markets_path
        is_candles = parsed.path.startswith(markets_path) and bool(
            re.fullmatch(r"/(?:KXHIGHLAX|HIGHLAX)-[A-Z0-9.\-]+/candlesticks", suffix))
        is_trades = parsed.path == trades_path
        if not (is_metadata or is_candles or is_trades):
            raise ValueError("Only historical metadata, candles, and public trades are allowed")
        query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        if is_metadata:
            allowed = {"series_ticker", "limit", "cursor"}
        elif is_candles:
            allowed = {"start_ts", "end_ts", "period_interval"}
        else:
            allowed = {"ticker", "min_ts", "max_ts", "limit", "cursor", "is_block_trade"}
        if set(query) - allowed or any(len(v) != 1 for v in query.values()):
            raise ValueError("Unexpected query parameter")
        if is_metadata and query.get("series_ticker", [None])[0] not in ALLOWED_SERIES:
            raise ValueError("Only the verified series is allowed")
        if is_candles:
            if set(query) != allowed or int(query["period_interval"][0]) not in ALLOWED_CANDLE_INTERVALS:
                raise ValueError("Only bounded one-minute or legacy hourly candles are allowed")
            start, end = int(query["start_ts"][0]), int(query["end_ts"][0])
            if start < 0 or end <= start or end >= int(time.time()):
                raise ValueError("Candle range must be finite and historical")
            if end - start > MAX_CONTRACT_SPAN_SECONDS:
                raise ValueError("Individual candle requests may span at most seven days")
        if is_trades:
            required = {"ticker", "min_ts", "max_ts", "limit"}
            if not required <= set(query):
                raise ValueError("Historical trade requests require ticker and bounded timestamps")
            ticker = query["ticker"][0]
            if not re.fullmatch(r"(?:KXHIGHLAX|HIGHLAX)-[A-Z0-9.\-]+", ticker):
                raise ValueError("Only trades for the verified series are allowed")
            start, end = int(query["min_ts"][0]), int(query["max_ts"][0])
            limit = int(query["limit"][0])
            if start < 0 or end <= start or end >= int(time.time()) or end - start > MAX_CONTRACT_SPAN_SECONDS:
                raise ValueError("Trade range must be finite, historical, and at most seven days")
            if not 1 <= limit <= 1000:
                raise ValueError("Historical trade page limit must be in [1, 1000]")
            if "is_block_trade" in query and query["is_block_trade"][0] not in ("true", "false"):
                raise ValueError("Invalid block-trade filter")

    @staticmethod
    def endpoint_type(url):
        path = urllib.parse.urlsplit(url).path
        if path == "/trade-api/v2/historical/markets":
            return "historical_markets_metadata"
        if path == "/trade-api/v2/historical/trades":
            return "historical_public_trades"
        if path.endswith("/candlesticks"):
            interval = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["period_interval"][0]
            return f"historical_market_candlesticks_{interval}m"
        raise ValueError("Unsupported historical endpoint")

    def get(self, url):
        self.validate_url(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        known = self.manifest["sources"].get(key)
        if known:
            path = self.root / known["path"]
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != known["sha256"]:
                raise ValueError("Immutable cache checksum mismatch: " + known["path"])
            return json.loads(body), known
        stored = sum(s["bytes"] for s in self.manifest["sources"].values())
        remaining = min(self.max_bytes - stored, self.max_bytes - self.transferred_this_run)
        if remaining <= 0:
            raise RuntimeError("Historical transfer/storage budget exhausted")
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                self.sleeper(wait)
            self.last_request = time.monotonic()
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "KLAX-offline-research/0.1", "Accept": "application/json"})
                with self.opener(request, timeout=45) as response:
                    body = response.read(remaining)
                    self.transferred_this_run += len(body)
                    if len(body) >= remaining:
                        raise RuntimeError("Historical transfer/storage budget exhausted before response completeness was established")
                    value = json.loads(body)
                    if not isinstance(value, dict):
                        raise ValueError("Expected JSON response object")
                    digest = hashlib.sha256(body).hexdigest()
                    path = self.raw / (key + ".json")
                    if path.exists():
                        if path.read_bytes() != body:
                            raise ValueError("Refusing to overwrite immutable historical response")
                    else:
                        temporary = path.with_suffix(".json.part")
                        with temporary.open("wb") as stream:
                            stream.write(body)
                        temporary.replace(path)
                    record = {"url": url, "endpoint_type": self.endpoint_type(url),
                              "retrieved_at": utc_now(), "sha256": digest,
                              "bytes": len(body), "path": path.relative_to(self.root).as_posix(),
                              "http_status": getattr(response, "status", 200), "status": "downloaded",
                              "source_information_available_at": None,
                              "note": "Retrieval time is not historical availability proof"}
                    self.manifest["sources"][key] = record
                    self.save()
                    return value, record
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                code = getattr(error, "code", None)
                self.manifest["failures"].append({"url": url, "at": utc_now(), "attempt": attempt,
                                                  "error_type": type(error).__name__, "http_status": code})
                self.save()
                if attempt == self.retries or (code is not None and code not in (429, 500, 502, 503, 504)):
                    raise
                self.sleeper(min(2 ** (attempt + 1), 8))
        raise AssertionError("Unreachable retry exit")


def acquire_metadata(client, start=date(2024, 1, 1), end=date(2025, 12, 31), max_pages=50, series=SERIES):
    if end < start or end >= date.today() or not 1 <= max_pages <= 100:
        raise ValueError("Invalid bounded metadata plan")
    if series not in ALLOWED_SERIES:
        raise ValueError("Unsupported series")
    cursor = ""
    seen_cursors = set()
    markets = {}
    sources = []
    for page in range(max_pages):
        params = {"series_ticker": series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload, source = client.get(BASE + "?" + urllib.parse.urlencode(params))
        rows = payload.get("markets")
        if not isinstance(rows, list):
            raise ValueError("Malformed markets page")
        sources.append(source["sha256"])
        for market in rows:
            ticker = market.get("ticker")
            if not isinstance(ticker, str) or not ticker.startswith(series + "-"):
                raise ValueError("Unexpected market series")
            clean = safe_market(market)
            if ticker in markets and markets[ticker] != clean:
                raise ValueError("Conflicting metadata for " + ticker)
            markets[ticker] = clean
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
        if cursor in seen_cursors:
            raise ValueError("Historical pagination cursor repeated")
        seen_cursors.add(cursor)
    else:
        raise RuntimeError("Metadata page budget exhausted; cached pages can be resumed")
    selected = [m for m in markets.values() if m["climate_date"] and start.isoformat() <= m["climate_date"] <= end.isoformat()]
    counts = Counter(m["climate_date"] for m in selected)
    expected = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    dates = sorted(counts)
    result = {"schema_version": 1, "created_at": utc_now(), "requested_start": str(start), "requested_end": str(end),
              "series": series, "pagination_complete": True, "pages": len(sources), "source_sha256": sources,
              "all_returned_contracts": len(markets), "selected_contracts": len(selected), "covered_days": len(dates),
              "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
              "missing_days": [str(d) for d in expected if str(d) not in counts],
              "contracts_per_day": dict(sorted(counts.items())),
              "station_screen_passes": sum(m["station_identity_screen"] for m in selected),
              "statuses": dict(Counter(m["status"] for m in selected)),
              "contracts": sorted(selected, key=lambda m: (m["climate_date"], m["ticker"])),
              "limitations": ["No historical rule-version proof", "No historical fee schedule acquired",
                              "No order-book depth acquired", "Coverage summaries omit outcomes and labels"]}
    filename = "kalshi_coverage.json" if series == SERIES else "kalshi_coverage_HIGHLAX.json"
    dump_json(client.root / "data/manifests" / filename, result)
    return result


def choose_pilot(coverage, days=7):
    """Choose earliest complete consecutive run, based only on date/identity coverage."""
    if not 1 <= days <= 7:
        raise ValueError("Pilot must be one to seven days")
    eligible = sorted({m["climate_date"] for m in coverage["contracts"]
                       if m["station_identity_screen"] and m["status"] in ("finalized", "settled")})
    dates = set(eligible)
    for value in eligible:
        start = date.fromisoformat(value)
        window = [str(start + timedelta(days=i)) for i in range(days)]
        if set(window) <= dates:
            return start, start + timedelta(days=days - 1)
    raise ValueError("No complete consecutive pilot interval")


def parse_ts(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Historical source timestamp must include a UTC offset")
    return int(stamp.timestamp())


def acquire_candles(client, coverage, start, end, period_minutes=1):
    if end < start or (end - start).days > 731 or end >= date.today():
        raise ValueError("Invalid historical candle dates")
    if period_minutes not in ALLOWED_CANDLE_INTERVALS:
        raise ValueError("Candlestick interval must be one minute or legacy hourly")
    rows = [m for m in coverage["contracts"] if str(start) <= m["climate_date"] <= str(end)
            and m["station_identity_screen"] and m["status"] in ("finalized", "settled")]
    if not rows:
        raise ValueError("No station-screened settled historical contracts in requested interval")
    summary = {"schema_version": 2, "created_at": utc_now(), "start_date": str(start), "end_date": str(end),
               "endpoint_type": f"historical_market_candlesticks_{period_minutes}m",
               "period_interval": period_minutes,
               "execution_grade": "B: aggregated bid/ask; historical depth and hypothetical fills unproven",
               "historical_depth_available": False,
               "contracts": [], "status": "in_progress"}
    output = client.root / (f"data/manifests/kalshi_candles_{period_minutes}m_" + str(start) + "_" + str(end) + ".json")
    for market in rows:
        opened, closed = parse_ts(market["open_time"]), parse_ts(market["close_time"])
        if closed <= opened or closed - opened > 7 * 86400:
            raise ValueError("Unexpected contract lifetime for " + market["ticker"])
        query = urllib.parse.urlencode({"start_ts": opened, "end_ts": closed, "period_interval": period_minutes})
        url = BASE + "/" + urllib.parse.quote(market["ticker"], safe="") + "/candlesticks?" + query
        try:
            payload, source = client.get(url)
            candles = payload.get("candlesticks")
            if not isinstance(candles, list) or payload.get("ticker") != market["ticker"]:
                raise ValueError("Malformed candle response")
            timestamps = [c["end_period_ts"] for c in candles]
            if any(not isinstance(ts, int) or ts < opened or ts > closed for ts in timestamps):
                raise ValueError("Candle timestamp outside requested range")
            if len(set(timestamps)) != len(timestamps):
                raise ValueError("Duplicate candle timestamp")
            summary["contracts"].append({"ticker": market["ticker"], "climate_date": market["climate_date"],
                                          "rows": len(candles), "source_sha256": source["sha256"],
                                          "path": source["path"], "status": "downloaded" if candles else "empty"})
        except urllib.error.HTTPError as error:
            if error.code not in (400, 404):
                raise
            summary["contracts"].append({"ticker": market["ticker"], "climate_date": market["climate_date"],
                                          "rows": 0, "status": "unavailable", "http_status": error.code})
        dump_json(output, summary)
    summary["status"] = "complete"
    summary["total_rows"] = sum(r["rows"] for r in summary["contracts"])
    summary["status_counts"] = dict(Counter(r["status"] for r in summary["contracts"]))
    dump_json(output, summary)
    return summary


def _validate_trade_page(payload, ticker, opened, closed, seen_ids):
    trades = payload.get("trades")
    if not isinstance(trades, list):
        raise ValueError("Malformed public-trades response")
    page_ids = set()
    for trade in trades:
        if not isinstance(trade, dict) or trade.get("ticker") != ticker:
            raise ValueError("Trade response contained an unexpected market")
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id:
            raise ValueError("Trade response lacks a stable trade ID")
        if trade_id in page_ids or trade_id in seen_ids:
            raise ValueError("Duplicate trade ID across paginated response")
        created = parse_ts(trade.get("created_time", ""))
        if created < opened or created > closed:
            raise ValueError("Trade timestamp outside requested contract range")
        page_ids.add(trade_id)
    seen_ids.update(page_ids)
    return trades


def acquire_trades(client, coverage, start, end, max_pages_per_contract=100, page_limit=1000):
    """Download finite archived public prints for station-screened contracts.

    A request is made per contract so unrelated markets can never enter the raw
    cache. Both block and non-block prints are retained; downstream code decides
    whether a print is admissible as evidence. No order-book or fill claim is
    inferred from a public trade.
    """
    if end < start or (end - start).days > 731 or end >= date.today():
        raise ValueError("Invalid historical trade dates")
    if not 1 <= max_pages_per_contract <= 1000 or not 1 <= page_limit <= 1000:
        raise ValueError("Invalid bounded trade pagination plan")
    rows = [m for m in coverage["contracts"] if str(start) <= m["climate_date"] <= str(end)
            and m["station_identity_screen"] and m["status"] in ("finalized", "settled")]
    if not rows:
        raise ValueError("No station-screened settled historical contracts in requested interval")
    summary = {"schema_version": 1, "created_at": utc_now(), "start_date": str(start), "end_date": str(end),
               "endpoint_type": "historical_public_trades", "page_limit": page_limit,
               "max_pages_per_contract": max_pages_per_contract,
               "historical_depth_available": False,
               "execution_grade": "C: public prints only; our hypothetical fill and queue position are unproven",
               "contracts": [], "status": "in_progress"}
    output = client.root / ("data/manifests/kalshi_trades_" + str(start) + "_" + str(end) + ".json")
    for market in rows:
        opened, closed = parse_ts(market["open_time"]), parse_ts(market["close_time"])
        if closed <= opened or closed - opened > MAX_CONTRACT_SPAN_SECONDS:
            raise ValueError("Unexpected contract lifetime for " + market["ticker"])
        cursor = ""
        seen_cursors = set()
        seen_ids = set()
        pages = []
        try:
            for _ in range(max_pages_per_contract):
                params = {"ticker": market["ticker"], "min_ts": opened, "max_ts": closed,
                          "limit": page_limit}
                if cursor:
                    params["cursor"] = cursor
                payload, source = client.get(TRADES_BASE + "?" + urllib.parse.urlencode(params))
                trades = _validate_trade_page(payload, market["ticker"], opened, closed, seen_ids)
                pages.append({"rows": len(trades), "source_sha256": source["sha256"],
                              "path": source["path"], "endpoint_type": source["endpoint_type"]})
                cursor = payload.get("cursor") or ""
                if not isinstance(cursor, str):
                    raise ValueError("Malformed public-trades pagination cursor")
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise ValueError("Historical trade pagination cursor repeated")
                seen_cursors.add(cursor)
            else:
                raise RuntimeError("Trade page budget exhausted; cached pages can be resumed")
            summary["contracts"].append({"ticker": market["ticker"], "climate_date": market["climate_date"],
                                          "rows": len(seen_ids), "pages": pages,
                                          "status": "downloaded" if seen_ids else "empty"})
        except urllib.error.HTTPError as error:
            if error.code not in (400, 404):
                raise
            summary["contracts"].append({"ticker": market["ticker"], "climate_date": market["climate_date"],
                                          "rows": 0, "pages": pages, "status": "unavailable",
                                          "http_status": error.code})
        dump_json(output, summary)
    summary["status"] = "complete"
    summary["total_rows"] = sum(r["rows"] for r in summary["contracts"])
    summary["total_pages"] = sum(len(r.get("pages", [])) for r in summary["contracts"])
    summary["status_counts"] = dict(Counter(r["status"] for r in summary["contracts"]))
    dump_json(output, summary)
    return summary


def verify_cache(root):
    """Offline byte-integrity and coverage audit; never parses raw outcomes."""
    root = Path(root).resolve()
    path = root / "data/manifests/kalshi_downloads.json"
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes)
    sources_by_path = {}
    endpoint_counts = Counter()
    for source in manifest["sources"].values():
        raw_path = (root / source["path"]).resolve()
        if not raw_path.is_relative_to(root / "data/raw/kalshi"):
            raise ValueError("Source path escapes the historical raw cache")
        body = raw_path.read_bytes()
        if len(body) != source["bytes"] or hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError("Source integrity failure: " + source["path"])
        derived_endpoint = HistoricalClient.endpoint_type(source["url"])
        if source.get("endpoint_type", derived_endpoint) != derived_endpoint:
            raise ValueError("Source endpoint type disagrees with its URL")
        endpoint_counts[derived_endpoint] += 1
        sources_by_path[source["path"]] = source
    contracts = {}
    batches = []
    for batch_path in sorted((root / "data/manifests").glob("kalshi_candles_*.json")):
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        interval = int(batch.get("period_interval", 60))
        batches.append({"path": batch_path.relative_to(root).as_posix(), "status": batch["status"],
                        "start_date": batch["start_date"], "end_date": batch["end_date"],
                        "endpoint_type": batch.get("endpoint_type", f"historical_market_candlesticks_{interval}m"),
                        "period_interval": interval, "contracts": len(batch["contracts"]),
                        "rows": sum(r["rows"] for r in batch["contracts"])})
        for entry in batch["contracts"]:
            if entry["status"] != "downloaded":
                continue
            source = sources_by_path.get(entry["path"])
            if source is None or source["sha256"] != entry["source_sha256"]:
                raise ValueError("Candle provenance is absent or inconsistent")
            key = (entry["ticker"], interval)
            previous = contracts.get(key)
            if previous and previous != entry:
                raise ValueError("Conflicting candle entries for " + entry["ticker"])
            contracts[key] = entry
    trade_contracts = {}
    trade_batches = []
    for batch_path in sorted((root / "data/manifests").glob("kalshi_trades_*.json")):
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        trade_batches.append({"path": batch_path.relative_to(root).as_posix(), "status": batch["status"],
                              "start_date": batch["start_date"], "end_date": batch["end_date"],
                              "endpoint_type": batch.get("endpoint_type"),
                              "contracts": len(batch["contracts"]),
                              "rows": sum(r["rows"] for r in batch["contracts"])})
        for entry in batch["contracts"]:
            if entry["status"] not in ("downloaded", "empty"):
                continue
            for page in entry.get("pages", []):
                source = sources_by_path.get(page["path"])
                if source is None or source["sha256"] != page["source_sha256"]:
                    raise ValueError("Public-trade provenance is absent or inconsistent")
                if source.get("endpoint_type", HistoricalClient.endpoint_type(source["url"])) != "historical_public_trades":
                    raise ValueError("Public-trade source has the wrong endpoint type")
            previous = trade_contracts.get(entry["ticker"])
            if previous and previous != entry:
                raise ValueError("Conflicting trade entries for " + entry["ticker"])
            trade_contracts[entry["ticker"]] = entry
    coverage_path = root / "data/manifests/kalshi_coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    expected = {m["ticker"] for m in coverage["contracts"]
                if m["station_identity_screen"] and m["status"] in ("settled", "finalized")}
    dates = sorted({row["climate_date"] for row in contracts.values()})
    minute_contracts = {ticker: row for (ticker, interval), row in contracts.items() if interval == 1}
    hourly_contracts = {ticker: row for (ticker, interval), row in contracts.items() if interval == 60}
    report = {"schema_version": 1, "audited_at": utc_now(), "network_used": False,
              "source_integrity": "passed", "raw_outcomes_inspected": False,
              "download_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "coverage_manifest_sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
              "source_files_verified": len(sources_by_path),
              "source_bytes_verified": sum(s["bytes"] for s in sources_by_path.values()),
              "source_endpoint_types": dict(sorted(endpoint_counts.items())),
              "unique_candle_contracts": len(contracts), "unique_candle_rows": sum(e["rows"] for e in contracts.values()),
              "one_minute_candle_contracts": len(minute_contracts),
              "one_minute_candle_rows": sum(e["rows"] for e in minute_contracts.values()),
              "hourly_candle_contracts": len(hourly_contracts),
              "hourly_candle_rows": sum(e["rows"] for e in hourly_contracts.values()),
              "public_trade_contracts": len(trade_contracts),
              "public_trade_rows": sum(e["rows"] for e in trade_contracts.values()),
              "covered_candle_days": len(dates), "first_date": dates[0] if dates else None,
              "last_date": dates[-1] if dates else None, "expected_contracts": len(expected),
              "missing_candle_tickers": sorted(expected - set(hourly_contracts) - set(minute_contracts)),
              "missing_one_minute_candle_tickers": sorted(expected - set(minute_contracts)),
              "missing_public_trade_tickers": sorted(expected - set(trade_contracts)),
              "batches": batches, "trade_batches": trade_batches,
              "limitations": ["Byte integrity and coverage are not execution-quality validation",
                              "No historical fee schedule or order-book depth",
                              "No simulated returns computed by this audit"]}
    dump_json(root / "data/manifests/kalshi_integrity.json", report)
    return report


def build_v3_development_market_manifest(root):
    """Verify only the registered V3 development minute bars and public trades.

    The older cache can contain protected-final artifacts from prior work.  This
    verifier filters by climate date before resolving or hashing a raw source,
    so development evidence never depends on protected-final bytes.
    """
    root = Path(root).resolve()
    start, end = date(2025, 1, 5), date(2025, 6, 30)
    coverage_path = root / "data/manifests/kalshi_coverage.json"
    downloads_path = root / "data/manifests/kalshi_downloads.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    downloads_bytes = downloads_path.read_bytes()
    downloads = json.loads(downloads_bytes)
    sources_by_path = {source["path"]: source for source in downloads["sources"].values()}

    expected = {
        row["ticker"]: row["climate_date"]
        for row in coverage["contracts"]
        if row.get("station_identity_screen") is True
        and row.get("status") in ("settled", "finalized")
        and start <= date.fromisoformat(row["climate_date"]) <= end
    }
    if not expected:
        raise ValueError("No eligible V3 development contracts are registered")

    verified_paths = set()

    def verify_source(path_text, digest, endpoint):
        source = sources_by_path.get(path_text)
        if source is None or source.get("sha256") != digest:
            raise ValueError("Development market provenance is absent or inconsistent")
        if HistoricalClient.endpoint_type(source["url"]) != endpoint:
            raise ValueError("Development market source has the wrong endpoint type")
        raw_path = (root / path_text).resolve()
        if not raw_path.is_relative_to(root / "data/raw/kalshi"):
            raise ValueError("Development market source escapes the historical cache")
        body = raw_path.read_bytes()
        if len(body) != source["bytes"] or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("Development market source integrity failure")
        verified_paths.add(path_text)

    candles, candle_batches = {}, []
    for batch_path in sorted((root / "data/manifests").glob("kalshi_candles_1m_*.json")):
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        if batch.get("period_interval") != 1:
            continue
        used = False
        for entry in batch.get("contracts", []):
            climate_day = date.fromisoformat(entry["climate_date"])
            if not start <= climate_day <= end:
                continue
            used = True
            if entry.get("status") != "downloaded":
                raise ValueError("V3 development one-minute candle is unavailable")
            verify_source(entry["path"], entry["source_sha256"], "historical_market_candlesticks_1m")
            previous = candles.get(entry["ticker"])
            if previous is not None and previous != entry:
                raise ValueError("Conflicting V3 one-minute candle entries")
            candles[entry["ticker"]] = entry
        if used:
            candle_batches.append({
                "path": batch_path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
            })

    trades, trade_batches = {}, []
    for batch_path in sorted((root / "data/manifests").glob("kalshi_trades_*.json")):
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        used = False
        for entry in batch.get("contracts", []):
            climate_day = date.fromisoformat(entry["climate_date"])
            if not start <= climate_day <= end:
                continue
            used = True
            if entry.get("status") not in ("downloaded", "empty"):
                raise ValueError("V3 development public trades are unavailable")
            for page in entry.get("pages", []):
                verify_source(page["path"], page["source_sha256"], "historical_public_trades")
            previous = trades.get(entry["ticker"])
            if previous is not None and previous != entry:
                raise ValueError("Conflicting V3 public-trade entries")
            trades[entry["ticker"]] = entry
        if used:
            trade_batches.append({
                "path": batch_path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
            })

    missing_candles = sorted(set(expected) - set(candles))
    missing_trades = sorted(set(expected) - set(trades))
    if missing_candles or missing_trades:
        raise ValueError(
            f"Incomplete V3 development market data: {len(missing_candles)} candles, "
            f"{len(missing_trades)} trades missing"
        )
    report = {
        "schema_version": 1,
        "component": "minute_kalshi",
        "status": "DEVELOPMENT_COMPLETE",
        "audited_at": utc_now(),
        "network_used": False,
        "protected_final_read": False,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "eligible_contracts": len(expected),
        "one_minute_candle_contracts": len(candles),
        "one_minute_candle_rows": sum(row["rows"] for row in candles.values()),
        "public_trade_contracts": len(trades),
        "public_trade_rows": sum(row["rows"] for row in trades.values()),
        "verified_raw_source_files": len(verified_paths),
        "coverage_manifest_sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
        "download_manifest_sha256": hashlib.sha256(downloads_bytes).hexdigest(),
        "candle_batches": candle_batches,
        "trade_batches": trade_batches,
        "historical_depth_available": False,
        "execution_grade": "B for aggregated one-minute candles; C for public prints",
        "limitations": [
            "Historical order-book depth and queue position are unavailable",
            "Public trades do not prove a hypothetical fill",
            "A replay must use conservative bar-based fill rules and cost stress",
        ],
    }
    dump_json(root / "data/manifests/v3_minute_kalshi.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["metadata", "pilot", "candles", "monthly", "trades", "verify"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--series", choices=ALLOWED_SERIES, default=SERIES)
    parser.add_argument("--period-minutes", type=int, choices=ALLOWED_CANDLE_INTERVALS, default=1)
    parser.add_argument("--trade-page-limit", type=int, default=1000)
    parser.add_argument("--max-trade-pages-per-contract", type=int, default=100)
    args = parser.parse_args(argv)
    if args.command == "verify":
        result = verify_cache(args.root)
        print(json.dumps({k: v for k, v in result.items() if k not in ("batches", "missing_candle_tickers")}, indent=2))
        return
    client = HistoricalClient(args.root, max_bytes=args.max_bytes)
    if args.command == "metadata":
        result = acquire_metadata(client, args.start, args.end, series=args.series)
        print(json.dumps({k: v for k, v in result.items() if k not in ("contracts", "contracts_per_day", "missing_days", "source_sha256")}, indent=2))
    else:
        filename = "kalshi_coverage.json" if args.series == SERIES else "kalshi_coverage_HIGHLAX.json"
        coverage = json.loads((client.root / "data/manifests" / filename).read_text(encoding="utf-8"))
        start, end = choose_pilot(coverage) if args.command == "pilot" else (args.start, args.end)
        if args.command == "trades":
            result = acquire_trades(client, coverage, start, end,
                                    max_pages_per_contract=args.max_trade_pages_per_contract,
                                    page_limit=args.trade_page_limit)
            print(json.dumps({k: v for k, v in result.items() if k != "contracts"}, indent=2))
        elif args.command == "monthly":
            if end < start or (end - start).days > 731:
                raise ValueError("Invalid monthly acquisition interval")
            cursor = start
            while cursor <= end:
                following_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
                month_end = min(following_month - timedelta(days=1), end)
                result = acquire_candles(client, coverage, cursor, month_end, args.period_minutes)
                print(json.dumps({k: v for k, v in result.items() if k != "contracts"}), flush=True)
                cursor = following_month
        else:
            result = acquire_candles(client, coverage, start, end, args.period_minutes)
            print(json.dumps({k: v for k, v in result.items() if k != "contracts"}, indent=2))


if __name__ == "__main__":
    main()
