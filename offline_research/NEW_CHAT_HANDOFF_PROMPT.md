# New-chat handoff prompt: complete Kalshi KLAX research history and architecture

Copy everything below the divider into the first message of the new Codex chat after migrating this repository. This is a continuation prompt, not a request to restart the research.

---

You are continuing a mature, offline research repository for Kalshi Los Angeles daily-high-temperature contracts. Treat the migrated repository, frozen artifacts, manifests, and result ledgers as the authoritative record. Do not restart from V1, silently regenerate historical results, modify frozen evidence, or interpret a positive development backtest as proof of future profitability.

## Conversation lineage

The primary source chat for the recent work is:

- Title: `Kalshi V5B — Research-Heavy Parallel Discovery`
- Codex thread ID: `01a0e422-b38f-7a52-9d7c-01ba33e75a91`
- Original source-machine task directory: `C:\Users\darks\Documents\Codex\2026-09-24\can-x20`
- Repository created and developed through that chat: `C:\Users\darks\Documents\Codex\kalshi\_weather\_llm`

If this Codex installation can read another local task, use `read_thread` on thread ID `01a0e422-b38f-7a52-9d7c-01ba33e75a91` for conversational context. If it cannot, continue from the repository. The repository artifacts control whenever chat recollection and saved evidence differ.

The architecture began in an earlier ChatGPT conversation:

- Title: `Apply Navier Stokes Method`
- Conversation ID: `6aa9e345-d790-83e8-a399-b32ae86b65bf`
- Link: <https://chatgpt.com/c/6aa9e345-d790-83e8-a399-b32ae86b65bf>

That conversation adapted OpenAI's publicly described Navier–Stokes research organization to a much smaller Kalshi problem. It inspired the iterative structure. It does not mean this repository reproduces OpenAI's internal system or its scale.

## What this repository is

The project asks one narrow question:

> Can forecasts available by a fixed historical decision time identify mispriced Kalshi `KXHIGHLAX` daily-high-temperature contracts at KLAX, after fees and realistic execution constraints, strongly enough to support at least 10% expected net return per entered trade?

The project converts historical weather information into a probability distribution across the mutually exclusive Kalshi temperature brackets, compares those probabilities with historical contract prices, and simulates at most the trades allowed by a frozen policy. It then tries to falsify the result with chronological folds, cost stress, execution evidence, calibration checks, bootstrap uncertainty, concentration checks, negative controls, and independent numerical reproduction.

The work is historical and offline. It has never placed a paper or live order. It has not established a sustainable 10% expected return. Positive returns in this repository are historical simulations on exposed research periods, not money earned in an account.

## Why the repository has so many versions

Each version answered a problem exposed by the prior version. The sequence was not simply “try more parameters until something wins.” It moved through four different scientific questions:

1. **Can the weather forecast be improved?**
2. **Can forecast output be converted into calibrated bracket probabilities?**
3. **Do those probabilities add information beyond the market price?**
4. **Does a fixed decision rule survive fees, execution constraints, time variation, and new data?**

Early versions built the research machinery. Middle versions found that price and fill assumptions could create misleading profits. Later versions obtained paid historical books, found a promising NO-side selector, watched its apparent advantage weaken on a wider period, repaired probability overconfidence, and then added physical meteorology. V10 is where the project currently ends: it has the strongest probability model found so far, but that model still needs a separately frozen economic replay and genuinely new confirmation.

## The scaled OpenAI-inspired architecture

The useful pattern taken from OpenAI's public Navier–Stokes account was:

```text
different formulations and subproblems
                ↓
independent exploration
                ↓
real experiments and intermediate evidence
                ↓
criticism, replication, and cross-group synthesis
                ↓
new follow-up hypotheses and resource reallocation
                ↓
separate final verification
```

This repository scales that pattern down to a small number of logical colonies and deterministic Python evaluation. Agent agreement never determines whether a method passes.

### Division of responsibility

- **Director/controller:** creates bounded tasks, enforces budgets and legal state transitions, prevents duplicate work, and preserves recovery state.
- **Explorers/colonies:** propose causal hypotheses, candidate configurations, ablations, and targeted checks from different viewpoints.
- **Typed-plan compiler:** rejects invented or out-of-scope operations and converts an admitted plan into approved deterministic operations.
- **Experiment runner:** produces forecasts, probability vectors, hypothetical decisions, and replay ledgers from frozen inputs.
- **Deterministic evaluator:** calculates Brier score, log loss, CRPS where valid, returns, fees, folds, stress cases, and sample counts.
- **Critic/adversary:** looks for leakage, bad timing, weak execution assumptions, concentration, unstable regimes, and simpler explanations.
- **Independent replicator:** recomputes identities, arithmetic, selections, fees, outcomes, and metrics from saved artifacts.
- **Synthesizer/allocator:** connects useful evidence across colonies and creates bounded `CONTINUE`, `FORK`, `COMBINE`, `REPLICATE`, `CHALLENGE`, or `STOP` decisions for the next epoch.
- **Protected evaluator:** may score one frozen strategy on a one-use confirmation set only after all readiness and identity gates pass.

### Authoritative data and experiment flow

```text
historical weather + observations + market records + rules/fees
                              ↓
                 immutable raw source files
                              ↓
             normalized, as-of canonical tables
                              ↓
                frozen chronological partitions
                              ↓
agent hypothesis → typed plan → deterministic experiment
                              ↓
       probability forecast + decision/replay ledger
                              ↓
 deterministic metrics + critic + independent replication
                              ↓
         evidence registry + synthesis + next epoch
                              ↓
       frozen candidate → separate one-shot confirmation
```

### The research stages are deliberately separate

1. **Forecast skill:** temperature error and distribution quality.
2. **Probability calibration:** reliable probabilities for the exact Kalshi brackets.
3. **Market-relative information:** incremental value beyond contemporaneous market probabilities.
4. **Execution economics:** return after exact fees, executable prices, latency, size limits, and stress.
5. **Confirmation:** one frozen method on genuinely new, outcome-blind Grade-A days.

A method can improve Brier score and still lose money. A strategy can show positive historical return and still fail because its fills were assumed, its sample was small, or its result depended on one time period. This separation is one of the project's main lessons.

### Data and safety controls

- Raw sources are immutable and hash-bound.
- Forecast issue time, valid time, retrieval time, and conservative availability time are preserved.
- Weather days remain intact when creating chronological folds.
- Discovery workers do not receive protected confirmation labels.
- Every candidate receives a canonical identity; duplicate parameterizations do not count as new evidence.
- Negative results remain in the registry and cannot be overwritten by a later positive summary.
- Fees, settlement mapping, price conventions, decision time, arrival delay, and promotion gates are frozen before scoring.
- B/B+ historical price evidence can support diagnostics but cannot be called a verified fill.
- Grade A is a stronger execution-aware historical simulation; it still cannot prove that an unsubmitted order would have filled.
- No live feed, paper order, live order, or broker connection is authorized.

## Complete version history

### Foundation / Goal 1 — build the laboratory

The repository first created the local acquisition, normalization, contract mapping, chronological partitioning, forecasting, replay, evaluation, evidence, and controller layers. The preferred `weather_data_collector` source was preserved and audited, but station selection, model timing, and daily-window semantics were corrected or explicitly bounded for KLAX research. This foundation made later versions reproducible instead of relying on ad hoc spreadsheets or remembered assumptions.

Why the next version was needed: the first campaign runner could evaluate candidates safely, but its discovery loop was too narrow to resemble the intended iterative architecture.

### V1 — safe but shallow three-recipe pilot

V1 tested three predefined calibration recipes. All three were rejected. It preserved the protected final period and correctly allowed `NO_IMPROVEMENT`, but all new candidates appeared in the first epoch. Later epochs added reviews rather than materially new experiments.

Why V2: the campaign was a valid small screen, but it was not a real iterative research loop. Synthesis could describe ideas but could not create executable follow-up experiments, and resources could not move toward useful intermediate findings.

### V2 — the first real iterative colony campaign

V2 added six colonies, a versioned typed research language, rolling folds, parent-child hypothesis lineage, stage gates, targeted requests, cross-colony synthesis, portfolio allocation, novelty checks, independent replication, and bounded stopping. It ran four productive epochs, used 98 local model calls, and evaluated and independently replicated 40 distinct candidates.

Result: all 40 candidates were rejected. The best development result was about **-0.289%** capital-weighted simulated return across 122 assumed-fill trades. The protected final stayed closed.

Why V3: V2 established that a better scheduler could not compensate for limited evidence and a narrow Gaussian calibration language. The next version needed minute market history, better weather inputs, exact settlement reconstruction, more probability families, and selective abstention.

### V3 — expanded evidence and stricter science

V3 added minute-level Kalshi candles and trades, exact settlement reconstruction, HRRR, GEFS uncertainty, as-of local observations, weather regimes, richer probability families, market-residual modeling, selective abstention, five chronological folds, bootstrap uncertainty, independent reproduction, and adversarial review. The full historical archive reached 543 registered HRRR/GEFS/local-observation days and the implementation received extensive integrity testing.

Result: 60 candidates were evaluated across six epochs. Every candidate improved the registered forecast reference, with Brier about 25.34% lower and CRPS about 32.94% lower, but none produced a simulated trade. Market screening rejected nearly everything: 57.9% first failed the spread control, 27.4% the entry-price band, 14.6% quote freshness, and one the 10% expected-return threshold. Conclusion: `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`.

Why V4: all 60 V3 candidates used 12:00 UTC and a 4°F interval-width cap. The search had improved forecasting but had starved other decision times and execution settings.

### V4 — broaden decision time and execution controls

V4 registered a 3,072-plan universe spanning 13:30, 15:00, and 18:00 UTC; several interval widths; quote ages; spread caps; price bands; and four model tracks. A fixed part of every epoch forced broad coverage while adaptive slots deepened, combined, challenged, or explored alternatives.

The original V4 process stopped in epoch 7 after the local model repeatedly returned a schema-invalid nomination. This was a protocol failure rather than a scientific or data failure. It had already executed and verified 82 candidates.

Why V4.1: the work needed crash-consistent continuation without refunding calls, changing candidates, or weakening gates.

### V4.1 — adversarially safe continuation

V4.1 imported the exact V4 state, reconciled the failed call, and added a tightly bound fallback that could admit only the already allocated registered plan after repeated malformed nominations. It preserved the absolute deadline and all prior counters.

The V4/V4.1 evidence found a promising HRRR/GEFS probability family. The named research lead, `v4-candidate-a779fd17e7b160650c8f`, used quantile brackets with bracketwise isotonic calibration at 18:00 UTC. On the exposed February–June 2025 development interval it showed **+70.21%** simulated return over 13 trades, a positive bootstrap lower bound, and strong forecast-score improvements.

It was rejected for promotion because all entries used assumed Grade-B candle fills, the historical fee schedule was unverified, the sample was only 13 days, the last two folds each lost 100%, and several high-return variants represented the same correlated signal. The V4.1 run artifacts also did not publish a clean terminal campaign summary; treat this phase as negative-to-provisional evidence, not a confirmed result.

Why V5: another broad search could not answer whether the signal was real. The next campaign had to freeze one leader and try to falsify it with better execution, fees, a larger period, and independent review.

### V5 — strict four-colony verification attempt

V5 assigned the four available agent slots to:

1. execution evidence;
2. fee and settlement integrity;
3. frozen probability validation; and
4. sample and regime robustness.

It did not search for a replacement strategy. It asked whether the one frozen V4 leader survived exact historical depth, costs, settlement, a later sample, and independent replication.

Result: `INSUFFICIENT_EXECUTION_EVIDENCE`. The free historical sources did not provide promotion-grade, contemporaneous, quantity-bearing Level-2 order books near 18:00 UTC with adequate continuity. Exact fee/settlement and later probability/sample bindings were also incomplete. Protected labels remained sealed.

Why V5P/V5A: the strict question could not be answered with free data. The user authorized partial-evidence work and later purchased Probalytics historical data.

### V5P — partial-evidence acquisition branch

V5P allowed analysis with less than V5's 90% Level-2 coverage while keeping evidence grades explicit:

- A: replayable depth with strong continuity;
- B+: paid full-book snapshots with weaker continuity;
- B: aggregated bid/ask evidence and assumed fills;
- C: trade prints that show activity but cannot establish a counterfactual fill.

It preserved the frozen V4 leader, the 18:00 decision point, finite historical acquisition, and a development/one-shot-holdout structure. It could produce exploratory findings but could not overturn the strict V5 verdict.

### V5A — paid Probalytics depth and reduced 92-day standard

V5A integrated the authorized June 1–August 31, 2026 Probalytics archive. It froze 64 development dates and 28 holdout dates and retained the four colonies: probability calibration, execution evidence, fee/entry economics, and sample/regime robustness.

The last valid checkpoint evaluated 1,058,045 proposals. Its inherited YES strategy selected 36 dates and showed **+17.70%** simulated return, with four of five folds positive. It failed because one fold returned **-100%**. Evidence was 4 A, 30 B+, and 2 B. V5A was stopped rather than cleanly terminalized, so this is an early-stop development snapshot, not a final verified result.

Why V5B: V5A showed that stricter selection could turn a broadly losing search positive, but its time instability was unacceptable. A bounded research architecture was used to look for a more stable selection mechanism on development data.

### V5B — four-population discovery and the NO-side lead

V5B used four independent populations:

1. forecast probability and calibration;
2. timing and execution evidence;
3. contract and relative value; and
4. robustness adversary.

Agents authored and reviewed hypotheses between epochs. Deterministic code admitted, deduplicated, scored, stressed, and ranked them. Cross-colony synthesis combined compatible nondefault changes. A global Pareto archive preserved candidates across forecast, return, stability, sample, and evidence-quality dimensions.

Result: 101 specifications produced 90 distinct trade ledgers. Two gate-passing specifications selected the same 30 trades, so they count as one discovery. The frozen policy buys **NO**, requires a 0.10 gap between the highest and second-highest bracket probabilities, and ranks eligible contracts by expected dollar profit.

On 64 exposed development dates it selected 30 dates and showed **+26.83%** simulated return after fees, four of five positive folds, **+21.60%** under a fixed two-cent adverse-entry stress, and **+21.14%** after removing the best day.

The limitations are decisive: only 3 trades were Grade A and all 3 lost; the positive result came from 27 B+ trades; 30 dates only met the minimum sample; an alternative preselection price stress broke the worst-fold gate; calibration was transferred; and repeated adaptive searches had exposed the period. V5B became the strongest economic hypothesis, not a confirmed trading system.

### V5B-next — richer weather features, negative economics

V5B-next tested six chronological weather models using cached HRRR/GEFS temperature, cloud, wind, and disagreement features crossed with four fixed economic policies. The cloud-and-wind ridge improved temperature MAE from 2.597°F for climatology to 1.801°F and improved Brier from 0.9156 to 0.7698.

Result: all 24 economic candidates lost money. The best result was **-3.71%**; the frozen weather-study leader returned **-8.50%**. This established an important principle: better temperature prediction and better overall probability scoring do not automatically identify mispriced contracts.

### Friend-method proxy and exact GFS/NAM/NBM branch

The friend's proposed architecture combined GFS, GFS Seamless, NAM, and NBM with chronological bias correction, constrained weights, uncertainty calibration, conservative probability screening, and a multi-bucket long-YES policy.

The first data-adapted proxy used existing HRRR/GEFS history. Its multi-bucket variant showed **+12.49%**, but failed sample, fold, worst-fold, two-cent stress, best-day-removal, and Grade-A checks. One best date accounted for more than the total profit.

The later exact GFS/NAM/NBM test rejected the method: the primary multi-bucket strategy returned **-30.50%** over 33 trades on 29 dates, with only 2 wins. Its overall Brier score was better, but selected contracts were severely overconfident. The exact test showed that the earlier positive proxy did not transfer to the requested model data.

### V5C–V5F — finite successor tests

These variants tested whether the friend-method ideas could improve the V5B lead:

- V5C: consensus veto;
- V5D: disagreement abstention;
- V5E: heavier probability tails; and
- V5F: fixed 50/50 stack of HRRR/GEFS and GFS/NAM/NBM probabilities.

None passed. V5F was best at **+5.13%** over 18 dates, but it fell below the 10% screen and became **-6.05%** after removing its best day. V5B remained strongest on the shared development cohort.

Why V6: the project had several isolated positive or forecast-improving methods. It needed one finite tournament with common dates, negative controls, behavioral deduplication, and independent ranking.

### V6 — multi-method tournament

V6 used four logical groups:

1. conditional weather uncertainty;
2. incremental information beyond the inherited probability chain;
3. decision and execution economics; and
4. null/adversarial explanations.

It accounted for all 28 registered specifications: 22 were evaluated and 6 were blocked by failed prerequisites, representing 19 distinct trade behaviors. Several methods improved Brier score, and fixed synthesis improved it further.

Result: no candidate passed. The strongest new diagnostic was GEFS-spread/NO/all evidence at **-1.11%** over 36 dates. The inherited V5B control was still **+25.60%** over 17 dates on the common cohort but failed the 30-date and fold gates. V6 reinforced that probability quality and trading value are separate.

### V6.1 — settlement-aligned, market-first redesign

V6 had compared new weather models mostly against an inherited weather-probability chain rather than the market itself. V6.1 therefore required source-specific settlement targets, a coherent 18:00 Kalshi probability vector, market-plus-weather scoring, Grade-A primary economics, negative controls, multiple-testing correction, and a global exposure ledger.

V6.1 stopped before spending its experiment clock or evaluating a scientific candidate. Independent review found that its first market baseline lost bracket identifiers while aligning probabilities and mixed execution grades. Its gates also demanded a sample larger than the available development cohort. This was a preregistration defect, not a model result.

### V6.2 — corrected market-first first wave

V6.2 fixed the bracket alignment and separated development gates from future 100-day confirmation gates. Its first wave found:

- calibrated HRRR/GEFS beat uniform, climatology, and the frozen weather reference on the 44-date weather cohort;
- market-only on 20 paid-book dates achieved Brier **0.4720** and log loss **0.7576**;
- the fixed 50/50 market-plus-weather blend was worse, with Brier **0.5941** and log loss **1.0895**.

The simple blend failed the market-relative gate. The run remained active after its first wave and did not become a terminal completed campaign. It nevertheless established the direction: weather must prove incremental value over a coherent market baseline, not merely beat another weather model.

### V7 — prospective shadow design, paused before it began

V7 preregistered a 42-day forward shadow comparison with frozen methods and zero orders. The user then clarified that the requested six weeks should be historical. V7 was paused before its first target date, so it contains no prediction, fill, or outcome. It was superseded by V7H.

### V7H — historical six-week causal replay

V7H covered August 4–September 14, 2026. All 42 days' forecasts and hypothetical orders were frozen before settlement outcomes were opened. Paid 18:00 and 18:00:05 books covered 40 dates; two missing dates became abstentions.

V5B showed **+27.75%** over 9 fills, 7 wins, 2 of 3 positive 14-day folds, a **+2.99%** one-sided bootstrap lower bound, **+23.74%** under two-cent stress, and **+20.44%** after its best trade was removed. V5F showed **+25.15%** over 8 fills but failed its bootstrap gate.

This appeared encouraging, but the dates had already influenced earlier V5/V6 research. V7H was causal stability evidence, not an untouched confirmation.

Why V7I: a six-week window and nine fills were too small. The same strategy needed a wider strict replay.

### V7I — wider 60-day strict replay

V7I extended July 30–September 27, 2026 and required strict paid books at 18:00 with the same order executable five seconds later. On the 55-date primary period, frozen V5B produced 13 fills, 8 wins, and **+8.24%** after fees. The added September 15–27 segment had 4 fills, 1 win, and **-47.68%**, pulling the result below the target.

The bootstrap lower bound was -26.43%, two-cent stress was +4.59%, best-winner removal left only +0.08%, and 3 of 5 folds were positive. This showed that the earlier +27.75% result was not stable enough for online use.

Why V7Y/V8: the economic selector was being driven by probabilities that were often overconfident. Before more trading tests, the project needed a full-year forecast-quality study and probability repair.

### V7Y — full calendar-2025 HRRR/GEFS probability audit

V7Y repaired the HRRR/GEFS availability policy and froze all 361 daily probability vectors before opening CLILAX labels. The final score used 359 dates; the primary interval contained 329 dates.

Raw V7Y achieved 33.74% modal-bracket accuracy, Brier **0.8590**, and log loss **6.6129**. It assigned exactly zero probability to the realized bracket on 52 dates and did not beat the uniform six-bracket baseline on the proper probability scores. The directional weather signal existed, but its probability layer was dangerously sharp and poorly calibrated.

### V8 — chronological probability repair plus strict economic replay

V8 evaluated a finite catalog of 104 deterministic repairs. Fifty-five passed all probability-development gates. The selected `rolling_confusion-alpha-2-w-0.75` method keeps 25% of the raw HRRR/GEFS vector and blends 75% from a Dirichlet-smoothed historical confusion distribution conditioned on the model's modal bracket. Each development prediction uses only earlier outcomes.

On the 329-date 2025 development interval, V8 improved:

- Brier: **0.8590 → 0.7507**;
- log loss: **6.6129 → 1.5188**;
- modal accuracy: **33.74% → 34.65%**;
- zero-probability realized outcomes: **52 → 0**.

Both proper scores improved in all five folds. The exact repair was then fit once on all 359 scorable 2025 dates, frozen, and applied without 2026 updates to strict V7I books.

On the 55-date primary replay it produced 13 fills, 7 wins, $1.3815 profit on $5.6185 outlay, and **+24.59%** capital-weighted simulated return. It remained **+19.02%** under two-cent adverse entry and **+14.01%** after removing its best winner.

V8 still failed promotion: only 13 fills, only 3 of 5 positive folds, a negative bootstrap lower bound, exposed dates, and an unweighted mean realized trade return of only 7.56%. The high aggregate return was promising but statistically fragile. V8 was frozen before any meteorological successor work.

### V9 — frozen physical-meteorology protocol

V9 preregistered the next causal question instead of modifying V8 after seeing more outcomes. Exposed diagnostics suggested:

- offshore-like days ran much warmer than HRRR and GEFS;
- persistent low-cloud days made HRRR too cold while GEFS was close;
- morning clouds that cleared created opposite HRRR/GEFS biases;
- weak/mixed coastal flow was hard;
- V8 was strongest in summer and weakest in winter.

V9 required observed KLAX cloud, ceiling, temperature, dewpoint, wind, and pressure; HRRR dewpoint and vertical temperatures for inversion strength; and the KLAX-minus-KDAG pressure gradient. It froze a six-candidate physical-weather protocol, chronological selection gates, and the later economic boundary. V9 itself was a protocol, not a fitted strategy.

### V10 — full meteorological combination study

V10 implemented the V9 direction as a bounded development experiment. Before acquisition it fixed six feature blocks:

1. season;
2. forecast marine layer;
3. observed marine layer;
4. KLAX-minus-KDAG pressure and coastal flow;
5. HRRR inversion and moisture; and
6. HRRR/GEFS uncertainty.

It acquired 19,148 archived KLAX/KDAG observations, 1,444 HRRR field slices containing dewpoint and 925 hPa temperature inputs, and 722 normalized day/lead rows across all 361 calendar-2025 dates. It tested every subset of the six blocks at 25%, 50%, and 75% overlay weights plus unchanged V8: **193 candidates total**.

Fifty-three non-control candidates passed all candidate-level development gates. The selected leader is `V10-pressure_and_flow-W75`:

- Brier: **0.7507 → 0.7343**;
- log loss: **1.5188 → 1.4846**;
- modal accuracy: **34.65% → 37.99%**;
- Brier improvement: 5 of 5 folds;
- log-loss improvement: 5 of 5 folds.

The benefit was concentrated on observed onshore-pressure days, where V8 accuracy was 42.44%. Offshore days remained difficult at 17.50% accuracy. Forecast-marine and ensemble-uncertainty blocks generally hurt matched combinations. Observed marine state and inversion/moisture had smaller mixed effects.

Because 192 alternatives were searched, a familywise audit was added. Across all combinations, adjusted Brier passed but adjusted log loss narrowly failed. Across the narrower 18 single-block methods, the pressure-only leader passed both adjusted tests. The leader is frozen as exposed development evidence.

V10 has **not** run a fixed market-return replay and has **not** established profitability. It is currently the best probability model, while frozen V8 is the strongest current probability-plus-economic research lead on the existing strict replay. Frozen V5B remains an important older economic comparator.

## Why we are where we are now

The project progressed by closing one source of false confidence at a time:

1. **V1–V2 fixed iteration.** The system learned to create, combine, criticize, and replicate new experiments rather than merely log empty epochs.
2. **V3–V4 fixed search coverage.** Better forecasting did not matter when spread, stale quotes, and fixed decision times prevented plausible trades.
3. **V5 fixed evidence quality.** Apparently excellent V4 returns were built on assumed fills, uncertain fees, few trades, and unstable folds.
4. **V5A/V5B found a selective mechanism.** Fewer, stricter NO-side trades performed better than broad long-YES trading, but most evidence was B+ and the period was repeatedly exposed.
5. **V5B-next, the friend method, and V6 separated forecasting from trading.** Several models improved Brier or temperature error while losing money.
6. **V7H/V7I tested temporal stability.** The strong six-week V5B result weakened to +8.24% when 13 more days were added.
7. **V7Y/V8 repaired overconfidence.** The raw weather model assigned zero probability to 52 actual outcomes; chronological confusion smoothing removed that failure and restored a strong, though small-sample, strict replay result.
8. **V9/V10 added physical meteorology.** Pressure and coastal flow produced a statistically stronger probability model, while several intuitively appealing feature blocks did not help.

The conclusion is precise: the repository has a plausible forecasting and selection direction, not a verified money-making system. The main remaining uncertainty is no longer whether HRRR/GEFS contains signal. It is whether the frozen V10 probabilities, combined with a frozen execution-aware selector, create a return that survives strict historical books and then genuinely new Grade-A evidence.

## Current scientific state and ranking

1. **Best probability candidate:** frozen V10 `pressure_and_flow-W75`.
2. **Strongest strict economic research lead:** frozen V8 probability repair with the V5B NO selector, +24.59% on 13 exposed Grade-A replay fills, but it failed sample, fold, bootstrap, and untouched-data gates.
3. **Older economic comparator:** frozen V5B NO/modal-gap policy, positive in its original development and V7H replay, but only +8.24% on the wider V7I primary period.
4. **Supporting diagnostic:** V5F improved probability accuracy and had some positive windows, but failed robustness and the 10% target.
5. **Rejected current forms:** V5A, V5B-next, the exact friend method, V5C–V5F as promotion candidates, and every V6 candidate.

No version is authorized for online betting.

## The next justified research step

After migration integrity is verified, the next campaign should be a separately registered, immutable V10 economic replay:

1. Freeze the exact V10 probability candidate, exact V5B-style selector or another predeclared selector, decision time, five-second arrival rule, fee engine, book-quality rule, date universe, and all promotion gates before reading economic outcomes.
2. Replay only against strict historical Grade-A books; keep missing dates as abstentions and do not substitute proxy prices.
3. Compare V10, frozen V8, frozen V5B, market-only, and simple negative controls on the identical eligible dates.
4. Report forecast scores, selected trades, aggregate return, mean trade return, fold returns, bootstrap lower bound, two-cent stress, best-day removal, capacity, and evidence quality separately.
5. Do not tune V10 from the replay outcome. A failure is a valid conclusion.
6. If and only if the fixed replay remains promising, collect at least 100 genuinely new, outcome-blind Grade-A days and run one confirmation after the universe and strategy are sealed. The existing 2025 and 2026 research dates are already exposed and cannot become new confirmation data.
7. A later read-only shadow phase may record quotes and hypothetical decisions. Paper or live trading requires a separate explicit authorization and is outside the current repository contract.

## Authoritative seals to verify after migration

- V7Y prediction freeze: `eab39e1cb031a6b4cec2bc79a44420f9ef989d74f4a73e7d6a2c825b2cd9d62c`
- V7Y completed summary: `879e159c42a46fd79d09be356e99f49b68ccb3db45f8bcba9dafa1166d6b9025`
- Frozen V8 strategy: `a227b8f72ad040c2411fe63d3863d1b9c9c11121c6df5fce2fadb198ea2379fd`
- Frozen V9 protocol: `b0dae199c11e753e663ce47009307570426f9b4abe0f48c9d67c94f064df4526`
- V10 acquisition protocol: `99ef913f8c0219217f06bbf7ec33c5466a99aa7dd8b290af6967cac75b13e7e0`
- V10 acquisition manifest: `ed7c8e7f84d096a09f16b285da80c86f7452faa1c53c86ff11840ade8b2800f9`
- V10 scoring freeze: `159938b537c1315c7cab605fa7877351aae56bde457780c337d0c5aec18d79d9`
- V10 result: `fdd16175e2ffa00ad529974ed8d7fdb5abac8aab1685766289cd0248adc2626d`
- V10 multiple-comparison audit: `70d121b65831094256bc6d9d6d0a2f9893ec535805d9e235cb6ecf86c1ad6cb7`
- Frozen V10 development candidate: `ec762e9524664b6b9892b4586b417ce7de357ef7e10576cac0b2a17c69824f79`

## Migration warning

The complete source workspace was approximately 74.3 GB. The normalized V7Y Parquet files contain absolute provenance paths rooted at:

`C:\Users\darks\Documents\Codex\kalshi\_weather\_llm`

Using the same path on the destination computer is safest. If the root changes, audit the absolute `source_path` and `index_path` fields before running anything. Preserve sealed artifacts byte-for-byte. Prefer a compatibility junction or a separately registered migration layer over rewriting frozen evidence and invalidating hashes.

Do not copy or trust the old `.venv`; recreate it from `requirements-local.lock` and `pyproject.toml`. The `.codex-build` and `.codex-slide-build` junctions are nonportable build helpers, not research inputs. Browser authentication, Probalytics sessions, credentials, and live account access are not part of the repository.

## Start here on the new computer

1. Resolve the migrated project root.
2. Read `AGENTS.md`, `GOALS.md`, and `README.md` before changing anything.
3. Read these history and result records:
   - `docs/NAVIER_STOKES_ARCHITECTURE_CROSSCHECK.md`
   - `docs/CAMPAIGN_PROTOCOL.md`
   - `docs/V3_IMPLEMENTATION_CONTRACT.md`
   - `docs/V5_FOUR_COLONY_VERIFICATION_CAMPAIGN_GOAL.md`
   - `docs/V5B_ARCHITECTURE.md`
   - `reports/CAMPAIGN_STOP_AND_RESULTS_2026-09-27.md`
   - `reports/v6-final-scientific-report.md`
   - `docs/V7H_HISTORICAL_SIX_WEEK_REPLAY.md`
   - `runs/replays/v7i-historical-20260730-20260927/V7I_SIXTY_DAY_RESULTS.md`
   - `reports/V7Y_HRRR_GEFS_CALENDAR_2025_ANALYSIS.md`
   - `docs/V8_PROBABILITY_REPAIR_AND_REPLAY_GOAL.md`
   - `docs/V9_METEOROLOGICAL_SUCCESSOR_GOAL.md`
   - `docs/V10_ALL_METEOROLOGICAL_COMBINATIONS_GOAL.md`
   - `runs/v10/all-meteorological-combinations/REPORT.md`
   - `runs/v10/all-meteorological-combinations/MULTIPLE_COMPARISON_AUDIT.md`
4. Confirm that `data`, `runs`, `configs`, `scripts`, `src`, `tests`, all versioned modules, and inherited V5/V6 modules are present. V7–V10 depend on shared earlier code and evidence; copying only the newest version folders is insufficient.
5. Recreate the environment without modifying frozen artifacts.
6. Verify hashes and offline safety before running a replay or new campaign.

With `PYTHONPATH=src;.` and the rebuilt environment, inspect and then run the existing V10 verification entry points:

```powershell
$env:PYTHONPATH = 'src;.'
.\.venv\Scripts\python.exe -m v10.protocol_freeze verify --project-root .
.\.venv\Scripts\python.exe -m v10.scoring_freeze verify --project-root .
.\.venv\Scripts\python.exe -m v10.freeze_candidate verify --project-root .
.\.venv\Scripts\python.exe -m pytest -q tests\test_v10_protocol_freeze.py tests\test_v10_evaluate_combinations.py tests\test_v10_freeze_candidate.py
```

If a verification fails, diagnose migration or path integrity. Do not regenerate a mismatched freeze to make the check pass.

## Required first response from the new chat

Before proposing new research, report:

- the resolved project root;
- whether the original absolute path was preserved;
- whether every required code, data, configuration, and run tree exists;
- whether each V7Y/V8/V9/V10 seal above verifies;
- whether copied absolute provenance paths still resolve;
- missing dependencies, files, or external sessions;
- whether the repository is safe to continue offline;
- your plain-language understanding of the architecture and the V1–V10 progression; and
- the exact next campaign you recommend, without launching it until migration validation is complete.

Do not describe V10 as profitable. Do not describe V8 or V5B historical returns as confirmed. Do not open a protected set, use network data, access a current/live market feed, or create a paper/live order during migration validation.

---
