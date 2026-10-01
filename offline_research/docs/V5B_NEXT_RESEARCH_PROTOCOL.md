# Next research protocol: weather-conditioned daily-high distributions

This is a proposed successor protocol after the frozen V5B pilot. It does not amend V5A/V5B, authorize a protected-label read or report any new experiment result. Its purpose is to test a small number of physical forecast-error mechanisms with newly audited local weather features. Independent agent reasoning must occur between research epochs; deterministic fits and replay are not model calls.

## Evidence inventory handshake

The data audit agent inspected all 64 exposed development dates without outcomes: 2,560 normalized weather rows, including 1,536 HRRR and 1,024 GEFS rows. HRRR temperature, total cloud, U/V wind and mean sea-level pressure are complete at four sampled horizons each day. Cloud ceiling is missing in 128 of 256 rows and is excluded from the initial model grammar. GEFS provides ensemble temperature mean and standard deviation at eight horizons; it does not supply individual members. HRRR uses one 06Z cycle with leads 2/8/14/20; GEFS uses one 00Z cycle with leads 9/12/15/18/21/24/27/30. Thus run-to-run changes and member-based tails remain unsupported.

The nearest grid points are approximately 1.61 km from KLAX for HRRR and 12.52 km for GEFS. Sparse forecast maxima are sampled maxima, not hourly daily-high forecasts. Cloud/wind summaries are forecast descriptors, not observations of marine-layer onset. No causal label such as "marine regime" may be inferred from the failing fold.

All rows are marked as-of validated under a conservative availability bound, but original historical availability is not proven. The construction uses the maximum of nominal time plus six hours and archived last-modified time. The feature readiness artifact must verify this bound precedes the 18:00 UTC decision and bind source/output hashes. Missing or failed availability is a blocker, not permission to assume the forecast was known. Outcome-free forecast valid times may lie after the decision because they are future forecasts, but issue/availability times may not.

## Common training and scoring dates

Use exactly the same 64 already exposed development dates. Reserve the first 20 as chronological training/warmup and score the following 44 dates for every model and reference. This leaves a small sample and does not reset the accumulated development-search exposure. Preserve the minimum 30 selected-day gate; failure to obtain enough trades is a valid result.

Every scored fit requires at least 20 strictly earlier official outcomes whose issuance time is no later than that decision. Carry the official label availability metadata into the new evaluator rather than relying on date order. Audit already found no late labels in the current prefix relationships, but the implementation must enforce availability for each prediction.

Generate inner one-step forecasts beginning after 10 available prior labels during warmup. At the first scored date there must be at least 10 earlier out-of-fit residuals. The distinction is explicit: inner residual generation can fit on 10 prior dates; scored forecasts require 20. Never use full-64 fitted residuals, future outcomes or the scored observation's residual to build its distribution. If the common first scored date cannot meet both conditions, fail readiness and revise the proposal before registration; do not silently give different model families different scored dates.

Fit scaling/centering only on the current training prefix. Standardization with zero variance uses a documented neutral scale of one. Missing required features are never filled with zero; an unsupported family is blocked before registration. Freeze a shared date mask without reading the excluded dates' outcomes. The current verified inventory should allow all required features on all 64 dates.

## Six finite model families

The exact feature-column mapping is in `v5b_next/research_specs.py` and matches the data-agent inventory: `hrrr_temperature_f_max`, `gefs_mean_temperature_f_max`, `hrrr_cloud_percent_mean`, `hrrr_wind_u_mean_m_s`, `hrrr_wind_v_mean_m_s`, and `absolute_sampled_max_disagreement_f`. The label-free feature builder reported all 64 dates ready under conservative as-of assumptions in `data/development/v5b_next/weather_features_manifest.json`, with detailed evidence in `reports/v5b-next-weather-feature-audit.md`. Reverify these bindings before registration.

1. **Climatology:** location is the mean of available prior official daily highs. This is the principal weather-uninformed predictive reference, using the same chronological residual machinery as other models.
2. **HRRR bias correction:** start from the sparse HRRR maximum and add the mean past model-minus-outcome correction with the correct sign. Tests whether local systematic sampled-maximum error is enough to improve probability quality.
3. **GEFS bias correction:** the analogous correction to maximum sampled GEFS ensemble mean. Tests whether broad ensemble guidance transfers more stably than the high-resolution model.
4. **Equal blend with bias correction:** fixed 50/50 average of the two sampled maxima, plus past mean residual correction. No outcome-tuned blend weight.
5. **Cloud-conditioned residual ridge:** use the same equal-blend base and fit its past residual from mean HRRR forecast cloud cover, with an intercept. Cloud is a plausible descriptor of radiative forecast error, not an asserted causal explanation of the prior losses.
6. **Cloud/wind/disagreement residual ridge:** extend the previous residual model with mean U wind, mean V wind and absolute disagreement of sampled model maxima. Tests whether advection and model disagreement add information beyond cloud. No onshore-wind projection is claimed without a separately bound local direction definition.

Both ridge families use fixed regularization 10 on prior-only standardized predictors; intercept is unpenalized. There is no hyperparameter sweep or fold-specific feature selection. Report coefficients and their prefix variability for interpretation, but do not mistake coefficient sign for proof of physical causality.

## Predictive distribution and exact contracts

For each model, use only its past chronological one-step residuals. Center a fixed Gaussian kernel of bandwidth 1 degree Fahrenheit on each past residual added to the current point forecast, and average these kernels to form a smooth CDF. This is a registered smoothing assumption, not a measured instrument-error distribution. It prevents arbitrary zero-probability tails without fitting bandwidth to returns. Include the number and dates of calibration residuals in every prediction audit record.

Map the continuous daily-high CDF to the event's integer reported-high contracts with continuity boundaries: `less cap` is below `cap - 0.5`; inclusive `between floor,cap` is from `floor - 0.5` to `cap + 0.5`; `greater floor` is above `floor + 0.5`. This assumes integer-report bins centered at each integer and must be reconciled with exact event rules before scoring. Do not apply floor+0.5 to the lower bound of an inclusive between contract. Verify complete, mutually exclusive contract chains and mass sum to one; reject gaps/overlaps instead of renormalizing invalid strikes. Settlement wins still use the exact integer reported outcome and inherited event rules.

## Fixed controls and budget

At most six model families crossed with four fixed contract policies yields **24 unique executable configurations**, across at most four research epochs. Baseline climatology is one of the six. The four policy arms are highest expected dollar profit on either side, highest expected relative return on either side, highest expected dollar profit on NO only, and highest expected dollar profit on either side restricted to A/B+ evidence. Existing price, spread and edge thresholds are fixed before registration. No configuration is added solely because a fold is just below a gate.

The inherited frozen leader and uniform-listed-bracket probabilities may be replayed on the same 44 dates as declared reference controls. Uniform listed brackets are a nonmeteorological diagnostic, not a calibrated temperature climatology; their probabilities depend on how the exchange lists brackets. The climatology family is the proper primary no-weather reference. Reference controls cannot enter adaptive selection, and their computational evaluations and development exposure are counted explicitly outside the 24 candidate budget. An official-observation persistence reference is optional only if registered in advance with the same label-availability enforcement; it cannot be added after seeing model results.

The 24-slot ceiling is not a requirement to use every slot: the whole study can stop early. The implemented fixed backend emits all six models for each admitted arm to preserve common coverage; individual-family retirement is an agent recommendation, not an implemented dynamic allocator. Unsupported inputs block registration or scoring. A new unregistered model mechanism requires a separate proposal/registration. No claim of exhaustive meteorological research follows from this limited test.

## Four-colony research loop

**Forecast probability** owns physical mechanisms, prefix fits and proper forecast scores. **Timing/execution** audits availability, five-second execution arrival, evidence grades, cost stress and feasibility; alternate decision times remain blocked. **Contract/relative value** compares the registered policy arms on identical model probability chains and dates. **Robustness/adversary** independently challenges chronological leakage, sample sufficiency, coefficient drift, fold consistency, best-day dependence and outcome selection.

Each maintains its own causal hypothesis records, candidate lineage and criticism rather than sharing one incumbent. In this finite backend, populations are introduced in stages: forecast owns the first arm, contract the relative-return arm, robustness the NO-only falsification arm, and execution the A/B+ arm. This is not four simultaneous autonomous model generators. Actual agents inspect an epoch's prediction diagnostics and trade ledgers, author evidence-backed cross-reviews, and decide whether to admit the next registered arm or stop. The controller validates fingerprints and data/code bindings, scores deterministically, stores the Pareto archive and requires reviews before advancing. Agent agreement is not independent numerical verification. The architecture does not claim an unattended local LLM call unless its invocation and response are actually logged.

Implemented bounded order: first compare six weather distributions under dollar-profit selection; then test whether relative-return selection changes their conclusions; then NO-only expression; then stronger-evidence eligibility. The backend requires a nonempty review packet before proposing another arm, while controller checks enforce actual reviewer rules. It emits six candidates per arm, so configuration must permit six proposals per owning colony. It does not synthesize extra models beyond the 24 predeclared combinations. Agents may stop the entire study earlier. Review cadence is at most four epochs, not four mandatory model calls.

## Required ablations and failure tests

Compare each bias-corrected single model with climatology, the fixed blend with both single models, cloud residual modeling with the blend, and wind/disagreement additions with cloud alone. All comparisons use the same scored dates and rule mapping. Report Brier/log loss, temperature point error, predictive intervals, calibration sample size, and economic results separately. Improvement in a forecast score does not establish a tradable edge.

Enforce prior-publication availability; perturb future labels and confirm earlier predictions do not change; independently recompute CDF-to-chain probabilities and settlements. Repeat exact fee calculations and fixed-selection adverse fills, Grade-A-only sensitivity, best-day removal and five chronological folds over the 44 scored dates. Small folds are noisy; do not exclude a known bad fold, tune a fold boundary or include calendar-date blacklists. Fixed Grade-A-only sensitivity is a diagnostic, not an extra optimized model family.

All registered development gates remain conjunctive: at least 30 selected days, expected and aggregate simulated net return at least 10%, four positive folds, worst nonempty fold at least -10%, positive adverse-fill/best-day tests, declared evidence requirement and proper calibration baseline. The final registration must explicitly choose the common reference for the Brier gate before runs; a model cannot choose whichever baseline is easiest after scoring.

## Completion and scientific limits

Save the source capability matrix, feature hashes, model specification registry, all prefix fit/prediction metadata, actual agent reviews, causal ablations, unique candidates, independent numerical verification and development report. Freeze one strategy only after the finite study and its terminal review. State which mechanisms were falsified, unsupported or unresolved, even if none pass.

These 44 dates come from the already repeatedly used 64-date development set. Expanding prediction does not make the eventual selected strategy an untouched out-of-sample success. V5A's reserved holdout stays unavailable to this successor. A separate genuinely untouched, eligible period and one-shot frozen confirmation are still needed; no order or claim of realized profit follows from this protocol.
