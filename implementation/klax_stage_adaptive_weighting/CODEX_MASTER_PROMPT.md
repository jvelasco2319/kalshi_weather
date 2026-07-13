# Codex master prompt — implement stage-adaptive five-model weights

You are working in the existing repository:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

The current bot, Signal Room, and Probability Lab may already be partially or fully implemented. **Audit the actual repository first. Reuse existing architecture and modify only what is necessary.** Do not create a parallel bot, evaluator, database, or web app.

This package is additive. Its sole strategy change is to replace one fixed five-model prior with a transparent, stage-adaptive weighting layer while preserving the existing state-consistent model distributions, probability calculations, economics, gates, and shadow-only safety.

## Non-negotiable safety state

```text
mode = shadow
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
order_submission_reachable = false
```

A real exchange create/cancel/replace/submit method must remain unreachable from the new weighting code and UI.

## First actions — no production edits yet

1. Place this package under:

   ```text
   implementation/klax_stage_adaptive_weighting/
   ```

2. Run:

   ```powershell
   python implementation\klax_stage_adaptive_weighting\reference\verify_package.py
   python -m pytest -q
   python -m ruff check src tests
   ```

3. Inspect the repository's current:
   - five-model strategy configuration;
   - model probability and residual code;
   - immutable evaluation persistence;
   - model-weight calculation;
   - replay/backfill commands;
   - Signal Room and Probability Lab routes;
   - explainability serializer/schema;
   - tests around shadow safety and no-look-ahead.

4. Create:

   ```text
   docs/klax_stage_weighting_repo_audit.md
   ```

   For every requirement in this package, record `present`, `partial`, `incorrect`, or `missing`, the existing path, and the smallest reuse-first change.

5. Do not infer completion from filenames. Exercise the evaluator and API with real or fixture evaluations.

## Authority

1. This package for stage-adaptive weighting behavior.
2. The current five-model strategy package for forecast state, residuals, probabilities, economics, and safety.
3. The current Probability Lab package for visual design and immutable explainability wiring.
4. Existing repository behavior where it does not conflict with the above.

## Exact model set

Only these models may receive strategy weight:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

Do not add `gfs_global`, `nam_conus` as a separate vote, `best_match`, HRRR, RAP, AIFS, or any other model.

Treat `gfs013` and `gfs_seamless` as one GFS family for the 45% family cap.

## Required weighting modes

Persist all three on every evaluation:

```text
fixed_baseline
stage_prior_only
stage_reliability
```

The configured primary shadow mode is `stage_reliability`. The other two are counterfactuals and must not create duplicate shadow positions or decisions.

## Core requirements

### 1. Determine the market stage in America/Los_Angeles

Use the exact stages and boundaries from `docs/02_STAGE_DEFINITIONS_AND_PRIORS.md`.

### 2. Use the configured stage prior

Load priors from `config/stage_adaptive_weights.shadow.yaml`. Do not hardcode them in the evaluator.

### 3. Score model probability performance without leakage

Use model posterior-mean probability for the bracket that actually settled. Do not use conservative lower bounds as a proper scoring distribution.

For each model, target date, and stage, collapse all eligible intrastage evaluations to one date-level stage score before updating weight history. A target date must not contribute dozens of correlated observations to the same stage.

Only include target dates settled strictly before the target date being evaluated.

### 4. Apply shrinkage and recency

Implement the equations in `docs/03_WEIGHTING_MATH.md`. Small stage samples remain close to the stage prior. Current-date outcomes never update current-date weights.

### 5. Apply caps deterministically

- individual model cap: 35%;
- combined GFS family cap: 45%;
- NBM maturity caps from configuration;
- unavailable or ineligible model weight: 0%;
- minimum four eligible feeds and three independent families for a tradable five-model probability.

Weights must sum to 1 within numerical tolerance after caps and redistribution.

### 6. Smooth stage transitions

During the configured minutes after a stage boundary, blend the previous and current stage's unnormalized score vectors before caps. Do not use the next stage before its boundary. The transition must be deterministic and visible in explainability.

### 7. Preserve model distributions

Do not shift point estimates or scenario distributions because a model receives a lower weight. Bias correction remains model-specific and separate. Weighting answers “how much influence,” not “how warm or cool.”

### 8. Persist the weight decomposition

Extend existing immutable evaluation persistence rather than create a duplicate source of truth. Each evaluation must preserve all fields in `contracts/stage_weight_snapshot.schema.json` or a documented equivalent mapping.

### 9. Integrate with the Probability Lab

Use the exact approved Probability Lab HTML in `ui_reference/approved_probability_lab_exact.html` as the visual baseline. Add the weighting panels and fields in `docs/06_PROBABILITY_LAB_UI.md` to the existing main application.

The browser is a renderer only. It must not calculate stage classification, log loss, shrinkage, multipliers, caps, or final weights.

### 10. Replay all three modes

Add or extend a chronological replay command that reports, by mode and stage:

- log loss;
- Brier score;
- calibration error;
- temperature MAE and bias as diagnostics;
- candidate count;
- quote-based expected ROI;
- paper realized ROI only when a valid fill simulator exists.

Do not select the winning mode using July 7 alone.

## Required commands or equivalent existing commands

Adapt names to repository CLI conventions and document them:

```powershell
kalshi-weather strategy-backfill-stage-performance `
  --station KLAX `
  --strategy-id klax-current-five-model-2026-07-11

kalshi-weather strategy-stage-weight-status `
  --target-date auto `
  --include-next-day `
  --json-output

kalshi-weather strategy-replay `
  --weighting-modes fixed_baseline,stage_prior_only,stage_reliability
```

## Required completion evidence

Codex must finish with:

- repository audit and reuse map;
- files changed and why;
- migrations/config changes;
- stage classification tests;
- no-look-ahead tests;
- cap and normalization tests;
- replay comparison report;
- one live shadow evaluation showing all three modes;
- Probability Lab desktop and mobile screenshots;
- API payload containing the weight decomposition;
- proof that the Command Center and Probability Lab share one `evaluationId`;
- proof that browser JavaScript does not implement strategy weighting;
- proof that order submission remains unreachable.

Follow `CODEX_TASK_GRAPH.yaml` in order. Stop before canary or live enablement.
