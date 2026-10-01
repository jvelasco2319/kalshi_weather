# V5P public execution-evidence acquisition

This namespace is separate from frozen V5. It records a bounded, public-source
search for historical `KXHIGHLAX` order-book evidence between 2025-07-01 and
2026-08-31, centered on the registered 18:00 UTC decision time.

## Result

No promotion-usable 18:00 UTC evidence was acquired.

- **PMXT:** its public catalog advertises hourly Kalshi Parquet order-book files
  from 2026-05-14 14:00 through 2026-06-11 03:00 UTC. Twenty-eight catalog dates
  can have an 18:00 file. Six visible examples total 403.8 MB, so the preliminary
  estimate for all 28 files is about 1.88 GB (actual sizes must be read from the
  catalog before acquisition). The archive and object hosts were unreachable from
  this computer during the run: the archive timed out and the local network filter
  blocked PMXT object domains. No PMXT file was downloaded or represented as
  validated.
- **Hugging Face `lerchen3/kalshi-orderbook-alpha`:** the pinned public revision
  contains 164 files (3,471,950,518 bytes), all from 2026-07-19 02:20:23 through
  14:26:53 UTC. A 10,747,904-byte range census found `KXHIGHLAX` in the feed, and
  one bounded full-file probe confirmed book records around 02:24:52-02:25:23 UTC.
  The source ends more than three hours before 18:00 UTC, so the 3.47 GB corpus was
  not acquired as campaign evidence.
- **PredictionMarketBench:** its public tree contains only `KXHIGHNY-26JAN20`, not
  `KXHIGHLAX`. The settlement file was not opened.
- **Other public Hugging Face candidates:** the PMXT mirror stops before Kalshi was
  added; the large Becker mirror contains Kalshi markets and trades but no depth;
  the only other order-book search result is a sports top-of-book sample.

## Safety and evidence rules

- Public historical HTTP files only; no authentication or paid source.
- No live WebSocket, exchange write, paper order, or real order.
- No settlement, result, confirmation-label, or protected-final path was read.
- Probe and capability artifacts are content-hashed in
  `manifests/public-probe-manifest.json`.
- `manifests/coverage-conclusion.json` is fail-closed. The partial campaign cannot
  claim executable fills until a source file both contains `KXHIGHLAX` and spans
  the registered decision window.

`acquire_hf_lax.py` is a pinned, filtered extractor retained for reproducibility.
It was intentionally interrupted once the census proved that the source does not
reach 18:00 UTC; no full-corpus manifest or extracted campaign data was produced.
