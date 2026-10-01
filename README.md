# Research swarm accurate

This branch extends the original research swarm template using the supplied Research Swarm Architecture and Prompt Template handbook, version 1.0 dated 1 October 2026. Real agents or people propose and review work; Python manages durable tasks, versioned evidence, resource reservations, numerical checks and scoped decision records.

It includes four initial research colonies, hourly and evidence-triggered reflection, child colonies, resource reallocation, task leases and fencing, separate scientific states, an evidence graph, two persistent memory scopes, and a visual report. The included example is **synthetic regression**, not a Kalshi strategy or a scientific finding. The original published template remains on `research_swarm_template`.

## 1. Download and set up

Requirements: Git and Python 3.11 or newer. Windows instructions:

```powershell
git clone --branch research_swarm_accurate --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
powershell -ExecutionPolicy Bypass -File .\scripts\setup_template.ps1
```

Setup installs the local package and runs its tests. It does not create a campaign, contact an AI model, acquire data, or start research. macOS/Linux users can run `python3 -m venv .venv`, `.venv/bin/python -m pip install -e '.[dev]'`, and `.venv/bin/python -m pytest` instead.

## 2. Start the synthetic example

From the cloned folder:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli init --run-dir runs/demo
.\.venv\Scripts\python.exe -m research_swarm.cli epoch --run-dir runs/demo
.\.venv\Scripts\python.exe -m research_swarm.cli status --run-dir runs/demo
Start-Process .\runs\demo\report.html
```

The first epoch runs the registered controls and stops at `AWAITING_REVIEW`. That pause is intentional: another parameter sweep does not substitute for agent research.

## 3. Run the actual agent loop

Give your research host the instructions in [docs/CHATGPT_SETUP_PROMPT.md](docs/CHATGPT_SETUP_PROMPT.md). For each round:

1. Read the active colony task files under the campaign's `tasks` folder. Claim their durable task IDs through the runtime, then dispatch real agents within the host's available capacity. Each claim supplies its own attempt workspace, owner, fencing token and reservation.
2. Submit each worker's evidence packet and acknowledge durable artifacts. Reconcile actual usage or retain its reservation while usage is unknown. A delivered task does not grant scientific acceptance.
3. Have at least two reviewers from different, nonproposing colony families inspect each candidate's artifacts.
4. Consolidate their findings into a review packet: shared synthesis, supported/rejected/unresolved lessons, and evidence-linked follow-up proposals.
5. Submit the packet and run the next epoch:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli review --run-dir runs/demo --packet path/to/review.json
.\.venv\Scripts\python.exe -m research_swarm.cli epoch --run-dir runs/demo
```

Packet structure is in [docs/PROTOCOL.md](docs/PROTOCOL.md); the ownership and submission steps are in [docs/RUNTIME.md](docs/RUNTIME.md). The runtime enforces admitted task leases and reservations; **it does not launch hosted agents or make API calls**. The host must route its real model calls through these leases and enforce tools and operating-system permissions. Unknown provider completion continues occupying capacity after cancellation or lease expiry until a result or host stop receipt arrives. With four available slots, a coordinator can use three researchers and rotate colony assignments.

## 4. Reflect every hour

At the next task boundary after 3,600 seconds, the controller blocks new experiments and enters `AWAITING_REFLECTION`. The host should also check the controller at least hourly while agents are researching, rather than waiting for an epoch command.

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli reflection --run-dir runs/demo
.\.venv\Scripts\python.exe -m research_swarm.cli reflect --run-dir runs/demo --packet path/to/reflection.json
```

Review finishes, resources change, and research can resume immediately. There is no extra hour of idle time. The default allocation reserves 20% for adversarial experiments and 20% for continued exploration; 60% can go to independently reviewed, reproduced winners. Weak colonies need sufficient new failed evidence before retirement. Child colonies share their parent's family allocation and do not increase the total budget or hosted capacity.

A breakthrough, contradiction or changed dependency can request an earlier checkpoint using an evidence-linked trigger packet. This preserves the original hourly schedule. Disputed or quarantined candidates cannot enter the winner pool.

See [docs/HOURLY_REFLECTION.md](docs/HOURLY_REFLECTION.md) for thresholds and exceptions. The controller is invoked by the host; it is not a background scheduling service.

## 5. Reach a conclusion

Submit the final independently reviewed epoch with `action: STOP`. The report ranks gate-passing candidates before failed ones and shows identical execution behaviors. A positive development result remains provisional.

For a main-question candidate that passes every gate:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli freeze --run-dir runs/demo --candidate-id candidate-FULL_HASH
```

Only when a separate, genuinely unused confirmation dataset and its access controls are ready:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli confirm --run-dir runs/demo --confirmation-data path/to/new_confirmation.json --authorize-one-shot
```

The confirmation claim is consumed before the controller reads that file, including when scoring fails. Never try another candidate on the same final dataset. The runner checks file identity but cannot establish that a differently serialized file has never been seen by a person or adapter. Protect confirmation data outside the researchers' access until selection is frozen. Final scoring must fit the remaining registered verification window.

**Scientific acceptance is a separate step.** The configured verification authority supplies version-matched domain-check records; the acceptance authority records precisely what was established. The default example has `permission_boundary: unconfigured`, so accepted claims are blocked even when numerical gates pass. The package does not install operating-system isolation. Set `host_enforced` only after implementing and checking that boundary externally. See [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).

For mathematics, literature and empirical work, use the artifact-based `claim` and `review-claim` path described in that guide instead of forcing the problem into the numerical example.

An artifact-only example registers pinned local documentation without a numerical adapter, metric, parameter grid or fake baseline:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli init --spec config/example_artifact_problem.json --run-dir runs/artifact-demo
.\.venv\Scripts\python.exe -m research_swarm.cli tasks --run-dir runs/artifact-demo
```

Proceed with task claims, actual evidence submissions, claim review and configured checks. The `epoch` command is intentionally unavailable for artifact-only campaigns.

Refresh the graphic after meaningful task/claim/decision changes with `report --run-dir runs/artifact-demo`; this consumes one local reporting credit. Read-only `status`, `tasks`, `claims`, and `resources` remain available without invoking a provider. The graphic shows the current claim versions, acceptance/applicability, numerical evidence where available, family allocations and reserved resources.

No successful candidate is also a valid conclusion. Report what failed, what remains untested, and whether the registered catalog was exhausted. A budget stop is not a proof that no solution exists.

## 6. Adapt it to your research problem

Before registration, define the problem and alternative formulations, main and easier questions, control, data provenance, measurable gates, finite experiment language, colony briefs, budgets, and hourly policy. Replace the synthetic adapter with an approved module under `src`; it must return numerical metrics and an auditable ledger and supply a separate reproduction path.

The controller runs only the registered executable language. Agents may invent broader mechanisms, but a new algorithm, dataset, gate, dependency or source change requires a **separately registered successor**. Copy the lessons forward, keep the failed record, and use new confirmation evidence. Increasing the search language silently would invalidate the original test.

Campaigns hash-bind source and development inputs. Pause/resume preserves absolute deadlines and consumed attempts. SQLite stores tasks, attempts, reservations, original record versions, dependencies and chained audit events. Research, verification and reporting allocations are separate. The default 70/25/5 split uses **local operation credits**; it is not a dollar or token spending cap. External provider usage and cancellation receipts are host-managed. These are local integrity controls; run trusted adapters under appropriate filesystem/network permissions.

This is schema version 2. Existing version-1 campaigns should stay on their pinned original branch/commit. Create a separately registered successor to adopt this architecture; never rewrite an old registration.

## Where to read next

- [Architecture and OpenAI-derived attributes](docs/ARCHITECTURE.md)
- [Colony roles](docs/COLONIES.md)
- [Review and experiment protocol](docs/PROTOCOL.md)
- [Hourly reflection](docs/HOURLY_REFLECTION.md)
- [Kalshi research lineage](docs/KALSHI_LINEAGE.md)
- [Prompt for a new research host](docs/CHATGPT_SETUP_PROMPT.md)
- [Document-to-implementation alignment](docs/DOCUMENT_ALIGNMENT.md)
- [New problem worksheet](docs/PROBLEM_WORKSHEET.md)
- [Task runtime and resource controls](docs/RUNTIME.md)
- [Claim acceptance and integrity recovery](docs/ACCEPTANCE.md)
- [Architecture evaluation](docs/EVALUATION.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

The historical trading packages remain on `swarm_setup_offline`, `swarm_setup_online`, and `swarm_vanilla_v10`. This branch does not include paid/raw market data, credentials, live feeds, or order code.
