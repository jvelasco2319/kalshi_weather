# Windows Quick Start Shape

Codex must replace dependency extras and environment-variable names with the repository's actual conventions.

```powershell
cd C:\Users\jarve\Documents\Codex\kalshi_weather

python -m pip install -e ".[dashboard]"

kalshi-weather strategy-doctor --target-date auto --include-next-day

kalshi-weather strategy-shadow-run `
  --target-date auto `
  --include-next-day `
  --serve-dashboard `
  --host 127.0.0.1 `
  --port 8765 `
  --open-browser
```

Expected local URL:

```text
http://127.0.0.1:8765
```

Expected behavior:

- Today and tomorrow events are discovered dynamically.
- Prices and model estimates appear as sources arrive.
- The decision may remain `DATA INCOMPLETE` until state-consistent history is ready.
- No orders are submitted.
