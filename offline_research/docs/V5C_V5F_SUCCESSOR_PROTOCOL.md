# V5C-V5F successor protocol

## Question

Can the exact GFS/NAM/NBM archive improve the frozen V5B NO strategy on the same 44 post-warmup development dates without changing V5B's price, spread, fee, execution-grade, side, expected-profit, or probability-gap rules?

This is a finite comparative development study. It cannot confirm profitability because the dates have already been exposed to earlier research. The reserved 28-day V5A holdout stays sealed.

## Frozen reference

The reference is the frozen V5B candidate identified by `runs/v5b_current_campaign.json` and its sealed `strategy-freeze.json`. The comparison uses dates 21-64 of the same 64-date development chronology because the exact friend-model forecasts require 20 prior dates.

Each successor changes one mechanism:

| Version | Mechanism | Fixed rule |
|---|---|---|
| V5C | Cross-model consensus/adversarial veto | A contract can be considered only when GFS/NAM/NBM assigns at most 25% YES probability to the bracket. |
| V5D | Disagreement-regime abstention | A contract can be considered only when the V5B and exact-model YES probabilities differ by at most 15 percentage points and the GFS/NAM/NBM daily-high range is at most 6F. |
| V5E | Tail recalibration | Replace the single Gaussian with an 80/20 mixture of the fitted Gaussian and a Gaussian with twice its sigma. |
| V5F | Fixed stacked ensemble | Blend the frozen V5B probability and V5E heavy-tail probability 50/50. |

The numbers above are mechanism tests chosen before successor scoring. No threshold search or post-result mutation is allowed within this suite.

## Evaluation

Every method uses one selected NO contract at most per date, exact historical fee logic, the frozen five-cent spread ceiling, and the existing evidence grades A, B+, and B. Returns are settlement simulations and never verified fills. Report aggregate return on entry outlay, selected dates, five chronological folds, worst fold, two-cent adverse-fill stress, best-day-removed return, multiclass Brier score, selection calibration, and evidence-grade composition.

The original V5B gates remain visible. Because the shared scoring cohort has only 44 dates, failure of the original 30-selected-day gate is not relaxed or hidden. Ranking is descriptive development ranking, not confirmation or authorization to trade.

## Safeguards

- Historical cache only; network denied during execution.
- No current/live feeds, credentials, paper orders, or live orders.
- No protected-holdout labels or outcome files.
- Configurations, code, V5B strategy freeze, exact forecasts, and development inputs are hash-bound before evaluation.
- An independent verifier re-runs every calculation and checks artifact hashes.
- A favorable result does not open the holdout automatically.

