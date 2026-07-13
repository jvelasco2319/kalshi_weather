# KLAX Stage-Adaptive Weighting Replay Report

Generated on 2026-07-13 from `journals/lax_model_validation.sqlite` with:

```powershell
python -m kalshi_weather.cli strategy-replay `
  --journal-path journals/lax_model_validation.sqlite `
  --weighting-modes fixed_baseline,stage_prior_only,stage_reliability `
  --bootstrap-samples 200 `
  --json
```

## Scope and data quality

- Strategy: `klax-current-five-model-2026-07-11`
- Weighting revision: `klax-stage-adaptive-weights-2026-07-13`
- Weighting config hash: `6cc92a576edfea961bba1cc9893e08b7ecd60d1019ec6e3ae75e73855f1a908a`
- Settled target dates: 1
- Eligible source evaluations: 49
- Blocked evaluations: 3
- Date-level performance rows persisted: 10
- Incompatible evaluations skipped: 33
- Post-settlement evaluations excluded: 54
- Inconsistent settlement dates: 0

Each target date has equal influence within a stage. Intrastage evaluations are collapsed before a date updates future weights. The single-date confidence intervals are necessarily degenerate and are not evidence that any mode is superior.

## Comparison

| Stage | Mode | Dates | Evals | Log loss | Brier | Calibration error | Temp MAE F | Temp bias F | Candidates | Mean quote ROI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `pre_target` | `fixed_baseline` | 1 | 20 | 1.0863 | 0.5986 | 0.2206 | 1.7748 | -1.7748 | 20 | 52.73% |
| `pre_target` | `stage_prior_only` | 1 | 20 | 1.0374 | 0.5782 | 0.2149 | 1.6246 | -1.6246 | 20 | 49.52% |
| `pre_target` | `stage_reliability` | 1 | 20 | 1.0374 | 0.5782 | 0.2149 | 1.6246 | -1.6246 | 20 | 49.52% |
| `target_02_10` | `fixed_baseline` | 1 | 26 | 1.4451 | 0.7386 | 0.2483 | 2.3993 | -2.3993 | 26 | 77.64% |
| `target_02_10` | `stage_prior_only` | 1 | 26 | 1.4809 | 0.7566 | 0.2509 | 2.4128 | -2.4128 | 26 | 68.37% |
| `target_02_10` | `stage_reliability` | 1 | 26 | 1.4809 | 0.7566 | 0.2509 | 2.4128 | -2.4128 | 26 | 68.37% |

Calibration error is the mean absolute multiclass probability error against the one-hot settled bracket. Quote ROI uses the persisted top-of-book quote and the existing maker-fee economics model.

## Interpretation

No mode is selected as a replay winner. Only one settled target date is available, and `stage_reliability` correctly matches `stage_prior_only` when there is no earlier compatible settled history available to that walk-forward evaluation. The configured primary shadow mode remains `stage_reliability`; this is a configuration choice, not a claim of measured superiority.

Paper realized ROI is `null` for every row because this replay does not prove executable fills. No candle or quote is treated as a fill.
