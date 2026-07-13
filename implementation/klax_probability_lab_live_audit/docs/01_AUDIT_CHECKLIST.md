# Repository audit checklist

Codex must inspect the real repository before changing code. Mark each item as `present and correct`, `present but incomplete`, `missing`, or `not applicable`, and record the path or symbol.

## Application integration

- Probability Lab route exists in the main application.
- Signal Room navigation links to Probability Lab.
- Route uses the current application shell and authentication/local-access policy.
- No second standalone app or separate strategy runtime was created.
- Desktop and mobile assets are part of the normal package/build process.

## Live and replay data flow

- Latest explainability endpoint exists.
- Evaluation-specific explainability endpoint exists.
- Evaluation index/replay endpoint exists.
- Current event and next-day event can be selected.
- Live update mechanism is connected to new immutable evaluations.
- Replay selection freezes one evaluation.
- Command Center and Probability Lab show the same `evaluationId`.
- No July 7 fixture can appear automatically in live mode.

## Evaluation persistence

- One immutable evaluation header exists.
- Model child rows are keyed by the same evaluation ID.
- Outcome map is versioned and keyed by evaluation/event.
- Mixture rows are persisted.
- Market rows are persisted.
- Economics rows are persisted per market and side.
- Equation trace rows are persisted or deterministically serialized from stored calculation trace objects.
- Gate and capture-health rows are persisted.
- Serializer reads atomically.

## Model fields

For each of the exact five model keys:

```text
ecmwf_ifs
gfs013
gfs_seamless
nam
nbm
```

verify:

- explicit row exists even when unavailable;
- run time;
- source-available time;
- received time;
- remaining-window start and end;
- remaining maximum;
- observed maximum;
- raw live state;
- historical correction;
- corrected point;
- history count;
- effective sample size;
- prior weight;
- effective weight;
- maturity state;
- eligibility and reason;
- scenario temperatures;
- scenario weights;
- per-bracket posterior and conservative Yes/No probabilities;
- source identifiers and freshness.

## Mixture and probability fields

- weighted scenario mixture;
- mixture effective sample size;
- live model spread;
- posterior mean Yes/No by bracket;
- mixture-count lower bound Yes/No;
- weighted component lower bound Yes/No;
- final `pTrade` Yes/No;
- probability values are null when unavailable rather than fabricated;
- posterior mean Yes probabilities sum to approximately one for an exhaustive outcome map.

## Market and economics fields

- verified ordered non-overlapping exhaustive outcome map;
- current Yes and No bid/ask;
- quote timestamp;
- book status, age, and sequence validity;
- fee schedule version;
- fee role and series multiplier;
- quantity;
- price basis (`executable_book`, `top_of_book_quote`, or `unavailable`);
- exact fee;
- execution cost;
- all-in cost;
- `pMean` and `pSafe` used for the selected side;
- required probability;
- expected value;
- modeled net ROI;
- maximum acceptable price;
- active hurdle;
- eligibility and rejection reason;
- server-enumerated price-sensitivity rows.

## Explainability and UI fields

- equation ID;
- human label;
- formula;
- substituted expression;
- result and units;
- missing-input list;
- gate status, severity, code, and message;
- capture-health source status;
- final analysis state;
- final execution state;
- final reason code;
- selected market and side, if any.

## Production UI audit

Compare the actual UI with `ui_reference/approved_probability_lab_exact.html` and confirm these panels:

1. shared header and controls;
2. conservative trade probability card;
3. executable/quote price card;
4. required probability card;
5. probability edge card;
6. modeled net ROI card;
7. decision card;
8. physical-high scenario distributions;
9. model contribution ledger;
10. probability funnel;
11. equation trace;
12. bracket probability matrix;
13. market-versus-weather comparison;
14. price-sensitivity figure;
15. calculation and data-health panel;
16. explanatory section;
17. footer with live/replay context.

## Safety audit

- no order button;
- no create-order route;
- no cancel/replace route;
- no live-order client import in the UI service;
- no production fallback to fixture data;
- no browser-side fee, probability, ROI, or eligibility implementation;
- no external analytics, fonts, CDNs, or telemetry;
- all live order flags remain false.
