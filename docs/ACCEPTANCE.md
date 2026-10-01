# Claim acceptance and integrity recovery

Rankings allocate attention. Acceptance records state what was established under a specific contract and artifact version. The controller keeps these separate.

## Separate states

Candidates expose execution, research outcome, review, verification, acceptance and applicability. Numerical reproduction can pass while the research outcome is unknown and acceptance is pending. Review readiness can request configured checks without granting acceptance. Fresh review context can reduce anchoring; it does not ensure uncorrelated model errors.

The example's `permission_boundary` is `unconfigured`. An `accepted` decision fails closed until the host actually protects the authority, checker and original data and declares `host_enforced` before registration. That declaration is a host attestation, not an isolation installer. The CLI is the trusted controller interface; do not expose privileged decision/check commands to research workers.

## Artifact-based claims

Numerical campaigns create candidate/run/check records automatically. Other research modes use `claim --packet ...` after an actual task submission has been acknowledged. A claim packet contains:

- `id`, registered `question_id`, `colony`, exact `claim`, `assumptions` and `objections`.
- `parents`: candidate IDs and exact versions.
- `evidence`: versioned `{kind, id, version}` original references.
- `proposed_checks` and `producer_result_id` for the eligible acknowledged task result.

Use `source --packet ...` to register an original source's ID, URL/file, author, version, retrieval date, exact locator and access limits. Proposed worker checks stay observations and do not replace trusted check records.

`review-claim` takes candidate ID/version, a rubric and at least two real nonfamily reviewer assignments with findings and recommendations (`ready`, `revisions_required`, `rejected`). A review creates a new candidate version and preserves its original. Use `claims` to retrieve current versions before checking.

## Configured checks and decisions

The separately controlled verifier executes the domain checks. `check-record --packet ...` ingests the resulting report; it does not pretend that ingesting a report ran the check. Required fields are `id`, `candidate_id`, `candidate_version`, `artifact_sha256`, `contract_version`, `name`, `outcome` (`passed`, `failed`, `inconclusive`), `scope`, `checker_version`, `diagnostics`, configured `issuer`, and `objections_resolved`. Preserve commands, environment/data versions and raw diagnostic references in the report. Issuer authentication is part of the external host boundary.

The acceptance authority uses `decision --packet ... --authorize-decision`. Its packet contains ID, current candidate ID/version, configured authority, accepted/rejected status, exact accepted scope, rationale, check IDs and supported/refuted/inconclusive research outcome. Acceptance requires review readiness and all contract-required, active, version-matched passing checks with resolved objections. Each required check must cite that exact accepted scope; a narrow check cannot silently support a broader decision.

A valid negative answer may be accepted when the question-oriented contract permits it. A reproduced intermediate result can have a narrower accepted scope. Neither implies that the main objective has been satisfied. Numerical metric-gate failures remain visible and are not rewritten to make the result positive.

## Disputes and repair

`quarantine --packet ...` records the affected kind/ID/version, reason and configured authority. It traverses dependents and marks them quarantined, retaining historical decisions. Quarantined evidence is excluded from winner allocation and cannot support new proposals or a freeze.

Verifier changes require a separate maintenance/successor registration, reviewed tests and explicit authority. Do not edit the active checker or registry. `revalidate --packet ... --authorize-decision` requires new active passing checks from the configured verification authority for that exact record version. It restores only the target; affected dependents remain quarantined until separately checked.

## Delivery

`finish --packet ...` records investigation delivery as `objective_satisfied_within_scope`, `partial_progress`, `inconclusive`, or `operationally_blocked`. Include rationale, decision IDs, unresolved questions, excluded run IDs/reasons and the next action. Objective satisfaction requires an active accepted main-question decision at the registered acceptance scope. A budget stop, positive score or finished task cannot satisfy that condition by itself.

The completion record is immutable and prevents new research admissions. Use a separately registered successor for later work. Report original evidence, failed attempts, exclusions, actual/unknown costs, runtime limitations and the strongest checked result; retain individual claim outcomes separately from delivery.
