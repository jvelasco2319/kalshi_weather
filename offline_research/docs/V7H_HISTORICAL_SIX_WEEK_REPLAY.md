# V7H historical six-week replay

V7H is the corrected response to the request for a multi-week historical comparison. The prospective V7 schedule was paused before its first target date and superseded for this task. V7H evaluates 42 consecutive completed LAX high-temperature markets from August 4 through September 14, 2026.

## Causal design

The forecast state ends on August 3. V7H produces every method's probability vector, 18:00 UTC selection, and 18:00:05 arrival result before opening any settlement outcome. The complete prediction and hypothetical-order record is sealed by SHA-256. Scoring then opens the 252 resolved contracts and binds the result to that freeze. Target dates never update a later forecast.

All transactions are one-contract historical simulations held to settlement. They use the date-effective fee calculation and require the exact frozen order to remain executable at the five-second arrival check. A missing paid book causes an abstention. No proxy fill, paper order, or live order is permitted.

## Data coverage

- HRRR/GEFS: 42 of 42 dates.
- GFS/NAM/NBM: 42 of 42 dates.
- Resolved Kalshi contracts: 252 of 252, six mutually exclusive brackets per date.
- Paid Probalytics 18:00 and 18:00:05 books: 40 of 42 dates.
- Explicit unavailable dates: August 6 and August 10.

## Result

V5B was the only method to pass all five preliminary checks. It returned 27.75% on total entry outlay, won seven of nine fills, was positive in two of three fixed 14-day folds, had a +2.99% one-sided 95% moving-block-bootstrap lower bound, returned +23.74% under a two-cent adverse-entry stress, and returned +20.44% after its best trade was removed.

V5F was the strongest supporting method. It returned 25.15%, won six of eight fills, stayed positive under two-cent stress and after removing its best trade, and produced the lowest Brier score. Its bootstrap lower bound was -9.15%, so it did not pass the full screen.

V6 returned -1.46% across 15 fills. The friend's combined GFS/NAM/NBM method returned +29.13%, but it won only once in nine fills and fell to -100% when that one winner was removed. The individual GFS and NAM diagnostics reported very large raw returns because they bought very cheap contracts; their poor win rates, weak forecast scores, and negative bootstrap lower bounds keep them in the diagnostic category.

## Interpretation

This result is useful stability evidence for V5B and corroborating evidence from V5F. It is not a statistically untouched confirmation: these target dates had already influenced earlier V5/V6 research and reporting. V5B also placed no fills in the first 14 days, and each named method generated only 8 to 15 fills. A sustainable 10% expected return is therefore not established.

The next scientific step is to freeze V5B as the primary method and V5F as its supporting comparator, then evaluate genuinely new Grade-A dates without changing either strategy. The existing 100-day independent-confirmation requirement remains appropriate before any move toward online betting.

## Reproducible artifacts

- `configs/v7h_historical_replay.json`
- `runs/replays/v7h-historical-20260804-20260914/prediction-order-freeze.json`
- `runs/replays/v7h-historical-20260804-20260914/scored-results.csv`
- `runs/replays/v7h-historical-20260804-20260914/method-summary.csv`
- `runs/replays/v7h-historical-20260804-20260914/validation/validation.json`
- `runs/replays/v7h-historical-20260804-20260914/validation/weekly-results.csv`
- `runs/replays/v7h-historical-20260804-20260914/validation/fourteen-day-fold-results.csv`
- `runs/replays/v7h-historical-20260804-20260914/validation/V7H_VALIDATION.md`

Run the independent check from the project root:

```powershell
$env:PYTHONPATH = 'src;.'
.\.venv\Scripts\python.exe scripts\validate_v7h_replay.py --project-root .
```
