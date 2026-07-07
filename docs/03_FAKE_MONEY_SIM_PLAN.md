# Fake-money simulation plan

## Required behavior

The paper broker simulates:

```text
buy YES at executable YES ask
sell YES at executable YES bid
buy NO at executable NO ask
sell NO at executable NO bid
```

For live orderbooks, use the reciprocal Kalshi bid/ask math:

```text
yes_ask = 1 - best_no_bid
no_ask  = 1 - best_yes_bid
```

## Fill assumptions

Start conservative:

1. Marketable paper buys fill at the implied ask.
2. Paper sells fill at the visible bid.
3. No midpoint fills in v0.
4. No maker queue simulation in v0.
5. Apply configurable fee/slippage buffers even if the exact fee function is not implemented yet.

## Ledger fields

Each simulated fill should record:

```text
timestamp_utc
ticker
side: yes/no
action: buy/sell
quantity
price
fee_estimate
cash_after
position_after
reason
model_probability
market_bid
market_ask
snapshot_id
```

## Why this matters

This is not a normal forecast project. It must test whether the market reprices before settlement and whether entry at ask / exit at bid is actually profitable.
## Phase 7 POC Validation

Paper trading remains fake-money only. `paper-report` summarizes fills, open
positions, cash, realized P&L, entry reasons, exit reasons, and no-trade states.

`paper-replay` reads stored prediction rows and simulates simple entries/exits
without live network calls. It is diagnostic only and should not be interpreted
as proof of edge without official joined outcomes and sufficient sample size.

`poc-check` does not run fake paper execution unless `--include-paper-once` is
passed. It never places live orders.
