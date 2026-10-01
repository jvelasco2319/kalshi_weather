# V7 six-week outcome-blind shadow campaign

Registered September 28, 2026 before the first target date.

**Status:** Paused and superseded before the first target date. No V7 target prediction, fill, or outcome was recorded. The user's corrected historical six-week request is complete as [V7H](V7H_HISTORICAL_SIX_WEEK_REPLAY.md).

## Question

Does any frozen LAX high-temperature method retain a positive, execution-supported economic result on genuinely new dates when its decision is made from information available at 18:00 UTC and the result is hidden until the full comparison is frozen?

V7 is a 42-day preliminary independent comparison. It is more credible than the exposed seven-day replay, but it does not replace the registered 100-Grade-A-day final-confirmation requirement.

## Fixed period and market

- Market: Kalshi `KXHIGHLAX` daily maximum-temperature brackets.
- Contract settlement source: the exact source named by each historical contract; incompatible sources are ineligible.
- Target dates: 2026-09-29 through 2026-11-09 inclusive, 42 calendar days.
- Decision time: 18:00:00 UTC.
- Arrival check: 18:00:05 UTC.
- Position: at most one hypothetical contract per method per climate date, held to settlement.
- Orders: zero paper orders and zero live orders. The ledger is research output only.

## Frozen methods

Primary ranking:

1. `v5b_causal_no`: the immutable V5B calibrated HRRR/GEFS NO policy.
2. `v6_gefs_spread_equal_blend_no`: the V6 GEFS-spread probability model with the frozen NO economic policy.
3. `diagnostic_nam_only`: the fixed NAM-only challenger identified by the seven-day replay. It remains a diagnostic until this campaign finishes.

Secondary comparators:

4. `friend_exact_primary_gfs_nam_nbm`.
5. `v5f_cross_family_stack`.
6. `diagnostic_gfs_only`.
7. `diagnostic_nbm_only`.

The June 1-August 3, 2026 training state is frozen once. V7 target outcomes, forecasts, books, or results cannot update weights, bias terms, residual distributions, thresholds, side eligibility, or ranking. A missing input causes an explicit abstention or missing-evidence record.

## Daily causal sequence

For each elapsed target date:

1. Acquire only the registered 00Z/06Z archived weather inputs that were nominally available by 18:00 UTC: HRRR/GEFS for V5B and V6, plus GFS/NAM/NBM for the friend family.
2. Acquire the exact KXHIGHLAX contract universe and preserve the contract's settlement source and bracket boundaries.
3. Acquire Probalytics full-depth snapshots for 17:55-18:06 UTC. Extract the last verified, contiguous book at or before 18:00:00 and 18:00:05.
4. Run the prediction and order-freeze phase without opening settlements. Offset zero selects and freezes one ticker, side, quantity, and whole-cent limit. Offset five may only decide whether that frozen order would have filled; it cannot reselect.
5. Hash the raw inputs, predictions, order record, abstentions, and evidence grade. Do not read that date's outcome in this phase.

The complete 42-day prediction ledger is frozen before the outcome-scoring phase begins. A late backfill follows the same causal filters and cannot change the registered methods.

## Execution and fee rules

A primary Grade-A fill requires a verified and contiguous Probalytics `before_*` book, quote age from 0 through 5,000 milliseconds, an exact 1-99 cent ask, displayed ask size of at least one contract, and a noncrossed top of book. The 18:00:05 snapshot tests only the limit frozen at 18:00:00. B+, B, missing, stale, reset, and proxy evidence are reported but excluded from primary economics.

Entry fee is the exact date-effective Kalshi taker-fee function, rounded up to $0.0001 per contract. The primary result buys one contract and holds it to settlement. Aggregate return is total payout less price and fee, divided by total price and fee.

## Evidence and ranking

Each method reports:

- eligible dates, orders, Grade-A fills, wins, losses, and abstentions;
- aggregate capital-weighted net return and mean expected net return;
- Brier score and clipped log loss when the numeric settlement target is available;
- three fixed 14-day folds and positive-fold count;
- one-sided 95% moving-block-bootstrap lower bound with a three-day block;
- one-cent and two-cent adverse-entry stress;
- best-day-removed return and concentration;
- coverage by execution grade and explicit missing-data reasons.

Ranking is lexicographic: gate status, Grade-A aggregate return, bootstrap lower bound, positive folds, Grade-A fill count, forecast score, then method ID. The 10% screen requires at least 10% aggregate Grade-A return, positive return in at least two of three folds, a one-sided 95% lower bound above zero, positive two-cent stress, and a positive best-day-removed result. Because the period has only 42 dates, passing these preliminary gates still does not confirm the strategy.

## Outcome boundaries and stopping

The campaign ends after all 42 dates have prediction/order freezes and one terminal scoring pass, or with an integrity conclusion if causal evidence cannot be reconstructed. It cannot extend the date range, add methods, repeat scoring with altered rules, or search until a positive result appears. Negative, inconclusive, and missing-execution conclusions are valid.

No outcome is read before the corresponding immutable daily freeze. No result is used to change a later prediction. No live feed, paper order, live order, broker credential, or order authorization is permitted.

## Completion labels

- `V7_SHADOW_PRELIMINARY_POSITIVE`: at least one method passes every six-week preliminary gate.
- `V7_SHADOW_POSITIVE_BUT_FRAGILE`: positive Grade-A aggregate return without every gate.
- `V7_SHADOW_NO_POSITIVE_EDGE`: sufficient Grade-A evidence and no positive method.
- `V7_SHADOW_INSUFFICIENT_EXECUTION_EVIDENCE`: the period cannot support a reliable economic comparison.
- `V7_SHADOW_INTEGRITY_FAILURE`: a frozen identity, causal boundary, or source binding fails.

Only a separate future campaign with at least 100 genuinely new Grade-A days may issue a final confirmation label.
