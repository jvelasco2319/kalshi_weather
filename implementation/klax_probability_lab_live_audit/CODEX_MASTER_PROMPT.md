# Codex master prompt — Audit, complete, and wire the KLAX Probability Lab to the live strategy

Work in the existing repository:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

This package is a focused implementation contract for the **KLAX Probability Lab**. Do not assume an earlier Codex pass completed it correctly. Inspect the actual repository, determine what is already present, reuse the current architecture, and add or repair only what is necessary so the approved Probability Lab is part of the main application and is driven by the live current-strategy evaluation stream.

The exact approved UI is included at:

```text
implementation/klax_probability_lab_live_audit/ui_reference/approved_probability_lab_exact.html
```

Its SHA-256 is recorded in:

```text
implementation/klax_probability_lab_live_audit/ui_reference/APPROVED_UI_SHA256.txt
```

Treat that HTML as the **visual, content, layout, and interaction source of truth**. It contains a July 7 demonstration fixture and browser-side demonstration calculations. Those fixture values and calculations are not production strategy logic. Preserve the approved experience while replacing the fixture and strategy calculations with immutable backend data from the current live evaluator.

## Read these files before editing code

```text
implementation/klax_probability_lab_live_audit/README.md
implementation/klax_probability_lab_live_audit/docs/01_AUDIT_CHECKLIST.md
implementation/klax_probability_lab_live_audit/docs/02_BACKEND_CONTRACT_AND_WIRING.md
implementation/klax_probability_lab_live_audit/docs/03_UI_IMPLEMENTATION_MAPPING.md
implementation/klax_probability_lab_live_audit/docs/04_ACCEPTANCE_TESTS.md
implementation/klax_probability_lab_live_audit/contracts/explainability_snapshot.schema.json
implementation/klax_probability_lab_live_audit/ui_reference/approved_probability_lab_exact.html
```

Use this only as a schema and UI-development fixture:

```text
implementation/klax_probability_lab_live_audit/fixtures/sample_explainability_snapshot.json
```

Never use that fixture as a silent live fallback.

## Strategy and safety boundary

Use the repository's current five-model strategy. The expected model set is exactly:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

Expected strategy identifier, unless the repository already has a newer explicitly versioned current identifier:

```text
klax-current-five-model-2026-07-11
```

Do not create another strategy engine, do not migrate old v1/v2 implementations, and do not count model aliases as extra votes.

The Probability Lab is read-only. Preserve and prove:

```text
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
order_submission_reachable = false
```

The UI and its HTTP routes must not import or expose exchange create, cancel, replace, or submit-order methods.

## Primary objective

At the end of this task, the running application must provide a Probability Lab that lets the operator inspect the exact current evaluation for today's or tomorrow's KLAX market, including:

1. empirical physical-high scenario distributions for all five models and the weighted mixture;
2. accepted observed-high floor and verified Kalshi outcome boundaries;
3. model weights, maturity, history, effective sample size, eligibility, and source freshness;
4. posterior mean and conservative Yes/No probability for every contract;
5. the probability funnel from model posterior mean to final conservative `pTrade`;
6. equations with the actual values substituted for the selected model, contract, and side;
7. current market price, required probability, exact fee, expected value, modeled ROI, and maximum acceptable price;
8. the exact data, calibration, settlement, fee, disagreement, book, risk, and execution gates;
9. live and replay modes tied to one immutable `evaluationId`;
10. an explicit partial or blocked state when data are unavailable, with no demo substitution.

## Reuse-first rule

Before implementation, create:

```text
docs/klax_probability_lab_repo_audit.md
```

For every required capability, record:

- existing module, route, schema, service, table, template, or static asset to reuse;
- current behavior and whether it is correct;
- smallest required change;
- new code needed only when no suitable implementation exists;
- test coverage that protects the change;
- database or configuration migration, if any.

Reuse the repository's existing:

- current five-model evaluator;
- immutable evaluation persistence;
- event discovery;
- market and observation collectors;
- HTTP server and routing conventions;
- template/static-asset stack;
- polling or SSE mechanism;
- CLI conventions;
- reason-code definitions;
- replay mechanism;
- shadow-only safety boundaries.

Do not create a parallel Flask/FastAPI/React application if the repository already has a web stack. Do not add a Node build system unless the repository already uses one.

## Phase 1 — Run the package verifier and audit the repository

From the repository root, run the package verifier first:

```powershell
python implementation\klax_probability_lab_live_audit\reference\verify_package.py
```

Then run the repository's existing tests and linters.

Audit whether each of the following already exists and works:

```text
Probability Lab route or tab
latest live explainability endpoint
historical evaluation endpoint
immutable evaluation serializer
five model scenario arrays and weights
mixture scenario arrays and weights
per-model bracket probabilities
mixture bracket probabilities
separate Yes and No conservative bounds
price-sensitivity rows
backend equation trace
analysis/execution states
live evaluation refresh
replay evaluation selection
Command Center evaluationId display
Probability Lab evaluationId display
```

Do not infer completion from filenames. Exercise routes and inspect actual payloads.

## Phase 2 — Repair or implement the backend explainability snapshot

The canonical payload must validate against:

```text
contracts/explainability_snapshot.schema.json
```

Prefer extending the existing immutable strategy evaluation model and serializer. Do not recalculate the strategy in the HTTP layer.

Required endpoints, adapted to the repository's route conventions if necessary:

```http
GET /api/strategy/current/events/{event_ticker}/explainability/latest
GET /api/strategy/current/events/{event_ticker}/explainability?evaluation_id={evaluation_id}
GET /api/strategy/current/events/{event_ticker}/evaluations
```

The latest endpoint must return the newest committed evaluation for the selected live event. The evaluation-specific endpoint must return exactly one immutable revision. The evaluation-list endpoint must provide bounded replay metadata such as evaluation ID, time, analysis state, execution state, and reason code.

Use normal failure semantics:

```text
200 — valid immutable snapshot
404 — no evaluation exists
409 — evaluation exists but is internally inconsistent
503 — evaluator or persistence dependency is unavailable
```

Never return HTTP 200 with the July 7 fixture after a live failure.

### Atomicity

Read one evaluation header and all normalized children under one database transaction or snapshot. Never combine:

```text
model rows from evaluation A
market rows from evaluation B
economics from evaluation C
gates from evaluation D
```

Every displayed number must be traceable to the same `evaluationId`.

### Required backend calculations

The Probability Lab must render persisted results from the current strategy, including:

```text
remaining-window maximum per model
observed-high floor
raw live state
historical correction
corrected point
scenario temperatures and scenario weights
history count and effective sample size
prior and effective model weights
maturity and eligibility
per-model posterior mean Yes and No
per-model conservative Yes and No
mixture posterior mean Yes and No
mixture-count lower bounds
weighted-component lower bounds
final pTrade Yes and No
market prices and book state
exact fee and fee schedule version
required probability
expected value
modeled net ROI
maximum acceptable price
price-sensitivity grid
final decision and reason code
all gates and capture health
```

If the evaluator does not currently persist one of these values, extend the evaluator or its normalized child records. Do not reconstruct decision-critical values in the browser.

### Partial states

Return an explainability snapshot even when the decision is blocked, provided an immutable evaluation exists. Use explicit states:

```text
DATA_BLOCKED
ANALYSIS_PARTIAL
ANALYSIS_READY + BLOCKED
ANALYSIS_READY + NO_TRADE
ANALYSIS_READY + SHADOW_CANDIDATE
```

Unavailable values should be `null` and arrays should be empty. Include the exact reason and missing input. Do not hide all graphs merely because book depth is unavailable. If probabilities are valid but execution is blocked, show the probability analysis and label economics as quote-only or non-executable where appropriate.

## Phase 3 — Integrate the exact approved UI into the main application

Use:

```text
ui_reference/approved_probability_lab_exact.html
```

as the visual and interaction baseline. Preserve, as closely as the existing app stack permits:

- dark KLAX Signal Room styling;
- header and status chips;
- event, time/evaluation, contract, side, and model controls;
- six headline cards;
- physical-high scenario distribution figure;
- model contribution ledger;
- probability funnel;
- equation trace;
- bracket probability matrix;
- market-versus-weather chart;
- price-sensitivity chart;
- calculation and data-health panel;
- explanatory section;
- responsive desktop and mobile layout.

Add the Probability Lab to the existing Signal Room navigation, preferably:

```text
Command Center | Probability Lab
```

Use the current application shell, server, templates, and static asset conventions. A suitable route is:

```text
/strategy/probability-lab
```

but follow existing route conventions if another path fits better.

### Replace demo logic, not the approved design

The attached HTML contains:

```javascript
const DATA = {...demo fixture...}
```

and browser-side demonstration functions such as fee, required-probability, ROI, and decision logic. In the production implementation:

- remove the embedded fixture from the shipped live page;
- fetch or stream a real explainability snapshot;
- render backend-provided economics and gate results;
- render backend-provided equation steps;
- use backend-provided price-sensitivity rows;
- use backend-provided scenario values and weights;
- keep only presentational calculations in the browser, such as scales, SVG paths, weighted histogram/ECDF bins, sorting, filtering, and number formatting.

The production JavaScript must not implement:

```text
fee formulas
Beta/Dirichlet calculations
model weighting
scenario probability assignment
pTrade selection
required probability
expected value
ROI
maximum acceptable price
risk sizing
trade eligibility
final reason code
```

A grep or code-review test should prove those calculations are absent from the production UI bundle.

### Distribution figure integrity

The approved preview uses a smoothed display for demonstration. Production must use the actual backend scenario temperatures and weights. Implement one honest weighted view:

```text
weighted histogram with optional smoothed display line
weighted ECDF
or backend-provided density bins
```

If smoothing is used, it must be presentational only, respect scenario weights, and preserve access to the underlying empirical points or bins. Do not fabricate a Gaussian distribution from a point estimate or an arbitrary sigma.

### Live mode

In live mode:

- discover and display the current selected KLAX event, with optional next-day event selection;
- load the latest immutable evaluation;
- subscribe through the repository's existing SSE/WebSocket mechanism or poll at a bounded interval;
- update the entire page atomically when a new `evaluationId` arrives;
- never mix fields from two evaluations;
- display evaluation age and source freshness;
- maintain the user's selected contract, side, and equation model when possible;
- show a clear connection/stale state if updates stop.

### Replay mode

In replay mode:

- load the evaluation index for the selected event;
- use the time/evaluation control to request an immutable historical evaluation;
- freeze the page until the operator selects another evaluation;
- clearly label replay mode;
- never silently switch back to live data.

### Shared state with the Command Center

The Command Center and Probability Lab must show the same:

```text
eventTicker
evaluationId
evaluatedAt
analysisState
executionState
finalReasonCode
selected focus market/side, when applicable
```

If the Command Center currently does not display `evaluationId`, add a compact identifier or diagnostic detail so the operator can verify synchronization.

## Phase 4 — Implement all approved panels

The implementation checklist in `docs/03_UI_IMPLEMENTATION_MAPPING.md` is mandatory. Every panel must have a live backend data source and an explicit partial-state behavior.

Do not omit a panel because a field is temporarily unavailable. Render the panel with an explicit reason and missing-input list.

## Phase 5 — Tests

Implement the acceptance tests in:

```text
docs/04_ACCEPTANCE_TESTS.md
```

At minimum include:

### Contract tests

- sample fixture validates against the schema;
- a real latest evaluation validates against the schema;
- exactly the five approved model slots are present, including explicit unavailable rows;
- scenario arrays and weights align;
- nonempty scenario weights sum to approximately one;
- per-model posterior mean Yes values across exhaustive mutually exclusive outcomes sum to approximately one;
- mixture posterior mean Yes values sum to approximately one;
- `pTrade` never exceeds either conservative input bound;
- market outcome map is verified, ordered, non-overlapping, and exhaustive;
- price-sensitivity rows are monotonic in required probability where expected;
- no source timestamp is later than `evaluatedAt`.

### API tests

- latest endpoint returns the newest committed evaluation;
- evaluation-specific endpoint is stable and immutable;
- no-evaluation returns 404, not demo data;
- inconsistent child records return an error, not a mixed snapshot;
- partial evaluation returns null/empty fields and precise reasons;
- current and next-day event selection work.

### UI tests

- all approved panels render from a fixture payload;
- all approved panels render from a real live payload;
- event, evaluation, contract, side, and model controls work;
- changing contract/side/model does not call or reproduce strategy math in the browser;
- live refresh replaces the entire snapshot only when `evaluationId` changes;
- replay mode remains frozen;
- unavailable model and missing-calibration states are visible;
- probability analysis remains visible when execution book is blocked;
- no external scripts, fonts, analytics, or telemetry are requested;
- no order button or order API path exists;
- desktop and mobile layouts have no clipping or horizontal overflow.

### Safety tests

- UI service cannot import or resolve create/cancel/replace order methods;
- live order flags remain false;
- fixture payload cannot be selected automatically in live mode;
- network/API failure produces an explicit error state;
- no browser code independently calculates fees, ROI, probabilities, or eligibility.

## Phase 6 — Live acceptance run

Run the live shadow stack for at least five continuous minutes with the Probability Lab open.

Save:

```text
artifacts/probability_lab/live_desktop.png
artifacts/probability_lab/live_mobile.png
artifacts/probability_lab/live_explainability_payload.json
artifacts/probability_lab/live_browser_console.txt
artifacts/probability_lab/live_network_requests.json
artifacts/probability_lab/live_acceptance_report.md
```

The report must state:

```text
current event ticker
latest evaluation ID
evaluation timestamp
analysis state
execution state
final reason code
five model statuses
calibration count per model
whether distributions are live or unavailable
whether economics are executable-book or quote-only
whether any fixture data were used
whether any real order path is reachable
```

If the strategy is genuinely uncalibrated, the Probability Lab should still render live model states, history counts, outcome map, market data, missing inputs, and exact gate reasons. Do not call the task complete with a fixture screenshot.

## Completion criteria

Do not declare completion until all of these are true:

1. The exact approved UI has been incorporated into the main application.
2. The page is reachable from the current Signal Room navigation.
3. The page is driven by a real immutable live or replay evaluation.
4. The latest explainability payload validates against the schema.
5. All five model slots appear with explicit status.
6. Every approved panel is present.
7. Probabilities, equations, economics, and gates are backend-provided.
8. Live refresh and replay selection work.
9. No fixture is used as a silent fallback.
10. No real order action is exposed or reachable.
11. Desktop and mobile browser tests pass.
12. Existing repository tests remain green.

## Final response required from Codex

Provide a concise implementation report containing:

1. repository audit path;
2. existing modules reused;
3. modules/routes/tables/assets added or changed;
4. the final Probability Lab route;
5. explainability API routes;
6. latest live evaluation ID and event ticker used for verification;
7. which approved panels were already present and which were added;
8. any backend fields that were missing and how they were persisted;
9. whether the five model distributions and mixture are live;
10. whether equations and economics are backend-provided;
11. live/replay synchronization proof;
12. schema, API, UI, and safety test results;
13. desktop/mobile screenshot paths;
14. any genuinely remaining data/calibration limitations;
15. proof that fixture fallback and order paths are disabled.

Do not return only a plan. Make the changes, run the tests, and provide the evidence.
