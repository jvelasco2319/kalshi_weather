# kalshi-weather

Local paper-trading research package for Kalshi LA high-temperature markets.

The current target is `KXHIGHLAX`, using KLAX/LAX observations and Open-Meteo
forecast guidance to estimate probabilities for Kalshi temperature brackets.
All execution is fake-money simulation. There is no live Kalshi order placement
code in this package.

## Windows Quick Start

Requirements: Git and Python 3.11 or newer.

```powershell
git clone https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_windows.ps1
.\.venv\Scripts\Activate.ps1
```

The bootstrap installs the CLI, dashboard, NAM/NBM Herbie stack, and development
tools. It also creates `.env` plus the local `data`, cache, journal, log, and
report directories. Update `.env` with a descriptive `NWS_USER_AGENT` before
daily use.

For a runtime-only install, add `-WithoutDevTools` to the bootstrap command.

Manual setup is also supported:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,full]"
copy .env.example .env
python -m kalshi_weather.cli init-runtime
python -m pytest tests/test_portability.py tests/test_cli.py tests/test_runtime_paths.py tests/test_signal_room_api.py
python -m ruff check .
```

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
kalshi-weather record-weather-market-loop --target-date auto --interval-seconds 900 --journal-path journals/lax_model_validation.sqlite --jsonl-path journals/lax_model_validation.jsonl --models ecmwf_ifs,gfs013,gfs_seamless,nam,nbm
kalshi-weather strategy-dashboard --mode live --event auto --port 8765
```

The module form also works after installation:

```powershell
python -m kalshi_weather.cli --help
```

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

## Safety

This package is paper-trading only.

- `KALSHI_ENABLE_REAL_ORDERS=false` by default.
- No create-order endpoint is implemented.
- No API keys are needed for current market-data commands.
- `.env`, private keys, SQLite files, and snapshots are ignored by git.

## Runtime Data

A clone contains empty runtime directory markers, but never another user's
databases, journals, logs, forecasts, or credentials. `bootstrap_windows.ps1`
and `kalshi-weather init-runtime` are both safe to rerun if these directories
are deleted. The application creates parent directories again before writing.

## Known Limitations

- Some configured Open-Meteo model identifiers may be rejected by the selected endpoint. Phase 2 requests each model separately, records successes/failures, and uses the generic fallback only when every model-specific request fails.
- The v0.2 forecast model is intentionally simple: blended future high plus global normal residual.
- Calibration reports need stored official outcomes before metrics are meaningful.
- Paper state resume/reset is implemented; paper reports still leave hold-time and mark-to-market P&L unavailable until more fill/quote history is captured.

## Phase 2 Notes

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

The zip intentionally excludes `.env`, `.git`, `.venv`, generated runtime
data, SQLite files, caches, snapshots, and key material. Run
`scripts\bootstrap_windows.ps1` after extracting it.

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
