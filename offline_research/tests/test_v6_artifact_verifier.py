"""Synthetic sealed artifact fixtures; no historical or protected input reads."""
from datetime import datetime, timezone, timedelta, date
import json
import pytest
from scripts.verify_v6_artifacts import verify, digest, VerificationError, ranking, audit_trade_math, filehash, audit_exhaustion, allocation_evidence, audit_quotas


def save(path,value):
    value={k:v for k,v in value.items() if k!='self_sha256'}
    value['self_sha256']=digest(value)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value))
    return value


@pytest.fixture
def fixture(tmp_path):
    start=datetime(2030,1,1,tzinfo=timezone.utc)
    config={'wall_seconds':14400,'minimum_elapsed_before_scientific_finish_seconds':10800,
      'max_unique_candidates':36,'max_epochs':4,'allow_network':False,'allow_orders':False,'allow_protected_labels':False,
      'groups':['conditional_uncertainty','incremental_market_information','decision_execution','null_adversarial'],
      'minimum_selected_days':30,'minimum_realized_return':.1,'minimum_expected_return':.1,
      'minimum_positive_folds':4,'minimum_worst_fold_return':-.1,'minimum_evidence_quality':.65,'stress_cents':2}
    directory=tmp_path/'runs/campaigns_v6/test'
    holdout='data/manifests/v5a_holdout_seal.json'
    save(tmp_path/holdout,{'synthetic_seal':True})
    readiness='data/manifests/v6_readiness.json'
    save(tmp_path/readiness,{'status':'READY_OFFLINE_DEVELOPMENT_ONLY','development_date_count':64,
      'common_scoring_date_count':44,'allow_network':False,'allow_orders':False,'allow_protected_labels':False,
      'bindings':{holdout:filehash(tmp_path/holdout)}})
    params={'method_id':'synthetic','policy_id':'fixed','stage':'economic'}; key=digest(params)
    reg=save(directory/'registration.json',{'campaign_id':'test','registered_at':start.isoformat(),
      'not_before':(start+timedelta(hours=3)).isoformat(),'deadline':(start+timedelta(hours=4)).isoformat(),
      'config':config,'bindings':{name:filehash(tmp_path/name) for name in (readiness,holdout)},'catalog_sha256':digest([key]),'catalog_fingerprints':[key],'orders':0,'protected_labels_accessed':False})
    reference=save(directory/'reference-controls.json',{'campaign_id':'test','registration_sha256':reg['self_sha256'],
      'counts_as_new_candidate':False,'confirmation_evidence':False,'control':{'counts_as_new_candidate':False}})
    params={'method_id':'synthetic','policy_id':'fixed','stage':'economic'}; key=digest(params)
    dates=[(date(2026,6,21)+timedelta(days=i)).isoformat() for i in range(44)]
    ledger=[{'climate_date':d,'abstention':True} for d in dates];behavior=digest(ledger)
    result={'common_scoring_dates':dates,'behavioral_ledger':ledger,'behavioral_fingerprint':behavior,
      'trades':[],'total_entry_outlay_dollars':0.,'total_net_profit_dollars':0.,
      'selected_days':0,'aggregate_realized_net_return':-1.,'mean_expected_net_return':-1.,'positive_fold_count':0,
      'worst_nonempty_fold_return':-1.,'evidence_quality_score':0.,'adverse_stress':{'2':{'aggregate_realized_net_return':-1.}},
      'best_day_removed_return':-1.,'multiclass_brier':.5,'baseline_multiclass_brier':.6,'orders':0}
    failed=['sample','realized_return','expected_return','positive_folds','worst_fold','evidence','adverse_fill','best_day_removed']
    attempt=save(directory/'worker-attempts/synthetic-attempt.json',{'attempt_id':'synthetic-attempt','candidate_id':key,
      'deadline':reg['deadline'],'private_result_path':'worker-results/synthetic-attempt.json',
      'worker_watchdog':'parent_liveness_and_absolute_deadline'})
    item=save(directory/f'candidates/{key}.json',{'candidate_id':key,'parameters':params,
      'worker_attempt_id':attempt['attempt_id'],'worker_attempt_sha256':attempt['self_sha256'],
      'evaluated_started_at':start.isoformat(),'evaluated_completed_at':start.isoformat(),
      'hypothesis':{'parameters':params,'fingerprint':key,'colony':'conditional_uncertainty'},'result':result,
      'behavioral_fingerprint':behavior,'gate_failures':failed,'development_screen_passed':False})
    state=save(directory/'recovery-state.json',{'campaign_id':'test','registration_sha256':reg['self_sha256'],
      'deadline':reg['deadline'],'status':'AWAITING_AGENT_REVIEW','updated_at':start.isoformat(),
      'unique_candidates':1,'candidate_hashes':{key:item['self_sha256']},'reference_controls_sha256':reference['self_sha256'],'orders':0,'protected_labels_accessed':False})
    save(tmp_path/'runs/v6_current_campaign.json',{'run_path':'runs/campaigns_v6/test'})
    return tmp_path,directory,reg,state,key,item


def test_valid_metadata_fixture(fixture):
    root,*_=fixture
    result=verify(root)
    assert result['verified'] and result['reproduced_candidates']==0
    assert not result['confirmation_verified']


def test_sealed_tamper_rejected(fixture):
    root,directory,reg,state,key,item=fixture
    p=directory/f'candidates/{key}.json';item['result']['orders']=1;p.write_text(json.dumps(item))
    with pytest.raises(VerificationError,match='sealed hash'):verify(root)


def test_rehashed_unsafe_flag_rejected(fixture):
    root,directory,reg,state,key,item=fixture
    item['result']['protected_labels_accessed']=True
    save(directory/f'candidates/{key}.json',item)
    with pytest.raises(VerificationError,match='unsafe'):verify(root)


def test_behavioral_chronology_rejected(fixture):
    root,directory,reg,state,key,item=fixture
    item['result']['behavioral_ledger'].reverse()
    save(directory/f'candidates/{key}.json',item)
    with pytest.raises(VerificationError,match='chronology'):verify(root)


def test_promotion_cannot_ignore_gates(fixture):
    root,directory,reg,state,key,item=fixture
    item['development_screen_passed']=True
    save(directory/f'candidates/{key}.json',item)
    with pytest.raises(VerificationError,match='promotion'):verify(root)


def test_early_completion_requires_certificate(fixture):
    root,directory,reg,state,key,item=fixture
    packet=save(directory/'terminal-review.json',{'epoch':1,'terminal':True,'proposals':[],
      'reviews':[{'reviewer':g,'colony':g,'candidate_ids':[key],'findings':['synthetic']} for g in ['decision_execution','null_adversarial']],
      'candidate_hashes':state['candidate_hashes'],'reviewed_at':reg['registered_at']})
    state.update(status='COMPLETE',terminal_review_sha256=packet['self_sha256'],completed_at=reg['registered_at'])
    save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='premature'):verify(root)


def test_bound_path_escape_rejected(fixture):
    root,directory,reg,state,key,item=fixture
    reg['bindings']={'../outside':'ignored'};reg=save(directory/'registration.json',reg)
    state['registration_sha256']=reg['self_sha256'];save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='escaped'):verify(root)


def test_ranking_collapses_behavior_and_stable_ties(fixture):
    *_,key,item=fixture
    other=json.loads(json.dumps(item));other['candidate_id']='0'*64
    ordered=ranking({key:item,other['candidate_id']:other})
    assert len(ordered)==1 and ordered[0]['candidate_id']=='0'*64


def test_independent_fee_and_settlement_reconstruction():
    trade={'climate_date':'2026-06-21','entry_price_cents':20,'fee_dollars':.02,
      'reported_high_f':65,'strike_type':'less','cap_strike':70,'floor_strike':None,
      'contract_side':'YES','won':True,'entry_outlay_dollars':.22,'net_profit_dollars':.78}
    result={'trades':[trade],'selected_days':1,'total_entry_outlay_dollars':.22,
      'total_net_profit_dollars':.78,'aggregate_realized_net_return':.78/.22}
    assert audit_trade_math(result)==1
    trade['fee_dollars']=.01
    with pytest.raises(VerificationError,match='fee'):audit_trade_math(result)


def test_finite_catalog_certificate_allows_early_scientific_finish(fixture):
    root,directory,reg,state,key,item=fixture
    packet=save(directory/'terminal-review.json',{'epoch':1,'terminal':True,'proposals':[],
      'reviews':[{'reviewer':g,'colony':g,'candidate_ids':[key],'findings':['synthetic']} for g in ['decision_execution','null_adversarial']],
      'candidate_hashes':state['candidate_hashes'],'reviewed_at':reg['registered_at']})
    cert=save(directory/'exhaustion-certificate.json',{'campaign_id':'test','registration_sha256':reg['self_sha256'],
      'catalog_sha256':reg['catalog_sha256'],'complete':True,'missing':[],'pending':[],
      'terminal_review_sha256':packet['self_sha256'],
      'reachable_fingerprints':[key],'evaluated_fingerprints':[key],'duplicate_map':{},'blocked':{}})
    state.update(status='COMPLETE',terminal_review_sha256=packet['self_sha256'],exhaustion_certificate_sha256=cert['self_sha256'],completed_at=reg['registered_at'])
    save(directory/'recovery-state.json',state)
    assert verify(root)['verified']
    cert['reachable_fingerprints']=[];cert=save(directory/'exhaustion-certificate.json',cert)
    state['exhaustion_certificate_sha256']=cert['self_sha256'];save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='catalog'):verify(root)


@pytest.mark.parametrize('bad',['missing','late','reversed'])
def test_candidate_timestamps_mandatory_and_bounded(fixture,bad):
    root,directory,reg,state,key,item=fixture
    if bad=='missing':del item['evaluated_started_at']
    elif bad=='late':item['evaluated_completed_at']=(datetime.fromisoformat(reg['deadline'])+timedelta(seconds=1)).isoformat()
    else:item['evaluated_started_at']=(datetime.fromisoformat(reg['registered_at'])+timedelta(seconds=1)).isoformat()
    item=save(directory/f'candidates/{key}.json',item)
    state['candidate_hashes'][key]=item['self_sha256']
    save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='timestamp|budget'):verify(root)


def test_incomplete_exhaustion_draft_is_not_terminal_certificate(fixture):
    root,directory,reg,state,key,item=fixture
    draft=save(directory/'exhaustion-certificate-draft.json',{'registration_sha256':reg['self_sha256'],
      'catalog_sha256':reg['catalog_sha256'],'reachable_fingerprints':[key],'evaluated_fingerprints':[key],
      'blocked':{},'duplicate_map':{},'missing':[],'pending':[],'complete':False,'terminal_review_sha256':None})
    state['exhaustion_draft_sha256']=draft['self_sha256'];save(directory/'recovery-state.json',state)
    assert verify(root)['verified']
    state['exhaustion_certificate_sha256']=draft['self_sha256'];save(directory/'exhaustion-certificate.json',draft)
    save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='incomplete exhaustion'):verify(root)


def test_objective_blocked_descendants_require_failed_precursor():
    p={'method_id':'gefs_spread_equal_blend','policy_id':'no_dollar_all','stage':'precursor'}
    key=digest(p);child=digest({**p,'stage':'economic'})
    r={'precursor':{'method_forecast_count':44,'policy_chain_multiclass_brier':.8,'reference_multiclass_brier':.7},
       'calibration':{'minimum_residual_count':10}}
    records={key:{'parameters':p,'result':r,'precursor_passed':False,'self_sha256':'evidence'}}
    reg={'self_sha256':'registration','catalog_sha256':'catalog','catalog_fingerprints':sorted([key,child]),
      'config':{'precursor_gate':{'method_forecast_count':44,'minimum_residual_count':10},'brier_tolerance':1e-12}}
    cert={'registration_sha256':'registration','catalog_sha256':'catalog','reachable_fingerprints':reg['catalog_fingerprints'],
      'evaluated_fingerprints':[key],'blocked':{child:{'prerequisite_id':'precursor_passed','precursor_candidate_id':key,
        'evidence_sha256':'evidence','reason':'failed'}},'duplicate_map':{},'missing':[],'pending':[],
      'complete':True,'terminal_review_sha256':'review'}
    audit_exhaustion(cert,reg,records,True)
    r['precursor']['policy_chain_multiclass_brier']=.6
    with pytest.raises(VerificationError,match='actually passed'):audit_exhaustion(cert,reg,records,True)


def test_reference_cannot_be_new_candidate(fixture):
    root,directory,reg,state,*_=fixture
    p=directory/'reference-controls.json';reference=json.loads(p.read_text())
    reference['control']['counts_as_new_candidate']=True;reference=save(p,reference)
    state['reference_controls_sha256']=reference['self_sha256'];save(directory/'recovery-state.json',state)
    with pytest.raises(VerificationError,match='new evidence'):verify(root)


def test_allocation_priority_uses_independent_reviews(fixture):
    *_,key,item=fixture
    item['precursor_passed']=False
    groups=['conditional_uncertainty','null_adversarial']
    evidence,priority=allocation_evidence({key:item},[],groups)
    assert priority[0]=='null_adversarial'
    reviews=[{'colony':g,'candidate_ids':[key]} for g in ('decision_execution','null_adversarial')]
    evidence,priority=allocation_evidence({key:item},reviews,groups)
    assert priority[0]=='conditional_uncertainty'
    assert evidence['conditional_uncertainty']['cross_reviewed_falsification']


@pytest.mark.parametrize('mutation',['deadline','watchdog','private','missing'])
def test_worker_attempt_integrity(fixture,mutation):
    root,directory,reg,state,key,item=fixture
    path=directory/'worker-attempts/synthetic-attempt.json';attempt=json.loads(path.read_text())
    if mutation=='missing':path.unlink()
    else:
        if mutation=='deadline':attempt['deadline']=reg['registered_at']
        if mutation=='watchdog':attempt['worker_watchdog']='none'
        if mutation=='private':attempt['private_result_path']='worker-results/shared.json'
        save(path,attempt)
    with pytest.raises(VerificationError,match='worker'):verify(root)


def test_capacity_limited_quotas_require_exact_relaxation():
    config={'groups':['conditional_uncertainty','null_adversarial'],'round_allocation':{
      'minimum_null_verification_slots':3,'minimum_other_group_slots':1,'maximum_any_group_slots':4}}
    a={'eligible_capacity_by_group':{'conditional_uncertainty':2,'null_adversarial':1},
       'eligible_fingerprints':['a','b','c'],'selected_by_group':{'conditional_uncertainty':2,'null_adversarial':1},
       'quota':config['round_allocation'],'quota_relaxations':[{'group':'null_adversarial',
       'reason':'eligible_catalog_capacity_below_minimum','eligible_capacity':1}]}
    audit_quotas(a,config)
    a['quota_relaxations']=[]
    with pytest.raises(VerificationError,match='relaxation'):audit_quotas(a,config)
