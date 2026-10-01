# V6.1 Settlement-Aligned, Market-First Research Campaign

Version 1.0 | Registered September 28, 2026 | KLAX historical research only

## Decision this campaign must make

V6.1 asks one narrow question:

> Do settlement-aligned weather forecasts, observations, and forecast revisions add predictive information beyond a coherent 18:00 UTC Kalshi market probability, and can that information support at least 10% net expected return and 10% historical simulated realized return under Grade-A execution evidence?

The completed V6 tournament is immutable. V6.1 is a successor because the prior V6 compared weather methods with an inherited weather-probability chain rather than with the market itself. The V5B confirmation loss is also immutable evidence. It showed that an apparently strong development policy can fail when its probability model is overconfident, its settlement sources are pooled, and most entries rely on weaker execution evidence.

No edge, insufficient data, and non-executable information are valid results. The campaign may not search until it finds profit. It is historical and offline after finite acquisition. It cannot place paper or live orders.

## Current evidence and launch state

Available locally:

- 142 consecutive HRRR/GEFS feature dates from May 9 through September 27, 2026.
- 92 June-August dates with contract rules and paid Level-2 evidence.
- 64 repeatedly exposed development dates from June 1 through August 3.
- 46 dates with strict Grade-A evidence across the paid June-September archive, before exposure rules.
- Exact historical fee binding for the tested series.
- 2024 through July 2, 2025 ASOS/METAR observations for KLAX, KHHR, KLGB, KSMO, and KTOA.

Missing or inadequate:

- 2026 as-of local observations for the five stations.
- A numeric Weather Company settlement-temperature archive; resolved contract labels can be used, but source-temperature MAE and CRPS must be disabled when the numeric target is unavailable.
- At least 100 independent, genuinely confirmation-eligible Grade-A weather days.
- A preregistered coherent market-probability projection and a settlement-source-specific market-plus-weather model.

The current launch mode is therefore `DEVELOPMENT_ONLY`. Confirmation-label access is disabled. Development uses the already exposed June 1-August 3 cohort. Final confirmation must come from a new, outcome-blind cohort that has not influenced any prior campaign.

## Four colonies

### 1. Settlement forecasting

This colony first solves the easier forecasting problem. It maps each contract to its exact station, timezone, bracket, rule revision, and settlement source. NWS and The Weather Company targets remain separate. It compares uniform, source-season climatology, HRRR-only, GEFS-only, calibrated HRRR/GEFS, and local-observation correction models.

It advances only models that beat uniform, climatology, and the frozen inherited weather baseline on both multiclass Brier score and clipped log loss. When an exact ordinal settlement temperature exists, CRPS is also required. Every prediction must use only information available by its registered decision time.

### 2. Market residuals

This colony constructs six mutually exclusive bracket probabilities from the contemporaneous Kalshi bid/ask book. A fixed projection creates probabilities that sum to one when spreads produce inconsistent intervals. That rule is frozen before outcomes are read.

The colony then tests whether weather adds information to the market:

1. market only;
2. weather only;
3. market plus weather;
4. market plus weather, source identity, and available local observations;
5. registered adjacent-bracket and forecast-revision variants.

Market plus weather advances only if it improves both Brier score and log loss versus market only, the paired moving-block bootstrap lower bounds are above zero, at least four of five chronological folds improve, and the last two folds improve when combined.

### 3. Execution economics

This colony converts a forecast advantage into a bounded historical decision rule. YES, NO, adjacent-bracket, revision, and abstention policies are separate registered candidates. It applies exact historical fees, displayed size, entry latency, and a maximum of one selected trade per weather day.

Grade A is the primary economic leaderboard. B+ and B are diagnostic only. A Grade-A backtest is still an execution-aware counterfactual simulation, not proof that an order would have filled.

### 4. Adversarial validation

This colony tries to make every apparent result disappear. It owns nested walk-forward selection, block bootstrap uncertainty, multiple-testing correction, source/month/regime concentration checks, leakage sentinels, time shifts, label shuffles, randomized contract mappings, the failed V5B policy, and an independent replay implementation.

Any shuffled, randomized, or future-data sentinel that appears to pass terminates the campaign with `INTEGRITY_FAILURE`.

## Frozen research sequence

### Phase 0: preservation and exposure accounting

1. Preserve the completed V6 and V5B artifacts.
2. Build a hash-bound global exposure ledger by climate date and contract scope.
3. Record feature, price, label, outcome, selection, diagnostic, and reporting access separately.
4. Deny confirmation eligibility to every date that influenced prior selection or whose outcome was opened.
5. Keep the V3 protected final interval sealed.

May 9-31, June 1-August 31, and September 1-27, 2026 are development-only or permanently exposed for future final confirmation because those pools influenced V5A/V5B/V6 research. The 30 V5B confirmation dates are also outcome-exposed. Old holdout seals cannot override this ledger.

### Phase 1: finite historical acquisition

Acquire and freeze only registered historical inputs:

- 2026 KLAX/KHHR/KLGB/KSMO/KTOA ASOS/METAR with conservative availability timestamps;
- contract rules and settlement-source identity;
- additional paid historical Level-2 book dates with sequence and size evidence;
- exact fee schedules and rule-effective dates;
- resolved labels, kept inaccessible for any proposed confirmation set.

Every acquisition has date, request, byte, retry, and storage caps. Once frozen, the experimental process has no network path.

### Phase 2: easier precursor problems

The settlement colony must pass exact bracket mapping and forecast-quality gates before any profit optimization. NWS and Weather Company results are never silently pooled. The market colony must produce a coherent pre-outcome probability vector for every scored date. Missing brackets, stale books, and post-decision snapshots force abstention or date exclusion under frozen rules.

### Phase 3: incremental information

Use five chronological outer folds. Candidate selection occurs only inside each training portion. Every scoring date remains in forecast evaluation even if the economic policy abstains. Market-plus-weather must beat market-only on paired dates and pass registered negative controls.

### Phase 4: cross-pollination

Only mechanisms that passed their precursor gate may be combined. The registered synthesis paths are:

- source-specific calibrated weather plus coherent market probabilities;
- forecast disagreement plus market entropy;
- local-observation correction plus marine-layer regime;
- adjacent-bracket relative value plus calibrated joint probabilities;
- forecast revision plus a registered arrival-time window.

An outer-fold result cannot create a new feature, threshold, or candidate. Any new hypothesis requires a later campaign.

### Phase 5: executable economics

Evaluate exact fees, displayed size, and entry prices. Report the base case plus one-, two-, and three-cent adverse price stress. Remove the best day, best month, and top 5% of profit days. Report NWS and Weather Company separately. At most one trade is selected per day.

### Phase 6: adversarial review

Run nested walk-forward replay, paired moving-block bootstrap, family-wise candidate correction, source/month/regime removals, and an independently written numerical reproduction. Preserve every failed candidate and behavioral duplicate.

### Phase 7: freeze or close

If a development candidate passes every gate, freeze exactly one strategy or one exact preregistered ensemble. Run a prospective power calculation and verify at least 100 confirmation-eligible Grade-A days before labels can be opened once. If the sample is smaller, close with `INSUFFICIENT_GRADE_A_SAMPLE` and keep labels sealed.

## Registered candidate catalog

The initial catalog contains 27 candidates and controls. Every feature set, threshold, weight, and policy counts as an attempt.

Settlement forecasting:

- `S00_UNIFORM_CONTROL`
- `S01_SOURCE_SEASON_CLIMATOLOGY`
- `S02_HRRR_SOURCE_SPECIFIC`
- `S03_GEFS_SOURCE_SPECIFIC`
- `S04_CALIBRATED_HRRR_GEFS`
- `S05_LOCAL_OBSERVATION_CORRECTION`
- `S06_REGIME_CONDITIONAL_ENSEMBLE`

Market residuals:

- `M00_MARKET_ONLY_CONTROL`
- `M01_MARKET_PLUS_CALIBRATED_WEATHER`
- `M02_SOURCE_SPECIFIC_RESIDUAL`
- `M03_MARKET_ENTROPY_DISAGREEMENT`
- `M04_ADJACENT_BRACKET_RELATIVE_VALUE`
- `M05_FORECAST_REVISION_1200_1500_1800`

Execution economics:

- `E00_PRICE_ONLY_CONTROL`
- `E01_YES_VALUE_POLICY`
- `E02_NO_VALUE_POLICY`
- `E03_ADJACENT_PAIR_POLICY`
- `E04_REVISION_ARRIVAL_POLICY`
- `E05_SIZE_AND_LATENCY_CONSERVATIVE`

Adversarial validation:

- `A00_PRIOR_DAY_WEATHER`
- `A01_LAGGED_MARKET`
- `A02_BLOCK_SHUFFLED_LABELS`
- `A03_DATE_SHIFTED_FORECASTS`
- `A04_SETTLEMENT_SOURCE_SWAP`
- `A05_RANDOMIZED_CONTRACT_MAPPING`
- `A06_FROZEN_FAILED_V5B`
- `A07_SYNTHETIC_OUTCOME_LEAK_SENTINEL`

Controls and sentinels cannot be promoted.

## Forecast gates

A promotable forecast mechanism must satisfy all of these on paired out-of-sample dates:

- lower multiclass Brier score than uniform, climatology, frozen weather, and market-only baselines;
- lower clipped log loss than market only;
- paired moving-block bootstrap lower bounds above zero for both market-relative improvements;
- improvement in at least four of five outer folds;
- improvement over the final two folds combined;
- no future timestamp, settlement-source pooling, or candidate-specific cohort removal;
- no negative control passes.

## Economic gates

A development candidate must satisfy all of these on Grade A only:

- at least 100 independent selected days for confirmation readiness;
- at least 15 selected days in every chronological fold;
- at most one trade per day;
- at least 10% capital-weighted net historical simulated return after exact fees;
- at least 10% mean model-implied expected net return;
- at least four of five folds positive;
- worst fold return at least -10%;
- positive return under two-cent adverse execution;
- three-cent stress reported;
- positive after the best day, best month, and top 5% of profit days are removed;
- positive source-specific result for every promoted settlement source;
- displayed size sufficient for the registered quantity;
- independent numerical reproduction.

For reporting, a `PROMISING_10_PERCENT_POINT_ESTIMATE` needs a point estimate of at least 10% and a one-sided 95% lower bound above zero. `CONFIRMED_10_PERCENT` needs the one-sided 95% lower bound to be at least 10% on one untouched, preregistered confirmation.

## Resource allocation

Four logical colonies work in parallel, while deterministic code owns data access, candidate admission, scoring, budgets, rankings, and stopping. After every phase, resources move away from failed branches and toward surviving mechanisms. Cross-pollination uses only saved evidence from prerequisite-passing branches. Agent agreement never substitutes for numerical reproduction.

The development run has a three-hour wall-clock ceiling after prerequisites pass. It may finish early when the finite catalog is exhausted, an integrity failure occurs, or a development candidate passes all gates and independent review is complete. The acquisition and readiness phase is separate and does not consume the experimental budget.

## Terminal conclusions

- `ROBUST_DEVELOPMENT_CANDIDATE_CONFIRMATION_PENDING`
- `CONFIRMED_10_PERCENT`
- `NO_SETTLEMENT_FORECAST_SKILL`
- `NO_INCREMENTAL_MARKET_INFORMATION`
- `NONEXECUTABLE_INFORMATION`
- `INSUFFICIENT_GRADE_A_SAMPLE`
- `NO_ROBUST_EDGE_WITHIN_REGISTERED_CATALOG`
- `INTEGRITY_FAILURE`

## Immediate launch decision

V6.1 starts in `PREREQUISITE_AND_MARKET_BASELINE_BUILD`. It may use the exposed June 1-August 3 cohort for development, but it cannot open any reserved outcome or claim confirmation. The first work products are the global exposure ledger, readiness record, settlement mapping, coherent market baseline specification, and 2026 local-observation acquisition manifest.

