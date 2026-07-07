# Kalshi Weather

Fake-money research and monitoring tools for Kalshi Los Angeles high-temperature
markets.

The main workflow estimates the daily high temperature at KLAX/LAX, compares the
model view with Kalshi `KXHIGHLAX` temperature brackets, and simulates
rules-based paper trading. The project also includes a model tournament dashboard
that tracks how each weather model would have performed over time.

This repo is intentionally fake-money-only. It does not place real Kalshi orders.

## What This Project Does

- Fetches public Kalshi market/orderbook data for `KXHIGHLAX`.
- Fetches KLAX/LAX observations and high-so-far temperature.
- Fetches Open-Meteo model estimates and optional direct NOAA/Herbie models.
- Builds bracket probabilities for canonical temperature brackets.
- Runs a rules-based paper trader with conservative simulated fills.
- Saves debug files for review: `latest.json`, `decisions.jsonl`,
  `candidates.csv`, `diagnostic.sqlite`, `terminal_output.txt`, and ZIP packages.
- Runs a model tournament dashboard to compare model estimates and fake results.

Canonical bracket labels are normalized to plain ranges such as `70-71`,
`72-73`, `<66`, and `>76`.

## Safety

This package is for local fake-money research.

- Real trading is disabled by default.
- `KALSHI_ENABLE_REAL_ORDERS=false` should stay false.
- No real order-placement workflow is required for the current runs.
- `.env`, runtime data, reports, logs, journals, SQLite files, and ZIP packages
  are git-ignored.

Do not put private Kalshi keys or secrets in GitHub.

## Requirements

- Windows PowerShell 5.1 or newer.
- Python 3.11 or newer.
- Git.
- Internet access for Kalshi, NWS, Open-Meteo, and optional NOAA model data.

Optional but useful:

- Conda or mamba for `eccodes`/`cfgrib` if you want direct NOAA/Herbie models.

## Install

Clone and enter the repo:

```powershell
git clone https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
```

Create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install the package and test tools:

```powershell
python -m pip install -e ".[dev]"
```

Copy the environment template:

```powershell
copy .env.example .env
```

Edit `.env` and set a descriptive NWS user agent:

```text
NWS_USER_AGENT=kalshi-weather-research/0.1 your_email@example.com
KALSHI_ENABLE_REAL_ORDERS=false
```

Verify the install:

```powershell
kalshi-weather --help
python -m pytest -q
```

If PowerShell blocks local scripts, allow scripts for the current shell only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Optional NOAA / Herbie Setup

Open-Meteo/current blend models work without NOAA/Herbie. Direct NOAA models are
slower and need extra dependencies.

Try the project installer:

```powershell
.\scripts\install_direct_noaa_models.ps1
```

If `eccodes` or `cfgrib` fails on Windows, conda is often easier:

```powershell
conda install -c conda-forge eccodes cfgrib
python -m pip install herbie-data xarray
```

You can still run the bot with NOAA disabled if this setup is incomplete.

## Current Recommended Run

Use the market-cycle supervisor, not an infinite raw `trader-paper-run` loop.
The supervisor repeatedly checks the active market, runs one controlled
paper-trading cycle, saves canonical files, and creates ZIP packages.

Run the next market using the current aggressive fake-money test settings:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather

.\scripts\run_lax_market_cycle_forever.ps1 `
  -Tomorrow `
  -AggressiveAllDay `
  -NoaaAfterNoon `
  -FastModelRefreshSeconds 60 `
  -NoaaModelRefreshSeconds 900 `
  -ObservationRefreshSeconds 300 `
  -MinEdgeCents 2 `
  -MinNoEdgeCents 2 `
  -MinNoUpsideCents 2 `
  -MaxNoBinProbability 0.40 `
  -PackageEveryMinutes 60 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

For a specific target date:

```powershell
.\scripts\run_lax_market_cycle_forever.ps1 `
  -TargetDate "YYYY-MM-DD" `
  -AggressiveAllDay `
  -NoaaAfterNoon `
  -FastModelRefreshSeconds 60 `
  -NoaaModelRefreshSeconds 900 `
  -ObservationRefreshSeconds 300 `
  -MinEdgeCents 2 `
  -MinNoEdgeCents 2 `
  -MinNoUpsideCents 2 `
  -MaxNoBinProbability 0.40 `
  -PackageEveryMinutes 60 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

Stop the run with `Ctrl+C`.

### Run Variants

Open-Meteo/current models only:

```powershell
.\scripts\run_lax_market_cycle_forever.ps1 `
  -Tomorrow `
  -AggressiveAllDay `
  -NoaaOff `
  -FastModelRefreshSeconds 60 `
  -MinEdgeCents 2 `
  -MinNoEdgeCents 2 `
  -MinNoUpsideCents 2 `
  -MaxNoBinProbability 0.40 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

NOAA/Herbie active all day:

```powershell
.\scripts\run_lax_market_cycle_forever.ps1 `
  -Tomorrow `
  -AggressiveAllDay `
  -NoaaModelRefreshSeconds 900 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

All configured weather models:

```powershell
.\scripts\run_lax_market_cycle_forever.ps1 `
  -Tomorrow `
  -AggressiveAllDay `
  -AllWeatherModels `
  -NoaaModelRefreshSeconds 900 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

More model-trusting / less market-prior behavior:

```powershell
.\scripts\run_lax_market_cycle_forever.ps1 `
  -Tomorrow `
  -AggressiveAllDay `
  -ModelAuthoritative `
  -NoaaAfterNoon `
  -MinEdgeCents 1 `
  -MinNoEdgeCents 1 `
  -MinNoUpsideCents 1 `
  -MaxNoBinProbability 0.60 `
  -ShowModelEstimates `
  -SnapshotStyle table
```

## What The Main Run Saves

Run folders are written under:

```text
reports\trader_agent\debug\<run_id>\
```

Important files:

- `latest.json` - latest full decision/context snapshot.
- `decisions.jsonl` - one decision record per cycle.
- `candidates.csv` - candidate trade audit table.
- `diagnostic.sqlite` - local SQLite journal.
- `terminal_output.txt` - captured terminal output.
- `final_results.json` - end/run summary when available.
- `bot_trust_report.json` - safety and model-trust summary when available.
- `effective_config.json` - effective run configuration when available.

ZIP packages are written under:

```text
reports\trader_agent\archives\
```

The supervisor packages the latest run every hour by default. Change that with:

```powershell
-PackageEveryMinutes 30
```

Manually package the latest run:

```powershell
.\scripts\package_debug_run.ps1 -Latest
```

## Model Tournament Dashboard

The model tournament compares model-specific fake bets and model estimates over
time. It is separate from the main paper trader.

Run the tournament for today and open a local dashboard:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather

.\scripts\run_lax_model_tournament_forever.ps1 `
  -TargetDate "YYYY-MM-DD" `
  -ShowDashboard `
  -DashboardPort 8766 `
  -IntervalSeconds 60
```

Then open:

```text
http://127.0.0.1:8766/dashboard.html
```

The dashboard shows:

- model estimates over time in PT,
- observed KLAX exact temperature and high-so-far,
- top temperature bracket highlights,
- open/closed fake model positions,
- signed P/L by row,
- reason text explaining why open rows have not closed.

The dashboard is read-only. Stake and target percentage are not editable.

## Useful CLI Commands

Inspect model estimates:

```powershell
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
```

Run one market lifecycle check:

```powershell
kalshi-weather trader-market-cycle `
  --series KXHIGHLAX `
  --station KLAX `
  --tomorrow `
  --cycle-mode once `
  --decision-mode rules `
  --strategy hybrid `
  --order-style passive `
  --paper-fill-price-mode conservative `
  --profile-mode auto `
  --use-canonical-paths `
  --use-cached-models `
  --noaa-model-mode scheduled `
  --noaa-model-refresh-seconds 900 `
  --show-snapshot changed `
  --snapshot-style compact
```

Audit a paper portfolio:

```powershell
kalshi-weather trader-portfolio --race-id <run_id> --journal-path <path-to-diagnostic.sqlite>
```

Settle/finalize a paper run after the official high is known:

```powershell
kalshi-weather trader-settle-paper-run --run-id <run_id> --journal-path <path-to-diagnostic.sqlite>
```

Open a model tournament dashboard for an existing run:

```powershell
kalshi-weather model-tournament-dashboard --run-id <run_id> --port 8766
```

## Main Parameters

- `-AggressiveAllDay`: uses a fixed aggressive profile instead of time-of-day
  risk profiles.
- `-NoaaOff`: disables direct NOAA/Herbie model fetches.
- `-NoaaAfterNoon`: uses Open-Meteo/current models before noon PT, then enables
  NOAA/Herbie models after noon.
- `-AllWeatherModels`: enables a broader model list. This can be slower and may
  produce more model disagreement.
- `-FastModelRefreshSeconds`: cadence for fast/current/Open-Meteo model refresh.
- `-NoaaModelRefreshSeconds`: cadence for slower NOAA/Herbie refresh.
- `-ObservationRefreshSeconds`: cadence for station observation refresh.
- `-MinEdgeCents`: minimum edge for general/YES candidates.
- `-MinNoEdgeCents`: minimum edge for NO candidates.
- `-MinNoUpsideCents`: minimum upside required for NO candidates.
- `-MaxNoBinProbability`: maximum model probability allowed for buying NO on a
  bracket. Higher values make NO trades easier to allow.
- `-ModelAuthoritative`: puts more trust in model probabilities and less in the
  market-implied prior.
- `-ShowModelEstimates`: prints model/market snapshots every cycle in table form.

## Troubleshooting

If `.\scripts\...` is not recognized, make sure you are in the repo root:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather
```

If Open-Meteo or NWS fails with a connection error, the bot will usually retry on
the next cycle. Check your internet connection and keep the laptop awake.

If NOAA/Herbie is slow, that is expected. Use scheduled NOAA refreshes:

```powershell
-NoaaModelRefreshSeconds 900
```

If NOAA dependencies are broken, run Open-Meteo only:

```powershell
-NoaaOff
```

If the dashboard looks stale, restart the dashboard server or hard refresh the
browser.

## Tests

Run the full test suite:

```powershell
python -m pytest -q
```

Run focused tests:

```powershell
python -m pytest -q tests\test_model_tournament.py
python -m pytest -q tests\test_model_refresh_modes.py
```

Lint:

```powershell
python -m ruff check .
```

## Notes For Contributors

- Keep the default system fake-money-only.
- Do not add real order placement unless the safety model is explicitly reviewed.
- Prefer the market-cycle supervisor for long runs.
- Keep large runtime artifacts out of git; use ZIP packages from
  `reports\trader_agent\archives\` for review.
- When changing trading behavior, add tests for risk limits, fake fills,
  settlement/finalization, and report packaging.
