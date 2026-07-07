# Prompt 09 — Quality gate

Make the repo reliable enough for daily fake-money operation.

## Tasks

1. Run:

```powershell
pytest
ruff check .
```

2. Fix failures.
3. Confirm `.env` and private keys are ignored.
4. Confirm the CLI cannot place real orders.
5. Confirm read-only commands still work with no API keys.
6. Confirm the paper runner logs enough context to debug every decision.
7. Update README with any changed commands.
8. Add a `TODO.md` with remaining priorities.

## Acceptance criteria

```text
All tests pass.
No secret files committed.
No live-order path enabled.
README is accurate.
```
