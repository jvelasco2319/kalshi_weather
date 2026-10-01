# Bounded campaign pause, status and recovery

The project has one pilot ticket and one campaign identity. A new `start` request cannot replace an active, paused or failed campaign. A completed start/resume request verifies saved completion hashes and returns the existing result without more model calls. The protected-final ticket remains separate and once-only.

From the project directory, set `PYTHONPATH=src` and use `.venv\Scripts\python.exe -m klax_lab.campaign --root .` with:

| Operation | Arguments | Effect |
|---|---|---|
| Start | `--action start` | Creates the exclusive ticket before the first task. |
| Inspect | `--action status` | Opens SQLite read-only and reports identity, state, original limits, spent/reserved resources and pending pause. |
| Clean stop | `--action pause` or `--action stop` | Writes a request for the coordinator to pause at its next completed task boundary. Does not kill an in-flight process. |
| Continue clean pause | `--action resume` | Reuses the same ticket, data/code/policy hashes, task identities, results and lifetime budgets. |
| Reviewed interrupted recovery | `--action resume --review-interrupted` | Explicitly acknowledges uncertain interrupted attempts and their conservative resource charges. It does not forgive charges or increase retries. |

The operator should inspect status, failure logs and the controller ledger before acknowledging interrupted recovery, and confirm any orphaned bounded child has ended. The coordinator job lock prevents simultaneous coordinators; the native runtime has its own inference lock. A process that died without cleanup leaves an active ticket and running attempt, which cannot continue without the explicit review flag. No automatic new campaign or automatic retry is launched.

## Durable continuation and accounting

SQLite commits task reservations and result/evidence records transactionally. Checksummed coordinator checkpoints identify the orchestration version and original input bindings. Resume checks registry integrity, task/attempt consistency, accepted result records, evidence hashes and saved experiment artifact manifests. The fixed task plan is replayed from its beginning: successful tasks reuse verified artifacts; unfinished eligible tasks alone may dispatch. This rebuilds the task counter, evidence board, tested recipes and candidate register without asking the model to regenerate accepted proposals.

The original campaign creation time is preserved. **Paused time counts toward the original wall-time limit**; resume cannot replenish its six-hour lifetime. Successful tasks retain their exact charged usage. Unknown interrupted work is charged its entire original token/compute/experiment reservation before a reviewed retry. Every accepted model attempt, including an unknown failed attempt, counts against the registered model-call limit. A retry uses the same task and hypothesis IDs, consumes the original retry allowance and reserves additional remaining resources. Thus completed work is never rerun, while genuinely uncertain work may be repeated only after explicit review and with its possible earlier cost retained.

Known nontransient failures, exhausted retries, insufficient remaining budget, changed code/data/policy, missing registries, corrupted checkpoints or altered result artifacts fail closed. No migration or source edit can silently authorize resuming a campaign against a different evaluator. The controller's older destructive `stop()` API still closes/cancels a registry; the user-facing clean-stop command uses the separate resumable pause path.

Completion first saves its summary and binds its hash plus the candidate register hash in a durable checkpoint, then commits the terminal controller event. A crash before the outer ticket update can be recovered explicitly by verifying those committed outputs and exporting the ledger. It cannot start another search. Reports remain reproducible from saved records.

## Evidence-backed later rounds

The first round's explorers receive baseline context independently. The second round assigns a simple/conflicting challenger and combined follow-ups using prior experiment evidence. Later follow-ups register explicit parent hypothesis IDs and a synthesis decision retaining positive and negative parent evidence. The third round emphasizes adversarial confirmation before the unchanged fixed champion ranking. Synthesis prompts differ by round. Every candidate still receives a separate critic and numerical verification.

The recipe allowlist, numerical gates, three-epoch limit, maximum hypotheses/experiments, model-call cap, token cap and zero paid budget are unchanged. If explorers abstain, no artificial hypothesis is inserted to claim a combination occurred. Parent linkage documents an evidence-informed follow-up; it does not certify that two components are causally additive.

## Verification scope

`tests/test_campaign_recovery.py` covers a clean pause followed by same-identity continuation, a real subprocess terminated with `os._exit(91)` after a fake worker accepted a task, mandatory recovery review, full unknown charges, unchanged budgets, reuse of completed numerical work, recovery between controller completion and ticket commit, and rejection of altered artifacts/checkpoints/policies. `tests/test_controller.py` checks the original wall deadline and reviewed retry ceiling. `tests/test_campaign_integration.py` additionally pauses/resumes the actual numerical CLI flow over a completely invented 2024/2025 dataset. `tests/test_campaign.py` verifies combined parent IDs and differentiated round prompts.

These tests use fake text workers and synthetic data. They are engineering evidence only, not an actual research campaign, historical scores, profits or an OS isolation claim. The local inference backend and its separately verified tool-free capability probe are unchanged by these coordinator additions.
