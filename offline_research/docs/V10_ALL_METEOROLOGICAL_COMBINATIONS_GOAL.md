# V10 all meteorological combinations goal

V10 implements the frozen V9 physical-weather direction without modifying frozen V8. It acquires the missing bounded historical inputs and evaluates every subset of six predeclared feature blocks. The complete catalog contains 64 subsets at three fixed calibration weights plus the unchanged V8 control, for 193 candidates.

The six blocks are season, forecast marine layer, observed marine layer, pressure and coastal flow, inversion and moisture, and HRRR/GEFS uncertainty. Candidate probabilities are generated chronologically from strictly earlier outcomes. Market returns do not select the forecast model.

HRRR's surface product does not provide the registered 950 hPa temperature field. V10 records that absence before scoring and uses the predeclared 925 hPa minus 2 m temperature substitution. No other field, threshold, weight, or model family may be added after outcomes are scored.

V10 is exposed development research. A winner must beat frozen V8 on Brier and log loss, improve at least four of five chronological folds on both scores, preserve modal accuracy, assign no realized outcome zero probability, and have paired block-bootstrap upper bounds below zero for both score differences. A favorable development result would still require a separate frozen economic replay and genuinely new confirmation.

All acquisition is finite and historical. Scoring is offline. V10 cannot use current or live market feeds or place paper or live orders.
