# Existing Architecture Reuse Plan

The repository already appears to be a Python package named `kalshi_weather` with a `kalshi-weather` CLI, model workers, shared weather/Kalshi snapshots, direct NOAA/Herbie support, SQLite/JSONL journaling, paper-model race commands, and safety tests.

Codex must inspect and reuse the actual implementations rather than blindly creating this suggested tree.

## Preserve and extend

| Existing responsibility | Reuse requirement |
|---|---|
| `src/kalshi_weather/cli.py` | Register new commands and flags using the existing CLI framework. Do not create a second CLI. |
| Existing Open-Meteo fetchers | Reuse request, retry, caching, units, and model-name mapping. Extend responses to retain complete hourly paths and timestamps. |
| Existing NOAA/Herbie NBM fetcher | Reuse the working NBM path. Preserve its canonical provider identity. |
| Existing KLAX observation parser | Reuse METAR decoding and QC. Add immutable observation events and source timestamps if absent. |
| Trader/paper context builder | Reuse market discovery and shared snapshot construction if it preserves raw fields and as-of timestamps. Do not reuse old probability math. |
| Existing Kalshi REST/WebSocket client | Reuse authentication, signing, reconnect, and endpoint wrappers. Extend trade pagination and orderbook state as required. |
| Existing validation journal | Apply additive SQLite migrations and retain raw JSONL. Do not create an unrelated database unless the current journal cannot support transactions/indexes. |
| Existing paper broker/accounting | Reuse only behind an adapter if settlement payoff and position accounting are correct. Do not reuse old entry probability or edge logic. |
| Existing safety flags/tests | Strengthen them. New shadow mode must have no order-submission dependency. |

## Preferred new namespace when no current module fits

```text
src/kalshi_weather/strategy_current/
    domain.py
    state_builder.py
    residuals.py
    model_weights.py
    probabilities.py
    settlement.py
    economics.py
    risk.py
    decision_engine.py
    shadow_runtime.py
    replay.py
    persistence.py
    reason_codes.py
```

This is a fallback organization, not a requirement. If equivalent modules already exist, extend them with small focused changes.

## CLI additions

Prefer additive commands:

```text
kalshi-weather strategy-shadow-run
kalshi-weather strategy-replay
kalshi-weather strategy-status
kalshi-weather strategy-validate-capture
```

Keep these existing behaviors unchanged:

```text
paper-model-race-run
record-weather-market-once
record-weather-market-loop
analyze-model-validation
```

The recorder commands may gain optional fields/tables needed by the current strategy, but must remain record-only and must not mutate paper or live portfolios.

## Shared snapshot rule

One event trigger should build one coherent immutable snapshot and share it across all model and contract evaluations. Do not launch five independent market fetches that can observe different books at different times.
