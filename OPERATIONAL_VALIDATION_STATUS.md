# Operational Validation Status

- phase name: Operational Validation and Model Health
- canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- tests passed: yes (`70 passed`)
- Ruff passed: yes
- CLI help passed: yes
- live trading enabled: false
- live order endpoint present: false
- authenticated order placement present: false
- Ollama summary removed/deprecated: yes
- model-health implemented: yes
- model-vs-market implemented: yes
- calibration-readiness improved: yes
- Windows automation scripts created: yes
- daily-maintenance includes model-health: yes
- separate model estimate comparison implemented: yes, see `MODEL_ESTIMATE_COMPARISON_STATUS.md`
- official outcomes count: 1
- joined prediction outcomes count: 174
- model predictions count: 318
- model_estimates count: 9
- model_estimate_probabilities count: 30
- market snapshots count: 53
- weather snapshots count: 53
- paper fills count: 0
- current model-health overall status: EARLY SIGNAL
- current readiness level: READY_FOR_SMOKE_CALIBRATION
- current model-vs-market status: TOO_SMALL
- unique joined market dates: 1
- unsettled prediction dates waiting: 2026-06-20
- next recommended command: review model-vs-market and paper-replay before changing thresholds

## Commands Run

- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `kalshi-weather model-health --series KXHIGHLAX --station KLAX`
- `kalshi-weather model-health --series KXHIGHLAX --station KLAX --json --output reports\latest_model_health.json`
- `kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX`
- `kalshi-weather calibration-readiness --station KLAX`
- `kalshi-weather daily-maintenance --series KXHIGHLAX --station KLAX --skip-collect`

## Current Interpretation

The system is operational as a validation machine, but edge is not proven.
The June 19, 2026 official outcome was fetched and joined, producing 174 joined
rows from one market date. A read-only POC check also collected June 20, 2026
predictions, which are intentionally waiting for settlement. Model-health
correctly reports `EARLY SIGNAL`, while model-vs-market reports `TOO_SMALL`
because one joined market date is not enough independent evidence.

## Known Limitations

- Production calibration is now possible as a smoke test only, not proof of edge.
- Model-vs-market can score 174 rows, but they all come from one market date.
- Paper fills remain zero because no edge cleared the configured hurdle.
- Windows scheduled tasks were scripted but not installed automatically.

## Next Recommended Work

1. Continue collecting additional market dates.
2. Fetch and join official outcomes after each settled date.
3. Re-run `kalshi-weather model-health --series KXHIGHLAX --station KLAX`.
4. Do not treat one-date performance as proof of edge.
