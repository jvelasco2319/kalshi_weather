# Model and Trading Algorithm

## Weather Target

For LA daily high markets:

```text
Y = official daily maximum temperature at KLAX/LAX from the NWS CLI-style climate report
```

For a Kalshi bracket:

```text
P(lo <= Y <= hi)
```

## Intraday High-Temperature Logic

The official daily high cannot be lower than the observed high so far.

```python
settlement_high = max(observed_high_so_far, future_high_sample)
```

Then bracket probability is:

```python
p = mean(lo - 0.5 <= settlement_high < hi + 0.5)
```

Open-ended brackets:

```text
65 or below: settlement_high <= 65
74 or above: settlement_high >= 74
```

Verify exact bracket boundaries from Kalshi titles/subtitles.

## Baseline Model v0.2

Current stored model version:

```text
v0.2-openmeteo-per-model-normal-residual
```

Open-Meteo is queried one model at a time. Generic `temperature_2m` columns
returned by a successful model request are renamed internally to
`temperature_2m__{model}`. If all configured model-specific requests fail, the
client uses a generic no-model fallback named `temperature_2m__best_match` and
records every model failure for debugging.

Use:

```text
future_high_center = blended max of successful Open-Meteo model hourly temperature_2m columns
future_high_sample = Normal(future_high_center, residual_sigma_f)
settlement_sample = max(observed_high_so_far, future_high_sample)
```

Start with `residual_sigma_f = 1.0 F` and calibrate after enough joined
prediction/outcome rows exist.

Every stored prediction includes series, station, market date, market ticker,
bracket bounds, `p_yes`, YES/NO bid/ask, YES/NO edge, observed high so far,
latest observation time, model future high, model details, residual sigma,
Monte Carlo sample count, and model version.

## Better Model v1

Add model-specific residual history:

```text
error = official_CLI_high - model_predicted_high_asof
```

Group by:

```text
station_id
month
lead_hours
asof_hour_local
marine_layer_features
```

Use empirical residual sampling, quantile regression, or isotonic calibration.

## Fake-Money Trading Logic

For each bracket:

```text
q_now = model P(YES)
yes_bid = executable sell price for YES
yes_ask = executable buy price for YES = 1 - best_no_bid
```

Terminal edge:

```text
buy YES if q_now - yes_ask > fee_buffer + model_error_buffer + required_edge
buy NO  if (1 - q_now) - no_ask > fee_buffer + model_error_buffer + required_edge
```

Micro-trading edge:

```text
expected_flip_edge = expected_exit_bid - entry_ask - fee_buffer - risk_buffer
```

The first paper version can use terminal-edge signals. A later version can add
`q_next` around catalysts such as new observations.

## Exit Logic

Sell fake positions when:

```text
profit target reached
model fair value deteriorates
market bid reaches fair value minus risk discount
max hold time exceeded
stop loss reached
```

## Outcome and Calibration Flow

Official outcome ingestion targets the NWS CLI-style daily climate report for
KLAX/LAX. Automatic fetch is best-effort; manual outcome recording is available
when the NWS product is unavailable or unparseable.

Settlement logic:

```text
range: lower <= official_high <= upper
below: official_high <= upper
above: official_high >= lower
```

Calibration reports use joined prediction/outcome rows and report Brier score,
log loss, average predicted probability, empirical YES rate, buckets, bracket
summary, model-version summary, and market-date summary.

## Phase 3 Refinements

The LAX market date is the date of fixed UTC-8 local standard time, not the
America/Los_Angeles daylight-saving calendar date. During PDT, the NWS climate
day runs from 01:00 local wall time to 01:00 local wall time the next day.

The Open-Meteo forecast window starts at current America/Los_Angeles wall time
and ends at the fixed-standard climate-day end converted back to wall time.

Open-Meteo probing checks candidate model identifiers with a minimal
`temperature_2m` request. The currently working same-endpoint identifiers are
used as evidence for provider configuration; rejected identifiers are reported
instead of hidden.

Opportunity diagnostics compute YES/NO terminal edges and compare the best edge
to the configured hurdle:

```text
required_hurdle = min_edge + fee_buffer + model_error_buffer
```

Paper state persists through SQLite. `paper-once` and `run-paper` resume latest
cash and open positions unless `--reset-paper` is supplied, in which case a reset
event is recorded and the fake account starts from configured cash.

`residual-report` is intentionally descriptive, not a trained model. It reports
`official_high_f - model_future_high_f`, grouped summaries, and a suggested
residual sigma only when enough joined rows exist.

Live trading remains disabled. No Kalshi create-order endpoint is implemented.

## Phase 4-7 Model Notes

The default model version is `v0.3-openmeteo-weighted-normal-residual`.
Preferred Open-Meteo components are `gfs_seamless`, `gfs013`, `gfs_global`,
and `best_match`, with configurable weights. The selected future high is stored
with `future_max_by_model`, selected components, weights used, successful/failed
models, and fallback status.

`v0.4-calibrated-residual-sigma` is reserved for applying residual sigma from
joined rows once enough production evidence exists. Small samples produce
warnings and should not be treated as edge.

`demo-fixture-model` is for offline plumbing demos only.
