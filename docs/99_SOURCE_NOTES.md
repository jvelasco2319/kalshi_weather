# Source notes for Codex

These are the external APIs and docs this package expects Codex to follow. Re-check docs before implementing any endpoint that fails.

## Kalshi

- Public market data base URL: `https://external-api.kalshi.com/trade-api/v2`
- Public market data docs: `https://docs.kalshi.com/getting_started/quick_start_market_data`
- Orderbook docs: `https://docs.kalshi.com/getting_started/orderbook_responses`
- Orderbook endpoint: `GET /markets/{ticker}/orderbook`
- Multiple orderbooks endpoint: `GET /markets/orderbooks?tickers=...`
- Create Order V2 docs exist, but live order creation is out of scope for this first package.

## LA market

- Series URL observed in Kalshi UI/search: `https://kalshi.com/markets/kxhighlax/highest-temperature-in-los-angeles`
- Series ticker target: `KXHIGHLAX`
- Station: Los Angeles Airport / KLAX / LAX

## NWS

- API base: `https://api.weather.gov`
- KLAX observations: `GET /stations/KLAX/observations`
- Include User-Agent.

## Open-Meteo

- NOAA forecast endpoint: `https://api.open-meteo.com/v1/gfs`
- Model families to start: HRRR CONUS, NBM CONUS, GFS seamless, AIGFS, HGEFS.

## Future

- Herbie can be added after prototype to pull GRIB2 data directly.
