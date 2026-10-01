# Friend method exact offline test protocol

This finite development-only test replaces the earlier HRRR/GEFS data adaptation with the historical GFS, NAM, and NBM daily-high features named in the supplied trading-bot specification. GFS Seamless is retained in provenance as a duplicate alias of GFS and does not receive a second ensemble weight.

The test uses the same 64 previously exposed dates as the earlier friend-method proxy and V5B-next comparison. The first 20 dates form an expanding chronological calibration prefix; the remaining 44 dates are scored. Labels may enter a fit only after their recorded publication time precedes the next 18:00 UTC decision. The reserved 28-date V5A holdout is unavailable to this test and must remain unopened.

Before scoring, each of the three effective source highs receives an expanding signed-bias correction. Nonnegative weights summing to one are fit by active-set ridge least squares, centered on equal weights with lambda 1.0. A zero intercept is fixed after bias correction. Predictive variance uses earlier prequential squared errors and a nonnegative direct disagreement coefficient with a 1°F floor. A Gaussian CDF maps the continuous forecast to the exhaustive integer Kalshi brackets. Two hundred deterministic date-seeded bootstrap refits provide a per-bucket tenth-percentile conservative probability.

Exactly four variants are permitted:

1. Kalshi fixed-PST NBM f008–f031 window, multi-bucket YES. This is the predeclared primary method.
2. Kalshi fixed-PST NBM f008–f031 window, single-best-bucket YES.
3. Supplied-fixture NBM f007–f030 window, multi-bucket YES.
4. Supplied-fixture NBM f007–f030 window, single-best-bucket YES.

The alternate NBM window is a timing sensitivity, not a second independent discovery. No threshold, model, or variant may be added after results are read in this run.

Each qualifying bucket must have an allowed A, B+, or B execution grade, a spread no wider than five cents, and at least $0.05 conservative expected profit after the frozen date-specific fee engine. Quantity is capped at one contract per bucket. B and B+ are historical execution evidence rather than verified fills.

The report separates forecast calibration, central and conservative expected returns, simulated settlement returns, chronological folds, evidence-grade composition, one-to-three-cent adverse-entry stress, and best-day removal. The registered screen requires at least 30 selected dates, at least 10% aggregate simulated return, at least 10% mean estimated return, four positive folds, worst-fold return of at least -10%, positive two-cent stress, positive return without the best date, sufficient evidence quality, and no worse multiclass Brier score than the inherited reference.

All four results are reported and ranked. A positive development result is a research lead on repeatedly exposed history. It is not independent confirmation, a verified fill record, actual profit, or authorization to trade.
