# Historical input format

Use one JSON object per date in a `.jsonl` file. Brackets must be ordered from coldest to warmest.

```json
{
  "date": "2025-07-01",
  "base_probabilities": [0.03, 0.10, 0.24, 0.36, 0.19, 0.08],
  "pressure_gradient_hpa": 2.4,
  "outcome_index": 3,
  "quotes": [
    {
      "bracket_index": 0,
      "ticker": "KXHIGHLAX-example-B0",
      "label": "69 or below",
      "yes_bid_cents": 2,
      "yes_ask_cents": 4,
      "no_bid_cents": 96,
      "no_ask_cents": 98,
      "evidence_grade": "A"
    }
  ],
  "source": "historical_export"
}
```

Required fields are `date` and six `base_probabilities`. Add `outcome_index` to score forecast accuracy. Add all six `quotes` to simulate the trade filter. Add `pressure_gradient_hpa` for V10; missing pressure is treated as neutral.

Valid evidence grades are `A`, `B_PLUS`, and `B`. The engine abstains if price, spread, evidence, probability-gap, or expected-return requirements fail.

