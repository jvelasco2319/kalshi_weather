# Production Wiring

## Current Live Path

```text
record-weather-market-loop
  -> validation journal SQLite
  -> SignalRoomReadRepository
  -> validation snapshot shadow evaluator
  -> /api/v1/signal-room/events/{event}/snapshot
  -> KLAX Signal Room HTML
```

The dashboard remains read-only. `live_trading_enabled`, `taker_enabled`, and
`order_submission_reachable` are all required to be false by
`StrategyConfig`.

## Repaired Inputs

- ECMWF IFS, GFS 0.13, and GFS Seamless come from Open-Meteo.
- NAM and NBM come from NOAA/Herbie.
- Kalshi market rows come from public REST market and orderbook reads.
- Observed high comes from the recorder observation payload when available.

## Shadow Evaluation

The live evaluator requires:

- at least four canonical model states;
- a parseable continuous settlement ladder;
- read-only top-of-book quotes for economics.

When these are present, the API returns:

- five model slots;
- launch-default residual probabilities;
- conservative YES and NO probabilities;
- required probability, EV/ROI, and max acceptable price;
- explicit warning gates for launch calibration and missing executable book depth.

## Limits

This repair intentionally does not submit orders and does not treat public REST
quotes as a sequence-valid executable book. The Probability Lab is suitable for
shadow review while the bot gathers settled current-strategy dates for a real
calibration backfill.
