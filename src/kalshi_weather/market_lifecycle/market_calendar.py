from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Protocol


_TICKER_DATE_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{1,2})\b")
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


class MarketMetadataClient(Protocol):
    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict[str, Any]]:
        ...


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def market_date_from_ticker(ticker: str) -> date | None:
    match = _TICKER_DATE_RE.search(ticker)
    if not match:
        return None
    month = _MONTHS.get(match.group("mon").upper())
    if month is None:
        return None
    return date(2000 + int(match.group("yy")), month, int(match.group("day")))


@dataclass(frozen=True)
class MarketTimeline:
    series_ticker: str
    event_ticker: str
    target_date: date
    market_open_time_utc: datetime | None
    last_trading_time_utc: datetime | None
    close_time_utc: datetime | None = None
    expiration_time_utc: datetime | None = None
    payout_time_estimate_utc: datetime | None = None
    status: str | None = None
    settlement_status: str | None = None
    result: str | None = None
    raw_market_metadata: dict[str, Any] | None = None

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "MarketTimeline":
        ticker = str(metadata.get("ticker") or metadata.get("market_ticker") or "")
        event_ticker = str(metadata.get("event_ticker") or _event_ticker_from_market_ticker(ticker) or "")
        target_date_raw = metadata.get("target_date") or market_date_from_ticker(event_ticker) or market_date_from_ticker(ticker)
        if isinstance(target_date_raw, date):
            target_date = target_date_raw
        elif target_date_raw:
            target_date = date.fromisoformat(str(target_date_raw))
        else:
            raise ValueError("metadata missing target_date")
        close = parse_dt(
            _first_present(
                metadata,
                "last_trading_time_utc",
                "last_trading_time",
                "close_time_utc",
                "close_time",
                "expected_expiration_time",
            )
        )
        return cls(
            series_ticker=str(metadata.get("series_ticker") or metadata.get("series") or ""),
            event_ticker=event_ticker,
            target_date=target_date,
            market_open_time_utc=parse_dt(
                _first_present(metadata, "market_open_time_utc", "open_time", "open_time_utc")
            ),
            last_trading_time_utc=close,
            close_time_utc=parse_dt(_first_present(metadata, "close_time_utc", "close_time")) or close,
            expiration_time_utc=parse_dt(
                _first_present(metadata, "expiration_time_utc", "expiration_time", "expected_expiration_time")
            ),
            payout_time_estimate_utc=parse_dt(
                _first_present(metadata, "payout_time_estimate_utc", "payout_time", "settlement_timer_time")
            ),
            status=metadata.get("status") or metadata.get("market_status"),
            settlement_status=metadata.get("settlement_status") or metadata.get("settlement_state"),
            result=metadata.get("result") or metadata.get("settlement_value"),
            raw_market_metadata=dict(metadata),
        )

    @property
    def raw(self) -> dict[str, Any] | None:
        return self.raw_market_metadata

    @property
    def trade_close_utc(self) -> datetime | None:
        return self.last_trading_time_utc or self.close_time_utc

    @property
    def metadata_complete_for_trading(self) -> bool:
        return self.market_open_time_utc is not None and self.trade_close_utc is not None

    @property
    def market_metadata_complete(self) -> bool:
        return self.metadata_complete_for_trading

    def is_open_for_trading(self, now_utc: datetime) -> bool:
        if not self.metadata_complete_for_trading:
            return False
        status = (self.status or "").lower()
        if status in {"settled", "closed", "finalized", "expired"}:
            return False
        now = now_utc.astimezone(timezone.utc)
        return bool(self.market_open_time_utc <= now <= self.trade_close_utc)

    def seconds_until_open(self, now_utc: datetime) -> float | None:
        if self.market_open_time_utc is None:
            return None
        return (self.market_open_time_utc - now_utc.astimezone(timezone.utc)).total_seconds()

    def seconds_until_close(self, now_utc: datetime) -> float | None:
        if self.trade_close_utc is None:
            return None
        return (self.trade_close_utc - now_utc.astimezone(timezone.utc)).total_seconds()

    def seconds_since_close(self, now_utc: datetime) -> float | None:
        seconds = self.seconds_until_close(now_utc)
        return None if seconds is None else max(0.0, -seconds)

    def to_debug_dict(self, now_utc: datetime | None = None) -> dict[str, Any]:
        payload = {
            "series_ticker": self.series_ticker,
            "event_ticker": self.event_ticker,
            "target_date": self.target_date.isoformat(),
            "market_open_time_utc": _dt_text(self.market_open_time_utc),
            "last_trading_time_utc": _dt_text(self.last_trading_time_utc),
            "close_time_utc": _dt_text(self.close_time_utc),
            "expiration_time_utc": _dt_text(self.expiration_time_utc),
            "payout_time_estimate_utc": _dt_text(self.payout_time_estimate_utc),
            "status": self.status,
            "settlement_status": self.settlement_status,
            "result": self.result,
            "metadata_complete_for_trading": self.metadata_complete_for_trading,
            "raw_market_metadata": self.raw_market_metadata,
        }
        if now_utc is not None:
            payload.update(
                {
                    "seconds_until_open": self.seconds_until_open(now_utc),
                    "seconds_until_close": self.seconds_until_close(now_utc),
                    "seconds_since_close": self.seconds_since_close(now_utc),
                }
            )
        return payload


@dataclass
class MarketCalendarProvider:
    client: MarketMetadataClient

    def discover_timeline(
        self,
        *,
        series_ticker: str,
        target_date: date | None = None,
        statuses: Iterable[str] = ("open", "closed", "settled"),
    ) -> MarketTimeline | None:
        markets: list[dict[str, Any]] = []
        for status in statuses:
            try:
                markets.extend(self.client.get_markets(series_ticker, status=status))
            except Exception:
                continue
        return timeline_from_markets(series_ticker=series_ticker, markets=markets, target_date=target_date)


def timeline_from_markets(
    *,
    series_ticker: str,
    markets: Iterable[dict[str, Any]],
    target_date: date | None,
) -> MarketTimeline | None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        ticker = str(market.get("ticker") or market.get("market_ticker") or "")
        event = str(market.get("event_ticker") or _event_ticker_from_market_ticker(ticker) or "")
        market_date = market_date_from_ticker(event) or market_date_from_ticker(ticker)
        if target_date is not None and market_date != target_date:
            continue
        if target_date is None and market_date is None:
            continue
        grouped.setdefault(event or ticker, []).append(market)
    if not grouped:
        return None
    event_ticker, event_markets = sorted(
        grouped.items(),
        key=lambda item: _timeline_sort_key(item[1]),
    )[0]
    merged = _merge_event_metadata(series_ticker, event_ticker, event_markets)
    return MarketTimeline.from_metadata(merged)


def incomplete_timeline(*, series_ticker: str, event_ticker: str, target_date: date) -> MarketTimeline:
    return MarketTimeline(
        series_ticker=series_ticker,
        event_ticker=event_ticker,
        target_date=target_date,
        market_open_time_utc=None,
        last_trading_time_utc=None,
        raw_market_metadata={"incomplete": True},
    )


def _merge_event_metadata(series_ticker: str, event_ticker: str, markets: list[dict[str, Any]]) -> dict[str, Any]:
    first = markets[0] if markets else {}
    merged = dict(first)
    merged["series_ticker"] = series_ticker
    merged["event_ticker"] = event_ticker or first.get("event_ticker") or first.get("ticker")
    merged["target_date"] = (
        market_date_from_ticker(str(merged.get("event_ticker") or ""))
        or market_date_from_ticker(str(merged.get("ticker") or ""))
    )
    for key in (
        "open_time",
        "open_time_utc",
        "market_open_time_utc",
        "close_time",
        "close_time_utc",
        "last_trading_time",
        "last_trading_time_utc",
        "expiration_time",
        "expiration_time_utc",
        "expected_expiration_time",
    ):
        values = [parse_dt(market.get(key)) for market in markets if market.get(key)]
        values = [value for value in values if value is not None]
        if values:
            merged[key] = min(values).isoformat() if "open" in key else max(values).isoformat()
    merged["markets"] = markets
    return merged


def _timeline_sort_key(markets: list[dict[str, Any]]) -> tuple[date, str]:
    first = markets[0] if markets else {}
    ticker = str(first.get("event_ticker") or first.get("ticker") or "")
    return (market_date_from_ticker(ticker) or date.max, ticker)


def _first_present(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value is not None and value != "":
            return value
    return None


def _event_ticker_from_market_ticker(ticker: str) -> str | None:
    parts = ticker.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return None


def _dt_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def fallback_weather_market_timeline(*, series_ticker: str, target_date: date) -> MarketTimeline:
    """Explicit fallback timeline for smoke tests when metadata fallback is enabled."""
    event_ticker = f"{series_ticker}-{target_date.strftime('%y%b%d').upper()}"
    open_time = datetime.combine(target_date - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    close_time = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return MarketTimeline(
        series_ticker=series_ticker,
        event_ticker=event_ticker,
        target_date=target_date,
        market_open_time_utc=open_time,
        last_trading_time_utc=close_time,
        close_time_utc=close_time,
        status="fallback",
        settlement_status="unknown",
        raw_market_metadata={"fallback": True},
    )
