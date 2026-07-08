# Kalshi Weather

Local fake-money tools for watching Kalshi Los Angeles high-temperature markets.

The easiest thing to run is the model tournament dashboard. It tracks the current
LA high-temperature market and the next market at the same time, then shows both
in one browser page with tabs.

This project is fake-money-only by default. It does not place real Kalshi orders.

## What You Get

- KLAX/LAX temperature observations.
- Weather model high-temperature estimates.
- Kalshi `KXHIGHLAX` temperature bracket prices.
- A local HTML dashboard for current and next-day markets.
- Fake model-tournament results and P/L for research.

## Requirements

- Windows PowerShell 5.1 or newer.
- Python 3.11 or newer.
- Git.
- Internet access.

Optional for direct NOAA/Herbie models:

- Conda or mamba.
- `eccodes`, `cfgrib`, `xarray`, and `herbie-data`.

Open-Meteo models work without the optional NOAA setup.

## Install

Open PowerShell and run:

```powershell
git clone https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks local scripts, run this once in the same PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Create your local environment file:

```powershell
copy .env.example .env
```

Then open `.env` and keep real trading disabled:

```text
KALSHI_ENABLE_REAL_ORDERS=false
```

It is also helpful to set a user agent for weather APIs:

```text
NWS_USER_AGENT=kalshi-weather-research/0.1 your_email@example.com
```

Verify the install:

```powershell
kalshi-weather --help
```

## Optional NOAA / Herbie Setup

You can skip this at first. The dashboard can run with Open-Meteo models.

To try direct NOAA/Herbie support:

```powershell
.\scripts\install_direct_noaa_models.ps1
```

If that fails on Windows, conda is usually easier:

```powershell
conda install -c conda-forge eccodes cfgrib
python -m pip install herbie-data xarray
```

## Run Two Markets With Tabs

This is the main easy-use command. It starts:

- one dashboard for today's LA high-temperature market,
- one dashboard for tomorrow's LA high-temperature market,
- one tabbed HTML page that shows both.

From the repo folder:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather

.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -CurrentDashboardPort 8766 `
  -NextDashboardPort 8767 `
  -TabbedDashboardPort 8768 `
  -IntervalSeconds 60 `
  -CheckEverySeconds 60
```

Open this in your browser:

```text
http://127.0.0.1:8768/lax_model_tournament_tabs.html
```

The tabbed page updates automatically. When the local date rolls forward, the
script starts the new target date and the HTML page adds/updates tabs from its
manifest.

Leave the PowerShell window open while you want the dashboard running.

Stop it with:

```text
Ctrl+C
```

## Direct Dashboard URLs

If you want to open the individual dashboards directly:

```text
http://127.0.0.1:8766/dashboard.html
http://127.0.0.1:8767/dashboard.html
```

The combined tab page is usually easier:

```text
http://127.0.0.1:8768/lax_model_tournament_tabs.html
```

## Useful Run Options

Run only today and tomorrow, without automatic date rolling:

```powershell
.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -NoAutoRollDates `
  -CurrentTargetDate "2026-07-07" `
  -NextTargetDate "2026-07-08"
```

Keep yesterday visible too:

```powershell
.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -RetainPastDays 1
```

Run more future days:

```powershell
.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -DaysAhead 2
```

Turn off cached model values and recompute every loop:

```powershell
.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -NoCachedModels `
  -ForceModelRecomputeEveryIteration
```

Use the slower direct NOAA/Herbie mode every loop:

```powershell
.\scripts\run_lax_model_tournament_two_markets.ps1 `
  -NoaaModelMode full_recompute_each_iteration
```

## Where Files Are Saved

Dashboard tab files:

```text
reports\trader_agent\dashboard_tabs\
```

Model tournament run files:

```text
reports\trader_agent\debug\<run_id>\
```

Common files:

- `terminal_output.txt`
- `dashboard.html`
- model tournament JSON/SQLite outputs
- generated report/debug files

Runtime reports and ZIP packages are ignored by git.

## Troubleshooting

If `.\scripts\...` is not recognized, you are probably not in the repo folder:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather
```

If the dashboard does not load:

1. Make sure the PowerShell script is still running.
2. Open `http://127.0.0.1:8768/lax_model_tournament_tabs.html`.
3. Try the direct ports: `8766` and `8767`.
4. Hard refresh the browser.

If weather downloads fail:

- Check your internet connection.
- Let the script retry on the next loop.
- Try Open-Meteo/current models first before setting up NOAA/Herbie.

If your laptop sleeps or loses Wi-Fi:

- Set Windows sleep to `Never` while plugged in.
- Keep the lid open or change lid-close behavior.
- Keep PowerShell running.

## Safety Notes

- This repo is for fake-money research.
- Keep `KALSHI_ENABLE_REAL_ORDERS=false`.
- Do not commit `.env`, API keys, runtime data, SQLite files, logs, or ZIPs.
- The model tournament dashboard is read-only and local to your machine.

## Developer Commands

Run tests:

```powershell
python -m pytest -q
```

Run lint:

```powershell
python -m ruff check .
```

Inspect the CLI:

```powershell
kalshi-weather --help
```
