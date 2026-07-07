# CLI reference target

Codex should wire these commands.

## Help

```powershell
kalshi-weather --help
```

## LLM Trade Advisor

```powershell
kalshi-weather advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch
kalshi-weather llm-advisor-smoke-test --rule-only
kalshi-weather llm-advisor-smoke-test --provider ollama --model gpt-oss:120b
kalshi-weather advisor-dry-run --series KXHIGHLAX --station KLAX --advisor-mode rule_based
kalshi-weather advisor-decision-report --race-id advisor_smoke
kalshi-weather advisor-export-training-examples --race-id advisor_smoke --output-dir reports/llm_trade_advisor/training_examples
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --use-llm-advisor --llm-provider ollama --llm-model gpt-oss:120b
```

Expected output:

```text
Fake-money advisor recommendations, trade-quality scores, validator vetoes, and export paths.
```

The advisor never places live orders. Use `--advisor-mode rule_based` on `paper-model-race-once` or `paper-model-race-run` to enable confirmed-edge fake-money gating. Use `--use-llm-advisor --llm-provider ollama --llm-model gpt-oss:120b` for the Ollama GPT-OSS path, `--llm-rule-only` for no-network deterministic checks, and `--llm-dry-run` to log LLM decisions without changing fake race actions.

## Markets

```powershell
kalshi-weather markets --series KXHIGHLAX
```

Expected output:

```text
Ticker, title, subtitle/bracket, yes bid, no bid, implied yes ask, implied no ask
```

## Weather snapshot

```powershell
kalshi-weather weather-snapshot --station KLAX
```

Expected output:

```text
Observed high so far, latest obs time, model maxes, blended future high
```

## Prediction once

```powershell
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
```

Expected output:

```text
Each bracket ticker, market price, model probability, edge
```

## Paper once

```powershell
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
```

Expected output:

```text
Signals and fake fills, if any.
```

## Run paper

```powershell
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
```

Expected output:

```text
Continuous loop with snapshots and paper ledger.
```

## Phase 4-7 POC Commands

```powershell
kalshi-weather research-status --series KXHIGHLAX --station KLAX
kalshi-weather daily-maintenance --series KXHIGHLAX --station KLAX
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
kalshi-weather opportunities --series KXHIGHLAX --station KLAX --short
kalshi-weather threshold-sweep --series KXHIGHLAX --station KLAX
kalshi-weather calibration-readiness --station KLAX
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
kalshi-weather model-health --series KXHIGHLAX --station KLAX
kalshi-weather validation-run --series KXHIGHLAX --station KLAX --after-settlement
kalshi-weather calibration-demo --station KLAX
kalshi-weather tune-residual-sigma --station KLAX
kalshi-weather fit-probability-calibration --station KLAX --dry-run
kalshi-weather model-weight-report --station KLAX
kalshi-weather replay-predictions --station KLAX
kalshi-weather paper-replay --series KXHIGHLAX --station KLAX
kalshi-weather poc-run --series KXHIGHLAX --station KLAX --max-iterations 3
kalshi-weather poc-demo --station KLAX
kalshi-weather poc-check --series KXHIGHLAX --station KLAX
```

These commands remain read-only or fake-money-only. `poc-demo` is fixture data
and not trading evidence.

## Simple Analysis Output

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX
kalshi-weather model-summary --series KXHIGHLAX --station KLAX
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --json --output reports/latest_simple_summary.json
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --csv --output reports/latest_simple_summary.csv
kalshi-weather weather-summary --station KLAX
```

`simple-summary` is analysis-only. It prints the current production estimate,
comparison model high estimates, one probability row per model, agreement
status, data-readiness warnings, and the next recommended action. It does not
trade and does not create fake fills.

Market prices and edge columns are hidden unless `--show-prices` or
`--show-edges` is passed. JSON and CSV outputs are clean report formats, not raw
debug dumps.

`collect-session` default output is now concise. Use `--verbose` for the older
detailed object output and `--debug-json` for the full nested debug payload.

## Kalshi History And Charts

```powershell
kalshi-weather kalshi-history-discover --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD
kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date YYYY-MM-DD --end-date YYYY-MM-DD --period-interval 1 --store
kalshi-weather kalshi-trends --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing
kalshi-weather kalshi-trend-chart --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing --output-dir reports/kalshi_trends
kalshi-weather temperature-estimate-chart --station KLAX --date YYYY-MM-DD --output-dir reports/temperature_estimates
kalshi-weather microtrade-trend-replay --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --chart
```

These commands are read-only analysis commands, except that
`kalshi-history-backfill --store` writes normalized candlestick rows to SQLite
and chart/dashboard commands write report artifacts. They do not place orders
or create paper fills.

Use `docs/KALSHI_HISTORY_AND_CHARTS.md` for chart interpretation.

## Operational Validation Commands

```powershell
kalshi-weather model-health --series KXHIGHLAX --station KLAX
kalshi-weather model-health --series KXHIGHLAX --station KLAX --json --output reports/latest_model_health.json
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
kalshi-weather calibration-readiness --station KLAX
kalshi-weather daily-maintenance --series KXHIGHLAX --station KLAX --skip-collect
```

`model-health` is the first command to read when the raw reports are confusing.
It is a report-only command and never trades.

## Separate Model Estimate Comparison Commands

```powershell
kalshi-weather model-provider-probe --station KLAX
kalshi-weather direct-noaa-check --station KLAX
kalshi-weather direct-noaa-check --station KLAX --json --output reports/latest_direct_noaa_check.json
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --include-probabilities --json --output reports/latest_model_estimates.json
kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX --json --output reports/latest_model_probabilities.json
kalshi-weather model-estimate-score --station KLAX
kalshi-weather collect-once --series KXHIGHLAX --station KLAX --include-model-estimates
```

These commands are comparison-only. They store optional sidecar rows in
`model_estimates` and `model_estimate_probabilities` when `--store` or
`--include-model-estimates` is passed. They do not alter the production
`model_predictions` logic and they never place trades.

`direct-noaa-check` diagnoses optional Herbie-backed HRRR/NBM/GFS/RAP access.
The direct NOAA targets are HRRR `hrrr/sfc`, NBM `nbm/co`, GFS
`gfs/pgrb2.0p25`, and RAP `rap/awp130pgrb`, all using `TMP at 2 m`.

`temperature-estimate-chart` creates
`actual_vs_model_temperatures.png`, a direct plot of actual NWS observation
temperatures, observed high so far, the production high-temperature estimate,
per-model Open-Meteo estimate lines when stored, and the official high when
available. Use `--no-fetch-actual` to make an offline chart from stored
snapshots only.

## Paper Model Race Commands

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --json --output reports/model_race/latest_model_race.json
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --interval-seconds 900 --max-iterations 2
kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX
kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX --csv --output reports/model_race/model_race_leaderboard.csv
kalshi-weather paper-model-race-reset --race-id default --confirm
```

These commands are fake-money only. They create separate $100 paper accounts
for the current blend, Open-Meteo models, and direct NOAA/Herbie models. They
write reports under `reports/model_race/` and never place live Kalshi orders.

The default race interval is 900 seconds because direct NOAA model reads can be
slow. If a model is unavailable, the race marks that model unavailable and keeps
the other model accounts moving.
## Safer Model Race Commands

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax --entry-interval-seconds 900 --exit-interval-seconds 60 --starting-cash-per-model 100
```

Runs the fake-money model race with slow model refresh/new-entry checks and fast exit monitoring. `--interval-seconds` remains supported and is treated as the entry interval.

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax_workers --race-mode independent --model-worker-mode --model-worker-count 4 --entry-interval-seconds 300 --exit-interval-seconds 60 --starting-cash-per-model 100
```

Runs model refreshes independently so faster models can evaluate fake entries without waiting for slower providers.

```powershell
kalshi-weather paper-model-race-exit-monitor --series KXHIGHLAX --station KLAX --race-id 20260623_lax --interval-seconds 60
```

Manages existing fake positions without refreshing slow direct NOAA/Herbie models.

```powershell
kalshi-weather paper-model-race-flatten --series KXHIGHLAX --station KLAX --race-id 20260623_lax --confirm
```

Attempts to flatten open fake positions at available bids. Missing-bid positions are not fake-sold unless `--synthetic-zero-exit` is explicitly passed.
### Model Race Modes

Independent model discovery:

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id 20260623_lax --starting-cash-per-model 100 --race-mode independent
```

Consensus-guarded strategy test:

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id 20260623_lax_guarded --starting-cash-per-model 100 --race-mode consensus_guarded
```

`independent` is the default. It does not block all entries because of global model spread or outlier diagnostics. `consensus_guarded` enables global model-agreement guards.
## Synthetic Edge-Case Commands

Build the built-in offline synthetic Kalshi-like scenarios:

```powershell
kalshi-weather synthetic-scenarios-build --scenario-set model_race_edge_cases --output-dir data/synthetic_scenarios/model_race_edge_cases --overwrite
```

List scenarios:

```powershell
kalshi-weather synthetic-scenarios-list --scenario-dir data/synthetic_scenarios/model_race_edge_cases
kalshi-weather synthetic-scenarios-list --json
```

Run one scenario:

```powershell
kalshi-weather synthetic-scenario-run --scenario-id clear_yes_profit_target --charts --fail-on-mismatch
```

Run the full offline edge-case suite:

```powershell
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

Synthetic commands are local-only. They do not call Kalshi APIs, do not require
API keys, and do not place live or paper trades outside their own fake local
scenario state.
