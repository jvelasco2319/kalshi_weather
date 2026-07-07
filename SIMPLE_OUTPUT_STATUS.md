# Simple Output Status

- canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- tests passed: yes (`95 passed`)
- Ruff passed: yes
- CLI help passed: yes
- live trading enabled: false
- live order endpoint present: false
- authenticated order placement present: false
- simple-summary implemented: yes
- model-summary alias implemented: yes
- weather-summary implemented: yes
- collect-session simplified: yes
- raw/debug output preserved: yes (`collect-session --verbose` and `collect-session --debug-json`)
- JSON output implemented: yes
- CSV output implemented: yes
- latest production future estimate: 69.1 F
- latest current settlement estimate: 69.8 F
- latest top current bracket: 69-70
- latest top current probability: 92.1%
- model agreement status: HIGH
- data readiness status: SMOKE_TEST_ONLY
- unique joined market dates: 1
- successful Open-Meteo models: gfs_seamless, gfs013, gfs_global, best_match
- failed Open-Meteo models: none
- market snapshots: 57
- weather snapshots: 57
- model predictions: 342
- model estimates: 9
- model estimate probabilities: 30
- official outcomes: 1
- joined prediction outcomes: 174
- paper fills: 0

## Commands Run

- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `kalshi-weather --help`
- `python -m pip show kalshi-weather`
- `kalshi-weather simple-summary --series KXHIGHLAX --station KLAX`
- `kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges`
- `kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --json --output results_for_chatgpt/REPORTS/simple_summary.json`
- `kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --csv --output results_for_chatgpt/REPORTS/simple_summary.csv`
- `kalshi-weather weather-summary --station KLAX`
- `kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 2`
- safety search command from `results_for_chatgpt/COMMAND_OUTPUTS/safety_search.txt`

## Known Limitations

- Direct NOAA/Herbie HRRR/NBM/GFS/RAP rows remain unavailable because optional Herbie/cfgrib/xarray/ecCodes dependencies are not installed.
- The current validation sample is still smoke-test only because joined outcomes cover one market date.
- Apparent market edges in `--show-prices --show-edges` are analysis-only and are not proof of edge.
- `simple-summary` does not alter production model logic and does not place fake or live trades.

## Next Recommended Work

Continue collecting snapshots, fetch or record official outcomes after settlement, run `join-outcomes`, and review `model-health`/`model-vs-market` only after more independent market dates are joined.
