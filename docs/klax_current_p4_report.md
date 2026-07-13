# KLAX Current P4 Report

Phase: P4 - Historical reconstruction and residual libraries

## Implemented

- Added `kalshi_weather.strategy_current.residuals` with:
  - historical live-state records based on P3 semantics
  - physical outcome and settlement-gap records
  - prior-date residual construction only
  - rolling recent-date cap
  - recency weights, effective sample size, weighted median residual
  - corrected point estimate helper for reporting
- Added tests in `tests/test_strategy_current_residuals.py`.

## Commands Run

| Command | Result |
|---|---|
| `python -m pytest -q tests\test_strategy_current_residuals.py` | PASS |
| `python -m pytest -q` | PASS |
| `python -m ruff check src tests` | PASS |

## Deviations

- Chronological replay fixtures are represented as pure live-state/outcome records for now. CLI replay wiring belongs to P8.
