# Prompt 05 — Paper-trading engine

Implement fake-money trading.

## Files to implement/update

```text
src/kalshi_weather/trading/paper_broker.py
src/kalshi_weather/trading/portfolio.py
src/kalshi_weather/trading/risk.py
src/kalshi_weather/data/storage.py
src/kalshi_weather/cli.py
tests/test_paper_broker.py
tests/test_risk.py
```

## Tasks

1. Implement a `PaperBroker` that tracks:
   - cash
   - positions by ticker and side
   - realized P&L
   - fill ledger
2. Implement fake fills:
   - buy YES at executable YES ask
   - sell YES at executable YES bid
   - buy NO at executable NO ask
   - sell NO at executable NO bid
3. Implement risk checks:
   - max order cost
   - max position per market
   - no negative cash
   - no selling more than held
4. Implement signal-to-paper-order conversion:
   - buy YES if `yes_edge > require_edge + fee_buffer + model_error_buffer`
   - buy NO if `no_edge > require_edge + fee_buffer + model_error_buffer`
5. Persist fake fills and snapshots to SQLite.
6. Wire CLI:

```powershell
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
```

## Acceptance criteria

```powershell
pytest tests/test_paper_broker.py tests/test_risk.py
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
```

Expected:

```text
Tests pass.
Command either places a fake fill or prints “no trade; edge below threshold.”
No real order is sent.
```

## Do not do

Do not call Kalshi create-order endpoints.
