from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import requests


class OutcomeUnavailableError(RuntimeError):
    """Raised when the official climate product is unavailable or unparseable."""


@dataclass(frozen=True)
class OfficialOutcome:
    station: str
    market_date: date
    metric: str
    official_high_f: float
    source: str
    source_url: str | None
    source_text: str | None
    fetched_at_utc: datetime


class NWSClimateProductClient:
    """Best-effort NWS CLI climate product reader."""

    def __init__(self, user_agent: str, api_base: str = "https://api.weather.gov") -> None:
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/geo+json"})

    def fetch_daily_high(self, station: str, market_date: date) -> OfficialOutcome:
        location = station[1:] if station.upper().startswith("K") else station
        products = self._cli_products(location)
        products_to_try = self._dated_cli_products(products, market_date) or products[:10]
        source_url = None
        for product in products_to_try:
            source_url = self._product_url(product)
            text = self._fetch_product_text(source_url)
            if not cli_text_matches_date(text, market_date):
                continue
            high = parse_cli_high_temperature(text)
            if high is None:
                continue
            break
        else:
            raise OutcomeUnavailableError(
                f"No parseable NWS CLI product found for location {location} date {market_date.isoformat()}"
            )
        return OfficialOutcome(
            station=station.upper(),
            market_date=market_date,
            metric="official_high_f",
            official_high_f=float(high),
            source="nws_cli",
            source_url=source_url,
            source_text=text,
            fetched_at_utc=datetime.now(timezone.utc),
        )

    def _cli_products(self, location: str) -> list[dict[str, Any]]:
        url = f"{self.api_base}/products/types/CLI/locations/{location.upper()}"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        products = response.json().get("@graph", [])
        if not products:
            raise OutcomeUnavailableError(f"No NWS CLI products found for location {location}")
        return list(products)

    def _dated_cli_products(self, products: list[dict[str, Any]], market_date: date) -> list[dict[str, Any]]:
        iso_token = market_date.strftime("%Y-%m-%d")
        slash_token = market_date.strftime("%m/%d/%Y")
        return [
            product
            for product in products
            if iso_token in str(product.get("issuanceTime", ""))
            or slash_token in str(product)
        ]

    def _fetch_product_text(self, product_id: str) -> str:
        response = self.session.get(product_id, timeout=30)
        response.raise_for_status()
        data = response.json()
        text = data.get("productText")
        if not text:
            raise OutcomeUnavailableError("NWS product response did not include productText")
        return str(text)

    def _product_url(self, product: dict[str, Any]) -> str:
        product_url = product.get("@id")
        if product_url:
            return str(product_url)
        product_id = product.get("id")
        if not product_id:
            raise OutcomeUnavailableError("NWS product listing did not include an id")
        return f"{self.api_base}/products/{product_id}"


def parse_cli_high_temperature(text: str) -> int | None:
    """Parse daily high from a CLI-style climate report text body."""
    skip_tokens = ("MONTH", "YEAR", "RECORD", "NORMAL", "DEPARTURE", "AVERAGE")
    daily_patterns = [
        r"^\s*MAXIMUM\s+(-?\d{1,3})\b",
        r"^\s*MAX\s+TEMP(?:ERATURE)?\s+(-?\d{1,3})\b",
        r"^\s*HIGHEST\s+TEMPERATURE\s+(-?\d{1,3})\b",
    ]
    for line in text.splitlines():
        upper = line.upper()
        if any(token in upper for token in skip_tokens):
            continue
        for pattern in daily_patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))

    patterns = [
        r"\bDAILY\s+MAX(?:IMUM)?\s+(?:TEMPERATURE\s+)?(-?\d{1,3})\b",
        r"\bMAXIMUM\s+TEMPERATURE\s+(-?\d{1,3})\b",
        r"\bHIGHEST\s+TEMPERATURE\s+(-?\d{1,3})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def cli_text_matches_date(text: str, market_date: date) -> bool:
    normalized = re.sub(r"\s+", " ", text.upper())
    month_full = market_date.strftime("%B").upper()
    month_abbr = market_date.strftime("%b").upper()
    day = str(market_date.day)
    day_zero = market_date.strftime("%d")
    year = str(market_date.year)
    tokens = {
        market_date.strftime("%m/%d/%Y"),
        f"{month_full} {day} {year}",
        f"{month_full} {day_zero} {year}",
        f"{month_abbr} {day} {year}",
        f"{month_abbr} {day_zero} {year}",
    }
    return any(token in normalized for token in tokens)
