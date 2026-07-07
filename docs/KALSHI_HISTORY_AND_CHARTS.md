# Kalshi History And Charts

This phase adds read-only Kalshi market history tracking and graph generation
for LA high-temperature markets. It is analysis-only. It does not place live
orders, does not add authenticated trading, and does not change the production
model.

## What Kalshi Candlesticks Are

Kalshi candlesticks summarize market prices over time. For this project, one
candle row represents one bracket market over one interval, usually one minute.
The row can include open/high/low/close YES bid, open/high/low/close YES ask,
midpoint/price close, volume, and open interest.

## Live Versus Historical Endpoints

Recent or currently active markets use the live/recent read-only candlestick
endpoints. Older settled markets may need historical endpoints. The CLI tries
to keep both normalized into one internal schema so charts do not care whether
the source was live or historical.

## Backfill

Discover markets:

```powershell
kalshi-weather kalshi-history-discover --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

Backfill candles:

```powershell
kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD --period-interval 1 --store
```

For KLAX, the default per-date window is the fixed-standard climate day:

```text
08:00 UTC on date D through 08:00 UTC on date D+1
```

## Trend Table

```powershell
kalshi-weather kalshi-trends --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing
```

This prints candle counts, bracket count, final and maximum YES midpoint by
bracket, favorite transitions, and whether model predictions were close enough
in time to compare.

## Charts

```powershell
kalshi-weather kalshi-trend-chart --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
```

Generated chart artifacts:

- `price_by_bracket.png`: YES midpoint over time, one line per bracket.
- `favorite_bracket_over_time.png`: which bracket had the highest market-implied YES midpoint over time.
- `volume_open_interest.png`: volume over the available candle window.
- `model_vs_market.png`: model probability versus market midpoint when nearby model predictions exist.
- `edge_over_time.png`: model edge versus market prices when nearby model predictions exist.
- `observed_high_and_model_estimate.png`: observed high and model estimate when nearby prediction/weather fields exist.
- `microtrade_candidate_windows.png`: approximate windows where best edge cleared the configured hurdle.

If data is missing, the command writes a `.txt` placeholder explaining why a
chart was not generated instead of crashing.

## Dashboard

```powershell
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
```

This creates a static HTML file at:

```text
reports/kalshi_trends/YYYY-MM-DD/dashboard.html
```

The dashboard links generated PNGs or placeholders and includes plain-English
sections about the favorite bracket, model disagreement, apparent microtrade
windows, official outcome status, and warnings.

## Model And Edge Joins

The trend helpers join candlesticks to `model_predictions` by ticker and nearest
`asof_utc`, with a default tolerance of 90 seconds for one-minute candles. If no
model prediction is close enough, model fields remain blank. The command does
not invent edges when the model probability is missing.

Computed fields include:

- market midpoint
- model minus midpoint
- YES edge versus ask
- NO edge versus ask when data exists
- best side
- best edge
- whether the edge cleared the configured hurdle

## Microtrade Trend Replay

```powershell
kalshi-weather microtrade-trend-replay --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --chart --output reports/latest_microtrade_trend_replay.json
```

This is an approximate candle-based replay. It uses candle ask-like prices for
entries and later bid/midpoint-like values for exits. It does not assume exact
fills, does not place fake fills into the paper ledger, and does not interact
with live trading.

Treat it as a visual analysis tool, not proof of executable trading performance.

## What To Upload To ChatGPT

Upload `chatgpt_kalshi_history_charts_results_package.zip`. It contains command
outputs, reports, chart files or placeholders, status files, safety confirmation,
and a source handoff zip that excludes `.env`, SQLite databases, runtime data,
snapshots, API keys, private keys, `.git`, and virtual environments.
