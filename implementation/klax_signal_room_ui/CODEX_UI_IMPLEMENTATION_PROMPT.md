# Codex Prompt: Implement the Approved KLAX Signal Room UI

You are implementing the approved **Prototype A / KLAX Signal Room** operator dashboard in the existing repository:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

The current strategy implementation package should already be present under a non-imported directory such as:

```text
implementation/klax_current/
```

Use these visual references from this UI add-on package:

```text
ui_reference/approved_prototype_a.html
ui_reference/approved_prototype_a.png
```

The approved visual direction is Prototype A. Preserve its visual hierarchy, dark theme, colors, responsive behavior, and one-screen operator workflow. Productionize it against the real current five-model strategy; do not ship the embedded July 7 sample data as the live data source.

## Authority and scope

Authority order:

1. The current five-model package for strategy, risk, probability, fee, and safety behavior.
2. This prompt for dashboard behavior and integration.
3. The approved Prototype A HTML and screenshot for visual design.
4. Existing repository conventions where they do not conflict with the above.

Do not implement, migrate, or depend on v1, v2, or v3.

The dashboard is **read-only**. It may display persisted decisions and shadow candidates, but it must never create, modify, cancel, replace, or submit orders. Do not add order buttons. Do not import a live-order client into the dashboard process. Do not change any strategy equation or risk rule as part of this UI task.

## Goal

Create a simple local web dashboard that answers, at a glance:

1. What is the bot's current decision?
2. Why did it make that decision?
3. What do the five approved models currently estimate?
4. How much do the models disagree?
5. Is the market price cheap enough to meet the active expected-return hurdle?
6. Which safety/data gates are passing, warning, or blocking?
7. Is the system collecting enough trustworthy data to advance beyond shadow mode?

The approved model set is exactly:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

No other model may appear as a strategy vote in this dashboard. Legacy or research feeds may remain elsewhere in the repository but must not enter dashboard strategy totals, spread, support, weights, or feed counts.

## First actions: inspect and map before editing

Do not make production edits until these steps are complete:

1. Verify the current package and repository baseline:

   ```powershell
   cd C:\Users\jarve\Documents\Codex\kalshi_weather

   $env:PYTHONPATH = "implementation\klax_current\reference"
   python implementation\klax_current\reference\verify_package.py

   python -m pytest -q
   python -m ruff check src tests
   ```

2. Inspect the actual repository for:
   - CLI framework and command registration;
   - any existing HTTP server, dashboard, templates, or static asset system;
   - SQLite and JSONL journal access patterns;
   - current immutable decision/evaluation records;
   - model-state, observation, market snapshot, order-book, fee, settlement, and capture-health records;
   - current strategy reason codes and enums;
   - existing Pydantic/dataclass/domain models;
   - existing configuration and dependency extras;
   - existing frontend or browser-test tooling.

3. Create:

   ```text
   docs/klax_signal_room_repo_mapping.md
   ```

   For every responsibility in this prompt, record:
   - existing module to reuse;
   - minimal extension required;
   - new module required only if no existing module fits;
   - migration, configuration, and test impact.

4. Preserve the existing architecture wherever it is correct. Do not create a parallel data store, duplicate exchange client, duplicate strategy engine, or duplicate CLI framework.

## Implementation architecture

### Reuse rule

Reuse an existing web stack if the repository already has one.

If the repository has no web framework, use this minimal fallback:

- FastAPI or Starlette for a read-only local HTTP service;
- Uvicorn for local serving;
- vanilla HTML, CSS, and JavaScript;
- Jinja2 only if server-side templating is useful;
- an optional dependency group such as `dashboard` so the core trading package does not require web dependencies.

Do not introduce React, Vue, Angular, Vite, a Node build pipeline, a CDN, or a frontend framework merely for this dashboard. The approved prototype is achievable with semantic HTML, CSS, SVG, and small vanilla-JavaScript modules.

### Preferred module shape when no existing modules fit

Adapt names to repository conventions. A reasonable fallback is:

```text
src/kalshi_weather/signal_room/
    __init__.py
    api_models.py
    repository.py
    service.py
    app.py
    cli.py
    templates/
        index.html
    static/
        signal_room.css
        signal_room.js
```

This is a fallback, not a mandate to duplicate an existing application layer.

### Read-only boundary

Create a narrow read interface such as:

```python
class SignalRoomReadRepository(Protocol):
    def list_events(...) -> list[EventSummary]: ...
    def get_latest_snapshot(event_ticker: str, ...) -> SignalRoomSnapshot: ...
    def get_timeline(event_ticker: str, ...) -> list[SignalRoomTimelinePoint]: ...
    def get_capture_health(event_ticker: str, ...) -> CaptureHealth: ...
```

The repository reads persisted, immutable strategy and market records. The dashboard must not rerun strategy math in JavaScript, and preferably should not independently reimplement the strategy in the HTTP layer either.

The strategy engine remains the only authority for:

- model state;
- effective weights;
- model spread;
- NBM maturity;
- posterior probabilities;
- conservative `p_safe` values;
- active ROI hurdle;
- exact fees;
- modeled ROI;
- max acceptable price;
- gate status;
- candidate selection;
- position size;
- reason codes.

The UI may only format these values, sort reader-facing rows according to an explicit backend order, and calculate presentation-only durations such as “3 seconds ago.”

## CLI and service behavior

Add an additive command using the existing CLI framework. Preferred name:

```text
kalshi-weather strategy-dashboard
```

Suggested options:

```text
--host 127.0.0.1
--port 8765
--event auto
--mode live|replay
--target-date YYYY-MM-DD
--open-browser / --no-open-browser
--poll-seconds 2
```

Requirements:

- Default bind address is `127.0.0.1`.
- Binding to a non-loopback interface requires an explicit `--allow-remote` flag and a clear warning.
- Do not expose credentials, API keys, private keys, raw auth headers, account identifiers, or unrestricted filesystem paths.
- The dashboard service must remain usable when the exchange order client is not configured.
- Shadow/live strategy mode is displayed from persisted state; the UI cannot change it.
- The command must not create a callable order-submission path.

## Read API

Use existing API conventions if present. Otherwise implement a compact versioned read API:

```text
GET /api/v1/signal-room/health
GET /api/v1/signal-room/events
GET /api/v1/signal-room/events/{event_ticker}/snapshot
GET /api/v1/signal-room/events/{event_ticker}/timeline
GET /api/v1/signal-room/events/{event_ticker}/capture-health
```

Recommended query parameters:

```text
snapshot?as_of=<ISO-8601 timestamp>
timeline?start=<ISO>&end=<ISO>&limit=<bounded integer>
```

Use polling for the first version. Poll every two seconds by default, with an ETag or equivalent revision token so unchanged snapshots can return `304 Not Modified`. Do not add browser WebSockets unless the repository already has a suitable read-only application stream.

Bound all timeline queries. Do not return unbounded raw event history.

## Canonical dashboard snapshot contract

Map existing domain models into one stable, frontend-facing read model. Do not force the strategy domain to depend on UI types.

Use timezone-aware ISO-8601 timestamps. Use decimal strings or fixed-point integer fields for prices, quantities, fees, bankroll, and monetary values. Probabilities are numbers on `[0, 1]`. Temperatures may be numeric Fahrenheit values.

A snapshot should contain the following logical fields. Rename only to match established repository conventions.

```json
{
  "schema_version": "1",
  "revision": "immutable snapshot or monotonic revision id",
  "generated_at": "2026-07-11T18:00:00Z",
  "event": {
    "ticker": "KXHIGHLAX-26JUL11",
    "target_date": "2026-07-11",
    "station": "KLAX",
    "status": "open|closed|settled",
    "market_open_at": "ISO timestamp",
    "market_close_at": "ISO timestamp",
    "settlement_bracket": null,
    "final_decimal_high_f": null,
    "official_high_f": null
  },
  "strategy": {
    "strategy_id": "klax-current-five-model-2026-07-11",
    "mode": "shadow",
    "live_trading_enabled": false,
    "canary_enabled": false,
    "taker_enabled": false,
    "order_submission_reachable": false,
    "code_revision": "git sha or equivalent",
    "config_hash": "stable hash"
  },
  "decision": {
    "evaluated_at": "ISO timestamp",
    "status": "TRADE_CANDIDATE|SHADOW_ONLY|NO_TRADE|DATA_INCOMPLETE",
    "reason_code": "stable repository reason code",
    "reason_text": "short operator-facing explanation",
    "focus_ticker": null,
    "focus_bracket": null,
    "focus_side": null,
    "executable_price": null,
    "p_mean": null,
    "p_safe": null,
    "required_probability": null,
    "modeled_net_roi": null,
    "max_acceptable_price": null,
    "proposed_quantity": null
  },
  "risk": {
    "model_spread_f": null,
    "active_roi_hurdle": 0.15,
    "adjusted_probability_hurdle": null,
    "observed_high_f": null,
    "market_leader_bracket": null,
    "risk_multiplier": null,
    "target_date_exposure_pct": null,
    "daily_loss_pct": null
  },
  "models": [
    {
      "model_key": "ecmwf_ifs",
      "label": "ECMWF IFS",
      "display_order": 1,
      "state_f": null,
      "remaining_window_max_f": null,
      "observed_floor_f": null,
      "mapped_bracket": null,
      "prior_weight": null,
      "effective_weight": null,
      "maturity_completed_dates": null,
      "maturity_required_dates": null,
      "maturity_status": "mature|provisional|excluded",
      "source_available_at": null,
      "received_at": null,
      "age_seconds": null,
      "strict_as_of_valid": null,
      "feed_status": "healthy|stale|missing|invalid|reference_only",
      "status_detail": null
    }
  ],
  "gates": [
    {
      "code": "stable gate code",
      "label": "Model spread",
      "severity": "pass|info|warning|block",
      "detail": "reader-facing explanation"
    }
  ],
  "readiness": {
    "tradable_feed_count": 0,
    "required_tradable_feed_count": 4,
    "independent_family_count": 0,
    "required_independent_family_count": 3,
    "nbm_completed_dates": 0,
    "nbm_next_maturity_threshold": 10,
    "orderbook_sequence_valid": false,
    "orderbook_depth_available": false,
    "fee_schedule_verified": false,
    "settlement_rules_verified": false,
    "capture_health_status": "healthy|warning|invalid"
  },
  "market": [
    {
      "ticker": "contract ticker",
      "bracket": "73–74°F",
      "yes_bid": null,
      "yes_ask": null,
      "no_bid": null,
      "no_ask": null,
      "p_mean_yes": null,
      "p_safe_yes": null,
      "p_safe_no": null,
      "required_probability_yes": null,
      "modeled_net_roi_yes": null,
      "max_acceptable_yes_price": null,
      "model_point_support_count": null,
      "eligible": false,
      "candidate": false,
      "status_code": null,
      "settled_outcome": null
    }
  ]
}
```

The timeline endpoint should return bounded, chronologically sorted points containing:

- evaluation timestamp;
- observed high so far;
- state-consistent value for each of the five models;
- optional persisted mixture summary if already produced by the strategy;
- decision status and reason code;
- focus contract and market price;
- revision/source ids needed for reproducibility.

Do not interpolate missing model states. Display gaps.

## Approved visual layout

Use the approved Prototype A HTML and screenshot as the visual source of truth.

### 1. Header

Display:

- `KLAX Signal Room` title;
- event ticker and target date;
- strategy mode chip, normally `SHADOW MODE`;
- `5 model slots`;
- count of actual live orders, expected to be `0` in shadow;
- `order path disabled` when `order_submission_reachable=false`.

Do not retain the text “Prototype A” in the production title.

### 2. Status banner

Replay mode:

- show a clear “Historical replay” banner;
- state the selected event and timestamp;
- show when the event is settled;
- make hindsight-only fields visually distinct.

Live mode:

- show capture health, latest source age, and stale-data warnings;
- hide the banner when everything is healthy only if the remaining status chips still make mode and freshness obvious.

Never present replay outcomes as if they were available live.

### 3. Control bar

Replay mode:

- keep the approved checkpoint slider;
- checkpoints represent persisted immutable evaluations only;
- display the selected Pacific Time checkpoint;
- no interpolation between checkpoints.

Live mode:

- replace the replay slider with event selector, latest evaluation time, refresh status, and pause/resume control;
- polling remains read-only;
- pausing refresh does not pause the strategy process.

### 4. Current decision card

Preserve the approved large decision treatment.

Allowed display states:

- `TRADE CANDIDATE`
- `SHADOW ONLY`
- `NO TRADE`
- `DATA INCOMPLETE`

Show:

- operator-facing reason;
- exact machine-readable reason code;
- current best focus contract and side;
- executable price or `—`;
- never pick the eventual winner as the focus row merely because replay settlement is known.

The focus contract is the persisted best incremental candidate selected at that evaluation. If no candidate exists, display `No eligible contract`.

### 5. Risk snapshot card

Keep four primary metrics:

1. Comparable five-model spread in °F.
2. Active required probability for the focus price and hurdle, labeled clearly.
3. Observed KLAX high so far.
4. Current market-leading bracket.

Use `—` for missing values. Do not convert missing to zero.

Warnings:

- spread `<3°F`: neutral;
- `3–4°F`: amber;
- `>=4°F`: red/block;
- use persisted gate results as authority even if configurable thresholds later change.

### 6. Five-model forecast path

Keep the large central line chart and the five model cards beneath it.

Fixed model order and colors:

```text
ECMWF IFS      #5B8FF9
GFS 0.13°      #F6BD16
GFS Seamless   #E8684A
NAM            #6AA84F
NBM            #C66DD4
```

The plotted value is the strategy's persisted, state-consistent model state:

```text
max(observed high so far, remaining-window forecast maximum)
```

Do not plot a legacy full-day maximum as if it were this value.

Chart requirements:

- show honest gaps where a model is missing or invalid;
- mark the selected replay checkpoint;
- in a settled replay, show the final decimal high as a neutral dashed reference;
- in live mode, never show a future final-high reference;
- optionally show observed high so far as a quiet neutral step/reference line if it remains readable;
- use Pacific Time on the visible x-axis;
- accessible SVG title/description and keyboard-readable current values;
- no external charting CDN.

Each model card shows:

- model label;
- state-consistent °F value;
- mapped bracket;
- prior weight;
- effective weight;
- source age/time;
- maturity or feed status.

Status examples:

- healthy/captured: green;
- provisional NBM: amber;
- stale/missing/as-of-invalid: red;
- reference-only or excluded: neutral.

Do not manufacture effective weights when the probability engine is unavailable. Display `—` and a clear status.

### 7. System gates

Render backend-provided gates in severity order:

1. blockers;
2. warnings;
3. informational items;
4. passing checks.

Include, when available:

- strict as-of validity;
- five-model availability;
- independent family count;
- model spread;
- residual/probability readiness;
- NBM maturity;
- order-book sequence validity and freshness;
- trade quantity/capture completeness;
- fee schedule verification;
- settlement-rule verification;
- regime drift;
- exposure and daily-loss limits;
- price-too-high gate;
- live-order-path disabled status.

Use exact persisted gate codes for debugging, either in a tooltip, expandable detail, or visible monospace badge.

### 8. Feed readiness

Keep the four-card layout:

- tradable feeds, for example `4 / 5`;
- NBM maturity, for example `12 / 30` with next threshold explained;
- book depth/sequence health;
- live execution state.

A nonempty REST response is not automatically “healthy.” Display the persisted capture-health result.

### 9. Bracket market table

Keep the full-width table and highlighted focus row.

Production columns should be:

```text
Bracket
Bid
Ask
p-safe YES
Required p at active hurdle
Modeled net ROI
Status
```

Optional compact secondary details may include:

- model point-support dots/count, explicitly labeled descriptive only;
- max acceptable price;
- settled outcome in replay mode;
- Yes/No toggle or paired detail if the backend has selected a No candidate.

Rules:

- highlight the persisted focus candidate, not the known replay winner;
- use exact backend values for `p_safe`, required probability, ROI, and eligibility;
- when calibration is unavailable, display `Unavailable`, not zero and not an invented estimate;
- when a price can never clear the hurdle even at probability 1, display `Impossible`;
- in live mode, do not display outcome;
- in replay mode, visually distinguish settlement truth from decision-time information.

### 10. Footer

Display:

- source event ticker;
- snapshot/evaluation timestamp;
- capture-health status;
- strategy id;
- short reminder: `Read-only immutable as-of snapshots · no order controls`.

Do not display raw local filesystem paths.

## Responsive and accessibility requirements

Match the approved responsive behavior:

- wide desktop: three columns with full-width market table;
- medium: left/center primary content and right cards wrapping below;
- mobile: one column, model cards in two columns when practical, compact market rows;
- no horizontal page overflow at 375px viewport width;
- keyboard-operable slider, event selector, refresh control, and pause control;
- visible focus indicators;
- sufficient color contrast;
- do not rely on color alone for pass/warn/block status;
- semantic headings, table markup, labels, and ARIA descriptions;
- honor `prefers-reduced-motion`;
- honor system dark mode; the approved dark design is primary, but do not make text unreadable under browser color adjustments.

## Frontend behavior

- Poll the latest snapshot every two seconds by default.
- Stop unnecessary polling while the page is hidden; refresh immediately when visible again.
- Use ETag/revision handling.
- If the API becomes unavailable, keep the last valid snapshot visible, mark it stale, and show the failure timestamp.
- Never clear a warning merely because a later request failed.
- Never compute trade eligibility from table values in JavaScript.
- Never infer missing data.
- Format prices as cents where appropriate and preserve exact values in accessible labels/tooltips.
- Format all visible operational times in `America/Los_Angeles`, while retaining UTC in machine-readable attributes.
- Escape all text from data sources before rendering.
- Use a strict Content Security Policy compatible with the local assets.
- No remote fonts, scripts, styles, analytics, or telemetry.

## Replay fixture

Use the supplied July 7 prototype data only as a deterministic development/test fixture.

Create a fixture such as:

```text
tests/fixtures/signal_room_july7_replay.json
```

The fixture may reproduce the approved screenshot states, including:

- the 73–74°F settled bracket;
- early NBM lower-temperature signal;
- spread warnings/blocks;
- strict as-of warning examples;
- unavailable calibrated probabilities;
- order depth unavailable;
- live execution disabled.

The production dashboard must never silently fall back to this fixture. Sample mode must require an explicit test/dev flag and display a prominent historical/sample banner.

## Tests

Use the repository's existing test tools. Add dependencies only when necessary and isolate them to dev/dashboard extras.

### Backend tests

Test at least:

1. Snapshot schema serialization.
2. Exactly five canonical model slots and fixed display order.
3. Unknown/research models are excluded.
4. Monetary values remain exact strings/fixed point through the API.
5. Missing values remain null and display as unavailable.
6. Timeline results are bounded, sorted, and never include records after an `as_of` cutoff.
7. No future source record is attached to an earlier evaluation.
8. Stale/invalid order book produces a blocking gate from persisted state.
9. NBM maturity and effective weight display correctly.
10. Final high and settled outcome are absent for an open live event.
11. Replay endpoint includes settlement truth only for a settled event.
12. API responses contain no secrets or raw credentials.
13. Dashboard package has no dependency on a live-order submission interface.
14. Health endpoint works with an empty database and reports `not_ready` rather than crashing.
15. ETag/revision returns `304` for an unchanged snapshot.

### Frontend/browser tests

Use existing browser tooling. If none exists, add one lightweight smoke path using Playwright or an equivalent dev-only tool.

Test at least:

1. Approved desktop structure renders.
2. 375px mobile view has no horizontal overflow.
3. Replay slider updates all cards and chart from the selected persisted checkpoint.
4. Live refresh updates from a newer revision.
5. API failure preserves the last snapshot and marks it stale.
6. Calibration-unavailable state never shows a fabricated probability or ROI.
7. Focus row follows the persisted candidate, not settlement outcome.
8. Final-high reference appears only for settled replay.
9. Model status is understandable without color.
10. No external network request is made by the page.
11. No order-control element or mutating HTTP method is present.
12. Keyboard focus and labels work for all controls.

### Safety regression

Add a test that imports/constructs the dashboard application in an environment with no exchange credentials and no order client. It must still start and render read-only state.

Add a repository-level assertion, where practical, that the dashboard route set contains only `GET`/`HEAD` methods.

## Acceptance criteria

The task is complete only when all of the following are true:

- The production UI visibly matches Prototype A's information hierarchy and design language.
- It reads current persisted strategy data rather than embedded demo data.
- It uses exactly the five approved models.
- It displays actual persisted decision reason codes and gate states.
- It never reimplements strategy math in the browser.
- It never exposes or reaches order submission.
- It supports live read-only monitoring and historical replay.
- It displays missing or uncalibrated values honestly.
- It works at desktop and mobile widths.
- It makes no external asset requests.
- Existing CLI behavior and tests remain intact.
- New unit, integration, API, and UI smoke tests pass.
- Live, canary, and taker execution remain disabled.

## Required deliverables

Produce:

1. Reuse mapping:

   ```text
   docs/klax_signal_room_repo_mapping.md
   ```

2. Operator documentation:

   ```text
   docs/klax_signal_room.md
   ```

   Include install command, dashboard CLI usage, data source, refresh semantics, replay usage, field definitions, known limitations, and safety boundary.

3. Implementation code and templates/assets.
4. Any minimal database view/index/migration needed for efficient read access.
5. API schema models.
6. July 7 deterministic fixture.
7. Backend and browser tests.
8. A short implementation report:

   ```text
   docs/klax_signal_room_implementation_report.md
   ```

   Include files changed, reuse decisions, commands run, test results, screenshots, and deviations.

9. Two screenshots produced from the implemented UI:
   - desktop, approximately 1440px wide;
   - mobile, approximately 375px wide.

## Implementation sequence

Follow this order:

### UI0 — Repository mapping

- inspect architecture;
- create mapping document;
- record baseline tests;
- no behavior changes.

### UI1 — Read model and repository

- define frontend-facing snapshot/timeline contracts;
- implement queries against existing immutable records;
- add required indexes or read-only views;
- add backend tests.

### UI2 — Read-only service and CLI

- add local server command;
- implement health/events/snapshot/timeline/capture-health endpoints;
- add ETag/revision behavior;
- prove no order dependency.

### UI3 — Productionize Prototype A

- split approved HTML into maintainable template/static assets;
- bind to API data;
- preserve design hierarchy and responsive behavior;
- implement live/replay controls;
- add honest unavailable/stale states.

### UI4 — Replay and fixture

- add explicit dev/test fixture adapter;
- reproduce July 7 checkpoints;
- ensure settlement truth is isolated from as-of decision data.

### UI5 — Validation and documentation

- run all existing and new tests;
- run linter/type checker used by the repository;
- capture desktop/mobile screenshots;
- complete operator docs and implementation report.

## Commands to report at completion

Report the exact commands and results for:

```powershell
python -m pytest -q
python -m ruff check src tests
```

Also report the dashboard-specific test command and the command used to launch it, for example:

```powershell
python -m pip install -e ".[dashboard]"
kalshi-weather strategy-dashboard --host 127.0.0.1 --port 8765 --event auto --mode live
```

Do not enable live trading, canary, taker execution, or order submission. Stop after the read-only dashboard is implemented, tested, and documented.
