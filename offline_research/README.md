# Kalshi LAX Offline Research

A local research project for testing whether historical weather forecasts can identify Kalshi LAX daily-temperature opportunities with at least 10% estimated net expected return per trade. No profitability has been demonstrated.

## Authoritative goal document

Read [GOALS.md](GOALS.md) for the complete architecture, data plan, research tracks, evaluation rules, milestones, acceptance criteria, and launch procedure.

1. **Goal 1:** Build and verify the complete offline research foundation, including historical datasets, baselines, market replay, independent evaluation, and a bounded agent controller.
2. **Goal 2:** Launch and complete the first bounded multiagent research campaign and deliver a reproducible historical performance report. No improvement or insufficient evidence are valid conclusions.
3. **Goal 3:** Implement and run the bounded V3 expanded-evidence campaign: minute Kalshi history, exact settlement reconstruction, HRRR and local observations, GEFS uncertainty, richer probabilities, weather regimes, selective abstention, five-fold stability, bootstrap uncertainty, independent replication, and adversarial review.

The corrected six-week request is complete as V7H, a historical replay covering August 4 through September 14, 2026. All 42 dates were predicted and assigned hypothetical orders before outcomes were opened. Exact paid 18:00/+5-second books cover 40 dates; two unavailable dates remain abstentions. V5B passed the preliminary screen with +27.75% across nine fills, while V5F provided supporting positive evidence. The dates were already exposed in earlier research, so this is stability evidence rather than an independent confirmation. See [the V7H record](docs/V7H_HISTORICAL_SIX_WEEK_REPLAY.md) and [independent validation](runs/replays/v7h-historical-20260804-20260914/validation/V7H_VALIDATION.md).

V8 repairs V7Y's overconfident probability layer and is the current research lead. Its fixed chronological confusion repair reduced 2025 development Brier from 0.8590 to 0.7507, reduced log loss from 6.6129 to 1.5188, and eliminated all 52 zero-probability realized outcomes. Applied without 2026 updates to the strict V7I books, it produced +24.59% capital-weighted simulated return across 13 fills, but failed the sample, fold-consistency, bootstrap, and untouched-data gates. This is promising exposed stability evidence rather than a verified edge or online-ready strategy. The exact future-test strategy is now immutable in [the V8 strategy freeze](runs/v8/frozen-primary-strategy/strategy-freeze.json), sealed as `a227b8f72ad040c2411fe63d3863d1b9c9c11121c6df5fce2fadb198ea2379fd`. See [the V8 goal](docs/V8_PROBABILITY_REPAIR_AND_REPLAY_GOAL.md) and [economic review](reports/V8_ADVERSARIAL_ECONOMICS.md).

V9 is a separately frozen physical-weather research successor. It preserves V8 unchanged and pre-registers observed KLAX cloud, ceiling and dewpoint features, HRRR inversion strength, a KLAX-minus-KDAG pressure gradient, six finite candidates, chronological forecast gates, and one-shot economic confirmation. It is an unfitted protocol awaiting additional data, not a promoted strategy. See [the V9 goal](docs/V9_METEOROLOGICAL_SUCCESSOR_GOAL.md), [registered protocol](configs/v9_meteorological_successor.json), and [immutable freeze](runs/v9/frozen-research-protocol/protocol-freeze.json).

V10 completed the V9 data direction as a finite 193-candidate development experiment. It acquired full-year archived KLAX/KDAG observations plus HRRR dewpoint and 925 hPa temperature, then evaluated every subset of six registered meteorological blocks at three fixed overlay weights. The pressure-only 75% overlay led: Brier improved from 0.7507 to 0.7343, log loss from 1.5188 to 1.4846, and modal accuracy from 34.65% to 37.99%, with both scores improving in all five chronological folds. The simple 18-member single-block family passed a post-hoc familywise audit, while the full 192-alternative family narrowly missed the adjusted log-loss test. The benefit was concentrated on observed onshore-pressure days; offshore days remained weak. The exact candidate is frozen under seal `ec762e9524664b6b9892b4586b417ce7de357ef7e10576cac0b2a17c69824f79` for later economic replay and genuinely new confirmation. It is exposed development evidence, not a profit claim or authorization for online use. See [the V10 report](runs/v10/all-meteorological-combinations/REPORT.md), [multiple-comparison audit](runs/v10/all-meteorological-combinations/MULTIPLE_COMPARISON_AUDIT.md), and [candidate freeze](runs/v10/frozen-development-candidate/candidate-freeze.json).

V5A is retained as an earlier bounded campaign record. It uses the complete 92-day June-August 2026 HRRR/GEFS window, a 64-day development split, and a 28-day one-shot holdout that has since been opened by later research. Paid execution evidence remains labeled A, B+, B, or unavailable; the original V5 strict verdict remains unchanged.

## Preferred weather collector

[weather_data_collector](https://github.com/ciel-ravencourt/weather_data_collector.git) is preserved as an untouched nine-file source snapshot at commit `a82f8aee0afecf568b3ce16f339c0bd7201773b8`, obtained using the signed-in browser's individual-file downloads. [The source manifest](docs/collector-source-manifest.json) records hashes; [the audit](docs/COLLECTOR_AUDIT.md) explains the native Windows adapter and changes needed for reliable historical research. Git history and example datasets were not copied.

## Starter historical data

`data/raw/bootstrap/` contains 2,192 NOAA LAX station-day observations for 2020-2025, a sample of 100 historical Kalshi KXHIGHLAX contracts, and the historical API cutoff response. [The download manifest](docs/bootstrap-download-manifest.json) records provenance, hashes, byte counts, and basic checks.

The complete Kalshi price acquisition now covers 361 dates, January 5 through December 31, 2025: 2,166 contracts and 72,390 hourly price records. All 2,171 raw responses passed SHA-256 and size verification. These summaries do not contain executable historical depth. See [coverage](docs/KALSHI_COVERAGE.md).

Archived NWS CLILAX products and the two-year GFS/NBM weather archive are downloaded and normalized with revision timestamps. The V3 archive now also contains all 543 registered HRRR, GEFS, and as-of local-observation days: 10,860 raw weather objects totaling about 20.6 GB and 21,720 normalized forecast rows. The development market archive contains 614,093 one-minute candles and 170,669 public trades for 1,062 contracts. Exact reconciliation produced 1,050 mutually exclusive outcomes across 175 eligible January-June 2025 settlement days; June 10-11 remain explicit source exclusions. Every frozen input is hash-bound by dataset identifier `872161299f101a830f60cd0e546732b20d7e490b18c140bb17eb9fca5912ec0c`.

## Implementation status

Implemented: historical acquisition, source audits, climate-day and contract mapping, chronological partitions, five deterministic baseline models, development replay with costs and sensitivity scenarios, provenance manifests, guarded experiment entry points, and a transactional bounded controller. Architecture V2 adds six research colonies, a typed plan compiler, rolling fold diagnostics, stage gates, iterative synthesis, parent-linked follow-ups, targeted research requests, portfolio allocation and search-coverage reporting.

The completed V1 campaign tested three unique candidates and found no improvement. The completed V2 campaign `local-20260925T170513829794Z` then ran four productive epochs, made 98 local model calls, and compiled, executed, independently replicated, and rejected 40 distinct candidates. Its best development result was approximately -0.289% capital-weighted simulated net return across 122 assumed-fill trades. There was no champion, the protected final remained unopened, and no 10% edge or actual account profit was demonstrated.

V3 is registered in the [expanded-evidence contract](docs/V3_IMPLEMENTATION_CONTRACT.md) and [machine-readable goal](configs/v3_goal.json). Its completed campaign was capped at six epochs, 60 candidates, 180 local model calls, one inference process, and eight hours. A champion had to pass every frozen gate: at least 10% development capital-weighted net return, positive cost stress, positive return in at least four of five chronological folds, a one-sided 95% bootstrap lower bound above zero, sufficient trades, no CRPS or Brier degradation, exact independent reproduction, and critic nonrejection.

The V3 architecture and bounded campaign are complete. Campaign `v3-offline-20260926T164700000Z-r2` ran six epochs, made 61 total model calls across its verified continuation chain, and evaluated 60 distinct candidates. It stopped when the registered candidate budget was exhausted with conclusion `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`. All candidates improved forecast scores relative to the registered reference (Brier 25.34% lower and CRPS 32.94% lower), but none produced a simulated trade. Of 23,760 contract-side decisions that reached market screening, 57.9% first failed the spread control, 27.4% the entry-price band, 14.6% quote freshness, and one the 10% expected-return floor. Every candidate therefore failed the economic, sample-size, stability, stress, and concentration gates. There was no champion, no protected-final authorization, no protected-final read, and no actual order.

The result also exposed a bounded-search coverage defect: all 60 candidates used 12:00 UTC, a 4°F interval-width cap, and substantially the same forecast stack, while only four came from the execution/abstention colony. The registered language supported other decision times, interval widths, and quote-age controls, but the allocator did not reach them before exhausting its budget. This does not invalidate the negative result; it defines the next preregistered research question. See the [current execution record](docs/IMPLEMENTATION_STATUS.md), [final campaign analysis](reports/v3-final-campaign-analysis.md), and [requirement-by-requirement completion audit](reports/v3-objective-completion-audit.md).

Because minute Kalshi history does not exist in the local archive before January 5, 2025, V3 reserves January 5 through February 3 as a fixed 30-day market-calibration prefix. Those days may fit the market-residual, market-conditioned calibration, and abstention layers but are excluded from return scoring. The shared five-fold evaluation window begins February 4. This prevents later development outcomes from leaking into earlier historical predictions.

The local OpenAI worker passed fresh actual tool-free V2 and V3 typed-plan capability probes. The V3 host supplies six registered alternatives per task, accepts an exact in-packet plan, and rejects invented plans. The capability boundary is not an OS sandbox. See [local workers](docs/LOCAL_WORKERS.md), [runtime provenance](docs/LOCAL_RUNTIME.md), the [V2 campaign protocol](docs/CAMPAIGN_PROTOCOL.md), and the [architecture cross-check](docs/NAVIER_STOKES_ARCHITECTURE_CROSSCHECK.md).

The code-only snapshot is published under `offline_research/` on the `swarm` branch of [jvelasco2319/kalshi_weather](https://github.com/jvelasco2319/kalshi_weather/tree/swarm/offline_research). Large datasets, generated runs, paid-source exports, local model binaries, credentials, and virtual environments remain local and must be transferred separately. No live feed or order connection exists. The prospective V7 schedule was paused before its first target date; V7H is complete and requires no background acquisition.

The final integrated V3 suite passes **632 tests and 114 parameterized subtests**. Its evidence includes finite-plan enforcement, denial filtering, exact continuation import, packet-size preflight, one-use campaign-bootstrap transitions, atomic publication, campaign leasing, independently coded ledger and metric recomputation with deliberate-corruption detection, and the sealed final-evaluation boundary. The terminal campaign verifier passed all 1,007 artifacts, and a second read-only audit confirmed that all 69 frozen source files still match the authorized code inventory. V2 also retains 429,505 independent replication checks. [Implemented commands](docs/OFFLINE_COMMANDS.md), [campaign recovery](docs/CAMPAIGN_RUNTIME.md), [restoration](docs/RESTORATION.md), and the [implementation inventory](docs/IMPLEMENTATION_INVENTORY.md) describe operation and boundaries.

## Local commands

Use the project's `.venv\Scripts\python.exe` with `PYTHONPATH=src` from this directory. The commands below are implemented; they do not start historical downloads implicitly.

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m klax_lab.cli status
.\.venv\Scripts\python.exe -m klax_lab.cli prepare
.\.venv\Scripts\python.exe -m klax_lab.cli probe-worker
.\.venv\Scripts\python.exe -m klax_lab.cli baseline
.\.venv\Scripts\python.exe -m klax_lab.cli controller-fixture
.\.venv\Scripts\python.exe -m klax_lab.cli readiness
.\.venv\Scripts\python.exe -m klax_lab.cli v3-fixtures
.\.venv\Scripts\python.exe -m klax_lab.cli v3-status
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 probes --root .
.\.venv\Scripts\python.exe -m klax_lab.substantive_readiness_v3 validate --root .
.\.venv\Scripts\python.exe -m pytest -q
```

`prepare` can report partial coverage. `baseline` waits for a terminal two-year acquisition pass and at least 95% validated forecast coverage in each development partition, hashes its exact inputs, denies Python network/subprocess activity, and excludes the protected final partition. Missing or contradicted inputs are quarantined with their dates disclosed. It reports simulated settlement returns separately from forecast scores. Historical fees, publication timing and hourly fills retain explicit assumptions. No account profit or executable 10% edge has been demonstrated.

`python -m klax_lab.pipeline --root . --timeout-hours 7` is the retained V2 dependency chain. V3 used `python -m klax_lab.orchestrator_v3` after complete normalization, frozen-data verification, all twelve substantive component checks, and one-use ticket issuance. The one-use V3 bootstrap is now `COMMITTED`; rerunning it is not a fresh campaign.

Do not start duplicate finite jobs while a run lock is held. Cache resumption is supported after an interrupted registered acquisition.

The background [research plan](docs/research-plan.md) reconstructs the earlier [Apply Navier Stokes Method](https://chatgpt.com/c/6aa9e345-d790-83e8-a399-b32ae86b65bf) discussion. GOALS.md controls if wording differs.
