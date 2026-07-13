# Acceptance tests

## Stage classification

- previous day 07:00 PT -> `pre_target`;
- target day 01:59:59 -> `pre_target`;
- 02:00 -> `target_02_10`;
- 11:00 -> `target_11_13`;
- 14:00 -> `target_14_16`;
- 17:00 -> `target_17_close`;
- DST-aware datetimes classify correctly;
- every valid evaluation maps to one stage.

## Transition blending

- at the boundary, alpha is 0;
- halfway through the transition, alpha is 0.5;
- after the transition, alpha is 1;
- no next-stage influence appears before the boundary.

## Walk-forward safety

- target date never scores itself;
- settlement must be known by evaluation time;
- 30 evaluations in one date/stage create one date-level score;
- score history is deterministic from persisted evaluation IDs.

## Weight math

- each configured stage prior sums to 1;
- best log-loss model receives multiplier 1;
- poorer models receive multiplier in `(0,1]`;
- insufficient history leaves multiplier 1 and status `prior_only`;
- weights sum to 1 after caps;
- no model exceeds 35%;
- GFS family never exceeds 45%;
- NBM follows its maturity cap;
- unavailable model weight is 0;
- deterministic input produces deterministic weights.

## Strategy integration

- model scenario arrays and per-model probabilities are identical across the three weighting modes;
- only mixture weights and downstream mixture outputs differ;
- one evaluation persists all modes atomically;
- only the primary shadow mode creates a candidate record;
- counterfactuals cannot create positions or exposure.

## Probability Lab

- live page contains no embedded July 7 fallback;
- latest view follows the current immutable evaluation;
- replay remains pinned by evaluation ID;
- all weighting fields come from API payload;
- the current stage and final weights match the backend exactly;
- desktop and mobile layouts do not overflow;
- no order action is exposed.

## Replay report

Report metrics by market stage and weighting mode. A valid report must include date counts and confidence intervals or date-level bootstrap uncertainty. It must not claim improvement from a single event.
