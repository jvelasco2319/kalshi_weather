# Model Registry and Policy

## Canonical strategy signals

| Canonical key | Preferred existing source | Family | Initial prior weight |
|---|---|---:|---:|
| `ecmwf_ifs` | `open_meteo:ecmwf_ifs` | ECMWF | 0.20 |
| `gfs013` | `open_meteo:gfs013` | GFS | 0.25 |
| `gfs_seamless` | `open_meteo:gfs_seamless` | GFS | 0.20 |
| `nam` | `open_meteo:nam` | NAM | 0.15 |
| `nbm` | `noaa_herbie:nbm` | NBM | 0.20 |

The weights are priors, not permanent fixed weights. They are updated from prior-date proper scoring as specified in `04_MODEL_PROBABILITIES_AND_MIXTURE.md`.

## Aliases and duplicate protection

- `open_meteo:nam_conus` maps to canonical `nam` and cannot become a second vote.
- `gfs_global` is excluded.
- GFS013 and GFS Seamless may both contribute, but their combined model weight is capped at 0.45 because they share a model family.
- Provider substitution is not transparent. A fallback provider must use a distinct `source_variant` and its own residual history.

## NBM maturity

NBM is part of the current design, but its influence must reflect how much completed-date evidence exists for the relevant time bucket:

| Prior completed dates | Maximum NBM mixture weight | Status |
|---:|---:|---|
| 0–9 | 0.00 | record and score only |
| 10–29 | 0.10 | provisional probability contribution |
| 30–59 | 0.20 | normal initial contribution |
| 60+ | 0.25 | eligible for larger evidence-based weight |

This preserves the July 7 minority signal without allowing one successful day to dominate the strategy.

## Availability gate

A trade probability requires:

- at least four eligible feeds;
- at least three independence families;
- valid source timestamps and complete remaining forecast paths;
- sufficient residual history for every positively weighted model.

Missing models are not imputed from other feeds. Weights are renormalized across eligible models only after all caps are applied.
