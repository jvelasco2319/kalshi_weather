# Local historical research commands

Run from `C:\Users\darks\Documents\Codex\kalshi\_weather\_llm` using the local
environment. All historical dates and economic settings are already registered
in `configs/evaluation.json` and `configs/pilot.json`.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m klax_lab.cli status
.\.venv\Scripts\python.exe -m klax_lab.cli v3-fixtures
.\.venv\Scripts\python.exe -m klax_lab.cli v3-status
.\.venv\Scripts\python.exe -m klax_lab.engineering --root .
```

`status` inspects saved state. `engineering` runs the complete synthetic test
suite plus a controller fixture, recording the environment's interpreter, exact
source/test/policy/dependency inventory, JUnit results and transcript. A passing
engineering run is required for readiness; it is not a historical result.
`v3-fixtures` regenerates the hash-bound numerical and market-policy engineering
fixtures. `v3-status` validates the V3 registration and republishes its
fail-closed component inventory. Neither command launches a campaign or reads
the protected final interval.

## V3 substantive readiness

The V3 capability probes validate the independent numerical-replication
contract, critic contract, and strict tool-free worker packet protocol. They
contain no market-performance evidence and cannot authorize the protected
final interval.

```powershell
.\.venv\Scripts\python.exe -m klax_lab.cli probe-worker --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 publish-worker-probe --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 probes --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 finalize-source --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 validate --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 issue --root . --campaign-id <new-v3-campaign-id>
```

`probe-worker` performs the actual local GPT-OSS inference against the bounded
V3 proposal protocol, in addition to its earlier capability and V2 checks. The
`publish-worker-probe` command verifies the pinned runtime, backend source, saved
V3 report, empty tool catalog, and offline/protected-final boundaries, then
publishes their hashes as `v3_worker_probe.json`. It consumes the existing
successful report and does not run inference again. The deterministic worker
parser fixture is limited to isolated tests and cannot satisfy real readiness.

`finalize-source` succeeds only after the finite acquisition reports exactly
543 complete days, zero unavailable days, the 35 GB cap, no protected-final
read, and the cache-only normalizer publishes complete hash-verified HRRR,
local-observation, and GEFS components. It preserves the original source plan
and pilot evidence while adding exact request, byte, coverage, and component
hashes.

After source finalization, freeze the real training and development inputs:

```powershell
.\.venv\Scripts\python.exe -m klax_lab.freeze_pipeline_v3 --root .
```

The command reads only finalized, manifest-bound local artifacts. It requires
exact 543-day HRRR/GEFS coverage, keeps KLAX mandatory, preserves available
KHHR/KLGB/KSMO/KTOA observations as optional context, freezes the registered
2024 training, January calibration, and February-June scored partitions, and
publishes `v3_dataset.json` plus `v3_five_fold_split.json`. It issues no
readiness or campaign ticket by itself. The weather handoff script invokes this
command after successful normalization and source finalization, refreshes the
deterministic fixtures, binds the saved actual V3 worker probe without another
inference, validates all twelve components, issues the one-use development
ticket, and starts or resumes the same bounded offline campaign. It never starts
the protected-final evaluator.

Once `issue` has created a substantive one-use V3 readiness and campaign
ticket, control the bounded campaign with:

```powershell
.\.venv\Scripts\python.exe -m klax_lab.orchestrator_v3 status --root .
.\.venv\Scripts\python.exe -m klax_lab.orchestrator_v3 start --root .
.\.venv\Scripts\python.exe -m klax_lab.orchestrator_v3 resume --root .
```

`start` refuses to run without the substantive readiness artifact, unused
campaign ticket, actual readiness-bound local GPT-OSS probe, and frozen V3 data
bundle. `resume` continues only the same campaign and conservatively retains
already reserved local-model calls. The campaign writes packets, responses,
candidate evidence, an independent numerical recomputation, critic decisions,
allocation decisions, recovery snapshots, and its final report under
`runs/campaigns_v3/<campaign-id>/`. It never reads the protected final interval;
it may only emit the one-use final authorization after one candidate passes
every registered development gate.

`validate` recomputes every frozen-dataset and five-fold identity, verifies all
twelve component schemas and file hashes, reruns the component fixtures and
capability probes, and checks offline and protected-final boundaries. `issue`
does the same work before writing a new readiness artifact and one-use offline
campaign ticket. It refuses an existing readiness or ticket path. None of
these commands runs a campaign.

## Historical acquisition and normalization

The finite 2024-01-01 through 2025-12-31 weather job is complete with one
explicit source gap. See `docs/COLLECTOR_AUDIT.md` and saved acquisition manifests
for its command, two-request-per-second ceiling and byte cap. The cached raw
inputs and manifests remain resumable and hash checked.

After the original archive job finishes:

```powershell
.\.venv\Scripts\python.exe -m klax_lab.repair_weather --root .
.\.venv\Scripts\python.exe -m klax_lab.cli prepare --root .
```

`repair_weather` is the finite acquisition lane: bounded retries of explicitly
missing historical fields, followed by a zero-transfer cache integrity pass.
`prepare` builds local normalized tables, source audits, exclusions and coverage.
It does not download missing data. Partial coverage remains visible.

## Baselines and readiness

```powershell
.\.venv\Scripts\python.exe -m klax_lab.cli probe-worker --root .
.\.venv\Scripts\python.exe -m klax_lab.cli baseline --root .
.\.venv\Scripts\python.exe -m klax_lab.cli replicate --root . --run-directory <baseline-run-folder>
.\.venv\Scripts\python.exe -m klax_lab.cli readiness --root . --run-directory <baseline-run-folder>
```

`probe-worker` performs one capability-boundary inference plus one V2 and one
V3 typed-plan inference, then writes a hash-bound pointer for readiness. Replace the
angle-bracket placeholder with the actual path returned by baseline.
These are intentional placeholders, not literal executable path values.
Baseline and replication execute in fresh guarded processes. Readiness binds
the historical verification, current engineering evidence, collector audit,
local-model capability probe and exact data/code versions. The actual successful
label is `READY_FOR_OFFLINE_CAMPAIGN`.

## Finite end-to-end batch

```powershell
.\.venv\Scripts\python.exe -m klax_lab.pipeline --root . --timeout-hours 7
```

The batch waits for the existing historical archive job, then repairs/verifies
the cache, prepares local data, builds and independently verifies baselines,
checks readiness, starts the bounded campaign, conditionally scores one frozen
champion, and writes the report. It has a seven-hour deadline and a project lock.
It stops on failed gates and code/policy changes. Do not edit research source or
policies while a batch is running. Do not start another instance to recover an
unavailable observation handle: inspect the actual process first.

If a campaign ticket already exists, the batch does not rebuild data, baselines
or readiness. A paused campaign remains paused until explicit resume; an
interrupted campaign requires review. Once that same campaign completes, rerun
the batch to verify its saved ticket and continue final evaluation/reporting.
The original readiness bytes and their ticket binding remain unchanged.

The first waiting batch was deliberately stopped before any historical scoring
or ticket consumption to close the goal audit's recovery, saved-prediction and
readiness-evidence gaps. Its state is retained under
`runs/historical-pipeline-20260925T031752726750Z/`.

## Campaign lifecycle and saved reports

The campaign entry point is `python -m klax_lab.campaign`. With the current
`architecture_version: 2` pilot it dispatches the iterative V2 controller and
uses `offline_campaign_v2_ticket.json`; the V1 ticket remains historical. Its `--help` describes
status, pause and explicit same-campaign resume actions; the runtime/controller
document records exact recovery semantics. A resume keeps
the original campaign identity, data, code, resource charges and trial budget.
Unknown interrupted attempts require explicit review and cannot refund their
reservation. The final evaluation has a separate once-only ticket.

```powershell
.\.venv\Scripts\python.exe -m klax_lab.campaign --help
.\.venv\Scripts\python.exe -m klax_lab.campaign --root . --action status
.\.venv\Scripts\python.exe -m klax_lab.campaign --root . --action pause
.\.venv\Scripts\python.exe -m klax_lab.campaign --root . --action resume
.\.venv\Scripts\python.exe -m klax_lab.final_evaluation --root . --campaign-summary <campaign-folder>\summary.json
.\.venv\Scripts\python.exe -m klax_lab.reporting --root . --campaign-directory <campaign-folder> --final-report <saved-final-summary.json>
```

Do not request a final run if no candidate qualified. In that case, the reporting
command omits `--final-report`; the final interval remains unused. Rerendering a
saved report uses verified artifacts without invoking a model or reevaluating
the final interval. The report contains hypothetical historical settlement
profits under its assumptions, not actual account gains.

`--action stop` is a synonym for a clean task-boundary pause. Resume of an
interrupted ACTIVE or FAILED record requires the additional
`--review-interrupted` flag after the failure evidence is inspected. This
acknowledges conservative charging of uncertain attempts, not permission to
change the research trial budget. See `docs/RESTORATION.md` for backup and
restoration rules.

No entry point places an order, starts a live feed, creates a schedule or deploys
a service. Any future online work needs a new user instruction after review.
