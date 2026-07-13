# KLAX Signal Room

KLAX Signal Room is a read-only local dashboard for the current five-model KLAX
weather strategy. It does not submit orders, expose order controls, or import an
order submission client. Live mode reads existing strategy-current state when
present and falls back to the existing validation recorder journal for live
display data.

## Start Live Dashboard

From the repository root:

```powershell
python -m kalshi_weather.cli strategy-dashboard --host 127.0.0.1 --port 8765
```

If the package is not installed in your active environment, set `PYTHONPATH`
first:

```powershell
$env:PYTHONPATH='src'
python -m kalshi_weather.cli strategy-dashboard --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

PowerShell launcher equivalent:

```powershell
.\scripts\start_signal_room_dashboard.ps1 -Mode live -SampleFixture ""
```

Useful options:

```powershell
python -m kalshi_weather.cli strategy-dashboard --sqlite-path journals/lax_model_validation.sqlite
python -m kalshi_weather.cli strategy-dashboard --poll-seconds 5
python -m kalshi_weather.cli strategy-dashboard --open-browser
```

The server refuses non-loopback hosts unless `--allow-remote` is supplied.

When reading `validation_*` recorder rows, the dashboard displays live model,
observation, and market data but blocks candidates with
`NO_TRADE_PROBABILITY_UNCALIBRATED` because calibrated shadow probabilities and
economics are not persisted by the legacy recorder loop.

## Start Historical Replay Fixture

The July 7 sample is explicit replay data for UI validation only:

```powershell
python -m kalshi_weather.cli strategy-dashboard --mode replay --sample-fixture tests/fixtures/signal_room_july7_replay.json --host 127.0.0.1 --port 8765
```

PowerShell launcher equivalent:

```powershell
.\scripts\start_signal_room_dashboard.ps1 -Mode replay -SampleFixture tests/fixtures/signal_room_july7_replay.json
```

Replay mode shows a historical banner and isolates settlement truth from
decision-time fields. It should not be used as live evidence.

## API

The dashboard exposes versioned read-only routes:

- `GET /api/v1/signal-room/health`
- `GET /api/v1/signal-room/events`
- `GET /api/v1/signal-room/events/{event_ticker}/snapshot`
- `GET /api/v1/signal-room/events/{event_ticker}/timeline`
- `GET /api/v1/signal-room/events/{event_ticker}/capture-health`

Snapshot responses include an `ETag`; unchanged live data returns `304 Not
Modified` when the client sends `If-None-Match`.

## UI Contract

- Exactly five model slots are rendered in canonical order: `ecmwf_ifs`,
  `gfs013`, `gfs_seamless`, `nam`, `nbm`.
- Unknown model keys are rejected by the response model.
- Prices, quantities, and ROI values remain strings or null.
- Missing probability or calibration values render as unavailable, never zero.
- The frontend formats and displays backend-provided values; it does not compute
  trading decisions.

## Validation

Run:

```powershell
python -m ruff check src tests scripts/capture_signal_room_screenshots.py
python -m pytest -q
python scripts/capture_signal_room_screenshots.py
```

Screenshot outputs:

- `reports/signal_room/klax_signal_room_desktop.png`
- `reports/signal_room/klax_signal_room_mobile.png`
- `reports/signal_room/screenshot_manifest.json`
