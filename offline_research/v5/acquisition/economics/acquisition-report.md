# V5 Fee and Settlement Integrity — bounded official evidence report

Acquired: 2026-09-27 UTC  
Series: `KXHIGHLAX`  
Bounded event dates: 2025-01-01 through 2026-08-31  
Safety: event metadata only; no nested markets, outcomes, protected confirmation labels, private account data, orders, or fills

## Acquisition result

- 604 dated KXHIGHLAX events were saved, from 2025-01-05 through 2026-08-31.
- The official event fee-change endpoint was queried separately for all 604 events: 604 succeeded, zero failed, and zero returned an event override.
- The official series fee-change endpoint was queried with `show_historical=true`: it returned zero series changes.
- 11 official or archived-official PDFs and six official Kalshi documentation pages were saved and SHA-256 bound.
- The direct current fee-schedule PDF was the only failed document request (`HTTP 429`). The 2025-07-08, 2025-09-17, 2025-10-01, and 2026-02-05 schedules are preserved through immutable captures of the official Kalshi PDF URL.
- The complete content-addressed inventory is `manifests/source-manifest.json`.

## What the evidence establishes

### Trading fee and rounding

The direct CFTC-filed general schedule establishes the historical base formula:

`ceil_to_cent(0.07 × contract_count × price × (1 − price))`

It applies to orders immediately matched against the book. Orders initially resting are not charged under the general schedule unless their product appears in a maker-fee section. It also states that there is no settlement, processing, or membership fee.

The captured Kalshi schedules establish these later periods:

| Effective date evidenced | Taker/general fee | Maker fee | Rounding | KXHIGHLAX status |
|---|---:|---:|---|---|
| 2025-07-08 | 7% quadratic | 1.75% only for listed series | next cent | not listed for maker fee |
| 2025-09-17 | 7% quadratic | 1.75% only for listed series | next cent | not listed for maker fee |
| 2025-10-01 | 7% quadratic | 1.75%, eligibility delegated to current fee page | next cent | historical page required to prove maker applicability |
| 2026-02-05 | 7% quadratic | 1.75%, eligibility delegated to current fee page | next cent | historical page required to prove maker applicability |

The API now reports KXHIGHLAX as `quadratic` with multiplier `1`. Its historical series fee-change array is empty, and all 604 bounded event fee-change arrays are empty. This is strong evidence that no series or event multiplier override is recorded by the current official history endpoints. It is not evidence of an individual order's maker/taker role or participant account class.

### Rebates and incentives

- A January 13, 2025 filing proposed a tiered monthly rebate beginning January 28, 2025.
- The August 31, 2025 filing explicitly states that the January program did **not** go into effect and terminated it. Therefore the V5 ledger must credit zero from that program.
- The replacement Volume Incentive Program could begin only upon Exchange notice on or after September 15, 2025. Eligibility and reward terms were market-page and participant dependent; the event-contract reward was capped at $0.005 per contract.
- The August 4, 2026 update could begin only upon Exchange notice on or after August 18, 2026 and allowed maker- or taker-specific treatment disclosed on market pages.

V5 must credit zero VIP reward unless the selected trade has contemporaneous KXHIGHLAX market eligibility and account-award evidence. The filing alone does not prove a rebate.

### Payout and settlement cost

The LAXHIGH certification, GLOBALTEMPERATURE certification/terms, official market-settlement documentation, and fee schedules agree that an ordinary winning binary contract pays $1 and a simple yes/no contract has zero settlement fee. These values may be frozen as a $1 payout multiplier and $0 settlement cost once the exact event rule revision is bound.

### Settlement rule and source

- The December 4, 2024 LAXHIGH certification covers Los Angeles Airport and the NWS Daily Climate Report, with inclusive integer-temperature brackets and strict above/below comparisons.
- The December 8, 2025 GLOBALTEMPERATURE certification was to begin after close of business December 9, 2025. It defines source hierarchy, official-station selection, full-precision comparison, inclusive between/at-least semantics, and a $1 payout.
- All 604 bounded historical event records identify the NWS LAX climatological report as their settlement source. Records around December 9–11, 2025 also retain that source.

The public event objects do not expose the contract-rule URL or revision effective for each historical event. The certification date alone cannot prove exactly when KXHIGHLAX moved from the LAXHIGH terms to GLOBALTEMPERATURE terms.

## Fail-closed readiness conclusion

The saved evidence substantially narrows the economics uncertainty but does not yet support `VERIFIED_EXACT` for a selected V5 trade ledger. Keep the colony at `FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE` until every accepted trade has all of the following:

1. Exact order arrival time and proven maker/taker role.
2. Direct-member, non-direct-member/FCM, and account precision classification.
3. A historical schedule covering that instant, including the July 2026 rounding transition.
4. A zero rebate or exact contemporaneous VIP eligibility/award binding.
5. The exact event contract-rule revision and event-status source.
6. A fixed selected-trade manifest and independent decimal ledger reproduction.

## Exact remaining access gaps

1. **Current July 7, 2026 schedule bytes:** the official live PDF returned HTTP 429 during the bounded acquisition. No saved authoritative PDF currently bridges the 2026-07-07 through 2026-08-31 rounding change.
2. **Historical maker-fee market page:** the October 2025 and February 2026 PDFs delegate maker applicability to a changing fee page. The API reports no KXHIGHLAX overrides, but an immutable historical market-page snapshot is absent.
3. **Per-event contract-rule revision:** public historical event metadata gives settlement source but not the contract-rule document/revision used by each event, so the LAXHIGH-to-GLOBALTEMPERATURE transition cannot be assigned event by event from the saved API data alone.
4. **Participant and fill facts:** maker/taker role, account class, account precision, and any actual paid fee are properties of the eventual execution evidence and cannot be inferred from public schedules.
5. **VIP entitlement:** historical KXHIGHLAX market designation, account eligibility, monthly aggregate volume, and actual award are absent. The safe binding is zero rebate.
6. **Selected trade and replica manifests:** V5 has not produced the frozen outcome-blind selected-trade ledger or independent economics reproduction, so no trade can yet receive an exact all-fields binding.

## Integrity controls

- `protected_confirmation_labels_read: false`
- `actual_orders_placed: false`
- acquisition used public network access once; replay and audit remain offline
- hashes and retrieval metadata are recorded in `manifests/source-manifest.json`
