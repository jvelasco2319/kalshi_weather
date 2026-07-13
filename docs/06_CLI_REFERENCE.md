# CLI reference target

Codex should wire these commands.

## Help

```powershell
kalshi-weather --help
```

## Markets

```powershell
kalshi-weather markets --series KXHIGHLAX
```

Expected output:

```text
Ticker, title, subtitle/bracket, yes bid, no bid, implied yes ask, implied no ask
```

## Weather snapshot

```powershell
kalshi-weather weather-snapshot --station KLAX
```

Expected output:

```text
Observed high so far, latest obs time, model maxes, blended future high
```

## Prediction once

```powershell
kalshi-weather predict-once --series KXHIGHLAX --station KLAX
```

Expected output:

```text
Each bracket ticker, market price, model probability, edge
```

## Paper once

```powershell
kalshi-weather paper-once --series KXHIGHLAX --station KLAX
```

Expected output:

```text
Signals and fake fills, if any.
```

## Run paper

```powershell
kalshi-weather run-paper --series KXHIGHLAX --station KLAX --interval-seconds 60
```

Expected output:

```text
Continuous loop with snapshots and paper ledger.
```

## Phase 4-7 POC Commands

```powershell
kalshi-weather research-status --series KXHIGHLAX --station KLAX
kalshi-weather daily-maintenance --series KXHIGHLAX --station KLAX
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
kalshi-weather opportunities --series KXHIGHLAX --station KLAX --short
kalshi-weather threshold-sweep --series KXHIGHLAX --station KLAX
kalshi-weather calibration-readiness --station KLAX
kalshi-weather calibration-demo --station KLAX
kalshi-weather tune-residual-sigma --station KLAX
kalshi-weather fit-probability-calibration --station KLAX --dry-run
kalshi-weather model-weight-report --station KLAX
kalshi-weather replay-predictions --station KLAX
kalshi-weather paper-replay --series KXHIGHLAX --station KLAX
kalshi-weather poc-run --series KXHIGHLAX --station KLAX --max-iterations 3
kalshi-weather poc-demo --station KLAX
kalshi-weather poc-check --series KXHIGHLAX --station KLAX
```

These commands remain read-only or fake-money-only. `poc-demo` is fixture data
and not trading evidence.
