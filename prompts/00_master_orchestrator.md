# Prompt 00 — Master orchestrator

You are Codex acting as the executor for this repository. ChatGPT is the strategy/architecture owner. Do not deviate from the files and prompts unless explicitly asked.

## Goal

Build a Python package in the current working directory for a Kalshi weather paper-trading system.

Initial market:

```text
Kalshi series: KXHIGHLAX
Question: Highest temperature in LA today?
Station: KLAX / Los Angeles International Airport
Mode: fake money only
```

## Non-negotiables

1. Do not place real orders.
2. Do not implement live authenticated trading in this phase.
3. Use public read-only Kalshi market/orderbook data.
4. Use NWS observations and Open-Meteo weather model data first.
5. Preserve point-in-time data by writing snapshots before decisions.
6. Write tests for every core math function.
7. Keep the code simple and runnable on Windows.
8. After each numbered prompt, run tests and report exactly what passed/failed.

## Execution order

Implement these prompts one at a time:

```text
01_project_bootstrap.md
02_kalshi_market_data.md
03_weather_ingestion_open_meteo_nws.md
04_lax_high_temp_algorithm.md
05_paper_trading_engine.md
06_live_paper_runner.md
07_backtest_and_replay.md
08_calibration_and_model_improvement.md
09_quality_gate.md
```

## Stop rule

If a test fails, fix it before moving to the next prompt. If an external API response differs from expected docs, add robust parsing and log the raw response shape, but do not hard-code a brittle workaround without tests.
