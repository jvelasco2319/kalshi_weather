# V3 protected-final evaluator

`klax_lab.final_evaluator_v3` is the isolated, one-use evaluator for the
registered July 1 through December 31, 2025 KLAX interval. It is implemented
but must not be invoked unless a real V3 campaign has produced a development
champion and `protected-final-authorization.json`.

## Release boundary

`validate_v3_final_release(...)` reads only registration, readiness, campaign,
candidate, replication, critic, and promotion artifacts. It re-evaluates the
registered promotion policy and requires all gates to pass. It also requires:

- the completed campaign's exact one-use campaign-ticket claim;
- a real or explicitly test-only readiness authorization;
- the deterministic champion stop reason;
- exact independent numerical replication;
- a separate critic nonrejection;
- matching candidate, plan, novelty, model, artifact, readiness, and ticket
  hashes; and
- an authorization that forbids model or policy changes and final-data
  feedback to discovery.

This validation rejects paths containing a protected-final or holdout segment.
The protected bundle path is not opened during release validation.

After validation, the evaluator atomically creates
`runs/protected_final_v3/<campaign-id>/ticket-state.json` with zero evaluations
remaining. This happens before the first protected read. An existing `CLAIMED`,
`FAILED`, or `COMPLETE` ticket refuses another attempt. A crash that leaves the
state ambiguous also refuses reuse. A failure after the claim is terminal.

## Frozen champion state

The development evaluator now stores a canonical, label-free fitted model
state inside each candidate's `compiled_manifest.json`. It contains learned
parameters and fit metadata but no dated training rows, calibration rows,
development labels, or outcomes. The manifest also stores the frozen 2024
reference distribution as integer-temperature counts without dated labels.

The fitted state is part of `compiled_manifest_sha256`, reproduced byte for
byte by the independent development evaluator, and included in the final
authorization's candidate artifact hashes. The final evaluator reconstructs
that exact state with `fitted_candidate_model_from_state(...)`. It never calls
a fit, calibration, selection, or threshold-tuning routine.

## Protected input contract

`load_protected_final_bundle_v3(...)` accepts one manifest below an authorized
protected-final root. Its identity is the canonical hash of these fields:

- schema `klax-v3-protected-final-bundle-v1`;
- exact July 1 through December 31, 2025 scope and `protected_final` partition;
- the champion's single frozen decision time;
- offline, historical-only, and as-of validation flags;
- hash-, byte-, and row-count-bound JSONL feature and label artifacts;
- a hash-, byte-, and row-count-bound exclusions artifact; and
- the exact set of recursively referenced source-object hashes.

Eligible labels plus explicit exclusions must cover all 184 dates. Every
feature has one unique event, exhaustive ordered contract brackets, completed
one-minute quotes no later than the decision, and observations and forecasts
available no later than the decision. Features contain no settlement value.
Labels are physically separate.

After the ticket is consumed, the evaluator invokes
`settlement_dataset_v3.build_protected_final_targets(...)`. The protected
bundle's eligible dates, exact KLAX integer highs, event identities, source
hashes, binary winners, excluded dates, and exclusion reasons must equal that
independently rebuilt settlement evidence.

## Outputs and verification

The evaluator writes canonical JSON predictions, the full decision and
assumed-fill ledger, a JSON summary, a Markdown report, an artifact manifest,
and independent verification evidence. It reports probability scores,
assumed-fill economics, fee and price stress, and day-bootstrap uncertainty as
separate sections. Every report states that no model refit, model selection,
threshold tuning, network access, orders, or account-gain measurement occurred.

`verify_protected_final_evaluation_v3(...)` independently verifies the saved
hash inventory, identities, probability conservation, at-most-one purchase per
event, assumed-fill arithmetic, and the one-use/no-refit scope. Any saved
artifact change causes failure.

The production CLI is:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m klax_lab.final_evaluator_v3 `
  --root C:\Users\darks\Documents\Codex\kalshi\_weather\_llm `
  --campaign-directory runs\campaigns_v3\<campaign-id> `
  --readiness data\manifests\v3_readiness.json `
  --ticket runs\v3_offline_campaign_ticket.json `
  --protected-bundle data\protected_final\frozen-v3\manifest.json
```

The command is intentionally documented for the future release point. It has
not been run against the real protected interval.
