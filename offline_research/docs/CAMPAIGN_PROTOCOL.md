# Completed iterative Kalshi weather campaign protocol — V2

This is the immutable historical contract for completed V2 campaign `local-20260925T170513829794Z`. It incorporated the architecture changes identified in [NAVIER_STOKES_ARCHITECTURE_CROSSCHECK.md](NAVIER_STOKES_ARCHITECTURE_CROSSCHECK.md). V2 completed with 40 independently replicated candidates, no champion, and conclusion `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`; the protected final remained unopened. The current V3 goal and future campaign contract are in [V3_IMPLEMENTATION_CONTRACT.md](V3_IMPLEMENTATION_CONTRACT.md) and [`configs/v3_goal.json`](../configs/v3_goal.json). V3 registration does not alter this protocol or its result.

The objective is to discover a KLAX daily-high-temperature method whose purchased positions have at least 10% estimated net expected return on total entry outlay, then test one frozen champion on the protected interval. This is an offline research screen. It does not promise profit, measure account gains, or authorize trading.

## Architecture borrowed from the OpenAI example

The useful pattern from OpenAI's Navier–Stokes work is an iterative research organization: many independent contexts explore, useful partial results are retained, synthesis creates new work, and a controller reallocates effort toward promising or unresolved directions. Our implementation scales that pattern down to one local model runtime and deterministic Python evaluation.

The language model proposes plans and questions. It never executes code or decides whether a result passed. The host owns schemas, compilation, data access, numerical evaluation, budgets, gates, evidence lineage and stopping. This separation lets the search change direction while preserving reproducibility and holdout isolation.

The loop is:

1. Curate baseline and recent positive and negative evidence.
2. Ask isolated colony contexts for typed plans or targeted checks.
3. Validate and compile novel plans with trusted code.
4. Evaluate on frozen development data and rolling chronological folds.
5. Run a separate critic and independent numerical replication.
6. Apply stage gates and the final development promotion gate.
7. Synthesize findings and allocate the next bounded portfolio.
8. Stop on a qualifying candidate, the experiment/call/epoch limit, or two empty epochs.

An epoch counts as productive only when at least one previously unseen executable plan completes. Reviews without a new experiment do not create artificial research progress.

## Six research colonies

| Colony | Current executable scope | Questions retained for later data work |
| --- | --- | --- |
| Forecast ensemble | GFS/NBM weights, global/monthly/harmonic bias, residual spread | Additional independent forecast models and lead-time structure |
| LAX meteorology | Seasonal coastal-error proxies expressed through registered operators | Cloud layers, pressure gradients, coastal winds and marine-layer timing |
| Observations and measurement | Official TMAX and settlement reconciliation audits | Point-in-time KLAX intraday observations and reporting precision |
| Probability calibration | Gaussian integer-bin probabilities, spread scaling and disagreement inflation | Richer tails and non-Gaussian distributions after separate validation |
| Market and execution | 10/15/20/30% entry screens and BOTH/YES/NO side policies | Historical depth, queue position and subhour execution evidence |
| Adversarial alternatives | Simpler plans, independent challenges, replication and falsification | Alternative datasets or mechanisms that survive the same controls |

The colony label routes a task; it does not change an executable plan's identity. Identical compiled plans proposed by different colonies count once.

## Versioned typed research language

Workers return `klax-research-plan-v2` JSON. A plan contains only finite enumerations:

- GFS weight: 0, 0.25, 0.5, 0.75 or 1;
- bias: global, monthly shrinkage or annual seasonal harmonic;
- spread: global, monthly shrinkage or disagreement inflation;
- spread scale: 0.85, 1, 1.15 or 1.3;
- disagreement coefficient: 0.25 or 0.5 when that spread operator is used;
- calibration: Gaussian probability integrated over the exact integer interval;
- expected-return entry threshold: 10%, 15%, 20% or 30%;
- permitted purchase side: BOTH, YES or NO;
- at most four existing parent hypothesis identifiers.

Unknown fields, code, expressions, paths, URLs, imports, tools and unavailable parent identifiers are rejected. The compiler maps a valid plan to the reviewed candidate fitter and replay engine. The plan and compiled recipe are both hashed and saved with every experiment.

This language is intentionally broader than V1's menu while still finite. Adding a future operator requires a code change, tests, a new schema/version and new readiness. A model cannot expand the executable surface through prose.

## Discovery and iteration

Epoch 1 creates three independent discovery contexts for each of the six colonies. Each receives the registered baseline plus a rotated set of host seeds. A host seed ensures the campaign can perform experiments if workers abstain. Up to ten unique plans are selected for numerical evaluation.

After a productive epoch, colony synthesizers may return one new plan or a targeted diagnostic, replication, data requirement or falsification. A global synthesizer cross-pollinates strong and contradictory findings. These outputs become controller messages and next-epoch tasks; synthesis is no longer forced to abstain.

The deterministic allocator reserves the next experiment portfolio as follows:

| Use | Share |
| --- | ---: |
| Continue or fork supported directions | 50% |
| Cross-colony combinations | 20% |
| Independent alternatives | 15% |
| Adversarial replication/challenges | 15% |

Rounding is deterministic. Unused category slots may be filled by novel registered seeds, but the saved coverage report distinguishes the original allocation. Decisions use `CONTINUE`, `FORK`, `COMBINE`, `REPLICATE`, `CHALLENGE`, `ALLOCATION` and `STOP` records with cited evidence.

## Evidence and gates

All plans fit calibration parameters on 2024 only. Development comparisons use January 5 through June 30, 2025. The saved development days are split chronologically into three diagnostic folds. Fold statistics show CRPS, Brier, trade count and capital-weighted simulated return; they are stability diagnostics, not three independent holdouts.

Every executable plan must pass through four learning gates:

1. **Forecast skill:** at least 1% relative CRPS improvement and all three usable folds.
2. **Probability calibration:** Brier degradation no greater than 0.005.
3. **Market information:** the same Brier limit and at least 30 simulated entries.
4. **Economic simulation:** at least 30 simulated entries and 10% capital-weighted return.

Every stage also requires independent numerical verification and a critic that does not reject the exact executable identity. Stage gates guide allocation. Promotion additionally requires the previously registered final development screen: at least 2% relative CRPS improvement, Brier degradation no greater than 0.005, 30 entries, 10% capital-weighted return, nonnegative return under the registered higher-cost stress, verified primary opportunity selection and critic approval.

One plan passing all stage and promotion gates stops discovery and freezes the ranked candidate set. The protected July–December 2025 interval can then be consumed once. The final evaluator applies the champion's exact entry threshold and side policy while comparing frozen baseline models under their original policy.

## Budgets and stopping

The production registration permits three to five epochs, 20–40 experiments, 60–120 model calls, no more than ten experiments in an epoch, and one local inference process at a time. The current policy registers five epochs, 40 experiments, 120 calls, a 1,966,080-token reservation and six hours of wall time. Paid API spending is zero.

The campaign stops when any condition occurs:

- one candidate passes every registered development gate;
- 40 unique experiments have run;
- 120 model calls have been charged;
- five epochs have been attempted;
- two consecutive epochs produce no new executable experiment;
- a required input, verification or resource boundary fails.

Failure and no improvement are valid outcomes. The controller cannot enlarge a budget because results are disappointing. After a protected final failure, further research requires a new registered campaign and genuinely untouched future data; the same final interval cannot become development data and still serve as an unbiased holdout.

## Offline and execution limits

Acquisition ends before research begins. Experiment subprocesses deny network access, child processes and protected-final reads. The model runtime has no tools. The host and native runtime remain trusted components rather than an operating-system sandbox.

The replay uses hourly price summaries, assumed slippage, a fee proxy, a one-minute entry delay and hold-to-settlement payouts. It cannot establish order-book depth, queue position, actual fills or a subhour latency advantage. Missing cloud, observation and depth datasets become explicit data requests; they cannot be fabricated or downloaded during a campaign.

## Required artifacts

V2 saves the controller database and ledger, every packet and response, typed plan, compiled recipe, fitted model, predictions, fold diagnostics, decisions, ledgers, critic response, independent replication, stage-gate results, candidate register, allocation decisions, targeted requests and `search_coverage.json`. The summary binds the exact readiness, pilot, code and dataset hashes.

The report must include all tested and rejected plans, search coverage by epoch and colony, baseline comparisons, calibration, uncertainty, assumed-fill PnL, entry outlay, trade count, cost sensitivity and strongest contrary evidence. A favorable historical simulation remains evidence for review, not permission to connect to Kalshi.
