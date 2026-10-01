# V5 Four-Colony Offline Verification Campaign Goal

Version 1.0 | Registered September 27, 2026 | Status: `GOAL_REGISTERED_PENDING_IMPLEMENTATION_AND_READINESS`

Workspace: `C:\Users\darks\Documents\Codex\kalshi\_weather\_llm`

Machine-readable registration: `configs/v5_four_colony_verification_campaign.json`

## Executive goal

V5 will determine whether the leading HRRR/GEFS LAX temperature-bracket signal is a genuine, executable Kalshi market inefficiency or an artifact created by sparse observations, proxy prices, assumed fills, uncertain historical fees, a small sample, or unstable probability calibration.

The campaign will not search broadly for a new winner. It will transfer one frozen V4 probability leader to a larger, later, untouched historical period and ask four independent evidence colonies to falsify it. A deterministic controller, rather than an agent, will enforce identities, data partitions, budgets, gates, arithmetic, protected-data access, and terminal labels.

The research target remains at least 10% estimated net expected return on total entry outlay for every selected trade. V5 also requires the one-shot confirmation ledger to show at least 10% aggregate capital-weighted realized return, positive robustness results, and stable performance through time before it may receive the strongest positive label. These are research gates, not a guarantee of future gains.

The campaign remains historical and offline. Finite acquisition of historical records is allowed before readiness. Live feeds, WebSocket recorders, prospective predictions, paper orders, live orders, and broker connectivity are excluded.

## Why V5 is the right next campaign

V4/V4.1 produced a promising probability signal but did not prove a tradable edge. The leading candidate was:

- Candidate ID: `v4-candidate-a779fd17e7b160650c8f`
- Research-plan SHA-256: `a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461`
- Fitted-model SHA-256: `9bf8b1ba469b1c7258108f5dd11c67c2822e7487edff5c819688d37319574f93`
- Frozen model-state SHA-256: `7c677c1264115b39d73c70cfb7162b9be138ae1c9df9ac30df472426f2a57fd3`
- Forecast inputs: HRRR plus GEFS mean and spread
- Features: temperature only
- Distribution: quantile brackets
- Calibration: bracket-by-bracket isotonic
- Regime: pooled
- Decision time: 18:00 UTC
- Sides: YES and NO
- Entry-price band: 5–80 cents
- Maximum interval width: 4°F
- Minimum estimated net expected return: 10%
- Maximum positions: one per event

On the February 4 through June 30, 2025 development interval, it selected 13 trades on 13 settlement days. Under assumed Grade-B candle fills and an explicitly unverified 2025 fee proxy, it reported:

- $4.70 total entry outlay
- $3.30 net profit
- 70.21% capital-weighted return
- 46.92% mean trade return
- 30.43% one-sided 95% day-bootstrap lower bound
- 58.73% stressed return
- 54.87% return after removing the best day

The probability model also beat the frozen reference on the 145 scored development events:

- CRPS: 0.65947 versus 0.98335
- Multiclass Brier: 0.82268 versus 1.10195
- Probability mass conservation: passed

Those numbers are interesting, but the evidence weaknesses are decisive:

- All 13 entries used assumed candle prices.
- None of the 3,150 frozen market-decision snapshots contained historical depth or supported a hypothetical fill.
- Historical bid/ask candle closes do not establish that one contract was available at the displayed price.
- The historical fee schedule was not verified.
- Only 13 settlement days were selected.
- Fold returns deteriorated from strongly positive in the first three folds to -100% in each of the last two folds.
- The isotonic layer was fitted on only 30 calibration days and contains exact-zero and exact-one mappings.
- Several high-return variants shared 67%–92% of their selected events and therefore constitute one signal family, not independent confirmations.

V5 is designed around these weaknesses. Its main result will be a classification of the evidence, not the production of a profitable candidate.

## Central scientific question

> At the fixed 18:00 UTC decision point, does the frozen HRRR/GEFS probability leader identify narrow KXHIGHLAX daily-high brackets whose mispricing survives exact historical execution, exact costs and settlement, a substantially larger chronological sample, and independent adversarial replication?

The genuine-inefficiency claim is conjunctive:

```text
Genuine evidence =
    frozen probability skill
  AND contemporaneous executable price and size
  AND exact historical fees and settlement
  AND adequate independent sample
  AND temporal and adversarial stability
  AND independent deterministic reproduction
```

A failure in any component cannot be repaired by stronger performance in another component. Good forecast probabilities do not prove fills. Observed fills do not repair a leaking model. Conservative cost assumptions do not become verified historical fees. Missing evidence is not scored as a loss or a win; it receives an explicit insufficient-evidence conclusion.

## Fixed hypotheses

### Primary hypothesis

The frozen probability leader generates one-contract KXHIGHLAX opportunities at 18:00 UTC with at least 10% net expected return after the executable opposing-side price and exact historical costs, and the resulting one-shot confirmation ledger has at least 10% aggregate capital-weighted realized return with the registered stability gates satisfied.

### Falsification hypotheses

V5 will determine whether the V4/V4.1 result instead arises from one or more of the following:

1. Candle closes or public trade prints were treated as executable prices.
2. Historical size, queue state, or latency was missing.
3. The fee, rounding, rebate, payout, or settlement rule was wrong or unverified.
4. Thirty calibration days produced unstable or overconfident isotonic probabilities.
5. The result was concentrated in a small early-period cluster and failed later regimes.
6. Multiple apparently strong candidates were correlated versions of the same underlying signal.
7. Historical forecast availability was inferred earlier than the data could have been known.
8. The observed result is compatible with chance after chronological, bootstrap, and adversarial testing.

## Frozen leader and transfer identity

V5 carries forward one probability leader. It may not choose a replacement after seeing V5 outcomes.

The source model is hash-bound to the V4 artifacts. Applying it to later dates creates a new immutable transfer manifest containing:

- source candidate ID;
- source plan hash;
- fitted-model hash;
- frozen state hash;
- scorer and contract-mapper hashes;
- new dataset hash;
- confirmation split hash;
- fixed decision and execution policy;
- explicit `refit_performed: false`.

The primary track permits no retraining, recalibration, feature selection, threshold adjustment, decision-time change, or model-family selection. Registered ablations and perturbations are falsification diagnostics only. They cannot compete for promotion and cannot replace the leader.

Maximum promotable candidate identities: **one**.

## Data partitions

Existing data keep their current roles:

| Period | Role | Treatment |
|---|---|---|
| 2024-01-01 through 2024-12-30 | Weather-model training | Already exposed and frozen |
| 2025-01-05 through 2025-02-03 | Calibration prefix | Already exposed and frozen |
| 2025-02-04 through 2025-06-30 | V4/V4.1 development | Reproduction and diagnostics only |
| 2025-07-01 through 2025-12-31 | Existing protected interval | Remains sealed |
| 2026-01-01 through 2026-08-31 | New finite historical acquisition | Added to the protected confirmation pool |

The proposed combined confirmation interval is July 1, 2025 through August 31, 2026, a maximum of 427 calendar days before outcome-blind exclusions. The exact eligible-date universe will be determined only from source completeness, contract identity, forecast availability, market-evidence coverage, and settlement-evidence availability. It must be frozen and hashed before any confirmation outcome or strategy return is read.

The protected pool will be divided into five contiguous chronological folds after the eligible-date universe is frozen. Fold construction may use dates and completeness flags but not outcomes, prices selected by the strategy, profits, or model scores.

### Outcome-blind label barrier

Before confirmation labels are opened, the system must:

1. bind the exact model and decision policy;
2. hash every input and source manifest;
3. verify forecast availability as of 18:00 UTC;
4. reconstruct execution evidence;
5. bind exact fees and settlement rules;
6. compute and freeze the selected trade set from probabilities and executable prices;
7. show at least 30 selected trades on 30 distinct settlement days;
8. show at least five selected trades in each chronological fold;
9. pass all four colony readiness verdicts;
10. deny every unregistered protected-data read.

If fewer than 30 trades survive before label access, the campaign ends as `INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ`. Confirmation labels remain sealed.

## The four colonies

### Colony 1 — Execution Evidence

**Question:** Could a one-contract signal actually have executed at a contemporaneous observed price after a realistic delay?

This colony owns historical market microstructure evidence. It does not modify probabilities, costs, dates, or outcomes.

Its acquisition hierarchy is:

1. actual member orders and fills, if relevant historical orders exist;
2. contemporaneous historical order-book snapshots plus sequenced deltas;
3. timestamped high-frequency full-book snapshots with displayed size;
4. candles and public trades for diagnostics only.

Before bulk acquisition, the colony runs a capability probe. A source is promotion-eligible only if it provides market identity, exchange timestamps and semantics, snapshot or sequence identity, side, price, quantity at each level, update semantics, historical coverage, and documented completeness. Every response is immutable and content-addressed.

The primary execution replay is frozen as:

- one-contract marketable IOC limit order;
- signal time 18:00 UTC;
- primary signal-to-arrival delay 5 seconds;
- adverse delay checks at 30 and 60 seconds;
- no passive-fill inference;
- no queue-priority assumption;
- no candle-touch inference;
- no trade-print substitution;
- maximum one position per event.

At arrival, the replay reconstructs a gap-free book, reads the opposing-side executable price, requires at least one displayed contract, applies the exact fee, recomputes expected net return, and requires at least 10%. Missing price, size, sequence, timing, fee, or identity produces an abstention.

Required experiments:

- trace every source behind the original 17 eligible decisions and 13 event-level selections;
- replay every preregistered day, including no-signal and missing-book days;
- publish the attrition funnel from model opportunity to settled trade;
- compare 5-, 30-, and 60-second delays;
- report one-, five-, and ten-contract capacity separately;
- quantify proxy fragility using positive-volume, next-minute, public-trade, and adverse-price diagnostics;
- test whether execution-data missingness is correlated with fold, month, price, uncertainty, or weather regime;
- independently reconstruct every accepted book state.

Execution promotion gates:

- 100% hash and sequence integrity;
- no future book state;
- at least 90% outcome-blind coverage of preregistered event-time windows;
- displayed executable quantity of at least one;
- an observed executable price for every selected trade;
- exact spread and timing from the same book state;
- primary 5-second aggregate result remains positive at 30 seconds;
- no candle or public print satisfies the fill gate;
- independent reconstruction and critic nonrejection.

If a usable historical depth source does not exist, this colony stops early with `EXECUTION_EVIDENCE_UNAVAILABLE`. That is a decisive answer about what the historical archive can support.

### Colony 2 — Fee and Settlement Integrity

**Question:** Do exact historical economic rules preserve the opportunity after fills are established?

This colony binds every trade to the fee, rounding, rebate, payout, and settlement rules effective for that contract and timestamp. It cannot choose trades or infer fills.

For every historical period it must acquire and freeze authoritative evidence for:

- fee schedule and effective dates;
- KXHIGHLAX applicability;
- maker and taker formulas;
- calculation price;
- per-contract, per-order, or per-fill rounding;
- minimum and maximum fees;
- account or participant class;
- maker rebates or liquidity incentives;
- settlement costs;
- payout multiplier;
- contract-rule version and later revisions.

No rebate is applied without contemporaneous eligibility evidence. Missing maker/taker evidence cannot be resolved in the profitable direction. A conservative maximum-fee envelope is useful for diagnostics but cannot support the `VERIFIED_EXACT` verdict.

Settlement binding requires the exact event and contract, climate date, KLAX station, America/Los_Angeles timezone, interval endpoints and inclusivity, rule revision, Kalshi result, CLILAX report and issue time, event status, payout, and settlement cost. Exceptional cancellations, voids, refunds, or corrections require an explicit branch.

Required experiments:

- historical fee-source census;
- formula and rounding conformance at prices 1–99 cents and relevant quantities;
- exact repricing of the fixed leader;
- independent settlement replay;
- preliminary-versus-final CLILAX and contract-revision challenges;
- independent decimal-arithmetic ledger reproduction;
- adversarial attempts to use newer schedules, favorable roles, unproven rebates, incorrect rounding, or retroactive revisions.

Promotion requires exact fee and exact settlement bindings for 100% of accepted trades, with an independent implementation matching every fee, payout, net profit, and aggregate.

### Colony 3 — Frozen Probability Validation

**Question:** Does the fixed HRRR/GEFS model retain probability skill on genuinely later dates?

This colony evaluates forecast probabilities separately from trading profit. It scores every eligible event, including days on which the trading policy abstains.

The primary score is paired improvement in discrete ordered-bracket CRPS:

`reference daily CRPS − candidate daily CRPS`

Secondary scores are multiclass Brier, clipped multiclass log loss, reliability error, probability assigned to the realized bracket, sharpness, entropy, exact-zero and exact-one frequency, and mass conservation.

The fixed probability gates are:

- at least 120 scored event-days;
- five contiguous chronological folds with at least 20 scored events per fold;
- at least 30 probability-screened opportunities for selected-subset reporting;
- candidate aggregate CRPS strictly better than the reference;
- one-sided 95% paired block-bootstrap lower bound for CRPS improvement above zero;
- candidate Brier no worse than the reference;
- CRPS improvement in at least four of five folds;
- Brier no worse in at least four of five folds;
- final two folds combined no worse on CRPS and Brier;
- overall classwise reliability error at most 0.10;
- selected-subset reliability error at most 0.15 when at least 30 cases exist;
- no realized bracket assigned probability below 1e-6;
- probability vectors finite, bounded, exhaustive, and summing to one within 1e-10;
- no later forecast cycle or settlement label enters a feature.

Calibration stability is challenged with the 30 registered leave-one-calibration-day-out refits. These are diagnostics, not alternatives. The median purchased-side probability change must be at most 0.05, the 95th percentile at most 0.15, and screen-selection Jaccard overlap at least 0.70.

Availability robustness repeats inference under nominal-cycle-plus-8-hour and plus-12-hour restrictions. The +8-hour case may not reverse aggregate CRPS improvement. The +12-hour case is reported.

Adversarial checks include shuffled labels, date and model-cycle shifts, previous-cycle substitution, raw versus isotonic probabilities, HRRR-only and GEFS-only ablations, contract-boundary days, removal of the ten most confident forecasts, removal of the best month, first-half versus second-half comparisons, duplicate and split-overlap injection, and deliberate protected-data access attempts.

### Colony 4 — Sample and Regime Robustness

**Question:** Is the result broad enough across independent dates and conditions to distinguish a repeatable signal from a small cluster?

This colony owns the outcome-blind date universe, chronological folds, sample counts, regime definitions, concentration diagnostics, and confirmation statistics. It does not change the candidate.

Before label access it must publish:

- the complete candidate-agnostic date universe;
- every exclusion and fixed reason;
- five contiguous fold assignments;
- trade-selection counts based only on frozen probabilities, executable prices, and exact costs;
- weather regimes derived without confirmation outcomes;
- month, side, price, bracket-width, forecast-confidence, and availability strata;
- proof that correlated V4 variants are treated as one signal family.

Primary sample gates:

- at least 120 scored weather events;
- at least 30 executable selected trades;
- at least 30 distinct settlement days;
- at least five selected trades per chronological fold;
- no duplicate settlement-day unit in the bootstrap;
- the selected set and its hash frozen before outcomes are opened.

Post-label stability gates:

- aggregate capital-weighted realized net return at least 10%;
- positive return in at least four of five folds;
- one-sided 95% ordinary day-bootstrap lower bound above zero;
- one-sided 95% three-day moving-block-bootstrap lower bound above zero;
- positive return after removing the most profitable day;
- positive leave-one-fold-out aggregate return for every omitted fold;
- registered cost and adverse-price stress remains positive;
- all month, side, price, regime, uncertainty, and seasonal breakdowns reported without using them to select a replacement policy.

Regime and concentration analyses can narrow the scientific conclusion but cannot create a new candidate. If the edge appears only in a subset discovered after outcomes are opened, that subset becomes a hypothesis for a future campaign with new untouched data.

## Maximum useful four-agent workflow

The current system supports four concurrent agent slots, including the coordinating agent. V5 uses all four when there is independent work:

| Agent slot | Primary colony | Main responsibility |
|---|---|---|
| 1 | Execution Evidence | Historical depth, reconstruction, latency and fill audit |
| 2 | Fee and Settlement Integrity | Historical schedules, rules, repricing and settlement replay |
| 3 | Frozen Probability Validation | Transfer identity, proper scoring, calibration and availability tests |
| 4 | Sample and Regime Robustness | Date universe, folds, sufficiency, regimes and confirmation statistics |

The deterministic controller is software and does not consume an agent slot. It validates schemas, checks hashes, denies protected reads, executes registered scoring, preserves counters, and assigns terminal states.

### Work barriers

**Barrier 0 — registration.** All hashes, sources, dates, gates, budgets, task schemas, and failure labels are frozen.

**Barrier 1 — source capability.** The four agents work concurrently on depth sources, fee/rule sources, weather availability, and date/fold eligibility. No confirmation labels are used.

**Barrier 2 — development reproduction.** Each colony reproduces its part of the V4/V4.1 evidence. Reproduction must explain the 17 eligible contract decisions, 13 selected trades, probability scores, costs, and fold pattern.

**Barrier 3 — outcome-blind transfer readiness.** Each colony submits a signed-by-software readiness verdict. The selected confirmation trade set is frozen before labels are opened.

**Barrier 4 — cross-review.** Agents rotate reviews so no colony verifies itself:

- Execution reviews Probability.
- Probability reviews Sample/Regime.
- Sample/Regime reviews Fees/Settlement.
- Fees/Settlement reviews Execution.

When a primary colony finishes early, its slot immediately takes its registered cross-review or an independent verifier task. Agents are not kept busy with duplicate discovery work.

**Barrier 5 — one-shot confirmation.** The deterministic evaluator opens the registered labels once. All agents audit only their assigned outputs. No new candidate, threshold, feature, date, latency, fee assumption, or subgroup may be selected.

**Barrier 6 — terminal synthesis.** The controller combines colony verdicts using the registered decision table. Agents may explain the result but cannot alter it.

### Agent task budget

- Four active agent slots.
- Five evidence epochs.
- Four primary colony tasks per epoch.
- Twenty core colony tasks.
- Up to sixty local model calls for structured research and critique.
- One local inference process at a time.
- Maximum two charged retries for malformed agent output.
- Maximum one promotable candidate identity.
- Maximum thirty-two registered deterministic evaluation variants, all primary or falsification variants named before results.
- Twelve-hour wall-clock budget after readiness is signed.
- Acquisition and readiness do not consume the scientific campaign clock.

The system should use fewer calls when a terminal failure is already established. More epochs cannot repair missing depth, an unverifiable fee schedule, a failing probability model, or an inadequate pre-label sample.

## Campaign stages and stopping rules

### Stage 0 — Implementation and registration

Build schemas, validators, controller state, transfer manifests, evidence inventories, scorers, replay logic, exact arithmetic, and terminal decision code. Freeze source-code hashes before readiness.

### Stage 1 — Finite historical acquisition

Acquire only bounded historical data. Probe sources before bulk downloads. Preserve raw bytes, requests, timestamps, provenance, hashes, licensing or retention notes, and failed-object manifests.

Stage 1 stops immediately if historical order-book evidence lacks price-level quantity and timestamp semantics. Candles are not converted into promotion-grade fills.

### Stage 2 — Development reproduction

Reproduce the V4/V4.1 leader on the already exposed interval. Differences must be resolved before transfer. This stage is diagnostic and cannot promote the leader.

### Stage 3 — Outcome-blind readiness

Construct the later-date feature, market, rule, and settlement manifests while keeping confirmation outcomes isolated. Freeze the eligible universe, folds, transfer identity, execution policy, exact costs, and selected trade set.

Terminal pre-label stops include:

- `EXECUTION_EVIDENCE_UNAVAILABLE`
- `FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE`
- `INSUFFICIENT_PROBABILITY_EVIDENCE`
- `INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ`
- `INTEGRITY_FAILURE`

### Stage 4 — One-shot protected confirmation

This stage is authorized only when every readiness gate passes. The evaluator reads the registered labels exactly once, computes the fixed metrics, and emits immutable outputs. The protected result is never sent back into discovery.

### Stage 5 — Independent replication and adversarial audit

Independent code regenerates predictions, book states, fees, payouts, ledgers, folds, bootstrap intervals, and gates. Critics test the registered failure modes. Any unresolved mismatch blocks the strongest conclusion.

### Stage 6 — Terminal report

The final report states what was demonstrated, what failed, exact counts, uncertainty, capacity at one/five/ten contracts, evidence limits, and the next scientifically legitimate step. It does not recommend live trading.

## Terminal decision table

The campaign emits exactly one primary conclusion:

### `ROBUST_EXECUTABLE_POSITIVE_AT_OR_ABOVE_10PCT`

Allowed only when:

- the probability colony confirms the frozen signal;
- every selected trade has promotion-grade executable price and size;
- every selected trade has exact fee and settlement bindings;
- at least 30 trades on 30 days and five trades per fold exist;
- every trade cleared the frozen 10% expected-return screen before outcome access;
- aggregate realized return is at least 10%;
- fold, bootstrap, moving-block, stress, best-day, and independent-replication gates pass;
- the adversarial critic records no unresolved defect.

This label means the historical evidence supports the preregistered signal. It does not guarantee future profitability and does not authorize orders.

### `EXECUTABLE_POSITIVE_BELOW_TARGET`

Execution, cost, probability, sample, integrity, and positive-return gates pass, but aggregate realized return is positive and below 10%. This is useful evidence of a possible smaller edge, not attainment of the target.

### `NO_ROBUST_EDGE`

The evidence is adequate to evaluate the signal, but realized return is nonpositive or one or more registered performance/stability gates fail.

### `INSUFFICIENT_EXECUTION_EVIDENCE`

Historical evidence cannot establish executable price and size for the preregistered decision windows. This does not prove the probability signal is false; it prevents a tradability claim.

### `INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ`

Fewer than 30 executable trades, fewer than 30 settlement days, or fewer than five trades per fold survive outcome-blind screening. Labels remain sealed.

### `FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE`

Exact historical economics cannot be established for every selected trade.

### `PROBABILITY_VALIDATION_FAILED`

The frozen HRRR/GEFS model fails skill, calibration, timing, or stability gates on later data.

### `INTEGRITY_FAILURE`

Identity, hash, partition, protected-data, counter, schema, or deterministic-reproduction rules fail.

## Resilience and recovery requirements

V4.1 exposed a controller fragility: malformed adversarial output could stop useful work. V5 must separate scientific failure from orchestration failure.

- Every task uses a strict typed schema.
- Agent prose is never authoritative campaign state.
- Malformed output receives at most two charged retries.
- A deterministic failure or no-op record closes an optional synthesis task after retries.
- Malformed optional synthesis cannot terminate the campaign.
- Only a required integrity artifact may cause `INTEGRITY_FAILURE`.
- Recovery state is written atomically and content-addressed.
- Resume preserves the original deadline, data hashes, candidate identity, counters, and consumed-call budget.
- Calls and elapsed time are never refunded.
- Resume verifies the exact prior process before launch and never starts a duplicate.
- Completion is based on validated artifacts, not on an agent claiming completion.
- Protected-data denial and zero order authorization are checked at every barrier.

## Required artifacts

### Registration and readiness

- `configs/v5_four_colony_verification_campaign.json`
- `data/manifests/v5_candidate_transfer.json`
- `data/manifests/v5_historical_source_inventory.json`
- `data/manifests/v5_confirmation_universe.json`
- `data/manifests/v5_confirmation_folds.json`
- `data/manifests/v5_readiness.json`
- `reports/v5-readiness-audit.md`

### Execution colony

- source capability census;
- immutable order-book manifest and normalized event stream;
- coverage and gap audit;
- executable ledger;
- proxy-to-executable attrition funnel;
- latency and capacity sensitivity;
- independent reconstruction;
- colony verdict.

### Fee and settlement colony

- historical fee-source inventory and effective schedule;
- rebate eligibility manifest;
- contract-rule revision manifest;
- settlement reconciliation;
- joined economic ledger;
- formula and rounding conformance;
- adversarial settlement tests;
- independent economic replication;
- colony verdict.

### Probability colony

- frozen leader and transfer manifest;
- as-of weather audit;
- predictions and daily proper scores;
- fold metrics and reliability tables;
- calibration jackknife;
- source ablations and availability sensitivities;
- independent probability replication;
- colony verdict.

### Sample and regime colony

- outcome-blind universe and exclusion ledger;
- chronological folds;
- pre-label sample-sufficiency report;
- regime definitions;
- realized performance by registered strata;
- ordinary and moving-block bootstrap results;
- fold-removal and concentration analyses;
- independent statistical replication;
- colony verdict.

### Campaign outputs

- append-only task and evidence registry;
- atomic recovery state;
- protected-access audit;
- scheduler/controller verification;
- terminal summary;
- independent campaign verification;
- final V5 analysis report.

Every artifact that controls a decision is hash-bound into the next artifact.

## What V5 will and will not answer

V5 can answer:

- whether the frozen weather probability signal generalizes;
- whether the historical archive can establish one-contract execution;
- whether exact historical economics preserve the expected edge;
- whether the result survives a larger, later, chronological sample;
- whether the 10% target is met under the registered evidence standard;
- which failure mode explains rejection.

V5 cannot guarantee future return, prove scale beyond observed depth, substitute for a prospective recorder, authorize trading, or rescue a failed candidate by searching the same confirmation outcomes.

If V5 finds strong probability skill but lacks historical order-book depth, the correct conclusion is that the weather hypothesis remains interesting while historical tradability is unresolved. A later prospective offline-observation campaign would require a separate user instruction because this campaign explicitly excludes live collection.

## Completion definition

This goal is complete when:

1. the implementation and registration are frozen;
2. finite historical acquisition is completed or a capability stop is documented;
3. all four colonies emit validated verdicts;
4. the protected interval is either legitimately evaluated once or remains sealed under a pre-label stop;
5. independent replication and adversarial review complete;
6. one terminal campaign label is issued;
7. all reports and machine-readable artifacts are saved;
8. no live or paper order was created.

The campaign succeeds scientifically when it gives a decisive, reproducible answer. Finding no robust edge or insufficient execution evidence is a valid completion. Manufacturing a 10% result is not.
