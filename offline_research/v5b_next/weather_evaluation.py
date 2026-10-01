"""Weather-family development replay with a common fixed scoring prefix."""
from copy import deepcopy
from pathlib import Path
import json

from v5b.campaign import checked, filehash
from v5b.evaluation import load_development as load_market, evaluate_candidate as score_market
from v5b_next.weather_model import forecast_sequence, bracket_probabilities, FAMILIES

FEATURE_NAMES = {'hrrr':'hrrr_temperature_f_max', 'gefs':'gefs_mean_temperature_f_max',
    'cloud':'hrrr_cloud_percent_mean', 'u':'hrrr_wind_u_mean_m_s',
    'v':'hrrr_wind_v_mean_m_s', 'disagreement':'absolute_sampled_max_disagreement_f'}
FEATURE_FILE = 'data/development/v5b_next/weather_features.json'
FEATURE_MANIFEST = 'data/development/v5b_next/weather_features_manifest.json'
POLICIES = {
    'dollar_profit_both':{'selection_mode':'expected_profit'},
    'relative_return_both':{'selection_mode':'expected_return'},
    'dollar_profit_no':{'selection_mode':'expected_profit','allowed_sides':['NO']},
    'dollar_profit_paid':{'selection_mode':'expected_profit','allowed_grades':['A','B_PLUS']},
}


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {'model_id','policy_id'}:
        raise ValueError('model_id and policy_id required; no extra controls')
    if spec['model_id'] not in FAMILIES or spec['policy_id'] not in POLICIES:
        raise ValueError('unregistered model or economic policy')
    return dict(spec)


def load_development(root):
    root = Path(root).resolve()
    context = load_market(root)
    manifest = checked(root/FEATURE_MANIFEST)
    if manifest['output']['path'] != FEATURE_FILE or manifest['output']['sha256'] != filehash(root/FEATURE_FILE):
        raise ValueError('weather feature output binding differs')
    for name, expected in manifest['bindings'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root) or filehash(path) != expected:
            raise ValueError(f'weather provenance binding differs: {name}')
    payload = json.loads((root/FEATURE_FILE).read_text(encoding='utf-8'))
    features = payload['features']
    if [x['climate_date'] for x in features] != context['dates']:
        raise ValueError('weather/market cohort mismatch')
    labels = json.loads((root/'data/development/v5a/development_labels.json').read_text(encoding='utf-8'))['labels']
    context.update(weather_features=features, label_records=labels)
    context['input_bindings'].update({p:filehash(root/p) for p in [FEATURE_FILE,FEATURE_MANIFEST]})
    return context


def evaluate_candidate(context, spec):
    spec = validate_spec(spec)
    forecasts = forecast_sequence(context['weather_features'],context['label_records'],spec['model_id'],FEATURE_NAMES)
    score_dates = context['dates'][20:]
    if len(context['dates']) != 64 or [x['climate_date'] for x in forecasts] != score_dates:
        raise ValueError('all families must score the same44 dates after20-date warmup')
    probabilities = {}
    for forecast in forecasts:
        day = forecast['climate_date']
        contracts = [row for row in context['rows_by_date'][day] if row['contract_side']=='YES']
        for ticker, probability in bracket_probabilities(forecast,contracts).items():
            probabilities[(day,ticker)] = probability
    scoring = {**context,'dates':score_dates, 'labels':{d:context['labels'][d] for d in score_dates},
               'rows_by_date':{d:context['rows_by_date'][d] for d in score_dates},
               'probabilities':probabilities,'raw_probabilities':probabilities}
    result = score_market(scoring,POLICIES[spec['policy_id']])
    # The scorer's reference normally uses its own input probability table.
    # Keep the inherited calibrated reference fixed across new model families.
    baseline = {**scoring,'probabilities':context['probabilities'],
                'raw_probabilities':context['raw_probabilities']}
    reference = score_market(baseline,POLICIES[spec['policy_id']])
    result['baseline_multiclass_brier'] = reference['multiclass_brier']
    result['execution_parameters'] = result['parameters']
    result['parameters'] = spec
    result['schema_version'] = 'v5b-next-weather-development-v1'
    result['weather_forecasts'] = forecasts
    result['common_scoring_dates'] = score_dates
    result['warmup_dates'] = context['dates'][:20]
    result['forecast_metrics'] = {
        'mae_location_f':sum(abs(f['location_f']-context['labels'][f['climate_date']]) for f in forecasts)/len(forecasts),
        'mae_predictive_mean_f':sum(abs(f['predictive_mean_f']-context['labels'][f['climate_date']]) for f in forecasts)/len(forecasts),
        'forecast_count':len(forecasts),
        'reference_brier':reference['multiclass_brier'],
        'forecast_brier':result['multiclass_brier'],
    }
    for level in (80,95):
        key=f'interval_{level}_f'
        result['forecast_metrics'][f'coverage_{level}'] = sum(f[key][0]<=context['labels'][f['climate_date']]<=f[key][1] for f in forecasts)/len(forecasts)
        result['forecast_metrics'][f'mean_width_{level}_f'] = sum(f[key][1]-f[key][0] for f in forecasts)/len(forecasts)
    result['calibration'] = {'method':'past_prequential_residual_kernel',
        'chronological_only':True,'minimum_training_count':min(x['training_count'] for x in forecasts),
        'minimum_residual_count':min(x['residual_count'] for x in forecasts),
        'kernel_sigma_f':1.}
    result['limitations'] = [
        'Repeatedly exposed development history; no independent confirmation.',
        'Sparse forecast maxima are proxies, not full hourly daily maxima.',
        'Conservative model publication bounds do not prove historical vendor receipt.',
        'Rounded integer-temperature mapping assumes a continuous latent temperature distribution.',
        'Historical quote grades do not establish fills. No orders placed.',
        'Five chronological folds use44 common scoring dates; they are not the earlier64-date fold boundaries.',
    ]
    json.dumps(result,allow_nan=False)
    return result
