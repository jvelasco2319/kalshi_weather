# KLAX Current Deviations

This file records deviations from the copied `klax-current-five-model-2026-07-11` package or assumptions that did not match the active repository.

## P0

- The package references `C:\Users\jarve\Documents\Codex\kalshi_weather`; the active workspace is `C:\Users\jarve\OneDrive\Documents\kalshi_weather`.
- The package assumes a prior Git revision can be recorded, but this repository has no `HEAD` commit yet.
- The package asks to inspect `tests/test_trader_paper_safety.py`; that file does not exist. Current closest safety coverage is `tests/test_safety_phase2.py` and paper broker tests.
- The package references `paper-model-race-run`; no matching CLI command exists in this repository.
- The package assumes an existing NOAA/Herbie NBM fetcher. The repository currently has an NBM registry entry but no working Herbie/NBM client implementation under `src/kalshi_weather`.
