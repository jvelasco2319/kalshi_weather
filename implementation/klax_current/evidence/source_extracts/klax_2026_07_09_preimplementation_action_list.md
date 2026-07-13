# KLAX Bot v2 — Required Changes Before Codex Implementation

## Decision

- **Proceed:** pure forecast, probability, fee, EV, sizing, and portfolio-risk core.
- **Proceed after collector repair:** shadow mode.
- **Do not proceed:** canary or live order placement.

## Add a mandatory Phase 0.5: Market-data integrity

1. Map and persist Kalshi `count_fp` for every trade; reject or quarantine records with missing or non-positive quantity.
2. Validate pagination using cursors and record page-level row counts, time bounds, and response hashes.
3. Capture raw order-book snapshots plus sequence-checked WebSocket deltas for all six contracts. Persist at least the top 10 levels on both Yes and No sides.
4. On sequence gaps, mark the book invalid, stop decisions, resnapshot, and record the gap.
5. Persist exchange timestamps, collector receipt timestamps, normalized timestamps, and quote age.
6. Deduplicate the candlestick and market-history exports; one canonical table should own each data grain.
7. Create explicit completeness checks. An empty failure file must never be treated as proof of complete capture.

## Required decision-state record

For every event that triggers evaluation, persist:

- Raw forecasts for ECMWF IFS, GFS 0.13°, GFS Seamless, and NAM.
- Model run initialisation, estimated provider availability, receipt time, and accepted-as-of time.
- Per-model correction, corrected values, consensus, model spread, observation floor, and regime flags.
- Residual-scenario IDs, bracket counts, `p_mean`, `p_safe_yes`, and `p_safe_no`.
- Best bid/ask, depth by level, quote age, fee schedule/version, maximum executable price, proposed quantity, EV, ROI, and reason code.
- Cancel, replace, submit, acknowledgement, fill, reject, settlement, and fee events.

## Trigger policy

Recompute on:

- A newly available model run.
- Every accepted KLAX observation or running-max change.
- A material best-price or depth change.
- Any order, fill, cancel, reject, fee, rule, or settlement-source change.
- A watchdog interval, solely to detect stale dependencies.

Hourly-only evaluation is not acceptable.

## Promotion gates

Shadow mode must remain incapable of submitting orders. Canary promotion requires at least 30–60 complete event-days and all of the following:

- No missing trade quantities or unresolved order-book sequence gaps in evaluated windows.
- Probability calibration by price band, market hour, season/regime, and model-spread bucket.
- Replay using only information available at each decision timestamp.
- Maker fill-rate and adverse-selection estimates derived from real order lifecycle data.
- Net expected and realized ROI including fees, slippage, unfilled orders, cancellations, and latency.
- Stable performance under date-clustered confidence intervals, not row-level pseudo-replication.

## July 9 lesson encoded as an acceptance test

At any price above the maximum whole-cent entry compatible with the configured return hurdle—even at `p_safe = 1`—the decision engine must return `NO_TRADE_PRICE_TOO_HIGH`. Under the general fee schedule and a 100-contract zero-slippage illustration, this ceiling was 86¢ for a 15% hurdle and 90¢ for a 10% hurdle.
