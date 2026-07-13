# KLAX Live Stack Operator Runbook

## Current Working Path

The currently wired path uses the existing recorder loop as the live data
collector and the Signal Room as a read-only viewer.

Start the recorder for July 12, 2026:

```powershell
python -m kalshi_weather.cli record-weather-market-loop --target-date 2026-07-12 --interval-seconds 900 --journal-path journals/lax_model_validation.sqlite --jsonl-path journals/lax_model_validation.jsonl
```

Start the dashboard in a second terminal:

```powershell
$env:PYTHONPATH='src'; python -m kalshi_weather.cli strategy-dashboard --host 127.0.0.1 --port 8765 --target-date 2026-07-12 --sqlite-path journals/lax_model_validation.sqlite
```

Open:

```text
http://127.0.0.1:8765/
```

## What You Should See

- Five fixed strategy model slots.
- Recorder-provided estimates for available canonical models.
- Missing/invalid status for unavailable canonical models.
- KLAX observed high when the recorder has observations.
- Kalshi market rows and visible bid/ask strings when recorder capture succeeds.
- `DATA INCOMPLETE` with `NO_TRADE_PROBABILITY_UNCALIBRATED`.

## Why The Decision Is Blocked

The validation recorder provides live display data, but it does not yet persist
the calibrated current-strategy probability/economics outputs required to emit a
shadow trade candidate. The dashboard therefore shows live values and blocks all
candidates.

Common blocking codes:

- `NO_TRADE_PROBABILITY_UNCALIBRATED`: no persisted calibrated probability/economics.
- `NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE`: recorder has observe-only REST/top-of-book data, not a sequence-valid executable depth book.
- `NO_TRADE_TOO_FEW_MODELS`: fewer than four canonical current-strategy model estimates are healthy.

## Safety

The dashboard is GET-only, binds to `127.0.0.1` by default, and imports no order
submission client. The strategy flags remain:

```text
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
order_submission_reachable = false
```

## Shutdown

Press `Ctrl+C` in each PowerShell window.
