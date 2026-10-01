# V5P outcome-blind settlement and universe schema

Implementation: `v5p/settlement_universe.py`  
Rolling preview: `data/manifests/v5p_outcome_blind_universe_preview.json`  
Final universe: `data/manifests/v5p_outcome_blind_universe.json`  
Normalized contracts: `data/manifests/v5p_settlement_contracts_outcome_blind.json`  
Holdout seal: `data/sealed/v5p_holdout/seal.json`

## Commands

During acquisition, publish the rolling safe intersection at the separate
preview path without writing a final artifact:

```powershell
python -m v5p.settlement_universe preview --project-root .
```

After the finite acquisition recovery state is exactly `COMPLETE`, freeze the
universe and split:

```powershell
python -m v5p.settlement_universe freeze --project-root .
python -m v5p.settlement_universe verify --project-root .
```

The freeze command fails closed while acquisition is open. It never opens raw
Kalshi responses, CLILAX product text, outcome tables, settlement targets, or
protected labels.

The preview uses schema `klax-v5p-outcome-blind-universe-preview-v1`, declares
`is_final_immutable_universe: false`, and is intentionally replaceable as safe
weather and market partitions arrive. The final universe uses the distinct
schema `klax-v5p-outcome-blind-universe-v1`, is written only after acquisition
is `COMPLETE`, and is immutable once written. Both use canonical
`self_sha256`; a controller must never treat the preview as final readiness.

## Safe inputs

The freezer reads only:

1. the hash-bound V5P acquisition recovery state;
2. `kalshi_coverage.json`, containing KXHIGHLAX contract metadata and rule text;
3. completed one-minute candle manifests, without opening their raw payloads;
4. completed HRRR/GEFS normalized feature manifests and hash-bound feature
   outputs;
5. the CLILAX archive date envelope, without opening report text; and
6. `v5p/acquisition/economics/manifests/evaluator-bounds.json`.

Any outcome-bearing key or path containing `labels`, `outcomes`,
`protected_final`, or `settlement_targets` is denied.

## Contract normalizer

`v5p_settlement_contracts_outcome_blind.json` uses schema
`klax-v5p-settlement-contract-normalizer-v1`. Each retained event contains:

- `climate_date` and `event_ticker`;
- normalized integer lower and upper endpoints, with null for a tail;
- SHA-256 of each exact `rules_primary` string;
- a `contract_set_sha256` over the ordered intervals;
- proof that the intervals cover every integer exactly once; and
- `outcomes_read: false`.

Station, source, variable, climate date, ticker identity, strike type, and
provider endpoints must agree. Gaps, overlaps, duplicate tickers, multiple
events on one date, or ambiguous rules exclude that date.

## Universe manifest

`v5p_outcome_blind_universe.json` uses schema
`klax-v5p-outcome-blind-universe-v1`. Decision-controlling fields are:

| Field | Meaning |
|---|---|
| `self_sha256` | Canonical SHA-256 of the manifest with this field omitted |
| `status` | `PROVISIONAL_ACQUISITION_OPEN` or `FROZEN_OUTCOME_BLIND` |
| `eligible_dates` | Sorted ISO dates, exhaustive over retained records |
| `eligible_records` | Per-date contract, weather, candle, fee, and rule bindings |
| `split.development_dates` | Earliest `floor(0.70 × N)` eligible dates |
| `split.holdout_dates` | Remaining latest dates |
| `safe_manifest_bindings` | Path, byte count, and SHA-256 for every safe source |
| `partial_analysis_ready` | At least one date meets V5P exact-or-bounded research gates |
| `promotion_ready` | All eligible dates have exact rule revisions; currently false |
| `exclusions` | Every omitted calendar date and fixed outcome-blind reason codes |

Every eligible record publishes:

- `weather_manifest_sha256` and `candle_manifest_sha256`;
- `contract_set_sha256` and `contract_bounds_exact`;
- the exact direct-taker fee period and rounding binding;
- `rule_evidence_grade`;
- `bounded_rule_uncertainty`;
- `settlement_rule_revision_exact`; and
- a per-date `promotion_ready` flag.

`BOUNDED_COMMON_NWS_LAX_SEMANTICS` permits V5P partial analysis because the
official public record supports the common NWS-LAX source, integer bracket
semantics, $1 payout, and $0 settlement fee. It does not become exact historical
rule-revision evidence and always has `promotion_ready: false`.

Dates July 1-7, 2025 are always excluded. A date is also excluded when it lacks
complete HRRR/GEFS normalized features, usable candle evidence, the CLILAX
envelope, exact contract boundaries, an exact direct-taker fee period, or at
least a defensible bounded settlement-rule mapping.

## Holdout seal

`data/sealed/v5p_holdout/seal.json` is created only by the final freeze after
acquisition closes. It contains holdout date identities and hashes but no
outcome payload. Its required state is:

```json
{
  "status": "SEALED",
  "label_payload_present": false,
  "label_payload_path": null,
  "holdout_labels_opened": false,
  "holdout_evaluations_consumed": 0
}
```

This module provides no label-opening operation. The later one-shot evaluator
must bind this universe hash, its frozen strategy, and a separate access ticket
before another component can populate or open a holdout label payload.

All three artifacts declare `protected_confirmation_labels_read: false`,
`actual_orders_placed: false`, and `network_used: false`.
