# V5 execution-evidence acquisition research

Research date: 2026-09-27  
Campaign series: `KXHIGHLAX`  
Requested window: 2025-07-01 through 2026-08-31 (427 calendar days)  
Registered execution-coverage gate: at least 90% of outcome-blind event windows

## Conclusion

The original V5 execution barrier cannot currently be cleared from any publicly
documented source found in this review.

Kalshi documents a current REST order-book snapshot and a real-time WebSocket
snapshot-plus-delta feed. It does not document a historical order-book endpoint.
Its historical API lists markets, candlesticks, public trades, member orders,
member fills, and positions, but not historical book snapshots or deltas.

Two commercial archives document Kalshi full-depth history:

* CryptoStruct advertises KXHIGHLAX tick-by-tick trades and Level-2 updates from
  March 2026 onward. Even granting a 2026-03-01 start, its overlap through the
  requested 2026-08-31 end is at most 184/427 calendar days (43.1%).
* Probalytics documents Kalshi binary order books from 2026-05-09 onward. Its
  maximum overlap through 2026-08-31 is 115/427 days (26.9%).

Combining those two cannot extend coverage earlier than March 2026. The best
documented upper bound therefore remains 43.1%, far below the registered 90%
gate. Both vendors grant customer usage rights, but neither page reviewed
documents that Kalshi has appointed it as an authorized redistributor. A vendor
purchase must therefore pass licensing and source-provenance review as well as
the technical capability probe.

Authenticated member history is useful only for orders the member actually
submitted. Historical fills prove the exact member order and trade identity,
side, price, executed quantity, match time, taker status, and charged fee.
Historical orders add the submitted price and quantity, creation/update times,
status, remaining quantity, and maker/taker cost and fee totals. Together they
can prove realized execution for a specific submitted order. They cannot prove
that an unsubmitted counterfactual campaign order would have filled, reveal the
rest of the queue, or reconstruct market depth. The frozen V5 evidence audit
also records that no campaign orders were placed, so member history cannot
retroactively validate the V4 leader.

## Exact access required to clear the barrier

One of the following must become available before promotion:

1. A Kalshi-supplied or contractually authorized archive for KXHIGHLAX covering
   the registered window with full price-level size, snapshot anchors or
   replayable deltas, venue timestamps, sequence/gap evidence, and an internal
   research license. The public institutional page only advertises historical
   market-implied probabilities; it does not promise historical depth. A written
   scope confirmation from `institutional@kalshi.com` is required.
2. A vendor archive whose verified KXHIGHLAX coverage reaches at least 90% of
   the registered event windows. On a calendar-day upper-bound basis, a complete
   archive beginning no later than 2025-08-12 and running through 2026-08-31
   could meet 90%. The vendor must also provide a gap/incident manifest and
   terms permitting internal backtesting.
3. For actual-fill validation only, a read-only export made with the member's
   own Kalshi API credentials from both `/historical/orders` and
   `/historical/fills` (plus the live-database portfolio routes on the other side
   of Kalshi's moving cutoff). This route can promote only exact submitted
   campaign orders and fills; it cannot validate unsubmitted historical signals.

Before accepting a vendor source, obtain a bounded sample and deterministically
verify native ticker preservation, event/receive timestamps, sequence or state
identity, full price-level quantities, snapshot reset semantics, gap labelling,
the five-second arrival lookup, and hashes for the delivered files. Do not buy a
full archive until the sample passes. No market data, credentials, protected
labels, live feeds, or orders were accessed for this research.

## Primary capability sources

* Kalshi historical endpoint inventory:
  <https://docs.kalshi.com/llms.txt>
* Kalshi current REST order book:
  <https://docs.kalshi.com/api-reference/market/get-market-orderbook>
* Kalshi real-time order-book snapshots and deltas:
  <https://docs.kalshi.com/websockets/orderbook-updates>
* Kalshi historical member fills:
  <https://docs.kalshi.com/api-reference/historical/get-historical-fills>
* Kalshi historical member orders:
  <https://docs.kalshi.com/api-reference/historical/get-historical-orders>
* Kalshi institutional data contact:
  <https://institutional.kalshi.com/>
* CryptoStruct KXHIGHLAX coverage:
  <https://cryptostruct.com/prediction-markets/kalshi-kxhighlax>
* CryptoStruct protocol and license:
  <https://docs.cryptostruct.com/market-data-api/protocol/>
  and <https://cryptostruct.com/license>
* Probalytics Kalshi coverage and collection architecture:
  <https://www.probalytics.io/platforms/kalshi> and
  <https://www.probalytics.io/architecture>
* Probalytics pricing and terms:
  <https://www.probalytics.io/pricing> and
  <https://www.probalytics.io/legal/terms>
* PredictionMarketBench public replay format (its weather episode is KXHIGHNY,
  not KXHIGHLAX):
  <https://github.com/Oddpool/PredictionMarketBench>

## Local evidence checked

* `src/klax_lab/acquire_kalshi.py` permits public historical markets,
  candlesticks, and public trades; it has no order-book or member-fill collector.
* `data/manifests/v3_minute_kalshi.json` records
  `historical_depth_available=false` and treats candles/trades as insufficient
  for hypothetical fills.
* `reports/v4-quote-evidence-audit.md` documents the missing size, sequence,
  queue, persistence, and arrival-state evidence.
* `v5/artifacts/current-execution-evidence-audit.json` records zero historical
  depth snapshots and zero hypothetical-fill-supported snapshots.
* `v5/artifacts/current-execution-readiness-verdict.json` records the blocked
  `EXECUTION_EVIDENCE_UNAVAILABLE` verdict, a 90% coverage requirement, and a
  minimum of 30 selected trades.


## Additional internet search on 2026-09-27

A wider search found no archive that reaches the missing 2025 window:

* PMXT publishes free hourly Kalshi order-book Parquet files, with visible files
  beginning in June 2026. Hourly samples cannot establish the registered
  five-second arrival state and begin too late.
* `lerchen3/kalshi-orderbook-alpha` on Hugging Face is a recent live capture of
  a rolling top-200 universe. Its dataset card does not document 2025 coverage
  or guaranteed KXHIGHLAX inclusion and its license is listed as `other`.
* An anonymous Reddit poster claimed on 2026-09-22 to hold a Kalshi data
  distribution license and to offer historical depth. The post supplies no
  legal identity, KXHIGHLAX coverage dates, schema, gap report, or licensing
  evidence. It is a contact lead only and cannot satisfy readiness without
  direct documentary verification and a bounded sample audit.

The best documented KXHIGHLAX coverage remains CryptoStruct from March 2026,
which is below the frozen 90% requirement.
