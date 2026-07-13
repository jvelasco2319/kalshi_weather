# End-to-End Algorithm

```text
on source_event(event):
    coalesce event burst deterministically
    evaluated_at = event-consistent UTC cutoff

    config = load immutable strategy version
    assert mode == shadow
    assert live/canary/taker are false

    market_rules = latest verified rules available by evaluated_at
    fee_schedule = latest verified schedule available by evaluated_at
    book = sequence-valid synchronized book available by evaluated_at
    portfolio = reconciled positions/open orders/shadow state
    capture = source completeness manifest

    if a required dependency is invalid:
        persist DecisionState + exact NO_TRADE reason
        return

    observations = accepted KLAX observations available by evaluated_at
    observed_max = maximum observation for target station day

    model_states = {}
    for canonical model in [ecmwf_ifs, gfs013, gfs_seamless, nam, nbm]:
        source_variant = configured canonical source
        run = latest complete run with available_at and received_at <= evaluated_at
        if no eligible run:
            record model unavailable
            continue

        future_points = selected run points with:
            evaluated_at <= valid_time <= verified station-day end
        if future_points empty:
            record model unavailable
            continue

        future_max = max(future_points.temperature_f)
        X = max(observed_max, future_max) if observed_max exists else future_max
        persist ModelLiveState and source point IDs
        model_states[model] = X

    enforce at least 4 feeds and 3 families

    for model in eligible model_states:
        residual_records = prior-date exact-hour records, else approved stage fallback
        enforce history gate and NBM maturity
        recency_weights = half_life_21_dates
        scenarios = []
        for prior date i:
            physical = max(observed_max, X_live[model] + residual[model,i])
            official = verified_quantizer(physical + settlement_gap[i])
            scenarios.append(weighted official outcome)
        build model fractional counts, mean probabilities, safe Yes/No bounds
        build corrected point and interval for reporting

    model_weights = prior weights adjusted by shrunk prior-date log score
    apply individual cap, GFS family cap, NBM maturity cap
    renormalize across eligible models

    mixture = weighted model bracket distributions
    p_trade_yes/no = minimum of:
        mixture-count lower bound
        weighted component lower bounds

    spread = max raw X - min raw X
    drift = recent residual median vs long weighted median
    active_hurdle and size multipliers = base + spread + drift policy

    candidates = []
    for each bracket and side Yes/No:
        q = conservative trade probability
        enumerate exact price grid with live fee schedule and quantity
        derive maximum qualifying price
        inspect executable book/depth
        compute exact EV, ROI, Kelly, and event-outcome P&L matrix
        append passing candidate or persist exact rejection reason

    select at most one best incremental event-level candidate by:
        highest conservative net EV
        then higher conservative ROI
        then lower event loss
        then deterministic ticker/side tie-break

    persist complete immutable decision and counterfactuals
    ShadowOrderSink.record(selected candidate or NO_TRADE)
    never call an exchange order endpoint
```
