# Safety and risk guards

## Default state

```text
KALSHI_ENABLE_REAL_ORDERS=false
```

No code should place live orders while this is false. The first build should not call authenticated create-order endpoints at all.

## Required guardrails

1. Paper mode is default.
2. Live trading code is not implemented in the first end goal.
3. API keys are never committed.
4. `.env` is ignored.
5. Any future live-order function must require:
   - environment variable enabled,
   - explicit CLI flag,
   - max order size,
   - kill switch,
   - dry-run preview.

## Risk limits for paper mode

```text
max_position_per_market
max_order_cost
max_total_exposure
max_daily_loss
max_hold_minutes
```

Risk code should block a simulated trade if these limits are exceeded.
## POC Safety Status

- Fake-money only.
- Live trading disabled by default.
- No authenticated Kalshi order placement.
- No create-order endpoint.
- Outcome storage skips unsettled dates unless an explicit force flag is used.
- Demo outputs are labeled `DEMO DATA - NOT TRADING EVIDENCE`.
- Results packages exclude `.env`, SQLite databases, snapshots, private keys,
  API keys, `.git`, `.venv`, and caches.
