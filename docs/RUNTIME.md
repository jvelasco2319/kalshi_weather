# Durable task runtime and resources

`runtime.sqlite3` stores immutable briefs and record versions, task attempts, leases, usage reservations and audit events. Commands open fresh database connections, so recovery does not depend on process memory. Inspect `tasks`, `resources` and campaign `status` before resuming work.

## Dispatch a task

Generated colony briefs show a durable task ID. Use the returned values, not invented examples:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli tasks --run-dir runs/demo
.\.venv\Scripts\python.exe -m research_swarm.cli claim-task --run-dir runs/demo --task-id ACTUAL_TASK_ID --owner ACTUAL_AGENT_OR_PERSON_ID
```

The response contains `attempt_id`, `fence`, `expires_at`, `reservation_id`, and a separate `workspace`. The host checks the permissions and actual tools, dispatches the worker with that brief and context, and records its start:

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli start-task --run-dir runs/demo --task-id ACTUAL_TASK_ID --attempt-id ACTUAL_ATTEMPT_ID --fence ACTUAL_INTEGER --owner ACTUAL_OWNER
```

The runtime does not call an AI provider. The real host must bind model/version, provider handle, prompts, tools, cost reporting and cancellation to the actual attempt. Include those provenance fields in its result packet. The example has no configured models because it only runs deterministic local software checks.

## Result and acknowledgment

A worker packet includes `task_id`, `attempt_id`, `fence`, `owner`, `finding`, exact `evidence` locators, `assumptions`, `objections`, `artifacts`, `checks_actually_run`, `remaining_gaps`, `next_action`, `execution`, and `research_outcome`. Artifact entries have `path`, `sha256` and `type`. Paths are relative to the attempt workspace; the controller validates and snapshots their bytes.

```powershell
.\.venv\Scripts\python.exe -m research_swarm.cli submit-task --run-dir runs/demo --packet path/to/actual-result.json
.\.venv\Scripts\python.exe -m research_swarm.cli ack-task --run-dir runs/demo --result-id ACTUAL_RESULT_ID
```

An identical retransmission returns the existing result. Altering the packet under the same attempt ID is rejected. A stale fencing token can store auditable output but cannot complete the current owner's task. Delivery completes only after durable acknowledgment. An execution error or null experiment can still deliver a useful task result; scientific acceptance remains pending.

## Retries, cancellation and recovery

The example allows at most two attempts. Only transient failures or expired ownership can retry unchanged work. A semantic failure needs a new plan and task. Retries receive new attempt/fencing IDs and new reservations; old usage is retained.

`release-task` takes `--error-kind transient|semantic` and `--reason`. `cancel-task` requests local cancellation with a reason. Neither command automatically cancels a provider call. Cancelled, released or expired attempts with unknown provider completion continue occupying capacity. A returned result or `confirm-stop --attempt-id ... --packet actual-stop-receipt.json` releases that capacity. A stop receipt states the real provider handle, terminal outcome and stop time; validation of that external receipt belongs to the host. It does not refund usage.

After interruption, reconcile actual provider/process state with the saved attempts and files. Do not infer successful completion from absent stderr or launch a duplicate while an attempt may still be live. Keep unacknowledged and stale output in the audit record.

## Reservations

The default local allocation is 700 research, 250 verification and 50 reporting credits. A deterministic invocation consumes one declared local operation credit, including a failed invocation. Model/API currency and tokens are separate and not automatically metered here.

Admission reserves a finite cost bound in its own stage. Unknown actual usage retains the reservation. `reconcile-usage --reservation-id ... --actual-units ...` records actual known usage; `--conservative` charges the full reserved amount. Cancellation is not evidence of zero cost. If actual usage exceeds an allocation, further admissions stop. Transfers require a reason and cannot move committed or unknown funds; the global total remains unchanged.

Check `resources` before dispatch. Configure an enforceable provider per-call bound before calling a financial cap hard. With host-managed calls, use best-effort reporting, conservative bounds and early stopping; this package cannot guarantee the provider bill.

Research admissions stop before the shutdown and report reserve. Checking can use its reserved stage while research is paused. Finishing delivery requires that owned/submitted/outstanding attempts have been resolved or cancelled with actual completion accounted for.

## Permissions

Allowlist validation constrains requested task briefs and submitted paths. It does not prevent a worker with unrestricted shell access from reading other folders or making other calls. Use restricted host tool handlers, separate accounts/containers, read-only source/input mounts and a protected verification environment as appropriate. The default `unconfigured` setting accurately reports that this boundary has not been installed and blocks accepted claims.
