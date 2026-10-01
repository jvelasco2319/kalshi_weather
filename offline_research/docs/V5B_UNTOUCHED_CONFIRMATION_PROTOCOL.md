# V5B untouched confirmation protocol

## Purpose

This is a one-shot historical confirmation of the frozen V5B NO strategy. It is designed to determine whether the V5B development result transfers to data whose settlement outcomes were not used to choose the strategy. It cannot modify the strategy, search for a replacement after outcomes are opened, or authorize trading.

## Frozen primary method

The only primary method is V5B candidate `c4d9b5adac5b672b56850f7f840bce1b266ad06a8925849dde7af5a9a1ec5b6d`. It buys at most one NO contract per date at the 18:00 UTC decision point. Its probability source, gap rule, price limits, spread limit, fee calculation, 10% model-implied expected-return screen, tie-breaking, and evidence-grade policy are inherited exactly from the immutable V5B strategy freeze.

V5F and all other methods remain diagnostics. They cannot replace V5B after confirmation results are known.

## Untouched calendar pool

The planned primary pool contains 78 dates:

- May 9–31, 2026: 23 dates before the exposed V5A/V5B development period and within Probalytics' advertised Kalshi full-depth archive.
- August 4–31, 2026: the 28-date sealed V5A holdout. Its metadata may be read; its outcomes remain unopened.
- September 1–27, 2026: 27 completed dates after the exposed development period.

The noncontiguous windows must be disclosed in the result. Seasonality is limited to late spring, summer, and early fall. A separate exposure audit must show that none of these outcomes were used for strategy design.

## Readiness before outcomes

For every date, the preparation lane must bind:

1. HRRR and GEFS inputs available by the frozen 18:00 UTC policy.
2. Frozen V4 bracket probabilities produced without refitting.
3. Historical Probalytics Level-2 snapshots near 18:00 UTC, with continuity, timestamp, depth, and size retained.
4. Exact KXHIGHLAX contract definitions, KLAX settlement mapping, and the effective historical fee schedule.
5. An execution grade of A, B+, B, or unavailable. B and B+ remain simulated execution evidence and cannot be called verified fills.

The entire universe and every selected contract must be frozen before any settlement outcome is loaded. If the outcome-blind selector finds fewer than 30 trades, the confirmation stops without opening outcomes. The shortfall is an insufficient-sample result; it is not permission to loosen the strategy.

## One-shot evaluation

If all readiness checks pass and at least 30 selections are frozen, official CLILAX outcomes may be loaded once. The primary result passes only if all of these conditions hold:

- at least 30 selected dates;
- aggregate net return on entry outlay of at least 10% after exact historical fees;
- at least four of five chronological folds are positive;
- the worst nonempty fold is at least -10%;
- a fixed-selection two-cent adverse-entry stress remains positive;
- return remains positive after removing the best profit day;
- multiclass Brier score is no worse than the frozen forecast reference;
- execution evidence, available size, latency, and grade composition are reported separately.

Development, confirmation, and any lower-quality extension must be reported separately. Failure, a positive result below 10%, or insufficient data are valid conclusions.

## Current outcome-blind finding

The sealed August holdout produces 12 selections across 28 dates: four Grade A and eight Grade B+. This is an outcome-blind count, not a return result. It shows that the original 28-day holdout cannot meet the 30-trade gate, so opening it alone would waste the one-shot test.

At the observed 12/28 selection rate, about 70 calendar days are needed to obtain 30 selections. The 78-date plan projects about 33 selections, but this projection is not evidence that the gate will be met. The actual count must be recomputed after all missing inputs are acquired.

## Safety boundary

Acquisition is finite and historical. Scoring is offline. No live feed, current-market decision, paper order, or live order is part of this protocol. Credentials are entered interactively and are never saved in the project.
