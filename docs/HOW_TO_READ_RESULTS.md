# How To Read The Results

This project is trying to prove one thing: whether the KLAX high-temperature model can produce probabilities that are more accurate than the Kalshi market, over a meaningful sample, before any real-money discussion happens.

## LLM Trade Advisor Results

Advisor mode adds decision records on top of fake-money model-race fills. Read them in this order:

1. `advisor-decision-report`: counts BUY/WAIT/BLOCK/SELL recommendations, validator vetoes, and average score.
2. `reports/llm_trade_advisor/latest_advisor_dry_run.json`: current candidate inputs and advisor/validator decisions with no fake execution.
3. `advisor_decisions` SQLite table: full input/output/final validator JSON for later training and scoring.
4. `reports/llm_trade_advisor/training_examples/*.jsonl`: exported examples for prompt review or model evaluation.

Important: advisor approval is still fake-money only. A high score means the paper setup was cleaner, not that the market is profitable.

For the Ollama GPT-OSS advisor, also read:

1. `llm-advisor-smoke-test --rule-only`: confirms the deterministic path and validator without network.
2. `reports/llm_advisor_decisions/*.jsonl`: compact trade snapshots, raw LLM response when available, parsed decision, validator result, and final fake-money action.
3. `docs/OLLAMA_GPT_OSS_LLM_ADVISOR.md`: configuration, dry-run mode, and safety notes.

If the LLM recommends BUY but the hard validator blocks, treat the validator as
the final answer. The LLM never has authority to bypass missing bids, cooldowns,
stale data, exposure limits, or other hard risk rules.

## 1. What the system is trying to prove

The system collects Kalshi market prices, KLAX observations, Open-Meteo forecasts, model probabilities, official outcomes, and fake-money paper results. The proof target is not "the code runs." The proof target is "the model beats the market benchmark after official outcomes are joined."

## 2. What Engineering POC Complete Means

Engineering POC complete means the plumbing works: commands run, data is stored, predictions are joinable, reports can be generated, and fake-money safety guards are in place. It does not mean the model has a proven edge.

## 3. What Edge Not Proven Yet Means

Edge is not proven while joined official outcomes are missing or too small. If `prediction_outcomes` is zero, there is no statistical evidence yet. If there are only a few joined rows, the result is a smoke test.

## 4. Why Official Outcomes Matter

The model predicts the official KLAX/LAX high temperature used for settlement-style evaluation. Generic weather app values are not enough. The official outcome is the scorekeeper.

## 5. What prediction_outcomes Means

`prediction_outcomes` is the joined table that connects a stored prediction to the official high. This is where the system knows whether each YES bracket won or lost.

## 6. How to read model-health

Run:

```powershell
kalshi-weather model-health --series KXHIGHLAX --station KLAX
```

Read the overall status first:

- `NOT READY TO JUDGE`: no statistical conclusion is possible.
- `PLUMBING ONLY`: commands work, but evidence is still too thin.
- `EARLY SIGNAL`: some joined outcomes exist, but the sample is still fragile.
- `NEEDS MODEL IMPROVEMENT`: calibration or market benchmark is worse than expected.
- `WATCHLIST`: enough data to monitor, but no clear edge.
- `PAPER-READY`: model is beating the benchmark on enough fake-money validation to consider deeper review, not live trading.

## 7. How To Read calibration-readiness

This answers: do we have enough settled data to score the model?

Important fields:

- `total_predictions`: how many prediction rows exist.
- `official_outcomes`: how many official daily highs are stored.
- `joined_rows`: how many predictions are scored against outcomes.
- `unique_joined_market_dates`: how many separate days are represented.
- `missing_outcomes_by_date`: settled dates that still need official outcomes.
- `unsettled_dates_skipped`: dates that are too new to score.

## 8. How To Read calibration-report

Calibration compares predicted probabilities to actual YES rates. If the model says 70% often, about 70% of those rows should settle YES over a large sample.

If there are fewer than 30 joined rows, treat the report as a plumbing check.

## 9. How To Read residual-report

Residual means:

```text
official_high_f - model_future_high_f
```

Positive residuals mean the official high was warmer than the model. Negative residuals mean the official high was cooler than the model.

## 10. How To Read model-vs-market

This is the trading question. It compares:

- model probability: `p_yes`
- market probability: midpoint of Kalshi YES bid/ask when available

If model Brier is lower than market Brier, the model did better on that sample. If market Brier is lower, the market did better.

## 11. How To Read threshold-sweep

Threshold sweep asks how many fake opportunities would clear different edge hurdles. More trades are not automatically better. A loose threshold can create more bad trades.

## 12. How To Read paper-replay And paper-report

`paper-report` shows actual fake fills recorded by the running paper engine.

`paper-replay` replays stored prediction rows using simplified entry and exit rules.

If no fake trades happened, that can be good discipline: no edge cleared the configured hurdle.

## 13. What Brier score means

Brier score measures probability accuracy. Lower is better. A perfect prediction has Brier score 0. A model that says 90% and loses is punished heavily.

## 14. What Log Loss Means

Log loss also measures probability accuracy. Lower is better. It punishes confident wrong predictions more sharply than Brier score.

## 15. What Residual Mean And Std Mean

Mean residual tells bias:

- positive: model too cold
- negative: model too warm

Residual standard deviation tells spread. If the spread is larger than configured sigma, the model is overconfident.

## 16. What Sample Size Too Small Means

One market date creates many bracket rows, but those rows are correlated. Thirty rows from one day are not the same as thirty independent days. Always check unique market dates.

## 17. What 0 Fake Trades Means

It means no fake trade passed the edge threshold and risk checks. That is not failure by itself. It means the system refused to force trades.

## 18. When To Trust The Model

Trust starts only after:

- many official outcomes are stored
- predictions are joined
- unique market dates are meaningful
- model-vs-market beats the market benchmark
- calibration does not show major overconfidence
- paper replay and paper reports are consistent

## 19. When Not To Trust The Model

Do not trust the model when:

- `prediction_outcomes` is zero
- joined rows are below 30
- unique market dates are below 5
- market benchmark beats the model
- residuals show strong warm/cold bias
- fake P&L is based on only a few trades

## 20. Commands To Run Daily

```powershell
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
kalshi-weather model-health --series KXHIGHLAX --station KLAX
```

## 21. Commands To Run After Settlement

```powershell
kalshi-weather fetch-missing-outcomes --station KLAX
kalshi-weather join-outcomes --station KLAX --overwrite
kalshi-weather calibration-readiness --station KLAX
kalshi-weather calibration-report --station KLAX
kalshi-weather residual-report --station KLAX
kalshi-weather model-vs-market --series KXHIGHLAX --station KLAX
kalshi-weather model-health --series KXHIGHLAX --station KLAX
```

## 22. Example Interpretations

No outcomes yet:
The system is collecting data, but no statistical conclusion is possible.

Model too cold:
Official highs are running warmer than the model future-high estimate.

Model overconfident:
The configured residual sigma is too narrow for the observed errors.

Market beats model:
Do not loosen thresholds. The benchmark is better than the model on current evidence.

Model beats market but sample too small:
Interesting, but not trustworthy yet. Keep collecting settled days.

Paper P&L positive but sample too small:
Nice, but not proof. A few fake trades can be luck.

## 23. Hard Rule

Do not consider real-money trading until joined outcomes and paper replay prove edge over a meaningful sample.

## 24. How To Read Model Estimate Comparison

`model-estimates` is a comparison report, not a trading signal. It shows what
the current production blend and each separate weather feed thinks the KLAX high
will be.

Important fields:

- `future_high_f`: the model's forecast maximum temperature for the remaining
  climate-day window.
- `settlement_high_estimate_f`: the larger of the observed high so far and the
  model's future high.
- `successful`: whether that provider/model produced a usable estimate.
- `error_message`: why a direct provider such as Herbie/NOAA was unavailable.

`model-probabilities` takes each estimate and runs the same probability method
used by the current model. This keeps the comparison fair:

```text
same probability model, different weather-model input
```

If HRRR, NBM, GFS, or RAP are unavailable, that is not a model failure by
itself. It usually means optional Herbie/cfgrib/xarray/ecCodes dependencies or
live NOAA archive access are missing.

Use this command to separate dependency problems from live NOAA product
problems:

```powershell
kalshi-weather direct-noaa-check --station KLAX
```

Direct NOAA model locations:

- HRRR: Herbie model `hrrr`, product `sfc`, field `TMP at 2 m`.
- NBM: Herbie model `nbm`, product `co`, field `TMP at 2 m`.
- GFS: Herbie model `gfs`, product `pgrb2.0p25`, field `TMP at 2 m`.
- RAP: Herbie model `rap`, product `awp130pgrb`, field `TMP at 2 m`.

`model-estimate-score` becomes useful after official outcomes exist. It tells
you whether each model was too warm, too cold, or too early to judge.

Do not blend comparison models into the production trading model until enough
settled dates prove that doing so improves accuracy and edge.

## 25. Simple outputs for daily analysis

Use this first when the raw reports feel too dense:

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX
```

Read it in this order:

- `Current production estimate`: the estimate used by the current production-style probability path.
- `Consensus estimate`: the average of successful comparison model settlement-high estimates.
- `Model range`: how far apart the available model estimates are.
- `Most likely bracket`: the top bracket from the current production model.
- `Data status`: whether the stored outcomes are enough to trust anything statistically.

The `MODEL HIGH ESTIMATES` table answers what each model thinks the final high
will be. The `PROBABILITIES BY MODEL` table answers how those high estimates
translate into Kalshi bracket probabilities.

Use market prices only when you are specifically checking apparent edges:

```powershell
kalshi-weather simple-summary --series KXHIGHLAX --station KLAX --show-prices --show-edges
```

An apparent edge is not proof. If the report says `SMOKE_TEST_ONLY`, the system
has enough data to verify plumbing, not enough independent market dates to trust
the edge.

For weather-only checks:

```powershell
kalshi-weather weather-summary --station KLAX
```

For clean collection progress:

```powershell
kalshi-weather collect-session --series KXHIGHLAX --station KLAX --interval-seconds 60 --duration-minutes 60
```

Use `collect-session --verbose` or `collect-session --debug-json` only when you
need to troubleshoot raw stored objects or provider diagnostics.

## 26. How To Read Kalshi History Charts

Use this after candlesticks have been backfilled:

```powershell
kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date YYYY-MM-DD --backfill-if-missing
```

Start with `price_by_bracket.png`. It answers which Kalshi temperature bracket
became expensive or cheap over time. If one line rises above the others, that
bracket became the market favorite.

`favorite_bracket_over_time.png` reduces the same idea to a simple timeline:
which bracket had the highest market-implied YES midpoint at each point.

`model_vs_market.png` appears when stored model predictions are close enough in
time to the candles. It compares our model probability with the Kalshi market
midpoint. A large gap is only an apparent disagreement, not proof of edge.

`edge_over_time.png` shows model probability minus market price. The hurdle line
is the configured threshold plus buffers. Treat any edge above that line as a
candidate for analysis, not as a trade instruction.

`microtrade-trend-replay` is approximate candle replay. Candles are summaries,
not full orderbook depth, so it cannot prove exact fills. It is useful for
asking whether buying low and selling higher looked possible on historical
candle data.

## 27. How To Read The Paper Model Race

The model race is fake-money only. Each comparison model gets its own $100
account and trades independently from its own probability estimate.

Run one update:

```powershell
kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX
```

Run a 15-minute loop:

```powershell
kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --interval-seconds 900 --max-iterations 2
```

Read these columns first:

- `Est high`: that model's estimated official KLAX high.
- `Top bracket`: the bracket that model thinks is most likely.
- `Best trade`: the strongest YES or NO candidate after market prices.
- `Edge`: model probability minus ask price for that side.
- `Action`: bought, sold, holding, wait, blocked, skip, or unavailable.
- `Cash`: fake cash remaining for that model only.
- `Open P/L`: mark-to-market fake profit or loss on positions still open.
- `Closed P/L`: fake profit or loss already locked in from closed trades.

`blocked` usually means the model passed an edge test but a risk guard stopped a
new entry. Common reasons are outlier status, max exposure, stale data, daily
loss limit, or an invalidated bracket.

This is not proof of edge until enough settled market dates exist. A model can
lead the fake leaderboard for a day because of luck, stale quotes, or a single
outlier forecast.
## Reading Safer Model Race Output

The fake model race now separates `Best trade` from `Action`.

- `Best trade` is the current opportunity the model likes most.
- `Action` is what the fake account actually did after safety gates.
- `blocked: spread` means model disagreement is too wide for new entries.
- `blocked: spread too wide` means the individual contract bid/ask spread is too wide.
- `blocked: outlier` means that model's settlement estimate is too far from the model median.
- `blocked: cooldown 22m` means the same model/ticker recently stopped out.
- `holding / no exit bid` means an old fake position is still open, but there is no executable bid to sell into.

Open P/L is deliberately conservative. If a position has no current bid, the output shows `n/a` instead of positive profit. Closed P/L is the realized fake result from completed fake fills.
## Reading Race Modes

In `independent` mode, model agreement is diagnostic only. A LOW agreement line can still be fine for the race because each model is supposed to trade separately from its own fake account.

Look for:

```text
Race mode: INDEPENDENT - no global spread block
```

That means a model can buy if its own edge and execution filters pass, even if RAP, HRRR, NBM, Open-Meteo, and current_blend disagree.

In `consensus_guarded` mode, model disagreement can block entries:

```text
Race mode: CONSENSUS_GUARDED - new entries blocked because spread > 4F
```

Use independent mode for model discovery. Use consensus-guarded mode for future safer strategy testing.
## Synthetic Edge-Case Results

Use synthetic results to verify algorithm behavior, not profitability.

Run:

```powershell
kalshi-weather synthetic-algo-test --charts --fail-on-mismatch
```

Read first:

- `reports/synthetic_scenarios/summary/synthetic_algo_test_report.md`
- `reports/synthetic_scenarios/summary/scenario_results.csv`
- `reports/synthetic_scenarios/summary/charts/pass_fail_summary.png`
- `reports/synthetic_scenarios/summary/charts/action_confusion_matrix.png`

`Passed: 30` and `Failed: 0` means the current fake-money model-race algorithm
matched the expected action for every built-in synthetic edge case. A failure
means either the algorithm behaved unexpectedly or the synthetic scenario needs
to be tightened so it isolates the intended case.

These results use local fake data only. They do not use real Kalshi prices,
real weather APIs, live orders, API keys, or private credentials.
