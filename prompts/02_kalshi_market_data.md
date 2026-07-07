# Prompt 02 — Kalshi public market data

Implement read-only Kalshi market data.

## Files to implement/update

```text
src/kalshi_weather/data/kalshi_client.py
src/kalshi_weather/data/market_discovery.py
src/kalshi_weather/trading/orderbook.py
src/kalshi_weather/cli.py
tests/test_orderbook_math.py
```

## Tasks

1. Implement a small `KalshiPublicClient` using `requests.Session`.
2. Add methods:
   - `get_markets(series_ticker: str, status: str = "open")`
   - `get_orderbook(ticker: str, depth: int | None = 1)`
   - `get_multiple_orderbooks(tickers: list[str])` if docs/params confirm the exact call; otherwise loop single orderbooks safely.
3. Implement orderbook math:
   - best YES bid = highest YES bid level
   - best NO bid = highest NO bid level
   - implied YES ask = `1 - best NO bid`
   - implied NO ask = `1 - best YES bid`
4. Implement robust parsing for both fixed-point dollar responses and older cent-style responses if encountered.
5. Wire CLI:

```powershell
kalshi-weather markets --series KXHIGHLAX
```

6. CLI output should show ticker, title/subtitle, yes bid, no bid, implied yes ask, implied no ask.

## Tests

Add/keep tests for:

```text
empty orderbook
fixed-point orderbook
asks implied from opposite bids
best bid from last/highest price
```

## Acceptance criteria

```powershell
pytest tests/test_orderbook_math.py
kalshi-weather markets --series KXHIGHLAX
```

Expected:

```text
Tests pass.
Command prints open KXHIGHLAX markets or a clear “no open markets found” message.
```

## Do not do

Do not place orders.
Do not require Kalshi API keys for public data.
