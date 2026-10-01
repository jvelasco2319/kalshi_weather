# V5P Partial-Evidence Kalshi Weather Campaign

## Authorization and purpose

On September 27, 2026, the user authorized a new campaign that may use less
than the frozen V5 requirement of 90% historical execution-window coverage.
This is a separate campaign. It does not amend, replace, or reinterpret the
completed V5 result.

V5P asks whether the frozen HRRR/GEFS KXHIGHLAX probability leader retains a
positive historical edge when tested against every useful historical record we
can obtain without purchasing data or accessing a private account. The research
screen remains 10% net expected return per selected trade. A result below that
screen is still reported, including a positive result below 10%.

V5P is historical and offline after acquisition. It authorizes finite downloads
of public historical files. It does not authorize current-price polling, live
feeds, prospective predictions, paper orders, live orders, or broker actions.

## Why this campaign is separate

The original V5 protocol required promotion-grade Level-2 evidence for at least
90% of the July 1, 2025 through August 31, 2026 window. No verified source meets
that condition. The best commercial KXHIGHLAX archive begins in March 2026, and
the free public sources begin later or sample books too slowly.

V5P accepts incomplete evidence but never disguises its quality. Every result is
stratified by execution-evidence grade, source, month, and fold. It can produce
an exploratory or preliminary conclusion. It cannot produce the original V5
`ROBUST_EXECUTABLE_POSITIVE_AT_OR_ABOVE_10PCT` label.

## Frozen target

* Series: `KXHIGHLAX`
* Station: `KLAX`
* Decision time: 18:00 UTC
* Candidate: `v4-candidate-a779fd17e7b160650c8f`
* Research plan SHA-256:
  `a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461`
* Fitted model SHA-256:
  `9bf8b1ba469b1c7258108f5dd11c67c2822e7487edff5c819688d37319574f93`
* Model-state SHA-256:
  `7c677c1264115b39d73c70cfb7162b9be138ae1c9df9ac30df472426f2a57fd3`
* Primary quantity: one contract
* Primary return screen: 10% net return on entry outlay

The frozen candidate is evaluated first without retraining. Registered research
iterations may be developed only on the development portion and must be logged
before being tested on the holdout portion.

## Historical data window

The maximum acquisition window is July 1, 2025 through August 31, 2026. Actual
eligibility is determined from the intersection of:

1. HRRR/GEFS forecast availability;
2. KXHIGHLAX market identity;
3. price or book evidence near 18:00 UTC;
4. exact or bounded fee/rule evidence; and
5. a reconcilable settlement record.

The eligible universe is frozen and hashed before any corresponding outcome is
opened. Dates are not removed based on whether the strategy wins or loses.

## Evidence grades

### Grade A — replayable execution evidence

Timestamped Level-2 snapshots and deltas with price-level quantities, sequence
or state identity, gap records, and a reconstructable arrival state. Actual
member fills qualify only for the exact submitted order.

### Grade B+ — sparse depth snapshot

A historical full-book snapshot with displayed size but without the temporal
resolution or continuity needed to prove the five-second arrival state. PMXT
hourly snapshots are expected to fall in this grade. Results using Grade B+ are
reported as executable-price sensitivity, not verified fills.

### Grade B — aggregated quote

One-minute candlestick bid/ask observations. A registered conservative fill
rule may calculate a diagnostic result, but it remains an assumed fill.

### Grade C — public trade print

Observed public trades may confirm market activity and price plausibility. They
cannot prove a counterfactual fill.

Results from different grades remain separate. A weaker grade cannot be promoted
by combining it with another weak source.

## Public acquisition scope

The campaign may download and hash:

* NOAA HRRR and GEFS archive ranges using the frozen field plan;
* KXHIGHLAX metadata, one-minute candles, and public trades;
* historical CLILAX and official NOAA/NCEI settlement records;
* official fee schedules, rule filings, and fee-change metadata;
* free PMXT Kalshi order-book Parquet files near 18:00 UTC;
* public research datasets whose licenses permit local research; and
* free vendor samples for parser and schema validation.

Paid archives, authenticated member data, and live recorders require separate
authorization. The acquisition layer is isolated from the offline experiment.

## Four colonies

### 1. Execution Evidence

Reconstructs the best available historical price and displayed size at or near
the registered arrival time. It assigns evidence grade, staleness, distance from
18:00 UTC, sequence status, and source completeness. It runs five-second,
30-second, one-minute, and hourly-snapshot sensitivities where the source allows.

### 2. Fee and Settlement Integrity

Binds each selected trade to the strongest available fee schedule, rounding
rule, payout rule, settlement source, and rebate treatment. Unverified rebates
are zero. Unresolved maker/taker or participant classification is evaluated as
an explicit range and never resolved in the profitable direction.

### 3. Frozen Probability Validation

Recreates the frozen candidate probabilities using only forecasts available at
the registered decision time. It tests CRPS, Brier score, log loss, calibration,
mass conservation, and availability-delay sensitivities.

### 4. Sample and Regime Robustness

Owns the outcome-blind universe, chronological splits, sample-size label,
weather regimes, concentration tests, bootstrap uncertainty, and temporal
stability. It prevents post-outcome date selection.

## Sample labels

V5P runs with every nonzero eligible sample, but its claim strength depends on
independent selected settlement days:

* 1–9: `ANECDOTAL_PARTIAL_EVIDENCE`
* 10–29: `PRELIMINARY_PARTIAL_EVIDENCE`
* 30 or more: `SUPPORTED_PARTIAL_EVIDENCE`

No sample below 30 days may be described as a verified sustainable edge.

## Development and holdout

After the eligible dates are frozen, the earliest 70% are the development
portion and the latest 30% are a one-shot holdout. Research iterations may use
only development outcomes. The holdout is opened once after the strategy,
thresholds, fee envelope, execution rules, and evidence strata are frozen.

If fewer than ten eligible settlement days exist, the campaign reports source
coverage and descriptive outcomes only; it does not optimize.

## Iterative loop

The development loop follows the existing proposer, verifier, critic, and
synthesis pattern:

1. propose one falsifiable change;
2. deduplicate it against prior plans;
3. run deterministic scoring on development data;
4. reproduce the result independently;
5. run adversarial leakage, cost, timing, and concentration checks;
6. retain only Pareto improvements in return, calibration, stability, and
   evidence quality; and
7. write the next research brief from measured failures.

The loop has a maximum 43,200-second wall-clock budget after data readiness. It
may stop early for a terminal result, integrity failure, or exhausted distinct
hypotheses. Calls and elapsed time are never refunded on resume.

## Reporting metrics

For every evidence grade and for the combined diagnostic view, report:

* selected trades and independent settlement days;
* net expected return and realized capital-weighted return;
* ordinary and three-day block bootstrap intervals;
* chronological-fold and month returns;
* best-day removal and leave-one-fold-out returns;
* fees and price/staleness stress;
* probability calibration and scoring improvement;
* source coverage, missingness, and time distance from decision; and
* maximum drawdown and loss concentration.

## Terminal labels

* `PARTIAL_EVIDENCE_POSITIVE_AT_OR_ABOVE_10PCT`
* `PARTIAL_EVIDENCE_POSITIVE_BELOW_TARGET`
* `PARTIAL_EVIDENCE_NO_EDGE`
* `PARTIAL_EVIDENCE_INCONCLUSIVE_SMALL_SAMPLE`
* `PARTIAL_EVIDENCE_INCONCLUSIVE_EXECUTION_PROXY`
* `PROBABILITY_VALIDATION_FAILED`
* `INTEGRITY_FAILURE`

The first label means the available partial historical sample crossed the 10%
screen. It is not a guarantee and is not equivalent to the original V5 robust
execution label.

## Start and readiness rule

Acquisition starts immediately. Offline scoring may start as soon as at least
one complete eligible date is frozen, and it expands deterministically as more
historical partitions arrive. The one-shot holdout cannot run until acquisition
closes and the final eligible universe is hashed.

No campaign process may access a protected outcome before its source and split
bindings pass readiness. No process may place an order.
