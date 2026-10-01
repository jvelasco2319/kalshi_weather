# V6 multi-method research tournament

Status at freeze: implementation and tests complete; campaign not yet registered. The immutable registration artifact is the source of truth for launch status. Registration must bind this document, its method schema, configuration, supported method catalog, evaluator code and input inventory before any candidate is scored.

## Objective and scientific starting point

Determine whether available historical weather information adds economically useful information beyond historical market prices, or whether prior apparent edges arise from probability error, selection, execution assumptions or repeated development search. A sustainable 10% expected net return per purchased contract on entry outlay including fees is a target, not a guaranteed result.

Preserve V4/V4.1, V5/V5A and both completed V5B studies. V4's apparent signal lacked strong execution evidence; V5A remained temporally unstable. The first V5B pilot produced a positive development-screen candidate, but it did not provide independent confirmation. Its weather successor evaluated 24 model-policy combinations: all were negative despite some improved forecast scores. These findings motivate competing explanations and precursor tests, not scalar retuning of earlier winning-looking settings. Reused development observations do not become untouched evidence because the architecture changes.

OpenAI's public Navier–Stokes account describes opposing problem formulations, easier precursor problems, reassignment after useful precursor results, consolidation across groups and subsequent verification. V6 borrows those workflow ideas. Its trading gates, budgets, ranking and confirmation rules are our own protocol; it does not reproduce the internal OpenAI system or equate a backtest with formal proof. Source: https://openai.com/index/navier-stokes-solution/ .

## Four logical method groups

| Group | Mechanism to investigate | Counter-hypothesis |
|---|---|---|
| `conditional_uncertainty` | Weather-dependent residual uncertainty improves future contract probabilities | Sparse horizons and short calibration history produce unreliable conditional structure |
| `incremental_market_information` | Weather features predict errors beyond a defined market-probability benchmark | Market prices already contain that information; better weather forecasts do not imply an edge |
| `decision_execution` | Conservative decision rules retain information after exact fees and realistic entry stress | High estimated edges select model errors, stale quotes or weak execution evidence |
| `null_adversarial` | Independently reproduced mechanisms survive falsification | Repeated search, particular events, costs or evidence grades explain the result |

There is one coordinator and at most three concurrent worker agents: four total active slots, not four workers plus a coordinator. Logical groups rotate across worker slots. Agent identity, research instructions, hypotheses, findings and review messages must be recorded. Deterministic calculations are not counted as agent reasoning calls. Every discovery candidate receives review from another group; independent numerical reproduction cannot be performed solely by its proposing implementation.

## Shared cohort, information and baseline contract

Use the already exposed 64 chronological development dates, June 1–August 3, 2026. The first 20 are warmup; every forecast method scores the same following 44 dates, June 21–August 3. Trade abstention is allowed, but a method may not silently change its forecast evaluation cohort. No fold-specific tuning, calendar exclusions or label-defined regimes.

Enforce forecast issue/availability time no later than the 18:00 UTC decision, and official outcome publication before any use as a prior fitting label. Model source availability is presently supported by conservative bounds, not proven original publication timestamps. Preserve the separately documented five-second order-arrival assumption. Do not advertise quotes after18:00 as available exactly at18:00. The historical simulation must have no network fallback, live feed, credentials or order path.

Fit means, scales, model structure and residual calibration chronologically. Each scored fit requires at least20 prior available labels; methods needing residual calibration must document a prior-only warmup sufficient for the same44 dates. Previously generated predictions used as fitting residuals must have been made before their outcome was available. The selected overall policy remains development-adaptive even when individual predictions are chronological.

Controls: inherited calibrated probabilities and the frozen prior policy are replayed on identical dates. Preserve their reference evaluation outside adaptive selection and record that it is not a new candidate. The Brier promotion reference is fixed to inherited calibrated probabilities on these44 dates. New market-implied benchmarks require a preregistered, outcome-blind construction respecting bid/ask constraints; normalized asks alone are not a calibrated market forecast.

Exact settlement rules and historical direct-taker fees remain fixed. At most one purchased unit of one contract-side per day is permitted. Grade A/B+/B/unavailable remain explicit; B/B+ are not verified fills, and A evidence does not prove an actual order filled. Unavailable evidence means abstention.

## Four-round research cycle

1. **Independent precursor tests:** establish whether a proposed mechanism produces all 44 registered forecasts from prior-only fits, retains at least 10 prior prequential residuals at every scored date, and has policy-chain multiclass Brier no worse than the inherited calibrated reference. Record its counter-hypothesis before results. A failed precursor objectively blocks that method's registered economic and synthesis descendants; the exhaustion verifier must bind the precursor result and account for each blocked fingerprint.
2. **Replication and counter-tests:** independently reconstruct surviving precursor findings; test explanations that could negate them. Failed precursor branches do not automatically receive financial experiments.
3. **Consolidation and cross-pollination:** circulate concise evidence packets across groups. Combine only compatible mechanisms with supported inputs; record both parents and predeclare component ablations.
4. **Tournament and terminal verification:** compare reproducible methods, evaluate registered failure gates, retain several behaviorally distinct research leaders and close with an independently reviewed conclusion.

Each method record includes causal rationale, counter-hypothesis, inputs, precursor tests with failure criteria, executable specification, parent lineage and falsification plan. New approaches within the registered finite method language are permitted; arbitrary source changes or unsupported features are not. Hash and validate each executable method before evaluation. Both parameter fingerprints and behavioral-ledger equivalence are tracked. Rewording a hypothesis or repeatedly selecting identical trades cannot masquerade as a new economic discovery.

Before each later round, two independently named worker reviews from distinct nonproposing groups must inspect evidence. The coordinator's consolidation packet identifies upheld findings, falsified explanations, blocked inputs and proposed reallocations. Textual agreement does not replace numeric verification. Terminal review is mandatory even when the budget is exhausted or no candidate passes.

## Budget and adaptive allocation

The immutable registration authorizes at most4 rounds and36 unique evaluated candidates, with a hard elapsed ceiling of14,400seconds (four hours). The ordinary scientific-finish time is no earlier than10,800seconds (three hours), except when the machine-checkable finite exhaustion certificate below succeeds. Time and counters never reset on resume. No candidate may start after the deadline; a running deterministic calculation must support a registered time bound/cancellation policy so the campaign cannot silently overrun its hard limit.

The target round allocation is nine candidate slots. Reserve at least three per full round for null/adversarial tests or independent verification when that group has three eligible registered descendants. Each other logical group receives at least one when it has an eligible descendant; no group receives more than four. Any capacity-based relaxation is recorded. Round1 evaluates the four fixed precursors. Later rounds allocate flexible slots among eligible groups using cross-reviewed precursor gains, cross-reviewed falsifications, or cross-reviewed Pareto improvements. Cross-review is an allocation signal rather than numerical replication; final numerical reproduction remains separate. Within equal evidence tiers, fewer prior allocated slots wins, then stable group-ID order. Raw return alone, duplicate count and agreement do not earn additional budget. Unused slots are not required to be filled.

Precursor/model-policy evaluations that expose new scores to selection count toward the36-candidate budget. Exact numerical replays of an unchanged candidate do not count as new candidates, but are logged as verification work with time cost. A control cannot later enter candidate selection without consuming a candidate slot and recording its prior exposure. All mathematical/probability and financial experiments must be separately enumerated so precursor work is not a hidden unlimited search.

Two rounds without new independently supported mechanisms trigger a stop recommendation, not automatic early scientific completion. Before three hours, further work must be useful replication, source capability checks, uncertainty analysis or critique within the frozen limits. Do not spin duplicate evaluations to consume time. If no useful authorized work remains, request the exhaustion verifier; otherwise wait without consuming fake research calls until the permitted finish boundary. Safety/integrity failures or an explicit user stop end computation immediately and are labeled ABORTED/STOPPED, not successful scientific completion.

## Machine-checkable finite exhaustion certificate

The registration must commit an explicit finite method catalog or an enumerator with a reproducible finite output, its hash and all allowed dependency rules. A certificate must bind campaign, code, data, catalog and state hashes and show that every catalog member is accounted for exactly once as evaluated, canonically duplicate of an evaluated member, or deterministically blocked by an objective capability/safety prerequisite. Blocked entries need the specific prerequisite ID and frozen evidence proving failure. A human opinion that an idea is unpromising or that agents ran out of ideas is insufficient.

There must be no pending queue, orphan artifact, unread review, eligible unaccounted method or uncompleted mandatory independent test. Every evaluated result and behavioral equivalence link must verify; terminal reviews and the certificate verifier must be complete. Exhausting36slots without covering a larger catalog is budget exhaustion, not finite-space exhaustion. An open-ended method language cannot obtain this certificate. Before three hours, such a run cannot be marked scientifically complete merely because it hit its candidate/round budget. The four-hour ceiling takes precedence; unmet required work at that boundary is explicitly incomplete/blocked, not silently waived.

## Exact V5B promotion gates and multi-winner ranking

All gates are conjunctive:

- At least30 selected independent development dates.
- Aggregate simulated realized net return at least10% on total entry outlay.
- Mean estimated expected net return per trade at least10% on entry outlay.
- At least4 of5 chronological folds strictly positive.
- Worst nonempty fold return at least-10%.
- Evidence quality at least0.65 using A=1, B+=0.65, B=0.30; these are protocol weights, not fill probabilities.
- Strictly positive fixed-selection+2cent entry stress, including recomputed exact fees.
- Strictly positive return after removing the most profitable day.
- Multiclass Brier no worse than the inherited calibrated reference on identical44 dates, with1e-12 numerical tolerance.

Maintain the same Pareto dimensions: aggregate return, expected return, worst fold, positive-fold count, sample count, evidence quality, negative Brier and+2cent stress return. Rank eligible distinct behaviors by number of failed gates (fewer first), sample>=30, positive-fold count, worst fold, aggregate return, evidence quality, then fingerprint for deterministic ties. Select at most three distinct behavioral ledgers; preserve underlying forecast methods even if their trade behavior matches. Behavioral hashes include every scoring date, selected ticker/side/quantity/entry/fee/grade or explicit abstention. They exclude narrative and predicted probabilities. Identical trade behavior is not another economic winner.

Only all-gate candidates may be called development-screen winners. If fewer than three pass, leave winner slots empty; report up to three separately labeled diagnostic leaders. A favorable mean forecast score cannot compensate for a failed economic gate. Independently reproduce every proposed winner and the final selected strategy.

## Frozen confirmation boundary

The64/44 dates are repeatedly exposed development evidence. V5A's reserved28-day holdout remains unavailable to V6, even if still sealed. Before any independent confirmation, prove a separate period's eligibility without outcomes, freeze its universe and register an exact one-shot protocol. Select one final strategy or a preregistered fixed ensemble from the development winners, bind code/parameters/data/fees/rules and weights, then evaluate that confirmation once. Never replace it after observing results.

If no separate untouched period is ready, complete only the research tournament and immutable development artifacts; confirmation remains BLOCKED and the10% target remains unconfirmed. No live/paper order, current market feed, credentials, purchase or protected-label access is authorized by this goal.

## Capabilities and required deliverables

Local features can support sparse HRRR temperature/cloud/wind summaries, GEFS ensemble mean/spread and sampled model disagreement. Individual members, run-to-run changes, true hourly daily maxima, observed marine-layer regimes, alternative decision-time replays and verified queue/partial fills are not established. Source capability changes require audited new bindings before a successor experiment, not silent imputation.

Deliver immutable registration; finite method catalog; feature/source inventory; independent agent packets; precursor and candidate ledgers; behavioral duplicate registry; allocation ledger; consolidated evidence packets; Pareto and up-to-three winner records; independent numerical verification; terminal review; exhaustion certificate if used; final scientific report and confirmation blocker/freeze. Clearly distinguish specified, implemented, tested and executed elements. Negative evidence, no incremental information and insufficient execution evidence are valid conclusions.
