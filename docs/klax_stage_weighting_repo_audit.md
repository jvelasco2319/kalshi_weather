# KLAX stage-adaptive weighting repository audit

Date: 2026-07-13

Branch: `adaptive_weighting`

Package: `implementation/klax_stage_adaptive_weighting/`

## Baseline

- Package verification: passed (14 reference tests).
- Repository tests: passed (`python -m pytest -q`).
- Repository lint: passed (`python -m ruff check src tests`).
- Production behavior changed during P0: no.
- Safety state: shadow mode; live, canary, taker, and order submission disabled.

## Requirement audit

| Requirement | Status | Existing path | Smallest reuse-first change |
|---|---|---|---|
| Exact five-model set | present | `strategy_current/registry.py` | Reuse `CANONICAL_MODEL_KEYS` and existing family metadata. |
| Fixed baseline prior | present | `config/strategy_current.shadow.yaml`, `strategy_current/config.py` | Preserve it as the `fixed_baseline` counterfactual. |
| Stage-prior configuration | missing | No production stage-weight config | Add and validate `config/stage_adaptive_weights.shadow.yaml`; persist its hash and revision. |
| PT market-stage classifier | missing | Timezone handling exists elsewhere, but no weighting-stage classifier | Add a deterministic timezone-aware classifier with boundary and DST tests. |
| Post-boundary smoothing | missing | No stage transition representation | Blend previous/current unnormalized vectors for configured post-boundary minutes before caps. |
| Per-model distributions remain unchanged | present | `strategy_current/probabilities.py`, `signal_room/evaluation.py` | Insert weighting after model probabilities are built and before `_mixture_probabilities`. |
| Current reliability weighting | incorrect | `strategy_current/probabilities.py::reliability_weights` | Replace its non-stage aggregate counts with date-level, stage-specific, recency-weighted summaries. |
| Proper settled-bracket score | missing | Per-model posterior means exist in `signal_room/evaluation.py` | Score the realized bracket from per-model posterior means, never conservative bounds. |
| One score per model/date/stage | missing | Immutable validation snapshots exist | Collapse eligible intrastage evaluations and upsert one stable performance row. |
| No look-ahead | partial | Residual history excludes current target date in `strategy_current/residuals.py` | Enforce `score_target_date < target_date` and `settled_at <= evaluated_at` in weighting queries. |
| Recency and effective sample size | partial | Reusable math exists in `strategy_current/residuals.py` | Apply the package half-life and n-eff equations to stage performance rows. |
| Shrinkage and reliability multiplier | partial | A non-stage shrinkage approximation exists in `probabilities.py` | Implement package equations and prior-only readiness thresholds. |
| Individual, GFS-family, and NBM caps | partial | Cap helpers exist in `probabilities.py`; live `_launch_weights` only caps GFS | Centralize deterministic cap redistribution and expose every applied cap. |
| Availability and independent-family gates | partial | Feed-count gates exist in evaluator/config | Add zero weight for unavailable feeds and block below four feeds or three families. |
| Three weighting modes per evaluation | missing | Current evaluator emits one fixed mixture | Compute all modes atomically; only configured primary mode drives the shadow decision. |
| Immutable weight decomposition | missing | Capture rows are immutable; no weight-evaluation child table | Extend existing SQLite schema with evaluation-linked weight snapshots and stage performance rows. |
| Configuration/code provenance | partial | Strategy config hash and code revision already exist | Add weighting revision/hash and persist both with every weight evaluation. |
| Counterfactual economics | missing | Existing quote economics are reusable | Evaluate all mode mixtures for analysis without creating extra candidates or positions. |
| Probability Lab backend contract | partial | Canonical explainability API already uses one evaluation ID | Add backend weighting snapshot, counterfactuals, history, caps, and equation rows. |
| Probability Lab visual baseline | partial | Existing Lab matches the approved structure | Add stage header, contribution ledger fields, weight path, attribution, and counterfactual panel. |
| Browser is renderer only | present | `static/probability_lab.js` contains presentation code only | Continue to pass computed backend values; add tests banning weighting math in JavaScript. |
| Command Center/Lab evaluation linkage | present | Both use `probability_lab.evaluation_id` | Extend tests to assert weighting snapshot and both screens share the same ID. |
| Chronological three-mode replay | missing | `strategy_current/replay.py` only counts source events; CLI replay is a stub | Extend replay with walk-forward mode/stage metrics and uncertainty. |
| Stage-performance backfill command | missing | Validation journal and settled observation rows exist | Add an idempotent command over immutable snapshots and official outcomes. |
| Stage-weight status command | missing | `strategy-status` exists | Add target-date/current-stage status with optional next-day output. |
| Live immutable evaluation | partial | Recorder persists immutable source snapshots; dashboard evaluates them | Persist one weight snapshot per evaluation and expose it through live API/UI. |
| Shadow-only safety | present | Safe config validation, `ShadowOrderSink`, read-only GET routes | Keep order methods unreachable and add weighting-specific negative assertions. |

## Reuse map

1. `validation_recorder.py` and `validation_journal.py` remain the capture source of truth.
2. `signal_room/evaluation.py` remains the evaluator; adaptive weighting is injected only at the existing model-mixture boundary.
3. `strategy_current/probabilities.py` remains responsible for combining unchanged model distributions.
4. Existing economics, gates, decision selection, and shadow sink remain unchanged for the primary mode.
5. `signal_room/explainability.py` remains the canonical backend serializer.
6. Existing Signal Room and Probability Lab routes remain the only web application.
7. Existing SQLite files receive additive tables; no parallel database is introduced.

## Planned additive records

- `strategy_stage_performance`: one immutable/upserted model/date/stage score with source evaluation IDs and settlement provenance.
- `strategy_stage_weight_evaluations`: one immutable evaluation-linked JSON snapshot containing all three modes and the full weight decomposition.

Old evaluations without these records remain readable and render a documented prior-only fallback.
