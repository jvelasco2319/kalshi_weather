from __future__ import annotations

from typing import Any

import requests


class KalshiPublicClient:
    """Small read-only Kalshi public market-data client."""

    def __init__(self, api_base: str = "https://external-api.kalshi.com/trade-api/v2") -> None:
        self.api_base = api_base.rstrip("/")
        self.session = requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict[str, Any]]:
        data = self._get("markets", params={"series_ticker": series_ticker, "status": status})
        return list(data.get("markets", []))

    def get_orderbook(self, ticker: str, depth: int | None = 1) -> dict[str, Any]:
        params = {"depth": depth} if depth is not None else None
        return self._get(f"markets/{ticker}/orderbook", params=params)

    def get_multiple_orderbooks(
        self, tickers: list[str], depth: int | None = 1
    ) -> dict[str, dict[str, Any]]:
        """Fetch orderbooks with the safe single-market endpoint.

        Kalshi has exposed a multi-orderbook endpoint in some docs, but the
        single-market call is stable and sufficient for the first paper runner.
        """
        return self.get_orderbooks_slow(tickers, depth=depth)

    def get_orderbooks_slow(self, tickers: list[str], depth: int | None = 1) -> dict[str, dict[str, Any]]:
        """Safe first version: loop single orderbook endpoint."""
        return {ticker: self.get_orderbook(ticker, depth=depth) for ticker in tickers}
