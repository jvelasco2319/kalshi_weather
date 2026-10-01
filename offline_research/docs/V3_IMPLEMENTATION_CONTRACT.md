# V3 expanded-evidence implementation contract

Status: **registered for implementation; data, code, and campaign results described as future work below are not yet complete**  
Registered: September 25, 2026 Pacific time  
Machine-readable registration: [`configs/v3_goal.json`](../configs/v3_goal.json)

## Why V3 exists

The completed V2 campaign `local-20260925T170513829794Z` exhausted its registered search budget without finding a qualifying candidate. It completed four productive epochs, used 98 local model calls, compiled and independently replicated 40 distinct candidates, and rejected all 40. The best development candidate produced approximately **-0.289% capital-weighted simulated net return across 122 assumed-fill trades**. It did not pass the forecast-skill, cost-stress, or critic gates. No V2 candidate became a champion, the protected final interval remained unopened, and no actual orders or account gains were measured.

V2's negative result is useful evidence. Its typed search varied GFS/NBM weights, Gaussian bias and spread corrections, entry thresholds, and purchase sides. Repeating that search against the same development period would increase overfitting risk without adding material information. V3 therefore expands the historical evidence and executable model families before it starts a new bounded search.

V3 preserves the useful research pattern from the OpenAI Navier-Stokes work: independent specialist contexts propose hypotheses, deterministic code tests them, critics try to falsify them, and a controller uses recorded evidence to allocate a finite next round. The model remains a proposer and synthesizer. Code owns data access, plan validation, numerical evaluation, gates, budgets, and stopping.

## Goal 3

**Implement, verify, and run one bounded V3 offline research campaign using higher-resolution Kalshi and Los Angeles weather evidence.** V3 must reconstruct the settlement target exactly enough for audit, improve as-of weather and market inputs, add non-Gaussian and regime-aware probability families, implement selective abstaining policies, freeze the resulting dataset and evaluation contract, and then run the iterative multi-agent process. The target remains at least 10% expected net return per purchased trade on total entry outlay.

Goal 3 completes when the V3 campaign and its report complete, regardless of whether the scientific conclusion is improvement, no improvement, or insufficient evidence. Profitability is not a completion requirement and may not be manufactured by extending the search. A positive historical result remains simulated evidence and does not authorize online data collection, paper orders, live orders, or deployment.

## Non-negotiable boundary

The project remains historical and offline.

- Acquisition may perform finite, date-bounded downloads of historical records and source documentation.
- Every experiment, candidate comparison, critic packet, replay, and report must run from a frozen local dataset with network access denied.
- No WebSocket, scheduler, heartbeat, live quote reader, prospective forecast, broker connection, paper order, or live order is part of V3.
- The protected July-December 2025 final interval remains sealed from discovery workers, synthesis, diagnostics, feature selection, regime design, and threshold tuning.
- Historical final-period files may exist in protected storage, but only the independent final evaluator may read their labels and prices. A champion may consume the interval once.
- A missing required source blocks or narrows the registered campaign. It does not permit a hidden fetch, fabricated value, or use of future information.

## Stage A: source feasibility and exact settlement

Before bulk acquisition, record source, coverage, as-of semantics, file size, rate limits, and a small verified sample for every new source. The source audit decides whether the requested family is required, optional, or unavailable. It must not silently substitute analysis or reanalysis data for a forecast that existed at the simulated decision time.

Reconstruct the target from the contract and its named settlement source:

- preserve each KXHIGHLAX contract's range semantics, station, close time, outcome, and rules version;
- preserve the original and revised CLILAX reports with issuance timestamps;
- record the NWS climate date, reported maximum, precision, corrections, and the exact mapping to every mutually exclusive contract bracket;
- reconcile CLILAX, Kalshi settlement, and NCEI daily summaries without treating disagreement as a feature;
- version and test the climate-day boundary, daylight-saving handling, integer-bin boundaries, and preliminary-versus-final report rule.

Settlement mapping must be deterministic and must prove that bracket probabilities are exhaustive, mutually exclusive, nonnegative, and sum to one within numerical tolerance.

## Stage B: bounded historical data expansion

The acquisition plan is finite and limited to the registered training, development, and protected-final dates. Bulk acquisition must be resumable, hash every retained object, preserve retrieval and issue times, and stop after its registered attempt and storage limits.

Data priorities are:

1. **Minute Kalshi evidence.** Acquire one-minute historical candlesticks and public trades when the official historical interface makes them available. Preserve bid/ask fields and their documented meaning, trade time, price, quantity, side convention, market status, and data availability. Do not infer executable depth or queue position from a candle or another trader's fill. If minute records cannot support an entry-time assertion, use a named conservative fill rule or abstain.
2. **Exact settlement evidence.** Complete the contract-to-CLILAX reconciliation above before economic scoring is trusted.
3. **HRRR and local observations.** Evaluate archived HRRR forecasts at KLAX and a documented small surrounding grid or point extraction. Add timestamped KLAX METAR variables available by the decision time: temperature, dew point, wind, pressure, visibility, sky cover, and ceiling. Nearby coastal and inland observations may be admitted only with their release times and station history.
4. **GEFS uncertainty.** Acquire actual archived GEFS members or documented native probabilistic fields for the registered cycles and leads. Retain member identity and avoid treating related cycles as independent samples.
5. **Local regime context.** Derive only as-of features needed to recognize marine-layer persistence and burnoff, sea breeze, offshore flow, frontal/precipitation conditions, and heat or weak-gradient days. Every derived feature must name its source records and cutoff.

A new family enters the V3 dataset only after unit conversion, time-zone, duplicate, missingness, availability, and look-ahead checks pass. The readiness report must state coverage by source, partition, cycle, and feature. An unavailable optional family is recorded as unavailable; the architecture must still support an honest reduced-scope campaign when its minimum inputs pass.

## Stage C: frozen dataset and five chronological folds

Weather-model training remains historical data ending no later than December 31, 2024. Minute Kalshi history begins January 5, 2025, so January 5 through February 3 is a fixed 30-day development calibration prefix for the market-residual, market-conditioned calibration, and abstention layers. Those dates are never scored as candidate performance. The common scored development interval is February 4 through June 30, 2025. The protected final remains July 1 through December 31, 2025.

After the calibration prefix and preregistered exclusions are removed, sort eligible February 4 through June 30 settlement days and split them into five contiguous scored folds whose day counts differ by at most one. Save the exact dates and hashes before candidate performance is evaluated. Weather-model fitting uses the frozen 2024 training partition. Market-residual and market-conditioned calibration use only the fixed January 5 through February 3 prefix. No scored fold outcome may influence fitting, calibration, feature selection, or thresholds.

All V3 model selection, regime thresholds, feature choices, entry times, price bands, side rules, abstention rules, and combinations occur on training and development only. Protected-final information cannot be summarized into an agent packet, even without explicit labels.

## Stage D: expanded executable architecture

V3 uses six specialist colonies:

| Colony | Responsibility |
| --- | --- |
| Settlement and measurement | Contract mapping, climate-day semantics, station precision, and revision audits |
| Local weather | HRRR, METAR, marine-layer, wind, pressure-gradient, and regime hypotheses |
| Ensemble probability | GEFS, empirical members, quantiles, mixtures, ordered brackets, and calibration |
| Market behavior | Minute price behavior, time-to-close, side asymmetry, residual edge, and staleness |
| Execution and abstention | Costs, conservative fills, price bands, liquidity evidence, uncertainty buffers, and selective entry |
| Adversarial alternatives | Simple challengers, leakage checks, independent replication, and failure analysis |

The V3 typed plan language must bind the dataset version, settlement mapper, feature set, forecast family, regime classifier, probability family, calibration method, decision time, market features, side policy, abstention rule, fill rule, cost scenario, and parent identifiers. Every executable choice must come from a reviewed finite enumeration. Unknown operators, arbitrary code, paths, URLs, tool requests, and unavailable data are rejected.

The initial executable probability families should include, when their data passes readiness:

- empirical ensemble-member bracket probabilities;
- quantile models or ordered-logistic bracket probabilities;
- finite non-Gaussian mixtures;
- isotonic or beta calibration fitted on permitted historical data;
- regime-conditioned models with a pooled fallback;
- conformal intervals used as an abstention signal; and
- a registered combination of weather probability and market-implied probability.

The market layer evaluates the economically relevant residual: whether a weather-versus-market probability difference remains credible after forecast uncertainty, price staleness, fees, adverse fill movement, and multiplicity. At most one purchased position may be selected per event in the primary analysis. The policy may abstain on most days.

The controller stores a structural signature for every compiled plan and collapses cosmetic or cross-colony duplicates. Each candidate retains lineage to its parents, evidence, data and code versions, fitted model, predictions, replay ledger, fold metrics, critic, and independent replication.

## Stage E: readiness before research

V3 research cannot start until a new readiness artifact proves:

- source manifests and hashes are complete for the admitted V3 dataset;
- settlement and probability-conservation fixtures pass;
- every feature is available at or before the simulated decision cutoff;
- training, five development folds, and protected-final access controls are frozen;
- the minute-data fill rule and its limitations are named;
- baseline forecasts and market replay reproduce from saved local inputs;
- experiment subprocesses succeed with networking denied and cannot read the protected final;
- the V3 plan schema rejects unregistered fields and unavailable data families;
- independent replay reproduces opportunity selection, entry outlay, fees, payouts, and return;
- the critic receives the exact compiled identity and saved evidence; and
- campaign, model-call, token, retry, wall-time, and concurrency limits are bound into the readiness hash.

Passing V2 readiness does not satisfy V3 readiness. Any code, schema, dataset, settlement, fill, cost, partition, or gate change invalidates the V3 readiness artifact and campaign ticket.

## Stage F: bounded iterative campaign

The registered production ceiling is six epochs, 60 distinct executed candidates, 180 local model calls, 2,949,120 reserved context tokens, one inference process at a time, and eight hours of campaign wall time. No epoch may execute more than ten new candidates. At most two retries may be charged for a transient task failure. Paid API spending is zero.

The allocator begins with equal colony proposal opportunity. Later epochs use saved evidence and reserve candidate capacity for supported continuations, cross-colony combinations, independent alternatives, and adversarial challenges. Synthesis may request a registered diagnostic or identify a future data gap, but it may not download data, add an operator, loosen a gate, enlarge a budget, or expose the protected final during the campaign.

The campaign stops at the first applicable condition:

1. one candidate passes every development promotion gate;
2. 60 distinct candidates have executed;
3. 180 local model calls have been charged;
4. six epochs have been attempted;
5. two consecutive epochs produce no new executable candidate;
6. eight hours of campaign wall time elapse; or
7. a required data, integrity, replication, critic, or resource boundary fails.

Disappointing results cannot enlarge these limits. A campaign with no champion completes as `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`. Material changes after completion require a separately registered V4 campaign and a new untouched evaluation period if the V3 protected final was consumed.

## Exact development promotion gates

A candidate becomes the sole frozen V3 champion only when **all** of the following are true on saved development predictions and ledger rows:

1. **Entry edge:** every selected purchase had at least 10% estimated net expected return on total entry outlay under the registered primary cost formula at decision time.
2. **Primary economic result:** aggregate capital-weighted simulated net return is at least 10%. Capital-weighted return is total simulated net profit divided by total simulated entry outlay, including the registered primary fee and fill assumptions.
3. **Cost stress:** aggregate capital-weighted return is strictly greater than zero after a 10% fee proxy and an additional $0.02 adverse price movement per purchased contract, quantity one. An unavailable stressed entry is an abstention, not an optimistic fill.
4. **Chronological stability:** at least four of the five frozen development folds have strictly positive capital-weighted return.
5. **Uncertainty:** a deterministic 10,000-resample, fold-stratified bootstrap over independent settlement days has a one-sided 95% lower confidence bound for aggregate capital-weighted return strictly greater than zero. The seed is `20260925`; folds are resampled separately and then combined. Undefined-return resamples fail the gate.
6. **Sufficient opportunities:** at least 30 trades on at least 30 distinct settlement days, at least five trades in each development fold, and no more than one selected purchase per event in the primary policy.
7. **Forecast and calibration guardrail:** development CRPS is no worse than the registered reference baseline and development Brier score is no worse than that baseline. All bracket probabilities must pass conservation checks. Any separately reported calibration error is diagnostic unless frozen into a later contract.
8. **Concentration:** the result must remain positive after removing the single most profitable settlement day. Month, regime, price band, side, and decision-time contribution tables are mandatory evidence for the critic.
9. **Independent reproduction:** a separate deterministic verifier must reproduce the exact selected opportunities, entry outlay, fees, payouts, fold returns, bootstrap bound, CRPS, and Brier score from saved predictions and market evidence.
10. **Adversarial review:** the independent critic must return a nonrejecting decision for the exact compiled candidate and cite no unresolved look-ahead, settlement, probability, availability, fill, concentration, or identity defect.

There is no weighted average or discretionary override across gates. A missing metric is a failure. Candidate ranking matters only among candidates that pass every gate; the ranking rule must be frozen in the readiness artifact before the campaign starts.

## Protected-final rule

If and only if one development champion passes every gate, freeze its complete executable identity and consume the protected final once. The final evaluator makes no model, threshold, regime, entry-time, side, fill, or cost adjustment. A final failure is reported and cannot be recycled into V3 discovery. No other V3 candidate sees the final result.

The final report separates estimated edge, assumed-fill historical return, uncertainty, forecast quality, calibration, opportunity frequency, capacity limits, and execution-evidence grade. It must say that actual account profit was not measured because no orders were placed.

## Deliverables

V3 must produce, at minimum:

- source feasibility, settlement-reconciliation, and data-quality reports;
- immutable source and dataset manifests;
- the frozen five-fold split and protected access manifest;
- versioned schemas and deterministic compiler for admitted V3 plans;
- source-backed baselines and replay fixtures;
- readiness and local worker capability evidence;
- campaign ticket, ledger, packets, responses, candidates, replications, critics, allocation decisions, and search coverage;
- a report containing all promotion metrics, negative evidence, cost sensitivity, concentration tables, and final-use status; and
- a clear `CHAMPION`, `NO_IMPROVEMENT`, or `INSUFFICIENT_EVIDENCE` conclusion.

Until those artifacts exist, V3 is an authorized implementation goal and a preregistered research design, not a completed data expansion, working model, profitable strategy, or campaign result.
