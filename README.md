# kalshi-weather

Local paper-trading research package for Kalshi LA high-temperature markets.

The current target is `KXHIGHLAX`, using KLAX/LAX observations and Open-Meteo
forecast guidance to estimate probabilities for Kalshi temperature brackets.
All execution is fake-money simulation. There is no live Kalshi order placement
code in this package.

## Setup

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather
python -m pip install -e ".[dev]"
copy .env.example .env
python -m pytest
python -m ruff check .
```

Update `.env` with a descriptive `NWS_USER_AGENT` before daily use.

## Commands

```powershell
kalshi-weather --help
kalshi-weather markets --series KXHIGHLAX
kalshi-weather weather-snapshot --station KLAX
kalshi-weather weather-debug --station KLAX
kalshi-weather time-debug --station KLAX
kalshi-weather probe-open-meteo-models --station KLAX
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
kalshi-weather predict-once --series KXHIGHLAX --station KLAX --store
kalshi-weather opportunities --series KXHIGHLAX --station KLAX
kalshi-weather collect-once --series KXHIGHLAX --station KLAX
kalshi-weather collect-loop --series KXHIGHLAX --station KLAX --interval-seconds 60 --max-iterations 10
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges
kalshi-weather weather-summary --station KLAX
kalshi-weather kalshi-history-discover --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD
kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD --period-interval 1 --store
kalshi-weather kalshi-trends --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing
kalshi-weather kalshi-trend-chart --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
kalshi-weather microtrade-trend-replay --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --chart
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
kalshi-weather paper-once --series KXHIGHLAX --station KLAX --reset-paper
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60 --max-iterations 3
kalshi-weather fetch-outcome --station KLAX --date YYYY-MM-DD
kalshi-weather fetch-outcomes --station KLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD --dry-run
kalshi-weather fetch-missing-outcomes --station KLAX
kalshi-weather validate-outcome-parser --station KLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD
kalshi-weather record-outcome --station KLAX --date YYYY-MM-DD --official-high-f 71 --source manual
kalshi-weather join-outcomes
kalshi-weather replay --snapshot-dir data/snapshots
kalshi-weather calibration-report --station KLAX
kalshi-weather residual-report --station KLAX
kalshi-weather paper-report
kalshi-weather calibration-readiness --station KLAX
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
kalshi-weather model-health --series KXHIGHLAX --station KLAX
kalshi-weather model-provider-probe --station KLAX
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX
kalshi-weather advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch
kalshi-weather advisor-dry-run --series KXHIGHLAX --station KLAX --advisor-mode rule_based
kalshi-weather advisor-decision-report --race-id advisor_smoke
kalshi-weather advisor-export-training-examples --race-id advisor_smoke --output-dir reports/llm_trade_advisor/training_examples
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --advisor-mode rule_based
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --interval-seconds 900 --max-iterations 2
kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-reset --race-id default --confirm
kalshi-weather synthetic-scenarios-build --scenario-set model_race_edge_cases --overwrite
kalshi-weather synthetic-scenarios-list
kalshi-weather synthetic-scenario-run --scenario-id clear_yes_profit_target --charts --fail-on-mismatch
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
kalshi-weather model-estimate-score --station KLAX
kalshi-weather validation-run --series KXHIGHLAX --station KLAX --after-settlement
```

The module form also works after installation:

```powershell
python -m kalshi_weather.cli --help
```

## Synthetic Edge-Case Harness

The project now includes an offline synthetic Kalshi-like dataset for testing
the fake-money model-race algorithm against controlled edge cases. It generates
30 local JSON scenarios with bracket markets, YES/NO prices, missing bids,
liquidity states, model estimates, probabilities, expected actions, and final
fake-account checks.

Use it when you want to ask, "does the algorithm recognize this edge case?"
without pulling real Kalshi data:

```powershell
kalshi-weather synthetic-scenarios-build --overwrite
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

Reports are written under `reports/synthetic_scenarios/summary/`. See
`docs/SYNTHETIC_EDGE_CASES.md` for the scenario list and interpretation guide.
This harness does not prove profitability and does not place live orders.

## LLM Trade Advisor

The fake-money model race can optionally use a confirmed-edge advisor gate:

```text
candidate edge -> trade quality score -> advisor recommendation -> hard validator -> fake-money fill/log
```

Use `--advisor-mode rule_based` for deterministic confirmed-edge testing without any external LLM. Use `prompt_only` to write prompt/input artifacts for manual review. `llm_json` is optional and fails closed unless explicitly configured. The advisor never executes trades; the hard validator can veto any buy.

The Ollama GPT-OSS advisor is available behind explicit LLM flags:

```powershell
kalshi-weather llm-advisor-smoke-test --rule-only
kalshi-weather llm-advisor-smoke-test --provider ollama --model gpt-oss:120b
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --use-llm-advisor --llm-provider ollama --llm-model gpt-oss:120b
```

Default behavior remains unchanged unless `--use-llm-advisor` is set. `--llm-rule-only` uses deterministic quality and the hard validator without Ollama. `--llm-dry-run` logs the LLM decision without letting it change the fake-money race action.

See `docs/LLM_TRADE_ADVISOR.md` and `docs/OLLAMA_GPT_OSS_LLM_ADVISOR.md` for commands, score interpretation, decision logging, and safety notes.

## What It Does

- Reads public Kalshi market and orderbook data.
- Reads NWS KLAX observations for the Pacific standard-time climate day.
- Reads Open-Meteo hourly forecast data for LAX coordinates.
- Parses Kalshi bracket labels, including ranges plus `<67 deg` / `>74 deg` open-ended labels.
- Computes Monte Carlo bracket probabilities from a simple residual model.
- Compares model probabilities to executable bid/ask prices.
- Simulates conservative fake fills only: buy at ask, sell at bid.
- Stores SQLite records and JSON decision snapshots under `data/`.
- Stores joinable prediction rows with model version, bracket bounds, prices, edges, and weather features.
- Stores official outcomes manually or from best-effort NWS CLI-style climate products.
- Computes market dates from fixed UTC-8 local standard time, matching NWS climate-day settlement.
- Probes Open-Meteo model aliases and reports which identifiers work.
- Resumes fake-money paper cash/positions from SQLite unless `--reset-paper` is used.
- Replays saved snapshots without live API calls.
- Backfills read-only Kalshi candlesticks for LA high-temperature markets.
- Generates market trend charts and static dashboards from stored candlesticks.
- Runs approximate candle-based microtrade trend replay without changing the paper ledger.

## Simple Outputs For Daily Analysis

Use `simple-summary` when you want the shortest useful answer:

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX
```

It shows the current production estimate, each comparison model's estimated
settlement high, each model's bracket probabilities, agreement status, and a
plain warning about whether the evidence is still only a smoke test.

`model-summary` is an alias:

```powershell
kalshi-weather model-summary --series KXHIGHLAX --station KLAX
```

Market prices and edges are hidden by default. Add them only when you want to
inspect apparent mismatches:

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges
```

Use `weather-summary` for a weather-only view without raw Open-Meteo column
lists:

```powershell
kalshi-weather weather-summary --station KLAX
```

`collect-session` now prints a compact progress table by default. Use
`--verbose` for the older detailed object output or `--debug-json` for the full
raw nested JSON when troubleshooting:

```powershell
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 10
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --max-iterations 1 --debug-json
```

All of these commands are analysis-only or collect-only. They do not place live
orders and do not create fake paper fills.

## Kalshi History And Trend Charts

Use these commands to see how LA high-temperature bracket prices moved over
time:

```powershell
kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date 2026-06-20 --end-date 2026-06-20 --period-interval 1 --store
kalshi-weather kalshi-trends --series KXHIGHLAX --station KLAX --date 2026-06-20
kalshi-weather kalshi-trend-chart --series KXHIGHLAX --station KLAX --date 2026-06-20 --output-dir reports/kalshi_trends
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date 2026-06-20 --output-dir reports/kalshi_trends
kalshi-weather temperature-estimate-chart --station KLAX --date 2026-06-20 --output-dir reports/temperature_estimates
```

The primary chart is `price_by_bracket.png`, which plots YES midpoint by
bracket. `model_vs_market.png` and `edge_over_time.png` are generated when
nearby model prediction rows exist. Missing-data charts produce text
placeholders rather than failing.

The key weather-estimation chart is
`reports/temperature_estimates/YYYY-MM-DD/actual_vs_model_temperatures.png`.
It plots actual NWS observed temperature, observed high so far, the production
temperature estimate, individual model estimates when stored, and the official
high when available.

`microtrade-trend-replay` is approximate candle analysis only. It does not
place live orders and does not write fake fills to the paper ledger.

## Safety

This package is paper-trading only.

- `KALSHI_ENABLE_REAL_ORDERS=false` by default.
- No create-order endpoint is implemented.
- No API keys are needed for current market-data commands.
- `.env`, private keys, SQLite files, and snapshots are ignored by git.

## Known Limitations

- Some configured Open-Meteo model identifiers may be rejected by the selected endpoint. Phase 2 requests each model separately, records successes/failures, and uses the generic fallback only when every model-specific request fails.
- The v0.2 forecast model is intentionally simple: blended future high plus global normal residual.
- Calibration reports need stored official outcomes before metrics are meaningful.
- Paper state resume/reset is implemented; paper reports still leave hold-time and mark-to-market P&L unavailable until more fill/quote history is captured.

## Phase 2 Notes

The intended canonical project directory is:

```powershell
C:\Users\jarve\Documents\Codex\kalshi_weather
```

Phase 2 adds:

- Per-model Open-Meteo requests with explicit success/failure diagnostics and generic fallback only when model-specific requests all fail.
- `weather-debug` for inspecting Open-Meteo model status.
- Collect-only commands that store market, weather, and prediction rows without fake or live trading.
- Joinable prediction storage with model version, bracket bounds, prices, edges, and weather features.
- Official outcome storage via best-effort NWS CLI fetch or manual record fallback.
- Outcome joining and calibration reports over joined prediction/outcome rows.
- `paper-report` for fake-money performance summaries.
- `scripts/make_handoff_zip.ps1` for creating a safe handoff zip that excludes secrets/runtime data.

Live trading remains disabled. There is still no Kalshi create-order endpoint in this package.

## Phase 3 Notes

Phase 3 adds exact NWS local-standard-time handling, outcome range/backfill
commands, parser validation, Open-Meteo alias probing, richer marine-layer
features, opportunity diagnostics, filtered calibration/residual reports, and
persistent fake-money paper state.

`fetch-missing-outcomes` skips the current fixed-standard climate date unless
`--include-current` is passed, so it will not store unsettled production
outcomes by accident.

## Handoff Zip

```powershell
.\scripts\make_handoff_zip.ps1
Get-Content .\HANDOFF_ZIP_CHECK.txt
```

The zip intentionally excludes `.env`, `.git`, `.venv`, runtime `data/`,
SQLite files, caches, snapshots, and key material.

## Phase 4-7 POC Workflow

Safe collection and maintenance:

```powershell
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
kalshi-weather daily-maintenance --series KXHIGHLAX --station KLAX
```

POC validation:

```powershell
kalshi-weather research-status --series KXHIGHLAX --station KLAX
kalshi-weather opportunities --series KXHIGHLAX --station KLAX --short
kalshi-weather threshold-sweep --series KXHIGHLAX --station KLAX
kalshi-weather calibration-readiness --station KLAX
kalshi-weather paper-replay --series KXHIGHLAX --station KLAX
kalshi-weather poc-demo --station KLAX
kalshi-weather poc-check --series KXHIGHLAX --station KLAX
```

Outcome and calibration flow:

```powershell
kalshi-weather fetch-missing-outcomes --station KLAX
kalshi-weather record-outcome --station KLAX --date YYYY-MM-DD --official-high-f NN --source manual --allow-unsettled-store
kalshi-weather join-outcomes --station KLAX --overwrite
kalshi-weather calibration-report --station KLAX
kalshi-weather residual-report --station KLAX
```

`poc-demo` uses fixture data only and is labeled `DEMO DATA - NOT TRADING EVIDENCE`.
It proves plumbing, not market edge. A real edge claim requires settled official outcomes,
joined predictions, calibration/replay evidence, and a much larger production sample.

## Operational Validation And Model Health

The current goal is daily proof-of-edge testing, not live trading and not a
more complex model.

Primary health command:

```powershell
kalshi-weather model-health --series KXHIGHLAX --station KLAX
```

It reports data readiness, residuals, calibration, model-vs-market benchmark,
paper status, automation status, safety status, and one recommended next
command. If official outcomes or joined rows are missing, it will say that
plainly.

Compare model probabilities to Kalshi market-implied probabilities:

```powershell
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
```

Check whether enough settled evidence exists:

```powershell
kalshi-weather calibration-readiness --station KLAX
```

Daily collection:

```powershell
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
```

After settlement:

```powershell
kalshi-weather fetch-missing-outcomes --station KLAX
kalshi-weather join-outcomes --station KLAX --overwrite
kalshi-weather calibration-report --station KLAX
kalshi-weather residual-report --station KLAX
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
kalshi-weather model-health --series KXHIGHLAX --station KLAX
```

Windows automation wrappers live in `scripts/`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_collect_session_lax.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_after_settlement_lax.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_model_health_lax.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_tasks_lax.ps1 -WhatIf
```

Logs are written under `logs/automation/`. To remove scheduled tasks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_windows_tasks_lax.ps1 -WhatIf
```

For plain-English interpretation, read `docs/HOW_TO_READ_RESULTS.md`.

Live trading remains disabled. There is still no Kalshi create-order endpoint
or authenticated order placement in this package.

## Separate Model Estimate Comparison

The current production model is unchanged. The comparison layer is diagnostic
only: it shows the current weighted blend, individual Open-Meteo feeds, and
optional direct NOAA/Herbie model estimates side by side.

```powershell
kalshi-weather model-provider-probe --station KLAX
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --include-probabilities --json --output reports/latest_model_estimates.json
kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX --json --output reports/latest_model_probabilities.json
kalshi-weather model-estimate-score --station KLAX
```

To store comparison rows without changing production predictions:

```powershell
kalshi-weather collect-once --series KXHIGHLAX --station KLAX --include-model-estimates
```

Direct NOAA/Herbie models are optional. If dependencies are missing, the
commands still pass and mark HRRR/NBM/GFS/RAP as unavailable:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_direct_noaa_models.ps1
kalshi-weather direct-noaa-check --station KLAX
```

Direct NOAA model locations:

- HRRR: Herbie model `hrrr`, product `sfc`, field `TMP at 2 m`, same-day short-range.
- NBM: Herbie model `nbm`, product `co`, field `TMP at 2 m`, calibrated blend baseline.
- GFS: Herbie model `gfs`, product `pgrb2.0p25`, field `TMP at 2 m`, global baseline.
- RAP: Herbie model `rap`, product `awp130pgrb`, field `TMP at 2 m`, short-range regional backup.

Read `docs/MODEL_ESTIMATE_COMPARISON.md` for interpretation details. These
comparison estimates are not blended into trading or paper entries by default.

## Fake-Money Model Race

The model race is a separate paper-only experiment. Each model gets its own
independent $100 fake-money account and trades only from its own probability
estimate. The goal is to compare which model would have made better fake
microtrading decisions, not to change the production blend.

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --interval-seconds 900 --max-iterations 2
kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-reset --race-id default --confirm
```

The default loop interval is 900 seconds, or 15 minutes, because direct NOAA
models are slower than Open-Meteo. Reports are written to
`reports/model_race/`, including `latest_model_race.txt`,
`latest_model_race.json`, `model_race_leaderboard.csv`, and
`model_race_trades.csv`.

Live trading remains disabled. The model race does not place real Kalshi orders
and does not blend comparison models into production logic. Read
`docs/PAPER_MODEL_RACE.md` for the plain-English interpretation guide.
## Safer Model Race Cadence

The fake-money model race now uses a safer microtrade cadence: monitor exits often, but open new trades less often. Use a daily/session race ID so interrupted runs are easy to inspect and flatten:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax --entry-interval-seconds 900 --exit-interval-seconds 60 --starting-cash-per-model 100
```

Fast exit-only monitoring is available without refreshing slow direct NOAA/Herbie models:

```powershell
kalshi-weather paper-model-race-exit-monitor --series KXHIGHLAX --station KLAX --race-id 20260623_lax --interval-seconds 60
```

If a run is interrupted with Ctrl+C, flatten open fake positions at executable bids:

```powershell
kalshi-weather paper-model-race-flatten --series KXHIGHLAX --station KLAX --race-id 20260623_lax --confirm
```

Open P/L is only trusted when an exit bid exists. If a position has no bid, compact output shows `open P/L n/a | no exit bid`; closed P/L remains locked-in fake realized profit/loss. Live trading remains disabled.
## Independent Model Race Mode

The default fake-money model race mode is now `independent`. Each model gets its own fake `$100` account and trades its own signals. Model disagreement is shown as a diagnostic, but it does not stop every model from trading by default.

Independent mode is for model discovery:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax --starting-cash-per-model 100 --race-mode independent --entry-interval-seconds 900 --exit-interval-seconds 60
```

Use model-worker mode when slow providers should not hold up faster models. Each model refresh runs as its own worker; when that model finishes, only that model is evaluated for a fake-money entry. Exit checks still run on the faster exit interval.

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax_workers --starting-cash-per-model 100 --race-mode independent --model-worker-mode --model-worker-count 4 --entry-interval-seconds 300 --exit-interval-seconds 60
```

Consensus-guarded mode is available for later risk-managed strategy testing:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax_guarded --starting-cash-per-model 100 --race-mode consensus_guarded
```

Execution filters still apply in both modes: no ask, no exit bid, wide contract spread, penny/no-liquidity contracts, cooldowns, high price, stale models, and exposure/cash limits.
