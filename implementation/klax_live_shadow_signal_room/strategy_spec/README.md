# KLAX Kalshi Trading Bot — Current Five-Model Codex Package

This is the **only implementation package Codex should use**. The older v1, v2, v2 amendment, and v3 packages are research history and are not prerequisites.

The package is designed for the existing Python repository at:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

It preserves the current `kalshi-weather` CLI, existing weather and Kalshi clients, SQLite/JSONL journaling, record-only validation commands, and paper-trading safety boundaries wherever they are correct. It adds new code only where the current architecture cannot express the state-consistent forecasting, probability, market-data, and risk requirements.

## Current model set

The strategy may use **exactly these five canonical signals**:

1. `ecmwf_ifs`
2. `gfs013`
3. `gfs_seamless`
4. `nam`
5. `nbm`

Existing aliases and other feeds may remain in the repository for backward compatibility, but they must not enter this strategy. In particular, do not use `current_weighted_blend`, `best_match`, `gfs_global`, `nam_conus` as an additional vote, HRRR, RAP, direct GFS, AIFS, or any other model.

## Core decision

Build a state-consistent, event-driven, shadow-first bot:

```text
full forecast paths + KLAX observations + exact market state
    -> remaining-window model states
    -> model-specific empirical probability distributions
    -> reliability-weighted five-model mixture
    -> conservative Yes/No probabilities
    -> exact fee-aware ROI and portfolio risk
    -> shadow candidate or explicit NO_TRADE reason
```

The bot must not infer a trade from a nearest temperature bracket or a point estimate alone.

## Start here

1. `CODEX_MASTER_PROMPT.md`
2. `docs/00_BUILD_DECISION.md`
3. `docs/01_EXISTING_ARCHITECTURE_REUSE_PLAN.md`
4. `docs/02_MODEL_REGISTRY_AND_POLICY.md`
5. `docs/03_FORECAST_STATE_AND_ASOF_MATH.md`
6. `docs/04_MODEL_PROBABILITIES_AND_MIXTURE.md`
7. `CODEX_TASK_GRAPH.yaml`
8. `docs/12_ACCEPTANCE_TESTS.md`

## Verify the package

```bash
python -m pip install -r reference/requirements.txt
PYTHONPATH=reference python reference/verify_package.py
```

## Hard defaults

```text
strategy_id = klax-current-five-model-2026-07-11
mode = shadow
live_trading_enabled = false
canary_enabled = false
taker_enabled = false
order_submission_reachable = false
```

Improved weather accuracy is an input to expected value. It is not a guarantee of a 10% return.
