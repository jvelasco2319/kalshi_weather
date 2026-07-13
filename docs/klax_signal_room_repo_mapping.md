# KLAX Signal Room - Repository Mapping

## Baseline

- Active workspace: `C:\Users\jarve\OneDrive\Documents\kalshi_weather`.
- UI add-on copied to: `implementation/klax_signal_room_ui/`.
- Current strategy package present at: `implementation/klax_current/`.
- Package verification: `$env:PYTHONPATH='implementation\klax_current\reference'; python implementation\klax_current\reference\verify_package.py` passed.
- Test baseline: `python -m pytest -q` passed.
- Ruff baseline: `python -m ruff check src tests` passed.
- Existing web stack: none in repository source. `fastapi`, `uvicorn`, `jinja2`, and `playwright` are available in the current environment.

## Responsibility Mapping

| UI responsibility | Existing module to reuse | Minimal extension | New module only if needed | Tests |
|---|---|---|---|---|
| CLI registration | `src/kalshi_weather/cli.py` Typer app | Add `strategy-dashboard` command | `signal_room/cli.py` helper | CLI help/test command |
| Read-only HTTP service | None | Add local loopback service | `signal_room/app.py` | API route/method tests |
| Snapshot read model | `strategy_current.config`, `registry`, `reason_codes`, `promotion` | Map persisted strategy state to UI contract | `signal_room/api_models.py`, `service.py` | schema and serialization tests |
| Repository reads | `SQLiteStore`, `ValidationJournal`, strategy_current SQLite tables | Read immutable records and summarize gaps | `signal_room/repository.py` | empty DB, bounded timeline, ETag |
| Current decision | `strategy_current.shadow_runtime`, `decision_engine`, `reason_codes` | Display persisted/fail-closed decision state | service mapping | no order dependency tests |
| Five model slots | `strategy_current.registry` | Fixed display order/colors/labels | service constants | exact five model tests |
| Model state/timeline | `strategy_current.state_builder`, persistence tables | Read stored model states when present; otherwise honest missing state | repository/service timeline adapters | future cutoff and no interpolation tests |
| Gates/readiness | `strategy_current.reason_codes`, config, persistence manifests | Surface missing/passing/blocking gates from persisted health | service gate builder | severity/status tests |
| Market rows | `market_discovery`, `settlement`, `economics` | Display persisted market rows if present; otherwise unavailable | service rows | monetary strings/null tests |
| Replay fixture | UI add-on prototype data | Add explicit fixture adapter only for test/dev mode | `tests/fixtures/signal_room_july7_replay.json` | replay truth isolated tests |
| Visual layout | `implementation/klax_signal_room_ui/ui_reference/approved_prototype_a.html/png` | Split into local template/static assets | templates/static files | browser smoke/mobile tests |
| Browser tooling | None in repo; Playwright importable | Use lightweight dev-only smoke test | Playwright test file if stable | desktop/mobile screenshot generation |
| Safety boundary | `tests/test_safety_phase2.py`, no live order client | Add dashboard no-order dependency/method tests | app route assertions | GET-only and no order controls |

## Reuse Decisions

- Keep the existing Typer CLI; do not add a second command runner.
- Keep strategy math in `strategy_current`; dashboard reads and formats only.
- Use FastAPI/Jinja2/vanilla JS because no repository web framework exists and the approved prototype needs no frontend build pipeline.
- Reuse existing SQLite path settings and strategy-current tables; do not create a parallel datastore.
- Do not import or construct any order client in dashboard code.

## Data Availability Notes

- The current strategy implementation has config, state, probability, replay, and promotion primitives, but not yet complete live strategy decisions, sequence-valid books, or NBM/Herbie capture.
- Live dashboard mode must therefore render a read-only `DATA_INCOMPLETE` snapshot from persisted config/capture status rather than fabricate probabilities or ROI.
- The July 7 prototype data may be used only through an explicit replay/sample fixture path and must be visibly labeled historical/sample.

## Migration / Configuration Impact

- No write migration is required for UI0/UI1. Existing additive strategy-current tables are enough for empty/partial state.
- Add optional `dashboard` dependencies to `pyproject.toml` for FastAPI/Uvicorn/Jinja2.
- Add local static/template assets under `src/kalshi_weather/signal_room/`.

## Test Impact

- Add backend tests for API schema, five model slots, empty DB health, ETag, route methods, and read-only/no-order guarantees.
- Add frontend smoke tests using TestClient and, where possible, Playwright for desktop/mobile screenshots.
