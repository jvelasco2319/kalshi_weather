"""Export the terminal weather study without changing registered artifacts."""
import csv
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v5b.campaign import checked
from v5b_next.campaign import locate,verify,records_at

root=Path(__file__).resolve().parents[1]
run=locate(root)
registration,state=verify(root,run)
assert state['status']=='FROZEN'
verification=checked(root/'reports/v5b-next-artifact-verification.json')
assert verification['verification']=='PASS' and verification['campaign_id']==state['campaign_id']
frozen=checked(run/'strategy-freeze.json')
records=records_at(run)
assert verification['reproduced_candidates']==len(records)
leader=records[frozen['candidate_id']]
result=leader['result']
controls=checked(run/'reference-controls.json')
passed=[x for x in records.values() if x['development_screen_passed']]
rows=[]
for item in sorted(records.values(),key=lambda x:(x['epoch'],x['parameters']['model_id'])):
    q=item['result'];p=item['parameters']
    rows.append({'candidate_id':item['candidate_id'],'epoch':item['epoch'],**p,
        'selected_days':q['selected_days'],'aggregate_net_return':q['aggregate_realized_net_return'],
        'positive_folds':q['positive_fold_count'],'worst_fold':q['worst_nonempty_fold_return'],
        'brier':q['multiclass_brier'],'predictive_mean_mae_f':q['forecast_metrics']['mae_predictive_mean_f'],
        'coverage_80':q['forecast_metrics']['coverage_80'],'coverage_95':q['forecast_metrics']['coverage_95'],
        'failed_gates':'|'.join(item['gate_failures'])})
with (run/'candidate-registry.csv').open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
table='\n'.join(f"| {x['model_id']} | {x['policy_id']} | {x['selected_days']} | {x['aggregate_net_return']:.2%} | {x['positive_folds']}/5 | {x['brier']:.4f} | {x['failed_gates'].replace('|', '; ') or 'none'} |" for x in rows)
forecast_rows=[x for x in rows if x['epoch']==1]
forecast_table='\n'.join(f"| {x['model_id']} | {x['predictive_mean_mae_f']:.3f} | {x['brier']:.4f} | {x['coverage_80']:.2%} | {x['coverage_95']:.2%} |" for x in forecast_rows)
fold_table='\n'.join(f"| {f['fold']} | {f['selected_days']} | {f['aggregate_realized_net_return']:.2%} |" for f in result['temporal_folds'])
c=controls['frozen_v5b_policy'];u=controls['uniform_listed_brackets_dollar_profit_both']
text=f'''# V5B weather-feature research: terminal report

Campaign `{state['campaign_id']}` completed {state['epoch']} reviewed epochs and {len(records)} registered candidates. **{len(passed)} candidates passed every development gate. Independent confirmation remains unavailable. The 10% expected-return target is not confirmed.**

This is a separate, preregistered mechanism study after the first 101-specification V5B pilot. It preserves the earlier code, data, freeze and findings. It does not erase the earlier search exposure or convert reused development dates into a fresh test set.

## What changed in the architecture

The new engine stores immutable proposal queues and progress cursors, adopts completed candidate writes after an interrupted state update, preserves original deadlines and counters, validates both hypothesis and evaluation specifications, and requires actual terminal review before freezing. It binds Python/NumPy/pandas versions as well as source and input hashes. Seventy-four synthetic tests passed before registration, including independently authored timing, crash, leakage and settlement tests.

Weather research now uses actual cached temperature, cloud, wind and sampled-model disagreement features. The feature audit verified 64 development dates and 2,560 source rows. All model fits use previously issued official labels; prefix-only scaling and ridge fits never use the current outcome. Predictive distributions use earlier one-step residuals rather than fitted residuals from scored observations. Six model families are crossed with four predeclared selection policies. Agents reviewed each completed arm and admitted the next comparison; deterministic scheduling is not itself model reasoning.

The first 20 dates are training warmup, leaving the same44June21â€“August3,2026 scoring dates for every model. Five new chronological folds are defined on these 44 dates. Their fourth fold is July18â€“26, not the original July10â€“22 Fold4; do not compare these fold numbers as if they identified the same events.

## Forecast mechanisms and quality

| Model | Predictive-mean MAE, Â°F | Brier |80% interval coverage|95% interval coverage|
|---|---:|---:|---:|---:|
{forecast_table}

The cloud-only model tests residual temperature error against forecast cloud cover. The larger ridge model adds forecast U/V winds and absolute sampled HRRR/GEFS disagreement. These regressions condition the predicted location; they do not fit regime-specific variance, recover individual ensemble members or prove a marine-layer cause. All ridge penalties and the1Â°F residual-kernel bandwidth were fixed before scoring. Coefficients, standardization, fit dates, residual dates and predictive intervals are retained in every candidate's weather forecasts.

Forecast score improvements must be judged separately from profitability. The climatology model is the primary no-weather model comparison. The Brier promotion gate uses the inherited calibrated probability chain on the identical 44 dates, fixed before registration.

## Complete registered economic comparisons

Every row uses one purchased contract at most per selected date, the inherited exact fee and integer-settlement rules, and its reported execution grade. Aggregate net return is net simulated profit divided by total entry outlay, not an actual account return.

|Model|Policy|Selected dates|Aggregate net return|Positive folds|Brier|Failed gates|
|---|---|---:|---:|---:|---:|---|
{table}

The grid contains six fitted weather distributions and four contract-policy comparisons, not 24 independent sources of predictive evidence. Reusing the same forecasts under different selection policies isolates a policy difference but does not create more historical observations. No threshold, fold, date exclusion, model or feature was tuned after observing these results.

## Frozen diagnostic selection

The registered ranking froze `{frozen['candidate_id']}`: `{leader['parameters']}`. Its failed gates are **{leader['gate_failures']}**. A freeze records the selected research specification; it does not waive failures or authorize confirmation/trading.

It selected {result['selected_days']} dates, returned {result['aggregate_realized_net_return']:.4%} on total entry outlay, and had evidence composition `{result['execution_grade_counts']}`. Fixed-selection +2-cent return was {result['adverse_stress']['2']['aggregate_realized_net_return']:.4%}; removing the best profitable date left {result['best_day_removed_return']:.4%}. Model-implied expected return was {result['mean_expected_net_return']:.4%}; that forecast-dependent estimate is not a validated true expectation.

|Fold|Selected dates|Aggregate net return|
|---|---:|---:|
{fold_table}

## Reference controls and prior findings

Two predeclared reference replays are recorded outside the 24-candidate budget and cannot enter selection. The prior frozen V5B policy on the same 44 dates selected {c['selected_days']} days and returned {c['aggregate_realized_net_return']:.4%}. Uniform probabilities across listed brackets with dollar-profit selection selected {u['selected_days']} days and returned {u['aggregate_realized_net_return']:.4%}. Uniform listed brackets are not a meteorological climatology. The inherited policy differs in both probabilities and selection, so its contrast cannot attribute a difference to one component alone.

The earlier101-specification pilot's +26.83% / 30-trade development result remains a separate, adaptively selected research lead with documented execution sensitivity. This weather study neither confirms that result nor replaces its limitations with an unsupported claim of progress toward guaranteed profit.

## Evidence limits and next decision

These data remain exposed development history. B/B+ quotes are not verified fills; even Grade-A historical books do not demonstrate actual account execution. Original historical weather-publication timing is not independently proven: availability uses the documented conservative bound. Sparse forecast maxima are not full hourly daily maxima. The continuous-temperature distribution mapped to integerÂ°F bins is an explicit modeling assumption. The small sample and many earlier searches limit inference.

V5A's 28-day holdout remains reserved and was not accessed. A separate ready untouched confirmation bundle has not been established. Unsupported research includes member-level tails, run-to-run changes, alternate decision-time replay and depth/partial-fill modeling. Those require new auditable inputs and separate preregistration; repeating this completed grid is not a new experiment.

Review artifacts, immutable queues, all prediction/trade ledgers, per-colony populations, Pareto archive, deterministic criticism, runtime/source bindings and terminal-review approval are in this campaign directory. See also `docs/V5B_NEXT_RESEARCH_PROTOCOL.md`, the three `reports/v5b-next-*-audit.md` reports, and `reference-controls.json`. The appropriate conclusion comes from these fixed comparisons and their failures, not from continuing until a favorable backtest appears. No orders or live feeds were used.
'''
(root/'reports/v5b-next-final-report.md').write_text(text,encoding='utf-8')
print({'report':'reports/v5b-next-final-report.md','candidates':len(records),'all_gate_candidates':len(passed),'frozen':frozen['candidate_id']})
