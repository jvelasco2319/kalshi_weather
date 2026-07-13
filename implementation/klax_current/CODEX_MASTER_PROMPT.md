# Master Prompt for Codex

You are implementing strategy `klax-current-five-model-2026-07-11` in the existing repository:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

The user did **not** implement v1, v2, or v3. Do not attempt to install, migrate, or layer those packages. This package is a clean, authoritative implementation against the current repository.

## First actions — no production edits yet

1. Copy this package into the repository under `implementation/klax_current/` or another non-imported documentation directory.
2. Run:

   ```bash
   PYTHONPATH=reference python reference/verify_package.py
   python -m pytest -q
   python -m ruff check src tests
   ```

3. Inspect the actual repository. At minimum inspect:
   - `src/kalshi_weather/cli.py`;
   - the existing model registry/configuration;
   - Open-Meteo fetchers;
   - NOAA/Herbie NBM fetcher;
   - KLAX observation/METAR code;
   - the current shared weather/market context builder;
   - Kalshi REST/WebSocket clients;
   - SQLite/JSONL journal code;
   - `paper-model-race-run`, `record-weather-market-once`, `record-weather-market-loop`, and `analyze-model-validation`;
   - `tests/test_cli.py` and `tests/test_trader_paper_safety.py`.
4. Create `docs/klax_current_repo_mapping.md` using `integration/repo_mapping_template.md`.
5. Map every required responsibility to an existing module before creating a new module.
6. Run the existing tests and record the baseline result.
7. Only then begin Phase P1 from `CODEX_TASK_GRAPH.yaml`.

## Authority

1. This package.
2. Later explicit human instructions.
3. Existing repository behavior where it does not conflict with this package.

Do not revive strategy equations from older packages.

## Reuse contract

Reuse existing code when it already does the job correctly:

- current CLI and command registration;
- model fetchers and retry/rate-limit behavior;
- KLAX coordinates and station observation parsing;
- Kalshi authentication and market discovery;
- shared snapshot caching;
- SQLite connection/migration conventions;
- JSONL/raw payload storage;
- paper/live safety flags;
- account reconciliation and order adapters, if present and correct.

Create new code only for responsibilities that are absent or semantically wrong:

- strict backward as-of selection;
- full forecast-path persistence;
- remaining-window state construction;
- matching historical reconstruction;
- model-specific residual distributions;
- reliability-weighted five-model probability mixture;
- settlement-gap separation;
- exact conservative probability and fee-aware ROI logic;
- sequence-valid orderbook state;
- immutable decision records and event-level risk;
- shadow execution sink and promotion reporting.

Do not duplicate a working network client merely to match suggested file names.

## Exact model policy

The new strategy model set is exactly:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

Canonical source preferences:

```text
open_meteo:ecmwf_ifs
open_meteo:gfs013
open_meteo:gfs_seamless
open_meteo:nam              # nam_conus is an alias, not a second vote
noaa_herbie:nbm             # do not silently mix provider histories
```

Other existing model keys remain available only to legacy commands. They must be ignored by the current strategy, replay, probability mixture, spread, and risk calculations.

## Required safety mode

```text
mode = shadow
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
```

Shadow mode must have no callable live-order path. Use a separate `ShadowOrderSink` or equivalent dependency that cannot submit an exchange order. A conditional branch around a real order client is insufficient.

## Engineering rules

- Python style and packaging must follow the existing repository.
- Use timezone-aware UTC internally; derive target-day logic in `America/Los_Angeles`.
- Use `Decimal` or fixed-point integer/string fields for prices, quantities, fees, and bankroll.
- Persist raw payloads plus normalized immutable events.
- Every decision must be reproducible from persisted source event IDs, config hash, and code revision.
- Enforce `source_available_at <= evaluated_at` and `received_at <= evaluated_at`.
- Do not use nearest-time joins.
- Do not calibrate target-day live states from historical full-day scalar maxima.
- Do not infer maker fills or depth from candlesticks.
- Require `count_fp` on trades and cursor exhaustion.
- Invalidate an order book on any sequence gap and resnapshot.
- Cancel before replace; reevaluate after cancellation.
- Never log credentials or private keys.
- Every failed gate emits a stable machine-readable reason code.
- Do not average per-trade ROI. Aggregate net P&L divided by aggregate capital deployed.

## Completion sequence

Follow `CODEX_TASK_GRAPH.yaml` in order. Each phase must produce:

- implementation code;
- unit and integration tests;
- schema/migration updates;
- a concise phase report;
- commands run and results;
- any deviation in `docs/klax_current_deviations.md`.

Stop before canary or live enablement. A human must separately approve that work after the promotion report passes.
