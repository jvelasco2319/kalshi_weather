# Direct NOAA Models Status

- canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- tests passed: yes, `117 passed`
- Ruff passed: yes
- CLI help passed: yes
- live trading enabled: false
- live order endpoint present: false
- Herbie installed: yes, `herbie-data 2026.3.0`
- xarray installed: yes, `xarray 2026.4.0`
- cfgrib installed: yes, `cfgrib 0.9.15.1`
- eccodes installed: yes, `eccodes 2.47.0`

## Targets

- HRRR target: `hrrr/sfc`
- NBM target: `nbm/co`
- GFS target: `gfs/pgrb2.0p25`
- RAP target: `rap/awp130pgrb`

## Latest Live Direct NOAA Check

Command:

```powershell
kalshi-weather direct-noaa-check --station KLAX
```

Latest successful values from the saved JSON report:

- HRRR direct estimate: 71.1 F future high, 71.1 F settlement estimate.
- NBM direct estimate: 68.4 F future high, 69.8 F settlement estimate.
- GFS direct estimate: 68.4 F future high, 69.8 F settlement estimate.
- RAP direct estimate: 75.3 F future high, 75.3 F settlement estimate.

The newest 17 UTC model cycle initially lacked an index for some direct models,
so the provider fell back to the 16 UTC cycle and then produced values. This is
expected for live NOAA products that are still publishing.

## Current/Open-Meteo Status

- current/Open-Meteo estimates still working: yes
- `model-estimates` includes direct NOAA rows: yes
- `model-probabilities` includes direct NOAA rows: yes
- direct NOAA models remain comparison-only: yes
- direct NOAA models are not blended into production trading logic: yes

## Commands Run

```powershell
python -m pytest
python -m ruff check .
python -m kalshi_weather.cli --help
python -m pip show herbie-data
python -m pip show xarray
python -m pip show cfgrib
python -m pip show eccodes
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_direct_noaa_models.ps1
kalshi-weather direct-noaa-check --station KLAX
kalshi-weather direct-noaa-check --station KLAX --json --output reports/latest_direct_noaa_check.json
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX --show-market-prices --json --output reports/latest_direct_model_probabilities.json
```

## Known Limitations

- Direct NOAA/Herbie fetches are much slower than Open-Meteo. A full check can
  take several minutes.
- Herbie may report that the newest model cycle has no index file yet. The
  provider now falls back to older recent cycles.
- Installing optional direct NOAA packages upgraded shared Anaconda packages
  including `pandas` and `packaging`. Pip reported conflicts with unrelated
  packages such as Streamlit, Syft, and recordlinkage. Kalshi Weather tests
  still pass.
- The Python environment emits pandas warnings about old `numexpr` and
  `bottleneck` versions. These warnings did not fail tests or CLI behavior.
- Herbie warns that it cannot create `C:\Users\jarve\.config\herbie\config.toml`
  and will use defaults.

## Next Recommended Work

- Collect and store several days of comparison model estimates.
- After official outcomes are joined, run `model-estimate-score --station KLAX`
  to compare HRRR/NBM/GFS/RAP against the current/Open-Meteo path.
- Consider a lighter direct NOAA cache strategy before collecting every minute
  with Herbie enabled.
- Do not blend direct NOAA models into production until enough settled dates
  prove the change improves accuracy.
