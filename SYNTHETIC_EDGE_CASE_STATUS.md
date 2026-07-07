# Synthetic Edge Case Status

## Summary

- Canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Tests passed: yes, `178 passed, 2 warnings`
- Ruff passed: yes
- CLI help passed: yes
- Live trading enabled: false
- Live order endpoint present: false
- Synthetic scenarios implemented: yes
- No real Kalshi API used: yes
- Scenario count: 30
- Scenarios passed: 30
- Scenarios failed: 0
- Key failures: none
- Scenario output path: `data/synthetic_scenarios/model_race_edge_cases`
- Reports path: `reports/synthetic_scenarios/summary`
- Charts generated: yes

## What Was Added

- `src/kalshi_weather/synthetic/scenarios.py`: JSON-serializable synthetic scenario schema and 30 built-in edge cases.
- `src/kalshi_weather/synthetic/providers.py`: offline providers and scenario runner that feed local fake data into the real fake-money model-race logic.
- CLI commands:
  - `synthetic-scenarios-build`
  - `synthetic-scenarios-list`
  - `synthetic-scenario-run`
  - `synthetic-algo-test`
- `tests/test_synthetic_scenarios.py`: offline regression tests for generation, validation, runner behavior, CLI behavior, mismatch failure, and safety.
- `docs/SYNTHETIC_EDGE_CASES.md`: user and ChatGPT review guide.

## Edge Cases Covered

- YES and NO profit-target exits.
- Edge below hurdle waits.
- Missing exit bid blocks entry.
- Missing bid on open position blocks fake exit and does not fabricate profit.
- Wide spread blocks entry.
- Penny/no-liquidity blocks entry.
- High entry price blocks unless override edge is large enough.
- Stop loss and cooldown.
- Edge disappearance.
- Probability drop.
- Weather invalidation.
- Max hold.
- Force flat.
- Independent mode with high model spread.
- Consensus-guarded high-spread block.
- Outlier watch and explicit outlier block.
- Unavailable and stale models.
- Exit-monitor-only tick.
- One-position-per-event.
- Valid sell-then-buy rotation.
- Too-cold model miss case.
- Market repricing and market moving against model.
- Boundary/rounding sanity.
- Mutually exclusive bracket settlement sanity.

## Commands Run

```powershell
python -m pytest
python -m ruff check .
python -m kalshi_weather.cli --help
python -m pip show kalshi-weather
python -m kalshi_weather.cli synthetic-scenarios-build --scenario-set model_race_edge_cases --output-dir data/synthetic_scenarios/model_race_edge_cases --overwrite
python -m kalshi_weather.cli synthetic-scenarios-list --scenario-dir data/synthetic_scenarios/model_race_edge_cases
python -m kalshi_weather.cli synthetic-scenario-run --scenario-id clear_yes_profit_target --charts --fail-on-mismatch
python -m kalshi_weather.cli synthetic-algo-test --charts --fail-on-mismatch
rg -n "create-order|orders|real order|live order|KALSHI_ENABLE_REAL_ORDERS|private_key|api_key|trade_api|submit|place_order|CreateOrder|requests.post|httpx.post|submit_order|send_order" src tests README.md docs config scripts .env.example
```

## Safety Confirmation

- Fake-money / paper-trading only: true
- Synthetic commands call real Kalshi API: false
- Synthetic commands place real trades: false
- Authenticated Kalshi order placement present: false
- Kalshi create-order endpoint present: false
- `.env` included in packages: false
- API keys included in packages: false
- Private keys included in packages: false

## Known Limitations

- Synthetic pass/fail verifies behavior on controlled fake cases; it does not prove live profitability.
- Synthetic prices are Kalshi-like but not historical Kalshi data.
- The chart set is summary-oriented; richer per-scenario chart templates can be added later.
- The pandas environment still emits optional dependency warnings for old `numexpr` and `bottleneck`; tests still pass.

## Next Recommended Work

- Add synthetic cases for partial fills, close-auction behavior, and multiple simultaneous markets.
- Compare synthetic scenarios against real collected snapshots after enough settled dates exist.
- Continue scoring model-race fake fills against official outcomes before trusting any model ranking.
