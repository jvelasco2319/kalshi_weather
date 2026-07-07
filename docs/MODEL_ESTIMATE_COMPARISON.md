# Model Estimate Comparison

This phase adds side-by-side model estimates for KLAX high-temperature markets.
It does not replace the current production model, does not blend new models into
trading logic, and does not place trades.

## What Changed

The existing current model remains the baseline:

- provider: `current`
- model id: `current_weighted_blend`
- source: the already-working Open-Meteo weighted forecast pipeline

The comparison layer also exposes individual Open-Meteo feeds:

- `gfs_seamless`
- `gfs013`
- `gfs_global`
- `best_match`

Optional direct NOAA estimates are available through Herbie when optional
dependencies and live model data access are installed:

- HRRR: short-range, high-resolution regional model
- NBM: blended calibrated national guidance
- GFS: global baseline model
- RAP: short-range regional model

If Herbie, cfgrib, ecCodes, xarray, network access, or a NOAA product is not
available, the command reports that model as unavailable and continues.

## Why It Exists

The goal is to answer:

> What does each model think the official KLAX high will be, and what bracket
> probabilities does that imply?

This lets us compare disagreement between models before changing the trading
model. Disagreement matters because microtrade opportunities are most dangerous
when a single model appears confident but other credible guidance disagrees.

## Commands

Probe provider availability:

```powershell
kalshi-weather model-provider-probe --station KLAX
kalshi-weather direct-noaa-check --station KLAX
kalshi-weather direct-noaa-check --station KLAX --json --output reports/latest_direct_noaa_check.json
```

Show high-temperature estimates:

```powershell
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --show-failures
```

Show estimates plus bracket probabilities:

```powershell
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --include-probabilities --show-failures
```

Show bracket probabilities grouped by model:

```powershell
kalshi-weather model-probabilities --series KXHIGHLAX --station KLAX
```

Store comparison rows without touching production prediction rows:

```powershell
kalshi-weather model-estimates --series KXHIGHLAX --station KLAX --include-probabilities --store --show-failures
```

Read the same comparison in a compact human-analysis format:

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges
```

`simple-summary` uses the same comparison estimates and probability logic, but
prints a cleaner model-estimate table, bracket-probability matrix, model
agreement status, and short warnings. It is analysis-only and does not change
the production model or create paper fills.

Score stored model estimates after official outcomes exist:

```powershell
kalshi-weather model-estimate-score --station KLAX
```

Optionally store comparison rows during collection:

```powershell
kalshi-weather collect-once --series KXHIGHLAX --station KLAX --include-model-estimates
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60 --include-model-estimates
```

## Optional Direct NOAA Setup

The base package does not require heavy weather-model dependencies. To try
direct NOAA/Herbie models:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_direct_noaa_models.ps1
```

Direct NOAA model access may still require working GRIB/cfgrib/ecCodes support
and reachable model archives. Missing optional dependencies are reported as
unavailable provider rows, not as a project failure.

## Direct NOAA Model Locations

- HRRR: Herbie model `hrrr`, product `sfc`, field `TMP at 2 m`, same-day short-range.
- NBM: Herbie model `nbm`, product `co`, field `TMP at 2 m`, calibrated blend baseline.
- GFS: Herbie model `gfs`, product `pgrb2.0p25`, field `TMP at 2 m`, global baseline.
- RAP: Herbie model `rap`, product `awp130pgrb`, field `TMP at 2 m`, short-range regional backup.

`direct-noaa-check` prints dependency status, configured Herbie targets, and a
live estimate/failure row for each direct NOAA model. If a row is unavailable,
read the `error_message` as provider/runtime information. It does not mean the
current Open-Meteo production path failed.

## How To Read The Output

`future_high_f` is the model's maximum 2-meter temperature over the remaining
KLAX fixed-standard-time climate-day window.

`settlement_high_estimate_f` is:

```text
max(observed_high_so_far_f, future_high_f)
```

The probability commands use the same normal-residual probability method as the
current model. That means:

```text
same probability model, different weather-model input
```

This is deliberately not a new trading model.

## Scoring

After official NWS climate outcomes are stored, `model-estimate-score` compares
each stored model estimate to the official high:

```text
error = official_high_f - settlement_high_estimate_f
```

Positive mean error means the model was too cold. Negative mean error means the
model was too warm. Small samples are labeled as small samples.

## Hard Rule

Do not blend these models into trading logic, loosen thresholds, or consider
real-money trading until enough official outcomes show that the comparison model
improves accuracy and paper replay/market benchmarks support an actual edge.

## Fake-Money Model Race

`paper-model-race-once` and `paper-model-race-run` use the same comparison
model estimates and probabilities, but keep every model in its own fake-money
account. The race is meant to answer:

```text
Which model would have made better paper microtrading decisions?
```

It does not blend models, does not change the production model, and does not
place live orders. Direct NOAA rows can be unavailable without stopping the
race; unavailable models simply skip new entries for that update.

Read `docs/PAPER_MODEL_RACE.md` for the full interpretation guide.
