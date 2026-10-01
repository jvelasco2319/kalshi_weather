# KLAX V4.1 nomination-failure continuation amendment

Status: **prospective, non-authorizing amendment**  
Scope: offline historical KLAX development only  
Source campaign: `v4-offline-20260926T212122609Z`

## Purpose

V4 stopped during epoch 7 because the pinned local model returned the same
schema-invalid nonproposal for all three charged nomination attempts.  The
host-selected plan, data, evaluator, promotion gates, and protected-final
boundary did not fail.  This amendment permits a fresh, hash-bound successor
campaign to preserve the source evidence and continue the registered search.
It does not authorize a launch, read protected-final data, create an order,
change a research plan, relax a gate, or claim a return.

The amendment is triggered only by model-response protocol failure.  No
candidate return, label, gate result, or ranking may select or parameterize the
repair.

## Bound incident evidence

The source checkpoint and the raw failing attempts are immutable evidence:

| Evidence | SHA-256 or value |
|---|---|
| Source recovery file | `d631a281a9004c1ded5fb3ac7b504a16224307492ea276d0dcc516efd41f89d0` |
| Source recovery state | `b4e1576fbb9ba5822182f877239e8657636c3f49e07f8e4b8005185ee2fa987b` |
| Source readiness | `d8c2410517c4931db413e1a7b2fee80f284fb0b678101fd16e1c247b031fa5ad` |
| Source ticket | `df259ed5d11ca6846b9f9a6dd765f0594f734c879ec4dd619268931f0f4d1cb5` |
| Failed task | `v4-e007-s11` |
| Allocated plan | `568bf0bb0164850f246724f70f4df899848435dff2ebfa782ce114df5733b744` |
| Evidence digest | `05de51d84834a444d8c16fe7d5cddda308e0e1499a6414a38acc29168d4ba0a3` |
| Attempt packet, all attempts | `cf9c667dd71c0393ed54570305153ee429afb0b1ec6ff1a6f0db7248499ce737` |
| Attempt stdout, all attempts | `d3ecbc80e9887626fb366e25c6f94daae7173e0921ae6f80d1e129e08e44a9b5` |
| Attempt 1 process record | `02efaea5ce4885aa8a3552c21cf42f14e745cd75a791920c085d83d566d721a4` |
| Attempt 2 process record | `cd0f7e3eac26991315f5e0f125cbc69383131247a18f4b40438edad7c9001eb4` |
| Attempt 3 process record | `10378c14c0df8261f7e2b3835ca978025e7a8f4fe7b5ad3398e6728be81ddf6b` |

Each process record reports exit code zero with no timeout, output-limit,
reader, or cleanup failure.  Each stdout decodes to an `abstain` response with
`seed_index: 0`.  V4 permits `abstain` only with `seed_index: null`, so the
fixed parser independently reproduces `Only V4 propose may select a seed`.

The source checkpoint is internally hash-valid.  It contains six completed
epochs and ten reviewed candidates in epoch 7; 82 candidates were admitted,
executed, replicated, criticized, and independently verified.  Its 86 charged
model calls comprise 83 candidate calls, one synthesis call, and two retry
calls, reserving 1,409,024 context tokens.  Calls 84 and 85 are checkpointed as
failed.  Call 86 is checkpointed as reserved because the final parser error
escaped before the failure status was saved, although its immutable process and
stdout artifacts prove that the call completed with the same invalid output.

## Exact fallback rule

After the initial nomination and the registered two retries have all produced
model-response schema failures, the host shall:

1. finish and charge every attempted call exactly once;
2. record `MODEL_NOMINATION_FAILED` with the task, worker, digest, plan,
   attempt call IDs, packet hashes, raw-output hashes, parser-error class, and
   parser-error message;
3. discard every field of the malformed responses for scientific purposes;
4. admit only the exact whole plan already assigned to that allocation slot;
5. record `ADMIT_EXACT_PREALLOCATED_PLAN_WITHOUT_MODEL_NOMINATION` and
   `fallback_model_calls: 0`; and
6. proceed through the unchanged evaluator, replication, critic, independent
   verifier, and promotion gates.

This rule governs the bound historical incident and every later V4.1 candidate
nomination.  Another parser-invalid candidate response may not stop the
successor after its retry budget is exhausted.  Runtime, process, filesystem,
binding, or synthesis failures remain hard stops and may not enter this
fallback path.

The fallback identity must simultaneously equal the queue plan identity, the
allocation-history `plan_sha256`, the follow-up packet plan identity, and one
member of the frozen 3,072-plan universe.  The host may not choose an adjacent,
better-ranked, forced-coverage, replacement, or newly constructed plan.

The fallback applies only after an exact packet reached the pinned runtime and
the resulting raw response failed the response parser.  Changed worker
identity, packet/digest/campaign binding, runtime hash, data, code, source
artifact, network boundary, protected-final boundary, or filesystem integrity
remains a hard integrity stop.  Exhausted infrastructure failures are not
silently converted to nominations.  Synthesis retains no deterministic
fallback.

A valid `propose` remains a model nomination.  Valid `reject` and `abstain`
responses retain the already registered same-plan fallback.  A malformed
attempt followed by a valid proposal charges both calls and uses the valid
proposal without recording terminal `MODEL_NOMINATION_FAILED`.

V4.1 may apply one compatibility normalization before parsing: when a candidate
packet contains exactly one seed and a response otherwise binds the exact
protocol and task, `reject` or `abstain` with `seed_index: 0` may be normalized
to the same nonproposal with `seed_index: null`.  The raw and normalized bytes
and hashes must both be retained.  This rule may not normalize `propose`, a
multi-seed packet, synthesis, an unknown action, a wrong task or protocol, or
any other field.  A normalized response remains a nonproposal; it never becomes
a nomination or scientific evidence and does not avoid the registered bounded
attempt accounting.

## Crash-consistent state transition

Every call reservation, completion, failure, terminal nomination failure,
fallback disposition, admission, and task artifact must be durably checkpointed
as a monotone transition.  Recovery may perform a transition once or observe
that it was already performed; it may never repeat a completed inference,
refund a call or token reservation, admit a candidate twice, or consume two
slots.

Terminalization is also one crash-consistent transition.  A base-V4 summary,
verification, or artifact manifest may not appear at the successor campaign's
canonical terminal paths while V4.1 verification is pending.  The host must
stage terminal files outside those paths, run the V4.1 scheduler verifier, and
publish the mutually bound V4.1 summary, verification, scheduler state, and
manifest only after verification returns.  If that verifier errors, the host
must leave no terminal summary or publish an explicit V4.1
`INSUFFICIENT_EVIDENCE` result with a `FAIL` verification.  Status and resume
logic must never treat a lone base-V4 summary as a completed V4.1 campaign.

For the bound incident only, a verifier may reconcile call 86 from `RESERVED`
to `FAILED` after independently checking the exact attempt-3 packet, process,
stdout, parser result, and source checkpoint hashes above.  This reconciliation
is a successor migration event.  The source checkpoint and source artifacts
remain byte-for-byte unchanged.

## Successor identity and immutable import

Changed code and fallback semantics invalidate the claimed source ticket.  The
source campaign may not resume in place.  Continuation requires:

- a new campaign ID distinct from the source;
- a new readiness artifact and one-use ticket binding this amendment, the
  continuation implementation, tests, frozen inputs, gates, budgets, and
  negative-capability checks; and
- a content-addressed continuation manifest that is rebuilt from the source
  files and compared for exact equality before dispatch.

The continuation manifest must bind the source readiness, ticket, claim,
recovery file, recovery state, complete source artifact inventory, raw failure
attempts, and controller logs.  It must preserve all 82 candidate identities,
plans, evaluation artifacts, replications, critics, independent verifications,
promotion decisions, seven digests, the epoch-4 synthesis artifact, allocation
history, queue, novelty index, call journal, counters, and source timestamps.
Imported candidate evidence remains legitimate adaptive evidence and must be
reverified rather than trusted from caller flags.

The only permitted state changes at import are successor campaign/readiness/
ticket bindings, the explicit call-86 reconciliation, the corresponding
`MODEL_NOMINATION_FAILED` and exact-plan fallback records, and the minimal queue
transition needed to evaluate that same plan.  The successor report must name
the source campaign and amendment and present one combined adaptive lineage; it
may not describe the successor as an independent or fresh replication.

## Cumulative resource accounting

Continuation must not reset any resource counter.  Before the zero-call
fallback, successor state starts with:

- current epoch: 7;
- admitted and executed candidates: 82 each;
- candidate calls: 83;
- synthesis calls: 1;
- retry calls: 2;
- total calls: 86; and
- reserved context tokens: 1,409,024.

The source campaign started at `2026-09-26T21:23:25.261434+00:00`.  Its absolute
12-hour deadline is `2026-09-27T09:23:25.261434+00:00`.  The successor must use
that deadline.  Saving elapsed active-process seconds and reconstructing
`started_at = now - saved_elapsed` refunds downtime and is forbidden.  If the
absolute deadline has passed before dispatch, the successor may verify and
finalize the imported evidence but may not execute another worker or candidate.

The fallback itself consumes no inference call or context reservation.  After
its exact plan is admitted and evaluated, the counters become 83 admitted and
executed candidates while model calls and reserved tokens remain 86.  The next
actual model invocation is call 87.

The persisted and terminal scheduler state must carry the actual
`model_context_tokens_reserved` counter.  Recovery must reject a value below
1,409,024 and must enforce the registered 16,384-token reservation per charged
call, so a rehashed checkpoint cannot refund tokens while retaining the call
journal.  The terminal verifier must compare this actual counter with the call
journal and registered total; a self-declared continuation floor is not
sufficient evidence.

## OpenAI-style iterative semantics

The V4 scientific loop lives in registered diversity, deterministic evaluation,
independent criticism, evidence digests, cross-pollination synthesis, and
evidence-based allocation.  A nomination-format failure is operational evidence
about the worker interface.  It is not evidence that a research branch is
falsified, supported, profitable, or unprofitable.

The exact-plan fallback follows the easier-subproblem and resource-reallocation
pattern: isolate a failed interface step, consume no extra inference after its
bounded retries, execute the already registered experiment, and return its
verified result to the common evidence pool.  The malformed response must not
enter candidate ranking, Pareto fronts, falsified branches, promotion evidence,
or synthesis content.  The operational failure count may appear separately in
campaign health reporting.

All six colonies, epoch quotas, forced-coverage schedule, follow-up digest
lineage, four-epoch synthesis cadence, and independent/formal verification
remain unchanged.

## Required fail-closed verification

Readiness must execute tests proving:

- the exact three observed raw outputs reproduce the registered parser error;
- three malformed attempts charge one candidate call and two retries, then
  admit the exact allocated plan with zero additional calls;
- malformed-then-valid, valid reject/abstain, infrastructure failure, identity
  mismatch, synthesis failure, and exhausted-budget paths obey the boundaries
  above;
- crashes at each reservation, process, parse, failure, fallback, admission,
  evaluation, checkpoint, and terminal-publication boundary recover
  idempotently;
- scheduler verification rejects a missing or duplicate attempt, early
  fallback, extra inference, wrong plan/digest, incomplete call timestamp,
  nonmonotone or concurrent call, refunded counter/token, or extended deadline;
- an injected V4.1-verifier crash cannot leave a canonical base-V4 summary or
  make resume/status report the successor as complete;
- the continuation manifest changes when any source byte changes and the source
  tree is unchanged after import;
- the successor ticket cannot target the source campaign ID, the old ticket
  cannot authorize new code, and a claimed successor ticket cannot be reused;
- every imported candidate and scheduler artifact is independently reverified;
  and
- protected-final access, network access, paper/live orders, and actual-profit
  claims remain denied.

Passing this amendment does not make the current aggregated candlestick quotes
promotion-eligible fill evidence.  The 82 imported candidates currently have
zero promotion-eligible observed-fill verifications.  Positive assumed/proxy
returns remain diagnostic and cannot satisfy the 10% objective.
