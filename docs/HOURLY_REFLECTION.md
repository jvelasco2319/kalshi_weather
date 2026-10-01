# Hourly reflection and resource reallocation

The registered interval is 3,600 seconds. At a due checkpoint the controller starts no new candidate; a running evaluation may finish within its timeout. The research host should stop issuing new experiment work, collect the active agents' findings, and submit a bound reflection packet. Read-only analysis of the checkpoint is allowed while reviewing it.

Research resumes after review finishes, as requested. There is no compulsory hour of waiting. An epoch's ordinary independent review remains pending if reflection interrupted it.

## Scorecard

The controller records each colony's distinct reproduced execution behaviors, passing candidates, best primary metric, improvement over the registered baseline, fraction of gates passed, newly available evidence, current allocation, and failure checkpoints. Duplicate behaviors do not increase the trial count. A raw metric lead without gate passes cannot enter the exploitation pool.

Default allocation after review:

- 60% of finite experiment slots to up to two independently reviewed winning families.
- 20% across active nonadversarial colonies for continued exploration.
- 20% protected for adversarial experiments.

Without a qualifying winner, the nonadversarial 80% is shared across research colonies. Shares guide available queued experiments over time, not guaranteed CPU utilization or paid model spend. A colony with no executable proposal does not force the host to invent busywork. Adversarial review itself is mandatory regardless of how many adversarial experiment proposals are queued.

These family percentages divide research experiment slots. The separate 25% verification and 5% reporting reserves divide the global local-credit budget. They are different levels of allocation, not percentages to add together.

## Retirement and new colonies

A retirement requires at least three distinct reproduced trials and two checkpoints with **new** failed evidence, no reproduced all-gate candidate, no positive gain over the registered baseline, a reason, and two independent nonfamily reviews of the artifacts. Re-reading identical failures does not count as a second failed checkpoint. A small sample or unavailable inputs do not establish a useless direction.

The falsification family cannot be retired. At least two independent nonadversarial families must remain active. Retired queues remain in the audit record and are not executed.

A child requires an active parent, reproduced all-gate parent candidates, a distinct question, its counter-hypothesis, and two independent nonfamily reviews. The registered total-colony cap remains binding. Children share the parent's exploitation pool, so splitting a winning group into several names does not multiply its family allocation.

## Audit and timing

Reflection packets bind the exact sealed request and its candidate hashes. Requests and decisions remain under `reflections`. The next checkpoint stays anchored to the original schedule. If review takes longer than an interval, missed intervals are recorded rather than triggering repeated empty reviews.

No reflection refunds attempts, extends the absolute deadline, changes frozen inputs/source/gates, grants confirmation-data access, or creates new model capacity. The CLI is a controller, not a timer daemon; the host must invoke it at task boundaries and at least hourly while research is ongoing.

An early checkpoint can use a packet with `kind` (`breakthrough`, `contradiction`, `dependency_change`), a concrete `reason` and actual `candidate_ids`. Submit it with `reflection --packet ...`. Early review does not postpone the next scheduled hour. Candidate versions and applicability states are pinned alongside artifact hashes; changes during review require a transparent checkpoint repair/reclassification rather than silently using a stale scorecard. Artifact-only campaigns use their claim records, and quarantined evidence is excluded.
