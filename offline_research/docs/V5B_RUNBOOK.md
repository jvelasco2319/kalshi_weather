# V5B offline research controls

Run from `C:\Users\darks\Documents\Codex\kalshi\_weather\_llm`.
The pointer `runs/v5b_current_campaign.json` identifies the registered campaign.
Do not overwrite it to reset time, candidate counts or results.

```powershell
.\scripts\control_v5b_campaign.ps1 -Action Status -ProjectRoot .
.\scripts\control_v5b_campaign.ps1 -Action Start -ProjectRoot .
```

Start runs one finite epoch in the foreground. The process normally exits at
`AWAITING_AGENT_REVIEW`; this is a reasoning checkpoint, not a crashed service.
`process_id` in the recovery file is historical and is not evidence of liveness.
The controller does not launch a persistent model service or background swarm.

At each checkpoint, actual research agents inspect the new candidate ledgers,
compare hypotheses and parents, and write a packet containing `epoch`, `reviews`
and `proposals`. Each review includes `colony`, `reviewer`, `candidate_ids` and
`findings`. At least two different colonies must review candidates from other
colonies. Empty proposals are valid when the evidence does not justify a new
hypothesis. Do not invent reviewers or recycle a prior epoch's findings.

```powershell
.\scripts\control_v5b_campaign.ps1 -Action Review -ProjectRoot . -Packet reports\v5b-review-epochN-combined.json
.\scripts\control_v5b_campaign.ps1 -Action Start -ProjectRoot .
```

Review acceptance does not score a strategy. The next Start executes admitted
proposals, the finite predeclared sensitivity schedule, and compatible synthesis.
Parameter fingerprints deduplicate specifications, not equivalent realized
trade sequences. Report observed trade equivalence separately.

Stop writes a cooperative request; the current candidate may finish first.
Resume removes that request and retains the original deadline and ledger.
An interrupted epoch can reconstruct candidates but does not guarantee the same
proposal sequence, because partial candidates can enter the reconstructed
populations. Inspect an interruption before continuing and disclose it. Never
launch a duplicate writer. A kernel lock rejects concurrent epoch writers.

The frozen controller checks its wall deadline only after leaving the review
checkpoint. If a checkpoint outlives the deadline, accept its genuine review
then invoke Start to record the time-budget conclusion; do not reset the clock.
The final epoch also needs an actual scientific review outside the controller's
review API, which only accepts nonterminal checkpoints.

After the terminal development result and its actual final review:

```powershell
.\scripts\control_v5b_campaign.ps1 -Action Freeze -ProjectRoot .
.\.venv\Scripts\python.exe scripts\verify_v5b_artifacts.py --project-root .
```

Freeze chooses exactly one candidate under the registered ranking. It is a
research freeze, not permission to trade or evidence of confirmation. There is
no holdout scoring command in V5B. The V5A holdout remains reserved. A separate
untouched period requires an eligibility audit and immutable confirmation
protocol before outcomes are opened; absent that evidence, report confirmation
as unavailable.

The verifier recomputes ledger arithmetic, candidate identities, lineage,
chronological membership, fold counts, gates, archive and source bindings.
It does not prove historical fills, authenticate fee documents independently,
or supply an operating-system-level access audit. Replay's Python socket guard
is not an OS sandbox. No live feeds, credentials, orders or purchases are used.
