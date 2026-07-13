# KLAX Probability Lab — live implementation audit package

This package tells Codex to inspect the current `kalshi_weather` repository, verify whether the approved Probability Lab is really implemented, and add or repair every missing piece in the main application.

## Source of truth

The exact approved UI is included unchanged at:

```text
ui_reference/approved_probability_lab_exact.html
```

It is the original approved HTML design, including all approved panels and interactions. Its embedded July 7 data and JavaScript calculations are demonstration fixtures only. Codex must preserve the visual and interaction design while replacing the fixture and decision-critical calculations with live immutable backend strategy evaluations.

## Install

Extract this package into the repository as:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\implementation\klax_probability_lab_live_audit
```

Then give Codex the complete contents of:

```text
implementation\klax_probability_lab_live_audit\CODEX_MASTER_PROMPT.md
```

Run the package verifier first:

```powershell
python implementation\klax_probability_lab_live_audit\reference\verify_package.py
```

## Package contents

- `CODEX_MASTER_PROMPT.md` — authoritative audit-and-implementation prompt.
- `ui_reference/approved_probability_lab_exact.html` — exact approved UI source.
- `ui_reference/approved_probability_lab.png` — visual reference.
- `contracts/explainability_snapshot.schema.json` — required live payload contract.
- `fixtures/sample_explainability_snapshot.json` — test-only fixture.
- `docs/01_AUDIT_CHECKLIST.md` — repository audit checklist.
- `docs/02_BACKEND_CONTRACT_AND_WIRING.md` — live and replay wiring requirements.
- `docs/03_UI_IMPLEMENTATION_MAPPING.md` — panel-by-panel field mapping.
- `docs/04_ACCEPTANCE_TESTS.md` — completion and test matrix.
- `reference/verify_package.py` — package integrity and contract verifier.

## Important boundaries

This package does not authorize live trading. The Probability Lab is read-only. It must not expose or reach create, cancel, replace, or submit-order APIs.

The sample fixture must never become a silent production fallback. When live data or calibration are unavailable, the page must show the exact partial or blocked state.
