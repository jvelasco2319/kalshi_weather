# Decision and scope

## Decision

Implement stage-adaptive model weights in the current shadow strategy.

The strategy keeps the same five model-specific probability distributions and changes only their mixture influence. The primary shadow weighting mode becomes:

```text
stage prior × walk-forward reliability multiplier
    -> availability and maturity gates
    -> individual and GFS-family caps
    -> normalized effective weights
```

## What this does not change

- model fetchers and source variants;
- remaining-window plus observed-high state;
- model-specific residual and bias correction;
- physical and settlement scenarios;
- per-model posterior probabilities;
- conservative probability bounds;
- exact fee and ROI equations;
- model-spread, drift, book, and portfolio gates;
- shadow-only safety.

## Why not hard switch models

Historical raw leadership varied by stage, but a hard “GFS early, Seamless at 11, NAM at 2” switch is too brittle. The weighting layer therefore changes priors softly and lets prior settled-date probability performance make bounded adjustments.

## Required counterfactuals

Every evaluation must preserve:

1. `fixed_baseline` — the current fixed prior;
2. `stage_prior_only` — stage priors with caps but no reliability adaptation;
3. `stage_reliability` — stage priors plus shrunk walk-forward log-loss adaptation.

Only one primary shadow decision is emitted. Counterfactual modes are analysis outputs, not extra orders or positions.
