# OpenAI Navier–Stokes Architecture Cross-Check

Date: September 25, 2026  
Scope: Compare OpenAI's publicly described Navier–Stokes research process, the intended design in the **Apply Navier Stokes Method** conversation, and the campaign actually executed in this repository.

Official source: [On the Navier–Stokes Millennium Prize Problem](https://openai.com/index/navier-stokes-solution/), OpenAI, September 8, 2026.

## Implementation update

The recommended V2 changes in this document were implemented on September 25, 2026. V2 campaign `local-20260925T170513829794Z` then completed four productive epochs and independently replicated 40 candidates. All 40 were rejected, no champion passed, and the protected final remained unopened. Its negative conclusion is preserved rather than used to extend the search.

The current Goal 3 responds to the limitations exposed by that run. [The V3 implementation contract](V3_IMPLEMENTATION_CONTRACT.md) retains independent exploration, synthesis, deterministic testing, replication, criticism, evidence-based allocation, and finite stopping while expanding the evidence to minute Kalshi history, exact settlement, HRRR/local observations, GEFS, regimes, richer probabilities, market residuals, and selective abstention. V3 requires a separate readiness artifact and cannot inherit V2 authorization merely because the controller structure is similar.

## Original V1 finding

The current project preserved the scientific control layer but did not preserve the central iterative discovery loop.

It implemented a safe, reproducible **candidate-screening campaign**:

```text
three predefined recipe families
        ↓
three tested candidates
        ↓
criticism + deterministic replication
        ↓
fixed development gates
        ↓
no candidate passes
```

The intended architecture was a **research-generation campaign**:

```text
independent exploration
        ↓
real experiments
        ↓
structured evidence exchange
        ↓
cross-group synthesis
        ↓
new or combined hypotheses
        ↓
resource reallocation
        ↓
another experimental epoch
```

The first pipeline is scientifically valid for rejecting a small registered set. It is not a faithful small-scale reproduction of the iterative architecture described by OpenAI or in the prior conversation.

## What OpenAI actually disclosed

OpenAI's public description establishes the following facts. It does not publish a complete internal scheduler, prompt library, communication protocol, or agent state machine.

1. A coordinating multi-agent system used an internal model that OpenAI describes as substantially more capable than GPT-6 Astra.
2. Agents could use tools, including cached-internet retrieval and code execution.
3. Agents were divided into communicating groups of varying sizes.
4. Separate groups received different formulations of the problem: Navier–Stokes variants A, B, C, and D.
5. Other groups attacked related, easier problems. Nearly 100 agents spent about 50 hours on the unforced Euler regularity problem.
6. When the Euler result made Navier–Stokes more promising, OpenAI moved resources away from other problems and supplied the Euler result to Navier–Stokes agents.
7. Different groups explored diverse approaches.
8. Codex consolidated useful intermediate results across groups. Follow-up prompts were built from those intermediate results and sent back into the research process.
9. The Navier–Stokes group used on the order of 10,000 concurrent agents. The result arrived about 88 hours after launch.
10. The Navier–Stokes effort generated roughly 2.7 million messages and 130 billion output tokens.
11. Analytical discovery was followed by a distinct formalization and verification stage in Lean, which took another 17 hours using GPT-6 Astra.

The critical pattern is:

```text
diverse formulations
→ related subproblems
→ intermediate discoveries
→ cross-pollination
→ new follow-up prompts
→ evidence-driven resource shifts
→ separate verification
```

Scale mattered, but the feedback mechanism mattered more.

## What the prior conversation intended

The **Apply Navier Stokes Method** conversation translated the disclosed pattern into a proposed Kalshi research laboratory with these features:

- 36 research directions organized into colonies;
- breadth in early epochs and concentration in later epochs;
- local evidence exchange within colonies;
- compact cross-colony evidence summaries;
- a global synthesizer that creates follow-up hypotheses;
- `CONTINUE`, `FORK`, `COMBINE`, `REPLICATE`, `CHALLENGE`, and `STOP` decisions;
- explicit compute reallocation toward promising areas while preserving alternative and adversarial work;
- experiments as the source of truth;
- cumulative hypothesis, experiment, evidence, and message registries;
- independent replication and a dedicated red team;
- a protected final evaluation set;
- a valid `NO_IMPROVEMENT` outcome.

That conversation was careful to distinguish proposed implementation choices from undocumented OpenAI internals. The 36 tracks, colony names, resource percentages, message schema, and campaign state machine were our design, inspired by OpenAI's published pattern.

## What this repository actually ran

The completed campaign was much narrower:

| Dimension | Executed campaign |
|---|---:|
| Research families | 3 |
| Candidate representation | Closed five-parameter Gaussian calibration recipe |
| Tested candidates | 3 |
| Epochs recorded | 3 |
| Epochs containing new experiments | 1 |
| Deterministic experiments | 3 |
| Local model calls | 15 |
| Tasks / attempts | 22 / 22 |
| Ledger messages | 10 |
| Concurrent local inference | 1 |
| Agent model | One pinned local gpt-oss-20b model across separate contexts |
| Reserved context tokens | 245,760 |
| Wall time | About 219 seconds |
| Protected-final evaluations | 0 |

All three tested candidates were created in Epoch 1. Epochs 2 and 3 created no new hypotheses and ran no new experiments.

The closed registry contained 34 unique recipes, but the campaign tested only 3 of them. The result therefore covers about 8.8% of even the narrow registered grid, before considering the much larger research catalog.

The ten messages were three replication requests and seven blocker/abstention notices. They did not form an interactive exchange in which one worker asked a targeted question, another ran a new investigation, and a later worker used the answer to construct a new experiment.

Some model-generated critic text also confused the chronology, describing candidates as evaluated only on 2024 even though 2024 was the fitting period and January–June 2025 was the development evaluation period. Deterministic evaluation remained authoritative, so this did not change the numerical result. It does show that this local worker was not reliably interpreting every research packet.

## Mechanism-by-mechanism comparison

| Mechanism | OpenAI public description | Prior intended design | Executed implementation | Assessment |
|---|---|---|---|---|
| Precise objective | Fixed mathematical problem and variants | KLAX probability forecast, then economic edge | Fixed KLAX target and 10% entry screen | Strong |
| Diverse formulations | A/B/C/D plus diverse approaches | 36 tracks across several colonies | Three narrow calibration families | Weak |
| Easier subproblems | Euler result informed Navier–Stokes | Weather accuracy → calibration → price comparison → cost survival | These metrics exist, but one compound gate controls advancement | Partial |
| Independent exploration | Separate groups start from different formulations | Independent workers before sharing winners | Three Epoch-1 explorer contexts with a closed option list | Limited |
| Communication | Agents communicate within groups | Structured messages and evidence exchange | Mostly replication requests and abstention notices | Weak |
| Cross-pollination | Codex consolidates results into follow-up prompts | Synthesizer creates combined hypotheses | Synthesizer was forced to return no candidate | Missing |
| Iterative experimentation | Intermediate results guide later work | New experiments every productive epoch | Epochs 2 and 3 ran no experiments | Missing in practice |
| Resource reallocation | Agents shifted toward promising problem | Evidence-driven colony budgets | Fixed loops over tracks 1–3 | Missing |
| Alternative search | Diverse groups continue exploring | Protected alternative-search allocation | No proposals outside the closed recipe grammar | Missing |
| Adversarial testing | Separate verification followed discovery | Critics, red team, replication | Critic plus deterministic arithmetic replication | Strong but narrow |
| Formal verification | Lean formalization after discovery | Independent verifier and protected final | Saved-artifact arithmetic verified; raw forecast refit and fills not independently verified | Partial |
| Protected final | Not the focus of public description | One-use sealed final evaluation | Final period stayed sealed because no candidate passed | Strong |
| Honest stopping | Research outcome reported as found | `NO_IMPROVEMENT` is valid | Negative result retained | Strong |

## Why the iterative part disappeared

The behavior follows directly from the implementation.

### 1. The search language was closed before research began

`src/klax_lab/campaign.py::candidate_options` restricts all proposals to a five-parameter recipe:

```text
GFS weight
bias mode
spread mode
spread scale
disagreement coefficient
```

This is useful for a controlled calibration sweep. It excludes most of the prior 36-track catalog: forecast revisions, marine-layer regimes, cloud clearing, analogs, intraday observations, measurement effects, price residual modeling, entry timing, and genuinely new model forms.

### 2. Synthesis could describe a follow-up but could not create one

Every epoch asked the synthesizer to use:

```text
action=abstain, candidate=null
```

The resulting synthesis record could enter the evidence board, but it could not produce a candidate, a task dependency, or an experiment. This severed the feedback edge between synthesis and the next experimental round.

### 3. Epoch scheduling was static

The coordinator loops through exactly three tracks in every epoch. It has no allocator that increases resources for a productive track, funds a surprising side result, requests a targeted dataset analysis, or reserves a share for alternatives and red-team work.

### 4. Later workers could only select another registered recipe or abstain

They could not propose a new feature, new model, new diagnostic, or a combination assembled from prior evidence. Once the first three candidates lost money, later workers rationally abstained.

### 5. The gate combined different scientific questions too early

Weather distribution accuracy, bin calibration, assumed-fill return, cost stress, replication, and critic approval all had to pass simultaneously. This is appropriate for final promotion, but it is too strict as the only signal for allocating exploratory research. A weather improvement can be worth developing even when the current entry rule is poor; a promising pricing residual can be worth studying even when it is not yet execution-ready.

### 6. Completion counted empty research epochs

After the fixed epoch loop, the campaign could finish with `NO_IMPROVEMENT` even if later epochs produced no hypotheses or experiments. That is a valid conclusion about the three tested recipes, but not about the broader research space.

## Corrected small-scale architecture

The control and validation work should remain. The next version should change the discovery engine.

### Layer 1 — immutable control plane

Retain:

- frozen chronological data splits;
- offline execution;
- as-of and leakage controls;
- immutable raw data and hashes;
- deterministic metrics and replay;
- budgets and timeouts;
- typed evidence records;
- one-use protected final evaluation;
- explicit `NO_IMPROVEMENT` and `INSUFFICIENT_EVIDENCE` outcomes.

### Layer 2 — staged research questions

Do not make one gate answer every question. Use four stages:

1. **Forecast skill:** Does a method improve CRPS, MAE, tail behavior, or regime-specific errors on rolling development folds?
2. **Probability calibration:** Does it improve bin probabilities, reliability, sharpness, and proper scores?
3. **Market information:** Does the model add information beyond contemporaneous market probabilities without using outcomes or unavailable data?
4. **Economic simulation:** Does a frozen entry policy retain positive return under fees, slippage, uncertainty, and capacity constraints?

A candidate advances to the next research stage when it passes that stage's criteria. Only final promotion requires all stages.

### Layer 3 — six initial colonies

Start with six logically separate colonies. They may run serially on one GPU; logical independence matters more than simultaneous execution.

1. **Forecast ensemble and bias** — model weights, lead times, revisions, grid cells, residual structure.
2. **LAX meteorology** — marine layer, cloud clearing, coastal flow, Santa Ana regimes, seasonal behavior.
3. **Observations and target measurement** — KLAX observations, high-so-far, reporting precision, climate-day and settlement reconciliation.
4. **Probability and calibration** — distributions, heteroskedasticity, tails, conformal/empirical calibration, regime mixtures.
5. **Market and execution** — market residuals, bin coherence, timing, liquidity evidence, fees, and abstention policies.
6. **Adversarial and alternatives** — leakage audits, simpler challengers, falsification, and from-scratch alternatives.

### Layer 4 — a real iterative epoch

Each epoch should execute this state machine:

```text
INDEPENDENT_DISCOVERY
    each colony proposes hypotheses without seeing a declared winner
        ↓
EXPERIMENT_COMPILATION
    host validates a typed experiment plan and compiles it to approved operations
        ↓
PARALLEL_OR_SERIAL_EXECUTION
    deterministic runners create predictions, diagnostics and artifacts
        ↓
LOCAL_CRITIQUE
    another worker challenges each result and can request a targeted check
        ↓
CROSS_COLONY_SYNTHESIS
    synthesizer emits 0–N executable follow-up specifications
        ↓
PORTFOLIO_ALLOCATION
    next-epoch budget is assigned from evidence
        ↓
NEXT_EPOCH
```

The synthesizer must be allowed to output typed follow-up candidates. The host must validate those candidates before execution. Safety should come from the candidate schema and deterministic compiler, not from forcing synthesis to abstain.

### Layer 5 — evidence-driven allocation

A practical small campaign can use:

```text
50%  deepen supported directions
20%  combine findings across colonies
15%  independent alternatives
15%  adversarial replication and falsification
```

These percentages are our policy, not an OpenAI-published rule. Reallocation decisions must cite evidence IDs and create explicit next-epoch tasks.

### Layer 6 — minimum productive scale

For a faithful local pilot:

- 6 colonies;
- 3 independent discovery contexts per colony in Epoch 1;
- 6–10 executable experiments per epoch;
- 3–5 epochs;
- 2–4 cross-colony combined candidates per later epoch;
- at least one independent alternative and one adversarial task every epoch;
- approximately 60–120 local model calls and 20–40 deterministic experiments;
- early stopping after two consecutive epochs produce no novel executable hypothesis, or when the fixed budget is exhausted.

This is still tiny compared with OpenAI's reported effort. It preserves the causal structure of the loop rather than imitating the agent count.

## Required implementation changes

1. Replace `candidate_options(track)` as the sole search space with a versioned, typed experiment DSL.
2. Keep approved operators bounded, but let agents compose new pipelines from those operators.
3. Allow the synthesizer to return validated follow-up candidates and task requests.
4. Add parent evidence and parent hypothesis links to every combined candidate.
5. Add a portfolio allocator that makes `CONTINUE`, `FORK`, `COMBINE`, `REPLICATE`, `CHALLENGE`, and `STOP` decisions.
6. Make the next epoch depend on those allocation decisions instead of a fixed `for track in range(1, 4)` loop.
7. Require at least one new executable experiment for an epoch to count as an experimental epoch.
8. Separate stage-specific research gates from the final economic promotion gate.
9. Add rolling development folds so iterative research is not optimized against one visible six-month aggregate.
10. Expand evidence packets beyond truncated result summaries to curated diagnostic tables, failure cases, regime slices, and residual examples.
11. Add explicit targeted-question messages that can generate follow-up tasks, not only passive ledger messages.
12. Report search coverage: proposed, compiled, executed, rejected, duplicated, combined, replicated, and falsified hypotheses by colony and epoch.

## Conclusion

The completed campaign did not fail because iteration was attempted and exhausted. It tested three constrained recipes in the first epoch, then stopped generating experiments. Its negative result is trustworthy for those recipes under the saved assumptions, but it does not answer whether the broader multi-agent research architecture can find a useful Kalshi method.

The next campaign should preserve the existing foundation and replace the discovery loop. The central acceptance test is simple:

> Does evidence from Epoch N create a materially new, executable, independently testable experiment in Epoch N+1, with a recorded reason for the resource allocation?

If the answer is no, the system is logging rounds rather than performing iterative research.
