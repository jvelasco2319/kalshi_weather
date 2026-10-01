# Research foundation

Status: background reconstruction. The current [GOALS.md](../GOALS.md) supersedes this plan. Both goals are historical and offline; no online or prospective collection is authorized.

Source: Apply Navier Stokes Method, https://chatgpt.com/c/6aa9e345-d790-83e8-a399-b32ae86b65bf. This is a concise reconstruction of the agreed design, not a recovered copy of the original downloadable foundation package.

## Research cycle

1. Establish simple forecast baselines and a common deterministic experiment harness.
2. Assign independent hypotheses to bounded research groups.
3. Execute reproducible experiments using data available at each historical prediction time.
4. Store structured evidence, failures, limitations, and versioned artifacts.
5. Have a synthesizer combine compatible findings into new hypotheses.
6. Reallocate effort toward promising results while reserving capacity for alternatives and falsification.
7. Independently replicate candidates, challenge them, and evaluate frozen candidates on protected data.
8. Replay historical forecasts using only information available at each decision time. Compare opportunities against documented historical price evidence and realistic costs.

Keep finite historical acquisition separate from frozen offline experiments and the changing research system.

## 36 tracks reconstructed from the chat

01. Numerical weather-model ensembles
02. KLAX-specific model bias
03. Lead-time-dependent model weighting
04. Forecast revisions
05. Marine-layer depth
06. Cloud-clearing timing
07. Inversion characteristics
08. Onshore/offshore flow
09. Sea-breeze timing
10. Regional pressure gradients
11. Santa Ana regimes
12. Seasonal coastal regimes
13. Current KLAX temperature
14. Observed high-so-far (only a hard bound if compatible with settlement measurement)
15. Heating rate
16. Temperature curvature
17. Dew point
18. Wind observations
19. Cloud observations
20. Pressure observations
21. ASOS/METAR precision
22. Temperature rounding uncertainty
23. Climate-day timing
24. Preliminary versus final values
25. Climatology prior
26. Historical analogs
27. Bayesian combination
28. Classical regression
29. Gradient boosting
30. Probability calibration
31. NWS forecast-discussion features
32. Independent from-scratch challengers
33. Model-versus-market probabilities
34. Consistency across mutually exclusive temperature bins
35. Market microstructure and information timing
36. Adversarial validation and research-process effectiveness

## Kalshi-specific priorities

The current scope is Kalshi daily-high-temperature contracts for KLAX, subject to verification of the exact market rules and station. Other cities, hourly markets, and a general-purpose multi-domain framework are deferred. The 36 tracks are a backlog, not 36 concurrent agents. Preserve their identifiers.

Use six logical groups, staffed in phases within the available worker limit:

| Group | Primary question | Existing tracks |
|---|---|---|
| A: Settlement and data | Are the forecast target, official label, climate day, rounding, source versions, and availability timestamps correct? | R21-R24; data checks for every track |
| B: Baseline forecasts | What simple reproducible probability model can we build, and does station-specific correction improve it? | R01-R04, R25-R29 |
| C: Intraday information | Do current observations, remaining heating, clouds, winds, or coastal regimes add predictive information at a fixed cutoff? | R05-R20, R31 |
| D: Contract probabilities | Are probabilities reliable near actual contract boundaries and among the opportunities the selection rule chooses? | R30 with R21-R24 |
| E: Market opportunities | At which times, sides, prices, and quantities does a candidate show at least 10% expected net ROI after realistic costs? | R33-R35 |
| F: Independent challenge | Does the improvement reproduce, survive leakage and selection checks, and beat simpler alternatives? | R32, R36 |

Collect market prices, depth, contract metadata, and weather inputs alongside one another from the start. Market research need not wait until a sophisticated weather model exists. Forecast evaluation and economic evaluation remain separate tests.

Initial research questions, in dependency order:

1. Confirm the exact contract-to-official-outcome mapping and timestamp conventions.
2. Audit weather forecast archives, observation histories, market prices, and available execution evidence. Treat missing historical source versions or depth as limitations rather than inventing them.
3. Establish simple climatology and available weather-guidance probability baselines on chronological folds.
4. Test incremental value from intraday observations and remaining-heating estimates.
5. Test calibration at actual temperature-bin boundaries and in the selected-opportunity subset.
6. Compare model probabilities with contemporaneous executable YES and NO prices at predefined decision times. Assess incremental information beyond market-derived baselines while acknowledging spread and margin effects.
7. Screen using size-dependent costs and available depth. Record no-trade decisions, quote age, time to settlement, and why each opportunity was accepted or rejected. Do not assume a passive order would fill merely because a historical price touched its limit.
8. Independently reproduce candidate results, assess uncertainty with weather-day dependence, and evaluate the frozen selection policy on protected historical data.

My initial research priority is selective intraday opportunities supported by both weather and market evidence. This is an untested prioritization, not a finding that market prices lag or that such opportunities exist. Treat information latency, coastal-regime errors, and contract-boundary miscalibration as hypotheses.

Defer elaborate model families, large-scale agent expansion, and subsecond trading infrastructure until the simpler pipeline provides usable evidence.

## Feasibility and success conditions

The 10% target is a research screen for estimated net return, not a forced result. A strategy with qualifying opportunities can still have losses. Estimate uncertainty in the selected trades' probabilities; do not use model confidence alone as evidence.

Report opportunity frequency, executable capacity, holding time, net expected and realized returns, calibration, uncertainty, and drawdowns together. Account for same-day dependence across bins, repeated forecasts, and positions. A small set of low-capacity opportunities may satisfy the per-trade target while producing little total dollar profit.

Before a full campaign, define a reference order size, desired opportunity frequency, and evaluation horizon. These remain open; do not silently invent them. Initial read-only collection and forecast checks do not depend on resolving all of them.

Advance only if the data can support the test and a frozen selection policy demonstrates credible performance on protected unseen historical data. Stop or narrow scope if estimated advantages vanish under realistic costs, depend on unavailable inputs or fills, fail calibration, or do not reproduce. A no-trade or no-improvement result is valid.

Current evidence status: no joined weather/market dataset, executable-opportunity study, or validated trading edge has yet been produced in this repository. No numerical probability of research success is justified.

Official references checked for planning:

- [Kalshi weather-market settlement guidance](https://help.kalshi.com/en/articles/13823837-weather-markets): daily temperature settlement source and climate-day conventions; individual market rules remain authoritative.
- [Kalshi historical data](https://docs.kalshi.com/getting_started/historical_data): historical market, candle, and trade endpoints. This does not by itself establish complete historical order-book reconstruction for our target markets.
- [Kalshi order book](https://help.kalshi.com/en/articles/13823828-the-orderbook): quotes include prices and quantities.
- [Kalshi fees](https://help.kalshi.com/en/articles/13823805-fees): verify the applicable fee schedule, including any maker fees.

## Build sequence

- M0: Confirm settlement target, source access, data history, and timestamps. The confirmed target is at least 10% expected net return per trade on total entry outlay. Define evaluation horizon and risk limits before an economic campaign.
- M1: Implement finite historical acquisition, immutable storage, and outcome reconciliation.
- M2: Implement simple baselines, chronological evaluation, calibration metrics, and leakage tests.
- M3: Implement registries, budgets, task state machine, bounded scheduling, recovery, and protected evaluation access.
- M4: Run a small multi-agent pilot with synthesis, replication, and adversarial review.
- M5: Freeze a candidate and evaluate on protected unseen historical evidence. Report net returns, uncertainty, drawdowns, and capital usage with a predefined evaluation horizon.

No date or return target is guaranteed. A forecast improvement does not establish a profitable strategy. Do not use repeated holdout evaluation or selective reporting to manufacture success.

## Economic evaluation

The user confirmed 10% expected return per trade. For the initial evaluation, use purchased binary contracts held to settlement and measure net expected return on total entry outlay.

Let q be the estimated probability that the purchased side pays $1, c its executable purchase price per contract, f entry fees allocated per contract, and s expected additional settlement costs per contract, if any. Then:

    expected_net_profit = q - c - f - s
    expected_net_return = (q - c - f - s) / (c + f)
    research_screen = expected_net_return >= 0.10

Use q = p for YES and q = 1 - p for NO, where p is the estimated YES probability. Require positive entry outlay. Use actual size-dependent fee rounding and depth-weighted execution prices; include spread and slippage in c rather than double-counting them as additional costs. If early exits are evaluated later, specify a separate exit-price and cost model.

Illustration only: q = 0.60, c = $0.50, f = $0.02, and s = $0 gives expected net return of $0.08 / $0.52 = 15.38%. The fee is hypothetical, not a current fee quotation.

Crossing the 10% screen using a model estimate is not proof of an edge. Report probability uncertainty, calibration, opportunity count, realized outcomes, and selection effects. Define any conservative uncertainty-based admission rule before evaluation. A no-trade result is valid when no opportunities qualify. Do not tune estimates merely to exceed the threshold.

Expected return on trade cost is not account return or a promise that any individual trade earns 10%. Account return also depends on capital held idle, sizing, correlated positions, losses, and drawdowns. Validate the selection policy on chronological unseen historical data. Historical realized returns are simulated results, not actual account profits.

Verify applicable fees at evaluation time: https://help.kalshi.com/en/articles/13823805-fees . Do not hard-code an assumed universal fee rate.

Permitted campaign outcomes include improvement, no improvement, insufficient data, invalidation, exhausted budget, and blocked. Set budgets and stopping rules before running.
