# V3 frozen training and development dataset contract

This contract covers calendar-year 2024 weather-model training and the offline
January 5 through June 30, 2025 V3 development interval. Development is split
before freezing: January 5-February 3 is a fixed 30-day calibration prefix,
while February 4-June 30 is the only scored evaluation window. It does not
authorize a campaign and does not read, copy, summarize, or evaluate the
protected July-December 2025 interval.

## Public API

`klax_lab.dataset_v3` exposes six operations:

1. `normalized_input_manifest(component, rows, upstream_manifests=())` binds an
   order-independent normalized row digest, the exact set of source SHA-256
   values, and complete upstream-manifest digests.
2. `build_frozen_development_dataset(...)` accepts explicit normalized rows and
   seven bindings: 2024 settlement, observation, and forecast inputs plus 2025
   development settlement, market, observation, and forecast inputs. It
   performs all joins in memory and writes a
   deterministic immutable freeze.
3. `freeze_project_development_artifacts(...)` is the filesystem adapter. It
   reads only caller-named normalized Parquet files and manifests below the
   project root, binds their exact bytes, and calls the core builder. Forecast
   paths are mandatory because no completed forecast artifact may be guessed.
4. `build_five_chronological_folds(dates, dataset_id=...,
   calibration_dates=...)` returns the exact registered five-fold evaluation
   partition and binds the separate calibration prefix.
5. `verify_frozen_development_dataset(destination)` independently recomputes
   artifact, dataset, and fold identities and checks the protected-final and
   label-separation invariants.
6. `publish_frozen_dataset_component_manifests(root, destination)` publishes
   the two canonical V3 readiness-component manifests,
   `data/manifests/v3_dataset.json` and
   `data/manifests/v3_five_fold_split.json`, only after a real freeze verifies.
   Each component explicitly keeps campaign readiness false; a
   separate readiness validator must still validate the complete V3 system.

The post-download command is:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m klax_lab.freeze_pipeline_v3 --root .
```

It runs only after the 543-day cache-only normalizer and source finalizer have
published complete hash-bound evidence. It discovers every HRRR and GEFS
Parquet file from those component manifests, verifies the exact registered
path, hash, byte count, row count, day, cycle, lead, field, member, and
availability policy, including the hash-bound v2 amendment and pre-amendment
failure snapshot, then adapts those rows into the dataset schema. It recomputes
each row's effective time as the later of cycle plus six hours and archived
field-object Last-Modified, rejects any row after the fixed noon decision, and binds
the exact settlement, minute-market, and local-observation artifacts as well.
The command has no downloader and issues neither a readiness ticket nor a
campaign ticket.

The core builder requires `decision_times_utc`, `required_stations`,
`required_forecast_models`, and a positive minimum record count for every
required forecast model. It also freezes the required field and member sets
for each model. The GEFS-summary default requires both `avg` and `spr`;
control-only or full-member datasets must override that set explicitly. All
these values become part of the dataset identity.

The real pipeline requires KLAX at every admitted as-of cutoff. KHHR, KLGB,
KSMO, and KTOA are optional context stations: their latest valid report is
preserved in a frozen row when available, and absence does not remove a day.
This matters for KTOA at 12:00 UTC, where the audited archive is incomplete.

## Normalized input rows

Training settlement, observation, and forecast tables use
`partition = "weather_training"` and dates in 2024. Development settlement,
market, observation, and forecast tables use `partition = "selection"` and
dates from January 5 through June 30, 2025. Every table has one or more source
SHA-256 fields. A row from another partition is an error. A protected-final
date or path is an error, not an exclusion.

| Component | Unique grain | Required semantic fields |
| --- | --- | --- |
| Weather-training target | `climate_date` | `station=KLAX`, integer `reported_high_f`, passed or unset reconciliation status, source hash |
| Development settlement | `climate_date` | `station=KLAX`, integer `reported_high_f`, `event_ticker`, `reconciliation_status=passed`, exact `contract_outcome_reconciliation`, one winning contract, source hash |
| One-minute candle | `ticker, end_period_ts` | `period_minutes=1`, completed bar Unix timestamp, normalized quote fields, source hash |
| Public trade | `trade_id` | `ticker`, publication timestamp, positive `quantity_contracts`, normalized price/side fields, source hash |
| Observation | `climate_date, station, observed_at` | `issued_at`, `available_at`, normalized weather fields, source record identity, `as_of_validated=true`, source hash |
| Forecast | `climate_date, model, member_id, initialized_at, valid_at, field_id, location_id` | `available_at`, numeric-or-null `value`, matching `is_missing`, `as_of_validated=true`, extraction/member identity, source hash |

Market candles become available at their completed bar-end timestamp. A
public trade becomes available at its publication timestamp. Observations must
have `observed_at <= issued_at <= available_at`. Forecast rows must have
`initialized_at <= available_at`. A forecast `valid_at` may be after the
decision because a genuine historical forecast can describe future weather.

The builder rejects duplicate grains before joining. It also rejects evidence
on a date without an eligible target or an explicitly registered exclusion.
Training labels plus exclusions must cover every 2024 date. Development labels
plus exclusions must cover every date in the registered interval. The 30-day
calibration prefix permits no exclusion. Every retained day and decision must
contain an as-of observation and a latest complete forecast cycle. Development
also requires a completed candle for every reconciled contract. A forecast
cycle is complete only when it has the registered minimum number of rows and
contains every registered field and member for that model.

## Physically separated feature artifacts

The 2024 feature grain is one `climate_date, decision_at` pair and is stored in
`weather_training_features.jsonl`. It contains no market evidence. Its schema
is [`frozen-weather-training-feature-v3.schema.json`](../schemas/frozen-weather-training-feature-v3.schema.json).

The development feature grain is one `event_ticker, decision_at` pair. The
same row schema applies to two separate files:

- `development_calibration_features.jsonl` has
  `data_role=development_calibration` and only January 5-February 3 rows.
- `development_evaluation_features.jsonl` has
  `data_role=development_evaluation` and only February 4-June 30 eligible rows.

The exact development schema is
[`frozen-development-feature-v3.schema.json`](../schemas/frozen-development-feature-v3.schema.json).

Each row contains:

- `climate_date`, event identity, UTC decision time and timestamp;
- every contract ticker and exact interval;
- the latest completed one-minute candle at or before the decision;
- public-trade count, quantity, latest trade, and source hashes over the fixed
  `(decision - 60 minutes, decision]` window;
- the latest as-of observation for each required station; and
- every record from the latest as-of forecast cycle that meets the registered
  model-specific completeness threshold.

Every feature artifact contains no reported high and no binary outcome.
`as_of_join_validated` is
true and `contains_settlement_label` is false. Candidate evaluation should use
the artifact for its registered role as its only decision-time evidence.
Forecast records use the downstream feature API's stable keys: `model`,
`field_id`, `member_id`, `value`, `is_missing`, and `as_of_validated`.
Observation records use `station`, normalized observation fields, and
`as_of_validated`.

## Physically separated label artifacts

Weather-fitting targets are stored only in `weather_training_labels.jsonl`
under [`frozen-weather-training-label-v3.schema.json`](../schemas/frozen-weather-training-label-v3.schema.json).

Development labels are split into `development_calibration_labels.jsonl` and
`development_evaluation_labels.jsonl`. Their grain is one settlement day and
event. The exact schema is
[`frozen-development-label-v3.schema.json`](../schemas/frozen-development-label-v3.schema.json).
It contains the exact CLILAX integer high and the reconciled binary outcome for
each ticker. This artifact is evaluation-only and physically separate from the
feature file.

## Provenance and identity

Each normalized input binding records the canonical row hash, row count,
complete source-hash set, and upstream-manifest hashes. The project adapter's
upstream envelope includes each Parquet path, byte count, file hash, row count,
and complete source manifest. The dataset manifest binds those inputs and the
feature and label artifact bytes.

`dataset_id` hashes the complete dataset policy, input bindings, counts, and
feature/label file identities. `folds_id` hashes the complete split and is
bound back to `dataset_id`. Existing frozen files may be reused only when their
bytes are identical. A changed input or policy must use a new destination.

## Five chronological folds

Only eligible February 4-June 30 scored evaluation dates are sorted and
divided into five contiguous blocks.
The earliest folds receive the remainder, so fold sizes differ by at most one.
Each fold saves its exact date list and date hash. The five lists concatenate
to the exact evaluation label dates without gaps or overlap. All 30 calibration
dates are saved and hashed separately in the fold artifact; none may appear in
a fold.

The frozen rule permits weather and regime fitting only on the 2024 training
partition. Market-residual and conformal fitting may additionally use the
fixed calibration prefix. No scored evaluation date is admitted as fitting or
calibration data. This rule is part of the fold identity and cannot be changed
inside a campaign.

## Real-data boundary

The exact settlement, minute-market, local-observation, and 2024 fitting-target
artifacts are present. The finite HRRR and GEFS download and normalizer may
still be in progress. Until all 543 registered weather days and source
finalization pass, `freeze_pipeline_v3` fails before creating a dataset
manifest. Synthetic fixtures are used only by tests and are never published
under `data/manifests` as readiness evidence.
