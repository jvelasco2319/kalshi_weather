"""Reproduce V5B's development-only V5A leader diagnostic; never open holdout labels."""
from pathlib import Path
import json
from collections import Counter
import pandas as pd
from v5a.development_search import _inputs, evaluate, status, _file_hash


def build(root):
    root = Path(root).resolve()
    universe, predictions, labels = _inputs(root)
    state = status(root)
    result = evaluate(universe, predictions, labels, state['incumbent']['parameters'], include_trades=True)
    dates = universe['split']['development_dates']
    fold4 = {d for i, d in enumerate(dates) if min(4, i * 5 // len(dates)) == 3}
    losses = []
    for trade in result['trades']:
        if trade['climate_date'] not in fold4:
            continue
        row = next(x for x in universe['records'] if x['climate_date'] == trade['climate_date'] and x['market_ticker'] == trade['market_ticker'] and x['contract_side'] == trade['contract_side'])
        losses.append(dict(trade, actual_high_f=labels[trade['climate_date']], floor_strike=row['floor_strike'], cap_strike=row['cap_strike'], strike_type=row['strike_type'], quote_at_utc=row['quote_at_utc']))
    mixes = []
    for weight in (0, .25, .5, .75, 1):
        frame = predictions.copy()
        frame['yes_probability'] = (1-weight)*predictions.yes_probability + weight*predictions.raw_yes_probability
        value = evaluate(universe, frame, labels, result['parameters'])
        mixes.append(dict(raw_weight=weight, **{k:value[k] for k in ('selected_days','aggregate_realized_net_return','multiclass_brier','temporal_folds')}))
    weather = []
    for date in dates:
        folder = root / 'data/normalized/v5p_probability_features' / f'date={date}'
        h = pd.read_parquet(folder / 'hrrr_points.parquet')
        g = pd.read_parquet(folder / 'gefs_summary_points.parquet')
        weather.append(dict(climate_date=date, actual_high_f=labels[date], hrrr_sampled_max_f=float(h[h.field_id=='temperature_2m'].value.max()), gefs_sampled_mean_max_f=float(g[g.member_id=='avg'].value.max()), gefs_spread_max_f=float(g[g.member_id=='spr'].value.max()), cloud_mean_percent=float(h[h.field_id=='total_cloud_cover'].value.mean())))
    best = max(result['trades'], key=lambda x:x['net_profit_dollars'])
    return dict(schema_version='v5b-development-diagnostic-v1', source_campaign_id=state['campaign_id'], source_state_updated_at_utc=state.get('updated_at_utc'), source_leader=result, fold4_dates=sorted(fold4), fold4_trades=losses, raw_calibrated_ablation=mixes, development_weather=weather, best_day=best, remove_best_day_return=(result['total_net_profit_dollars']-best['net_profit_dollars'])/(result['total_entry_outlay_dollars']-best['entry_outlay_dollars']), zero_calibrated_probabilities=int((predictions[predictions.partition=='development'].yes_probability==0).sum()), holdout_labels_opened=False, actual_orders_placed=False, network_used=False)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',default='.')
    args=parser.parse_args()
    root=Path(args.project_root).resolve()
    value=build(root)
    output=root/'reports/v5b-development-diagnostic.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'path':str(output),'sha256':_file_hash(output),'fold4_loss_count':len(value['fold4_trades'])}))


if __name__=='__main__':
    main()
