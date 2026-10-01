# Goal 1 implementation and evidence inventory

This is an index of implementation and verification evidence, not a declaration
that the historical research has passed. `data/manifests/readiness.json` is the
actual Goal 1 decision; `data/manifests/project_completion.json` records Goal 2.
Both require actual completed work. Synthetic tests cannot establish returns.

| Requirement | Implementation | Evidence required before readiness |
| --- | --- | --- |
| Preferred collector and exact original calculation | `acquire_weather.py`, `grib_reader.py`, `collector_reference.py` | Pinned nine-file source snapshot, `docs/collector-source-manifest.json`, `docs/COLLECTOR_AUDIT.md`, hash-verified reference output and schema round trip. The original calculation comparison uses synthetic inputs; original historical four-feed output was not recovered. |
| Historical weather, station and sampling semantics | `acquire_weather.py`, `repair_weather.py`, `forecast_dataset.py` | Terminal 731-day acquisition manifest, zero-transfer cache integrity pass, source indexes/byte hashes, grid coordinates, metadata checks, development-only raw re-decode audit, coverage and quarantines. |
| Historical contracts, prices and settlement mapping | `acquire_kalshi.py`, `dataset.py`, `quality.py`, `climate.py` | `kalshi_integrity.json`, source pagination/batch manifests, market-quality report, as-of NWS reconciliation, exclusions. Recorded binary outcomes determine payout; price summaries do not establish depth. |
| Time and data leakage controls | `domain.py`, `policy.py`, `forecast_dataset.py`, `offline.py` | Fixed-PST/DST, initialization/lead, revision, unit and as-of tests; chronological policy; denied network, subprocess and protected-file checks in fresh experiment processes. |
| Frozen comparable baselines | `models.py`, `baseline.py`, `replication.py` | Fitted models, daily mean/standard deviation, contract probabilities, daily scores, cost-aware decisions and ledgers, independent saved-artifact verification, frozen source/data version. |
| Returns and assumptions | `evaluation.py`, `baseline.py`, `experiments.py` | Hand-calculated fixture checks, primary opportunity/selection verification, actual binary settlement linkage, mean and capital-weighted returns, twelve predeclared cost cases, explicit evidence grade and limitations. |
| Bounded multiagent orchestration | `controller.py`, `campaign_v2.py`, `research_plan.py`, `research_protocol.py`, `local_backend.py` | Durable queue and registries, typed executable plans, six colonies, rolling folds, stage gates, negative evidence, targeted tasks, parent-linked follow-ups, allocation decisions, independent critic/replicator tasks, search coverage, clean pause and same-campaign resume tests, separate V2 one-use ticket. `campaign.py` retains V1 history and delegates the current command to V2. |
| Local model and capability boundary | `local_backend.py`, pinned native runtime and model | Matching actual capability probe, strict V1/V2 response schemas, curated development-only packets, empty tool catalog, byte-pinned runtime, shared single-inference lock. The host and native runtime remain trusted; this is not an OS sandbox. |
| Protected final evaluation | `final_evaluation.py` | Once-only ticket consumed before final-data access, frozen champion and baseline models, no refitting or discovery dispatch, numerical verification. No qualifier leaves this interval unused. |
| Evidence-preserving handoff | `reporting.py`, `engineering.py`, `acceptance.py`, `readiness.py` | Current full-suite JUnit and transcript with matching code/policy/test hashes, actual controller fixture, linked acceptance inventory, numerical comparison tables and saved CSVs, document generation from saved artifacts. |

The engineering runner reserves no research trials and makes no model calls. It
runs the complete test collection, records failures without claiming a pass,
rejects code or test changes during the run, and stores an immutable transcript.
Readiness checks the current source, tests, dependencies and policies against
that record. It also requires named cases covering time leakage, fees, unit
validation, same-day grouping, holdout denial, offline operation, worker loss,
restart, duplicate completion and bounded fixture collaboration.

Historical availability, original four-feed collector output, precise 2025 fee
applicability and executable depth are separate data limitations. The readiness
record must retain these limitations. A working pipeline does not establish a
10% future expected return, and no readiness or campaign outcome authorizes
online operation.

See `docs/OFFLINE_COMMANDS.md` for reproducible entry points and
`docs/CAMPAIGN_PROTOCOL.md` for the registered research and economic rules.
