# Independent Model Race Status

- Canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Tests passed: yes (`python -m pytest`, 169 passed)
- Ruff passed: yes (`python -m ruff check .`)
- CLI help passed: yes (`python -m kalshi_weather.cli --help`)
- Live trading enabled: false
- Live order endpoint present: false
- Fake-money only: true
- Default race mode: independent
- Independent mode implemented: yes
- Consensus-guarded mode implemented: yes
- Global spread block disabled in independent mode: yes
- Outlier block disabled in independent mode by default: yes
- Execution/liquidity filters still active: yes
- Compact output updated: yes

## Latest Independent Test Summary

Independent mode is the default. It still computes agreement, spread, and outlier diagnostics, but it does not block all models when spread is high. Models trade from separate fake-money accounts when their own edge and execution filters pass.

Live/read-only fake-money command result: `independent_test` ran in independent mode and multiple models bought fake positions. Output included `Race mode: INDEPENDENT - no global spread block`.

## Latest Consensus-Guarded Test Summary

Consensus-guarded mode remains available with `--race-mode consensus_guarded`. In that mode, high global model spread can block new entries, and outlier blocking is enabled by default.

Live/read-only fake-money command result: `consensus_test` ran in consensus-guarded mode. The observed live spread during this command was MEDIUM, so no global block was needed; output included `Race mode: CONSENSUS_GUARDED - model agreement guards active`.

## Commands Run

- `python -m pytest tests\test_model_race.py -q`
- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `kalshi-weather paper-model-race-reset --race-id independent_test --confirm`
- `kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id independent_test --starting-cash-per-model 100 --race-mode independent`
- `kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id consensus_test --starting-cash-per-model 100 --race-mode consensus_guarded`
- `kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX --race-id independent_test`

## Known Limitations

- Independent mode is for model discovery, not final deployment risk management.
- Direct NOAA/Herbie availability still depends on read-only provider/network success.
- Execution filters can still block a model-specific trade even when model disagreement does not.

## Next Recommended Work

- Run daily independent races with unique race IDs.
- Join fake fills and model estimates to official outcomes after settlement.
- Compare P/L, calibration, and error by model before deciding whether any model deserves production weight.
