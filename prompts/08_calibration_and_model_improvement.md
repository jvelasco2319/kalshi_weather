# Prompt 08 — Calibration and model improvement

Improve the model only after the paper loop works.

## Tasks

1. Add storage for final official outcomes when available.
2. Create a dataset of:

```text
asof timestamp
observed high so far
model future high predictions
market bracket
model probability
final official high
settled YES/NO
```

3. Implement basic calibration diagnostics:
   - Brier score
   - log loss
   - calibration buckets
4. Add a configurable residual sampler:
   - global residuals first
   - station/month/asof-hour residuals later
5. Add a calibration report command:

```powershell
kalshi-weather calibration-report
```

## Acceptance criteria

```powershell
kalshi-weather calibration-report
```

Expected:

```text
If outcomes exist, print calibration metrics.
If not enough outcomes exist, print a clear “need more data” message.
```

## Do not do

Do not add complex ML until the baseline has several days of logged predictions and outcomes.
