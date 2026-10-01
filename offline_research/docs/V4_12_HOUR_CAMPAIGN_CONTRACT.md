# V4 twelve-hour offline campaign contract

Status: **preregistered; implementation, readiness, and launch are still required**  
Registered: September 26, 2026 Pacific time  
Machine-readable registration: [`configs/v4_offline_campaign.json`](../configs/v4_offline_campaign.json)

## Purpose and interpretation

V4 is a bounded twelve-hour continuation of the offline KLAX research program. It addresses the specific V3 coverage defect: all 60 V3 candidates used a 12:00 UTC decision time and a 4°F maximum interval width, while nearly every market decision was rejected by spread, price-band, or quote-freshness controls. V4 must explore decision time, uncertainty width, quote age, spread, and price-band interactions before assigning most of its budget adaptively.

The economic target remains unchanged. Every selected trade must have at least 10% estimated net expected return on entry outlay, and a candidate must also deliver at least 10% aggregate capital-weighted simulated return plus every stability, stress, sample-size, forecast, replication, and critic gate inherited from V3.

The February 4 through June 30, 2025 scored development period has already informed V3 and the design of V4. Any positive V4 result on those dates is therefore **provisional adaptive discovery evidence**. Passing all gates earns the label `PROVISIONAL_V4_DEVELOPMENT_GATE_PASS`; it does not create a champion or establish expected future profit. Champion status requires a separate, preregistered exact-identity test on genuinely untouched data.

V4 does not read the protected July-December 2025 interval. It creates no final-evaluation authorization, paper order, live order, or actual-profit claim.

## Outcome-blind opportunity funnel

Before any new candidate sees scored development results, deterministic code must analyze only the January 5 through February 3 calibration prefix. Settlement outcomes, simulated payouts, profit, expected return, CRPS, and Brier score are prohibited in this stage.

The funnel enumerates 768 policies from:

- 13:30, 15:00, and 18:00 UTC decision times;
- 4°F, 6°F, 8°F, and 12°F maximum central-interval widths;
- 1, 5, 15, and 60 minute quote-age limits;
- 5, 10, 15, and 25 cent spread caps; and
- the four existing price bands: 5-80, 10-85, 15-90, and 20-95 cents.

The funnel does not discard a registered plan. It orders the 64 quote-age by spread by price-band triples using total distinct-day coverage across all twelve decision-time by width cells, followed by deterministic tightness and hash tie-breaks. Zero-opportunity policies remain negative evidence and run after policies with observed calibration-prefix opportunities. Scored returns may not alter this funnel order.

The resulting artifact must report all 768 policies and contain only opportunity count, distinct-day coverage, quote freshness, spread burden, the registered control values, and deterministic selection fields. This stage separates “historical market evidence exists at this time” from “the eventual strategy was profitable.”

## Iterative search sequence

The registered universe contains exactly 3,072 plans: four model tracks crossed with three decision times, four interval widths, four quote-age limits, four spread limits, and four price bands. Every executed candidate must be a previously untested member of this universe.

Each twelve-candidate epoch has fixed roles: six forced-coverage plans, two evidence-guided deepen plans, one cross-track or cross-control combination, one independent alternative, and two adversarial challenges. If an adaptive role has no valid distinct proposal, its slot takes the next forced-coverage plan. This preserves iteration without allowing favorable scored results to expand the search language.

The forced queue uses the outcome-blind funnel order plus an orthogonal round-robin over every axis. Each completed eight-epoch block places 12 forced candidates in every four-level axis value and 16 at every decision time. Adaptive choices cannot consume capacity needed to satisfy a still-reachable coverage floor. This prevents parent ranking from repeating V3's starvation of later decision times, wider intervals, and longer quote ages.

For candidates with no selected trade, allocation ranking uses opportunity-funnel depth and rejection evidence before ROI: distinct days and decisions reaching the expected-return screen, days reaching market screening, normalized spread/price/freshness rejections, then quote age, spread, forecast scores, and plan hash. This ranking only decides what to study next. It never counts as return or a promotion gate.

The adaptive slots use saved evidence differently. Deepen changes one axis near a supported parent and targets its main rejection bottleneck. Combine takes a model track from one supported parent and execution controls from another. Alternative maximizes registered-axis distance from evaluated work. Adversarial slots try to falsify a current leader or diagnosis with stricter controls, an adjacent time, or another model track.

The six V3 colonies remain in place: settlement measurement, local weather, ensemble probability, market behavior, execution and abstention, and adversarial alternatives. Four coordinating agents may work concurrently, but the verified local inference boundary remains one model process at a time unless a later readiness artifact proves another limit safe.

Every candidate slot is sent through the V4 local GPT-OSS protocol as one or more complete host-curated `ResearchPlanV4` options. The worker may nominate only an exact option index; it cannot edit a field or introduce another plan. Each initial nomination and every retry is charged inside the single-process model-call guard and recorded in the call journal. If every charged attempt rejects or abstains, the host admits the same already-registered whole plan and records that fallback; malformed output or an identity mismatch remains an integrity stop. Every fourth completed epoch uses a separately charged synthesis call. Its typed content, source digest, packet hash, and artifact hash must be embedded in the next epoch's digest. Synthesis has no deterministic fallback.

## Twelve-hour limits

The campaign ceiling is 43,200 wall-clock seconds, 256 epochs, 3,072 distinct executed candidates, 3,300 local model calls, and 54,067,200 reserved context tokens. Each epoch may execute no more than twelve new candidates. Paid API spending remains zero.

These limits use the clean V3 successor runtime as evidence: 50 new candidates in 825 seconds, or approximately 218 candidates per hour. Linear twelve-hour capacity is approximately 2,618 candidates. The 3,072-plan universe would require roughly 14.1 hours at that rate, so the twelve-hour wall clock is expected to control. The larger candidate, epoch, and call ceilings remain hard safety limits and allow normal variation in throughput. The call limit reserves 3,072 candidate-nomination calls, 64 synthesis calls, and 164 transient retry calls. Numerical evaluation itself is deterministic and does not consume a model call.

The clock includes funnel construction, inference, evaluation, retries, checkpoints, replication, criticism, and finalization. Resume logic may not refund elapsed time, calls, candidates, or tokens. Reaching twelve hours is a stop condition, not a reason to skip terminal verification.

## Promotion gates

V4 preserves every V3 gate:

1. Every selected trade has at least 10% estimated net expected return on entry outlay.
2. Aggregate capital-weighted simulated net return is at least 10%.
3. Return remains strictly positive with the 10% fee proxy and an additional $0.02 adverse price per contract.
4. At least four of five chronological folds have strictly positive return.
5. The one-sided 95% fold-stratified, settlement-day bootstrap lower bound is strictly positive, using 10,000 resamples and seed `20260925`.
6. There are at least 30 trades on 30 distinct settlement days, with at least five trades in every fold and no more than one purchase per event.
7. CRPS and Brier score are no worse than the registered reference, and probabilities conserve mass.
8. Return remains positive after removing the most profitable day, with month, regime, price-band, side, and decision-time contribution tables.
9. An independent verifier reproduces identity, selections, outlay, fees, payouts, folds, bootstrap, CRPS, and Brier score.
10. The independent critic finds no unresolved look-ahead, settlement, probability, availability, fill, concentration, identity, or partition defect.

There is no discretionary override. A missing metric fails its gate. The campaign stops early for either of two economic results: one exact candidate passes every gate, or one candidate has strictly positive primary capital-weighted return and passes every gate except the 10% aggregate-return threshold. The latter receives `PROVISIONAL_ROBUST_POSITIVE_GATE_PASS`; the 10% target remains unmet. A weaker positive return is recorded and the campaign continues. Required integrity failures and hard resource limits also stop execution.

## Terminal conclusions

V4 must end with exactly one of these conclusions:

- `PROVISIONAL_V4_DEVELOPMENT_GATE_PASS`: an exact candidate passed every unchanged gate on adaptively reused development data and is frozen for future untouched confirmation;
- `PROVISIONAL_ROBUST_POSITIVE_GATE_PASS`: a candidate produced strictly positive primary return and passed every gate except the 10% aggregate-return threshold, then was frozen for future untouched confirmation;
- `PROVISIONAL_POSITIVE_RETURN_ONLY`: no candidate passed every gate, but at least one produced strictly positive primary development return;
- `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`: no candidate produced strictly positive primary development return; or
- `INSUFFICIENT_EVIDENCE`: integrity, replication, critic, opportunity, or resource evidence prevents a valid conclusion.

The terminal report must disclose the full adaptive test count, all candidate identities, the best result even when it is nonpositive, the first positive result if any, the best all-gate progress, and every stop reason. Positive development performance must always be described as simulated and provisional.

## Readiness and launch boundary

This registration is not executable authorization. Before launch, implementation must add and test the V4 plan schema, outcome-blind funnel, balanced allocator, coverage accounting, checkpoint and resume logic, unchanged gates, terminal labels, and protected-final denial. A new readiness artifact must hash the code, frozen inputs, funnel rules, gates, budgets, and negative-capability checks. A one-use ticket may be issued only after that readiness passes.

Any source, dataset, cost, fill, split, gate, control-grid, budget, or protected-access change after readiness invalidates the ticket unless a prospective hash-bound amendment is independently verified. Disappointing returns cannot amend this contract during the run.
