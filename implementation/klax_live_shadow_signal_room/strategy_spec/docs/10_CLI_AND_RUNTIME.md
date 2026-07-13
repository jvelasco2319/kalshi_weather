# CLI and Runtime

Add commands through the existing `kalshi-weather` CLI framework.

## `strategy-shadow-run`

Runs the event-driven five-model strategy with a `ShadowOrderSink`.

Suggested flags:

```text
--series KXHIGHLAX
--station KLAX
--target-date auto
--journal-path ...
--strategy-config ...
--once
--until-local-time 18:00
--json-output
```

It must print a compact summary: evaluation time, model availability, forecast center/interval, top conservative opportunities, selected shadow action or reason, and data-health status.

## `strategy-replay`

Replays immutable events chronologically. It must distinguish:

- forecast-only scoring;
- top-of-book opportunity analysis;
- depth-aware taker simulation;
- synchronized-book maker simulation.

It must refuse to label candle-only results as executable.

## `strategy-status`

Reports capture health, latest source ages, orderbook validity, model history counts, NBM maturity, current model weights, deployment flags, and kill-switch state.

## `strategy-validate-capture`

Runs hard completeness tests over a date range and exits nonzero on critical defects.

## Existing commands

Keep current recorder and paper commands backward compatible. Do not silently change their historical outputs or trading semantics.
