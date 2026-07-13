# Backend contract and live/replay wiring

## Canonical data path

```text
weather, observation, market, and book collectors
    -> atomically committed source state
    -> current five-model StrategyEvaluationService
    -> immutable evaluation header and normalized child rows
    -> explainability snapshot serializer
    -> live/replay API
    -> Command Center and Probability Lab
```

The Probability Lab is not a strategy engine. It is a read-only projection of one immutable evaluation.

## Canonical payload

Use:

```text
contracts/explainability_snapshot.schema.json
```

The serializer should adapt existing repository models to this contract rather than introduce a duplicate persistence model when a correct one already exists.

## Recommended routes

```http
GET /api/strategy/current/events/{event_ticker}/explainability/latest
GET /api/strategy/current/events/{event_ticker}/explainability?evaluation_id={evaluation_id}
GET /api/strategy/current/events/{event_ticker}/evaluations?limit=500
```

Optional, only if the repository already uses server-sent events or WebSockets:

```http
GET /api/strategy/current/events/{event_ticker}/explainability/stream
```

A bounded polling loop is acceptable when it matches the existing architecture.

## Atomic snapshot rules

The serializer must read one evaluation revision and all children under a transaction or snapshot. It must reject incomplete internal joins. Never silently use the newest child row from another evaluation.

Every response must expose:

```text
evaluationId
strategyId
strategyConfigHash
evaluatedAt
eventTicker
targetDate
mode
analysisState
executionState
finalReasonCode
```

## Strict as-of rule

Every source value used in an evaluation must satisfy:

```text
source_available_at <= evaluated_at
received_at <= evaluated_at
```

No nearest-time future join is permitted.

## Live behavior

1. Resolve the current event and optional next-day event through existing event discovery.
2. Request the latest immutable explainability snapshot.
3. Display the response immediately, including partial and blocked states.
4. Poll or subscribe for a newer evaluation ID.
5. Replace the entire in-memory snapshot only after a complete valid payload is received.
6. Preserve the selected contract, side, and model where those keys still exist.
7. Show connection age and stale state.
8. Never patch individual fields from a newer response into an older snapshot.

## Replay behavior

1. Load a bounded evaluation index for the event.
2. Select an immutable evaluation by ID.
3. Request the evaluation-specific endpoint.
4. Freeze the selected evaluation until the user changes it.
5. Label the screen as replay and show its timestamp.
6. Do not automatically jump to live mode.

## Partial and blocked behavior

### Data blocked

Show live source and error information when the evaluator cannot form a valid state.

Examples:

```text
NO_TRADE_SOURCE_FROM_FUTURE
NO_TRADE_UNVERIFIED_SETTLEMENT_RULES
NO_TRADE_MODEL_FEEDS_INSUFFICIENT
```

### Analysis partial

Show live model states, calibration counts, outcome map, and market prices when probabilities are not yet calibrated.

Example:

```text
NO_TRADE_PROBABILITY_UNCALIBRATED
```

### Analysis ready, execution blocked

Show full distributions, probabilities, equations, and quote-based economics even when sequence-valid depth is unavailable.

Example:

```text
NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE
```

Label quote-only economics as counterfactual, not executable.

### Analysis ready, no trade

Show the full calculation and legitimate no-trade reason, such as:

```text
NO_TRADE_INSUFFICIENT_EDGE
NO_TRADE_MODEL_DISAGREEMENT
NO_TRADE_REGIME_DRIFT
NO_TRADE_PORTFOLIO_RISK
```

### Shadow candidate

Show the qualifying shadow candidate and sizing output, but no order action.

## Server-provided price sensitivity

The backend must enumerate the permitted price grid using the exact quantity, fee role, fee schedule, series multiplier, and execution-cost assumptions. Return rows such as:

```json
{
  "price": 0.37,
  "requiredProbability": 0.4421
}
```

The browser plots these rows. It does not approximate the fee formula.

## Backend-provided equation trace

Emit one row per meaningful calculation step:

```json
{
  "equationId": "raw_live_state",
  "label": "Observed floor plus remaining forecast",
  "scope": {"modelKey": "nbm"},
  "formula": "X_m = max(O, R_m)",
  "substitutedExpression": "max(71.6°F, 73.2°F)",
  "result": 73.2,
  "units": "°F",
  "status": "available",
  "missingInputs": []
}
```

The frontend selects and displays rows by scope. It does not reconstruct substitutions from unrelated fields.

## Security

- local read-only application API;
- no create/cancel/replace order route;
- no API credentials sent to the browser;
- no external analytics or telemetry;
- no fixture fallback in live mode;
- no browser-side strategy calculations;
- `orderSubmissionReachable` remains false.
