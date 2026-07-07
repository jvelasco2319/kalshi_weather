# Synthetic Kalshi-Like Edge Cases

This package includes an offline synthetic test harness for the fake-money
model-race strategy. It creates Kalshi-like LA high-temperature bracket markets
without calling Kalshi, NWS, Open-Meteo, or any other network provider.

The LLM Trade Advisor phase adds a separate offline advisor suite:

```powershell
kalshi-weather advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch
```

It covers missing exit bids, cooldown after stops, one-shot signals, clean confirmed edge, open-position exits, stale data, wide spreads, high prices, long-hold separation, independent versus guarded risk, and malformed LLM JSON. It uses no network and no real trading.

Synthetic tests are not backtests and do not prove profitability. They are
controlled edge-case checks: given a known bracket market, known YES/NO prices,
known model probabilities, known liquidity, and known expected behavior, the
real model-race algorithm should buy, sell, wait, block, skip, or exit for the
right reason.

## What The Scenarios Mimic

Each scenario is saved as JSON and includes:

- Mutually exclusive high-temperature brackets.
- Kalshi-style YES/NO bid and ask prices in decimal dollars.
- Bid/ask consistency: `yes_ask = 1 - no_bid` and `no_ask = 1 - yes_bid` where possible.
- Missing bids and illiquid contracts.
- Volume and open interest fields.
- Synthetic temperature observations and official final high.
- Per-model estimates and bracket probabilities.
- Expected fake-money actions and final account state checks.

The scenario schema lives in:

```text
src/kalshi_weather/synthetic/scenarios.py
```

The offline adapters and runner live in:

```text
src/kalshi_weather/synthetic/providers.py
```

## Build Scenarios

```powershell
kalshi-weather synthetic-scenarios-build --scenario-set model_race_edge_cases --output-dir data/synthetic_scenarios/model_race_edge_cases --overwrite
```

This writes:

- `manifest.json`
- `scenario_index.csv`
- 30 scenario JSON files

No real Kalshi or weather API is called.

## List Scenarios

```powershell
kalshi-weather synthetic-scenarios-list --scenario-dir data/synthetic_scenarios/model_race_edge_cases
```

Use `--json` when another tool needs exact scenario IDs without table wrapping.

## Run One Scenario

```powershell
kalshi-weather synthetic-scenario-run --scenario-id clear_yes_profit_target --charts --fail-on-mismatch
```

The runner loads one scenario, feeds its ticks into the real fake-money
model-race logic, compares actual actions to expected actions, and writes a
scenario result under `reports/synthetic_scenarios/<scenario_id>/`.

## Run All Scenarios

```powershell
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

The full runner writes:

- `reports/synthetic_scenarios/summary/synthetic_algo_test_report.md`
- `reports/synthetic_scenarios/summary/scenario_results.csv`
- `reports/synthetic_scenarios/summary/synthetic_algo_test_summary.json`
- charts under `reports/synthetic_scenarios/summary/charts/`

The current built-in set has 30 scenarios and is expected to pass 30/30.

## Scenario Coverage

The built-in set covers:

- Clear YES profit target.
- Clear NO profit target.
- Edge below hurdle.
- Missing exit bid at entry.
- Missing bid on an open position.
- Wide spread blocking.
- Penny/no-liquidity blocking.
- High entry price blocking.
- High price override.
- Stop loss and cooldown.
- Edge disappearing.
- Probability drop exit.
- Weather invalidating a bracket.
- Max hold exit.
- Force-flat exit.
- Independent mode with high model spread.
- Consensus-guarded spread blocking.
- Outlier watch in independent mode.
- Explicit outlier blocking.
- Unavailable models.
- Stale models.
- Exit-monitor-only ticks.
- One-position-per-event behavior.
- Valid sell-then-buy rotation.
- No fabricated profit when no bid exists.
- Too-cold model miss case.
- Market repricing before settlement.
- Market moving against the model.
- Boundary/rounding sanity.
- Mutually exclusive bracket settlement sanity.

## How To Read Pass/Fail

A scenario passes only when:

- The scenario JSON validates.
- Exactly one bracket settles YES.
- Expected actions are observed at the expected tick for the expected model.
- Final fake account checks pass.
- No live trading flags are enabled.

A mismatch means the algorithm did something different from the scenario's
expected behavior. That can mean a real algorithm issue, or it can mean the
synthetic market setup needs to isolate the edge case more cleanly.

## Charts

The full runner generates:

- `pass_fail_summary.png`
- `action_confusion_matrix.png`
- `sample_edge_over_time.png`
- `sample_trade_actions.png`
- `sample_account_equity.png`

It also generates a per-scenario chart folder for each test under:

```text
reports/synthetic_scenarios/summary/scenario_runs/<scenario_id>/charts/
```

Open this index to browse all tests with chart thumbnails:

```text
reports/synthetic_scenarios/summary/scenario_chart_index.html
```

Each scenario folder contains:

- `price_path.png`
- `model_probabilities.png`
- `edge_over_time.png`
- `trade_actions.png`
- `account_equity.png`

These charts summarize synthetic behavior only. They are useful for debugging
the algorithm, not for estimating live profitability.

## Adding A Scenario

Add a new case in `built_in_scenarios()` or create a JSON file matching the
`SyntheticMarketScenario` schema. Keep these rules:

- Prices must be between `0.0` and `1.0`.
- Bids must not exceed asks.
- YES/NO quotes should be complementary where possible.
- Missing bids should be explicit `null`.
- Only one final bracket should settle YES.
- Expected actions should state the behavior being tested.

Then run:

```powershell
python -m pytest tests/test_synthetic_scenarios.py
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

## Safety

Synthetic commands use local JSON and local SQLite scenario state only. They do
not call real Kalshi APIs, do not place live orders, do not require API keys,
and do not change production trading behavior. The project remains fake-money
only.
