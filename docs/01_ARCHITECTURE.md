# Architecture

```text
Kalshi market data ----\
                       +--> snapshot store --> probability engine --> signal engine --> paper broker --> ledger
NWS observations ------/
                       \
Open-Meteo models -----/
```

## Modules

```text
src/kalshi_weather/data/kalshi_client.py
  Public market and orderbook reads. Authenticated order code is future-only.

src/kalshi_weather/data/nws_client.py
  KLAX observations from api.weather.gov.

src/kalshi_weather/data/open_meteo_client.py
  Open-Meteo guidance, requested one model at a time in Phase 2.

src/kalshi_weather/model/lax_high_temp.py
  Station constants, climate-day logic, feature construction.

src/kalshi_weather/model/probability.py
  Distribution samples and bracket probabilities.

src/kalshi_weather/trading/orderbook.py
  YES/NO bid/ask math.

src/kalshi_weather/trading/signals.py
  Fair-value and fake-money decision rules.

src/kalshi_weather/trading/paper_broker.py
  Fake-money execution ledger.

src/kalshi_weather/trading/runner.py
  Collect-only and fake-money paper orchestration loops.

src/kalshi_weather/backtest/replay.py
  Replay stored snapshots.

src/kalshi_weather/validation.py
  Operational validation reports: calibration readiness, model-vs-market, and model-health.
```

## Data Store

SQLite tables:

```text
snapshots
market_snapshots
weather_snapshots
model_predictions
official_outcomes
prediction_outcomes
paper_orders
paper_fills
paper_positions
paper_equity
```

## Phase 2 Additions

```text
src/kalshi_weather/data/outcomes.py
  Best-effort NWS CLI official high-temperature outcome ingestion.

src/kalshi_weather/model/outcomes.py
  Bracket settlement logic for range/below/above brackets.

src/kalshi_weather/model/version.py
  Current model version string stored with every prediction.

scripts/make_handoff_zip.ps1
  Builds a safe handoff zip and verifies source data modules are present.
```

Phase 2 upgrades `model_predictions` into a joinable table with series,
station, market date, bracket bounds, executable prices, edges, weather
features, residual settings, and model version. `official_outcomes` stores
official KLAX high temperatures, and `prediction_outcomes` stores joined
settlement rows for calibration.

`collect-once` and `collect-loop` use the same read-only data and probability
pipeline as paper mode, but never trade, not even fake trades.

The canonical project root is:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

## Phase 3 Additions

```text
time_utils.py
  Fixed UTC-8 NWS local-standard market-date helpers.

data/open_meteo_client.py
  Model alias probing and variable fallback for richer hourly feature requests.

data/outcomes.py
  CLI product parsing hardened for daily high rows and date matching.

data/storage.py
  Outcome date helpers, richer joined-row loads, paper state events, and paper resume data.

trading/runner.py
  Opportunity diagnostics and persistent fake-money paper state.
```

New commands:

```text
time-debug
probe-open-meteo-models
opportunities
fetch-outcomes
fetch-missing-outcomes
validate-outcome-parser
residual-report
```

`fetch-missing-outcomes` skips the current fixed-standard climate date unless
`--include-current` is passed, protecting production calibration from unsettled
outcomes.
## Phase 4-7 POC Components

- `kalshi_weather.reporting` writes JSON/text reports into timestamped folders.
- `kalshi_weather.model.registry` lists supported model versions and rejects unknown versions.
- `SQLiteStore` now also stores opportunity snapshots for later replay/research.
- `daily-maintenance`, `collect-session`, and `poc-check` orchestrate existing read-only components.
- `paper-replay` and `replay-predictions` use stored rows only and do not call live APIs.

All Kalshi access remains public/read-only in the implemented code path.

## Operational Validation Components

- `model-health` reads SQLite only and summarizes whether evidence is sufficient.
- `model-vs-market` scores joined prediction/outcome rows against market-implied probabilities.
- `daily-maintenance --skip-collect` can generate reports without live collection.
- Windows wrappers in `scripts/` run collection, after-settlement reports, and model-health with logs under `logs/automation/`.

## Separate Model Estimate Comparison Components

```text
model/model_estimates.py
  Dataclasses and helpers for comparison-only model high estimates and probabilities.

data/herbie_client.py
  Optional lazy Herbie provider for direct NOAA HRRR/NBM/GFS/RAP estimates.

data/storage.py
  Sidecar tables: model_estimates and model_estimate_probabilities.

cli.py
  model-provider-probe, model-estimates, model-probabilities, and model-estimate-score.
```

The comparison layer reads the same NWS/Open-Meteo/Kalshi public inputs but
does not alter default prediction, opportunity, paper trading, or replay logic.
