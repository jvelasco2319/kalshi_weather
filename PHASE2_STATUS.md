# Phase 2 Status

## Canonical Directory

- Canonical project root: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Previous active directory: `C:\Users\jarve\OneDrive\Documents\kalshi_weather`
- OneDrive mismatch resolved: yes. The editable install now points to the canonical Codex directory.
- Backup created before replacement: `C:\Users\jarve\Documents\Codex\kalshi_weather_backup_20260619_173343`
- OneDrive project preserved: yes.
- Runtime `data/` copied to canonical: yes.
- `.env`, `.venv`, `.git`, caches, egg-info, and secrets were not copied into the canonical replacement.

## Quality Gates

- `python -m pytest`: passed, 40 tests.
- `python -m ruff check .`: passed.
- `python -m kalshi_weather.cli --help`: passed.
- `kalshi-weather --help`: passed.
- Handoff zip generated: `C:\Users\jarve\Documents\Codex\kalshi_weather\kalshi_weather_handoff_latest.zip`.
- Handoff zip check: `HANDOFF_ZIP_CHECK.txt` confirms required `src/kalshi_weather/data/` files are present.

## Open-Meteo Status

- Model-specific request mode: enabled.
- Successful models in live canonical debug: `gfs_seamless`.
- Failed configured models in live canonical debug: `hrrr_conus`, `nbm_conus`, `aigfs025`, `hgefs025`.
- Fallback used in live canonical debug: false.
- Latest selected model future high in live canonical debug: 63.4 F.
- Failed model errors are surfaced in `weather-debug` and logs instead of hidden.

## Canonical SQLite Counts

- Market snapshots: 24.
- Weather snapshots: 24.
- Model predictions: 144.
- Official outcomes: 0.
- Joined prediction outcomes: 0.
- Paper fills: 0.
- Paper equity records: 18.

Official outcome CLI commands were verified in scratch SQLite paths so the production database was not polluted:

- Automatic `fetch-outcome` for `KLAX` on `2026-06-19`: passed in scratch and stored official high 70.0 F.
- Manual `record-outcome` for `KLAX` on `2026-06-19`: passed in scratch.
- `join-outcomes`: joined 6 scratch prediction rows.
- `calibration-report`: produced scratch joined-row metrics and fewer-than-30 warning.

## Safety

- Live trading enabled: false.
- Live order endpoint present: false.
- Authenticated Kalshi order placement: not implemented.
- Kalshi create-order calls: not implemented.
- Paper trading remains fake-money only.

## Commands Run

```powershell
python -m pip install -e ".[dev]"
python -m pip show kalshi-weather
python -m pytest
python -m ruff check .
python -m kalshi_weather.cli --help
kalshi-weather --help
kalshi-weather weather-debug --station KLAX
kalshi-weather collect-once --series KXHIGHLAX --station KLAX
kalshi-weather collect-loop --series KXHIGHLAX --station KLAX --interval-seconds 60 --max-iterations 2
kalshi-weather markets --series KXHIGHLAX
kalshi-weather weather-snapshot --station KLAX
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60 --max-iterations 3
kalshi-weather calibration-report
kalshi-weather paper-report
kalshi-weather fetch-outcome --station KLAX --date 2026-06-19
kalshi-weather record-outcome --station KLAX --date 2026-06-19 --official-high-f 71 --source manual --overwrite
kalshi-weather join-outcomes --station KLAX
kalshi-weather calibration-report
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\make_handoff_zip.ps1
```

## Known Limitations

- Several configured Open-Meteo model identifiers are rejected by the selected Open-Meteo endpoint; `gfs_seamless` currently succeeds.
- Automatic NWS CLI parsing has been validated for one KLAX settled date and should be checked across more dates.
- Production calibration remains empty until real official outcomes are recorded or fetched into the canonical database.
- Paper account resume/reset is left for Phase 3; current SQLite records support reporting/audit.
- Estimated unrealized P&L, entry-edge averages, and hold-time averages report as unavailable unless those fields are captured in future paper state.

## Next Recommended Work

- Validate NWS CLI outcome parsing across multiple KLAX historical dates.
- Improve Open-Meteo model alias/provider selection for rejected model identifiers.
- Add paper state resume/reset with `--reset-paper`.
- Add empirical residual calibration after enough joined rows exist.
