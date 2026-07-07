# POC Final Status

- canonical directory: C:\Users\jarve\Documents\Codex\kalshi_weather
- Python version: Python 3.12.7
- editable package path: Editable project location: C:\Users\jarve\Documents\Codex\kalshi_weather
- tests passed: yes (70 passed)
- Ruff passed: yes
- CLI help passed: yes
- live trading enabled: false
- live order endpoint present: false
- create-order search findings: safety search only found safety/config/docs/test guard references, not implementation of live order placement.
- commands run: pytest, Ruff, CLI help, research-status, time-debug, weather-debug, opportunities, threshold-sweep, calibration-readiness, calibration-report, residual-report, paper-report, paper-replay, poc-demo, poc-check, daily-maintenance, collect-session, collect-loop, markets, weather-snapshot, predict-once, paper-once, run-paper, model-weight-report, tune-residual-sigma, fit-probability-calibration dry-run, replay-predictions, model-health, model-vs-market.
- POC commands available: research-status, daily-maintenance, collect-session, opportunities, threshold-sweep, calibration-readiness, model-vs-market, model-health, validation-run, calibration-report, residual-report, paper-report, paper-replay, poc-run, poc-demo, poc-check.
- production DB counts: {'market_snapshots': 40, 'weather_snapshots': 40, 'model_predictions': 240, 'official_outcomes': 1, 'prediction_outcomes': 174, 'paper_fills': 0, 'paper_positions': 0, 'opportunity_snapshots': 0, 'paper_equity': 22}
- proof-of-edge status: edge is not proven yet. There are 174 joined rows, but they all come from one market date, and no fake paper fills fired under current thresholds. Demo works and proves plumbing only.
- how user should test: run kalshi-weather poc-check --series KXHIGHLAX --station KLAX and kalshi-weather poc-demo --station KLAX from the canonical root.
- known limitations: calibration/model-vs-market are smoke-test only until joined rows span enough independent market dates; paper replay is approximate with sparse snapshots.
- next recommended work: continue collect-session/daily-maintenance, fetch official outcomes after settlement, join outcomes, then review calibration/replay.

## Operational Validation Update

- `model-health` implemented: yes.
- `model-vs-market` implemented: yes.
- improved `calibration-readiness`: yes.
- Windows automation scripts created: yes.
- beginner results guide created: `docs/HOW_TO_READ_RESULTS.md`.
- external LLM summary automation removed/deprecated: yes.
- current model-health overall status: `EARLY SIGNAL`.
- reason: one official outcome was fetched and 174 prediction rows were joined, but they all come from one market date.
- next recommended command: continue collecting and fetch/join additional settled dates.
