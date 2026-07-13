# Master spec

## Mission

Create a paper-trading system for Kalshi daily high-temperature weather markets, beginning with:

```text
Series: KXHIGHLAX
Market family: Highest temperature in LA today?
Settlement station: Los Angeles International Airport / KLAX
Settlement source: NWS Climatological Report (Daily)
Target: official daily maximum temperature in integer Fahrenheit
```

## Non-negotiable constraints

1. Start read-only against Kalshi.
2. Trade with fake money only.
3. Never send real Kalshi orders unless ChatGPT later produces a new explicit package/prompt for that stage.
4. Every decision must be reproducible from a stored snapshot.
5. Every backtest must be point-in-time safe.
6. Model output must be probabilistic, not just a high-temperature point estimate.
7. All brackets for an event should be modeled together so probabilities are coherent.

## Current end goal

A command like this should run continuously:

```powershell
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
```

It should:

1. Discover open LA high-temp markets.
2. Fetch orderbooks.
3. Fetch KLAX observations and Open-Meteo model guidance.
4. Estimate bracket probabilities.
5. Compare fair probabilities to market prices.
6. Simulate buy/sell decisions with fake money.
7. Record snapshots and paper P&L.

## Phase 2 stabilization goal

The canonical project root is:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

Phase 2 keeps the project fake-money only and adds:

1. Per-model Open-Meteo fetch diagnostics.
2. Collect-only mode for storing market/weather/prediction rows without trading.
3. Joinable model prediction storage with model versioning.
4. Official outcome storage from NWS CLI products when available, with manual fallback.
5. Prediction/outcome joining for bracket settlement.
6. Calibration reports over joined rows.
7. Paper performance reporting.
8. A safe handoff zip script that excludes secrets and runtime data.

Live Kalshi order placement remains out of scope.

## Phase 3 research-engine goal

Phase 3 turns the runnable MVP into a calibration and fake-money research
engine:

1. Market dates are computed from fixed UTC-8 local standard time, matching the NWS climate day.
2. `time-debug` exposes UTC, America/Los_Angeles wall time, fixed-standard time, and climate-day windows.
3. Official outcomes can be fetched across ranges, backfilled for prediction dates, validated, manually recorded, and joined.
4. Open-Meteo model aliases can be probed, and unsupported model/variable combinations are surfaced.
5. Opportunity diagnostics explain why fake trades are or are not triggered.
6. Paper cash and positions resume from SQLite unless `--reset-paper` is used.
7. Calibration and residual reports operate over joined prediction/outcome rows.

Live Kalshi order placement remains out of scope.

## Phase 4-7 POC goal

Phase 4-7 finishes the fake-money proof-of-concept workflow:

1. Preferred Open-Meteo models are fetched one at a time and blended with explicit weights.
2. Safe daily maintenance and collect-session commands gather read-only evidence.
3. Official outcomes are only stored after a settlement buffer unless explicitly forced.
4. Calibration readiness, calibration reports, residual reports, model-weight diagnostics, and replay reports distinguish production evidence from demo fixtures.
5. `poc-demo` proves offline plumbing only and is labeled demo-only.
6. `poc-check` runs the full safe POC validation workflow and writes timestamped reports.

Live Kalshi order placement remains out of scope.

## Codex operating instruction

Codex is the executor. Do not redesign the strategy. Implement the numbered prompts in order. If a prompt conflicts with a file, ask for clarification rather than improvising.
