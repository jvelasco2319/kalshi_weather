# KLAX stage-adaptive five-model weighting — Codex package

This package is an **additive implementation and audit package** for the currently implemented KLAX shadow bot. It does not replace the existing collectors, state-consistent forecast builder, probability engine, Signal Room, or Probability Lab. It changes one responsibility: how the five eligible model distributions are weighted at different market stages.

The five models remain exactly:

- ECMWF IFS
- GFS 0.13°
- GFS Seamless
- NAM
- NBM

The implementation uses:

1. **broad market-stage priors** based on the historical stage pattern;
2. **walk-forward, prior-settled-date probability log loss** to adjust those priors;
3. **shrinkage and recency weighting** so small samples cannot swing the ensemble;
4. **GFS-family, individual-model, and NBM-maturity caps**;
5. **parallel fixed-weight and stage-prior counterfactuals** for honest comparison;
6. a Probability Lab extension that explains every weight from prior to final contribution.

The package is shadow-only. It must not enable live, canary, taker, or order-submission paths.

## Start here

1. Read `CODEX_MASTER_PROMPT.md`.
2. Run `python reference/verify_package.py`.
3. Copy this directory into the existing repository under `implementation/klax_stage_adaptive_weighting/`.
4. Give Codex the complete contents of `CODEX_MASTER_PROMPT.md`.

## Important statistical boundary

The included stage priors are **initial shadow priors**, not proof that a model will always be best at that time. The historical stage comparison used an older forecast-state representation. Codex must preserve the priors as a configurable starting point, rebuild performance from the current state-consistent evaluator, and use only prior settled dates.

## Intended output

For every immutable strategy evaluation, persist and expose:

- market stage and stage-transition state;
- fixed prior, stage prior, and final effective weight for every model;
- stage probability log loss, history count, and effective sample size;
- reliability multiplier;
- every cap and exclusion applied;
- fixed, stage-prior-only, and stage-plus-reliability counterfactual mixtures;
- the primary shadow decision driven by the configured weighting mode.

The Probability Lab must render these backend values; it must not calculate weights in browser JavaScript.
