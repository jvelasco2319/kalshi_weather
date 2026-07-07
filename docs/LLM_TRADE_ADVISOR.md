# LLM Trade Advisor / Confirmed-Edge Trading

This phase keeps the Kalshi weather project fake-money only while adding an advisor gate between edge detection and paper execution.

The old model-race path was mostly:

```text
model edge appears -> fake buy if risk filters pass
```

The advisor path is:

```text
model edge appears
-> trade quality score
-> advisor recommendation
-> deterministic hard-risk validator
-> fake-money execution only if approved
-> advisor decision log
```

The LLM advisor never executes trades. It recommends `BUY_YES`, `BUY_NO`, `SELL`, `HOLD`, `WAIT`, `BLOCK`, `REDUCE_SIZE`, or `LONG_HOLD_CANDIDATE`. The hard validator remains the final authority.

## Advisor Modes

- `off`: legacy model-race behavior. This is the default.
- `rule_based`: deterministic confirmed-edge advisor. No network or external LLM is required.
- `prompt_only`: writes the prompt and advisor input to a JSON artifact for manual review. It does not call an LLM and returns `WAIT`.
- `llm_json`: optional strict-JSON adapter. It is disabled by default and fails closed to `BLOCK` unless a local provider config is supplied.

External LLM calls are not required for imports, tests, or normal operation.

## Trade Quality Score

The score is 0-100 and combines:

- positive components: edge, model confidence, signal persistence, market confirmation, liquidity, and time of day
- penalties: spread, missing bid, stale data, recent stop, overexposure, model disagreement, bracket invalidation, high price, and no exit bid

Interpretation:

- `0-39`: poor, block or wait
- `40-59`: weak, usually wait
- `60-74`: acceptable but below normal buy threshold
- `75-89`: strong, paper buy can proceed only if validator approves
- `90-100`: exceptional, still must pass hard risk rules

## Hard Validator

The validator can veto any advisor recommendation. It blocks new entries for:

- missing ask or required exit bid
- stale market, weather, or model data
- wide spread
- penny/no-liquidity traps unless explicitly allowed
- cooldown after stop loss
- daily fake loss limit
- position or exposure limit
- high entry price with insufficient edge
- bracket invalidation or contradictory observed high
- any live-trading path

## Commands

Run offline advisor edge-case tests:

```powershell
kalshi-weather advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch
```

Run a current-market advisor dry run without opening fake positions:

```powershell
kalshi-weather advisor-dry-run --series KXHIGHLAX --station KLAX --advisor-mode rule_based --json --output reports/llm_trade_advisor/latest_advisor_dry_run.json
```

Run model race with confirmed-edge advisor mode:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id advisor_smoke --starting-cash-per-model 1000 --race-mode independent --advisor-mode rule_based --entry-interval-seconds 300 --exit-interval-seconds 30 --max-entry-iterations 1 --max-exit-iterations 2 --max-open-positions-per-model 1 --cooldown-after-stop-minutes 30 --max-risk-per-trade 15 --force-flat-at-end
```

Summarize advisor behavior:

```powershell
kalshi-weather advisor-decision-report --race-id advisor_smoke
```

Export future training/evaluation examples:

```powershell
kalshi-weather advisor-export-training-examples --race-id advisor_smoke --output-dir reports/llm_trade_advisor/training_examples
```

## Logs

Advisor decisions are stored in SQLite table `advisor_decisions`.

Exports include:

- `advisor_inputs.jsonl`
- `advisor_decisions.jsonl`
- `validator_results.jsonl`
- `labeled_examples.jsonl`

Reports are written under `reports/llm_trade_advisor/`.

## Safety

This phase is fake-money only.

- No Kalshi create-order endpoint is implemented.
- Advisor commands do not place real trades.
- Model race with advisor still simulates fake fills only.
- `KALSHI_ENABLE_REAL_ORDERS=false` remains the default.
- `.env`, API keys, private keys, SQLite DBs, and raw runtime data must not be included in handoff packages.

## Limitations

The advisor makes the paper strategy more selective, but it does not prove profitability. Real confidence requires settled-day scoring, calibration review, quote-history replay, and careful comparison against actual official outcomes.

