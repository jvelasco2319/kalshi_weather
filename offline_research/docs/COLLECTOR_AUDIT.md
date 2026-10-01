# Preferred collector audit and historical weather adapter

Audit date: September 24, 2026 Pacific / September 25 UTC. This is a source and compatibility audit, not evidence of forecast skill or trading returns.

## Source recovered and preserved

The user's private repository is `https://github.com/ciel-ravencourt/weather_data_collector.git`, pinned to commit `a82f8aee0afecf568b3ce16f339c0bd7201773b8`. The relevant source files were recovered through the user's authenticated GitHub file downloads and are preserved without edits under `external/weather_data_collector/`. This is a source snapshot, not a successful authenticated Git clone or a claim that every repository file was retrieved. No source license was established in the inspected files; the private source must not be published.

Inspected source hashes (SHA-256):

| File | SHA-256 |
|---|---|
| `weather_pipeline.py` | `0c3e032b71538efe6b2903fa9fb5e2ec1a1901c6243038cdb38e3393ab3fc4fc` |
| `requirements.txt` | `738e16549b352bd33914832dd6214ad51e42ab1170a55e0a25cb547868ad8594` |
| `schema.py` | `794b1263cd27a2a7f2842792b12c8742f825e98c884882c4484c966c88e73fbf` |
| `probability/distribution.py` | `5aecc11ce30085d7dbe2535f7537f3303a9436e40c493a45bafa16147131705b` |
| `ensemble/ensemble.py` | `3209067cf62ab4d72d18da8f676c5443b9f91bd16d77d913db5695bc43ff0b89` |

The adapter checks the parent pipeline hash before acquisition. It does not import the original pipeline: that module requires pygrib, and its configuration import creates directories. Instead, `src/klax_lab/acquire_weather.py` explicitly adapts the original `_url` archive paths, while `src/klax_lab/grib_reader.py` implements validated local extraction. Future patches must retain this parent provenance.

## What source inspection established

The original collector supports GFS from an RDA mirror, GFS Seamless from NOAA's S3 GFS archive, NAM Grid 218 from NCEI, and NBM from NOAA's S3 archive. These are actual source-code findings, superseding the earlier README-only assessment.

1. **Location:** `resolve_city` queries Open-Meteo and takes the first matching city. That does not establish KLAX. The adapter fixes the verified LAX station coordinates at 33.93816, -118.3866 and retains the selected model grid coordinates and distance.
2. **Climate day:** the original uses a civil IANA timezone calendar day. For this KLAX research convention, the adapter uses fixed Pacific standard time: 08:00 UTC through the following 08:00 UTC, end exclusive, on all dates. The specific historical contract still requires rule verification.
3. **GFS interval maxima:** the original retains only complete six-hour Tmax windows inside the day. For a 00Z initialization and the fixed-PST target window, those windows do not cover 08:00–12:00 or 30:00–32:00 forecast hours. Its extraction also selects `tmax` by short name without explicitly requiring the two-metre level. The adapted initial feature therefore uses verified instantaneous two-metre temperature samples, not partially covering Tmax fields.
4. **Model dependence:** GFS and GFS Seamless are mirrors/products of the GFS family, not independent models. The original weights assign them 0.30 and 0.20. This adapter downloads one GFS source, records it as `gfs`, and does not double-count its mirror.
5. **Probability:** the original sets an uncalibrated 2°F standard deviation. It evaluates Gaussian density at integer temperatures and normalizes a truncated grid, rather than integrating probability over rounded integer bins. It can remain a named naive comparator; it is not validated probability calibration. The separate domain mapper uses integrated rounded-bin probabilities.
6. **Caching and provenance:** the original reuses any nonempty cached file. The adapter instead records and checks byte lengths, SHA-256, URL, requested byte range, HTTP metadata, and retrieval time. It refuses an unproven or corrupted cached file.
7. **Field semantics:** original extraction assumes Kelvin and does not compare actual reference/forecast timestamps to the requested initialization and lead. The adapter checks GRIB edition, short name, level type/value, units, instantaneous step type, step units, start/end lead, initialization, valid time, product template, and grid. NBM's index includes an ensemble-standard-deviation TMP record immediately after the deterministic TMP record; the adapter explicitly excludes it.
8. **Dependencies:** the pinned requirements include pygrib 2.1.8; a native Windows wheel was unavailable in the environment check. Native Windows ecCodes 2.48.0 was installed and actually decoded both NOAA pilot fields. No WSL installation or operating-system change was required.

Nearest-cell selection is an explicit adaptation. The original minimizes squared latitude/longitude differences; ecCodes uses its geographic nearest-neighbour routine. Selected cells are retained so coastal sensitivity can be tested later. No interpolation or land-only masking is silently applied.

The original calculation is separately preserved and execution-checked on explicitly synthetic, original-shaped daily-high records. The pinned probability, ensemble and schema modules are imported without the fetch pipeline; their output JSON is saved and parsed. See [the calculation reference](COLLECTOR_REFERENCE.md) and `data/manifests/collector_reference.json`. This retains the original four-model weights and mixture of integer-density distributions. It is distinct from the historical `collector_style_fixed_2f` diagnostic approximation and does not claim recovery of original historical four-feed outputs.

**NBM scan-order caveat:** the pilot NBM grid sets `alternativeRowScanning=1`. The `index` reported by the ecCodes nearest-point routine is in its geometric point ordering; directly reading `values[index]` does not reproduce the nearest routine's temperature on the odd scanned row containing KLAX. A local check found a 16 K difference for the pilot lead-8 field, while reversing the encoded odd row recovered the nearest routine's value. The production reader retains `codes_grib_find_nearest` and its returned value. Independent replication must use that validated routine or an independently verified scan-order treatment; the saved grid index is not permission to index a raw values array blindly. A proposed scalar-index performance shortcut was not adopted.

## Initial historical feature and timing policy

The registered pilot uses the 00:00 UTC initialization for each target date. Its main samples are forecast leads 9, 12, 15, 18, 21, 24, 27 and 30 hours. Leads 8 and 31 were also verified and included to improve boundary coverage. All ten samples lie in the fixed-PST target day; lead 32 is excluded.

The resulting `sampled_max_temperature_f` is **the maximum of ten instantaneous forecast samples**, not an observed daily high and not an exact continuous forecast maximum. Sampling can miss the peak. Calibration and comparison with other sampling schedules belong in chronological development experiments. This adapter does not apply a probability distribution or claim the proxy is the settlement target.

For initial research, forecast availability is conservatively assumed to be initialization plus six hours. The proposed decision is 14:00 UTC. The fields retain `historical_availability_proven: false` and an explicit availability-basis string. This is an assumption requiring review, not historical publication proof. In the pilot, S3 Last-Modified timestamps are approximately 03:39–03:46 UTC for GFS and 01:02–01:03 UTC for NBM. They are saved as supporting source metadata; they do not by themselves establish how a trader could have obtained a file at that instant.

## Measured compatibility pilot

Target date: **2025-01-05**, initialization **00Z**, GFS plus NBM, ten leads each. All 20 selected fields downloaded and decoded successfully. No outcome labels, market profits or holdout results were inspected for this audit.

| Measurement | GFS | NBM |
|---|---:|---:|
| Selected temperature field bytes, ten leads | 8,788,993 | 13,872,933 |
| Full source-object bytes represented by those fields | 5,437,698,692 | 1,640,958,919 |
| Forecast grid | Regular latitude/longitude, 0.25° | Lambert, CONUS blend |
| Nearest cell latitude | 34.000000 | 33.941229 |
| Nearest cell longitude | -118.500000 | -118.388130 |
| Distance from KLAX | 12.516 km | 0.369 km |

Total transfer including indexes was **23,241,303 bytes**. The source objects represented by those range requests total approximately 7.08 GB. Files were retrieved only with HTTP 206 and exact verified `Content-Range`; a server returning HTTP 200 for a GRIB range is rejected before reading a full object.

At the pilot's measured size, 731 dates covering 2024 and 2025 would require about **17.0 GB** of indexed transfers for both models and ten leads. This is an extrapolation, not a promise: compression, index size and historical model versions vary. The actual job has a shared 50 GB project weather ceiling, not a separate 50 GB allowance for each date. Raw range bytes and their hashes are retained. Full multi-variable global/CONUS source objects are not retained or claimed to have been downloaded.

Pilot evidence manifests:

- `data/manifests/weather/2025-01-05_20260925T013621474592Z.json` — first GFS/NBM lead-9 compatibility check.
- `data/manifests/weather/2025-01-05_20260925T013738109305Z.json` — ten-lead acquisition; earlier cached lead 9 is reused.
- Later timestamped manifests for the same day record cache-only verification.

## Bounded acquisition and reusable outputs

After the successful pilot and cache test, a finite job was started for **2024-01-01 through 2025-12-31**, GFS and NBM, ten leads per day. It uses one shared request limiter of at most two HTTP request starts per second and one shared transfer budget. The initial 2024-01-01 acquisition completed with all fields verified. This audit does **not** claim the entire two-year acquisition is complete; the monthly progress files and final range manifest are authoritative for coverage.

Operational logs: `data/weather_acquisition_2024_2025.log` preserves the initial sequential run; `data/weather_acquisition_2024_2025_parallel.log` records the resumed run. The initial job completed nine dates at approximately 34.5 seconds per uncached date, implying almost seven more hours. It was stopped before the resumed process was launched. Existing verified caches were reused.

The resumed implementation uses four bounded download workers behind one thread-safe two-requests-per-second gate. HTTP sessions are private to persistent request threads; a shared byte reservation reserves each response's worst-case size before dispatch, preventing concurrent requests from oversubscribing the transfer limit. GRIB decoding remains serial and overlaps the downloads, avoiding an unverified assumption about native decoder thread safety. Model and lead ordering are deterministic even when downloads finish out of order. Metadata and derived artifacts are written through completed temporary files and atomic renames. An operating-system-held CLI lock rejects a second weather CLI job for the same project.

Paths:

```text
data/raw/weather/<model>/<YYYYMMDD>/t00z/f009.idx
data/raw/weather/<model>/<YYYYMMDD>/t00z/f009.idx.json
data/raw/weather/<model>/<YYYYMMDD>/t00z/f009.2m_temperature.grib2
data/raw/weather/<model>/<YYYYMMDD>/t00z/f009.2m_temperature.grib2.json
data/raw/weather/<model>/<YYYYMMDD>/t00z/f009.2m_temperature.point.json
data/normalized/weather/<model>_<YYYY-MM-DD>_sampled_temperature.json
data/manifests/weather/<date>_<retrieval-or-replay-id>.json
data/manifests/weather/progress/<YYYY-MM>_<run-id>.json
```

The source GRIB, index and provenance files are immutable. The point cache avoids expensive grid decoding on subsequent passes while checking its source hash and metadata. Daily normalized JSON includes target date, model, initialization, climate window, feature definition, planned leads, sample count, sampled maximum, availability assumption and individual point records. Every point record contains the reference/valid times, temperature in K and F, grid coordinates/distance/index, extraction method, ecCodes version and source path/hash. A missing lead produces a recorded gap and no complete daily feature; it is not filled from another run or today's forecast.

Monthly progress JSON is mutable operational status. Raw evidence and individual acquisition manifests remain intact. A stopped job can resume by running the same bounded date plan; valid caches prevent repeated downloading, and complete point caches prevent repeated GRIB decoding. No scheduler or unattended periodic collection is configured.

## Implemented commands

Run from the repository with the environment installed and `PYTHONPATH` set to `src`, or with the package installed:

```text
python -m klax_lab.acquire_weather --project-root . --date 2025-01-05 --models gfs nbm --include-boundary-samples --max-transfer-mb 250
python -m klax_lab.acquire_weather --project-root . --date 2025-01-05 --models gfs nbm --include-boundary-samples --offline
python -m klax_lab.acquire_weather --project-root . --start-date 2024-01-01 --end-date 2025-12-31 --models gfs nbm --include-boundary-samples --max-transfer-mb 50000
```

The first and third commands are historical acquisition commands and require network permission. The second performs only cache reads and local validation. Do not launch a duplicate acquisition while the current bounded job is running.

## Finite gap repair after acquisition

`python -m klax_lab.repair_weather --root .` is the bounded post-acquisition repair entry point. It requires a terminal full-range acquisition record with exactly 731 distinct registered dates and matching monthly counts. It holds the same operating-system lock as the acquisition CLI, so an active download prevents repair from starting. Its original gap list is frozen to that acquisition record.

Only original gap dates may receive requests. Each date has at most two persisted attempts, using the same GFS/NBM model set, ten leads, two-requests-per-second limiter, four download workers and shared project weather byte ceiling. Attempts are recorded before dispatch, so interruption does not reset the retry limit. Verified raw caches are reused without overwrite. The HTTP adapter itself has no automatic retry policy; the repair wrapper supplies the explicit finite retry budget.

After those attempts, every registered date is checked through the existing acquisition adapter with `offline=True`. The `COMPLETE` status requires a terminal 731-day integrity manifest, no incomplete dates and **zero transferred bytes**. If a full offline check finishes with gaps, status is `INCOMPLETE`; the CLI still exits successfully so the downstream preregistered partition-coverage rules can evaluate the disclosed exclusions. A blocked, failed or unfinished integrity pass exits unsuccessfully. Successful completion is cached in a repair journal under `data/manifests/weather/repair/`; incomplete results remain explicit and cannot silently increase their network-attempt budget. CLI progress goes to stderr and the final status, paths and gap counts are a single JSON object on stdout.

The repair tests use synthetic manifests and mocked acquisition only. They verify that active and nonterminal jobs block, duplicate and escaping records fail, only exact gap dates are retried, restart cannot grant extra attempts, zero-gap verification constructs no HTTP session, and nonzero transfer during offline integrity checking fails. Implementing or testing this wrapper does not claim that the running historical job or its later repairs have finished.

## Validation performed and remaining limitations

Fourteen synthetic weather tests passed in the native environment, including a generated GRIB2 field decoded through ecCodes. Tests cover archive URL/lead semantics, fixed-PST boundaries across DST, units, initialization and valid-time mismatch, instantaneous versus interval fields, deterministic versus uncertainty products, complete daily sample requirements, exact HTTP range validation, transfer-budget refusal, cache tampering, concurrent byte reservations, shared rate limiting, atomic writes, deterministic completion ordering and rejection of overlapping CLI jobs.

The real 20-field pilot then completed in cache-only mode while both socket connection and Requests request methods were explicitly denied. It transferred **zero bytes** and returned two complete sampled-proxy features with no errors. A subsequent parallel-cache check also succeeded with both networking and GRIB decoding denied, demonstrating reuse of the verified point cache. This demonstrates offline operation for this adapter; it is not a substitute for operating-system sandboxing of arbitrary agent code.

Required later checks remain: full historical coverage and exclusions; historical availability adequacy; source/model version changes across the interval; source license/retention terms; station and settlement reconciliation; calibrated forecast skill; historical price/fee/execution evidence; protected holdout controls; and independent scientific replication. A source download, an uncalibrated model output or a positive fixture result does not establish profitability.

## External implementation references

- [NOAA NBM archive registry](https://registry.opendata.aws/noaa-nbm/) identifies the public GRIB2 source archive.
- [ECMWF ecCodes Python interface](https://confluence.ecmwf.int/spaces/ECC/pages/127318080/Python%2B3%2Binterface%2Bfor%2BecCodes) and the [official Python implementation](https://github.com/ecmwf/eccodes-python) document the decoder used here.
- The user's collector source snapshot and the raw download manifests are the primary evidence for the specific adapter and pilot results above.
