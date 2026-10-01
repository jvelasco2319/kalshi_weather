# Research swarm template

This branch turns the lessons from the Kalshi research into a reusable, bounded research controller. Real agents or people propose and review experiments; Python evaluates them, reproduces the numerical result, records failures, and enforces the registered rules.

It includes four initial research colonies, evidence-based iteration, hourly reflection, child colonies, resource reallocation, a visual report, and separate final confirmation. The included example is **synthetic regression**, not a Kalshi strategy or a scientific finding.

## 1. Download and set up

Requirements: Git and Python 3.11 or newer. Windows instructions:

```powershell
git clone --branch research_swarm_template --single-branch https://github.com/jvelasco2319/kalshi_weather.git
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

1. Dispatch the active colony task files under the campaign's `tasks` folder to real agents, within the host's available capacity.
2. Have at least two reviewers from different, nonproposing colony families inspect each candidate's artifacts.
3. Consolidate their findings into a review packet: shared synthesis, supported/rejected/unresolved lessons, and evidence-linked follow-up proposals.
4. Submit the packet and run the next epoch:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli review --run-dir runs/demo --packet path/to/review.json
.\.venv\Scripts\python.exe -m research_swarm.cli epoch --run-dir runs/demo
```

Packet structure is in [docs/PROTOCOL.md](docs/PROTOCOL.md). The runner exports tasks; **it does not launch hosted agents or make API calls**. The host must enforce `max_agent_tasks`, including its coordinator. With four available slots, a coordinator can use three researchers and rotate colony assignments. Colonies are research directions, not permanently occupied agents.

## 4. Reflect every hour

At the next task boundary after 3,600 seconds, the controller blocks new experiments and enters `AWAITING_REFLECTION`. The host should also check the controller at least hourly while agents are researching, rather than waiting for an epoch command.

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli reflection --run-dir runs/demo
.\.venv\Scripts\python.exe -m research_swarm.cli reflect --run-dir runs/demo --packet path/to/reflection.json
```

Review finishes, resources change, and research can resume immediately. There is no extra hour of idle time. The default allocation reserves 20% for adversarial experiments and 20% for continued exploration; 60% can go to independently reviewed, reproduced winners. Weak colonies need sufficient new failed evidence before retirement. Child colonies share their parent's family allocation and do not increase the total budget or hosted capacity.

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

The confirmation claim is consumed before the controller reads that file, including when scoring fails. Never try another candidate on the same final dataset. The runner checks file identity but cannot establish that a differently serialized file has never been seen by a person or adapter. Protect confirmation data outside the researchers' access until selection is frozen.

No successful candidate is also a valid conclusion. Report what failed, what remains untested, and whether the registered catalog was exhausted. A budget stop is not a proof that no solution exists.

## 6. Adapt it to your research problem

Before registration, define the problem and alternative formulations, main and easier questions, control, data provenance, measurable gates, finite experiment language, colony briefs, budgets, and hourly policy. Replace the synthetic adapter with an approved module under `src`; it must return numerical metrics and an auditable ledger and supply a separate reproduction path.

The controller runs only the registered executable language. Agents may invent broader mechanisms, but a new algorithm, dataset, gate, dependency or source change requires a **separately registered successor**. Copy the lessons forward, keep the failed record, and use new confirmation evidence. Increasing the search language silently would invalidate the original test.

Campaigns hash-bind source and development inputs. Pause/resume preserves absolute deadlines and consumed attempts. These are integrity checks, not an operating-system sandbox: run trusted adapters under appropriate filesystem/network permissions.

## Where to read next

- [Architecture and OpenAI-derived attributes](docs/ARCHITECTURE.md)
- [Colony roles](docs/COLONIES.md)
- [Review and experiment protocol](docs/PROTOCOL.md)
- [Hourly reflection](docs/HOURLY_REFLECTION.md)
- [Kalshi research lineage](docs/KALSHI_LINEAGE.md)
- [Prompt for a new research host](docs/CHATGPT_SETUP_PROMPT.md)

The historical trading packages remain on `swarm_setup_offline`, `swarm_setup_online`, and `swarm_vanilla_v10`. This branch does not include paid/raw market data, credentials, live feeds, or order code.
