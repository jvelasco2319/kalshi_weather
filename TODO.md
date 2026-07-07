# TODO

## Completed MVP

- [x] Prompt 01: package bootstrap, config, CLI surface.
- [x] Prompt 02: read-only Kalshi public markets/orderbooks and orderbook math.
- [x] Prompt 03: KLAX weather ingestion from NWS and Open-Meteo.
- [x] Prompt 04: LAX high-temperature bracket probability model.
- [x] Prompt 05: fake-money paper broker, risk checks, ledger persistence.
- [x] Prompt 06: bounded continuous fake-money runner with JSON snapshots.
- [x] Prompt 07: offline replay over saved snapshots.
- [x] Prompt 08: baseline calibration metrics and placeholder report command.
- [x] Prompt 09: tests and Ruff quality gate.

## Next Priorities

- [x] Add official outcome storage and manual record fallback for settled market dates.
- [x] Join stored predictions to official outcomes and produce calibration reports over joined rows.
- [x] Add per-model Open-Meteo request handling with explicit success/failure diagnostics and generic fallback.
- [x] Add collect-only commands.
- [x] Add paper-report command.
- [x] Fix fixed UTC-8 NWS local-standard-time market-date handling.
- [x] Add outcome range fetch, missing-outcome backfill, and parser validation commands.
- [x] Add Open-Meteo model alias probing.
- [x] Persist and restore paper account state across CLI runs.
- [x] Add max daily paper loss and total exposure limits.
- [x] Add opportunity diagnostics command.
- [x] Add residual-report placeholder.
- [x] Add model-health scorecard for daily operational validation.
- [x] Add model-vs-Kalshi benchmark over joined prediction/outcome rows.
- [x] Add Windows automation wrappers for collection, after-settlement joining, and model-health.
- [x] Add beginner guide for reading results.
- [x] Remove external LLM summary automation from the standard workflow.
- [x] Add comparison-only current/Open-Meteo model estimates.
- [x] Add optional direct NOAA/Herbie model estimates with graceful unavailable states.
- [x] Add model-provider-probe, model-estimates, model-probabilities, and model-estimate-score commands.
- [x] Add sidecar storage for model_estimates and model_estimate_probabilities.
- [x] Add simple-summary/model-summary for concise model and probability analysis.
- [x] Add weather-summary for concise weather-only output.
- [x] Simplify collect-session default output while preserving --verbose and --debug-json.
- [x] Add Kalshi candlestick history storage and read-only backfill commands.
- [x] Add Kalshi trend tables, PNG chart generation, and static dashboard output.
- [x] Add approximate candle-based microtrade trend replay.
- [x] Add fake-money model race microtrading with separate $100 model accounts.
- [x] Add offline synthetic Kalshi-like edge-case scenarios and runner.
- [x] Add fake-money LLM Trade Advisor / confirmed-edge gate with rule-based mode, hard validator, decision logs, synthetic tests, dry-run, reports, and training export.
- [ ] Validate automatic NWS CLI product parsing against several more settled KLAX dates.
- [ ] Improve Open-Meteo provider selection for HRRR/NBM if endpoint aliases remain rejected.
- [x] Validate direct NOAA/Herbie live retrieval on a machine with Herbie/cfgrib/ecCodes installed.
- [ ] Score comparison models across many settled dates before considering any blending.
- [ ] Score model-race fake fills across many settled market dates before trusting any model ranking.
- [ ] Revisit direct NOAA fetch speed and cache behavior after several collection days.
- [ ] Add empirical residual sampling by station/month/as-of hour after several logged days.
- [ ] Add catalyst-aware exit logic around new KLAX observations.
- [ ] Add true mark-to-market paper unrealized P&L using stored quote history.
- [ ] Add more synthetic cases for multi-day carry, partial fills, and market close edge cases.
- [ ] Compare synthetic failure modes against real collected quote snapshots after enough settled days exist.
- [ ] Score advisor decisions against settled outcomes before trusting confirmed-edge thresholds.
- [ ] Review exported advisor training examples after several full fake-money days.

## Safety

- [x] Keep live trading disabled.
- [x] Do not implement Kalshi create-order calls.
- [x] Keep `.env`, private keys, SQLite files, and snapshots out of git.

## Phase 4-7 Follow-Ups

- [ ] Collect at least 30 settled joined rows before interpreting calibration.
- [ ] Collect at least 100 settled joined rows before fitting production probability calibration.
- [ ] Review Open-Meteo model-weight diagnostics after joined rows exist.
- [ ] Improve paper replay with denser intraday snapshots and settlement-aware exits.
- [ ] Keep demo/fixture results separate from production evidence.
- [ ] Treat any live-readiness discussion as a separate project with authentication,
      order guardrails, kill switches, approvals, and independent review.
- [ ] Collect enough settled official outcomes to move model-health beyond NOT READY TO JUDGE.
- [ ] Review model-vs-market only after at least 30 joined rows across 5 market dates.
- [ ] Keep improving human-readable reports without treating clearer output as proof of edge.
- [ ] Validate Kalshi live/historical candlestick endpoints across more settled market dates.
- [ ] Compare candle-based replay against denser quote snapshots before drawing execution conclusions.
## Safer Model Race Follow-Ups

- Keep using unique race IDs per day/session, for example `--race-id 20260623_lax`.
- After interrupted fake-money runs, use `paper-model-race-flatten --race-id <race_id> --confirm` to close only positions with executable bids.
- Consider adding a richer market-depth parser if Kalshi exposes top-of-book quantity in more response shapes; current size checks only apply when size fields are available.
- Review stop-loss cooldown and high-price thresholds after several settled days of fake-money data.
## Independent Model Race Follow-Ups

- Use `--race-mode independent` for model discovery and daily model-vs-model comparisons.
- Use `--race-mode consensus_guarded` only when testing a future risk-managed strategy where model disagreement should block entries.
- After several settled days, compare fake P/L by model against official outcomes to identify which providers add signal.
- Keep execution filters active while tuning thresholds; independent mode removes global model-disagreement blocking, not liquidity safety.
