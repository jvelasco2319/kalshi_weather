# KXHIGHLAX historical Level-2 archive search

This directory records the V5P search for historical KXHIGHLAX order books at the frozen 18:00 UTC decision time. It is an acquisition audit, not a claim of fills or profitability.

## Conclusion

**Exact byte-verified usable 18:00 UTC dates: 0.**

No requested date currently has an inspected KXHIGHLAX full-depth book spanning 18:00 UTC with adequate timestamp and continuity evidence. The difference between a filename or vendor catalog and usable execution evidence is deliberate: a catalogued file is not promoted until its bytes, ticker, time window, quantities, and gap semantics pass the gates in `acquisition-plan.json`.

## Strongest leads

| Rank | Lead | What is proven | What is missing | Current disposition |
|---|---|---|---|---|
| 1 | PMXT | 28 public 18:00 filenames from 2026-05-14 through 2026-06-10; CC BY 4.0 | Host access, KXHIGHLAX presence, and continuity | Best free bounded probe, but likely diagnostic-only because independent adapter evidence reports no sequences and incomplete deltas |
| 2 | CryptoStruct | Series-specific daily catalog; public rows for 2026-08-13 through 2026-08-31; documented full L2 protocol | An inspected in-window paid file | Best low-cost contingency after a free schema sample; purchase was not attempted |
| 3 | Probalytics | Strong advertised per-market gap detection, resnapshot, and book-hash merge | Public KXHIGHLAX sample or ticker/date coverage manifest | Technically credible but too expensive to subscribe without ticker-level proof |
| 4 | Hugging Face alpha | Actual local KXHIGHLAX rows and L2 schema | Capture stops at 14:26:53 UTC on 2026-07-19 | Useful parser/schema fixture only; no decision-time evidence |

## Most important data-quality finding

The open-source `h5i-db` PMXT adapter independently documents that PMXT Kalshi files have no sequence numbers, can contain incomplete hourly deltas, use delayed flush-style receive timestamps, and reconcile successfully to later snapshots only about half the time. PMXT must therefore pass a strict per-file reconstruction and reconciliation test. Accessibility alone would not make its rows execution-grade.

## Files

- `source-matrix.json`: source-by-source coverage, schema, timing, gap, license, price, and sample evidence. Self SHA-256: `1247750e2a38341332165769bafd4b9f377e58369ee8fdf6ef8040685bbf8d67`.
- `acquisition-plan.json`: bounded next steps and fail-closed gates. Self SHA-256: `45572ec5db00cfdf5a16cddb482bc73934c2f48f2288549272ec7ff2fa8a0da2`.
- `build_search_artifacts.py`: deterministic artifact builder.

## Safety state

No purchase, account login, credential, current/live feed, order, or protected confirmation label was used. No multi-gigabyte irrelevant dataset was downloaded.
