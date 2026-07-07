# Paper Model Race Status

## Summary

- Canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Tests passed: yes, `python -m pytest` reported 144 passed.
- Ruff passed: yes, `python -m ruff check .` passed.
- CLI help passed: yes, `python -m kalshi_weather.cli --help` passed.
- Live trading enabled: false.
- Live order endpoint present: false.
- Fake-money only: true.
- Model race implemented: yes.
- Starting cash per model: `$100`.
- Compact shell output implemented: yes.
- Report command implemented: yes.
- Reset command implemented: yes.

## Models Included

- `current:current_weighted_blend`
- `open_meteo:best_match`
- `open_meteo:gfs013`
- `open_meteo:gfs_global`
- `open_meteo:gfs_seamless`
- `noaa_herbie:hrrr`
- `noaa_herbie:nbm`
- `noaa_herbie:gfs`
- `noaa_herbie:rap`

## Latest Live Fake-Money Check

- Latest generated report: `reports/model_race/latest_model_race.txt`
- Latest JSON report: `reports/model_race/latest_model_race.json`
- Latest loop session: `reports/model_race/model_race_20260622_205023/`
- Current/Open-Meteo models working: yes.
- Direct NOAA models working: partial.
- Successful direct NOAA models in latest run: HRRR, NBM, RAP.
- Failed direct NOAA models in latest run: GFS direct.
- GFS direct failure mode: unavailable provider row; the run continued safely.
- Model agreement: LOW.
- Model spread: 2.9 F.
- Open positions count: 8.
- Fake-money live commands placed real trades: false.

## Latest Leaderboard Snapshot

| Model | Cash | Open P/L | Closed P/L | Total Equity | Trades | Wins | Losses |
|---|---:|---:|---:|---:|---:|---:|---:|
| `noaa_herbie:gfs` | $100.00 | $0.00 | $0.00 | $100.00 | 0 | 0 | 0 |
| `current:current_weighted_blend` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `noaa_herbie:hrrr` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `noaa_herbie:nbm` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `open_meteo:best_match` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `open_meteo:gfs013` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `open_meteo:gfs_global` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `open_meteo:gfs_seamless` | $94.50 | $-0.36 | $-0.64 | $94.14 | 3 | 0 | 1 |
| `noaa_herbie:rap` | $95.00 | $-2.50 | $0.00 | $92.50 | 1 | 0 | 0 |

## SQLite Row Counts

- `model_race_accounts`: 47
- `model_race_positions`: 22
- `model_race_fills`: 29
- `model_race_equity`: 36
- `model_race_events`: 4

## Commands Run

- `python -m pytest tests\test_model_race.py`
- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `kalshi-weather paper-model-race-reset --race-id default --confirm`
- `kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id default --starting-cash-per-model 100`
- `kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id default --starting-cash-per-model 100 --json --output reports/model_race/latest_model_race.json`
- `kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX --race-id default`
- `kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id default --starting-cash-per-model 100 --interval-seconds 900 --max-iterations 1`
- Required safety `rg` scan over `src`, `tests`, docs, config, scripts, and `.env.example`.

## Known Limitations

- Direct NOAA/Herbie reads are slow compared with Open-Meteo.
- Direct GFS can be unavailable because the remote UCAR data source can fail SSL/index access; the race records it as unavailable and continues.
- The latest leaderboard is only a live smoke test from one market date and should not be treated as proof of edge.
- Fake fills depend on visible top-of-book prices and simplified paper execution.
- Direct NOAA models remain comparison-only and are not blended into production logic.

## Next Recommended Work

- Let the model race run every 15 minutes over several full market days.
- Join outcomes after settlement and compare model-race behavior against official highs.
- Add settled-day scoring for model-race positions after enough data exists.
- Review direct GFS data-source reliability separately from the model-race engine.
