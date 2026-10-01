# V5A paid-depth readiness goal

V5A is an outcome-blind continuation of the completed V5 source-capability
audit. It integrates the authorized Probalytics historical KXHIGHLAX order-book
archive for June 1 through August 31, 2026 without changing the frozen V5 or
V5P registrations, the frozen V4 probability leader, the 18:00 UTC decision
time, the five-second arrival delay, or any promotion threshold.

The purpose of V5A is to answer two separate questions without mixing their
standards:

1. Does the paid archive contain enough contemporaneous, quantity-bearing,
   gap-contiguous book evidence to satisfy the original V5 execution gate?
2. If the original gate is not satisfied, which dates and contracts may still
   be used for clearly labelled partial-evidence diagnostics?

## Frozen inputs

V5A binds the existing V5 registration and frozen probability leader, the
validated Probalytics source archive, its normalized full-book and top-of-book
Parquet files, the V5P historical acquisition state, and the V5P fee and
settlement evidence. It also binds an outcome-blind event-rule manifest for all
92 dates. That manifest uses Probalytics only to recover missing contract
identifiers, then obtains the rule text and contract bounds from Kalshi's
official historical market records. It records the settlement-source change
from the NWS Climatological Report through August 13 to The Weather Company
starting August 14. V5A reads no settlement outcome, CLILAX product text,
protected label payload, or candidate result when it builds execution
eligibility.

Every KXHIGHLAX contract side at the registered five-second arrival point is
retained before any outcome is opened. A row receives Grade A only when a
verified pre-arrival full-book state is no more than five seconds old, its
sequence continuity is `CONTIGUOUS`, and at least one contract is displayed at
the best ask. A verified full-book state no more than 60 seconds old with an
unknown continuity chain is Grade B+. All other rows are unavailable for an
assumed marketable purchase. Grade B+ is useful for sensitivity analysis but
does not establish a promotion-grade fill.

## Restart rules

At the user's direction, V5A uses a reduced 92-day research standard. All 92
June-August dates may be scored by the probability colony once their weather
inputs are complete. Execution is evaluated with an evidence ladder: Grade A
is strict full-book evidence, Grade B+ is paid full-book evidence with weaker
continuity, and Grade B is a one-minute quote proxy when a paid book is absent.
Dates without an execution price remain in probability scoring and become
explicit execution abstentions. Results are always broken out by evidence
grade. A finding based on Grade B or B+ is a partial historical result rather
than a verified-fill result.

The original V5 campaign may restart only if every frozen V5 gate passes. In
particular, the archive must make at least 120 probability events possible,
provide at least 90 percent outcome-blind promotion-grade event-window
coverage, support at least 30 selected trades across 30 dates and five folds,
bind every selected trade to exact fees and the exact historical event-rule
revision, and finish the later HRRR/GEFS, Kalshi, and CLILAX acquisition before
the selected universe is frozen. Passing a partial-evidence threshold never
waives an original V5 gate.

A separate 92-day offline V5A analysis may begin after the finite source
acquisition is complete and an outcome-blind selected-trade universe has been
frozen. It must preserve Grade A, Grade B+, Grade B, and abstention labels;
report the actual sample size; use direct-taker fee treatment with zero rebate;
keep protected outcomes sealed until the freeze; and make no claim of verified
profitability when an original V5 gate remains unmet. Samples of 1-9 selected
trades are anecdotal, 10-29 are preliminary, and 30 or more are supported
partial evidence.

## Outputs

The deterministic builder writes:

- `data/manifests/v5a_paid_execution_outcome_blind.json`, containing all
  five-second contract-side execution states and source hashes;
- `data/manifests/v5a_paid_depth_readiness.json`, containing the original V5
  gate verdict, partial-analysis readiness, exact blockers, and safety state;
- `data/manifests/v5a_event_rules_outcome_blind.json`, containing the complete
  92-date event-rule partitions and settlement-source transitions without any
  outcomes or settlement values.

The outputs are content-addressed and must state that no network connection,
protected-label read, live feed, paper order, or live order occurred. V5A does
not authorize trading.
