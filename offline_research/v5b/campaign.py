"""Bounded development-only research controller. No holdout entry point."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

CONFIG = 'configs/v5b_campaign.json'
POINTER = 'runs/v5b_current_campaign.json'

def now():
    return datetime.now(timezone.utc)

def stamp():
    return now().isoformat()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def filehash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.pending')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)

def seal(obj):
    obj = {k:v for k,v in obj.items() if k != 'self_sha256'}
    obj['self_sha256'] = digest(obj)
    return obj

def checked(path):
    obj = read(path)
    if obj != seal(obj):
        raise ValueError(f'Artifact integrity failed: {path}')
    return obj

@contextmanager
def lease(directory):
    """Kernel-released lock survives crashes without requiring stale PID guesses."""
    import msvcrt
    path = directory / 'campaign.lock'
    with path.open('a+b') as f:
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Campaign already has an active writer') from exc
        try:
            yield
        finally:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

@contextmanager
def offline():
    original_socket, original_connection = socket.socket, socket.create_connection
    def denied(*args, **kwargs):
        raise PermissionError('V5B replay forbids network access')
    socket.socket = denied
    socket.create_connection = denied
    try:
        yield
    finally:
        socket.socket, socket.create_connection = original_socket, original_connection

def locate(root):
    pointer = read(root / POINTER)
    path = (root / pointer['run_path']).resolve()
    if not path.is_relative_to((root/'runs/campaigns_v5b').resolve()):
        raise ValueError('Campaign path escaped run root')
    return path

def verify(root, directory):
    registration = checked(directory/'registration.json')
    for name, expected in registration['bindings'].items():
        p = (root/name).resolve()
        if not p.is_relative_to(root) or filehash(p) != expected:
            raise ValueError(f'Frozen binding differs: {name}')
    state = checked(directory/'recovery-state.json')
    if state['campaign_id'] != registration['campaign_id']:
        raise ValueError('Campaign identity differs')
    if state['deadline'] != registration['deadline']:
        raise ValueError('Deadline changed')
    return registration, state

def register(root):
    if (root/POINTER).exists():
        directory = locate(root)
        return verify(root, directory)[1]
    config = read(root/CONFIG)
    if any(config[k] for k in ('allow_network','allow_orders','allow_protected_labels')):
        raise ValueError('Unsafe configuration')
    identifier = 'v5b-development-' + now().strftime('%Y%m%dT%H%M%S%fZ')
    directory = root/'runs/campaigns_v5b'/identifier
    directory.mkdir(parents=True)
    files = [CONFIG, 'docs/V5B_RESEARCH_GOAL.md',
             'data/manifests/v5a_outcome_blind_universe.json',
             'data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json',
             'data/development/v5a/development_labels.json',
             'data/manifests/v5a_event_rules_outcome_blind.json']
    manifest = read(root/files[3])
    files.append(manifest['output']['path'])
    files += [p.relative_to(root).as_posix() for p in (root/'v5b').glob('*.py')]
    files += [p.relative_to(root).as_posix() for p in (root/'v5a').glob('*.py')]
    deadline = (now()+timedelta(seconds=config['wall_seconds'])).isoformat()
    registration = seal({'campaign_id':identifier,'registered_at':stamp(),'deadline':deadline,
        'config':config,'bindings':{name:filehash(root/name) for name in sorted(set(files))},
        'confirmation':'UNAVAILABLE_SEPARATE_UNTOUCHED_PERIOD_REQUIRED',
        'v5a_holdout_reserved':True,'protected_labels_accessed':False,'orders':0})
    write(directory/'registration.json',registration)
    state = seal({'campaign_id':identifier,'status':'REGISTERED','epoch':0,'unique_candidates':0,
        'deadline':deadline,'duplicates_skipped':0,'stagnant_epochs':0,'frontier_ids':[],
        'reviewed_epochs':[],'created_at':stamp(),'updated_at':stamp(),'protected_labels_accessed':False,
        'orders':0,'network_used':False})
    write(directory/'recovery-state.json',state)
    write(root/POINTER, {'campaign_id':identifier,'run_path':directory.relative_to(root).as_posix()})
    return state

def gate_failures(result, config):
    checks = {
      'sample':result['selected_days'] >= config['minimum_selected_days'],
      'realized_return':result['aggregate_realized_net_return'] >= config['minimum_realized_return'],
      'expected_return':result['mean_expected_net_return'] >= config['minimum_expected_return'],
      'positive_folds':result['positive_fold_count'] >= config['minimum_positive_folds'],
      'worst_fold':result['worst_nonempty_fold_return'] >= config['minimum_worst_fold_return'],
      'evidence':result['evidence_quality_score'] >= config['minimum_evidence_quality'],
      'adverse_fill':result['adverse_stress'][str(config['stress_cents'])]['aggregate_realized_net_return'] > 0,
      'best_day_removed':result['best_day_removed_return'] > 0,
      'calibration':result['multiclass_brier'] <= result['baseline_multiclass_brier'] + 1e-12,
    }
    return [key for key,passed in checks.items() if not passed]

def vector(r):
    return (r['aggregate_realized_net_return'],r['mean_expected_net_return'],
            r['worst_nonempty_fold_return'],r['positive_fold_count'],r['selected_days'],
            r['evidence_quality_score'],-r['multiclass_brier'],
            r['adverse_stress']['2']['aggregate_realized_net_return'])

def pareto(records):
    ids = sorted(records)
    values = {key:vector(records[key]['result']) for key in ids}
    return [key for key in ids if not any(
        other != key and all(a >= b for a,b in zip(values[other],values[key]))
        and any(a > b for a,b in zip(values[other],values[key])) for other in ids)]

def rank(item):
    r=item['result']
    return (-len(item['gate_failures']), int(r['selected_days']>=30),r['positive_fold_count'],
            r['worst_nonempty_fold_return'],r['aggregate_realized_net_return'],r['evidence_quality_score'])

def records_at(directory):
    return {p.stem:checked(p) for p in (directory/'candidates').glob('*.json')}

def review(root, packet_path):
    directory=locate(root)
    with lease(directory):
        _,state=verify(root,directory)
        packet=read(packet_path)
        if state['status'] != 'AWAITING_AGENT_REVIEW' or packet.get('epoch') != state['epoch']:
            raise ValueError('Review does not match pending epoch')
        reviewers=packet.get('reviews',[])
        if len({r.get('colony') for r in reviewers}) < 2:
            raise ValueError('At least two distinct actual colony reviews required')
        for r in reviewers:
            if not r.get('findings') or not r.get('reviewer') or not r.get('candidate_ids'):
                raise ValueError('Review requires author, candidate evidence and findings')
            if any(i not in records_at(directory) for i in r['candidate_ids']):
                raise ValueError('Review references unknown candidate')
        from v5b.hypotheses import validate_hypothesis, COLONIES
        existing=records_at(directory)
        for r in reviewers:
            if r['colony'] not in COLONIES:
                raise ValueError('Unknown reviewing colony')
            if any(existing[i]['hypothesis']['colony']==r['colony'] for i in r['candidate_ids']):
                raise ValueError('A colony cannot independently review its own candidate')
        for proposal in packet.get('proposals',[]):
            validate_hypothesis(proposal)
        write(directory/f'agent-review-{state["epoch"]:02}.json',seal(packet))
        state['reviewed_epochs'].append(state['epoch'])
        state['status']='READY'
        write(directory/'recovery-state.json',seal(state))
        return state

def epoch(root):
    from v5b.evaluation import load_development, evaluate_candidate, validate_spec
    from v5b.hypotheses import seeds, propose, synthesis, fingerprint
    directory=locate(root)
    with lease(directory), offline():
        registration,state=verify(root,directory)
        config=registration['config']
        if state['status'] in ('COMPLETE','FROZEN','STOPPED','AWAITING_AGENT_REVIEW'):
            return state
        if now()>=datetime.fromisoformat(state['deadline']):
            state['status']='COMPLETE';state['stop_reason']='WALL_BUDGET'
            write(directory/'recovery-state.json',seal(state))
            report(root,directory,records_at(directory),state)
            return state
        records=records_at(directory)
        populations={}
        for item in sorted(records.values(),key=rank,reverse=True):
            h=item['hypothesis']; c=h['colony']
            if len(populations.setdefault(c,[]))<config['population_size']:
                populations[c].append(h)
        next_epoch=state['epoch']+1
        if next_epoch==1:
            proposals=seeds()
        else:
            packet=checked(directory/f'agent-review-{state["epoch"]:02}.json')
            proposals=packet.get('proposals',[])+propose(populations,critiques=packet['reviews'],
                epoch=next_epoch-2,max_per_colony=config['max_proposals_per_colony'])
            leaders=[items[0] for items in populations.values() if items]
            for left,right in zip(leaders,leaders[1:]):
                combined=synthesis(left,right,epoch=next_epoch)
                if combined: proposals.append(combined)
        context=load_development(root)
        old_front=set(pareto(records)) if records else set()
        evaluated=[key for key,item in records.items() if item['epoch']==next_epoch];critiques=[]
        colony_names=list(dict.fromkeys(h['colony'] for h in seeds()))
        state['status']='RUNNING';state['process_id']=os.getpid()
        write(directory/'recovery-state.json',seal(state))
        for h in proposals:
            if len(records)>=config['max_unique_candidates'] or now()>=datetime.fromisoformat(state['deadline']):
                break
            if (directory/'stop-request.json').exists():
                state['status']='STOPPED';break
            params=validate_spec(h['parameters'])
            key=fingerprint(params)
            if key in records:
                state['duplicates_skipped']+=1;continue
            result=evaluate_candidate(context,params)
            failures=gate_failures(result,config)
            item=seal({'candidate_id':key,'epoch':next_epoch,'hypothesis':h,'parameters':params,
                'result':result,'gate_failures':failures,'development_screen_passed':not failures,
                'confirmation_passed':False})
            write(directory/'candidates'/f'{key}.json',item)
            records[key]=item;evaluated.append(key)
            reviewer=colony_names[(colony_names.index(h['colony'])+1)%len(colony_names)]
            critiques.append({'candidate_id':key,'proposing_colony':h['colony'],'reviewing_colony':reviewer,
                'review_type':'deterministic_falsification_not_agent_opinion','failed_tests':failures,
                'stress_2_cent_return':result['adverse_stress']['2']['aggregate_realized_net_return'],
                'best_day_removed_return':result['best_day_removed_return'],
                'grade_a_sensitivity':result['grade_a_sensitivity']})
        front=pareto(records)
        newcomers=set(front)-old_front
        state.update(epoch=next_epoch,unique_candidates=len(records),frontier_ids=front,
            stagnant_epochs=0 if newcomers else state['stagnant_epochs']+1,updated_at=stamp())
        if state['status']!='STOPPED':
            state['status']='COMPLETE' if next_epoch>=config['max_epochs'] or len(records)>=config['max_unique_candidates'] or now()>=datetime.fromisoformat(state['deadline']) else 'AWAITING_AGENT_REVIEW'
        write(directory/f'epoch-{next_epoch:02}.json',seal({'epoch':next_epoch,'evaluated':evaluated,
             'new_frontier_ids':sorted(newcomers),'duplicates_skipped_cumulative':state['duplicates_skipped'],
             'requires_new_causal_research':state['stagnant_epochs']>=config['stagnant_epochs_before_agent_review']}))
        write(directory/f'criticism-{next_epoch:02}.json',seal({'checks':critiques}))
        write(directory/'pareto-archive.json',seal({'candidate_ids':front,'unique_candidates':len(records)}))
        new_pop={}
        for item in sorted(records.values(),key=rank,reverse=True):
            c=item['hypothesis']['colony']
            if len(new_pop.setdefault(c,[]))<config['population_size']:new_pop[c].append(item['candidate_id'])
        write(directory/'colony-populations.json',seal({'populations':new_pop}))
        verify(root,directory)
        write(directory/'recovery-state.json',seal(state))
        report(root,directory,records,state)
        return state

def report(root,directory,records,state):
    leader=max(records.values(),key=rank) if records else None
    write(directory/'development-summary.json',seal({'campaign_id':state['campaign_id'],'status':state['status'],
        'unique_candidates':len(records),'epoch':state['epoch'],'leader':leader,
        'scientific_conclusion':'DEVELOPMENT_ONLY_CONFIRMATION_UNAVAILABLE',
        'ten_percent_confirmed':False,'protected_labels_accessed':False,'orders':0}))
    if leader:
        r=leader['result']
        text=f'''# V5B development report\n\nCampaign: {state['campaign_id']}\n\n{len(records)} unique candidates; {state['epoch']} completed epochs; status {state['status']}.\n\nLeader {leader['candidate_id']}: {r['selected_days']} selected days, {r['aggregate_realized_net_return']:.2%} aggregate simulated net return, {r['mean_expected_net_return']:.2%} estimated expected return, {r['positive_fold_count']}/5 positive folds, worst fold {r['worst_nonempty_fold_return']:.2%}.\n\nExecution composition: {r['execution_grade_counts']}. Failed development gates: {leader['gate_failures']}.\n\nThese are adaptively selected historical development simulations, not realized account gains. B/B+ are not verified fills. The separate untouched confirmation period is unavailable; the 10% target is NOT confirmed. V5A's holdout remains reserved.\n\nCandidate JSON artifacts contain complete trade/abstention ledgers, stress, calibration and evidence diagnostics. Actual agent reviews are separate from deterministic checks. Network replay is denied; no orders authorized.\n'''
        (directory/'development-report.md').write_text(text,encoding='utf-8')

def freeze(root):
    directory=locate(root)
    with lease(directory):
        reg,state=verify(root,directory)
        if state['status'] not in ('COMPLETE','FROZEN'):
            raise ValueError('Development must finish before freezing')
        target=directory/'strategy-freeze.json'
        if target.exists():return checked(target)
        records=records_at(directory)
        if not records:raise ValueError('No evaluated candidate')
        leader=max(records.values(),key=rank)
        artifact=seal({'campaign_id':state['campaign_id'],'candidate_id':leader['candidate_id'],
            'parameters':leader['parameters'],'candidate_sha256':filehash(directory/'candidates'/f'{leader["candidate_id"]}.json'),
            'registration_sha256':filehash(directory/'registration.json'),'frozen_at':stamp(),
            'confirmation_status':'BLOCKED_NO_SEPARATE_UNTOUCHED_PERIOD',
            'holdout_access_authorized':False,'ten_percent_confirmed':False,'orders':0})
        write(target,artifact)
        state['status']='FROZEN';write(directory/'recovery-state.json',seal(state))
        report(root,directory,records,state)
        return artifact

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['register','epoch','status','review','stop','resume','freeze'])
    parser.add_argument('--project-root',default='.')
    parser.add_argument('--packet')
    args=parser.parse_args();root=Path(args.project_root).resolve()
    if args.action=='register':value=register(root)
    elif args.action=='epoch':value=epoch(root)
    elif args.action=='status':value=verify(root,locate(root))[1]
    elif args.action=='review':value=review(root,Path(args.packet))
    elif args.action=='freeze':value=freeze(root)
    elif args.action=='stop':
        directory=locate(root);write(directory/'stop-request.json',{'requested_at':stamp()});value={'status':'STOP_REQUESTED'}
    else:
        directory=locate(root)
        with lease(directory):
            _,state=verify(root,directory)
            (directory/'stop-request.json').unlink(missing_ok=True)
            if state['status']=='STOPPED':
                state['status']='AWAITING_AGENT_REVIEW' if state['epoch'] else 'READY'
                write(directory/'recovery-state.json',seal(state))
        value=epoch(root)
    print(json.dumps(value,indent=2,sort_keys=True,allow_nan=False))

if __name__=='__main__':main()
