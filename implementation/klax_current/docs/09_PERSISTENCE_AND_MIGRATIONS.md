# Persistence and Additive Migrations

Use the existing SQLite journal and migration conventions. `sql/001_strategy_current_additive.sql` is a logical reference; adapt table names and foreign keys to the repository rather than creating duplicates.

## Required immutable entities

- strategy configuration version;
- raw source payload and hash;
- forecast run and every forecast path point;
- accepted/rejected KLAX observation;
- market/series rule version;
- fee schedule version;
- orderbook snapshot/delta and book validity interval;
- public trade with `count_fp` and page audit;
- capture completeness manifest;
- coherent decision state;
- per-model live state and residual provenance;
- model weight snapshot;
- bracket probabilities;
- candidate economics/risk;
- shadow order/fill simulation;
- settlement and realized outcome.

## Timestamp fields

At minimum preserve:

```text
run_time
source_available_at
valid_time or observation_time
exchange_time when applicable
received_at / ingested_at
evaluated_at
```

## Reproducibility

Every decision row references:

- source event IDs;
- strategy ID and config hash;
- code revision;
- target date and evaluation time;
- market rule and fee versions;
- residual date IDs;
- model weights;
- book sequence and quote age.
