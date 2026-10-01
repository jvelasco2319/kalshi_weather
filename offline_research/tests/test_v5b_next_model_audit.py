"""Independent synthetic temporal/probability audit; no historical scoring."""
from copy import deepcopy
from datetime import date,timedelta
from statistics import NormalDist
import pytest
from v5b_next.weather_model import forecast_sequence,bracket_probabilities,FAMILIES


def synthetic():
    rows=[];labels=[]
    for i in range(40):
        d=date(2026,1,1)+timedelta(days=i)
        rows.append({'climate_date':str(d),'decision_at':f'{d}T18:00:00Z','hrrr_sparse_max_f':60+i/10,'gefs_max_mean_f':61+i/11,'hrrr_cloud_mean_pct':float(i%3)*20,'hrrr_u_mean_m_s':float(i%5),'hrrr_v_mean_m_s':float(i%4),'model_abs_disagreement_f':1.})
        labels.append({'climate_date':str(d),'reported_high_f':60+i%6,'issued_at':f'{d+timedelta(days=1)}T09:00:00Z'})
    return rows,labels


@pytest.mark.parametrize('family',FAMILIES)
def test_delayed_label_value_cannot_affect_predictions_until_release(family):
    rows,labels=synthetic()
    labels[24]['issued_at']=rows[27]['climate_date']+'T17:00:00Z'
    original=forecast_sequence(rows,labels,family)
    altered=deepcopy(labels);altered[24]['reported_high_f']+=50
    changed=forecast_sequence(rows,altered,family)
    assert original[:7]==changed[:7]
    assert original[7]['location_f']!=changed[7]['location_f']
    assert labels[24]['climate_date'] not in original[6]['training_dates']
    assert labels[24]['climate_date'] not in original[6]['residual_dates']
    assert labels[24]['climate_date'] in original[7]['training_dates']


def test_equal_timestamp_not_available():
    rows,labels=synthetic();labels[24]['issued_at']=rows[26]['decision_at']
    forecasts=forecast_sequence(rows,labels,'hrrr_bias')
    assert labels[24]['climate_date'] not in forecasts[6]['training_dates']
    assert labels[24]['climate_date'] in forecasts[7]['training_dates']


def test_constant_bias_units_and_no_residual_double_fit():
    rows,labels=synthetic()
    for row,label in zip(rows,labels):
        row['hrrr_sparse_max_f']=70.;label['reported_high_f']=73
    forecasts=forecast_sequence(rows,labels,'hrrr_bias')
    assert all(x['location_f']==73 for x in forecasts)
    assert all(set(x['prequential_residuals_f'])=={0.} for x in forecasts)


def test_exact_half_degree_integer_boundaries():
    distribution=NormalDist(mu=70,sigma=1)
    forecast={'location_f':70.,'prequential_residuals_f':[0.]*10,'kernel_sigma_f':1.}
    contracts=[{'market_ticker':'low','strike_type':'less','cap_strike':70},
               {'market_ticker':'mid','strike_type':'between','floor_strike':70,'cap_strike':71},
               {'market_ticker':'high','strike_type':'greater','floor_strike':71}]
    p=bracket_probabilities(forecast,contracts)
    assert p['low']==pytest.approx(distribution.cdf(69.5))
    assert p['mid']==pytest.approx(distribution.cdf(71.5)-distribution.cdf(69.5))
    assert p['high']==pytest.approx(1-distribution.cdf(71.5))


def test_extreme_tail_gap_rejected_structurally():
    forecast={'location_f':70.,'prequential_residuals_f':[0.]*10,'kernel_sigma_f':1.}
    contracts=[{'market_ticker':'low','strike_type':'less','cap_strike':70},
               {'market_ticker':'mid','strike_type':'between','floor_strike':70,'cap_strike':99},
               {'market_ticker':'high','strike_type':'greater','floor_strike':101}]
    with pytest.raises(ValueError):bracket_probabilities(forecast,contracts)


@pytest.mark.parametrize('bad_upper',[98,100.5])
def test_extreme_tail_overlap_and_noninteger_rejected(bad_upper):
    forecast={'location_f':70.,'prequential_residuals_f':[0.]*10,'kernel_sigma_f':1.}
    contracts=[{'market_ticker':'low','strike_type':'less','cap_strike':70},
               {'market_ticker':'mid','strike_type':'between','floor_strike':70,'cap_strike':99},
               {'market_ticker':'high','strike_type':'greater','floor_strike':bad_upper}]
    with pytest.raises(ValueError):bracket_probabilities(forecast,contracts)


def test_unregistered_decision_hour_rejected():
    rows,labels=synthetic();rows[22]['decision_at']=rows[22]['climate_date']+'T17:00:00Z'
    with pytest.raises(ValueError):forecast_sequence(rows,labels,'hrrr_bias')
