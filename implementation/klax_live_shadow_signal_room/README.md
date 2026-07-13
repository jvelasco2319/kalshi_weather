# KLAX Live Shadow Signal Room — Codex Integration Package

This package turns the approved Prototype A into a runnable live shadow system. It includes the authoritative current five-model strategy specification, the approved UI reference, and one master Codex prompt that requires end-to-end wiring.

## Use only this prompt for the live integration

```text
CODEX_LIVE_SHADOW_SIGNAL_ROOM_PROMPT.md
```

The prompt instructs Codex to reuse the existing repository architecture, implement the current five-model algorithm if it is not already present, run continuous data collectors, automatically discover the current and next KLAX markets, persist immutable decisions, and serve the approved read-only UI.

## Suggested placement

Extract into:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\implementation\klax_live_shadow_signal_room
```

Then give Codex the contents of the master prompt.

## Intended command after implementation

```powershell
kalshi-weather strategy-shadow-run `
  --target-date auto `
  --include-next-day `
  --serve-dashboard `
  --host 127.0.0.1 `
  --port 8765 `
  --open-browser
```

The runtime is shadow-only and must have no reachable order-submission path.
