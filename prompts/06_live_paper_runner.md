# Prompt 06 — Live paper runner

Implement the continuous fake-money loop.

## Files to implement/update

```text
src/kalshi_weather/trading/runner.py
src/kalshi_weather/data/storage.py
src/kalshi_weather/cli.py
scripts/run_paper_lax_high.ps1
scripts/run_paper_lax_high.sh
```

## Tasks

1. Implement a loop:

```text
fetch markets/orderbooks
fetch weather snapshot
compute probabilities
compute signals
paper trade if risk allows
persist everything
print dashboard row
sleep interval
```

2. Add graceful Ctrl+C handling.
3. Add robust external API error handling:
   - log error
   - skip trade
   - continue next interval
4. Add JSON snapshot writing to `data/snapshots`.
5. Wire CLI:

```powershell
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
```

## Acceptance criteria

```powershell
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
```

Let it run for 3 loops.

Expected:

```text
No crash.
No real order.
SQLite file created.
Snapshot files created.
Dashboard displays current fake cash/positions.
```
