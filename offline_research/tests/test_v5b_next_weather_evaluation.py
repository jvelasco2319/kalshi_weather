from copy import deepcopy
from datetime import date, timedelta
import pytest
from test_v5b_evaluation import fixture as market_fixture
from v5b_next.weather_evaluation import evaluate_candidate, POLICIES
from v5b_next.weather_model import FAMILIES


def fixture():
    context=market_fixture(64)
    context['weather_features']=[]
    context['label_records']=[]
    for i,d in enumerate(context['dates']):
        context['weather_features'].append({'climate_date':d,'decision_at':f'{d}T18:00:00Z',
            'hrrr_temperature_f_max':67.+i%3, 'gefs_mean_temperature_f_max':66.+i%4,
            'hrrr_cloud_percent_mean':float(i%5)*10, 'hrrr_wind_u_mean_m_s':1.,
            'hrrr_wind_v_mean_m_s':2., 'absolute_sampled_max_disagreement_f':1.})
        context['label_records'].append({'climate_date':d,'reported_high_f':65,
            'issued_at':f'{date.fromisoformat(d)+timedelta(days=1)}T09:00:00Z'})
    return context


@pytest.mark.parametrize('family',FAMILIES)
def test_all_models_use_same_dates_and_fixed_reference(family):
    context=fixture()
    result=evaluate_candidate(context,{'model_id':family,'policy_id':'dollar_profit_both'})
    assert result['common_scoring_dates']==context['dates'][20:]
    assert result['baseline_multiclass_brier']==pytest.approx(.14)
    assert sum(x['selected_days'] for x in result['temporal_folds'])==result['selected_days']
    assert result['forecast_metrics']['forecast_count']==44
    assert result['calibration']['minimum_residual_count']==10
    assert result['holdout_labels_opened'] is False


def test_policies_do_not_retrain_weather_model():
    context=fixture()
    forecasts=[evaluate_candidate(context,{'model_id':'equal_blend_bias','policy_id':p})['weather_forecasts'] for p in POLICIES]
    assert all(x==forecasts[0] for x in forecasts)


def test_scoring_rejects_holdout_context():
    context=fixture();context['partition']='holdout'
    with pytest.raises(ValueError):
        evaluate_candidate(context,{'model_id':'climatology','policy_id':'dollar_profit_both'})
