# Sources and Implementation References

## User evidence

- `klax_july7_0724_1800_chatgpt_brief.md`
- July 7 joined model/market checkpoint analysis and data-quality findings
- July 9 pre-implementation market-data review
- Open-Meteo historical candidate file supplied by the user
- Existing repository architecture prompt describing `src/kalshi_weather`, the `kalshi-weather` CLI, SQLite journals, record-only commands, and paper safety tests

## Current official implementation references checked July 11, 2026

- Kalshi Create Order API: `https://docs.kalshi.com/api-reference/orders/create-order`
- Kalshi Get Trades API: `https://docs.kalshi.com/api-reference/market/get-trades`
- Kalshi Orderbook WebSocket: `https://docs.kalshi.com/websockets/orderbook-updates`
- Kalshi Get Market Candlesticks: `https://docs.kalshi.com/api-reference/market/get-market-candlesticks`
- Kalshi Get Series: `https://docs.kalshi.com/api-reference/market/get-series`
- Kalshi Get Market: `https://docs.kalshi.com/api-reference/market/get-market`
- Kalshi fee schedule effective July 7, 2026: `https://kalshi.com/docs/kalshi-fee-schedule.pdf`
- Open-Meteo Weather Forecast API/model list: `https://open-meteo.com/en/docs`
- Open-Meteo GFS/NOAA model API: `https://open-meteo.com/en/docs/gfs-api`

Runtime code must still version and verify changing exchange rules, schemas, fees, and model identifiers.
