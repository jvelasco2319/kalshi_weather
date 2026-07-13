from __future__ import annotations

import re
from datetime import date
from typing import Iterable

from kalshi_weather.schemas import Bracket

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
_DEG = r"(?:\s*(?:°|º|deg(?:rees)?|degrees|Â°))?"
_DASH = r"(?:to|-|–|—|â€“)"
_TICKER_DATE_RE = re.compile(r"-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{1,2})\b")
_TEXT_DATE_RE = re.compile(
    r"\b(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>\d{4})\b",
    re.I,
)
_RANGE_RE = re.compile(rf"(?P<lo>-?\d+){_DEG}\s*{_DASH}\s*(?P<hi>-?\d+){_DEG}", re.I)
_BELOW_RE = re.compile(
    rf"(?P<hi>-?\d+){_DEG}\s*(?:or\s+below|or\s+lower|and\s+below)", re.I
)
_ABOVE_RE = re.compile(
    rf"(?P<lo>-?\d+){_DEG}\s*(?:or\s+above|or\s+higher|and\s+above)", re.I
)
_INCLUSIVE_BELOW_RE = re.compile(rf"(?:<=|at\s+or\s+below)\s*(?P<hi>-?\d+){_DEG}", re.I)
_INCLUSIVE_ABOVE_RE = re.compile(rf"(?:>=|at\s+or\s+above)\s*(?P<lo>-?\d+){_DEG}", re.I)
_STRICT_BELOW_RE = re.compile(rf"(?:<|less\s+than)\s*(?P<hi>-?\d+){_DEG}", re.I)
_STRICT_ABOVE_RE = re.compile(rf"(?:>|greater\s+than|more\s+than)\s*(?P<lo>-?\d+){_DEG}", re.I)


def parse_bracket_label(ticker: str, text: str) -> Bracket | None:
    """Parse a Kalshi temperature bracket label into inclusive integer-F bounds."""
    cleaned = " ".join((text or "").split())
    m = _RANGE_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=int(m.group("lo")), hi_f=int(m.group("hi")))
    m = _BELOW_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=None, hi_f=int(m.group("hi")))
    m = _ABOVE_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=int(m.group("lo")), hi_f=None)
    m = _INCLUSIVE_BELOW_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=None, hi_f=int(m.group("hi")))
    m = _INCLUSIVE_ABOVE_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=int(m.group("lo")), hi_f=None)
    m = _STRICT_BELOW_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=None, hi_f=int(m.group("hi")) - 1)
    m = _STRICT_ABOVE_RE.search(cleaned)
    if m:
        return Bracket(ticker=ticker, label=cleaned, lo_f=int(m.group("lo")) + 1, hi_f=None)
    return None


def bracket_text_from_market(market: dict) -> str:
    for key in ("subtitle", "title", "yes_sub_title", "sub_title", "rules_primary"):
        value = market.get(key)
        if value:
            return str(value)
    return str(market.get("ticker", ""))


def parse_brackets_from_markets(markets: Iterable[dict]) -> list[Bracket]:
    brackets: list[Bracket] = []
    for market in markets:
        ticker = str(market.get("ticker", ""))
        bracket = parse_bracket_label(ticker, bracket_text_from_market(market))
        if bracket is not None:
            brackets.append(bracket)
    return brackets


def market_date_from_market(market: dict) -> date | None:
    ticker = str(market.get("ticker", ""))
    ticker_match = _TICKER_DATE_RE.search(ticker)
    if ticker_match:
        year = 2000 + int(ticker_match.group("yy"))
        month = _MONTHS.get(ticker_match.group("mon").upper())
        day = int(ticker_match.group("day"))
        if month is not None:
            return date(year, month, day)

    text_match = _TEXT_DATE_RE.search(bracket_text_from_market(market))
    if text_match:
        year = int(text_match.group("year"))
        month = _MONTHS.get(text_match.group("mon").upper())
        day = int(text_match.group("day"))
        if month is not None:
            return date(year, month, day)
    return None


def filter_markets_for_date(markets: Iterable[dict], market_date: date) -> list[dict]:
    return [market for market in markets if market_date_from_market(market) == market_date]
