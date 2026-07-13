# KLAX Current P8 Report

Phase: P8 - Replay and execution simulation

## Implemented

- Added `kalshi_weather.strategy_current.replay` with:
  - chronological replay event accounting
  - explicit candle analytics-only labeling
  - depth-aware taker fill simulation
  - maker simulation refusal without synchronized books and latency assumptions
- Added CLI command:
  - `kalshi-weather strategy-replay`
- Added tests in `tests/test_strategy_current_replay.py`.

## Commands Run

| Command | Result |
|---|---|
| `python -m pytest -q tests\test_strategy_current_replay.py` | PASS |
| `python -m pytest -q` | PASS |
| `python -m ruff check src tests` | PASS |

## Deviations

- July 7 and July 9 replay fixtures are not yet wired to real persisted market days because the current repo lacks complete source-event captures with trades and sequence-valid books. P8 provides the replay/simulation rules and refuses candle-only executable claims.
