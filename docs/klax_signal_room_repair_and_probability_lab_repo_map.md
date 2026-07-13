# KLAX Signal Room Repair And Probability Lab Repo Map

Generated for the current workspace:

```text
C:\Users\jarve\OneDrive\Documents\kalshi_weather
```

## Live Data And Recording

- `src/kalshi_weather/validation_recorder.py`
  - `record-weather-market-loop` source path.
  - Writes `validation_snapshots`, `validation_model_rows`, `validation_market_rows`, and `validation_observation_rows`.
  - Canonical bracket labels are now derived from actual parsed bounds instead of collapsing every upper-tail contract to `>73`.

- `src/kalshi_weather/data/herbie_client.py`
  - NOAA/Herbie fetch path for NAM and NBM.
  - Used by the validation recorder for current-strategy NAM/NBM model slots.

- `src/kalshi_weather/validation_journal.py`
  - Durable SQLite journal used by the live dashboard.

## Current Strategy Primitives

- `src/kalshi_weather/strategy_current/config.py`
  - Shadow-only current-strategy config and safety validation.

- `src/kalshi_weather/strategy_current/registry.py`
  - Canonical five-model set:
    `ecmwf_ifs`, `gfs013`, `gfs_seamless`, `nam`, `nbm`.

- `src/kalshi_weather/strategy_current/economics.py`
  - Fee, EV, ROI, required price grid, and max acceptable price primitives.

- `src/kalshi_weather/strategy_current/probabilities.py`
  - Historical residual and conservative probability primitives. The live Signal Room repair uses a launch-default deterministic residual evaluator until enough settled current-strategy dates are available.

## Signal Room API And UI

- `src/kalshi_weather/signal_room/evaluation.py`
  - New validation-snapshot shadow evaluator.
  - Requires at least four canonical model states and a complete settlement ladder.
  - Computes quote-only probabilities, conservative YES/NO probabilities, ROI, max acceptable prices, gates, explainability, and Probability Lab payloads.

- `src/kalshi_weather/signal_room/service.py`
  - Wires validation journal snapshots into the evaluator.
  - Snapshot responses now include `probability_lab` and `explainability`.

- `src/kalshi_weather/signal_room/app.py`
  - Read-only FastAPI app.
  - Adds read-only `/probability-lab` and `/explainability` endpoints.

- `src/kalshi_weather/signal_room/api_models.py`
  - Expands `MarketRow` with NO-side economics.
  - Adds `probability_lab` and `explainability` to `SignalRoomSnapshot`.

- `src/kalshi_weather/signal_room/templates/index.html`
  - Adds the Probability Lab section.

- `src/kalshi_weather/signal_room/static/signal_room.js`
  - Renders Probability Lab weights, funnel, equation trace, and sensitivity.

- `src/kalshi_weather/signal_room/static/signal_room.css`
  - Adds compact operator-style Probability Lab styling.

## Contracts And Docs

- `implementation/klax_signal_room_repair_with_probability_lab/contracts/explainability_snapshot.schema.json`
  - Local explainability payload contract, created because the referenced external contract package was missing.

- `implementation/klax_signal_room_repair_with_probability_lab/docs/PRODUCTION_WIRING.md`
  - Notes the current production wiring and remaining limits.

## Tests

- `tests/test_signal_room_api.py`
  - Covers incomplete settlement ladders, complete validation ladders, Probability Lab endpoint, and explainability endpoint.

- `tests/test_validation_recorder.py`
  - Covers Herbie NAM/NBM model rows and no-trading recorder behavior.

- `tests/test_market_discovery.py`
  - Covers market bracket parsing used by the recorder and Signal Room.
