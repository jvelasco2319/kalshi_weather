# Risk and Portfolio

## Disagreement

Use raw state-consistent model values:

\[
spread=\max_mX_m-\min_mX_m
\]

Initial policy:

| Spread | Hurdle | Size |
|---|---:|---:|
| below 3°F | base | normal |
| 3°F to below 4°F | +3 percentage points | ×0.50 |
| 4°F or more | no new canary/live trade | shadow counterfactual only |

The probability mixture already preserves disagreement; the spread rule is an additional deployment-risk control.

## Regime drift

Compare the most recent 10-date residual median with the 45-date recency-weighted median. Flag drift when:

- absolute difference is at least 1.5°F; or
- signs reverse and both magnitudes are at least 1.0°F.

Initial response: add three percentage points to the ROI hurdle and halve size.

## Kelly sizing

With all-in cost per contract `k` and conservative probability `q`:

\[
f_{full}=\max(0,\frac{q-k}{1-k})
\]

\[
f_{used}=0.25\times f_{full}\times risk\ multipliers
\]

Initial caps:

- 1.5% of bankroll across the target date;
- 0.75% per contract;
- 0.25% during a future canary;
- 2% daily-loss kill threshold;
- one new entry per target date in canary.

## Event-level scenario matrix

All positions and open/pending orders for the mutually exclusive temperature brackets belong to one event portfolio. Before accepting a candidate, calculate P&L under every possible settlement bracket. Reject any order that breaches a cap in any outcome.

Evaluate both Yes and No candidates and choose at most one best incremental event-level action per coherent evaluation.
