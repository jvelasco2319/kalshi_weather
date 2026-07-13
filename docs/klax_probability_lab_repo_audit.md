# KLAX Probability Lab Repo Audit

Package verifier: `python implementation\klax_probability_lab_live_audit\reference\verify_package.py` passes.

## Reuse Map

| Capability | Existing path or symbol | Current behavior | Smallest change | Test coverage |
| --- | --- | --- | --- | --- |
| HTTP app shell | `src/kalshi_weather/signal_room/app.py` | FastAPI app with local static assets and read-only routes | Add Probability Lab route and canonical explainability routes | API and UI route tests |
| Event discovery | `SignalRoomService.list_events` | Lists current-strategy and validation-journal KLAX events | Reuse for lab event control | API tests |
| Immutable live snapshot | `SignalRoomService.latest_snapshot` | Builds a complete snapshot from current tables or validation journal | Add canonical serializer over one snapshot | Schema and invariant tests |
| Historical replay | `SignalRoomService.timeline` and repository validation timeline | Bounded timeline by target date | Add evaluation index with `evaluationId` and evaluation-specific lookup | Replay API tests |
| Five model slots | `SignalRoomSnapshot.models` | Exactly five canonical slots enforced by Pydantic | Map to canonical model objects with explicit unavailable states | Contract tests |
| Probability payload | `evaluate_validation_snapshot().probability_lab` | Provides weights, per-model bracket probabilities, mixture rows, funnel, equations, and sensitivity in a local shape | Adapt to canonical schema and add scenario support | Schema and UI tests |
| Market/economics | `MarketRow` plus `strategy_current.economics` | Quote-only shadow economics are generated server-side | Serialize per-side economics rows and server price-sensitivity rows | Economics invariant tests |
| Gates and safety | `GateState`, `ReadinessState`, `StrategyState` | Read-only flags are false and gates are visible | Expand canonical gate/capture-health projection | Safety tests |
| Existing Command Center UI | `templates/index.html`, `static/signal_room.js/css` | Shows live snapshot and compact tabbed lab | Add navigation and evaluation ID display | Frontend tests |
| Approved Probability Lab UI | `implementation/klax_probability_lab_live_audit/ui_reference/approved_probability_lab_exact.html` | Package reference only | Add a dedicated production route using the same panel order with live data | Browser and static UI tests |

## Audit Findings

- Probability Lab tab exists in the Command Center, but it is compact and not the approved full-page Probability Lab.
- Latest live explainability endpoint exists at `/api/v1/signal-room/events/{event}/explainability`, but it is not the canonical package schema.
- Historical snapshots can be selected by `as_of`; evaluation-ID lookup and an evaluation index are missing.
- Model weights, bracket probabilities, quote-only economics, and gates are present for validation-journal shadow evaluations.
- Scenario temperatures and weights are not persisted as first-class rows; the implementation must serialize backend-provided launch residual scenario support from the same immutable evaluation snapshot.
- Current UI uses backend probability rows for charts, but it does not render all approved panels at once.
- No order button or mutating Signal Room route exists. Signal Room Python code does not import order-submission clients.

## Required Changes

- Add a canonical explainability serializer in the Signal Room service layer.
- Add `/api/strategy/current/events/{event_ticker}/explainability/latest`.
- Add `/api/strategy/current/events/{event_ticker}/explainability?evaluation_id=...`.
- Add `/api/strategy/current/events/{event_ticker}/evaluations`.
- Add `/strategy/probability-lab` as the full approved Probability Lab page.
- Add local CSS/JS assets for the Probability Lab, with no fixture fallback and no browser-side strategy math.
- Add tests for schema validation, invariants, API behavior, UI route availability, static no-math checks, and safety flags.

## Database And Configuration

No migration is required for this repair. The canonical serializer reads the same immutable validation snapshot used by the current Signal Room response. Missing future persistence fields are emitted as `null`, empty arrays, or explicit blocked/unavailable states.
