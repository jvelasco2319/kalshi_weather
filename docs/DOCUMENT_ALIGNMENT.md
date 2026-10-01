# Alignment with the supplied research handbook

Reference: [Research Swarm Architecture and Prompt Template](Research_Swarm_Architecture_and_Prompt_Template.docx), version 1.0, 1 October 2026. Source SHA-256: `d6b8844884c9f0d3bd95bb067f74cb84fd2bf3266aab343a0984b655affb92f3`.

This branch implements the handbook's proposed design where the local controller can enforce it. The original source document is included unchanged in this folder. Its cited research motivates the proposal; this implementation does not independently reproduce those papers, OpenAI's internal system, or their reported performance.

| Handbook sections | Change in this branch | Enforcement or remaining boundary |
|---|---|---|
| 3–4 Contract and authority | Versioned definitions, assumptions, exclusions, novelty, outputs, authorities and domain checks; separate operational plans | Source and input bindings are frozen. Changes require a registered successor. |
| 4 Topology and capability checks | Single, independent and coordinator task policies; explorer/root/depth caps; declared models/tools | Local admissions are enforced. Actual provider calls, model IDs and tool permissions must be checked by the host. |
| 5 Lifecycle and checkpoints | Actual task delivery, review, numerical checking, shared synthesis, hourly and evidence-triggered reflection | An event checkpoint binds evidence and keeps the hourly schedule. |
| 6 Versioned records | Contract, task, candidate, artifact, source, run, review, check, decision, memory, plan and completion records in SQLite | Original versions and audit events remain accessible. Hashes are integrity checks, not authentication against a user who can rewrite the entire store. |
| 6 Separate status axes | Execution, research outcome, review, verification, acceptance and applicability are distinct | Passing a numerical check leaves claim acceptance pending. |
| 6 Disputed dependencies | Quarantine traverses versioned dependency edges; new work/freeze rejects disputed premises | Authority-managed revalidation restores only the cited record; descendants need their own revalidation. |
| 7 Memory and communication | Task-local workspace/brief; project evidence and procedural memory stored separately; compact synthesis links originals | Retrieved methods remain conditional, tentative recipes until checked for the new problem. |
| 8 Scheduling | Atomic task claims, attempt IDs, fencing tokens, leases, immutable retransmissions, durable artifacts and acknowledgment | Host launches workers. Unknown running/completion states remain counted; retries are bounded. |
| 8 Resource control | Research/verification/reporting allocations, atomic reservations, unknown-usage retention, logged transfers and shutdown time | Local credit admissions are enforced. Provider spending is not automatically metered or capped by this package. |
| 9 Domain checks | Numerical or artifact-only campaigns, artifact-based claims and domain instructions; version-matched check reports with exact scope and diagnostics | No Lean/kernel checker, literature evaluator, experimental apparatus or universal statistical verifier is bundled. |
| 10 Protected acceptance | Worker packets cannot write decisions; acceptance requires configured checks, authority action and an externally enforced boundary | Default boundary is unconfigured and acceptance fails closed. Role labels do not install isolation. |
| 10 Stopping | Retry limits, time/budget admissions, preserved outstanding usage, distinct investigation completion classes | Scientific stagnation still needs substantive host judgment: a cheap discriminating test can be useful even with no positive score. |
| 11 Evaluation | Manifest-based comparison of single, independent, coordinator and full designs; matched declared caps, actual/unknown usage, repeated-run variation | No provider benchmark is run automatically; supplied outcomes still require independent evaluation. |
| 12–17 Prompt and release kit | Worksheet, coordinator prompt, role/domain guides, completion and release requirements | Fill actual capabilities and required domain checks before starting real research. |

Pilot settings remain configurable: three explorer slots plus one coordinator, one delegation level, two unchanged attempts, 25% verification reserve, 5% reporting reserve, and hourly reflection. These are proposed defaults, not experimentally established optima.

Software tests establish the implemented transitions and failure handling. They do not establish scientific reliability, research performance or that the combined architecture is optimal.
