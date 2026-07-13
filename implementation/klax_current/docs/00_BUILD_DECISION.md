# Build Decision

Implement one clean current strategy against the existing repository. Do not implement or migrate v1–v3.

## What July 7 changed

The live forecast variable must be the highest temperature still possible from the evaluation time forward, combined with the maximum already observed. A historical full-day forecast maximum can preserve a peak from a forecast hour that has already passed and can create a confident hot-tail error late in the day.

NBM also demonstrated why the system should preserve model-specific probability mass instead of collapsing every model to one average. On July 7, NBM identified the eventual 73–74°F bracket while the market still priced that outcome cheaply, even though most other models were warmer.

## What July 9 changed

The strategy cannot be evaluated or executed honestly from one-minute candles alone. Trade quantity, cursor completion, synchronized orderbook depth, quote age, model snapshots, and decisions must be persisted. The collector must fail completeness checks rather than treating an empty failure file as proof of success.

## Current design response

- Use the five requested signals only.
- Build a separate empirical predictive distribution for each model.
- Combine the five distributions with historical reliability weights and a GFS-family cap.
- Let NBM contribute progressively as its completed-date history matures.
- Use rolling and recency-weighted residuals rather than a permanent seasonal correction.
- Price every bracket and both sides from conservative probabilities.
- Trade only when exact all-in expected ROI clears the configured hurdle.
- Remain shadow-only until enough joined event days validate probability calibration and execution.
