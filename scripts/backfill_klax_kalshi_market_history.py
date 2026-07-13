"""Backfill historical Kalshi market data for KLAX high-temperature markets.

This pulls the market side of the research dataset, analogous to the weather
model as-of history. For each KLAX market date it discovers the Kalshi event,
uses Kalshi's market open/close timestamps, and writes one-minute candlesticks
plus trades for each bracket ticker.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kalshi_weather.data.market_discovery import (  # noqa: E402
    bracket_text_from_market,
    market_date_from_market,
    parse_bracket_label,
)

DEFAULT_SERIES = "KXHIGHLAX"
DEFAULT_API_BASES = [
    "https://external-api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
]
DEFAULT_RAW_DIR = Path("data/raw/klax_temperature_history/kalshi_market_history")
DEFAULT_OUT_DIR = Path("data/processed/klax_temperature_history/kalshi_market_history")
PT = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc
MONTH_CODES = {
    1: "JAN",
    2: "FEB",
    3: "MAR",
    4: "APR",
    5: "MAY",
    6: "JUN",
    7: "JUL",
    8: "AUG",
    9: "SEP",
    10: "OCT",
    11: "NOV",
    12: "DEC",
}


@dataclass(frozen=True)
class DiscoveryResult:
    api_base: str
    event_ticker: str
    markets: list[dict[str, Any]]
    source: str
    raw: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="KLAX market date, YYYY-MM-DD.")
    parser.add_argument("--series", default=DEFAULT_SERIES)
    parser.add_argument(
        "--api-base",
        action="append",
        dest="api_bases",
        help="Kalshi API base. Can be passed more than once.",
    )
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--period-interval", type=int, default=1, help="Candlestick interval in minutes.")
    parser.add_argument("--skip-trades", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Ignore raw JSON cache and re-download.")
    parser.add_argument("--sleep-seconds", type=float, default=0.1, help="Pause between API calls.")
    parser.add_argument("--limit", type=int, default=1000, help="Pagination page size for markets/trades.")
    return parser.parse_args()


def parse_date_arg(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def market_event_tickers(series: str, market_date: date) -> list[str]:
    yy = market_date.strftime("%y")
    month = MONTH_CODES[market_date.month]
    day_no_zero = str(market_date.day)
    day_zero = f"{market_date.day:02d}"
    tickers = [f"{series}-{yy}{month}{day_no_zero}"]
    zero_ticker = f"{series}-{yy}{month}{day_zero}"
    if zero_ticker not in tickers:
        tickers.append(zero_ticker)
    return tickers


def safe_file_stem(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iso_pt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(PT).isoformat()


def timestamp_seconds(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp())


def get_cached_or_fetch(
    session: requests.Session,
    api_base: str,
    path: str,
    params: dict[str, Any] | None,
    cache_path: Path,
    refresh: bool,
) -> dict[str, Any]:
    if cache_path.exists() and not refresh:
        with cache_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    url = f"{api_base.rstrip('/')}/{path.lstrip('/')}"
    response = session.get(url, params=params, timeout=45)
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"{response.status_code} {response.reason} for {response.url}: {response.text[:500]}",
            response=response,
        )
    data = response.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "url": response.url,
                "status_code": response.status_code,
                "downloaded_utc": datetime.now(UTC).isoformat(),
                "payload": data,
            },
            f,
            indent=2,
            sort_keys=True,
        )
    return {"url": response.url, "status_code": response.status_code, "payload": data}


def payload(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("payload", data)


def paged_get(
    session: requests.Session,
    api_base: str,
    path: str,
    params: dict[str, Any],
    list_key: str,
    cache_path: Path,
    refresh: bool,
    sleep_seconds: float,
) -> dict[str, Any]:
    if cache_path.exists() and not refresh:
        with cache_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    items: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        url = f"{api_base.rstrip('/')}/{path.lstrip('/')}"
        response = session.get(url, params=page_params, timeout=45)
        if response.status_code >= 400:
            raise requests.HTTPError(
                f"{response.status_code} {response.reason} for {response.url}: {response.text[:500]}",
                response=response,
            )
        data = response.json()
        pages.append({"url": response.url, "payload": data})
        items.extend(list(data.get(list_key, [])))
        cursor = data.get("cursor") or data.get("next_cursor")
        if not cursor:
            break
        time.sleep(sleep_seconds)

    combined = {
        "downloaded_utc": datetime.now(UTC).isoformat(),
        "api_base": api_base,
        "path": path,
        "params": params,
        list_key: items,
        "pages": pages,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(combined, f, indent=2, sort_keys=True)
    return combined


def markets_from_event_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    body = payload(data)
    if isinstance(body.get("markets"), list):
        return list(body["markets"])
    event = body.get("event")
    if isinstance(event, dict) and isinstance(event.get("markets"), list):
        return list(event["markets"])
    return []


def discover_markets(
    session: requests.Session,
    api_bases: list[str],
    series: str,
    market_date: date,
    raw_day_dir: Path,
    refresh: bool,
    limit: int,
    sleep_seconds: float,
) -> DiscoveryResult:
    errors: list[str] = []
    statuses = [None, "open", "closed", "settled"]
    for api_base in api_bases:
        for event_ticker in market_event_tickers(series, market_date):
            event_cache = raw_day_dir / safe_file_stem(api_base) / f"event_{event_ticker}.json"
            try:
                event_data = get_cached_or_fetch(
                    session,
                    api_base,
                    f"events/{event_ticker}",
                    {"with_nested_markets": "true"},
                    event_cache,
                    refresh,
                )
                markets = markets_from_event_payload(event_data)
                markets = [m for m in markets if market_date_from_market(m) == market_date]
                if markets:
                    return DiscoveryResult(api_base, event_ticker, markets, "event", event_data)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{api_base} events/{event_ticker}: {exc}")
            time.sleep(sleep_seconds)

            for status in statuses:
                params: dict[str, Any] = {"event_ticker": event_ticker, "limit": limit}
                if status:
                    params["status"] = status
                suffix = f"markets_{event_ticker}_{status or 'all'}.json"
                market_cache = raw_day_dir / safe_file_stem(api_base) / suffix
                try:
                    data = paged_get(
                        session,
                        api_base,
                        "markets",
                        params,
                        "markets",
                        market_cache,
                        refresh,
                        sleep_seconds,
                    )
                    markets = list(data.get("markets", []))
                    markets = [m for m in markets if market_date_from_market(m) == market_date]
                    if markets:
                        return DiscoveryResult(api_base, event_ticker, markets, f"markets:{status or 'all'}", data)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{api_base} markets {event_ticker} status={status}: {exc}")
                time.sleep(sleep_seconds)

        for status in statuses[1:]:
            params = {"series_ticker": series, "status": status, "limit": limit}
            series_cache = raw_day_dir / safe_file_stem(api_base) / f"series_{series}_{status}.json"
            try:
                data = paged_get(
                    session,
                    api_base,
                    "markets",
                    params,
                    "markets",
                    series_cache,
                    refresh,
                    sleep_seconds,
                )
                markets = [m for m in data.get("markets", []) if market_date_from_market(m) == market_date]
                if markets:
                    event_ticker = str(markets[0].get("event_ticker") or "")
                    return DiscoveryResult(api_base, event_ticker, markets, f"series:{status}", data)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{api_base} series {series} status={status}: {exc}")
            time.sleep(sleep_seconds)

    raise RuntimeError("Could not discover Kalshi markets:\n" + "\n".join(errors[-20:]))


def market_window(markets: list[dict[str, Any]], market_date: date) -> tuple[datetime, datetime]:
    opens = [parse_dt(m.get("open_time")) for m in markets]
    closes = [parse_dt(m.get("close_time")) for m in markets]
    opens = [v for v in opens if v is not None]
    closes = [v for v in closes if v is not None]
    if opens and closes:
        return min(opens), max(closes)

    fallback_open = datetime.combine(market_date - timedelta(days=1), dt_time(hour=7), PT)
    fallback_close = datetime.combine(market_date + timedelta(days=1), dt_time(hour=0, minute=59), PT)
    return fallback_open.astimezone(UTC), fallback_close.astimezone(UTC)


def price_to_dollars(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if abs(number) > 1.0:
        number = number / 100.0
    return round(number, 4)


def flatten_price_block(row: dict[str, Any], prefix: str, block: Any) -> None:
    if not isinstance(block, dict):
        return
    for key in ("open", "high", "low", "close", "mean", "previous"):
        value = block.get(key)
        if value is None:
            value = block.get(f"{key}_dollars")
        row[f"{prefix}_{key}"] = price_to_dollars(value)


def normalize_market(market_date: date, event_ticker: str, market: dict[str, Any]) -> dict[str, Any]:
    ticker = str(market.get("ticker") or "")
    text = bracket_text_from_market(market)
    bracket = parse_bracket_label(ticker, text)
    open_utc = parse_dt(market.get("open_time"))
    close_utc = parse_dt(market.get("close_time"))
    row = {
        "date": market_date.isoformat(),
        "event_ticker": event_ticker or market.get("event_ticker"),
        "ticker": ticker,
        "title": market.get("title"),
        "bracket_label": bracket.label if bracket else text,
        "bracket_lo_f": bracket.lo_f if bracket else None,
        "bracket_hi_f": bracket.hi_f if bracket else None,
        "strike_type": market.get("strike_type"),
        "open_time_utc": iso_utc(open_utc),
        "open_time_pt": iso_pt(open_utc),
        "close_time_utc": iso_utc(close_utc),
        "close_time_pt": iso_pt(close_utc),
        "status": market.get("status"),
        "result": market.get("result"),
        "yes_bid": price_to_dollars(market.get("yes_bid_dollars") or market.get("yes_bid")),
        "yes_ask": price_to_dollars(market.get("yes_ask_dollars") or market.get("yes_ask")),
        "no_bid": price_to_dollars(market.get("no_bid_dollars") or market.get("no_bid")),
        "no_ask": price_to_dollars(market.get("no_ask_dollars") or market.get("no_ask")),
        "last_price": price_to_dollars(market.get("last_price_dollars") or market.get("last_price")),
        "volume": market.get("volume") or market.get("volume_fp"),
        "volume_24h": market.get("volume_24h") or market.get("volume_24h_fp"),
        "open_interest": market.get("open_interest") or market.get("open_interest_fp"),
    }
    return row


def fetch_candlesticks(
    session: requests.Session,
    api_base: str,
    ticker: str,
    start_utc: datetime,
    end_utc: datetime,
    period_interval: int,
    raw_day_dir: Path,
    refresh: bool,
) -> dict[str, Any]:
    cache_path = raw_day_dir / "candlesticks" / f"{safe_file_stem(ticker)}_{period_interval}m.json"
    params = {
        "market_tickers": ticker,
        "start_ts": timestamp_seconds(start_utc),
        "end_ts": timestamp_seconds(end_utc),
        "period_interval": period_interval,
    }
    return get_cached_or_fetch(
        session,
        api_base,
        "markets/candlesticks",
        params,
        cache_path,
        refresh,
    )


def candlesticks_from_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    body = payload(data)
    if isinstance(body.get("candlesticks"), list):
        return list(body["candlesticks"])
    markets = body.get("markets")
    if isinstance(markets, list) and markets:
        return list(markets[0].get("candlesticks", []))
    return list(body.get("candlesticks", []))


def normalize_candlestick(
    market_date: date,
    event_ticker: str,
    market_row: dict[str, Any],
    start_utc: datetime,
    period_interval: int,
    candle: dict[str, Any],
) -> dict[str, Any]:
    period_start_ts = candle.get("period_start_ts") or candle.get("start_ts")
    period_end_ts = candle.get("end_period_ts") or candle.get("period_end_ts") or candle.get("end_ts")
    period_end_utc = datetime.fromtimestamp(int(period_end_ts), tz=UTC) if period_end_ts is not None else None
    if period_start_ts is not None:
        period_start_utc = datetime.fromtimestamp(int(period_start_ts), tz=UTC)
    elif period_end_utc is not None:
        period_start_utc = period_end_utc - timedelta(minutes=period_interval)
    else:
        period_start_utc = None
    row: dict[str, Any] = {
        "date": market_date.isoformat(),
        "event_ticker": event_ticker,
        "ticker": market_row["ticker"],
        "bracket_label": market_row["bracket_label"],
        "bracket_lo_f": market_row["bracket_lo_f"],
        "bracket_hi_f": market_row["bracket_hi_f"],
        "period_start_utc": iso_utc(period_start_utc),
        "period_start_pt": iso_pt(period_start_utc),
        "period_end_utc": iso_utc(period_end_utc),
        "period_end_pt": iso_pt(period_end_utc),
        "minute_from_market_open": (
            int((period_start_utc - start_utc).total_seconds() // 60) if period_start_utc else None
        ),
        "volume": candle.get("volume") or candle.get("volume_fp"),
        "open_interest": candle.get("open_interest") or candle.get("open_interest_fp"),
    }
    flatten_price_block(row, "price", candle.get("price"))
    flatten_price_block(row, "yes_bid", candle.get("yes_bid"))
    flatten_price_block(row, "yes_ask", candle.get("yes_ask"))
    flatten_price_block(row, "no_bid", candle.get("no_bid"))
    flatten_price_block(row, "no_ask", candle.get("no_ask"))
    return row


def fetch_trades(
    session: requests.Session,
    api_base: str,
    ticker: str,
    start_utc: datetime,
    end_utc: datetime,
    raw_day_dir: Path,
    refresh: bool,
    limit: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    cache_path = raw_day_dir / "trades" / f"{safe_file_stem(ticker)}.json"
    params = {
        "ticker": ticker,
        "min_ts": timestamp_seconds(start_utc),
        "max_ts": timestamp_seconds(end_utc),
        "limit": limit,
    }
    return paged_get(
        session,
        api_base,
        "markets/trades",
        params,
        "trades",
        cache_path,
        refresh,
        sleep_seconds,
    )


def normalize_trade(
    market_date: date,
    event_ticker: str,
    market_row: dict[str, Any],
    trade: dict[str, Any],
) -> dict[str, Any]:
    created = parse_dt(trade.get("created_time") or trade.get("created_at"))
    ts = trade.get("ts") or trade.get("timestamp")
    if created is None and ts is not None:
        created = datetime.fromtimestamp(int(ts), tz=UTC)
    return {
        "date": market_date.isoformat(),
        "event_ticker": event_ticker,
        "ticker": trade.get("ticker") or market_row["ticker"],
        "bracket_label": market_row["bracket_label"],
        "bracket_lo_f": market_row["bracket_lo_f"],
        "bracket_hi_f": market_row["bracket_hi_f"],
        "created_utc": iso_utc(created),
        "created_pt": iso_pt(created),
        "trade_id": trade.get("trade_id") or trade.get("id"),
        "yes_price": price_to_dollars(trade.get("yes_price") or trade.get("yes_price_dollars")),
        "no_price": price_to_dollars(trade.get("no_price") or trade.get("no_price_dollars")),
        "count": trade.get("count"),
        "taker_side": trade.get("taker_side"),
    }


def main() -> int:
    args = parse_args()
    market_date = parse_date_arg(args.date)
    api_bases = args.api_bases or DEFAULT_API_BASES
    raw_day_dir = Path(args.raw_dir) / market_date.isoformat()
    out_day_dir = Path(args.out_dir) / market_date.isoformat()
    out_day_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "kalshi-weather-research/0.1"})

    print(f"Fetching Kalshi KLAX historical market data for {market_date}")
    discovery = discover_markets(
        session,
        api_bases,
        args.series,
        market_date,
        raw_day_dir,
        args.refresh,
        args.limit,
        args.sleep_seconds,
    )
    market_rows = [
        normalize_market(market_date, discovery.event_ticker, market)
        for market in sorted(discovery.markets, key=lambda m: str(m.get("ticker") or ""))
    ]
    markets_df = pd.DataFrame(market_rows)
    markets_path = out_day_dir / f"klax_kalshi_markets_{market_date}.csv"
    markets_df.to_csv(markets_path, index=False)

    start_utc, end_utc = market_window(discovery.markets, market_date)
    window = {
        "date": market_date.isoformat(),
        "api_base": discovery.api_base,
        "event_ticker": discovery.event_ticker,
        "discovery_source": discovery.source,
        "market_count": len(discovery.markets),
        "market_open_utc": iso_utc(start_utc),
        "market_open_pt": iso_pt(start_utc),
        "market_close_utc": iso_utc(end_utc),
        "market_close_pt": iso_pt(end_utc),
        "period_interval_minutes": args.period_interval,
    }
    window_path = out_day_dir / f"klax_kalshi_market_window_{market_date}.json"
    window_path.write_text(json.dumps(window, indent=2, sort_keys=True), encoding="utf-8")

    print(
        f"Discovered {len(market_rows)} markets from {discovery.api_base} "
        f"({discovery.event_ticker}); window {window['market_open_pt']} to {window['market_close_pt']}"
    )

    candle_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for idx, market_row in enumerate(market_rows, start=1):
        ticker = str(market_row["ticker"])
        print(f"[{idx}/{len(market_rows)}] {ticker}")
        try:
            candle_data = fetch_candlesticks(
                session,
                discovery.api_base,
                ticker,
                start_utc,
                end_utc,
                args.period_interval,
                raw_day_dir,
                args.refresh,
            )
            candle_rows.extend(
                normalize_candlestick(
                    market_date,
                    discovery.event_ticker,
                    market_row,
                    start_utc,
                    args.period_interval,
                    candle,
                )
                for candle in candlesticks_from_payload(candle_data)
            )
        except Exception as exc:  # noqa: BLE001
            failures.append({"ticker": ticker, "dataset": "candlesticks", "error": str(exc)})
        time.sleep(args.sleep_seconds)

        if not args.skip_trades:
            try:
                trade_data = fetch_trades(
                    session,
                    discovery.api_base,
                    ticker,
                    start_utc,
                    end_utc,
                    raw_day_dir,
                    args.refresh,
                    args.limit,
                    args.sleep_seconds,
                )
                trade_rows.extend(
                    normalize_trade(market_date, discovery.event_ticker, market_row, trade)
                    for trade in trade_data.get("trades", [])
                )
            except Exception as exc:  # noqa: BLE001
                failures.append({"ticker": ticker, "dataset": "trades", "error": str(exc)})
            time.sleep(args.sleep_seconds)

    candles_df = pd.DataFrame(candle_rows)
    candles_path = out_day_dir / f"klax_kalshi_candlesticks_{args.period_interval}m_{market_date}.csv"
    candles_df.to_csv(candles_path, index=False)

    history_path = out_day_dir / f"klax_kalshi_market_history_{args.period_interval}m_{market_date}.csv"
    candles_df.to_csv(history_path, index=False)

    trades_path: Path | None = None
    if not args.skip_trades:
        trades_df = pd.DataFrame(trade_rows)
        trades_path = out_day_dir / f"klax_kalshi_trades_{market_date}.csv"
        trades_df.to_csv(trades_path, index=False)

    failures_path = out_day_dir / f"klax_kalshi_pull_failures_{market_date}.csv"
    pd.DataFrame(failures).to_csv(failures_path, index=False)

    summary = {
        **window,
        "market_rows": len(markets_df),
        "candlestick_rows": len(candles_df),
        "trade_rows": len(trade_rows),
        "failures": len(failures),
        "outputs": {
            "markets": str(markets_path),
            "candlesticks": str(candles_path),
            "market_history": str(history_path),
            "trades": str(trades_path) if trades_path else None,
            "failures": str(failures_path),
            "window": str(window_path),
        },
    }
    summary_path = out_day_dir / f"klax_kalshi_pull_summary_{market_date}.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print("\nWrote outputs")
    print(f"markets: {markets_path}")
    print(f"candlesticks: {candles_path} ({len(candles_df)} rows)")
    print(f"market_history: {history_path}")
    if trades_path:
        print(f"trades: {trades_path} ({len(trade_rows)} rows)")
    print(f"summary: {summary_path}")
    if failures:
        print(f"failures: {failures_path} ({len(failures)} failures)")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
