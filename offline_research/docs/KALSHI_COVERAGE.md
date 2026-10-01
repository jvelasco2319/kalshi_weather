# Historical Kalshi source audit

Acquired 2026-09-25 UTC for finite offline LAX research. No market outcomes, settlement values, or profitability were inspected in this audit. Raw archived responses necessarily retain retrospective fields and must be partitioned before discovery workers receive data.

## Verified coverage

| Source | Observed result |
|---|---|
| KXHIGHLAX full historical pagination | 4 pages; 3,396 archived contracts returned |
| Requested interval | 2024-01-01 through 2025-12-31 |
| Intersection with returned metadata | 2,166 contracts; 361 dates; 2025-01-05 through 2025-12-31 |
| Contract status and station screen | All 2,166 selected contracts finalized; primary rules explicitly identify Los Angeles Airport and a climate report |
| Exploratory HIGHLAX predecessor query | Complete one-page response containing zero markets |
| Pilot hourly candles | 2025-01-05 through 2025-01-11; all 42 contracts downloaded; 790 rows |
| Total initial cached source bytes | 8,205,240 bytes, including both metadata queries and pilot candles |
| Completed hourly archive | 2025-01-05 through 2025-12-31; all 2,166 eligible contracts; 72,390 hourly rows |
| Final verified source files and bytes | 2,171 immutable responses; 31,625,315 bytes; all content hashes and byte counts passed offline verification |

The requested 2024 training interval is absent from these responses. The HIGHLAX check does not prove no other legacy identifier or source exists. It only records that this explicit candidate returned no data. Do not silently treat 2024 as covered or select the replacement interval using profits. An exact revised split must be registered from source adequacy before discovery.

The first pilot was chosen because it is the earliest seven consecutive station-screened dates, without inspecting outcomes. Six contracts exist on each of the 361 covered dates. The observation bootstrap's 2020–2025 span is longer than the verified market span.

The subsequent full-year job completed on 2026-09-25 UTC. All twelve monthly batches finished with every selected contract downloaded; no contract returned an empty or unavailable candle response. A separate offline file-integrity audit completed at 2026-09-25T01:48:31 UTC. Its summary excludes outcomes and return calculations. Contract-level availability does not imply that every hourly interval has a quote.

| Candle interval | Contracts | Hourly rows |
|---|---:|---:|
| January 5–31 | 162 | 3,287 |
| February | 168 | 4,099 |
| March | 186 | 4,795 |
| April | 180 | 4,995 |
| May | 186 | 6,287 |
| June | 180 | 6,412 |
| July | 186 | 6,606 |
| August | 186 | 7,022 |
| September | 180 | 7,005 |
| October | 186 | 7,404 |
| November | 180 | 7,060 |
| December | 186 | 7,418 |
| Total, excluding duplicate pilot references | 2,166 | 72,390 |

January–June contains 1,062 contracts across 177 weather days and 29,875 rows. July–December contains 1,104 contracts across 184 weather days and 42,515 rows. These are availability counts, not outcome statistics. The coordinator's proposed weather-only 2024 calibration, January–June 2025 selection and July–December 2025 protected evaluation remains conditional on weather-source coverage and the registered evaluation plan.

## Files and provenance

`data/manifests/kalshi_downloads.json` maps SHA-256 hashes of request URLs to immutable raw responses in `data/raw/kalshi/`. Each successful entry records source URL, retrieval time, HTTP status, byte count, content hash and relative path. Failure attempts are preserved separately. A missing historical availability timestamp is explicitly null: retrieval now is not proof of what was available at a past decision time.

`data/manifests/kalshi_coverage.json` records complete KXHIGHLAX pagination, requested coverage, missing dates, per-day contract counts and a safe static metadata projection. `kalshi_coverage_HIGHLAX.json` records the empty exploratory predecessor query. Neither summary includes settlement labels, expiration values or retrospective market-price fields.

`data/manifests/kalshi_candles_2025-01-05_2025-01-11.json` maps each pilot contract to its source response and candle count. Candle objects have `end_period_ts`, `yes_bid`, `yes_ask`, `price`, `volume`, and `open_interest`. Bid and ask objects contain open/high/low/close aggregate fields. The source documents prices as decimal dollar strings. Normalization must parse these explicitly and reject unrecognized units or invalid values.

The twelve full-period `kalshi_candles_START_END.json` files provide the same mapping monthly. `kalshi_integrity.json` records the completed deduplicated audit, zero missing eligible candle tickers, and hashes of the download and coverage manifests. Pilot inspection found no missing bid/ask closes, all hour-aligned timestamps, and only 5–33 candle rows per contract; replay must therefore enforce observed coverage and staleness rules rather than interpolate assumed executable quotes.

The complete series metadata endpoint has no historical date-range filter; pagination also returns archived contracts beyond the chosen 2024–2025 interval. Those records are retained as untouched source evidence and excluded from the selected-coverage projection. Source request cursors are opaque pagination tokens, not account credentials.

## Economic evidence limitations

Hourly bid/ask candles support **Grade B, assumption-dependent replay**. They do not establish executable size, quote persistence, latency-adjusted fills, or full historical order-book depth. Candle highs, lows and past trade prints cannot be assumed to be prices available at a chosen decision instant. A row ending after the decision is future information. Using a prior candle close still requires explicit staleness and execution assumptions.

Archived contract metadata provides rule text as retrieved; a historical version effective at each simulated decision is not independently established. This screen verifies explicit station identity, not every rounding or climate-day detail. Contract threshold semantics, outcome reconciliation and official daily reports still require the target audit.

No historical fee schedule, maker/taker adjustment history, order-book depth or trade-history stream was acquired in this subtask. Present-day costs cannot silently substitute for historical fees. Missing fee provenance requires labeled sensitivity scenarios. The downloaded price sample establishes technical availability only, not a strategy's gains.

## Implemented finite acquisition interface

From the repository with its Python environment:

```text
python src/klax_lab/acquire_kalshi.py metadata
python src/klax_lab/acquire_kalshi.py metadata --series HIGHLAX
python src/klax_lab/acquire_kalshi.py pilot
python src/klax_lab/acquire_kalshi.py candles --start YYYY-MM-DD --end YYYY-MM-DD
python src/klax_lab/acquire_kalshi.py monthly --start 2025-01-05 --end 2025-12-31
python src/klax_lab/acquire_kalshi.py verify
```

The `candles` and `monthly` commands require an explicitly chosen historical interval from the coverage manifest. Full 2025 acquisition was subsequently authorized and completed, saving one manifest per monthly interval. The module defaults to the repository containing its source and permits `--root` for a separate cache. `--max-bytes` may lower the 500,000,000-byte ceiling.

The client allows only public read-only historical market metadata and hourly candle endpoints on the fixed Kalshi host. It refuses redirects, other endpoint types, future candle windows, and candle requests longer than seven days per contract. Acquisition is throttled to at most one new request per 0.5 seconds, with two bounded retries for transient failures. Pagination has a 50-page default ceiling and rejects repeated cursors or conflicting contract metadata. Raw responses are never overwritten; reuse verifies checksums. The cache and transfer budgets both fail closed. Interrupted jobs resume through their request cache.

Run one acquisition process at a time against a cache. The manifest is replaced atomically after each successful response, but the module is not a concurrent download service. This client is isolated from offline experiment imports and implements no orders, streams, subscriptions or scheduler.

Eleven offline unit tests passed for URL restrictions, immutable-cache resumption and tamper detection, byte limits, interrupted staging-file recovery, pagination completeness and loop detection, outcome-free coverage projection, deterministic pilot selection, transient retry handling, non-retried authentication failures, candle timestamp bounds, explicit source timezone offsets, and overlapping-batch deduplication in the offline integrity audit. Tests use synthetic fixtures and do not contact Kalshi.

Run `verify` once acquisition has stopped. The command uses no network and does not parse raw outcomes. It checks every source file against its recorded byte count and SHA-256, reconciles candle source references, deduplicates overlapping pilot/monthly manifests, and writes `data/manifests/kalshi_integrity.json`. This verifies source integrity and contract-level coverage; it is not a claim of quote executability or profitability.

## Primary source references

- [Historical markets endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-markets)
- [Historical candle endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks)
- [CFTC-hosted 2022 fee filing](https://www.cftc.gov/sites/default/files/filings/orgrules/22/09/rule091222kexdcm003.pdf): supports an older general taker formula rounded up to whole cents; it does not establish uninterrupted applicability to all 2025 LAX contracts.
- [Mutable Kalshi fee PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf): direct inspection returned a July 7, 2026 effective document with multiplier and centicent wording. A search result still described October 2025. This mismatch demonstrates why search snippets or today's URL contents cannot substitute for an effective-date archive.
- Raw data and local manifests listed above are the primary evidence for the observed coverage counts.
