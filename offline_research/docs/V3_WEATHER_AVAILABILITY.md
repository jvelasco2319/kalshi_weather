# V3 weather availability and offline normalization

The V3 weather decoder preserves the nominal model cycle, valid time, archive
URL, exact byte range, retrieval time, archive `Last-Modified`, ETag, and source
hash. None of these values proves when the forecast first became publicly
available in history. Decoder rows therefore begin with
`historical_availability_proven: false` and `as_of_validated: false`.

The normalization lane applies policy
`max_nominal_plus_6h_archived_last_modified_v2`. Its effective availability is
the later of the decoded nominal model cycle plus six hours and the archived
field object's HTTP `Last-Modified` timestamp, when present. Missing HTTP
metadata falls back to the six-hour bound. The rule never moves information
earlier and the HTTP value remains unchanged in the normalized row. It is a
conservative lower bound on the state of the archived object, not proof of the
original publication time.

This policy replaced `nominal_model_cycle_plus_6h_v1` after the complete raw
cache exposed 16 GEFS field objects on June 28, 2025 whose archived timestamps
were 10:49 UTC: later than the prior 06:00 UTC bound and earlier than the first
12:00 UTC decision. Before code was changed or a campaign was authorized, the
failed 542-of-543 normalization state was copied byte for byte to
`data/manifests/v3_weather_normalization_progress.pre-availability-v2.json`.
The amendment in
`data/manifests/v3_weather_availability_policy_v2_amendment.json` binds that
snapshot, the old code hashes, all 16 source-object and metadata hashes, and
the unchanged research boundaries. The machine-readable policy is
`configs/v3_weather_availability_policy.json`. Readiness rehashes all three
records and rejects a missing or altered registration.

The registered data use the 06 UTC HRRR cycle and the 00 UTC GEFS cycle. Every
row must remain available by the first admitted 12:00 UTC decision after the
max rule is applied, so it is also eligible at 15:00 and 18:00 UTC. A row whose
effective time follows noon fails closed; the global decision schedule is not
changed per day. The 09:00 UTC schedule remains excluded. Every normalized row
records the exact eligible decision schedules and policy identifier.

`python -m klax_lab.weather_normalize_v3 --root <project>` is cache-only. It
does not import an acquisition client and never falls back to current or
`latest` endpoints. It scans exactly the registered 366 training dates in 2024
and 177 selection dates from January 5 through June 30, 2025. It never plans or
reads the protected final interval.

Each complete day must contain all 24 registered HRRR point-field rows and all
16 GEFS mean/spread point rows at KLAX. Outputs and daily manifests are atomic
and hash-verified on resume. Existing v1 days are migrated without GRIB
re-decoding: their old manifest and Parquet hashes and v1 row timing are first
revalidated, v2 rows are written to a staged sibling directory, and the whole
day directory is swapped with a recoverable v1 backup. The v2 daily manifest
records the old output hashes and amendment hash. An interrupted partial stage
is discarded only after the live v1 day revalidates exactly. Complete component
manifests cannot publish while any day remains on v1. Finalization requires the
durable per-day lineage to show exactly 542 migrated v1 days and June 28, 2025
as the sole fresh v2 decode; this remains true across process restarts and is
hash-bound into source completion. Missing raw cache remains
partial progress; identity, timing, provenance, or coverage inconsistencies
are integrity failures.

The progress artifact is
`data/manifests/v3_weather_normalization_progress.json`. The readiness component
manifests `v3_hrrr_local_observations.json` and `v3_gefs.json` are withheld
until every required day passes. The HRRR/local component also independently
checks the normalized local-observation Parquet files and requires KLAX as-of
coverage for every registered date at 12:00, 15:00, and 18:00 UTC. Nearby
stations remain optional diagnostic evidence and their gaps are not represented
as complete coverage.

GEFS evidence is the archived 30-member ensemble mean and standard deviation.
It does not retain member trajectories, skew, or multimodality, so empirical
member-histogram plans require a different complete member-level dataset.
