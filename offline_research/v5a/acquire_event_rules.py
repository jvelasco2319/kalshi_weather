"""Finite official acquisition of outcome-blind KXHIGHLAX market rules.

Raw API responses are quarantined because settled market objects include
outcome fields.  The safe manifest copies only event identity, settlement
source, contract boundaries, timestamps, and rule text.  It never propagates a
result, expiration value, settlement value, or price.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Mapping

import requests
import pandas as pd


START = date(2026, 6, 1)
END = date(2026, 8, 31)
BASE = "https://api.elections.kalshi.com/trade-api/v2"
RAW_ROOT = Path("data/raw/v5a/rule_transition/quarantine")
SAFE_PATH = Path("data/manifests/v5a_event_rules_outcome_blind.json")
DISCOVERY_PATH = Path("data/raw/v5a/probalytics_market_tickers_safe.json")
SCHEMA = "klax-v5a-event-rules-outcome-blind-v1"

FORBIDDEN = {
    "result", "expiration_value", "settlement_value", "settlement_value_dollars",
    "yes_settlement_value_dollars", "last_price", "last_price_dollars",
    "yes_bid", "yes_ask", "no_bid", "no_ask", "outcome", "outcomes",
}
SAFE_MARKET_FIELDS = (
    "ticker", "event_ticker", "market_type", "yes_sub_title", "no_sub_title",
    "created_time", "updated_time", "open_time", "close_time",
    "latest_expiration_time", "settlement_timer_seconds", "status",
    "rules_primary", "rules_secondary", "title", "subtitle", "strike_type",
    "floor_strike", "cap_strike", "functional_strike", "is_provisional",
)


class EventRuleAcquisitionError(ValueError):
    pass


def _days() -> list[date]:
    return [START + timedelta(days=index) for index in range((END - START).days + 1)]


def _ticker(day: date) -> str:
    return "KXHIGHLAX-" + day.strftime("%y%b%d").upper()


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    payload = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _assert_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in FORBIDDEN:
                raise EventRuleAcquisitionError(f"forbidden field propagated: {key}")
            _assert_safe(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe(item)


def _safe_market(market: Mapping[str, Any], expected_ticker: str) -> dict[str, Any]:
    safe = {field: market.get(field) for field in SAFE_MARKET_FIELDS}
    if safe["event_ticker"] != expected_ticker or not str(safe["ticker"]).startswith(expected_ticker + "-"):
        raise EventRuleAcquisitionError("market identity mismatch")
    if safe["market_type"] != "binary" or not str(safe["rules_primary"] or "").strip():
        raise EventRuleAcquisitionError("market rule identity incomplete")
    _assert_safe(safe)
    return safe


def _safe_event(
    payload: Mapping[str, Any], expected_ticker: str,
    supplemental_markets: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    event = payload.get("event")
    if not isinstance(event, Mapping):
        raise EventRuleAcquisitionError("official response lacks event object")
    if event.get("event_ticker") != expected_ticker or event.get("series_ticker") != "KXHIGHLAX":
        raise EventRuleAcquisitionError("event identity mismatch")
    sources = event.get("settlement_sources")
    if not isinstance(sources, list) or not sources:
        raise EventRuleAcquisitionError("event lacks settlement source")
    safe_sources = []
    for source in sources:
        if not isinstance(source, Mapping) or not source.get("name") or not source.get("url"):
            raise EventRuleAcquisitionError("malformed settlement source")
        safe_sources.append({"name": str(source["name"]), "url": str(source["url"])})
    markets = payload.get("markets", event.get("markets"))
    if not isinstance(markets, list):
        raise EventRuleAcquisitionError("nested markets malformed")
    if not markets and supplemental_markets is not None:
        markets = supplemental_markets
    safe_markets = []
    for market in markets:
        if not isinstance(market, Mapping):
            raise EventRuleAcquisitionError("market is not an object")
        safe_markets.append(_safe_market(market, expected_ticker))
    safe_markets.sort(key=lambda row: str(row["ticker"]))
    result = {
        "event_ticker": expected_ticker,
        "series_ticker": "KXHIGHLAX",
        "title": event.get("title"),
        "sub_title": event.get("sub_title"),
        "collateral_return_type": event.get("collateral_return_type"),
        "mutually_exclusive": event.get("mutually_exclusive"),
        "settlement_sources": safe_sources,
        "category": event.get("category"),
        "strike_date": event.get("strike_date"),
        "strike_period": event.get("strike_period"),
        "last_updated_ts": event.get("last_updated_ts"),
        "fee_type_override": event.get("fee_type_override"),
        "fee_multiplier_override": event.get("fee_multiplier_override"),
        "markets": safe_markets,
    }
    _assert_safe(result)
    return result


def _get(session: requests.Session, url: str) -> tuple[requests.Response, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        retrieved = datetime.now(UTC).isoformat()
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            return response, retrieved
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise EventRuleAcquisitionError(f"official request failed: {type(last_error).__name__}")


def _known_market_tickers(
    root: Path,
) -> tuple[dict[str, set[str]], dict[str, list[Mapping[str, Any]]]]:
    by_day: dict[str, set[str]] = {day.isoformat(): set() for day in _days()}
    safe_by_day: dict[str, list[Mapping[str, Any]]] = {
        day.isoformat(): [] for day in _days()
    }
    coverage_path = root / "data/raw/v5p/kalshi_workspace/data/manifests/kalshi_coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    for row in coverage.get("contracts", []):
        day = str(row.get("climate_date"))
        ticker = row.get("ticker")
        if day in by_day and isinstance(ticker, str):
            by_day[day].add(ticker)
            safe_by_day[day].append(row)
    top_path = root / "data/normalized/v5p_probalytics_full_history_20260601_20260831/target_top_of_book.parquet"
    frame = pd.read_parquet(top_path, columns=["climate_date", "market_platform_id"])
    for day, ticker in frame.drop_duplicates().itertuples(index=False, name=None):
        if str(day) in by_day:
            by_day[str(day)].add(str(ticker))
    discovery_path = root / DISCOVERY_PATH
    if discovery_path.is_file():
        discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
        if discovery.get("outcomes_queried") is not False:
            raise EventRuleAcquisitionError("safe ticker discovery is not outcome-blind")
        for row in discovery.get("events", []):
            day = str(row.get("climate_date"))
            if day not in by_day:
                raise EventRuleAcquisitionError("safe ticker discovery date is out of range")
            for ticker in row.get("market_tickers", []):
                if not isinstance(ticker, str) or not ticker.startswith(_ticker(date.fromisoformat(day)) + "-"):
                    raise EventRuleAcquisitionError("safe ticker discovery identity mismatch")
                by_day[day].add(ticker)
    return by_day, safe_by_day


def _market_partition_exact(markets: list[Mapping[str, Any]]) -> bool:
    intervals: list[tuple[int | None, int | None]] = []
    for market in markets:
        strike = market.get("strike_type")
        floor = market.get("floor_strike")
        cap = market.get("cap_strike")
        if strike == "less" and cap is not None:
            intervals.append((None, int(cap) - 1))
        elif strike == "between" and floor is not None and cap is not None:
            intervals.append((int(floor), int(cap)))
        elif strike == "greater" and floor is not None:
            intervals.append((int(floor) + 1, None))
        else:
            return False
    if len(intervals) < 2:
        return False
    ordered = sorted(intervals, key=lambda row: -100000 if row[0] is None else row[0])
    if ordered[0][0] is not None or ordered[-1][1] is not None:
        return False
    return all(
        left[1] is not None and right[0] is not None and left[1] + 1 == right[0]
        for left, right in zip(ordered, ordered[1:])
    )


def acquire(project_root: Path | str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    raw_root = root / RAW_ROOT
    raw_root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "klax-v5a-historical-rules/1.0"})
    tickers_by_day, safe_markets_by_day = _known_market_tickers(root)
    events, raw_records = [], []
    for day in _days():
        ticker = _ticker(day)
        url = f"{BASE}/events/{ticker}?with_nested_markets=true"
        raw_path = raw_root / f"{ticker}.json"
        if raw_path.is_file():
            payload_bytes = raw_path.read_bytes()
            retrieved = datetime.fromtimestamp(raw_path.stat().st_mtime, UTC).isoformat()
            response_status = 200
            etag = None
            last_modified = None
        else:
            response, retrieved = _get(session, url)
            payload_bytes = response.content
            pending = raw_path.with_suffix(".json.pending")
            pending.write_bytes(payload_bytes)
            pending.replace(raw_path)
            response_status = response.status_code
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
        payload = json.loads(payload_bytes)

        nested = payload.get("markets")
        supplemental: list[Mapping[str, Any]] = list(safe_markets_by_day[day.isoformat()])
        if not isinstance(nested, list) or not nested:
            known_safe_tickers = {
                str(market.get("ticker")) for market in supplemental
            }
            for market_ticker in sorted(
                tickers_by_day[day.isoformat()] - known_safe_tickers
            ):
                market_url = f"{BASE}/markets/{market_ticker}"
                market_path = raw_root / f"market-{market_ticker}.json"
                if market_path.is_file():
                    market_bytes = market_path.read_bytes()
                    market_retrieved = datetime.fromtimestamp(
                        market_path.stat().st_mtime, UTC
                    ).isoformat()
                    market_status, market_etag, market_modified = 200, None, None
                else:
                    market_response, market_retrieved = _get(session, market_url)
                    market_bytes = market_response.content
                    pending = market_path.with_suffix(".json.pending")
                    pending.write_bytes(market_bytes)
                    pending.replace(market_path)
                    market_status = market_response.status_code
                    market_etag = market_response.headers.get("ETag")
                    market_modified = market_response.headers.get("Last-Modified")
                market_payload = json.loads(market_bytes)
                market = market_payload.get("market")
                if not isinstance(market, Mapping):
                    raise EventRuleAcquisitionError("individual market response malformed")
                supplemental.append(market)
                raw_records.append({
                    "climate_date": day.isoformat(), "url": market_url,
                    "path": market_path.relative_to(root).as_posix(),
                    "retrieved_at_utc": market_retrieved, "http_status": market_status,
                    "etag": market_etag, "last_modified": market_modified,
                    "bytes": market_path.stat().st_size, "sha256": _file_hash(market_path),
                    "quarantined_outcome_fields_not_propagated": True,
                })
        safe = _safe_event(payload, ticker, supplemental)
        safe["climate_date"] = day.isoformat()
        safe["raw_source_sha256"] = _file_hash(raw_path)
        safe["contract_partition_exact"] = _market_partition_exact(safe["markets"])
        events.append(safe)
        raw_records.append({
            "climate_date": day.isoformat(),
            "url": url,
            "path": raw_path.relative_to(root).as_posix(),
            "retrieved_at_utc": retrieved,
            "http_status": response_status,
            "etag": etag,
            "last_modified": last_modified,
            "bytes": raw_path.stat().st_size,
            "sha256": _file_hash(raw_path),
            "quarantined_outcome_fields_not_propagated": True,
        })

    transition_rows = []
    previous = None
    for event in events:
        identity = tuple((row["name"], row["url"]) for row in event["settlement_sources"])
        if identity != previous:
            transition_rows.append({
                "first_climate_date": event["climate_date"],
                "settlement_sources": event["settlement_sources"],
            })
            previous = identity
    result = {
        "schema_version": SCHEMA,
        "series_ticker": "KXHIGHLAX",
        "date_start": START.isoformat(),
        "date_end": END.isoformat(),
        "calendar_days": len(events),
        "event_count": len(events),
        "market_count": sum(len(event["markets"]) for event in events),
        "event_rules_bound_count": sum(
            bool(event["markets"]) and event["contract_partition_exact"] for event in events
        ),
        "events": events,
        "settlement_source_transitions": transition_rows,
        "raw_sources": raw_records,
        "raw_responses_quarantined": True,
        "outcomes_propagated": False,
        "protected_confirmation_labels_read_by_planner": False,
        "network_used_for_finite_historical_acquisition": True,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    _assert_safe(result)
    result["self_sha256"] = _canonical_hash(result)
    safe_path = root / SAFE_PATH
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    pending = safe_path.with_suffix(".json.pending")
    pending.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(safe_path)
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = acquire(args.project_root)
    print(json.dumps({
        "safe_manifest": SAFE_PATH.as_posix(),
        "self_sha256": value["self_sha256"],
        "calendar_days": value["calendar_days"],
        "market_count": value["market_count"],
        "settlement_source_transitions": value["settlement_source_transitions"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
