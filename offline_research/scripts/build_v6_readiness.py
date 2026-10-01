"""Read-only V6 cached-input readiness; never opens a protected label payload."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, timedelta, datetime
from hashlib import sha256
import json
import math
from pathlib import Path

UNIVERSE='data/manifests/v5a_outcome_blind_universe.json'
LABELS='data/development/v5a/development_labels.json'
FEATURES='data/development/v5b_next/weather_features.json'
MANIFEST='data/development/v5b_next/weather_features_manifest.json'
HOLDOUT='data/manifests/v5a_holdout_seal.json'
FREEZE='runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json'
OUTPUT='data/manifests/v6_readiness.json'

def digest(x):
    return sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()

def seal(x):
    x=dict(x);x.pop('self_sha256',None);x['self_sha256']=digest(x);return x

def filehash(p):
    h=sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def safe_path(root,name):
    p=(root/name).resolve()
    if not p.is_relative_to(root) or ('holdout' in name.lower() and name!=HOLDOUT) or 'protected' in name.lower():
        raise ValueError('Forbidden input path: '+name)
    return p

def build(root):
    root=Path(root).resolve();bindings={}
    def bind(name,expected=None):
        p=safe_path(root,name);actual=filehash(p)
        if expected is not None and actual!=expected:raise ValueError('Binding changed: '+name)
        bindings[name]=actual;return p
    def read(name,expected=None,sealed=True):
        x=json.loads(bind(name,expected).read_text(encoding='utf-8-sig'))
        if sealed and x.get('self_sha256')!=seal(x)['self_sha256']:raise ValueError('Invalid self hash: '+name)
        return x
    def ref(r):return bind(r['path'],r['sha256'])
    u=read(UNIVERSE);l=read(LABELS);m=read(MANIFEST);s=read(HOLDOUT);f=read(FREEZE)
    dates=[(date(2026,6,1)+timedelta(days=i)).isoformat() for i in range(64)]
    reserved=[(date(2026,8,4)+timedelta(days=i)).isoformat() for i in range(28)]
    if u['split']['development_dates']!=dates or u['split']['holdout_dates']!=reserved:raise ValueError('Universe date boundary changed')
    if s['holdout_dates']!=reserved or s['universe_self_sha256']!=u['self_sha256'] or s['holdout_dates_sha256']!=digest(reserved):raise ValueError('Holdout seal binding changed')
    if s['status']!='SEALED' or s['holdout_evaluations_consumed']!=0 or s['holdout_labels_opened'] is not False or s['protected_confirmation_labels_read'] is not False:raise ValueError('Holdout no longer reserved')
    for obj in (u,l,s):
        if obj.get('actual_orders_placed') is not False or obj.get('protected_confirmation_labels_read') is not False:raise ValueError('Unsafe input provenance')
    if l['holdout_labels_opened'] is not False or l['universe']['self_sha256']!=u['self_sha256']:raise ValueError('Label universe mismatch')
    ref(l['universe'])
    if [r['climate_date'] for r in l['labels']]!=dates:raise ValueError('Development label boundary changed')
    for r in l['labels']:
        t=datetime.fromisoformat(r['issued_at'].replace('Z','+00:00'))
        if t.tzinfo is None or t.date()<=date.fromisoformat(r['climate_date']) or not math.isfinite(r['reported_high_f']):raise ValueError('Invalid label provenance')
    if m['dates']!=dates or m['labels_read'] is not False or m['protected_labels_read'] is not False or m['network_used'] is not False:raise ValueError('Unsafe weather manifest')
    if m['output']['path']!=FEATURES:raise ValueError('Unexpected weather output')
    for name,expected in {**m['bindings'],**m['raw_source_bindings']}.items():
        if any(day in name for day in reserved):raise ValueError('Protected-period weather binding')
        bind(name,expected)
    weather=read(FEATURES,m['output']['sha256'],sealed=False)['features']
    if [r['climate_date'] for r in weather]!=dates:raise ValueError('Weather date boundary changed')
    required=['hrrr_temperature_f_max','gefs_mean_temperature_f_max','gefs_spread_delta_f_at_mean_max','hrrr_cloud_percent_mean','hrrr_wind_u_mean_m_s','hrrr_wind_v_mean_m_s','absolute_sampled_max_disagreement_f']
    for r in weather:
        if r['partition']!='development' or r['decision_time_utc']!='18:00' or any(not math.isfinite(r[k]) for k in required):raise ValueError('Invalid weather features')
        if r['gefs_spread_delta_f_at_mean_max']<=0:raise ValueError('Nonpositive uncertainty scale')
    if f['orders']!=0 or f['holdout_access_authorized'] is not False or f['ten_percent_confirmed'] is not False:raise ValueError('Unsafe prior freeze')
    prior=str(Path(FREEZE).parent).replace('\\','/')
    reg=read(prior+'/registration.json')
    candidate=read(prior+'/candidates/'+f['candidate_id']+'.json')
    if bindings[prior+'/registration.json']!=f['registration_sha256'] or bindings[prior+'/candidates/'+f['candidate_id']+'.json']!=f['candidate_sha256'] or candidate['parameters']!=f['parameters']:raise ValueError('Prior freeze binding changed')
    for key in ('acquisition_closure','event_rules','grade_b_fallback','paid_execution','registration'):
        ref(u['source_bindings'][key])
    paid=read(u['source_bindings']['paid_execution']['path'])
    fallback=read(u['source_bindings']['grade_b_fallback']['path'])
    ref(paid['source']) # Hash only; never deserialize mixed-period market archive.
    for item in fallback['source_manifests']:ref(item)
    records=[r for r in u['records'] if r['climate_date'] in dates]
    if len(records)!=768 or any(r['partition']!='development' for r in records):raise ValueError('Unexpected development contract universe')
    if len({(r['climate_date'],r['market_ticker'],r['contract_side']) for r in records})!=768:raise ValueError('Duplicate contract sides')
    grades=Counter(r['execution_evidence_grade'] for r in records)
    if set(grades)-{'A','B_PLUS','B','UNAVAILABLE'}:raise ValueError('Unknown evidence grade')
    score=dates[20:];score_records=[r for r in records if r['climate_date'] in score]
    return seal({'schema_version':'v6-readiness-v1','status':'READY_OFFLINE_DEVELOPMENT_ONLY','bindings':bindings,
      'development_dates':dates,'common_scoring_dates':score,'warmup_dates':dates[:20],
      'development_date_count':64,'common_scoring_date_count':44,'contract_side_count':len(records),'contract_count':len(records)//2,
      'execution_grade_counts':dict(sorted(grades.items())),'scoring_execution_grade_counts':dict(sorted(Counter(r['execution_evidence_grade'] for r in score_records).items())),
      'execution_available_date_count':len({r['climate_date'] for r in records if r['execution_evidence_grade']!='UNAVAILABLE'}),
      'feature_names':required,'runnable_capabilities':['prequential_climatology','hrrr_gefs_bias_and_equal_blend','cloud_wind_residual_ridge','spread_disagreement_conditional_uncertainty','fixed_complexity_weather_analogues','coherent_market_bracket_baseline','weather_market_residual_comparison','inherited_frozen_policy_control','18UTC_grade_stratified_execution_stress'],
      'blocked_capabilities':{'verified_partial_fills':'Native depth quantity units and queue priority unverified','continuous_depth_replay':'Only selected snapshots cached','alternate_time_depth':'No 13:30/15:00 paid books cached','individual_member_tails':'GEFS mean and spread only','forecast_revision_model':'One cycle per model','independent_confirmation':'V5A holdout reserved; exposed development is not confirmation','older_training_expansion':'540 exposed older days require separate schema harmonization and binding verification'},
      'holdout_status':'RESERVED_V5A_NOT_AUTHORIZED','holdout_dates':reserved,'holdout_labels_read':False,'development_labels_read':True,
      'allow_network':False,'allow_orders':False,'allow_protected_labels':False,'network_used':False,'orders':0,
      'model_fitting_performed':False,'ten_percent_confirmed':False,
      'limitations':['B and B_PLUS are not verified fills. A is snapshot evidence, not actual historical fills.','Conservative archived weather availability is not original publication proof.','Common 44 dates follow 20 warmup dates; abstention dates remain in scoring universe.','No protected payload or climate archive is opened or hashed.']})

def main():
    p=argparse.ArgumentParser();p.add_argument('--project-root',default='.');args=p.parse_args();root=Path(args.project_root)
    result=build(root);out=root/OUTPUT;out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['status','development_date_count','common_scoring_date_count','execution_grade_counts','self_sha256']}))
if __name__=='__main__':main()

