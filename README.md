# V10 Weather — online dashboard

This branch contains the current single-page V10 dashboard for the Kalshi
Los Angeles daily-high-temperature market. It uses public market and weather
data, keeps V10 fixed, and records **fake trades only**. It has no account
connection or exchange order endpoint.

## Set up and open

Use Windows, Git, 64-bit Python 3.12 or newer, and an internet connection.

```powershell
git clone --branch swarm_setup_online --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
.\scripts\setup_v10_dashboard.ps1
.\scripts\start_v10_dashboard.ps1
```

The dashboard opens at **http://127.0.0.1:8770/**. Keep the computer awake and
the server window running. The browser may be closed; automatic collection
continues while the server runs. Stop the server with Ctrl+C. Pause/resume is
also available in the dashboard and survives a restart.

If you already cloned this branch, pull its latest changes, rerun setup, and
start the dashboard. Setup verifies the frozen model and runs the tests.
A fresh clone has an empty local journal and a $100 practice wallet; existing
live captures and user journals are not published to GitHub.

## What you can see

- V10's most likely temperature range, estimated chance, and saved time.
- **V10 before the trade**: saved latest-run monitoring previews and their
  HRRR/GEFS issue times. Before the daily forecast is available, the outlook
  and probability comparison show the newest monitoring preview.
- Actual LAX temperatures alongside the latest captured HRRR and GEFS mean
  runs, their issue/save times and temperature error scores. A second tab shows
  **GFS, GFS Seamless, NAM, and NBM** beside the same observations.
- Clouds, cloud ceiling, wind, pressure difference, and temperature trend.
- V10 versus Kalshi implied probabilities for the six ranges, plus YES/NO
  entry prices. Missing or one-sided probabilities stay blank.
- A $100 practice account, a 10% entry budget, entries, estimated open value,
  and settled fake profit/loss. User-reported real trades are kept separate.
- **Tomorrow's market**, checked every 15 minutes with its own date and
  expandable prices. A market not listed yet is retried at the next check.
- **Monthly monitoring** of forecasts, automatic entries, skips, missed
  checks, forecast results, and settled fake profit/loss. Expand each day's
  status or select an earlier month to inspect its saved history.

GFS Seamless uses the same NOAA GFS run, not an independent model. Comparison
errors count only forecasts saved before their matching observations. These
curves and current weather displays do not refit or revise V10.
The HRRR/GEFS graph uses independent public NOAA captures, not V10 previews.
Only new complete runs are downloaded; an unavailable new run leaves the last
available curve visible. Scores retain the first saved forecast for each valid
time, so newer runs cannot rewrite earlier errors. Observation matches use the
nearest LAX report within 30 minutes and require capture before that report.

## Automatic timing

| Data or action | Timing |
| --- | --- |
| Today's Kalshi prices | Every 30 seconds |
| Tomorrow's market and airport reports | Every 15 minutes |
| New HRRR/GEFS run availability | Every 15 minutes; already saved runs are not downloaded again |
| V10 monitoring preview | Once a new complete HRRR or GEFS run is captured; separate history, never eligible for automatic trades |
| Other-model comparison snapshot | Daily at/after 6 AM Pacific |
| UI refresh | Every 15 minutes outside 10 AM–5 PM Pacific; every minute within that window |
| V10 staging | 17:45 UTC, 15 minutes before the cutoff |
| Daily V10 decision | 18:00 UTC: 11 AM PDT / 10 AM PST |
| Pending settlements | Hourly, beginning at noon Pacific the next day |

Monitoring previews keep the frozen V10 fitted weights and pressure transform,
but use an experimental latest-run input policy. The original valid-time grid
is retained: four HRRR temperatures and eight GEFS mean/spread pairs. Future
samples use the latest complete captured cycle; samples already in the past
retain earlier forecasts, bootstrapping from the original 06Z HRRR/00Z GEFS
cycles on first startup. Missing required temperatures or ensemble spread means
no new preview. Actual issue and receipt times are preserved, and pressure
reports retain the 15-minute availability lag and original selection window.

The registered daily forecast still uses its fixed 06Z HRRR/00Z GEFS inputs
at 18:00 UTC. Only that on-time forecast can feed the automatic fake-trade
selector. Monitoring previews cannot overwrite it or enter daily accuracy or
trade results. Official collection takes priority near the cutoff; new-run
checks resume afterwards. Neither the model nor its fitted tables are retuned.

Manual refresh displays new results immediately. Slow non-market source jobs
are deferred around the decision cutoff. Failed requests preserve the last
successful values, show an error, and use bounded retries.
HRRR publishes hourly runs; GEFS publishes 00/06/12/18 UTC runs, with publication
delays. Each display capture is bounded to 100 requests, 128 MiB and three
minutes. Automatic display captures are deferred from 30 minutes before the
V10 cutoff until three minutes after it so the daily decision keeps priority.

The registered window is **October 2, 2026–January 9, 2027**. October monitoring
covers **October 2–31: 30 eligible dates**. October 1's late preview is excluded.
Missing days are shown as missing and are not backfilled as on-time forecasts.
History survives server restarts, including results received after month-end.

## Automatic fake trades

After the daily cutoff, an eligible on-time V10 forecast is checked using the
fixed V8 NO selector: at least a 10-point gap between the two highest bracket
probabilities, a 5–80 cent NO ask, at most a 5-cent spread, and at least 10%
estimated net return after estimated fees. Selection occurs at the cutoff;
the same contract is then checked against a fresh post-cutoff quote. There is
at most one automatic entry per day, within two minutes of the cutoff. Each
entry uses up to 10% of available practice cash, including fees, and is held
to settlement. Otherwise the journal records the skip and its reason.

Public quotes do not demonstrate fills. These are simulations, not actual
orders or proof of profitability. Forecast accuracy and fake trading results
are separate. Current contracts use The Weather Company while V10 retains
its original NWS-based calibration, so this is an exploratory transfer test.
Settlement uses final Kalshi binary outcomes, not preliminary airport highs.

## Preserved inputs and local records

The unchanged frozen V4 weather model, V8 repair, and V10 pressure overlay are
verified against 34 input/code bindings before startup. Small fitted artifacts
and supporting integrity records are bundled under `frozen/v10_dashboard/`;
setup restores them byte-for-byte to their expected local paths. No fitting
or historical acquisition occurs. Byte-preserving Git attributes protect the
registered hashes across Windows and other platforms.

Live responses, receipts, forecasts, comparison captures, preferences, and
practice journals stay in ignored `runs/`. Preserve this directory locally
when moving your own existing records. Raw archives, credentials, runtime
registrations, and user journals are excluded from Git.

```powershell
# Open without a browser, or use a different local port
.\scripts\start_v10_dashboard.ps1 -NoBrowser -Port 8771

# Start paused
.\scripts\start_v10_dashboard.ps1 -NoTracking

# Verify saved model inputs without a network request
.\.venv\Scripts\python.exe .\scripts\prepare_v10_dashboard.py --verify-only
```

## Earlier three-method snapshot runner

The earlier V5B/V8/V10 comparison below is preserved separately. Its setup
uses Python 3.11 or newer. It writes `artifacts/online/dashboard.html` using
the older documented Open-Meteo transfer approximation. It does not drive
the new dashboard's practice journal or monthly forecast test.

This branch runs the same three retained research methods against the currently open Kalshi Los Angeles high-temperature market:

1. **V5B** — current HRRR/GEFS bracket probabilities plus the strict NO-side trade filter.
2. **V8** — the frozen six-bracket probability repair.
3. **V10** — V8 plus the frozen KLAX-versus-KDAG pressure-flow adjustment.

It fetches current public data and writes paper recommendations. **It cannot place orders:** the package contains no account credentials, order endpoint, or order-writing function.

## 1. Clone this branch

```powershell
git clone --branch swarm_setup_online --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
```

Requirements: Windows, Git, Python 3.11 or newer, and internet access.

## 2. Set it up

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_online.ps1
```

The setup creates `.venv`, installs the small dependency set, verifies the three frozen configurations, runs the tests, and takes one current snapshot.

## 3. Run one current snapshot

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_online.ps1
```

The new snapshot is written first, then the dashboard opens automatically at `artifacts\online\dashboard.html`.

To test a specific currently open date:

```powershell
.\scripts\run_online.ps1 -TargetDate '2026-10-02'
```

## 4. Keep the dashboard updating

```powershell
.\scripts\run_online.ps1 -Loop -IntervalSeconds 60
```

Leave the PowerShell window open. Stop it with `Ctrl+C`.

## What the graphic shows

- V5B, V8, and V10 action or abstention
- selected NO contract, model win probability, and fee-adjusted expected return
- HRRR high, GEFS ensemble mean/range, and their disagreement
- KLAX-minus-KDAG pressure gradient and V10 regime
- all six bracket probabilities and Kalshi top-of-book prices
- quote timestamps, the frozen 18:00 UTC test window, and the zero-order safety state

Raw snapshots are appended to `artifacts\online\journal.jsonl`; the latest machine-readable result is `artifacts\online\latest.json`.

## Fair-test rule

The runner works at any time, but only snapshots within five minutes of **18:00 UTC** are tagged as on-time tests comparable to the historical research. Other runs are observation-only.

Current HRRR/GEFS probabilities use a documented transfer approximation from public Open-Meteo feeds. V8 and V10 use the exact frozen transforms. Do not interpret results as an untouched confirmation until a new, preregistered sample is settled.

The historical-only version is the `swarm_setup_offline` branch. A setup prompt for another ChatGPT/Codex session is in [docs/CHATGPT_SETUP_PROMPT.md](docs/CHATGPT_SETUP_PROMPT.md).

If setup says Python is missing, install 64-bit Python 3.11 or newer from python.org and select **Add Python to PATH**. If setup cannot reach one of the public services, check the internet connection and rerun the same command; no partial run can place an order.

