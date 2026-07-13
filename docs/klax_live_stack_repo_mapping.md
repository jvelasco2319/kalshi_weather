# KLAX Live Shadow Stack Repo Mapping

## Baseline

- Active workspace: `C:\Users\jarve\OneDrive\Documents\kalshi_weather`.
- Authority package copied to: `implementation/klax_live_shadow_signal_room/`.
- Strategy reference verification passed:
  `python implementation\klax_live_shadow_signal_room\strategy_spec\reference\verify_package.py`.
- Current recorder journal path: `journals/lax_model_validation.sqlite`.
- Existing recorder writes `validation_*` tables, not `strategy_current_*` decision tables.

## Responsibility Mapping

| Responsibility | Existing module to reuse | Extension made / needed | Current reason when unavailable |
|---|---|---|---|
| CLI registry | `src/kalshi_weather/cli.py` | Existing `strategy-dashboard`; future `strategy-shadow-run --serve-dashboard` still needed | `NO_TRADE_CAPTURE_INCOMPLETE` |
| Recorder loop | `validation_recorder.py`, `validation_journal.py` | Reuse as live observe-only collector for dashboard display | source-specific warning rows |
| Dashboard API | `signal_room/app.py` | Reused existing versioned API | n/a |
| Read repository | `signal_room/repository.py` | Added read-only `validation_*` fallback methods | `NO_MARKET_AVAILABLE` |
| Snapshot service | `signal_room/service.py` | Added validation-journal snapshot adapter | `NO_TRADE_PROBABILITY_UNCALIBRATED` |
| Five model slots | `strategy_current/registry.py` | Filters validation rows to exactly `ecmwf_ifs`, `gfs013`, `gfs_seamless`, `nam`, `nbm` | `NO_TRADE_TOO_FEW_MODELS` |
| Market prices | `validation_market_rows` | Display fixed-point string prices from recorder rows | `NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE` |
| Observed high | `validation_observation_rows` | Display high-so-far when recorder has accepted observations | source warning |
| Probabilities/economics | `strategy_current/probabilities.py`, `economics.py` | Not wired from validation journal; blocked honestly | `NO_TRADE_PROBABILITY_UNCALIBRATED` |
| Executable order book | Existing Kalshi REST top-of-book recorder | Sequence-valid ten-level book not present in validation journal | `NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE` |
| Order safety | `ShadowOrderSink`, signal-room GET-only routes | Dashboard imports no order submission path | `ORDER_PATH_DISABLED` |

## Current Wiring State

```text
record-weather-market-loop
  -> validation_snapshots / validation_model_rows / validation_market_rows / validation_observation_rows
  -> SignalRoomReadRepository validation fallback
  -> SignalRoomService DATA_INCOMPLETE snapshot
  -> Prototype A dashboard
```

This is live read-only display wiring for current recorder data. It is not yet
the full prompt's continuous calibrated shadow decision stream.

## Remaining Full Live-Stack Work

- Persist current-strategy immutable decisions into `strategy_current_decisions`.
- Persist current-strategy model live states into `strategy_current_model_states`.
- Add real event discovery for today/tomorrow tabs through Kalshi event metadata.
- Add sequence-valid depth book stream or explicit REST fallback worker state.
- Wire calibrated residual history, probabilities, economics, and candidate rejection matrices.
- Extend `strategy-shadow-run`/add `strategy-live-stack` to supervise collectors plus dashboard in one command.
