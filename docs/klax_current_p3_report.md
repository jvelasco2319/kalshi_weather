# KLAX Current P3 Report

Phase: P3 - Canonical as-of state builder

## Implemented

- Added `kalshi_weather.strategy_current.state_builder` with:
  - strict backward as-of run selection
  - rejection of future `source_available_at` and `received_at` inputs
  - remaining-window forecast maximum
  - observed maximum so far from accepted, available KLAX observations
  - raw live state calculation as `max(observed_max, future_max)`
  - model spread calculation requiring at least four feeds and three families
- Added July 7 regression coverage proving a past hot forecast point cannot remain the future maximum after its valid time has passed.
- Added future-source leakage tests for forecast and observation rows.

## Commands Run

| Command | Result |
|---|---|
| `python -m pytest -q tests\test_strategy_current_state_builder.py` | PASS |
| `python -m pytest -q` | PASS |
| `python -m ruff check src tests` | PASS |

## Deviations

- The state builder is pure and event-record based. It is not yet wired into live CLI commands; that belongs to P7 after probability, economics, and shadow decisions exist.
