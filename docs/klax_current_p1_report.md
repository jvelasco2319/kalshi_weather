# KLAX Current P1 Report

Phase: P1 - Strategy-scoped model registry and configuration

## Implemented

- Added runtime strategy config files:
  - `config/strategy_current.shadow.yaml`
  - `config/model_registry_current.yaml`
- Added `kalshi_weather.strategy_current` with:
  - exact canonical model order: `ecmwf_ifs`, `gfs013`, `gfs_seamless`, `nam`, `nbm`
  - source preferences for Open-Meteo and NOAA/Herbie NBM
  - `nam_conus` alias collapse to `nam`
  - provider/source history keys that separate non-alias provider substitutions
  - shadow-only config validation
  - deterministic config hash
- Added tests in `tests/test_strategy_current_registry.py`.

## Commands Run

| Command | Result |
|---|---|
| `python -m pytest -q tests\test_strategy_current_registry.py` | PASS |
| `python -m pytest -q` | PASS |
| `python -m ruff check src tests` | PASS |

## Deviations

- None beyond P0 deviations already recorded in `docs/klax_current_deviations.md`.
