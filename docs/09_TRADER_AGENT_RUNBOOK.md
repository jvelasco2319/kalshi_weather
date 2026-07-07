# Trader Agent Runbook

## Inspect context

```powershell
kalshi-weather trader-context --series KXHIGHLAX --station KLAX --dry-run --json
```

Use this first. It should print the full context and candidate trade board without requiring an LLM key.

## Ask for a recommendation without execution

```powershell
kalshi-weather trader-recommend --series KXHIGHLAX --station KLAX --llm-provider mock
```

With a real provider integrated later:

```powershell
kalshi-weather trader-recommend --series KXHIGHLAX --station KLAX --llm-provider openai --model YOUR_MODEL
```

## Run fake-money loop

```powershell
kalshi-weather trader-paper-run --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 180
```

The loop should:

1. Build context.
2. Build trade board.
3. Ask LLM trader.
4. Validate decision.
5. Execute through paper broker only.
6. Journal decision.

## Replay

```powershell
kalshi-weather trader-replay --series KXHIGHLAX --station KLAX --date 2026-06-26 --use-recorded-snapshots
```

Use replay to score the LLM trader against deterministic baselines.

## Troubleshooting

If the LLM produces invalid JSON, the validator should fallback to HOLD.

If no LLM key is available, use mock or dry-run mode.

If the trade board is empty, debug the market-bracket mapping and probability-bin labels.

If every candidate is ineligible, inspect fee-adjusted edge and min-edge settings.
