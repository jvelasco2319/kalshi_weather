# Final Status

## Phase 3 Implemented

- Canonical root remains `C:\Users\jarve\Documents\Codex\kalshi_weather`.
- Fixed NWS market-date handling to use fixed UTC-8 local standard time.
- Fixed remaining-day Open-Meteo window to end at the fixed-standard climate-day end converted to local wall time.
- Added `time-debug`.
- Added outcome range fetch, missing-outcome backfill, parser validation, and storage helper methods.
- Hardened NWS CLI daily-high parsing and date matching.
- Added Open-Meteo model alias probe command and variable fallback.
- Added richer weather model details for marine-layer features.
- Added `opportunities` diagnostics.
- Added filtered calibration report output and `residual-report`.
- Added persistent fake-money paper state resume/reset.
- Added fake-money risk settings and storage fields for richer paper reports.
- Updated docs, config, tests, and handoff packaging.

## Quality Gates

- `python -m pytest`: 52 passed.
- `python -m ruff check .`: passed.
- `python -m kalshi_weather.cli --help`: passed.

## Live Commands

The following live read-only or fake-money commands passed:

- `kalshi-weather time-debug --station KLAX`
- `kalshi-weather probe-open-meteo-models --station KLAX`
- `kalshi-weather weather-debug --station KLAX`
- `kalshi-weather opportunities --series KXHIGHLAX --station KLAX`
- `kalshi-weather collect-once --series KXHIGHLAX --station KLAX`
- `kalshi-weather fetch-missing-outcomes --station KLAX`
- `kalshi-weather join-outcomes --station KLAX --overwrite`
- `kalshi-weather calibration-report --station KLAX`
- `kalshi-weather residual-report --station KLAX`
- `kalshi-weather paper-report`
- `kalshi-weather fetch-outcomes --station KLAX --start-date 2026-06-19 --end-date 2026-06-19 --dry-run`
- `kalshi-weather validate-outcome-parser --station KLAX --start-date 2026-06-19 --end-date 2026-06-19`

## Current Canonical Data Counts

- Market snapshots: 27.
- Weather snapshots: 27.
- Model predictions: 162.
- Official outcomes: 0.
- Joined prediction outcomes: 0.
- Paper fills: 0.
- Paper equity records: 18.
- Paper state events: 0.

Production outcomes remain 0 because the only stored prediction date is the current fixed-standard climate date and `fetch-missing-outcomes` correctly skipped it. Dry-run outcome fetch and parser validation for `2026-06-19` parsed 70.0 F from NWS CLI text.

## Open-Meteo Status

- Successful probe IDs: `best_match`, `gfs013`, `gfs025`, `gfs_global`, `gfs_graphcast025`, `gfs_seamless`.
- Forecast path used `gfs_seamless`.
- Generic fallback used in latest live debug: false.
- Rejected IDs include HRRR/NBM aliases on the selected endpoint.

## Known Limitations

- Calibration/residual metrics remain empty until official outcomes are stored for settled prediction dates.
- Paper unrealized P&L and hold-time averages remain placeholders.
- Opportunity table output is dense in narrow terminals.
- No complex residual/ML model is trained yet.

## Live Trading Status

Live trading is disabled. No authenticated Kalshi create-order functionality is implemented or called.

## Phase 4-7 POC Additions

- Added safe daily collection and maintenance commands.
- Added opportunity snapshots, threshold sweep, research status, and final POC check.
- Added calibration readiness, demo calibration, residual sigma tuning, model-weight reporting,
  prediction replay, and fake paper replay.
- Added offline `poc-demo`, clearly labeled as demo-only and not trading evidence.
- Live trading remains disabled and out of scope.
