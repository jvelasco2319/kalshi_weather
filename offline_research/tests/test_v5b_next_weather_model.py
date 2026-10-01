from copy import deepcopy
from datetime import date, timedelta
import pytest
from v5b_next.weather_model import forecast_sequence, bracket_probabilities, FAMILIES


def fixture():
    rows, labels = [], []
    for i in range(35):
        day = date(2026, 6, 1)+timedelta(days=i)
        rows.append({'climate_date':str(day), 'decision_at':f'{day}T18:00:00Z',
                     'hrrr_sparse_max_f':70+i*.1, 'gefs_max_mean_f':71+i*.08,
                     'hrrr_cloud_mean_pct':float(i%7)*10, 'hrrr_u_mean_m_s':float(i%4),
                     'hrrr_v_mean_m_s':float(i%3), 'model_abs_disagreement_f':abs(1-i*.02)})
        labels.append({'climate_date':str(day), 'issued_at':f'{day+timedelta(days=1)}T09:00:00Z',
                       'reported_high_f':70+i%5})
    return rows, labels


@pytest.mark.parametrize('family', FAMILIES)
def test_current_and_future_outcomes_do_not_change_prediction(family):
    rows, labels = fixture()
    before = forecast_sequence(rows, labels, family)
    changed = deepcopy(labels)
    for row in changed[20:]:
        row['reported_high_f'] += 40
    after = forecast_sequence(rows, changed, family)
    assert before[0] == after[0]
    assert before[-1]['location_f'] != after[-1]['location_f']
    assert before[0]['training_count'] == 20
    assert before[0]['residual_count'] == 10
    assert max(before[0]['training_dates']) < before[0]['climate_date']
    assert len(before) == 15


def test_future_feature_scaling_cannot_influence_earlier_ridge():
    rows, labels = fixture()
    before = forecast_sequence(rows, labels, 'cloud_wind_disagreement_ridge')
    changed = deepcopy(rows)
    for row in changed[21:]:
        row['hrrr_cloud_mean_pct'] *= 100
    after = forecast_sequence(changed, labels, 'cloud_wind_disagreement_ridge')
    assert before[0] == after[0]


def test_late_labels_fail_common_sample_instead_of_dropping_dates():
    rows, labels = fixture()
    labels[19]['issued_at'] = '2026-09-01T00:00:00Z'
    with pytest.raises(ValueError, match='readiness'):
        forecast_sequence(rows, labels, 'hrrr_bias')


def test_integer_contract_partition():
    forecast={'location_f':70.,'prequential_residuals_f':[0.]*10,'kernel_sigma_f':1.}
    contracts=[{'market_ticker':'low','strike_type':'less','cap_strike':70},
               {'market_ticker':'mid','strike_type':'between','floor_strike':70,'cap_strike':71},
               {'market_ticker':'high','strike_type':'greater','floor_strike':71}]
    p=bracket_probabilities(forecast,contracts)
    assert sum(p.values()) == pytest.approx(1.)
    assert p['mid'] > p['low'] > p['high']
    with pytest.raises(ValueError, match='partition'):
        bracket_probabilities(forecast,contracts[:2])
