# Model Estimate Comparison Status

- canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- tests passed: yes (`117 passed`)
- Ruff passed: yes
- CLI help passed: yes
- live trading enabled: false
- live order endpoint present: false
- authenticated order placement present: false
- current model behavior preserved: yes
- Open-Meteo estimates implemented: yes
- current blend estimate implemented: yes
- direct NOAA/Herbie provider implemented: yes, optional and graceful
- Herbie dependency available: yes
- model-provider-probe implemented: yes
- model-estimates implemented: yes
- model-probabilities implemented: yes
- model-estimate-score implemented: yes
- storage tables created: yes
- latest current blend high estimate: 69.33 F
- latest Open-Meteo individual estimates:
  - best_match: 69.8 F
  - gfs013: 68.7 F
  - gfs_global: 68.7 F
  - gfs_seamless: 69.8 F
- latest HRRR estimate or failure reason: available, 71.1 F future high
- latest NBM estimate or failure reason: available, 68.4 F future high
- latest GFS direct estimate or failure reason: available, 68.4 F future high
- latest RAP estimate or failure reason: available, 75.3 F future high
- official outcomes count: 6
- joined outcomes count: 282
- model predictions count: 1746
- model_estimates count: 9
- model_estimate_probabilities count: 30
- paper fills count: 0

## Commands Run

- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_direct_noaa_models.ps1`
- `kalshi-weather direct-noaa-check --station KLAX`
- `kalshi-weather direct-noaa-check --station KLAX --json --output reports/latest_direct_noaa_check.json`
- `kalshi-weather model-provider-probe --station KLAX`
- `kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures`
- `kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --include-probabilities --json --output reports\latest_model_estimates.json`
- `kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX --json --output reports\latest_model_probabilities.json`
- `kalshi-weather model-estimate-score --station KLAX`
- `kalshi-weather collect-once --series KXHIGHLAX --station KLAX --include-model-estimates`

## Current Interpretation

The separate model estimate comparison layer is implemented and working for the
current production blend, individual Open-Meteo feeds, and direct NOAA/Herbie
HRRR/NBM/GFS/RAP rows. The direct NOAA rows are slower than Open-Meteo and may
fall back to older recent cycles when the newest cycle index is not yet
published.

The new comparison layer is sidecar-only. It does not replace the current
weighted Open-Meteo model, does not change opportunities, and does not change
paper-entry logic by default.

## Known Limitations

- Direct NOAA/Herbie live retrieval is validated in this environment, but it is
  slow and should be treated as comparison-only until scored outcomes exist.
- `model-estimate-score` currently has no scored rows because comparison
  estimates were just added and the latest stored comparison estimates are for
  the unsettled June 20, 2026 market date.
- Stored comparison rows are diagnostic only and should not be interpreted as
  proof of edge.

## Next Recommended Work

1. Continue collecting comparison rows across settled market dates.
2. Use `kalshi-weather direct-noaa-check --station KLAX` to diagnose direct
   NOAA provider health.
3. After settlement, run `kalshi-weather fetch-missing-outcomes --station KLAX`
   and `kalshi-weather model-estimate-score --station KLAX`.
4. Do not blend comparison estimates into production logic until scored outcomes
   justify it.
