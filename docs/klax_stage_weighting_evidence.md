# KLAX Stage-Adaptive Weighting Completion Evidence

## Delivered scope

The implementation is isolated on branch `adaptive_weighting`. The supplied package is preserved under `implementation/klax_stage_adaptive_weighting/`, and its reference verifier passes.

The production integration adds:

- config-driven PT market stages, priors, transition blending, shrinkage, recency, deterministic caps, and readiness gates in `strategy_current/stage_weighting.py`;
- date-level settled performance backfill and chronological three-mode replay in `strategy_current/stage_analysis.py`;
- additive immutable persistence in `strategy_current/persistence.py`;
- live Signal Room evaluation, repository, service, API, and explainability wiring;
- CLI commands for backfill, status, and replay;
- Probability Lab stage status, weight trajectory, attribution, decomposition ledger, and counterfactual panels;
- focused stage, leakage, cap, persistence, replay, API, browser-renderer, and safety tests.

## Persistence and config

The migration is additive. It creates:

- `strategy_stage_performance`, keyed by strategy, revision, model, target date, stage, and outcome-map hash;
- `strategy_stage_weight_evaluations`, containing one immutable weight snapshot per source snapshot and weighting revision.

The shadow config is `config/stage_adaptive_weights.shadow.yaml`. Safety flags are all false, the individual cap is 35%, the combined GFS cap is 45%, and NBM receives 0% until 10 eligible settled dates.

## Live complete evaluation

Pinned complete evaluation `b40c3ccc30b32a5f04d3` for `KXHIGHLAX-26JUL13` contains all three modes:

- `fixed_baseline`
- `stage_prior_only`
- `stage_reliability` (primary)

Its stage is `target_02_10`, readiness is `PRIOR_ONLY`, and final primary weights are:

| Model | Final weight |
| --- | ---: |
| ECMWF IFS | 32.7027% |
| GFS 0.13 | 27.0000% |
| GFS Seamless | 18.0000% |
| NAM | 22.2973% |
| NBM | 0.0000% |

The weights sum to 1.0. The GFS family total is exactly 45%. NBM is present as a model distribution but its strategy influence is capped at 0% because the maturity threshold is not met. `order_submission_reachable` is false.

At as-of `2026-07-13T17:59:47.735862+00:00`, the Command Center snapshot and the Probability Lab pinned endpoint both return evaluation ID `b40c3ccc30b32a5f04d3`.

The latest live snapshot observed during final audit was correctly `BLOCKED`: NAM had timed out after 900 seconds, and immature NBM could not replace the missing tradable family. The engine emitted zero weights instead of silently using an invalid mixture. This is expected fail-closed behavior.

## API evidence

The live application exposes:

- `/api/strategy/current/events/{ticker}/weighting/latest`
- `/api/strategy/current/events/{ticker}/weighting?evaluation_id=...`
- `/api/strategy/current/events/{ticker}/weighting/history`
- `/api/strategy/current/events/{ticker}/probability-lab/latest`
- `/api/strategy/current/events/{ticker}/probability-lab?evaluation_id=...`

The combined Probability Lab endpoint computes one snapshot per refresh and preserves the strict explainability schema. Persisted evaluation and weight history are read directly from immutable rows. In live timing checks, the latest bundle returned in 0.590 seconds, the 58-row evaluation index in 0.020 seconds, and the 58-row weight history in 0.044 seconds.

## Browser evidence

The live page at `http://127.0.0.1:8765/strategy/probability-lab` was exercised against the July 13 journal. It rendered five model rows, four counterfactual/requirement rows, the stage trajectory, and all analytical charts. Repeated 5-second requests returned HTTP 200, and the UI advanced to new immutable evaluation IDs when recorder snapshots arrived.

- Desktop screenshot: `docs/evidence/adaptive_weighting/probability_lab_live_desktop.png`
- Mobile screenshot: `docs/evidence/adaptive_weighting/probability_lab_live_mobile.png`
- Desktop QA viewport: 1930 by 1454, no document-level horizontal overflow, no zero-size panels.
- Mobile QA viewport reported by the in-app browser: 582 by 1260, no document-level horizontal overflow, no clipped controls.

The browser bundle only fetches and renders backend stage, history, cap, and weight fields. `test_probability_lab_browser_bundle_does_not_contain_strategy_math_or_order_paths` prevents stage/reliability math or order methods from entering the JavaScript bundle.

## Replay evidence

The replay comparison is recorded in `docs/klax_stage_weighting_replay_report.md`. It includes log loss, Brier score, calibration error, temperature MAE and bias, candidate count, and quote-based expected ROI by stage and mode. Paper realized ROI remains null because no valid fill simulator is present.

## Safety evidence

- The weighting module has no exchange client dependency.
- The UI routes are read-only GET routes.
- The config loader rejects enabled trading, canary, taker, or order-submission flags.
- Live API payloads report `order_submission_reachable: false`.
- Browser JavaScript contains no create, cancel, replace, or submit order path.
- The implementation stops at shadow evaluation; canary and live enablement are unchanged.
