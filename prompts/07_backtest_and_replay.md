# Prompt 07 — Backtest and replay

Implement replay of stored snapshots.

## Files to implement/update

```text
src/kalshi_weather/backtest/replay.py
src/kalshi_weather/backtest/metrics.py
src/kalshi_weather/cli.py
tests/test_backtest_metrics.py
```

## Tasks

1. Replay saved snapshots in timestamp order.
2. Recompute model probabilities from stored data, not live APIs.
3. Re-run paper signals against stored orderbooks.
4. Output metrics:
   - number of simulated trades
   - gross P&L
   - net P&L after buffers/fees
   - max drawdown
   - win rate on closed flips
   - average hold time if exits exist
5. Add CLI:

```powershell
kalshi-weather replay --snapshot-dir data/snapshots
```

## Acceptance criteria

```powershell
pytest tests/test_backtest_metrics.py
kalshi-weather replay --snapshot-dir data/snapshots
```

Expected:

```text
Backtest runs on saved snapshots and produces a metrics table.
```
