# Replay, Shadow, and Promotion

## Replay order

1. Reconstruct source availability and live states.
2. Score each model distribution chronologically.
3. Calculate model weights from prior settled dates only.
4. Generate contract probabilities and trade candidates.
5. Join market state available at that time.
6. Apply execution assumptions and fees.
7. Settle the event portfolio.

## Required reports

Forecast report:

- MAE, median absolute error, bias, Brier score, and log loss;
- probability calibration by band;
- performance by market stage, season, spread, and drift;
- model-specific and mixture results;
- NBM incremental value after the four core signals are known.

Economic report:

- net P&L and aggregate ROI;
- ROI by predicted-safe-ROI decile;
- opportunity count and capital deployed;
- maker fill rate and unfilled opportunity cost;
- post-fill adverse selection;
- slippage and fees;
- maximum drawdown and losing streak;
- date-clustered confidence intervals.

## Minimum evidence

- At least 30 settled dates for forecast probability use.
- Prefer 60 complete joined market days before canary review.
- NBM requires its maturity schedule and cannot bypass it because of July 7.

## Promotion decision

The output is one of:

```text
NO_GO_DATA_INCOMPLETE
NO_GO_PROBABILITY_UNCALIBRATED
NO_GO_EXECUTION_NOT_VALIDATED
NO_GO_RETURN_TARGET_NOT_SUPPORTED
READY_FOR_HUMAN_CANARY_REVIEW
```

No automatic promotion is allowed.
