# Phase 3 Status

## Canonical Directory

- Canonical project root: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Tests passed: yes, `52 passed`.
- Ruff passed: yes.
- `python -m kalshi_weather.cli --help`: passed.
- Live trading enabled: false.
- Live order endpoint present: false.
- Authenticated Kalshi order placement: not implemented.

## NWS Local-Standard-Time Logic

- NWS market-date logic fixed: yes.
- Market date uses fixed UTC-8 local standard time.
- June 19, 2026 climate day boundaries:
  - Start: `2026-06-19 08:00 UTC`
  - End: `2026-06-20 08:00 UTC`
- `time-debug` live summary:
  - `now_utc`: `2026-06-20 02:06 UTC`
  - America/Los_Angeles wall time: `2026-06-19 19:06 PDT`
  - Fixed local-standard time: `2026-06-19 18:06 PST`
  - Computed market date: `2026-06-19`
  - Remaining window: `2026-06-19 19:06` to `2026-06-20 01:00` local wall time.

## Open-Meteo Probe Summary

- Probe command passed: `kalshi-weather probe-open-meteo-models --station KLAX`.
- Successful model IDs:
  - `best_match`
  - `gfs013`
  - `gfs025`
  - `gfs_global`
  - `gfs_graphcast025`
  - `gfs_seamless`
- Failed model IDs:
  - `aigfs`
  - `aigfs025`
  - `gfs_global016`
  - `gfs_global025`
  - `gfs_graphcast`
  - `graphcast`
  - `graphcast025`
  - `hgefs`
  - `hgefs025`
  - `hrrr`
  - `hrrr_conus`
  - `nam`
  - `nam_conus`
  - `nbm`
  - `nbm_conus`
- `weather-debug` live status:
  - Successful forecast model: `gfs_seamless`.
  - Generic fallback used: false.
  - Selected future high: 62.1 F.
  - Marine-layer feature summary included cloud-cover, radiation, wind, gust, and apparent-temperature summaries.

## Production SQLite Counts

- Market snapshots: 27.
- Weather snapshots: 27.
- Model predictions: 162.
- Official outcomes: 0.
- Joined prediction outcomes: 0.
- Paper fills: 0.
- Paper equity records: 18.
- Paper state events: 0.

`fetch-missing-outcomes --station KLAX` skipped 1 current fixed-standard climate date and stored 0 outcomes. This is expected because the only production prediction date is still the current/unsettled NWS climate date. `fetch-outcomes --dry-run` and `validate-outcome-parser` both parsed `2026-06-19` as 70.0 F from NWS CLI text, but production storage was not polluted with the unsettled current date.

## Calibration And Residuals

- `join-outcomes --station KLAX --overwrite`: scanned 90 rows, matched 0, joined 0, skipped 90.
- `calibration-report --station KLAX`: passed with the graceful empty-state message.
- `residual-report --station KLAX`: passed with 0 joined rows and a fewer-than-30 warning.
- Calibration metrics are unavailable until official outcomes are stored for settled prediction dates.

## Paper State

- Persistent paper state implemented: yes.
- `paper-once` and `run-paper` resume latest paper cash and positions from SQLite by default.
- `--reset-paper` records a reset event and starts from configured fake cash.
- `paper-report` includes current cash, open positions, total exposure, fills by day, entry-edge average when available, and reset events.
- Paper fills count: 0.

## Commands Run

```powershell
python -m pytest
python -m ruff check .
python -m kalshi_weather.cli --help
kalshi-weather time-debug --station KLAX
kalshi-weather probe-open-meteo-models --station KLAX
kalshi-weather weather-debug --station KLAX
kalshi-weather opportunities --series KXHIGHLAX --station KLAX
kalshi-weather collect-once --series KXHIGHLAX --station KLAX
kalshi-weather fetch-missing-outcomes --station KLAX
kalshi-weather join-outcomes --station KLAX --overwrite
kalshi-weather calibration-report --station KLAX
kalshi-weather residual-report --station KLAX
kalshi-weather paper-report
kalshi-weather fetch-outcomes --station KLAX --start-date 2026-06-19 --end-date 2026-06-19 --dry-run
kalshi-weather validate-outcome-parser --station KLAX --start-date 2026-06-19 --end-date 2026-06-19
python -m pip show kalshi-weather
rg -n "create-order|orders|real order|live order|KALSHI_ENABLE_REAL_ORDERS|private_key|api_key|trade_api|submit|place_order|CreateOrder|requests.post|httpx.post" src tests README.md docs config .env.example
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\make_handoff_zip.ps1
```

## Known Limitations

- Production official outcomes and joined outcomes remain 0 because current-date outcomes were intentionally skipped.
- HRRR/NBM and several AIGFS/HGEFS aliases are rejected by the selected Open-Meteo endpoint.
- Opportunity table rendering is dense in narrow terminals; the underlying values are present.
- Paper unrealized P&L and hold-time averages remain placeholders until quote/fill history is expanded.
- Residual reporting is descriptive only; no complex ML calibration is trained yet.

## Next Recommended Work

- Run `fetch-missing-outcomes --station KLAX` after the current fixed-standard climate date settles.
- Validate NWS CLI parsing across several fully settled KLAX dates.
- Promote the best working Open-Meteo aliases into preferred same-day model config after more probing.
- Add quote-history-based paper mark-to-market P&L.
- Add empirical residual calibration after enough joined rows exist.
