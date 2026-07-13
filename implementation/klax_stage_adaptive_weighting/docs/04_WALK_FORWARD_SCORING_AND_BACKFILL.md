# Walk-forward scoring and backfill

## Canonical source

Build stage-performance rows from immutable historical strategy evaluations that used the current state-consistent model definition. Do not reuse an old full-day scalar-maximum residual table as if it were equivalent.

## Per-date/per-stage row

Persist one row with at least:

```text
strategy_id
weighting_revision
model_key
target_date
stage_id
realized_market_ticker
realized_bracket_index
evaluation_count
mean_log_loss
mean_brier_score
mean_absolute_temperature_error (diagnostic)
source_evaluation_ids
settled_at
created_at
code_revision
```

Use the arithmetic mean of intrastage log losses so each target date has equal stage-level influence before recency weighting.

## Idempotent backfill

The backfill command must:

1. select settled target dates;
2. reconstruct or load immutable evaluations;
3. verify the outcome map used by each evaluation;
4. map the realized official result to exactly one bracket;
5. score every eligible model;
6. upsert only by a stable composite key;
7. record source evaluation IDs and code/config versions;
8. be safe to rerun.

## Missing history

If a model or stage lacks history:

- preserve the stage prior;
- set reliability multiplier to 1;
- expose exact date count and effective sample size;
- label the model `prior_only` for that stage;
- do not fabricate log loss.

## Outcome-map changes

Do not compare probability scores across incompatible outcome maps without an explicit reconciliation. If the number or definition of brackets changes, either:

- score only evaluations using a compatible map; or
- map both prediction and settlement to a verified common event definition.

Fail closed rather than silently scoring the wrong bracket.
