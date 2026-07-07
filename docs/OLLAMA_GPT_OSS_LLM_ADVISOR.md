# Ollama GPT-OSS 120B LLM Advisor

The Ollama advisor is an optional fake-money review layer for the paper model
race. It does not replace the weather models. The weather/model pipeline still
creates high-temperature estimates, bracket probabilities, edges, spreads, and
candidate fake trades.

The LLM reads one compact trade snapshot at a time and returns strict JSON:

```text
weather/model estimates -> probabilities -> edge -> candidate trade -> Ollama advisor -> hard validator -> fake-money fill
```

The hard validator has final veto power. If the LLM says BUY and the validator
says BLOCK, the final action is BLOCK.

## What It Does

- Reviews model estimate, probability, edge, spread, liquidity, signal
  persistence, recent stop/cooldown state, open position state, and risk state.
- Returns one strict JSON recommendation: `BUY_YES`, `BUY_NO`, `SELL`, `HOLD`,
  `WAIT`, `BLOCK`, `REDUCE_SIZE`, or `LONG_HOLD_CANDIDATE`.
- Logs the trade snapshot, raw LLM response, parsed decision, validator result,
  and final fake-money action to JSONL.
- Fails closed to WAIT or BLOCK if Ollama is unavailable, times out, or returns
  invalid JSON.

## What It Does Not Do

- It does not place live Kalshi orders.
- It does not call Kalshi trading endpoints.
- It does not require Kalshi API keys.
- It does not bypass risk limits.
- It does not prove that the model has market edge.

## Configuration

Default provider and model:

```text
provider: ollama
model: gpt-oss:120b
```

Supported environment overrides:

```powershell
$env:KALSHI_LLM_PROVIDER="ollama"
$env:KALSHI_LLM_MODEL="gpt-oss:120b"
$env:OLLAMA_HOST="https://your-ollama-cloud-host"
$env:OLLAMA_API_KEY="your optional Ollama Cloud key"
```

If your Ollama Cloud model is named differently, use `--llm-model` without code
changes.

## Smoke Tests

Rule-only smoke test, no network:

```powershell
kalshi-weather llm-advisor-smoke-test --rule-only
```

Ollama smoke test:

```powershell
kalshi-weather llm-advisor-smoke-test --provider ollama --model gpt-oss:120b
```

If Ollama is unavailable, the command reports a failed live smoke and the
decision safely falls back instead of buying.

## Paper Model Race With LLM Advisor

```powershell
$env:KALSHI_LLM_PROVIDER="ollama"
$env:KALSHI_LLM_MODEL="gpt-oss:120b"

kalshi-weather paper-model-race-run `
  --series KXHIGHLAX `
  --station KLAX `
  --target-date 2026-06-25 `
  --race-id 20260625_lax_llm_advisor `
  --starting-cash-per-model 1000 `
  --race-mode independent `
  --model-worker-mode `
  --model-worker-count 5 `
  --entry-interval-seconds 300 `
  --exit-interval-seconds 30 `
  --cooldown-after-stop-minutes 30 `
  --max-open-positions-per-model 1 `
  --max-risk-per-trade 15 `
  --max-exposure-per-bracket 25 `
  --max-exposure-per-model 50 `
  --use-llm-advisor `
  --llm-provider ollama `
  --llm-model gpt-oss:120b `
  --force-flat-at-end
```

Rule-only mode uses deterministic trade quality and the hard validator without
calling Ollama:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --use-llm-advisor --llm-rule-only
```

Dry-run mode calls/logs the advisor but does not let the LLM alter the model
race action:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --use-llm-advisor --llm-dry-run --duration-minutes 15
```

## Decision Logs

Default path:

```text
reports/llm_advisor_decisions/
```

Each JSONL row includes:

- timestamp
- race ID
- model key
- market ticker and bracket
- compact trade snapshot
- deterministic trade quality
- raw LLM response if available
- parsed LLM decision
- hard validator result
- final fake-money action

Secret-looking fields are redacted before writing logs. Do not bundle `.env`,
SQLite databases, API keys, private keys, or production data in review packages.

## Safety

Live trading remains disabled by default. The advisor output is advisory only,
and fake-money execution can occur only after the existing model race and hard
risk validator approve it.

