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
- [ ] Validate automatic NWS CLI product parsing against several more settled KLAX dates.
- [ ] Improve Open-Meteo provider selection for HRRR/NBM if endpoint aliases remain rejected.
- [ ] Add empirical residual sampling by station/month/as-of hour after several logged days.
- [ ] Add catalyst-aware exit logic around new KLAX observations.
- [ ] Add true mark-to-market paper unrealized P&L using stored quote history.

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
