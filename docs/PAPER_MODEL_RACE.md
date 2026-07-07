# Paper Model Race

## Confirmed-Edge Advisor Mode

The model race supports an optional fake-money advisor gate:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id advisor_smoke --race-mode independent --advisor-mode rule_based --starting-cash-per-model 1000 --max-risk-per-trade 15 --force-flat-at-end
```

Default `--advisor-mode off` preserves legacy behavior. `rule_based` adds deterministic trade-quality scoring and a hard validator before fake entries. `prompt_only` writes prompt/input artifacts and waits. `llm_json` is optional and fails closed unless configured. The advisor never places live orders.

The newer Ollama path is opt-in with `--use-llm-advisor`:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --use-llm-advisor --llm-provider ollama --llm-model gpt-oss:120b
```

Use `--llm-rule-only` to test the deterministic quality score and hard
validator without calling Ollama. Use `--llm-dry-run` to call and log the LLM
advisor without letting the LLM change the fake-money race action. Decision logs
are written under `reports/llm_advisor_decisions/`.

Use:

```powershell
kalshi-weather advisor-decision-report --race-id advisor_smoke
kalshi-weather advisor-export-training-examples --race-id advisor_smoke --output-dir reports/llm_trade_advisor/training_examples
```

See `docs/LLM_TRADE_ADVISOR.md` for the full guide.

The paper model race is a fake-money comparison system for the KLAX Kalshi
weather project. It gives each model its own $100 paper account and lets each
model independently buy, sell, hold, or skip based on its own probability
estimate.

It is not live trading. It does not call Kalshi order placement. It does not
require trading credentials. It does not blend comparison models into the
production estimate.

## Models In The Race

- `current:current_weighted_blend`
- `open_meteo:best_match`
- `open_meteo:gfs013`
- `open_meteo:gfs_global`
- `open_meteo:gfs_seamless`
- `noaa_herbie:hrrr`
- `noaa_herbie:nbm`
- `noaa_herbie:gfs`
- `noaa_herbie:rap`

If a model is unavailable during a run, it is marked unavailable and does not
open new fake trades. Existing fake positions can still be marked or closed
using current market prices.

## What It Does

For each model and bracket, the race compares the model's probability to the
Kalshi read-only market prices:

```text
YES edge = model p_yes - yes ask
NO edge = (1 - model p_yes) - no ask
```

By default, a new fake trade needs an edge above `0.09`. It also has to pass
risk checks for cash, max risk, max exposure, stale data, bracket invalidation,
daily fake loss, and model outlier status.

## How Fake Money Works

Each model starts with `$100.00`.

The default risk limits are:

- max risk per trade: `$5`
- max exposure per model: `$25`
- max exposure per bracket: `$10`
- max daily fake loss per model: `$10`
- max hold time: `45` minutes
- force-flat time: `17:55` America/Los_Angeles

One model cannot spend another model's cash. One model's win or loss does not
change any other model's account.

## Entry And Exit Rules

The race can buy YES or NO when the edge clears the hurdle.

It can exit when:

- profit target is hit
- stop loss is hit
- edge disappears
- probability drops sharply
- observed high invalidates a bracket
- max hold time expires
- force-flat time arrives
- `--force-flat-at-end` is used at session end

The default behavior allows one open directional fake position per model per
event. This keeps the comparison readable and avoids chasing tiny bracket
changes.

## How To Run

Run one update:

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX
```

Run a 15-minute loop:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --interval-seconds 900 --max-iterations 2
```

Show the leaderboard:

```powershell
kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX
```

Reset the fake race accounts:

```powershell
kalshi-weather paper-model-race-reset --race-id default --confirm
```

## How To Read The Scoreboard

`Cash` is the fake cash available to that model.

`Open P/L` is the fake profit or loss on positions that are still open.

`Closed P/L` is the fake profit or loss already locked in from closed trades.

`Best trade` is the strongest current YES or NO candidate for that model.

`Edge` is the model probability advantage after comparing to the visible ask
price.

`Action` means:

- `bought`: a fake entry was opened
- `sold`: a fake exit was recorded
- `holding`: the model still has an open fake position
- `wait`: no trade cleared the rules
- `blocked`: a trade cleared edge but failed a safety/risk guard
- `skip`: the model had data but no usable new trade
- `unavailable`: the model did not produce a usable estimate

## Why A Model Can Be Blocked

A model can be blocked because it is an outlier, the data is stale, the bracket
has already been invalidated by observed temperature, the model hit exposure
limits, or the fake daily loss guard is active.

RAP can sometimes appear as an outlier because it is a short-range regional
model and can react differently than the broader blend. Outlier status is shown
in the compact output. By default, only the outlier model is blocked from new
entries; other models keep running.

## Reports

Every run writes:

- `reports/model_race/latest_model_race.txt`
- `reports/model_race/latest_model_race.json`
- `reports/model_race/model_race_leaderboard.csv`
- `reports/model_race/model_race_trades.csv`

Loop runs also create:

- `reports/model_race/model_race_YYYYMMDD_HHMMSS/session_summary.txt`
- `reports/model_race/model_race_YYYYMMDD_HHMMSS/session_summary.json`
- `reports/model_race/model_race_YYYYMMDD_HHMMSS/iterations.jsonl`
- `reports/model_race/model_race_YYYYMMDD_HHMMSS/leaderboard_final.csv`
- `reports/model_race/model_race_YYYYMMDD_HHMMSS/trades.csv`

## What Not To Conclude Yet

The race can tell you which model is doing better in fake money over the
current sample. It cannot prove a real market edge from one or two days.

Trust improves only after many settled official outcomes, joined predictions,
calibration checks, and stable fake-money performance across market dates.
## Safer Cadence

The model race is still fake-money only. It now separates two clocks:

- Entry/model refresh clock: default 900 seconds. This refreshes weather model estimates, calculates bracket probabilities, and considers new fake entries.
- Exit-monitor clock: default 60 seconds. This uses the latest stored model probabilities and refreshed market prices to manage existing fake positions without re-running slow direct NOAA/Herbie downloads.

Recommended daily/session command:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax --entry-interval-seconds 900 --exit-interval-seconds 60 --starting-cash-per-model 100
```

When you want fast models to act without waiting for slow direct NOAA/Herbie downloads, add model-worker mode. Each model refresh is scheduled independently, and a fake entry decision is made as soon as that model's own estimate finishes. The command still never places live orders.

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax_workers --race-mode independent --model-worker-mode --model-worker-count 4 --entry-interval-seconds 300 --exit-interval-seconds 60 --starting-cash-per-model 100
```

Fast exit monitoring only:

```powershell
kalshi-weather paper-model-race-exit-monitor --series KXHIGHLAX --station KLAX --race-id 20260623_lax --interval-seconds 60
```

If Ctrl+C leaves fake positions open, flatten by race ID:

```powershell
kalshi-weather paper-model-race-flatten --series KXHIGHLAX --station KLAX --race-id 20260623_lax --confirm
```

Flattening sells only at available bids by default. A missing bid leaves the fake position open and marks it `exit_blocked_no_bid`; it does not invent a sell price.

## Safety Filters

New fake entries require an ask and, by default, a current exit bid. Entries are blocked when spread is too wide, the contract is a penny contract, top-of-book size is too small when size is available, the model estimate is stale, the model is an outlier, global model spread is above 4F, the price is above 80c without an explicit override, or the same model/ticker is in stop-loss cooldown.

When global model spread is between 2F and 4F, max risk per trade is reduced by 50%. Existing fake positions are still monitored for exits when new entries are blocked.

Open P/L is trusted only when a current executable bid exists. If the bid is missing, shell output shows `open P/L n/a | no exit bid`; closed P/L remains realized fake profit/loss.
## Race Modes

The fake-money model race has two modes.

`independent` is the default. Each model gets its own fake-money account and trades its own probabilities. Global model disagreement, model spread, and outlier status are displayed as diagnostics, but they do not block all entries. This is the mode to use when learning which model is useful.

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax --race-mode independent --starting-cash-per-model 100
```

`consensus_guarded` preserves the safer global-spread behavior. It can block entries when the models disagree too much, and it blocks outlier models by default. This mode is for later risk-managed deployment testing.

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id 20260623_lax_guarded --race-mode consensus_guarded --starting-cash-per-model 100
```

Execution filters remain active in both modes: no ask, no exit bid, wide contract spread, penny/no-liquidity contracts, high price, cooldown after stop loss, stale model estimate, exposure limits, and cash limits.
## Offline Synthetic Edge-Case Testing

The paper model race has an offline synthetic test harness for behavior checks.
It does not call Kalshi and does not use live or historical market data.

```powershell
kalshi-weather synthetic-scenarios-build --overwrite
kalshi-weather synthetic-scenario-run --scenario-id clear_yes_profit_target --charts --fail-on-mismatch
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

The synthetic runner feeds controlled fake Kalshi-like brackets, YES/NO
orderbooks, model probabilities, weather ticks, and expected actions into the
same fake-money model-race logic used by live-read collection sessions. It is
useful for checking entry blockers, exits, cooldowns, missing bid handling,
outliers, independent versus consensus-guarded mode, and one-position behavior.

Passing synthetic scenarios means the logic recognized the designed edge cases.
It does not prove real-market edge or profitability.
