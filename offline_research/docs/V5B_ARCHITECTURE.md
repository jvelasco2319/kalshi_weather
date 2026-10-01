# V5B implemented research architecture

This describes the registered V5B pilot implementation, rather than the full future research wishlist. The campaign is offline, development-only and bounded by eight epochs, 160 unique candidates and a fixed two-hour deadline. Its initial population contains 22 agent-authored seeds. Four independent colony populations retain up to eight candidates each.

## Actual reasoning versus deterministic execution

Codex agents author causal hypotheses and inspect results between epochs. Research packets contain named reviewers, specific candidate references, findings and optional new typed proposals. The controller pauses at `AWAITING_AGENT_REVIEW` after each nonterminal epoch. At least two distinct reviewing colonies are required before another epoch can proceed; a colony cannot review its own candidates in the accepted packet.

This is a real opportunity for new agent reasoning between epochs, but the local evaluator does not itself call an LLM. The 22 seed records have `codex_agent_seed` provenance. Agent followups use an explicit agent-review origin. Four finite sensitivity schedules have `deterministic_sensitivity` provenance; compatible combinations have `deterministic_cross_colony_synthesis` provenance. Proposal counts are not model-call counts.

The controller validates reviewer identity fields and candidate references, not the truth or quality of natural-language criticisms. Deterministic falsification artifacts are separate from actual agent reviews. The optional `critiques` argument to the bounded proposal function does not automatically turn prose into new code. Agents must explicitly submit new supported parameter records.

## Four independent populations

1. **Forecast probability:** raw versus inherited calibrated forecast ablations, chronological rank-frequency calibration, probability temperature, uniform blending, adjacent-bracket mass diffusion, entropy and modal-gap filters.
2. **Timing and execution:** evidence grade eligibility, spread constraints and adverse-entry screening on frozen 18:00 UTC inputs. This pilot does not implement new decision times, depth imbalance or partial fills.
3. **Contract and relative value:** YES/NO eligibility, interior versus tail contracts, price bands and expected-dollar-profit versus expected-relative-return ranking. One purchased contract is selected per eligible day; multi-leg arbitrage is not implemented.
4. **Robustness adversary:** uncertainty haircuts, edge thresholds and price floors; cross-review checks temporal failure, evidence composition, adverse entry and dependence on the best day.

Within each colony, retained candidates are ranked by number of failed gates, sample eligibility, positive-fold count, worst-fold return, aggregate return and evidence quality. All colonies do not mutate one shared incumbent. A separate global Pareto archive retains nondominated candidates across realized return, estimated expected return, worst fold, positive-fold count, selected days, evidence quality, negative Brier score and two-cent adverse return. This many-dimensional archive can be large; archive membership is not a promotion decision.

## Epoch flow and novelty

The first epoch evaluates the 22 seeds. Later epochs take actual review-packet proposals, add up to four bounded sensitivity proposals per colony from its own retained parents, and attempt compatible combinations of adjacent colony leaders. This is a limited synthesis schedule, not exhaustive search over every pair.

Canonical executable parameters produce a SHA-256 candidate fingerprint. Defaults are filled, grade/side sets are ordered, and inactive calibration settings are normalized. The candidate registry is the hashed per-candidate JSON directory: a matching fingerprint is skipped before evaluation. Narrative changes do not create novel candidates. Distinct parameter settings can still select the same trades; the current controller does not deduplicate by trade ledger or mathematical equivalence.

Synthesis combines nondefault changes only when the parents come from different colonies and changes do not conflict. It records both hypothesis parents and requires ablation testing in the hypothesis text. Recording a falsification requirement does not mean that every custom free-text test is automatically executed. The evaluator executes the fixed tests described below; agents review additional causal implications.

After each epoch the controller records new frontier IDs, duplicates, population membership and deterministic criticism. Two stagnant epochs set `requires_new_causal_research`; the controller still pauses for agent review after every epoch. It does not automatically reallocate CPU/model-call budgets among colonies or automatically terminate on stagnation. Agents must redirect the next packet. Finite sensitivity proposals stop after four schedules, but agent-authored proposals can continue within the registered epoch, candidate and time limits.

## Development data and evaluation

The input loader verifies inherited manifests and returns only the 64 chronological development dates and their labels. The frozen probability file contains outcome-blind probabilities for both partitions; the loader filters to development before exposing its evaluation frame. V5B reads no protected confirmation labels. V5A's 28-day holdout remains reserved to V5A and cannot serve as an unbiased V5B test after V5A consumption.

The evaluator uses exact event eligibility flags and the inherited historical fee and settlement functions. An eligible day's contract probabilities are transformed, filtered and scored; at most one contract is chosen. Expected return is `(model probability - entry outlay) / entry outlay`, where outlay includes exact fees. Aggregate simulated realized return is total simulated net profit divided by total outlay. These are different statistics and neither is realized account profit.

Expanding rank calibration updates only after the scored date. It abstains during its minimum-history warmup. Five chronological folds divide the development dates. Reusing these dates across adaptive proposals still creates selection bias; temporal folds are not untouched confirmation.

Each result includes trades and abstentions, multiclass Brier score/log loss, evidence counts, fixed-selection one/two/three-cent entry stresses, best-profit-day removal and fixed-selection Grade-A-only sensitivity. Entry stress recomputes fees and caps entry at 100 cents. It is a cost sensitivity test, not an order-book queue or partial-fill simulator. Grade A does not imply an actual order was filled: all simulation records explicitly mark verified fills false. B/B+ remain weaker evidence.

## Registered development gates

- At least 30 selected days.
- At least 10% aggregate simulated realized return and 10% mean estimated expected net return.
- At least four positive chronological folds; worst nonempty fold at least -10%.
- Evidence score at least 0.65, using A=1, B+=0.65 and B=0.30. These are protocol weights, not empirical fill probabilities.
- Strictly positive fixed-selection two-cent stress return and best-day-removed return.
- Multiclass Brier no worse than the inherited baseline, within numerical tolerance.

A passing development screen is not confirmation. The code does not stop early merely because a candidate passes, and the selected final development leader can still fail gates. The final report must show failures explicitly.

## Integrity, control and recovery

Registration hashes V5B/V5A Python sources, campaign config and goal, the bound universe, probability manifest/output, development labels and outcome-blind event rules. State, candidates and campaign artifacts carry self hashes. A Windows kernel file lock prevents concurrent campaign writers. Atomic pending-file replacement reduces partial writes. Status and epoch entry verify registration bindings and campaign identity/deadline.

Replay denies Python `socket.socket` and `socket.create_connection` in process. This is not an operating-system network sandbox. There are no acquisition or order calls in the research loop. The architecture must not advertise stronger process isolation than implemented.

Commands support register, epoch, status, review, stop, resume and freeze. A stop request is observed between candidates. Resume keeps the original deadline and counters. Interrupted epoch artifacts remain in the registry, allowing duplicate skips; recovery does not claim transactional rollback of an entire epoch. Review waits consume the same absolute wall budget. A scheduled monitor must invoke status/allowed actions; the evaluator itself does not run indefinitely in a background reasoning loop.

## Final freeze and confirmation limitation

After development completion, `freeze` records one ranked candidate, its parameters, candidate/registration hashes and a timestamp. Repeating freeze returns the existing artifact. The freeze marks confirmation blocked and grants no holdout authorization. The implemented controller has no holdout evaluation action. A separately bound untouched confirmation period and preregistered one-shot protocol are required before confirmation can occur. The current pilot can finish with an informative development-only result.

## Blocked research backlog

Run-to-run weather changes, HRRR/GEFS member disagreement, airport/season/regime features, alternative decision times, quote age, depth imbalance, partial fills and synchronized multi-leg contract strategies require additional provenance-bound inputs and/or evaluator logic. They remain explicit blocked hypotheses. New features cannot be inserted into this frozen pilot: they require a separately registered successor. Missing features must never silently map onto an existing parameter.

The structural interchange schema is `schemas/v5b-hypothesis.schema.json`; runtime normalization, fingerprint checks, cross-field price ordering and final evaluator validation remain authoritative. The schema is documentation and validation support; the frozen controller does not load it.
