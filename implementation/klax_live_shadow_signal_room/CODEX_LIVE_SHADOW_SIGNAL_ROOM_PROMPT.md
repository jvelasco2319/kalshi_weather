# Codex Prompt — Implement the Live KLAX Five-Model Shadow Bot and Wire Prototype A End to End

You are working in the existing repository:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

The user has **not** implemented v1, v2, or v3. Implement only the current system described by this package. The finished result must be runnable now as a live, read-only, shadow system for the current and next KLAX high-temperature markets.

This prompt supersedes the earlier UI-only prompt. The earlier Prototype A was a visual reference; it was not sufficient by itself because it only read persisted snapshots. Your task now is to implement the missing continuous runtime and connect all layers:

```text
live data collectors
    -> normalized immutable events
    -> state-consistent five-model algorithm
    -> conservative contract probabilities and economics
    -> immutable shadow decisions
    -> live Prototype A dashboard
```

The browser must never connect directly to Kalshi, Open-Meteo, NOAA, Herbie, or Aviation Weather. All external data is collected, normalized, evaluated, and persisted by the Python backend. The UI displays backend-produced state and does not reproduce trading math in JavaScript.

---

## 1. Authority

Use this authority order:

1. `strategy_spec/` in this package for the current five-model strategy, risk, probability, persistence, fee, replay, and safety rules.
2. This prompt for the live runtime, current/tomorrow event orchestration, and end-to-end UI wiring.
3. `ui_reference/approved_prototype_a.html` and `.png` for the visual design.
4. Existing repository behavior where it does not conflict with the first three items.

Do not install or revive older implementation packages. Do not silently retain an old point-estimate trading rule.

The strategy ID remains:

```text
klax-current-five-model-2026-07-11
```

The strategy model set is exactly:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

All other models are excluded from strategy votes, spread, probabilities, weights, and trade decisions. Existing legacy collectors may remain for other commands, but their outputs must not enter this strategy.

---

## 2. Required operating result

The implementation is complete only when the user can run one command and, without placing orders, see the current and/or next target-date market update continuously in Prototype A.

Preferred command, adapted to the existing CLI conventions:

```powershell
kalshi-weather strategy-shadow-run `
  --target-date auto `
  --include-next-day `
  --serve-dashboard `
  --host 127.0.0.1 `
  --port 8765 `
  --open-browser
```

If extending `strategy-shadow-run` would damage an existing contract, add one convenience command:

```powershell
kalshi-weather strategy-live-stack --targets today,tomorrow --open-browser
```

The convenience command must use the same underlying worker and dashboard services as the separate commands. Do not build a second algorithm path merely for the combined command.

Within a normal healthy startup, the dashboard should begin showing available information without waiting for probability calibration:

- discovered KLAX market events and contracts;
- current target-date and next target-date tabs when available;
- current bid/ask and market leader;
- five model slots and each latest state-consistent estimate;
- KLAX observations and observed high so far for the target date;
- feed ages and data-health gates;
- model spread;
- residual-history and NBM maturity counts;
- conservative probabilities and shadow economics when the history gates pass;
- otherwise an honest `DATA INCOMPLETE` decision with the exact missing requirement.

A missing probability library must not prevent the user from viewing live models and prices. It must prevent a trade candidate.

---

## 3. Hard safety boundary

This implementation is a live **data and shadow-decision** system, not a live trading system.

These values are immutable defaults for this task:

```text
mode = shadow
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
order_submission_reachable = false
```

Requirements:

- Use a separate `ShadowOrderSink` or equivalent non-submitting dependency.
- The live stack must start and function when no order client is configured.
- The dashboard process and routes must not import an order-submission interface.
- Do not add order buttons or mutating dashboard endpoints.
- Do not enable order creation, cancellation, replacement, or account actions.
- Do not use a runtime `if shadow:` around a fully reachable live order client as the only safety control.
- Add a test proving that no order method can be reached from the live stack.

The system may use read-only Kalshi credentials for authenticated market-data streams. Never expose those credentials to the browser or logs.

---

## 4. Start with repository mapping, not a parallel rewrite

Before production edits:

1. Place this package under a non-imported directory such as:

   ```text
   implementation/klax_live_shadow_signal_room/
   ```

2. Verify the authoritative strategy specification:

   ```powershell
   $env:PYTHONPATH = "implementation\klax_live_shadow_signal_room\strategy_spec\reference"
   python implementation\klax_live_shadow_signal_room\strategy_spec\reference\verify_package.py
   ```

3. Run the existing baseline:

   ```powershell
   python -m pytest -q
   python -m ruff check src tests
   ```

4. Inspect at minimum:
   - the CLI and command registry;
   - current Open-Meteo model fetchers;
   - the existing NOAA/Herbie NBM fetcher;
   - KLAX/METAR observation code;
   - Kalshi REST and WebSocket clients;
   - event and market discovery code;
   - SQLite migrations and JSONL/raw-payload storage;
   - any event bus, scheduler, loop, daemon, or supervisor code;
   - current paper/shadow runtime;
   - existing HTTP server/templates/static assets;
   - current decision, reason-code, and capture-health records;
   - all order interfaces and safety tests.

5. Create:

   ```text
   docs/klax_live_stack_repo_mapping.md
   ```

For each responsibility below, record:

- existing module to reuse;
- smallest extension needed;
- new module only when no correct home exists;
- migration/config/test impact;
- exact reason code used when the responsibility is unavailable.

Do not duplicate a working HTTP client, exchange client, model fetcher, database, CLI, or algorithm module just to match suggested names.

---

## 5. Target runtime architecture

Reuse the repository architecture where possible. The logical responsibilities must be present even if the actual module names differ.

```text
LiveShadowSupervisor
  ├── EventDiscoveryWorker
  ├── MarketMetadataWorker
  ├── OrderBookWorker
  ├── PublicTradeWorker
  ├── OpenMeteoModelWorker x 4
  ├── NbmModelWorker
  ├── KlaxObservationWorker
  ├── RulesAndFeeWorker
  ├── StalenessWatchdog
  ├── EvaluationCoordinator
  ├── ShadowOrderSink
  └── SignalRoomReadServer
```

Every source payload is persisted before it is eligible to trigger an evaluation.

Recommended event flow:

```text
source response/message
  -> raw payload + hash
  -> normalized immutable event
  -> commit
  -> publish internal source-event notification
  -> deterministic debounce/coalescing
  -> one coherent as-of evaluation
  -> immutable DecisionState and dashboard snapshot revision
  -> UI revision notification
```

Use one existing journal/database as the source of truth. Do not create a separate UI database. SQLite WAL mode is acceptable if it matches existing conventions and supports one writer plus dashboard readers.

The worker and dashboard may run in one supervised process for convenience, but preserve clean internal boundaries so either can also run separately.

---

## 6. Automatically discover today and tomorrow’s KLAX events

Do not hardcode event tickers or dates.

Use the Kalshi events/markets API through the existing client. Search the `KXHIGHLAX` series for:

- open events;
- unopened/upcoming events;
- the nearest event for the current `America/Los_Angeles` target date;
- the nearest event for the next local target date.

Implementation rules:

1. Use series metadata, strike date, occurrence date, open/close times, and event/market metadata as the primary source.
2. Ticker parsing may be a verified fallback only.
3. Exhaust event pagination.
4. Request nested markets when supported; otherwise fetch markets by event ticker.
5. Validate that the event is mutually exclusive and that the contract set is non-overlapping and exhaustive under the verified market rules.
6. Refresh discovery every 30 seconds by default and immediately on lifecycle messages when available.
7. Auto-subscribe new market tickers when the next event opens.
8. Auto-unsubscribe and retain historical display when an event settles or ages out.
9. Never mix target dates in one evaluation.
10. If no current or next event exists, keep the dashboard running and show `NO_MARKET_AVAILABLE` rather than crashing.

Define `--target-date auto` as:

```text
select the nearest open KLAX target-date event;
if none is open, select the nearest unopened event;
when --include-next-day is set, also monitor the next local target date.
```

The dashboard should provide event tabs labeled `TODAY`, `TOMORROW`, or the actual date. It should default to the nearest open event.

---

## 7. Implement/reuse the live source collectors

### 7.1 Four Open-Meteo paths

Use the repository’s existing Open-Meteo client for:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
```

Requirements:

- Fetch complete hourly `temperature_2m` paths covering every monitored target-date station window.
- Persist model source key, provider, model/run identifier, run time when known, source availability time, receipt time, valid time, units, coordinates/grid metadata, and raw payload hash.
- Check for new data every five minutes by default; deduplicate identical runs and payloads.
- Apply exponential backoff with jitter on failure.
- One failed model must not stop the other collectors.
- Do not use Best Match, GFS Global, HRRR, RAP, AIFS, or a weighted blend as a substitute.
- `nam_conus` may map to the canonical NAM signal, but never create a sixth vote.

If the provider does not expose a trustworthy run-availability timestamp, use the receipt timestamp conservatively for live availability and document the limitation. Do not backdate availability.

### 7.2 NBM

Reuse the existing NOAA/Herbie NBM client and canonical source history:

```text
noaa_herbie:nbm
```

Requirements:

- Persist the full target-date 2 m temperature path, not only one scalar maximum.
- Check for a newly available run at least every five minutes, using repository/provider conventions to avoid redundant downloads.
- Preserve NBM as a distinct provider/source variant.
- Do not silently replace it with Open-Meteo NBM or another provider.
- If unavailable, display NBM as missing/provisional and enforce the current maturity rules.
- NBM begins contributing to tradable probability only according to the maturity schedule in `strategy_spec/config/strategy_current.shadow.yaml`.

### 7.3 KLAX observations

Reuse the existing KLAX METAR/observation client and parser. Poll once per minute by default, or follow the existing source cadence if it is more appropriate.

Persist:

- raw report;
- station identifier;
- observation time;
- source availability/receipt time;
- decoded whole-degree temperature;
- decimal T-group temperature when present;
- accepted/rejected quality status and reason;
- deduplication key and payload hash.

Requirements:

- Only accepted observations from KLAX enter the observed maximum.
- The running maximum uses observations within the verified target station day and available by the evaluation cutoff.
- Special observations may update the state between routine observations.
- Missing or malformed observations produce explicit health warnings, not a zero temperature.
- Respect source rate limits and cache behavior.

### 7.4 Kalshi market metadata and top of book

At startup and on event changes, persist:

- event metadata;
- market metadata;
- contract brackets/strikes;
- open/close/expiration times;
- settlement sources and rules;
- fee override/multiplier fields;
- price increments/ranges;
- current best prices and sizes when supplied.

Never infer settlement brackets solely from display text when structured strike fields exist.

### 7.5 Kalshi order-book stream

Reuse the authenticated Kalshi WebSocket client. For every contract in each monitored event:

1. subscribe by market ticker;
2. require an initial order-book snapshot;
3. apply incremental deltas in sequence;
4. persist at least ten levels on the native Yes and No sides;
5. preserve fixed-point prices and quantities;
6. record exchange timestamp, receipt timestamp, subscription ID, and sequence;
7. invalidate the book on any sequence gap;
8. request/resubscribe a new snapshot after invalidation;
9. block trade candidates while the book is invalid or stale.

If read-only WebSocket credentials are unavailable, use existing REST market/order-book polling as an **observe-only fallback** so the UI can still show prices. In fallback mode:

```text
orderbook_depth_available = false
orderbook_sequence_valid = false
candidate gate = blocked
reason = NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE
```

Do not pretend REST/candle top-of-book is a sequence-valid depth book.

### 7.6 Public trades

Use the public-trade stream if the existing client supports it; otherwise use cursor-complete REST polling.

Persist `count_fp`, fixed-point prices, trade ID, ticker, timestamp, block flag, cursor/page audit, and payload hash.

A nonempty pull with missing/nonpositive `count_fp`, unexhausted cursor pagination, or unresolved duplicate trade IDs fails capture health.

Trades enrich the UI and later execution analysis. They do not replace the order book for candidate eligibility.

### 7.7 Fees and settlement rules

Load and version the current fee and settlement metadata through the existing implementation. Revalidate at startup, on event changes, and periodically.

If fee treatment, fee multiplier, settlement source, target station day, rounding, or bracket mapping is unverified, display the market but block candidates with an exact reason code.

---

## 8. Correct state construction for both today and tomorrow

Use one pure function for historical replay and live inference.

For model `m`, target date `d`, evaluation time `t`, verified station-day start `S_d`, and station-day end `E_d`:

```text
remaining_window_start = max(t, S_d)
remaining_window_end   = E_d
```

This clarification is essential for tomorrow’s market: before the target day begins, do not include temperatures from the current calendar day merely because they are after the evaluation timestamp.

Select the latest complete model run for which:

```text
source_available_at <= t
received_at <= t
```

Then:

\[
R_{m,d,t}
=
\max_{\tau \in [\max(t,S_d),E_d]}
\widehat T_{m,d,t,\tau}
\]

Let the accepted observed target-day maximum be:

\[
O_{d,t}
=
\max\{T_{obs}: S_d \le observation\_time \le t,\ available\_at \le t\}
\]

If no target-day observation exists yet, `O` is null and must not be replaced with zero.

The comparable model live state is:

\[
X_{m,d,t}
=
\begin{cases}
\max(O_{d,t},R_{m,d,t}) & \text{if }O_{d,t}\text{ exists}\\
R_{m,d,t} & \text{otherwise}
\end{cases}
\]

Persist the exact forecast-path point IDs used to derive `R`.

Hard rules:

- A forecast maximum whose valid time has passed cannot remain in `R`.
- A future source record cannot enter an earlier evaluation.
- Do not use nearest-time joins.
- Do not interpolate a missing model state.
- Historical reconstruction must call the same pure state-builder function.
- Legacy full-day scalar maxima cannot satisfy the current calibration gate.

---

## 9. Implement the current five-model algorithm, not a UI approximation

Follow the exact equations, maturity, weighting, probability, fee, and risk rules under `strategy_spec/`.

At a minimum the backend must produce and persist:

- five `ModelLiveState` slots;
- model-specific residual history status;
- NBM maturity status;
- model-specific physical/settlement outcome distributions;
- prior and effective model weights;
- GFS family-cap application;
- mixture bracket probabilities;
- conservative Yes and No probabilities;
- comparable model spread;
- regime-drift state;
- active ROI hurdle and size multiplier;
- exact all-in cost, expected value, and expected ROI for every evaluated side;
- exact maximum acceptable price by enumerating the valid price grid;
- full event-outcome P&L matrix;
- selected best incremental shadow candidate or exact no-trade reason.

The UI must receive these persisted outputs. Do not recompute `p_safe`, ROI, model weights, or eligibility in JavaScript.

### Honest startup behavior before history is ready

The live system must still be useful on day one.

When state-consistent residual history is insufficient:

- continue collecting all five live states;
- display individual model values, spread, observed high, market prices, and data ages;
- display residual counts and the exact threshold still needed;
- calculate price-only quantities such as the required probability for the active ROI hurdle when fees/rules are verified;
- set `p_safe`, modeled ROI, and trade candidate to null/unavailable;
- emit `DATA_INCOMPLETE` with `NO_TRADE_PROBABILITY_UNCALIBRATED` or the repository-equivalent reason;
- never synthesize a probability from model vote count or nearest bracket.

A presentation-only raw model center may be shown if it is produced by the backend and clearly labeled `uncalibrated display only`. It may not feed the decision engine.

---

## 10. Event-driven evaluation coordinator

Reevaluate on any committed change to:

- discovered event/contract set;
- market status or rules;
- verified fee schedule;
- model run/path;
- accepted KLAX observation;
- observed maximum;
- order-book price/size/validity;
- public trade record when it affects capture status;
- positions/open shadow state;
- stale-data watchdog;
- strategy config version.

Use deterministic event coalescing, for example a configurable 250 ms debounce, so a burst of related updates creates one coherent evaluation while preserving all triggering source IDs.

Each evaluation must have:

```text
evaluation_id
event_ticker
target_date
evaluated_at_utc
strategy_id
config_hash
code_revision
trigger_event_ids
source_cutoff
source record IDs
book sequence and age
rules/fee versions
```

A decision must be reproducible after process restart.

When multiple events are monitored, maintain completely separate target-date contexts and decision streams.

---

## 11. Supervisor scheduling and resilience

Use existing scheduling/concurrency patterns. A reasonable default configuration is:

```text
event discovery:              30 seconds
market metadata refresh:      30 seconds
Open-Meteo new-run check:      5 minutes
NBM new-run check:             5 minutes
KLAX observation poll:        60 seconds
trade REST fallback poll:     10 seconds
rules/fee refresh:            15 minutes and event changes
book stale watchdog:           1 second
evaluation debounce:         250 milliseconds
dashboard revision delivery: <=2 seconds after decision commit
```

Make these configurable, bounded, and validated.

Requirements:

- Exponential backoff with jitter per source.
- One source failure does not terminate the supervisor.
- A fatal database error stops evaluation rather than continuing with unpersisted state.
- Graceful Ctrl+C shutdown.
- Restart recovery from the persisted journal.
- A process lock or lease prevents two writers from running the same event worker accidentally.
- Health status distinguishes `healthy`, `degraded`, `stale`, and `invalid`.
- The dashboard remains available during source degradation and preserves the last valid snapshot with a stale banner.

---

## 12. Wire Prototype A to real backend state

Productionize `ui_reference/approved_prototype_a.html` as the **KLAX Signal Room**.

The page must not contain embedded July 7 data in live mode. Any replay/demo fixture requires an explicit `--demo` or replay flag and a visible historical banner.

### Required live UI behavior

- Current/tomorrow event tabs.
- Live revision/freshness indicator.
- Latest evaluation timestamp in Pacific Time.
- Decision card: `TRADE CANDIDATE`, `SHADOW ONLY`, `NO TRADE`, or `DATA INCOMPLETE`.
- Exact persisted reason code and reason text.
- Five fixed model cards in this order:
  1. ECMWF IFS
  2. GFS 0.13°
  3. GFS Seamless
  4. NAM
  5. NBM
- State-consistent model path chart with honest gaps.
- Observed KLAX high-so-far line/value.
- Spread, active hurdle, market leader, and required probability.
- Feed ages and strict-as-of status.
- NBM maturity and residual counts.
- Order-book sequence/depth/capture health.
- Bracket table using live prices and backend-produced probabilities/economics.
- `Unavailable` rather than fabricated zeroes.
- No settled outcome or future final-high line for an open event.
- Read-only footer and explicit `order path disabled` status.

### Data delivery

Prefer a small versioned read API. Reuse existing conventions when present. Otherwise provide:

```text
GET /api/v1/signal-room/health
GET /api/v1/signal-room/events
GET /api/v1/signal-room/events/{event_ticker}/snapshot
GET /api/v1/signal-room/events/{event_ticker}/timeline
GET /api/v1/signal-room/events/{event_ticker}/capture-health
GET /api/v1/signal-room/stream
```

`/stream` may use server-sent events to announce new immutable snapshot revisions. The page must fall back to bounded ETag polling every two seconds if SSE is unavailable. Do not connect the browser directly to the exchange WebSocket.

Only `GET` and `HEAD` routes are permitted in this dashboard service.

### Browser safety

- Bind to `127.0.0.1` by default.
- Require an explicit `--allow-remote` flag for non-loopback binding.
- Use a strict Content Security Policy.
- No CDN, remote font, analytics, or telemetry.
- Escape all source text.
- Never return credentials, raw auth headers, unrestricted raw payloads, or filesystem paths.
- Keep last valid state visible during API failure and mark it stale.
- Do not compute trading math in the browser.

---

## 13. Dashboard snapshot contract

Adapt to existing domain names, but expose a stable read contract with at least:

```json
{
  "schema_version": "1",
  "revision": "monotonic-or-content revision",
  "generated_at": "ISO-8601 UTC",
  "event": {
    "ticker": "KXHIGHLAX-...",
    "target_date": "YYYY-MM-DD",
    "relative_day": "today|tomorrow|other",
    "status": "unopened|open|closed|settled",
    "market_open_at": "ISO or null",
    "market_close_at": "ISO or null",
    "station": "KLAX"
  },
  "strategy": {
    "strategy_id": "klax-current-five-model-2026-07-11",
    "mode": "shadow",
    "live_trading_enabled": false,
    "canary_enabled": false,
    "taker_enabled": false,
    "order_submission_reachable": false,
    "code_revision": "git sha",
    "config_hash": "hash"
  },
  "decision": {
    "evaluated_at": "ISO",
    "status": "TRADE_CANDIDATE|SHADOW_ONLY|NO_TRADE|DATA_INCOMPLETE",
    "reason_code": "stable code",
    "reason_text": "operator text",
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
    "observed_high_f": null,
    "active_roi_hurdle": 0.15,
    "risk_multiplier": null,
    "market_leader_bracket": null
  },
  "models": [],
  "gates": [],
  "capture_health": {},
  "market": []
}
```

Prices, quantities, fees, and bankroll values must be fixed-point strings. Missing values are null.

---

## 14. CLI commands and operator experience

Implement or extend these commands through the existing CLI:

### `strategy-doctor`

Read-only preflight. Check and report without exposing secrets:

- database writable/readable;
- current config valid;
- five model clients import/configure;
- event discovery reachable;
- KLAX observation source reachable;
- Kalshi read authentication available when required;
- dashboard dependencies installed;
- current and next event discovery result;
- latest historical residual counts;
- NBM maturity;
- live/canary/taker/order flags all false.

Exit nonzero only for failures that prevent the requested mode. For example, missing WebSocket credentials should be a degraded warning for observe-only dashboard mode but a blocker for executable-book candidate mode.

### `strategy-shadow-run`

Headless continuous collectors + algorithm + immutable shadow sink. Support:

```text
--target-date auto|YYYY-MM-DD
--include-next-day
--once
--until-local-time HH:MM
--strategy-config PATH
--journal-path PATH
--json-output
--serve-dashboard
--host
--port
--open-browser
```

### `strategy-dashboard`

Read-only dashboard against the existing journal. It must not start duplicate collectors unless an explicit `--start-worker` option delegates to the same supervisor.

### `strategy-status`

Report current/next events, latest model states/ages, observed high, book validity, residual counts, NBM maturity, current decision, last evaluation age, and deployment safety flags.

### `strategy-validate-capture`

Hard completeness checks over a target date or date range.

---

## 15. Example Windows quick start that must work after implementation

Document the exact repository-specific commands, but support this shape:

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather

python -m pip install -e ".[dashboard]"

kalshi-weather strategy-doctor --target-date auto --include-next-day

kalshi-weather strategy-shadow-run `
  --target-date auto `
  --include-next-day `
  --serve-dashboard `
  --host 127.0.0.1 `
  --port 8765 `
  --open-browser
```

Expected browser URL:

```text
http://127.0.0.1:8765
```

The command should print a compact live summary when each decision revision is committed:

```text
2026-07-12 09:31:05 PT | JUL12 | 5/5 feeds | observed 68.0F |
spread 2.1F | 73-74 ask 0.24 | p-safe unavailable |
DATA_INCOMPLETE NO_TRADE_PROBABILITY_UNCALIBRATED | dashboard :8765
```

Do not hardcode this sample date or values.

---

## 16. Persistence and migrations

Use additive migrations only. Reuse existing tables where semantically correct.

The live stack must persist enough to reproduce every dashboard revision and shadow decision:

- event/market/rule versions;
- fee versions;
- raw and normalized model path points;
- observations;
- order-book snapshots/deltas/validity intervals;
- public trades and pagination audits;
- capture manifests;
- model live states;
- residual/probability provenance;
- model weights;
- bracket probabilities;
- candidate economics/rejections;
- immutable decision states;
- dashboard revision pointer or deterministic view.

Add indexes for:

- latest record by event and source;
- strict as-of selection;
- latest decision by event;
- bounded timeline reads;
- order-book sequence lookup;
- target-date history counts.

Do not store derived values only in browser memory.

---

## 17. Tests and live smoke acceptance

Use existing test tools. Add only minimal optional dashboard dependencies.

### Repository compatibility

- Existing tests pass before and after.
- Existing recorder and paper commands retain their behavior.
- No duplicate model or Kalshi clients are introduced without a documented reason.

### Source tests

1. Event discovery selects current and next target dates correctly in Pacific Time.
2. Pagination is exhausted.
3. An unopened tomorrow event displays without being treated as open.
4. Each of the five model clients persists complete target-window paths.
5. Other model keys cannot enter the strategy registry.
6. NBM provider cannot silently switch.
7. METAR decimal temperature is preferred when valid and raw/whole values remain traceable.
8. A future source record is rejected.
9. Order-book snapshot/delta sequence is enforced.
10. REST fallback displays prices but blocks executable candidates.
11. `count_fp` is required and trade cursors are exhausted.

### State and algorithm tests

12. Tomorrow-before-midnight state uses `max(target_day_start, evaluated_at)` and excludes the current day.
13. A past forecast maximum cannot survive after its valid time.
14. Observed high floors today’s model state.
15. Historical and live paths use the same state-builder function.
16. Missing residual history displays live values but produces no `p_safe` or candidate.
17. Full calibrated outputs match the reference vectors in `strategy_spec/reference/`.
18. Yes and No probabilities/economics are evaluated separately.
19. Exact fee and maximum-price vectors pass.
20. Invalid rules, fees, book, data age, or history produces exact no-trade reasons.

### Supervisor tests

21. One source failure does not stop healthy tasks.
22. Duplicate worker lock prevents two writers.
23. Restart recovers from persisted state.
24. New source events produce one debounced coherent evaluation.
25. Separate target dates never share source state.
26. Ctrl+C shuts down cleanly.
27. No live order client is constructed.

### Dashboard tests

28. Prototype A is backed by live/replay API data, not embedded sample values.
29. Exactly five model slots appear in fixed order.
30. Current/tomorrow event tabs work.
31. A new decision revision updates within two seconds under normal local conditions.
32. API/SSE failure preserves the last snapshot and marks it stale.
33. Null probability displays `Unavailable`, not `0%`.
34. The browser never computes eligibility or ROI.
35. Open events do not expose future settlement outcomes.
36. There are no mutating routes or order controls.
37. 375 px layout has no horizontal page overflow.
38. No external asset request occurs.

### Network smoke test

Provide an explicit opt-in command, never part of ordinary unit tests:

```powershell
kalshi-weather strategy-live-smoke-test --target-date auto --include-next-day --duration-seconds 120
```

It must verify, without orders:

- event discovery returns a valid event or a clear no-market state;
- at least one market quote is observed when an event is open;
- all five model slots resolve to healthy/missing statuses;
- at least one KLAX observation or explicit source status is captured;
- at least one immutable decision revision is persisted;
- the dashboard snapshot is readable;
- live/canary/taker/order-submission flags remain false.

A model may legitimately be missing during the smoke window. The test should fail on silent absence, invalid timestamps, or crashes—not merely because a provider has no new run.

---

## 18. Required documentation and deliverables

Produce:

1. `docs/klax_live_stack_repo_mapping.md`
2. `docs/klax_live_stack_operator_runbook.md`
3. `docs/klax_live_stack_implementation_report.md`
4. Additive code and migrations
5. Validated runtime config example
6. Dashboard/API contracts
7. Unit/integration/browser tests
8. A 120-second read-only live smoke report
9. Desktop and mobile screenshots from the implemented live UI
10. Exact commands and test results

The operator runbook must explain:

- installation;
- credential/config expectations using the repository’s existing environment names;
- one-command startup;
- separate worker/dashboard startup;
- today/tomorrow selection;
- meaning of every decision state;
- what remains visible when calibration is incomplete;
- stale/degraded recovery;
- database location and backup;
- graceful shutdown;
- proof that no orders can be submitted.

---

## 19. Completion report format

At completion, report:

```text
Repository baseline:
Files/modules reused:
New files/modules and why:
Migrations applied:
Commands added/extended:
Current/tomorrow event discovery result:
Five source statuses:
Latest immutable decision status:
Dashboard URL:
Live smoke result:
Existing test result:
New test result:
Ruff/type-check result:
Desktop/mobile screenshot paths:
Order path reachability proof:
Known limitations:
```

Do not stop after rendering a page with fixture data. Do not declare completion until the live collectors, algorithm, persisted decision stream, and Prototype A are connected end to end.

Do not enable canary, taker, live trading, or order submission.
