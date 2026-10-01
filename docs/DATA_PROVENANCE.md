# Bundled historical data provenance

`examples/history.public-development.jsonl` contains 329 normalized, probability-only records from February 4 through December 31, 2025.

The records are derived from the earlier KLAX research pipeline using archived NOAA HRRR and GEFS forecasts, KLAX observations, and public meteorological inputs. Each row contains the realized bracket position and the exact causal walk-forward probability vectors used by V5B, V8, and V10. The V10 vector uses only information available before that date's outcome was added to the rolling calibration state.

The bundled file does **not** contain raw weather downloads, account information, credentials, paid-source exports, historical quotes, order books, or executable prices. Its SHA-256 is:

`61f91baa7f5f7a897ab1b2d1103cc3582351dec4c5ee1750a470b3dff7deac27`

This is exposed development evidence. It can reproduce the model comparison and graphic, but it cannot establish realized trading returns or serve as an untouched holdout.
