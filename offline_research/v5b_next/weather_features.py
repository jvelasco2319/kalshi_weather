"""Build label-free development weather features with audited conservative as-of bounds.

No model fitting, outcome reading, network access or predictive experiment occurs.
Archived publication metadata is not proof of original real-time availability.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import math
import pandas as pd

POLICY = 'max_nominal_plus_6h_archived_last_modified_v2'
UNIVERSE = 'data/manifests/v5a_outcome_blind_universe.json'
FIELDS = {'temperature_2m':'degF','total_cloud_cover':'percent','cloud_ceiling':'ft',
          'wind_u_10m':'m_s','wind_v_10m':'m_s','mean_sea_level_pressure':'hPa'}
OUT = 'data/development/v5b_next'


def file_hash(path):
    h=sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def content_hash(obj,field):
    return sha256(json.dumps({k:v for k,v in obj.items() if k!=field},sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def checked(path,field):
    x=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if x.get(field)!=content_hash(x,field): raise ValueError(f'Invalid manifest hash: {path}')
    return x


def timestamp(value):
    result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None: raise ValueError('Naive provenance timestamp')
    return result


def validate_rows(frame, climate_date, model):
    """Reject missing provenance, mixed dates, wrong fields/units or post-decision data."""
    expected={(field,lead) for field in FIELDS for lead in (2,8,14,20)} if model=='hrrr' else {(member,lead) for member in ('avg','spr') for lead in (9,12,15,18,21,24,27,30)}
    key='field_id' if model=='hrrr' else 'member_id'
    observed=[(str(r[key]),int(r['lead_hours'])) for r in frame.to_dict('records')]
    if len(observed)!=len(expected) or set(observed)!=expected: raise ValueError('Missing or duplicate field/lead coverage')
    decision=timestamp(climate_date+'T18:00:00+00:00')
    for row in frame.to_dict('records'):
        if row['climate_date']!=climate_date or row['model']!=model or row['point_id']!='KLAX': raise ValueError('Wrong date/model/station')
        if row['contains_settlement_label']!=False or row['as_of_validated']!=True: raise ValueError('Unsafe feature provenance')
        if row['availability_policy_id']!=POLICY or int(row['availability_delay_hours'])!=6: raise ValueError('Unregistered availability policy')
        nominal=timestamp(row['nominal_issue_time_utc'])
        reference=timestamp(row['forecast_reference_time_utc'])
        archived=timestamp(row['source_last_modified_at_utc'])
        available=timestamp(row['effective_information_available_at_utc'])
        if nominal!=reference or nominal.date().isoformat()!=climate_date or nominal.hour!=(6 if model=='hrrr' else 0): raise ValueError('Wrong model cycle')
        if timestamp(row['valid_time_utc'])!=reference+timedelta(hours=int(row['lead_hours'])): raise ValueError('Lead/valid-time mismatch')
        if available!=max(nominal+timedelta(hours=6),archived) or available>decision: raise ValueError('Unavailable as-of provenance')
        if '18:00' not in list(row['eligible_decision_times_utc']): raise ValueError('18UTC not eligible')
        for name in ('source_sha256','index_sha256'):
            value=row[name]
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value): raise ValueError('Missing provenance hash')
        if not row['source_path'] or not row['index_path']: raise ValueError('Missing source path')
        if model=='gefs' and (row['field_id']!='temperature_2m' or row['ensemble_statistic']!=('mean' if row['member_id']=='avg' else 'standard_deviation')): raise ValueError('Unexpected GEFS statistic')
        unit=FIELDS[row['field_id']] if model=='hrrr' else ('degF' if row['member_id']=='avg' else 'delta_degF')
        if row['units']!=unit: raise ValueError('Unexpected units')
        value=row['value']
        if bool(row['is_missing']):
            if model!='hrrr' or row['field_id']!='cloud_ceiling' or not pd.isna(value): raise ValueError('Required feature missing')
        elif not math.isfinite(float(value)): raise ValueError('Nonfinite feature')
        if not row['is_missing'] and row['field_id']=='total_cloud_cover' and not 0<=value<=100: raise ValueError('Invalid cloud percent')
        if model=='gefs' and row['member_id']=='spr' and value<0: raise ValueError('Negative ensemble spread')


def aggregate_day(hrrr,gefs,climate_date):
    validate_rows(hrrr,climate_date,'hrrr');validate_rows(gefs,climate_date,'gefs')
    out={'climate_date':climate_date,'partition':'development','decision_at':climate_date+'T18:00:00+00:00','decision_time_utc':'18:00','availability_policy_id':POLICY,'historical_publication_proven':False}
    mapping={'temperature_2m':'hrrr_temperature_f','total_cloud_cover':'hrrr_cloud_percent','mean_sea_level_pressure':'hrrr_pressure_hpa','cloud_ceiling':'hrrr_ceiling_ft'}
    for field,prefix in mapping.items():
        vals=hrrr[hrrr.field_id==field].value.dropna()
        for stat in ('min','mean','max'):out[f'{prefix}_{stat}']=float(getattr(vals,stat)()) if len(vals) else None
        out[f'{prefix}_missing_fraction']=float(1-len(vals)/4)
    u=hrrr[hrrr.field_id=='wind_u_10m'].set_index('lead_hours').value.sort_index()
    v=hrrr[hrrr.field_id=='wind_v_10m'].set_index('lead_hours').value.sort_index()
    speed=(u*u+v*v)**.5
    out.update(hrrr_wind_u_mean_m_s=float(u.mean()),hrrr_wind_v_mean_m_s=float(v.mean()),hrrr_wind_speed_mean_m_s=float(speed.mean()),hrrr_wind_speed_max_m_s=float(speed.max()))
    for member,prefix in [('avg','gefs_mean_temperature_f'),('spr','gefs_spread_delta_f')]:
        values=gefs[gefs.member_id==member].value
        for stat in ('min','mean','max'):out[f'{prefix}_{stat}']=float(getattr(values,stat)())
    mean_rows=gefs[gefs.member_id=='avg'].sort_values('lead_hours')
    max_lead=int(mean_rows.loc[mean_rows.value.idxmax(),'lead_hours'])
    out['gefs_spread_delta_f_at_mean_max']=float(gefs[(gefs.member_id=='spr')&(gefs.lead_hours==max_lead)].value.iloc[0])
    out['gefs_minus_hrrr_sampled_max_f']=out['gefs_mean_temperature_f_max']-out['hrrr_temperature_f_max']
    out['absolute_sampled_max_disagreement_f']=abs(out['gefs_minus_hrrr_sampled_max_f'])
    out['hrrr_pressure_range_hpa']=out['hrrr_pressure_hpa_max']-out['hrrr_pressure_hpa_min']
    return out


def build(root):
    root=Path(root).resolve();universe=checked(root/UNIVERSE,'self_sha256')
    dates=universe['split']['development_dates']
    if dates!=sorted(set(dates)) or len(dates)!=64 or set(dates)&set(universe['split']['holdout_dates']):raise ValueError('Development boundary differs')
    if dates[0]!='2026-06-01' or dates[-1]!='2026-08-03':raise ValueError('Unregistered development dates')
    bindings={UNIVERSE:file_hash(root/UNIVERSE)};source_hashes={};features=[];missing=0;verified_rows=0
    for day in dates:
        folder=root/'data/normalized/v5p_probability_features'/f'date={day}'
        manifest=checked(folder/'manifest.json','manifest_sha256')
        if manifest['climate_date']!=day or manifest['decision_time_utc']!='18:00' or manifest['status']!='NORMALIZED_FEATURES_ONLY' or manifest['protected_confirmation_labels_read']!=False:raise ValueError('Unsafe day manifest')
        bindings[(folder/'manifest.json').relative_to(root).as_posix()]=file_hash(folder/'manifest.json')
        frames={}
        for model,name in [('hrrr','hrrr_points.parquet'),('gefs','gefs_summary_points.parquet')]:
            path=folder/name;relative=path.relative_to(root).as_posix()
            binding=next(x for x in manifest['outputs'] if x['path']==relative)
            actual=file_hash(path)
            if actual!=binding['sha256'] or path.stat().st_size!=binding['bytes']:raise ValueError('Normalized source binding changed')
            bindings[relative]=actual;frame=pd.read_parquet(path);validate_rows(frame,day,model);verified_rows+=len(frame)
            if len(frame)!=binding['rows']:raise ValueError('Row count binding changed')
            for row in frame.to_dict('records'):
                for field,hashfield in [('source_path','source_sha256'),('index_path','index_sha256')]:
                    source=Path(row[field]).resolve()
                    if not source.is_relative_to(root/'data/raw/v5p/weather'):raise ValueError('Source path outside weather archive')
                    rel=source.relative_to(root).as_posix();expected=row[hashfield]
                    if rel not in source_hashes:source_hashes[rel]=file_hash(source)
                    if source_hashes[rel]!=expected:raise ValueError('Raw source/index hash differs')
                if row['historical_availability_proven']!=False:raise ValueError('Unexpected changed publication-proof classification')
            missing+=int(frame.is_missing.sum());frames[model]=frame
        features.append(aggregate_day(frames['hrrr'],frames['gefs'],day))
    manifest={'schema':'v5b-next-weather-features-v1','status':'DEVELOPMENT_FEATURES_READY_CONSERVATIVE_ASOF','dates':dates,'date_count':len(dates),'verified_source_rows':verified_rows,'missing_optional_ceiling_rows':missing,'bindings':bindings,'raw_source_bindings':source_hashes,'availability_policy_id':POLICY,'historical_publication_proven':False,'labels_read':False,'protected_labels_read':False,'network_used':False,'model_fitting_performed':False,'limitations':['Availability uses conservative max(nominal+6h, archived last-modified); original publication is not proven.','Four HRRR and eight GEFS valid times are sampled; maxima are not complete hourly daily maxima.','GEFS mean/spread cannot reconstruct individual-member tails.','Ceiling missingness is retained, not imputed or interpreted as clear sky.','One cycle/model; run-to-run changes unavailable.','Forecast cloud/wind summaries are predictors, not observed marine-layer labels.']}
    manifest['capabilities']=['sampled_model_temperature_maxima','ensemble_mean_and_spread_summaries','forecast_cloud_cover','forecast_wind_vectors','forecast_pressure','sampled_model_disagreement']
    manifest['unsupported_capabilities']=['individual_ensemble_member_tails','run_to_run_forecast_changes','observed_marine_layer_regimes','exact_hourly_daily_maxima','original_publication_proof']
    return features,manifest


def write(root):
    root=Path(root).resolve();features,manifest=build(root);folder=root/OUT;folder.mkdir(parents=True,exist_ok=True)
    payload={'schema':'v5b-next-weather-feature-rows-v1','features':features}
    content=json.dumps(payload,indent=2,sort_keys=True,allow_nan=False)+'\n';path=folder/'weather_features.json'
    if path.exists() and path.read_text()!=content:raise ValueError('Existing immutable features differ')
    path.write_text(content,encoding='utf-8')
    manifest['output']={'path':path.relative_to(root).as_posix(),'sha256':file_hash(path),'rows':len(features)}
    manifest['self_sha256']=content_hash(manifest,'self_sha256');target=folder/'weather_features_manifest.json'
    content=json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+'\n'
    if target.exists() and target.read_text()!=content:raise ValueError('Existing immutable manifest differs')
    target.write_text(content,encoding='utf-8');return manifest


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-root',default='.')
    result=write(parser.parse_args().project_root)
    print(json.dumps({k:result[k] for k in ('status','date_count','verified_source_rows','missing_optional_ceiling_rows','self_sha256')}))
