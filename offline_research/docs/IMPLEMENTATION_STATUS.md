# V3 implementation and campaign status

**Final checkpoint:** September 26, 2026  
**Implementation:** `COMPLETE`  
**Campaign:** `OFFLINE_CAMPAIGN_COMPLETE`  
**Scientific conclusion:** `NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET`

The registered V3 objective is complete. Historical acquisition, normalization, exact settlement reconstruction, dataset freezing, substantive readiness, the bounded six-colony campaign, independent numerical replication, adversarial review, and terminal integrity audits all finished. No candidate passed the registered promotion gates. The protected July-December 2025 interval remains sealed, no actual order was placed, and no profitability claim is supported.

## Final data state

The historical weather acquisition completed all **543 of 543** registered days. It preserved 10,860 raw HRRR/GEFS objects totaling approximately 20.6 GB and generated 21,720 normalized forecast rows. As-of observations cover KLAX and four nearby stations. The market archive contains **614,093 one-minute candles** and **170,669 public trades** for **1,062 contracts**.

Exact settlement reconciliation produced **1,050 contract outcomes** across **175 eligible January-June 2025 days**. June 10 and June 11 remain explicit source exclusions. The weather-training set contains 365 eligible targets. January 5-February 3 is the fixed 30-day market-calibration prefix; the following 145 development days are divided into five chronological 29-day folds. July-December 2025 was never opened.

The frozen dataset identifier is `872161299f101a830f60cd0e546732b20d7e490b18c140bb17eb9fca5912ec0c`. Its bundle SHA-256 is `c24f483a93ae8766edc51047b8f3dbf7c9c3d5de8ca1b4781f2b2549bbf41824`.

## Architecture delivered

V3 implements the registered OpenAI-inspired iterative research architecture in a bounded, auditable form:

- six research colonies for settlement, local weather, ensemble probabilities, market behavior, execution/abstention, and adversarial alternatives;
- typed finite proposal packets and a compiler that rejects invented or unregistered plans;
- exact parent lineage, novelty signatures, deterministic allocation, combinations, mutations, and adversarial challenges;
- HRRR, GEFS, local observations, weather regimes, pooled fallbacks, multiple probability/calibration families, optional market residuals, and conformal abstention;
- historical replay with as-of quote controls, costs, one purchase per event, and deterministic tie breaking;
- five chronological folds, bootstrap uncertainty, cost stress, concentration analysis, independent recomputation, and an independent critic;
- immutable artifact manifests, one-use readiness and campaign bootstrap, durable recovery, atomic publication, packet-size preflight, and an exclusive campaign lease; and
- a separate protected-final evaluator that cannot read the sealed interval without a hash-bound champion authorization.

The first V3 launch stopped before inference when an epoch-two packet exceeded the worker transport limit. Its artifacts and consumed call remained immutable. A hash-bound amendment authorized one deterministic successor, imported the exact prior state and budgets, rebuilt the affected queue, and added denial and transport preflights. The successor is the authoritative completed campaign. The one-use bootstrap chain is now `COMMITTED` and cannot be reused as a fresh budget.

## Completed campaign

Campaign `v3-offline-20260926T164700000Z-r2` completed six epochs and stopped because the registered **60-candidate budget** was exhausted. Its verified continuation chain records **61 total model calls**, including 11 already consumed by the predecessor and 50 new calls. It admitted and evaluated 60 distinct candidates, ranked 59, and retained one denied historical diagnostic only in quarantine.

| Colony | Evaluated | Rejected |
| --- | ---: | ---: |
| Settlement measurement | 9 | 9 |
| Local weather | 9 | 9 |
| Ensemble probability | 13 | 13 |
| Market behavior | 12 | 12 |
| Execution/abstention | 4 | 4 |
| Adversarial alternatives | 13 | 13 |

There were no duplicate admissions, nonproposal responses, integrity failures, or champions. Independent replication passed and the critic did not reject any candidate for an integrity defect. All candidates still failed promotion because none produced a trade.

## Scientific result

All 60 candidates had the same forecast scores:

- Brier score `0.8226837628`, versus reference `1.1019490232`: **25.34% lower**;
- ordered discrete CRPS `0.6594683884`, versus reference `0.9833535955`: **32.94% lower**.

Those forecast gains never became an eligible purchase. Across 8,700 candidate-days, 2,160 had no usable central interval width and 4,560 exceeded the universal 4°F width cap. The remaining 1,980 candidate-days produced 23,760 contract-side market decisions:

| First recorded rejection | Count | Share |
| --- | ---: | ---: |
| Spread above limit | 13,768 | 57.9% |
| Price outside band | 6,519 | 27.4% |
| Quote too stale | 3,472 | 14.6% |
| Expected return below 10% target | 1 | less than 0.1% |

Every candidate therefore had zero trades and undefined realized ROI. All failed the 10% aggregate-return, minimum 30 trades, minimum 30 settlement days, bootstrap, stress, five-fold, best-day-removal, and contribution-breakdown gates. The selected-trade expected-return rule did not emit a violation only because the selected-trade set was empty; this is not a substantive pass.

The campaign also revealed a search-allocation coverage defect. All 60 compiled plans used 12:00 UTC, a 4°F interval-width cap, and substantially the same forecast stack. Fifty-eight used a one-minute quote-age limit; only two used five minutes, both paired with a five-cent spread cap. No candidate reached 15:00 or 18:00 decision times, 15- or 60-minute quote ages, 6/8/12°F width caps, or the key interactions among these controls. The language supported them, but adaptive parent reuse and finite enumeration exhausted the budget first. This finding does not change the registered result.

## Verification

The complete code suite passed **632 tests and 114 parameterized subtests** with zero failures, followed by a successful source compilation check. The terminal campaign verifier passed all **1,007 artifacts**. A separate read-only audit confirmed:

- all 69 frozen source files match the authorized code inventory;
- the continuation manifest and deterministic epoch-two rebuild are internally consistent;
- readiness, ticket, amendments, claim, and committed bootstrap transitions are valid;
- the predecessor authorization archive remains unchanged;
- no denied plan entered the published ranking; and
- `protected_final_read`, `final_authorized`, and `actual_orders` are all false.

The terminal campaign-artifact SHA-256 is `60074df9c5deb6a467e77143a579c46242d55a41ac1b3dba17d4cba286adaec2`. The report SHA-256 is `e94d52b4ba66a41dc99635388351dab09abc004409d30f1cc343360322e60ab4`. The readiness and ticket hashes are `02d739099b64bafb78fd5dcab7cac20470f8eed0e7e01cfe1b4b4dc32d766946` and `d2ebeb8413f37ef0e5ea9955c62bb62bdbe4bb7bcf9fffa050d7fdd10bb74883`.

## Defensible next campaign

The next iteration should be a separately registered execution-feasibility campaign. Keep the current forecast model and 10% expected net-return target fixed at first. On the calibration prefix only, measure the outcome-blind opportunity funnel across decision times 12:00/15:00/18:00 UTC, width caps 4/6/8/12°F, quote ages 1/5/15/60 minutes, spread caps 5/10/15/25 cents, and the registered price bands. Select a small Pareto set by a preregistered rule that favors the tightest controls with adequate opportunity counts, freeze it, and then score it on the development folds with all present promotion gates unchanged.

The allocator should force interaction coverage and reserve a much larger quota for execution/abstention before it reuses ROI-ranked parents. Market-residual and forecast-family variants should wait until at least one execution policy produces enough trades for valid inference. If no calibration policy produces adequate opportunities without quotes older than 60 minutes, spreads above 25 cents, or weaker assumed-fill rules, the project should acquire richer historical quote or order-book evidence rather than relax the gates.

Detailed results are in [the final campaign analysis](../reports/v3-final-campaign-analysis.md) and [the objective completion audit](../reports/v3-objective-completion-audit.md).
