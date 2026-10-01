# Structured research protocol

All examples below are **schemas with placeholders**, not completed reviews. Replace identities and findings with actual work. Keep packets outside Git when they include private data.

## Epoch review

Read `state.json` and `candidates/*.json`. For each `last_candidates` entry, obtain two reviewers with different IDs and different nonproposing colony families. A reviewer cannot use multiple colonies as separate identities. Submit:

```json
{
  "epoch": 1,
  "action": "CONTINUE",
  "synthesis": "Explain the supported insight, unresolved objections, and why the next tests are useful.",
  "lessons": [
    {"status": "UNRESOLVED", "claim": "State the narrow finding and its limits.", "candidate_ids": ["candidate-FULL_HASH"]}
  ],
  "reviews": [
    {"origin": "agent", "reviewer_id": "ACTUAL_REVIEWER_ONE", "colony": "measurement", "candidate_ids": ["candidate-FULL_HASH"], "findings": "Actual artifact inspection and objections."},
    {"origin": "agent", "reviewer_id": "ACTUAL_REVIEWER_TWO", "colony": "falsification", "candidate_ids": ["candidate-FULL_HASH"], "findings": "Actual independent checks and counter-explanations."}
  ],
  "proposals": [
    {
      "id": "successor-one",
      "colony": "mechanisms",
      "question_id": "main",
      "title": "A new executable slope test",
      "mechanism": "Explain why the observed residuals imply a useful correction.",
      "counter_hypothesis": "Explain how bias or group shifts could mimic the improvement.",
      "parameters": {"slope": 2.0, "bias": 0.0},
      "parents": ["candidate-FULL_HASH"],
      "operation": "FORK",
      "transfer_rationale": "If moving between questions, explain how the parent's insight will be tested on this question.",
      "falsification_plan": "Specify which controls and worst-group result could reject this explanation."
    }
  ]
}
```

Lessons may be `SUPPORTED`, `REJECTED` or `UNRESOLVED`. `SUPPORTED` requires reproduced all-gate evidence; partial improvements remain unresolved. It describes development support for the narrow claim, not final verification. `STOP` ends development after review. Do not manufacture reviewer identities to get past a gate.

`CONTINUE` explores/refines; `FORK` has at least one parent; `COMBINE` has at least two distinct parents; `CHALLENGE` tests a counter-explanation. These operations record scientific intent. The actual behavior comes from registered parameters and the approved adapter. Merely changing a title, question label or operation does not create a new executable experiment.

## Hourly reflection

Read the exact request returned by the `reflection` command. Match its checkpoint and `self_sha256`:

```json
{
  "checkpoint": 1,
  "request_sha256": "EXACT_REQUEST_HASH",
  "synthesis": "Explain which directions progressed and why resources should change.",
  "reviews": [
    {"origin": "agent", "reviewer_id": "ACTUAL_REVIEWER_ONE", "colony": "measurement", "target_colony": "mechanisms", "candidate_ids": ["candidate-FULL_HASH"], "findings": "Review all artifact IDs needed for the target decision."},
    {"origin": "agent", "reviewer_id": "ACTUAL_REVIEWER_TWO", "colony": "falsification", "target_colony": "mechanisms", "candidate_ids": ["candidate-FULL_HASH"], "findings": "Independent review of the same target evidence."}
  ],
  "decisions": [{"colony": "mechanisms", "action": "KEEP", "reason": "Explain the evidence."}],
  "new_colonies": [
    {"id": "specialized_mechanism", "parent_colony": "mechanisms", "parent_candidates": ["candidate-FULL_HASH"], "research_question": "A narrower question suggested by the reproduced winner.", "counter_hypothesis": "An alternative explanation to test."}
  ]
}
```

Keep `new_colonies` empty unless a qualifying parent exists. Use `RETIRE` only when the retirement evidence threshold is met. Winner allocation requires two target-specific nonfamily reviews referencing every passing candidate used by the scorecard for that colony. Retirement reviews reference every scored candidate for the colony. Child-creation reviews reference every listed parent. Add separate review entries for each target while preserving real reviewer identities.

## Artifact map

- `registration.json`: frozen contract, source/input hashes and absolute deadline.
- `state.json`: current status, counters, queues, lineage and allocations.
- `attempts` and `candidates`: consumed evaluations, metrics, ledgers and reproduction results.
- `epochs`, `reviews`, `syntheses`: allocation, actual review packets and shared knowledge.
- `tasks`: next research briefs for real agents; these do not certify execution.
- `reflections`: requests and decisions with evidence bindings.
- `strategy-freeze.json`: immutable development selection.
- `confirmation-claim.json` and `confirmation-result.json`: consumed final attempt and conclusion.
- `report.html`: visual ranking and resource allocation.

An integrity failure stops new work. A missing reserved result is not automatically replayed: inspect it and register a transparent repair/successor if needed. Do not edit the campaign record to make counters balance.
