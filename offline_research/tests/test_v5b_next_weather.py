from datetime import datetime,timedelta,timezone
import math
import pandas as pd
import pytest
from v5b_next.weather_features import FIELDS,POLICY,aggregate_day,validate_rows


def fixture(model):
    cycle=datetime(2026,6,1,6 if model=='hrrr' else 0,tzinfo=timezone.utc)
    rows=[]
    fields=list(FIELDS) if model=='hrrr' else ['avg','spr']
    for field in fields:
        for lead in ((2,8,14,20) if model=='hrrr' else (9,12,15,18,21,24,27,30)):
            row={'climate_date':'2026-06-01','model':model,'point_id':'KLAX','field_id':field if model=='hrrr' else 'temperature_2m','member_id':None if model=='hrrr' else field,'lead_hours':lead,'contains_settlement_label':False,'as_of_validated':True,'availability_policy_id':POLICY,'availability_delay_hours':6,'nominal_issue_time_utc':cycle.isoformat(),'forecast_reference_time_utc':cycle.isoformat(),'source_last_modified_at_utc':(cycle+timedelta(hours=1)).isoformat(),'effective_information_available_at_utc':(cycle+timedelta(hours=6)).isoformat(),'valid_time_utc':(cycle+timedelta(hours=lead)).isoformat(),'eligible_decision_times_utc':['18:00'],'source_sha256':'0'*64,'index_sha256':'1'*64,'source_path':'fixture.grib','index_path':'fixture.idx','ensemble_statistic':None if model=='hrrr' else ('mean' if field=='avg' else 'standard_deviation'),'units':FIELDS[field] if model=='hrrr' else ('degF' if field=='avg' else 'delta_degF'),'value':float(lead if model=='hrrr' else 60+lead if field=='avg' else lead/10),'is_missing':False}
            rows.append(row)
    return pd.DataFrame(rows)


def test_label_free_features_and_aligned_spread():
    row=aggregate_day(fixture('hrrr'),fixture('gefs'),'2026-06-01')
    assert row['hrrr_temperature_f_max']==20
    assert row['gefs_mean_temperature_f_max']==90
    assert row['gefs_spread_delta_f_at_mean_max']==3
    assert row['absolute_sampled_max_disagreement_f']==70
    assert row['historical_publication_proven'] is False
    assert 'reported_high_f' not in row


def test_missing_ceiling_retained_not_imputed():
    h=fixture('hrrr');mask=h.field_id=='cloud_ceiling'
    h.loc[mask,'value']=float('nan');h.loc[mask,'is_missing']=True
    row=aggregate_day(h,fixture('gefs'),'2026-06-01')
    assert row['hrrr_ceiling_ft_mean'] is None
    assert row['hrrr_ceiling_ft_missing_fraction']==1


@pytest.mark.parametrize('column,value',[('source_last_modified_at_utc','2026-06-01T19:00:00+00:00'),('contains_settlement_label',True),('units','kelvin'),('source_sha256',None),('valid_time_utc','2026-06-02T12:00:00+00:00'),('climate_date','2026-08-04')])
def test_invalid_provenance_rejected(column,value):
    h=fixture('hrrr');h.loc[0,column]=value
    with pytest.raises(ValueError):validate_rows(h,'2026-06-01','hrrr')


def test_duplicates_rejected():
    h=fixture('hrrr');h=pd.concat([h.iloc[:-1],h.iloc[:1]],ignore_index=True)
    with pytest.raises(ValueError):validate_rows(h,'2026-06-01','hrrr')


def test_missing_temperature_rejected():
    h=fixture('hrrr');h.loc[0,'value']=float('nan');h.loc[0,'is_missing']=True
    with pytest.raises(ValueError):validate_rows(h,'2026-06-01','hrrr')
