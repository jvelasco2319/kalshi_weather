# Local restoration and registry changes

This project has no remote repository or online backup. Restoring it on another
computer requires copying the local files; Git alone does not contain the bulk
datasets, model weights, native runtime, or complete experiment results. Keep
the private collector snapshot private. No credentials belong in a backup of
research artifacts or source control.

## Preserve a restorable copy

Pause the campaign at a task boundary and wait for a recorded `PAUSED` state
before taking a campaign backup. A file named lock is not proof that a process
has stopped: confirm the process state as well. Avoid copying a SQLite database
while a writer is active. When a writer must remain active, use SQLite's backup
API to create a consistent snapshot and retain its timestamp, file hash, campaign
identity and ticket binding; do not copy only the main database while ignoring
its active write-ahead log.

For a stopped campaign, copy its complete `runs/campaigns/<campaign-id>/`
directory and the project-level campaign/final tickets together, including the
database, checkpoint, saved packets, responses, evidence, model parameters,
predictions, decisions, ledgers and artifact manifests. Do not reset or remove a
ticket to make a resumed run appear new. Unknown interrupted worker attempts
keep their charged reservations and require the explicit recovery review.

Also retain:

- `src/`, `tests/`, `configs/`, project metadata, documentation and dependency
  lock, with the exact Git revision or source inventory used by the campaign.
- `data/raw/`, normalized tables, weather caches and acquisition/normalization
  manifests. The raw sources, offsets, sidecars and cache metadata belong
  together; a nonempty downloaded file alone is not a valid cache entry.
- `data/models/`, its provenance/runtime specification and the referenced pinned
  model and native runtime under `external/`.
- Original collector source snapshot and its hash manifest.
- `runs/engineering_validation/`, baseline runs, actual worker probe evidence,
  protected evaluator output if used, final reports and presentation evidence.

Use a separate destination outside the active project for a backup. This
document does not initiate a backup or delete any source. Verify byte counts and
SHA-256 values against the manifests after copying. Keep the original until the
copy has passed verification and the user has chosen a retention policy.

## Restore and validate

Use the pinned source and dependency lock. Recreate a local Python environment,
verify dependency imports, and verify the exact native library/model bytes.
Absolute runtime paths may need to be registered on the new machine. A changed
runtime specification requires a fresh local capability probe; it is not an
automatic permission to continue an existing frozen campaign.

Restore historical raw/cache files and their manifests before running any
experiment. Historical acquisition may retrieve explicitly missing immutable
objects within the same finite dates and byte limits; experiment entry points
must fail rather than fetch. If an archived object has changed, preserve both
versions and assign a new dataset identity instead of overwriting the old
evidence. Rebuild normalized tables from the same policy, compare schema and
content identities, and document any metadata-only difference.

For a completed campaign, render its saved report without new model calls or
final scoring. For a paused/interrupted campaign, `campaign --action status`
must identify the original ticket, code/data binding and budget ledger before
an explicit resume. A mismatch is an audit failure, not grounds for a new
campaign ID. If the original lifetime wall or retry budget has expired, preserve
the record as incomplete; do not silently enlarge its budget.

## Schema changes

Before a schema migration, stop relevant writers, create and verify a consistent
backup of registries/manifests, record the original schema/version and source
hash, then migrate a copy transactionally. Verify keys, row counts, identities,
resource totals, tickets and artifact hashes. Preserve the original for rollback.
No migration may reset a trial count, refund an unknown worker, change an
outcome, hide a failure or reopen a spent final interval. This pilot has no
automatic migration that is authorized to do those things.

Small coverage/readiness summaries are eligible for local Git tracking through
explicit `.gitignore` exceptions. Bulk files, SQLite registries, private source,
runtime binaries, model weights and secrets remain excluded. Versioned small
manifests are provenance pointers, not a substitute for the actual local files.
