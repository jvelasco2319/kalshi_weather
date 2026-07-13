# KLAX Current Strategy - Repository Mapping

## Baseline

- Repository revision: no Git `HEAD` yet; repository is on unborn `master`.
- Active workspace: `C:\Users\jarve\OneDrive\Documents\kalshi_weather`.
- Package reference path copied to: `implementation/klax_current/`.
- Python version: Python 3.12.7.
- Packaging tool: setuptools via `pyproject.toml`.
- CLI framework: Typer, `kalshi_weather.cli:app`.
- Package verification: `PYTHONPATH=reference python reference/verify_package.py` from `implementation/klax_current` passed, 18 reference tests and 8 schemas.
- Test command/result: `python -m pytest -q` passed with all collected tests. Warnings came from older installed `numexpr` and `bottleneck` versions used by pandas.
- Ruff command/result: `python -m ruff check src tests` passed.
- SQLite migration mechanism: inline additive `CREATE TABLE IF NOT EXISTS` and `_ensure_*_columns()` checks in `SQLiteStore`; `ValidationJournal.ensure_schema()` for validation capture.
- Existing deployment/runtime: local CLI command `kalshi-weather`; existing runtime modes are record-only validation, collect-only, and paper trading. No live order path is present.

## Existing module mapping

| Required responsibility | Existing module/function/table | Reuse as-is | Modify | New code | Notes/tests |
|---|---|---:|---:|---:|---|
| CLI registration | `src/kalshi_weather/cli.py`, Typer `app` | yes | yes | no | Add current strategy commands here only. Existing commands in `tests/test_cli.py` must remain visible. |
| Open-Meteo ECMWF | `OpenMeteoClient.forecast_hourly_by_model`, `model_registry.ecmwf_ifs` | partial | yes | no | Existing client accepts arbitrary model ids, but default base URL is GFS. Current strategy needs canonical `open_meteo:ecmwf_ifs` source identity and endpoint handling. |
| Open-Meteo GFS013 | `OpenMeteoClient.forecast_hourly_by_model`, `model_registry.gfs013` | yes | yes | no | Reuse fetch path; extend persistence to full forecast path and source timestamps. |
| Open-Meteo GFS Seamless | `OpenMeteoClient.forecast_hourly_by_model`, `model_registry.gfs_seamless` | yes | yes | no | Reuse fetch path; extend persistence to full forecast path and source timestamps. |
| Open-Meteo NAM | `model_registry.nam`, `model_registry.nam_conus`, `open_meteo_params_for_keys()` | partial | yes | no | Preserve `nam_conus` as alias/fallback, never a second strategy vote. |
| NOAA/Herbie NBM | Registry entry `model_registry.nbm` only | no | no | yes | No working Herbie/NBM fetcher found in `src`; P2 needs an additive provider module or optional integration. |
| KLAX observations | `validation_recorder.AWCMetarClient`, `NWSClient.station_observations`, `awc_metars_to_frame()` | partial | yes | no | Reuse METAR/NWS parsing; add QC status, immutable observation events, source timestamps. |
| Kalshi market discovery | `KalshiPublicClient.get_markets`, `market_discovery.filter_markets_for_date`, `parse_brackets_from_markets` | yes | yes | no | Reuse parsing; add versioned rules/series metadata for current strategy. |
| Kalshi rules/series | Raw market payloads in validation snapshots | partial | yes | maybe | Need fail-closed versioned rule capture and settlement parsing. |
| Kalshi trades | None found | no | no | yes | P2 must add public trade pull with `count_fp` and exhausted cursor audit. |
| Kalshi orderbook WebSocket | None found | no | no | yes | Existing REST top-of-book parser is not sequence-valid. P2/P7 need snapshot/delta state and invalidation. |
| SQLite/JSONL journal | `SQLiteStore`, `ValidationJournal`, `append_jsonl` | partial | yes | no | Extend additively; avoid an unrelated DB. |
| Forecast path persistence | Only aggregate model maxes and raw JSON payload fragments | no | yes | yes | Need run-level and point-level persistence with run, valid, available, received timestamps. |
| As-of state builder | `remaining_lax_day_local`, `_forecast_window`, `weather_snapshot_from_frames` | partial | yes | yes | Existing logic computes remaining live max but is not a pure immutable as-of builder and lacks historical reconstruction. |
| Residual reconstruction | `validation_analysis.analyze_model_validation` | no | no | yes | Existing analysis uses scalar estimated highs and final highs; current strategy needs model-specific reconstructed live-state residuals. |
| Probability mixture | `model.probability` normal residual sampler | no | no | yes | Replace for current strategy with model-specific residual distributions and reliability-weighted mixture. Preserve legacy behavior. |
| Fee/economics | `trading.signals`, `fee_buffer` setting | no | no | yes | Existing edge logic is simple threshold math; current strategy needs exact fee-aware ROI/price-grid enumeration. |
| Event risk | `trading.risk.RiskLimits` | partial | yes | yes | Reuse Decimal conventions; add event-outcome loss matrix and current strategy gates. |
| Shadow sink | No current equivalent | no | no | yes | Add non-submitting `ShadowOrderSink`; do not wrap a real order client. |
| Replay | `backtest.replay.replay_snapshots`, CLI `replay`, `replay-predictions`, `paper-replay` | partial | yes | yes | Existing replay is JSON snapshot/paper replay. Current strategy needs chronological source-event replay and candle disclaimers. |
| Safety tests | `tests/test_safety_phase2.py`, `tests/test_paper_broker.py`, `tests/test_cli.py` | partial | yes | yes | Prompt-mentioned `tests/test_trader_paper_safety.py` is absent. Strengthen no-live-order and shadow-only tests. |

## Existing interfaces that must remain backward compatible

- `kalshi-weather record-weather-market-once`
- `kalshi-weather record-weather-market-loop`
- `kalshi-weather analyze-model-validation`
- Existing `collect-*`, `paper-*`, `replay-*`, `poc-*`, and report commands.
- `src/kalshi_weather/model_registry.py` public helpers: `select_model_keys`, `open_meteo_model_keys`, `open_meteo_params_for_keys`, `registry_rows`.
- `OpenMeteoClient.forecast_hourly()` and `forecast_hourly_by_model()`.
- `KalshiPublicClient` REST market and orderbook methods.
- `SQLiteStore` current tables and report methods.
- `ValidationJournal` current snapshot tables and duplicate-bucket behavior.

## Obsolete behavior to isolate, not delete

- `current_weighted_blend`, `best_match`, `gfs_global`, `hrrr`, `rap`, `aifs`, and other legacy registry entries remain available to legacy commands but are excluded from current strategy calculations.
- Legacy scalar model high fields remain useful for existing reports, but cannot certify current strategy residuals.
- Existing normal residual probability and paper edge logic remain available to old paper commands, but are not used by `klax-current-five-model-2026-07-11`.
- Existing REST top-of-book snapshots remain useful for display and legacy paper mode, but cannot satisfy sequence-valid executable-book gates.

## Additive migrations

- Add strategy config/version table.
- Add raw source payload table with content hash and source identity.
- Add forecast run and forecast path point tables.
- Add KLAX observation event table with QC status and availability timestamps.
- Add market rule/fee version tables.
- Add orderbook snapshot/delta tables with sequence validity and invalidation reason.
- Add public trade and trade-page audit tables with `count_fp`.
- Add capture completeness manifest table.
- Add decision state, per-model live state, model weight, probability, economics/risk, and shadow order tables.

## Genuine blockers

- No Herbie/NBM fetcher exists in this repository despite the package assuming one. P2 must add it or document a provider integration blocker.
- No Kalshi authenticated client or live order path exists, which is good for shadow safety. Canary/live work remains prohibited.
- No WebSocket client exists for sequence-valid orderbooks.
- No public trade pagination client exists.
- `paper-model-race-run` is referenced by the package but no matching CLI command exists in this repo.
- `tests/test_trader_paper_safety.py` is referenced by the package but absent; closest existing safety test is `tests/test_safety_phase2.py`.

## Proposed phase/file plan

- P1: add `src/kalshi_weather/strategy_current/config.py`, `registry.py`, and tests for the exact five-model policy. Keep legacy registry untouched except where helper reuse is safe.
- P2: add `strategy_current/persistence.py`, `source_events.py`, and additive SQLite schema helpers; extend collectors to persist full paths, observations, trades, books, and manifests.
- P3: add `strategy_current/state_builder.py` with strict backward as-of selection and shared live/historical state construction.
- P4-P6: add residual, weight, probability, settlement, economics, and risk modules in `strategy_current`.
- P7-P9: add shadow runtime, replay, status, capture validation commands, and promotion reporting through the existing CLI.
