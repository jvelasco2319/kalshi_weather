# Current Primary Documentation Notes

The Codex implementation should inspect current official documentation during implementation and reuse the repository's existing clients.

- Kalshi Get Events: supports filtering by series ticker/status, nested markets, cursor pagination, and updated/close-time filters.
- Kalshi order-book WebSocket: authenticated; sends a snapshot followed by sequence-numbered deltas; subscriptions are by market ticker.
- Kalshi Get Trades: returns `count_fp`, fixed-point prices, and cursor pagination; an empty cursor ends pagination.
- Open-Meteo Forecast API: exposes hourly `temperature_2m` and listed ECMWF IFS, GFS Seamless, GFS, NBM, and NAM model choices. This strategy still uses the canonical providers defined in the strategy specification.
- Aviation Weather Data API: exposes METAR in JSON and other formats and asks clients to keep requests limited in scope/frequency.

Official documentation URLs are included in the master prompt implementation context only as source references; production behavior must be covered by tests and fail closed when a schema changes.
