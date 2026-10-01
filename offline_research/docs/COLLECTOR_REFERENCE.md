# Preserved original collector calculation

This artifact is a **synthetic calculation reference**, not a recovered historical forecast, a trading result, or an additional research model. It addresses preservation of the original collector's calculation and output shape while keeping its known flaws visible. The two-year historical GFS/NBM baseline remains a separate adaptation.

## Source and executed evidence

The untouched collector snapshot is pinned to commit `a82f8aee0afecf568b3ce16f339c0bd7201773b8`. `src/klax_lab/collector_reference.py` verifies the SHA-256 of the pipeline, probability, ensemble and schema source files before loading anything. It imports the actual verified bytes of only `probability/distribution.py`, `ensemble/ensemble.py` and `schema.py`. The loader does not read or write bytecode caches or change the snapshot.

Neither `weather_pipeline.py` nor `config.py` is imported. No geocoder, download function, GRIB fetch/extraction code, live feed or external service is executed. The original pipeline source is used only to audit the assembly of its output dictionary. Locally supplied records have the shape of its extracted daily-high result: `daily_high_f`, `source` and `samples`.

The actual imported original functions produce model distributions, the combined distribution and summary statistics. The original schema validator runs on that dictionary, the output is saved as JSON, and the original validator runs again on the parsed saved output. Additional checks verify finite probabilities, normalization, integer-temperature keys and retained model records. Tests perform the import and round trip with socket and Requests connection methods denied. This is Python-level network denial, not an operating-system sandbox claim.

A separate scalar implementation reproduces the calculation. Its outputs are compared against those produced by the imported original NumPy functions with absolute tolerance `1e-12`; differences at floating-point rounding scale are not treated as algorithm changes. The synthetic four-model expected-temperature comparison differed by approximately `1.42e-14` F; no probabilities, temperatures or results are taken from held-out data.

## Faithful algorithm mapping

| Original component | Preserved behavior |
|---|---|
| `gaussian_temperature_distribution` | Fixed standard deviation 2 F; integer grid from `floor(mean - 10)` through `ceil(mean + 10)`, inclusive; evaluate exponential density at each integer, then normalize the truncated grid. |
| `create_weight_vector` | Original weights: GFS 0.30, GFS Seamless 0.20, NAM 0.20, NBM 0.30. Renormalize over available models. |
| `build_ensemble_distribution` | Weighted mixture of the individual discrete distributions, using the union of their temperature bins, missing-bin mass zero, then normalize and sort bins. |
| `ensemble_statistics` | Expected temperature, most likely integer bin and its probability mass as `confidence`. The word confidence is preserved from the original; it is not a confidence interval or demonstrated calibration. |
| `run_pipeline` output assembly | Preserve metadata, available model names, explicit unavailable-model reasons, each model's mean/std/distribution/daily-high record, and ensemble distribution/statistics. The fetching/extraction portions are not executed. |
| `validate_dataset` | Execute the original schema validator before serialization and after parsing saved JSON; add separate normalized-probability checks. The original schema validator itself checks only a limited set of required fields/types. |

The reference intentionally retains two problematic behaviors: the GFS family can contribute through both GFS entries, and integer-bin probabilities come from point density with truncated-grid renormalization. It does not silently replace either behavior with independent model families or Gaussian CDF integration.

## Synthetic fixtures and saved artifacts

Both fixtures use the clearly artificial target date January 15, 2000, synthetic source-file labels, and a location name explicitly identifying a synthetic KLAX fixture. No referenced GRIB file exists or is represented as recovered.

- **All four models:** supplied daily highs are GFS 63.25 F, GFS Seamless 65.50 F, NAM 68.00 F and NBM 70.75 F. This exercises fractional means, original weights and different distribution supports.
- **GFS/NBM only:** retain the same supplied GFS and NBM values and explicitly mark the other models unavailable. Their equal original default weights renormalize to 0.50/0.50. This remains a mixture of two distributions, not one distribution at their average temperature.

The deterministic outputs are preserved without overwrite under `runs/collector-reference/`:

```text
synthetic-inputs.json
original-output-all-four-models.json
reference-output-all-four-models.json
original-output-gfs-nbm-only.json
reference-output-gfs-nbm-only.json
verification.json
```

`data/manifests/collector_reference.json` is the readiness pointer. It has `status: PASS`, `synthetic: true`, the reference-code hash, pinned parent source hashes, execution/parse flags and a path/size/SHA-256 artifact inventory. `verify_reference_artifacts(root, reference)` checks those records, confines output paths to the fixed reference directory, verifies source/code/artifact integrity and returns the saved original-output paths. Readiness verification does not import or execute the original collector modules.

Run the explicitly synthetic reference from the project environment:

```text
python -m klax_lab.collector_reference --root .
python -m unittest discover -s tests -p test_collector_reference.py -v
```

## Distinction from the historical baseline

The registered historical `collector_style_fixed_2f` baseline averages the adapted GFS and NBM sampled-maximum features and places one fixed-2-F Gaussian at that average. Contract probabilities use the project's integrated rounded-bin mapping. It does **not** reproduce the original collector's weighted density mixture. Its existing description correctly calls it a diagnostic approximation; no evaluation configuration, candidate family or acceptance threshold is changed by this reference artifact.

The preserved reference outputs come directly from the original probability/ensemble functions on specified synthetic inputs. They are not evidence that the collector's archived GFS interval maxima, GFS RDA mirror, NAM Grid 218 forecasts or original city-geocoder outputs were recovered. The snapshot acquisition explicitly excluded example datasets and Git history. No original historical output JSON has therefore been preserved from the previous project.

An exact historical recreation of the entire original pipeline would require its original daily-high inputs, coordinates, civil-day/interval selection and all available model records for each date. The current fixed-PST KLAX instantaneous-temperature samples cannot recover those missing quantities. If Goal 1 is interpreted as requiring a historical four-feed original-pipeline comparison, that stronger requirement remains unmet; this artifact must not be relabeled as that comparison. It verifies and preserves the original calculation and schema only, without expanding the registered historical research scope.
