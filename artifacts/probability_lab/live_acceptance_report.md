# KLAX Probability Lab Live Acceptance Report

Current event ticker: KXHIGHLAX-26JUL12

Latest evaluation ID: bc688021ade9c1a3e74d

Evaluation timestamp: 2026-07-13T02:07:19.007315Z

Analysis state: ANALYSIS_READY

Execution state: SHADOW_CANDIDATE

Final reason code: SHADOW_CANDIDATE_NO

Five model statuses:
- ecmwf_ifs: eligible, ready, scenarios=41
- gfs013: eligible, ready, scenarios=41
- gfs_seamless: eligible, ready, scenarios=41
- nam: eligible, ready, scenarios=41
- nbm: eligible, ready, scenarios=41

Calibration count per model: launch default residual support; history counts are serialized per model in the payload.

Distributions: live backend scenario support rendered for all models with available state and the weighted mixture.

Economics: quote-only top-of-book economics; sequence-valid executable depth is blocked.

Fixture data used: no. The live route reads journals/lax_model_validation.sqlite and no fixture fallback is configured.

Real order path reachable: false.

Live watch: 11 samples over 312 seconds. Final sampled evaluation was 9e5c9f4ebf62c5681e23.

Page evidence on final sample: hero=93.5%, distributionSvg=1, marketSvg=1, sensitivitySvg=1.

Artifacts:
- artifacts/probability_lab/live_desktop.png
- artifacts/probability_lab/live_mobile.png
- artifacts/probability_lab/live_explainability_payload.json
- artifacts/probability_lab/live_browser_console.txt
- artifacts/probability_lab/live_network_requests.json
- artifacts/probability_lab/live_acceptance_samples.json
