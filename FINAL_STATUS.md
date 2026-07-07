# Final Status

## Phase 3 Implemented

- Canonical root remains `C:\Users\jarve\Documents\Codex\kalshi_weather`.
- Fixed NWS market-date handling to use fixed UTC-8 local standard time.
- Fixed remaining-day Open-Meteo window to end at the fixed-standard climate-day end converted to local wall time.
- Added `time-debug`.
- Added outcome range fetch, missing-outcome backfill, parser validation, and storage helper methods.
- Hardened NWS CLI daily-high parsing and date matching.
- Added Open-Meteo model alias probe command and variable fallback.
- Added richer weather model details for marine-layer features.
- Added `opportunities` diagnostics.
- Added filtered calibration report output and `residual-report`.
- Added persistent fake-money paper state resume/reset.
- Added fake-money risk settings and storage fields for richer paper reports.
- Updated docs, config, tests, and handoff packaging.

## Quality Gates

- `python -m pytest`: 52 passed.
- `python -m ruff check .`: passed.
- `python -m kalshi_weather.cli --help`: passed.

## Live Commands

The following live read-only or fake-money commands passed:

- `kalshi-weather time-debug --station KLAX`
- `kalshi-weather probe-open-meteo-models --station KLAX`
- `kalshi-weather weather-debug --station KLAX`
- `kalshi-weather opportunities --series KXHIGHLAX --station KLAX`
- `kalshi-weather collect-once --series KXHIGHLAX --station KLAX`
- `kalshi-weather fetch-missing-outcomes --station KLAX`
- `kalshi-weather join-outcomes --station KLAX --overwrite`
- `kalshi-weather calibration-report --station KLAX`
- `kalshi-weather residual-report --station KLAX`
- `kalshi-weather paper-report`
- `kalshi-weather fetch-outcomes --station KLAX --start-date 2026-06-19 --end-date 2026-06-19 --dry-run`
- `kalshi-weather validate-outcome-parser --station KLAX --start-date 2026-06-19 --end-date 2026-06-19`

## Current Canonical Data Counts

- Market snapshots: 27.
- Weather snapshots: 27.
- Model predictions: 162.
- Official outcomes: 0.
- Joined prediction outcomes: 0.
- Paper fills: 0.
- Paper equity records: 18.
- Paper state events: 0.

Production outcomes remain 0 because the only stored prediction date is the current fixed-standard climate date and `fetch-missing-outcomes` correctly skipped it. Dry-run outcome fetch and parser validation for `2026-06-19` parsed 70.0 F from NWS CLI text.

## Open-Meteo Status

- Successful probe IDs: `best_match`, `gfs013`, `gfs025`, `gfs_global`, `gfs_graphcast025`, `gfs_seamless`.
- Forecast path used `gfs_seamless`.
- Generic fallback used in latest live debug: false.
- Rejected IDs include HRRR/NBM aliases on the selected endpoint.

## Known Limitations

- Calibration/residual metrics now have a one-date smoke-test sample only; they are not proof of edge.
- Paper unrealized P&L and hold-time averages remain placeholders.
- Opportunity table output is dense in narrow terminals.
- No complex residual/ML model is trained yet.

## Live Trading Status

Live trading is disabled. No authenticated Kalshi create-order functionality is implemented or called.

## LLM Trade Advisor / Confirmed-Edge Trading

- Added fake-money-only advisor decision schema.
- Added deterministic trade-quality score with hard veto flags.
- Added `rule_based`, `prompt_only`, and optional fail-closed `llm_json` advisors.
- Added deterministic hard-risk validator with final veto authority.
- Integrated advisor gating into `paper-model-race-once` and `paper-model-race-run` behind `--advisor-mode`.
- Added `advisor_decisions` SQLite logging.
- Added `advisor-synthetic-test`, `advisor-dry-run`, `advisor-decision-report`, and `advisor-export-training-examples`.
- Added prompt file at `prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md`.
- Added docs at `docs/LLM_TRADE_ADVISOR.md`.

Safety status:
- Fake-money only: true.
- Live trading enabled by default: false.
- Advisor executes trades directly: false.
- Hard validator final veto: true.

## Phase 4-7 POC Additions

- Added safe daily collection and maintenance commands.
- Added opportunity snapshots, threshold sweep, research status, and final POC check.
- Added calibration readiness, demo calibration, residual sigma tuning, model-weight reporting,
  prediction replay, and fake paper replay.
- Added offline `poc-demo`, clearly labeled as demo-only and not trading evidence.
- Live trading remains disabled and out of scope.

## Operational Validation and Model Health

- Added `model-health` plain-English scorecard.
- Added `model-vs-market` benchmark over joined prediction/outcome rows.
- Improved `calibration-readiness` with readiness levels, missing outcomes, unsettled skips, and next commands.
- Added report-only `validation-run`.
- Added Windows automation scripts for collect session, after-settlement reports, model-health, install, and uninstall.
- Added `docs/HOW_TO_READ_RESULTS.md`.
- `daily-maintenance --skip-collect` now writes model-health, model-vs-market, and calibration-readiness reports.
- External LLM summary automation is removed from the standard workflow.
- Current model-health status: `EARLY SIGNAL` after one official outcome was fetched and 174 prediction rows were joined.
- Model-vs-market status remains `TOO_SMALL` because all joined rows are from one market date.

## Separate Model Estimate Comparison

- Added comparison-only model estimates without replacing the current production model.
- Added side-by-side current blend and individual Open-Meteo estimates.
- Added optional direct NOAA/Herbie provider rows for HRRR, NBM, GFS, and RAP.
- Added `model-provider-probe`, `model-estimates`, `model-probabilities`, and `model-estimate-score`.
- Added `model_estimates` and `model_estimate_probabilities` sidecar tables.
- Added `--include-model-estimates` for collect commands; default collection behavior remains unchanged.
- Latest live comparison status:
  - current blend: 69.33 F
  - Open-Meteo best_match: 69.8 F
  - Open-Meteo gfs013: 68.7 F
  - Open-Meteo gfs_global: 68.7 F
  - Open-Meteo gfs_seamless: 69.8 F
  - HRRR/NBM/direct GFS/RAP: unavailable because Herbie is not installed
- Tests: 84 passed.
- Ruff: passed.
- CLI help: passed.
- Live trading remains disabled and no Kalshi create-order endpoint is implemented.

## Simple Output / Human Analysis View

- Added `simple-summary` for concise model estimates, bracket probabilities,
  agreement status, warnings, and next action.
- Added `model-summary` as an alias.
- Added `weather-summary` for weather-only output without raw Open-Meteo column lists.
- Simplified `collect-session` default console output to a compact progress table.
- Preserved raw collection detail with `collect-session --verbose` and
  `collect-session --debug-json`.
- JSON and CSV outputs are available for `simple-summary`.
- Latest quality gates: `python -m pytest` passed with 95 tests, Ruff passed,
  and CLI help passed.
- Live trading remains disabled and no Kalshi create-order endpoint is implemented.

## Kalshi History And Trend Charts

- Added read-only Kalshi candlestick client methods for live/recent markets,
  historical markets, batch recent candles, historical markets, and historical cutoff.
- Added `kalshi_candlesticks` and `kalshi_trend_artifacts` SQLite tables.
- Added `kalshi-history-discover` and `kalshi-history-backfill`.
- Added `kalshi-trends`, `kalshi-trend-chart`, and `kalshi-trend-dashboard`.
- Added approximate `microtrade-trend-replay`; it does not place paper fills or live orders.
- Added static chart/dashboard documentation in `docs/KALSHI_HISTORY_AND_CHARTS.md`.
- Latest quality gates: `python -m pytest` passed with 108 tests, Ruff passed,
  and CLI help passed.
- Live trading remains disabled and no Kalshi create-order endpoint is implemented.

## Direct NOAA Herbie Activation

- Added `direct-noaa-check` for dependency, target, and live provider diagnostics.
- Installed and verified optional direct NOAA dependencies: Herbie, xarray,
  cfgrib, and eccodes.
- Direct NOAA targets are HRRR `hrrr/sfc`, NBM `nbm/co`, GFS
  `gfs/pgrb2.0p25`, and RAP `rap/awp130pgrb`.
- Live direct NOAA values were produced for all four comparison models:
  - HRRR: 71.1 F future high.
  - NBM: 68.4 F future high.
  - GFS: 68.4 F future high.
  - RAP: 75.3 F future high.
- Current/Open-Meteo estimates still work and remain the production comparison
  baseline.
- Direct NOAA models remain comparison-only and are not blended into trading or
  paper-entry behavior.
- Latest quality gates: `python -m pytest` passed with 117 tests, Ruff passed,
  and CLI help passed.
- Live trading remains disabled and no Kalshi create-order endpoint is implemented.

## Fake-Money Model Race Microtrading

- Added separate fake-money accounts for each comparison model, starting at
  $100 per model.
- Added model race storage for accounts, open positions, fills, equity, and
  audit events.
- Added `paper-model-race-once`, `paper-model-race-run`,
  `paper-model-race-report`, and `paper-model-race-reset`.
- Default output is a compact scoreboard with estimate, top bracket, best
  trade, edge, action, cash, open P/L, and closed P/L.
- Model race reports are written under `reports/model_race/`.
- Latest quality gates: `python -m pytest` passed with 144 tests, Ruff passed,
  and CLI help passed.
- Latest live fake-money check wrote `PAPER_MODEL_RACE_STATUS.md` and
  `reports/model_race/latest_model_race.txt`.
- The race is fake-money only. It does not call Kalshi order placement, does
  not require trading credentials, and does not blend models into production.
## Safer Model Race Cadence and Risk Filters

Status: implemented and validated on 2026-06-23.

- Fake-money model race now separates slow entry/model refresh cadence from fast exit monitoring.
- `paper-model-race-exit-monitor` manages existing fake positions without refreshing slow direct NOAA/Herbie models.
- New entries are blocked by missing exit bid, wide spread, penny contracts, high price, model disagreement, outlier status, stale model estimates, and stop-loss cooldowns.
- Open P/L no longer shows positive values when there is no executable bid; output shows `n/a` and `no exit bid`.
- `paper-model-race-flatten` safely closes open fake positions at available bids and requires `--confirm`.
- Full test suite passed: 162 tests.
- Ruff passed.
- CLI help passed.
- Live trading remains disabled.
## Independent Model Race Mode

Status: implemented and validated on 2026-06-23.

- Default model race mode is now `independent`.
- Independent mode lets each model trade its own fake-money account without global model-spread blocking.
- Outlier status is diagnostic only in independent mode unless `--block-outlier-models` is explicitly passed.
- `consensus_guarded` mode remains available for the previous safer/global-spread blocking behavior.
- Execution filters still apply in both modes: no ask, no exit bid, wide contract spread, penny/no-liquidity contracts, high price, cooldown, stale models, and exposure/cash limits.
- Full test suite passed: 169 tests.
- Ruff passed.
- CLI help passed.
- Live trading remains disabled.

## Synthetic Kalshi-Like Edge Case Dataset

Status: implemented and validated on 2026-06-23.

- Added an offline synthetic scenario schema and generator under `src/kalshi_weather/synthetic/`.
- Added synthetic provider adapters that feed local fake Kalshi-like data into the real fake-money model-race algorithm.
- Added `synthetic-scenarios-build`, `synthetic-scenarios-list`, `synthetic-scenario-run`, and `synthetic-algo-test`.
- Built 30 synthetic edge-case scenarios covering entry, exits, liquidity blockers, model disagreement, outliers, stale/unavailable models, rotation, no-bid P/L, and bracket settlement sanity.
- Latest synthetic algorithm run passed 30 of 30 scenarios.
- Reports and charts are under `reports/synthetic_scenarios/summary/`.
- Synthetic commands do not call real Kalshi APIs, do not require API keys, and do not place live orders.
- Live trading remains disabled.
