# Integration and persistence

## Reuse-first integration

Prefer extending the current evaluator's weight provider interface, for example:

```text
ModelWeightProvider
  fixed_baseline(...)
  stage_prior_only(...)
  stage_reliability(...)
```

Suggested names are not mandatory. Reuse the repository's actual abstractions.

## Immutable evaluation linkage

One evaluation ID must atomically link:

- source model states;
- model probability distributions;
- weighting decomposition for all three modes;
- primary mixture and counterfactual mixtures;
- economics and gates;
- final shadow decision.

Do not persist a mutable “current weights” table without an evaluation-linked history.

## Required readiness/status values

Suggested stable statuses:

```text
WEIGHTING_FIXED_BASELINE
WEIGHTING_STAGE_PRIOR_ONLY
WEIGHTING_STAGE_RELIABILITY_PARTIAL
WEIGHTING_STAGE_RELIABILITY_READY
WEIGHTING_BLOCKED_INSUFFICIENT_MODELS
WEIGHTING_BLOCKED_INSUFFICIENT_FAMILIES
WEIGHTING_BLOCKED_CAP_CONFIGURATION
```

## Counterfactual discipline

Counterfactual modes may calculate their own mixture probabilities and economics. They must not:

- create additional orders;
- create duplicate paper positions;
- change portfolio exposure;
- alter the primary decision reason;
- change live safety flags.

## Configuration versioning

Persist:

```text
strategy_config_hash
weighting_revision
weighting_config_hash
code_revision
```

A weight snapshot without these identifiers is not reproducible.
