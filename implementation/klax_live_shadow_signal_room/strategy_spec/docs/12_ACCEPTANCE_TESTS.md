# Acceptance Tests

## Repository and compatibility

1. Existing test suite passes before and after changes.
2. `paper-model-race-run` behavior is unchanged unless a separate approved refactor is documented.
3. Recorder commands remain record-only.
4. Only one CLI entry point exists.
5. No duplicate Open-Meteo, Herbie, or Kalshi client is introduced when a correct client already exists.

## Model set

6. The strategy registry contains exactly ECMWF IFS, GFS013, GFS Seamless, NAM, and NBM.
7. `nam_conus` maps to NAM and never creates an extra vote.
8. GFS Global, Best Match, current blend, HRRR, RAP, direct GFS, AIFS, and all other feeds are absent from strategy calculations.
9. Provider changes create separate source variants and histories.
10. GFS family combined weight never exceeds 0.45.
11. NBM weight follows the completed-date maturity caps.

## As-of state

12. A past forecast maximum cannot survive after its valid time.
13. `source_available_at > evaluated_at` is rejected.
14. `received_at > evaluated_at` is rejected.
15. Observed maximum floors every model state and physical scenario.
16. Historical reconstruction uses the same function as live inference.
17. Legacy full-day scalar residuals cannot satisfy current certification.

## Probability

18. Residual windows contain prior target dates only.
19. One target date counts as one independent outcome.
20. Recency weights and effective sample size match reference vectors.
21. Model-specific distributions preserve minority probability mass.
22. Conservative Yes and No probabilities are no greater than their posterior means.
23. Mixture probabilities sum to one within tolerance.
24. The final trade probability is no greater than either conservative mixture method.
25. Reliability weights are calculated only from prior settled dates.

## Market and economics

26. Nonempty trade pulls missing `count_fp` fail.
27. Trade pagination must end with an empty cursor.
28. An orderbook sequence gap invalidates the book and blocks new candidates.
29. Candles cannot satisfy an executable-book gate.
30. Settlement rules must be mutually exclusive, exhaustive, and verified.
31. Fee arithmetic uses fixed point and exact rounding.
32. The 100-contract fee and price-ceiling reference vectors pass.
33. Both Yes and No are evaluated.
34. Event-level loss is checked under every settlement bracket.

## Deployment safety

35. Shadow mode has no real order client dependency.
36. Live, canary, and taker flags default false.
37. Missing model state, invalid book, stale book, unverified rules, unverified fees, or insufficient history returns an explicit NO_TRADE reason.
38. Every decision is reproducible from persisted source IDs and config hash.

## July regressions

39. July 7: a 76.3°F forecast point from a past hour cannot remain the 3pm future maximum.
40. July 7: NBM may preserve a low-bracket minority probability without overriding all models.
41. July 9: missing model snapshots prevent a profitability claim rather than being imputed.
42. July 9: all-null trade quantities fail capture validation.
43. July 9: contracts above the exact ROI price ceiling are rejected even with probability 1.
