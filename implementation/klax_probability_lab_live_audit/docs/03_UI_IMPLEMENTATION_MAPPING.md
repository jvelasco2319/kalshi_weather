# Approved UI implementation mapping

The exact visual reference is:

```text
ui_reference/approved_probability_lab_exact.html
```

Use the same panel order and interaction model unless the current application shell requires a narrow adaptation.

## Header and status chips

Backend fields:

```text
eventTicker
targetDate
mode
evaluationId
evaluatedAt
analysisState
executionState
finalReasonCode
models[].eligibility
models[].maturityState
```

Show live/replay mode, number of available model slots, number of contributing models, evaluation age, and order-path-disabled state.

## Controls

### Event

Source: current/next-day event list from the existing application.

### Evaluation/time

Source: evaluation index endpoint. In live mode, follow latest. In replay mode, select immutable ID.

### Contract

Source: `outcomeMap.brackets`, ordered by `order`. Never hardcode July 7 labels.

### Position side

Local view control only: Yes or No. It selects backend-provided side-specific fields.

### Equation model

Source: the exact five `models` rows. Missing models remain selectable or visible with unavailable status.

## Hero cards

### Conservative trade probability

Use the selected economics row's `pSafe`, or the selected mixture bracket's `pTradeYes`/`pTradeNo`. Do not recompute.

### Executable or quote price

Use `economics.price` and label according to `economics.priceBasis`.

### Probability required

Use `economics.requiredProbability`.

### Probability edge

Prefer a backend-provided value if persisted. A presentational subtraction of two backend values is acceptable only for display and must not drive eligibility. The backend decision remains authoritative.

### Modeled net ROI

Use `economics.modeledNetRoi`.

### Decision

Use `analysisState`, `executionState`, `finalReasonCode`, and `decision.reasonCode`.

## Physical-high scenario distributions

Inputs:

```text
models[].scenarioTemperaturesF
models[].scenarioWeights
models[].effectiveWeight
mixture.scenarioTemperaturesF
mixture.scenarioWeights
station.observedHighF
outcomeMap.brackets
```

Use weighted empirical bins, weighted ECDF, or backend-provided density bins. If a smooth line is rendered, retain weighted empirical support and do not invent a Gaussian sigma.

Show all five model legend entries even when unavailable.

## Model contribution ledger

Columns:

```text
model label
rawLiveStateF
residualMedianF
correctedPointF
historyCount
nEff
priorWeight
effectiveWeight
maturityState
eligibility
selected bracket pMean/pSafe
contribution to selected mixture, if persisted
freshnessSeconds
```

If a model has zero weight, show the exact cap or exclusion reason.

## Probability funnel

For selected market and side, display in order:

```text
mixture posterior mean
mixture-count conservative lower bound
weighted component conservative lower bound
final pTrade
required probability
probability edge
final decision/gate
```

Source: `mixture.bracketProbabilities`, selected `economics`, and final decision/gates.

## Equation trace

Filter `equations` by selected model, market, and side. Preserve the approved card layout. Show blocked rows with missing inputs.

Expected equation families:

```text
remaining_window_max
raw_live_state
residual_correction
corrected_model_state
model_posterior_mean
model_conservative_bound
mixture_weight
mixture_probability
final_ptrade
required_probability
exact_fee
expected_value
modeled_roi
max_acceptable_price
kelly_or_risk_size
```

## Bracket probability matrix

Rows:

```text
ECMWF IFS
GFS 0.13°
GFS Seamless
NAM
NBM
weighted mixture mean
final conservative pTrade
```

Columns: verified ordered outcome map.

Allow view modes:

```text
posterior mean Yes
conservative Yes
posterior mean No
conservative No
```

## Market versus weather

For every bracket, compare:

```text
mixture posterior mean
final pTrade
selected-side market price
required probability
```

Use the selected side consistently. Clearly label price basis.

## Price sensitivity

Inputs:

```text
economics.priceSensitivity
economics.pSafe
economics.price
economics.maxAcceptablePrice
economics.activeHurdle
market.feeRole
market.feeScheduleVersion
```

The browser only plots server rows.

## Calculation and data health

Show separate statuses for:

```text
five model feeds
strict as-of validity
observation health
calibration readiness
NBM maturity
outcome-map verification
settlement-rule version
fee verification
model spread
drift gate
book state and age
sequence validity
price basis
portfolio risk
order path disabled
```

Do not collapse all failures into `DATA INCOMPLETE`.

## Explanatory section

Preserve the approved three-part explanation:

1. distribution rather than point estimate;
2. `pTrade` versus required probability;
3. final decision reason and gates.

## Accessibility and responsive behavior

- semantic headings and controls;
- accessible chart labels or adjacent data tables;
- keyboard-focus states;
- sufficient contrast;
- no horizontal overflow at approximately 390 px;
- desktop layout tested near 1440–1600 px;
- charts resize without clipped labels;
- unavailable values use text, not color alone.
