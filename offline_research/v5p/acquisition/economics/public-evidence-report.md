# V5P Fee and Settlement Integrity — public evidence closure

Scope: `KXHIGHLAX`, 2025-07-01 through 2026-08-31  
Use: offline research only; no protected confirmation labels, private account data, orders, or live replay network access

## Evaluator conclusion

The free public acquisition clears the July 7, 2026 fee and rounding transition. It also provides exact direct-exchange **taker** economics from July 8, 2025 through August 31, 2026, subject to quote-depth/fill evidence and the exclusions below. It does not make the campaign promotion-ready: the exact per-event settlement-rule revision and several execution/account facts remain unavailable from public sources.

The machine-readable policy is `manifests/evaluator-bounds.json`. It is fail-closed: research may run within the stated bounds, but it cannot claim verified profitability.

## Fee periods the evaluator may use

| Exchange date | Taker | Maker | Rounding | Allowed use |
|---|---:|---:|---|---|
| 2025-07-01 to 2025-07-07 | unbound | unbound | unbound | exclude |
| 2025-07-08 to 2025-09-30 | 7% quadratic, multiplier 1 | multiplier 0 | total order fee up to next cent | direct taker with fill evidence; maker schedule also evidenced |
| 2025-10-01 to 2026-07-06 | 7% quadratic, multiplier 1 | unresolved for KXHIGHLAX | total order fee up to next cent | direct taker with fill evidence; exclude maker |
| 2026-07-07 to 2026-08-31 | 7% quadratic, multiplier 1 | default multiplier 0 | fee plus position cost up to a centicent | direct taker with fill evidence |

The July 7, 2026 official schedule states:

- taker fee: `round up(M × 0.07 × C × P × (1-P))`, with default `M=1`;
- maker fee: `round up(M × 0.0175 × C × P × (1-P))`, with default `M=0`;
- the rounding target is a centicent for fee plus position cost;
- there is no settlement fee; and
- an FCM can charge additional fees.

`KXHIGHLAX` is absent from that schedule's non-standard series, so the default multipliers apply. The current official series and 604 event fee-history responses report no override. Those current history endpoints corroborate the preserved schedules; they are not used to invent a missing immutable maker-page snapshot.

## Rebates, payout, and settlement

The evaluator credits **zero rebate**. The January 2025 proposed program never took effect. Later incentive programs require market designation, account eligibility, volume, notice, and an actual award record that the public evidence does not supply.

The public rules support a $1 ordinary winning-contract payout and a $0 exchange settlement fee. Both the LAXHIGH and GLOBALTEMPERATURE materials use strict `above`, strict `below`, and inclusive `between` threshold semantics. All 604 saved historical event records identify the NWS Los Angeles Airport climate report as the settlement source.

The evidence does not identify the exact contract-rule document/revision attached to each historical KXHIGHLAX event. The December 8, 2025 GLOBALTEMPERATURE certification says initial listing could begin after close of business December 9, but it does not establish the event-by-event transition from LAXHIGH. The evaluator may use the common semantics for partial-evidence research, but strict promotion must fail until the revision is bound.

## Remaining gaps and evaluator action

1. **July 1-7, 2025 fee schedule:** exclude these seven dates.
2. **Maker applicability from October 1, 2025 through July 6, 2026:** exclude maker fills; direct taker economics remain available.
3. **Exact event-rule revision:** keep strict promotion closed for every candidate relying only on this public package.
4. **Execution and participant facts:** require contemporaneous order arrival, quote depth, fill, maker/taker role, account precision, participant class, and any FCM/broker fee. An assumed fill is not promotable.
5. **VIP entitlement:** credit zero unless an exact contemporaneous award is later supplied.

## Evidence integrity

The public package binds the official July 2026 PDF, official fee-rounding documentation, CFTC/Kalshi certifications, the Internet Archive snapshot index, and the frozen V5 evidence inventory by SHA-256. Re-run `build_evaluator_bounds.py` offline to verify every bound file and regenerate the self-hashed manifest. The source manifest records bounded retrieval failures separately, including Wayback timeouts; those failures do not erase the successful preserved CDX capture.

- `protected_confirmation_labels_read: false`
- `actual_orders_placed: false`
- `private_account_data_read: false`
- `historical_replay_network_allowed: false`
- `frozen_v5_modified: false`
