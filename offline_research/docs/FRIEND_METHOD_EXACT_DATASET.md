# Exact weather dataset for the friend-method test

## Purpose

This archive supplies the historical forecast inputs named in `TRADING_BOT_SPEC.md` for the same June 1 through August 31, 2026 KXHIGHLAX research window used by the paid Probalytics market archive. It replaces the earlier HRRR/GEFS proxy for a direct development-period test of the method's core weather model.

It is an offline research input. It contains no settlement labels, Kalshi prices, live feed, credentials, or order capability. The existing frozen market and label inputs remain separate, which makes it possible to verify the weather archive without opening or altering any protected confirmation data.

## Forecast snapshot

Each climate date uses the historical 00:00 UTC forecast cycle. That cycle predates the registered 18:00 UTC market decision point. The acquisition records both times in every point file and rejects any unexpected initialization or valid time.

The target is KLAX at latitude 33.93816 and longitude -118.3866. This is the airport settlement location used by the existing project, rather than downtown Los Angeles.

The stored inputs are:

| Model | Stored values | Daily-high rule | Source |
|---|---:|---|---|
| GFS | 6-hour TMAX at leads 12, 18, 24, 30, and 36 | Maximum of leads 18, 24, and 30, matching the supplied fixture; 12 and 36 are boundary diagnostics | NOAA NODD GFS archive, exact indexed GRIB ranges |
| GFS Seamless | Alias of GFS | Same value as GFS; one effective source | No duplicate download |
| NAM Grid 218 | 2-meter temperature at leads 9 through 30 every three hours | Maximum of all eight values | Iowa State Mesonet NCSS point subsets of archived NOAA NAM218 |
| NBM Core | Hourly 2-meter temperature at leads 7 through 31 | Primary maximum over leads 8–31 for the fixed-PST climate day; a second maximum over leads 7–30 reproduces the supplied fixture | NOAA NODD NBM archive, exact indexed GRIB ranges |

The GFS/GFS Seamless pairing is deliberately treated as one effective signal. The user-supplied July 3 fixture contains identical numeric arrays for those two names. Counting them twice would make the model appear more certain without adding independent information.

## File layout and provenance

The archive root is `data/raw/friend_method_weather_v1`. Every date, model, and lead has a `point.json` record. GFS and NBM folders also preserve the exact GRIB message and its complete source index. NAM folders preserve the returned point CSV. Each point record stores the source URL, retrieval time, source byte count, SHA-256 digest, forecast cycle, valid time, target coordinates, and decoded Fahrenheit value.

Each date also has a `daily.json` file containing the calculated model highs, the duplicate alias, expected decision time, and an explicit assertion that market prices and settlement labels are absent. `manifest.json` covers the whole 92-day acquisition and records missing tasks, source policies, effective source groups, and file totals.

Run the resumable acquisition with:

```powershell
$env:PYTHONPATH='src;.'
.\.venv\Scripts\python.exe scripts\acquire_friend_method_weather.py --project-root . --start-date 2026-06-01 --end-date 2026-08-31 --workers 12
```

Run the cache-only verification with:

```powershell
.\.venv\Scripts\python.exe scripts\verify_friend_method_weather.py --project-root .
```

The verifier requires all 92 dates and all 3,496 point records. It recomputes raw-file hashes, verifies the calendar and model lead sets, checks cycle and valid times, recalculates every daily high under both NBM windows, checks GRIB markers, and confirms that the archive contains no market or outcome fields. Its result is written to `data/manifests/friend_method_weather_v1_verification.json`.

## Known limitations

Archive presence and a 00:00 UTC initialization establish a conservative as-of basis, but the original vendor receipt timestamp is not independently available. The test should describe these as historical model files available from the archives, not as proof of the user's exact real-time receipt.

The NAM point subsets are served by Iowa State Mesonet's archive of NOAA NAM218 because NCEI's historical subset endpoint failed during source testing. The response is retained byte for byte and identified by provider in every record.

GFS TMAX is a six-hour maximum product. Its intervals do not align exactly with every fixed-PST climate-day boundary. The primary leads reproduce the supplied implementation convention, while the extra boundary leads are retained so that a sensitivity analysis can test whether that convention matters.

NBM and NAM daily highs are maxima over discrete forecast hours. They do not reconstruct sub-hourly temperature peaks. This is consistent with the friend method's available forecast grid, but it remains a source of forecast error that the chronological calibration must absorb.

The 92 dates are already exposed development history in this project. A positive result from this archive would rank the method as a development lead; it would not be independent confirmation or authorization to trade. The protected 28-day confirmation labels remain outside this acquisition and must not be opened merely because the exact weather data is now available.
