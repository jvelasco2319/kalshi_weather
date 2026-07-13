# Market Data and Execution

## Trades

Persist `trade_id`, ticker, `count_fp`, Yes/No fixed-point prices, creation time, block flag, raw page/cursor metadata, and payload hash.

A nonempty trade pull fails when:

- `count_fp` is absent or nonpositive;
- pagination does not reach an empty cursor;
- duplicate trade IDs remain after normalization;
- requested time coverage is incomplete without a recorded reason.

## Orderbook

Maintain each contract from:

1. authenticated `orderbook_snapshot`;
2. incremental `orderbook_delta` messages;
3. strict sequence checking;
4. automatic invalidation and resnapshot after a gap.

Persist at least ten levels on both native sides, exchange timestamp, receipt timestamp, sequence, subscription ID, and quote age.

Candlesticks are retained for coarse analytics only. They cannot prove historical depth, queue position, synchronized cross-contract prices, or maker fills.

## Event-driven triggers

Reevaluate when any of the following changes:

- a model run becomes available;
- a forecast path changes;
- a KLAX observation arrives;
- the observed maximum changes;
- a relevant orderbook level changes;
- the book becomes invalid or valid;
- market rules, fees, or lifecycle status change;
- an order, fill, cancellation, rejection, or position changes;
- a stale-data watchdog fires.

Use deterministic event coalescing so bursts create one coherent evaluation without losing source event IDs.

## Future execution state machine

```text
IDLE
  -> EVALUATED
  -> CANDIDATE
  -> CANCEL_STALE_ORDERS
  -> REEVALUATE
  -> POST_ONLY_SUBMIT
  -> ACKNOWLEDGED
  -> PARTIAL / FILLED / CANCELLED / REJECTED
  -> RECONCILE
```

The current implementation stops at a non-submitting shadow sink. Taker execution is disabled.
