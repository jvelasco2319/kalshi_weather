# KLAX Current P5 Report

Phase: P5 - Five-model probability mixture

## Implemented

- Added `kalshi_weather.strategy_current.probabilities` with:
  - model-specific residual scenario distributions
  - Dirichlet/Beta posterior mean and conservative Yes/No probabilities
  - NBM maturity caps
  - shrunk reliability weights
  - individual and GFS-family caps
  - conservative mixture probabilities using the minimum of mixture and weighted component bounds
  - forecast reporting summary from corrected model points
- Added tests in `tests/test_strategy_current_probabilities.py`.

## Commands Run

| Command | Result |
|---|---|
| `python -m pytest -q tests\test_strategy_current_probabilities.py` | PASS |
| `python -m pytest -q` | PASS |
| `python -m ruff check src tests` | PASS |

## Deviations

- P5 exposes pure probability functions but is not yet connected to a strategy CLI command. That orchestration belongs to P7.
