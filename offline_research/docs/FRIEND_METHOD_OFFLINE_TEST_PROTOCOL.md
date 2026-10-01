# Offline test protocol for the supplied weather-bot method

This protocol freezes the interpretation of `TRADING_BOT_SPEC.md` before economic scoring. The source specification SHA-256 is `f5caf97f363c704513d5415de681100c6e734a9716895ca0230ffc8d4bd51652`.

## Question

Does the specification's core trading mechanism—chronologically calibrated weather-ensemble probabilities, conservative long-YES edge screening, and optional purchases of several mutually exclusive buckets—perform better than the frozen V5A, V5B, V5B-next and V6 research methods on the same exposed offline development evidence?

This is a development comparison. The dates have been examined repeatedly. It cannot independently confirm profitability or authorize trading.

## Exact implementation boundary

The supplied specification requests GFS, GFS Seamless, NAM and NBM inputs and names a legacy `Los_Angeles,_CA_2026-07-03.json` fixture. The fixture is now present and hash-bound as `9f7951828cd242a75076827054c0d3387703ce041957452d7ebeca7872a6a0f2`. It contains one target date and can validate parsing, probability mass, sample counts and duplicate grouping. It cannot fit calibration or support a return estimate. There is still no historical four-model input set. The existing frozen 64-date cohort contains HRRR sampled maxima and GEFS mean/spread summaries.

The conformance test must detect 3/3/8/24 samples for GFS/GFS Seamless/NAM/NBM and group GFS with GFS Seamless because their numerical contents match despite different source paths. Parsing success remains research-only because station identity, historical availability, reporting-window coverage and raw-GRIB provenance are not established.

The scored test therefore implements a **data-adapted core method**:

- HRRR and GEFS are the two effective weather sources.
- Each source receives a prior-only expanding signed-error bias correction.
- A nonnegative two-source weight constrained to sum to one is fitted on prior published labels only.
- Forecast variance has a 1°F floor and a nonnegative contribution from weighted between-source spread, fitted to prior one-step squared errors.
- Contract probabilities are Gaussian CDF masses at nearest-integer half-degree boundaries.
- Two hundred deterministic prior-date bootstrap refits produce scenario probabilities; the 10th percentile per bucket is used for entry screening.
- Only long YES purchases are allowed. A bucket needs at least $0.05 conservative expected profit after the exact date-specific fee and executable ask.
- The single-bucket variant chooses the highest conservative expected profit. The multi-bucket variant buys one unit of every qualifying bucket. Quantity is capped at one because the historical record does not establish reusable depth, queue position or partial fills.

The result is not the exact four-model strategy. It tests whether the specification's calibration, conservative edge and long-YES selection ideas add value with the strongest comparable frozen inputs we actually possess.

## Fixed evaluation contract

Use the same 64 LAX development dates as V5B-next and V6. The first 20 dates are warmup. Score the following 44 dates at 18:00 UTC. Use only labels published before each decision for fitting. Preserve A, B+ and B execution grades; none is described as a verified hypothetical fill.

One contract pays $1 when its bucket settles YES. Entry outlay is ask plus the exact frozen fee. Aggregate return is total simulated net profit divided by total entry outlay. Multi-bucket trades on the same date share an event but each contract remains a trade; sample and temporal gates use distinct selected dates.

Report central probability Brier score, the inherited calibrated Brier reference, realized return, modeled expected return, selected trades and dates, five chronological folds, evidence composition, fixed-selection adverse-price stress, Grade-A sensitivity and return after removing the best date.

Apply the same conjunctive gates used in the V6 comparison: at least 30 dates, at least 10% realized return, at least 10% mean expected return per contract, four positive folds, worst fold at least -10%, evidence quality at least 0.65, positive +2-cent stress, positive result after removing the best date, and Brier no worse than the inherited calibrated probability reference.

## Explicitly untested parts of the supplied system

The following require data or infrastructure absent from the frozen comparison and receive no performance claim:

- exact historical GFS/GFS-Seamless/NAM/NBM fitted weights and time-varying duplicate lineage;
- intraday station-observation conditioning;
- native NBM probabilistic products;
- historical depth consumption, queue position, partial fills and maker execution;
- full fractional-Kelly quantities and portfolio allocation across cities;
- exits, reconciliation, kill switches, paper operation and live execution.

No network input, current market data, protected confirmation label, credential, paper order or live order is permitted during the test.
