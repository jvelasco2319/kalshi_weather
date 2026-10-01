# KLAX Swarm Setup — Online Current-Data Test

This branch runs the same three retained research methods against the currently open Kalshi Los Angeles high-temperature market:

1. **V5B** — current HRRR/GEFS bracket probabilities plus the strict NO-side trade filter.
2. **V8** — the frozen six-bracket probability repair.
3. **V10** — V8 plus the frozen KLAX-versus-KDAG pressure-flow adjustment.

It fetches current public data and writes paper recommendations. **It cannot place orders:** the package contains no account credentials, order endpoint, or order-writing function.

## 1. Clone this branch

```powershell
git clone --branch swarm_setup_online --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
```

Requirements: Windows, Git, Python 3.11 or newer, and internet access.

## 2. Set it up

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_online.ps1
```

The setup creates `.venv`, installs the small dependency set, verifies the three frozen configurations, runs the tests, and takes one current snapshot.

## 3. Run one current snapshot

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_online.ps1
```

The dashboard opens automatically at `artifacts\online\dashboard.html`.

To test a specific currently open date:

```powershell
.\scripts\run_online.ps1 -TargetDate '2026-10-02'
```

## 4. Keep the dashboard updating

```powershell
.\scripts\run_online.ps1 -Loop -IntervalSeconds 60
```

Leave the PowerShell window open. Stop it with `Ctrl+C`.

## What the graphic shows

- V5B, V8, and V10 action or abstention
- selected NO contract, model win probability, and fee-adjusted expected return
- HRRR high, GEFS ensemble mean/range, and their disagreement
- KLAX-minus-KDAG pressure gradient and V10 regime
- all six bracket probabilities and Kalshi top-of-book prices
- quote timestamps, the frozen 18:00 UTC test window, and the zero-order safety state

Raw snapshots are appended to `artifacts\online\journal.jsonl`; the latest machine-readable result is `artifacts\online\latest.json`.

## Fair-test rule

The runner works at any time, but only snapshots within five minutes of **18:00 UTC** are tagged as on-time tests comparable to the historical research. Other runs are observation-only.

Current HRRR/GEFS probabilities use a documented transfer approximation from public Open-Meteo feeds. V8 and V10 use the exact frozen transforms. Do not interpret results as an untouched confirmation until a new, preregistered sample is settled.

The historical-only version is the `swarm_setup_offline` branch. A setup prompt for another ChatGPT/Codex session is in [docs/CHATGPT_SETUP_PROMPT.md](docs/CHATGPT_SETUP_PROMPT.md).

