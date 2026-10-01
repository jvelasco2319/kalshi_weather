# V9 meteorological successor goal

## Decision and boundary

V9 is a frozen research protocol for a stronger physical-weather successor to V8. It is not a fitted strategy and cannot replace the immutable V8 primary candidate until it obtains the required data, passes chronological development gates, and later passes a genuinely new one-shot economic confirmation.

V8 remains unchanged. V9 begins from the exact frozen V8 probabilities and tests whether observed and forecast meteorology explains systematic LAX temperature errors without using future outcomes or market returns to select a weather model.

## Why V9 exists

The exposed 2025 study found physically plausible patterns:

- Offshore-like days numbered only 20, but actual highs averaged 2.71°F warmer than HRRR and 3.36°F warmer than GEFS.
- On 65 persistent-low-cloud days, HRRR averaged 2.49°F too cold while GEFS was almost unbiased.
- On 112 mornings where low cloud cleared, HRRR averaged 1.03°F too cold while GEFS averaged 1.61°F too warm.
- Weak or mixed coastal flow was the hardest 38-day regime; frozen V8 bracket accuracy was 21.05%.
- Frozen V8 accuracy was 41.11% in summer and 23.21% in winter.
- Season plus the first marine-layer proxy improved development accuracy from 34.65% to 37.08%, Brier from 0.7507 to 0.7445, and log loss from 1.5188 to 1.5000. Both block-bootstrap intervals narrowly crossed zero, so the result is a hypothesis rather than a stable improvement.

## Required upgrade

A stronger successor must add observed KLAX ceiling and cloud data, humidity or dewpoint, inversion strength, and an inland-to-coast pressure gradient.

V9 therefore requires as-of archived KLAX observations through the 18:00 UTC decision, HRRR forecast dewpoint and vertical temperatures, and KLAX-minus-KDAG sea-level pressure. The exact fields, transformations, thresholds, and source-separation rules are fixed in `configs/v9_meteorological_successor.json`.

## Model architecture

The base probability vector remains frozen V8. A finite six-candidate catalog tests:

1. unchanged V8;
2. season plus observed marine-layer hierarchical calibration;
3. offshore-flow plus inversion hierarchical calibration;
4. a regularized continuous physical residual model;
5. a combined partially pooled physical model; and
6. the combined model with a predeclared uncertainty abstention policy.

Every development prediction is expanding-window and uses only earlier outcomes. Forecast candidates are selected using Brier score, log loss, chronological consistency, block-bootstrap uncertainty, and modal accuracy. Kalshi returns are forbidden from forecast-model selection.

## Completion and promotion

Completing V9 development requires a frozen candidate that beats V8 on both proper probability scores, improves at least four of five folds on each score, assigns no realized outcome zero probability, preserves modal accuracy, and has paired block-bootstrap upper bounds below zero for both Brier and log-loss differences.

Even a development winner remains research-only. Economic confirmation requires the unchanged V5B NO selector, strict 18:00 UTC Grade-A books, exact fees, a five-second fill check, at least 100 genuinely new Grade-A days, at least 40 fills, four positive folds, at least 10% return after fees, a positive bootstrap lower bound, and positive cost and concentration stresses. Confirmation is one-shot and cannot trigger retuning.

## Safety

All scoring is offline. V9 cannot use current or live market feeds, place paper or live orders, read later observations as decision-time features, or claim a confirmed edge from exposed development results.
