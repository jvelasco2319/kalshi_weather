# Acceptance tests

Codex must implement or run equivalent tests using the repository's current test framework.

## A. Package and fixture verification

```powershell
python implementation\klax_probability_lab_live_audit\reference\verify_package.py
```

Expected: exit code 0.

## B. Schema and invariant tests

1. The test fixture validates against the JSON Schema.
2. A real persisted evaluation validates against the JSON Schema.
3. Exactly five model rows exist in canonical order or stable key set.
4. Every nonempty scenario-temperature array has an equally sized weight array.
5. Every nonempty scenario-weight array sums to approximately one.
6. Per-model posterior mean Yes probabilities sum to approximately one across verified exhaustive outcomes.
7. Mixture posterior mean Yes probabilities sum to approximately one.
8. `pTradeYes <= min(mixtureLowerBoundYes, weightedComponentLowerBoundYes)`.
9. `pTradeNo <= min(mixtureLowerBoundNo, weightedComponentLowerBoundNo)`.
10. The outcome map is verified, ordered, exhaustive, and non-overlapping.
11. Every source availability/receipt timestamp used by the evaluation is at or before `evaluatedAt`.
12. Price-sensitivity rows use the configured price grid and are monotonic in required probability except where documented fee rounding creates equal steps.

## C. API tests

1. Latest endpoint returns the latest committed evaluation ID.
2. Evaluation-specific endpoint returns a stable byte-equivalent or semantically equivalent immutable snapshot on repeated reads.
3. A missing event/evaluation returns 404.
4. A broken internal join returns repository-standard 409/invalid-evaluation response.
5. Evaluator outage returns 503.
6. No error path returns the sample fixture.
7. Partial evaluations include exact reason codes and null/empty unavailable fields.
8. Today and next-day event routes work.
9. Evaluation index is bounded and sorted.
10. The Command Center and Probability Lab can request the same evaluation ID.

## D. UI component tests

1. Header and controls render.
2. Six hero cards render.
3. Distribution chart renders all five explicit model slots and mixture.
4. Observed-high and bracket boundaries render.
5. Model contribution ledger renders.
6. Probability funnel renders.
7. Equation trace renders and changes with selected model.
8. Bracket matrix renders all verified outcomes.
9. Yes/No view switch uses backend-provided values.
10. Market-versus-weather chart renders.
11. Price-sensitivity chart uses backend rows.
12. Calculation/data-health panel renders all gates.
13. Partial state renders meaningful values and missing reasons.
14. Book-blocked state still renders probability analysis.
15. Contract, side, model, event, and evaluation controls work.
16. Live mode updates only on a complete newer evaluation ID.
17. Replay mode remains frozen.
18. Fixture mode is visibly labeled and inaccessible as an automatic live fallback.

## E. Browser strategy-math prohibition

The production UI bundle must not independently implement:

```text
BetaPPF or beta quantiles
Dirichlet smoothing
model reliability weighting
family caps
NBM maturity weighting
fee formula
required probability formula
expected value formula
ROI formula
maximum price search
Kelly sizing
trade eligibility
final decision reason
```

Presentational differences such as `pSafe - requiredProbability` may be displayed but may not drive any state. Prefer backend-provided edge.

Add a static test or code-review check for prohibited strategy functions in the shipped UI assets.

## F. Safety tests

1. Probability Lab has no order button.
2. Probability Lab routes have no write method.
3. UI service cannot import the order submission client.
4. Live, canary, and taker flags remain false.
5. `orderSubmissionReachable` is false.
6. No API credential or secret appears in HTML, JavaScript, logs, screenshots, or network captures.
7. No external analytics, font, script, or telemetry request occurs.

## G. Visual and responsive tests

Capture and inspect:

```text
1440x1000 live desktop
390x844 live mobile
1440x1000 replay desktop
390x844 partial-state mobile
```

Confirm:

- no horizontal overflow;
- no clipped chart labels;
- controls remain usable;
- table/matrix has a deliberate scroll container where needed;
- colors are not the only status signal;
- evaluation ID and mode remain visible;
- all approved sections are present.

## H. Five-minute live test

Start the normal shadow runtime and Probability Lab. Keep it running for at least five minutes.

Pass conditions:

- a real current or next-day event is selected;
- at least one immutable evaluation is loaded;
- all five model slots show explicit status;
- evaluation age updates;
- a new evaluation atomically refreshes the page when available;
- no fixture values appear;
- no browser console errors;
- no external requests;
- no real order path is reachable;
- final state and reason code are shown even when no trade is possible.
