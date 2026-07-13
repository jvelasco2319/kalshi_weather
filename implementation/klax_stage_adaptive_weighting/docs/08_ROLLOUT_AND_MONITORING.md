# Rollout and monitoring

## Shadow rollout

Activate `stage_reliability` as the primary **shadow** weighting mode while preserving the two counterfactual modes.

Use explicit readiness:

- `PRIOR_ONLY` — stage history below minimum; stage prior is used;
- `PARTIAL` — some eligible models have reliability history and others use priors;
- `READY` — all positively weighted models meet configured reliability history;
- `BLOCKED` — eligibility or cap configuration prevents a valid mixture.

## What to monitor

By stage and model:

- effective weight;
- stage log loss and n-eff;
- calibration reliability;
- weight volatility from one settled date to the next;
- GFS-family cap frequency;
- NBM cap frequency;
- model availability and staleness;
- fixed versus adaptive pTrade differences;
- candidate changes caused only by weighting.

## Promotion gate

Do not enable canary or live trading from this package. Before any future promotion, require a reviewed chronological comparison showing that stage-adaptive weighting is at least as well calibrated as the fixed baseline and does not create unstable concentration or materially worse tail risk.

Suggested minimum evidence:

- at least 30 completed state-consistent forecast dates;
- preferably 60 joined market dates;
- date-clustered uncertainty;
- no material safety regression;
- explicit human approval.
