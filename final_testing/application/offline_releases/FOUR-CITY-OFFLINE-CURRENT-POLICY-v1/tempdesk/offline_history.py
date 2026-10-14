"""Durable, button-triggered offline histories, isolated from online wallets."""
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import hashlib, json, os, subprocess, sys, threading, time, uuid
from .offline_sources import atomic_json
from .bridge import Release
from .parity_policy import POLICY_NAMESPACE, LEGACY_NAMESPACE, mark_result

START='2026-10-01'
CITIES=('la','ny','miami','phoenix')

def days_between(start,end):
    a=date.fromisoformat(start);b=date.fromisoformat(end)
    return [(a+timedelta(days=i)).isoformat() for i in range(max(0,(b-a).days+1))]

class History:
    def __init__(self,app_root,release_path,runtime_root,runner=None):
        self.app_root=Path(app_root).resolve();self.release=Release(release_path)
        self.runtime=Path(runtime_root).resolve()
        if runner is None:
            dashboard=self.release.root.parent.parent
            if self.release.root.parent.name!='releases' or self.runtime!=dashboard/'runtime/offline-history':
                raise ValueError('Offline runtime must be the registered Dashboard/runtime/offline-history')
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.runner=runner or self._run_city
        self.app_manifest_sha256=None
        if runner is None:
            self.app_manifest_sha256=hashlib.sha256((self.app_root/'offline-manifest.json').read_bytes()).hexdigest()
            self._verify_app()
        # Earlier results stay byte-for-byte at their original path. A changed
        # exit policy starts its own wallet history rather than borrow old cash.
        self.legacy_state_path=self.runtime/'history.json'
        self.policy_root=self.runtime/'policy-histories'/POLICY_NAMESPACE
        self.policy_root.mkdir(parents=True,exist_ok=True)
        self.state_path=self.policy_root/'history.json';self.jobs=self.runtime/'jobs';self.jobs.mkdir(exist_ok=True)
        self.auth_status='DISCONNECTED'
        self.state=json.loads(self.state_path.read_bytes()) if self.state_path.is_file() else {
            'start_day':START,'end_day':START,'mode':'CONDITIONAL_ARCHIVE_REPLAY','profiles':[], 'city_results':{},
            'job':{'status':'IDLE'},'updated_ns':None,'initial_wallet_cash':100,'initial_total_cash':900,
            'policy_namespace':POLICY_NAMESPACE}
        if self.state.get('policy_namespace')!=POLICY_NAMESPACE:
            raise ValueError('Offline history policy identity mismatch; old results cannot become current-policy results')
        if self.state.get('job',{}).get('status') in ('RUNNING','QUEUED'):
            self.state['job']['status']='INTERRUPTED';self.state['job']['message']='The server stopped before completion. Click again to resume missing work.'
            self._save()

    def _save(self): atomic_json(self.state_path,self.state)

    def legacy_snapshot(self):
        """Read-only export of older policy evidence; never merge its money."""
        with self.lock:
            if not self.legacy_state_path.is_file():
                raise FileNotFoundError('No earlier policy history is saved')
            raw=self.legacy_state_path.read_bytes()
        previous=json.loads(raw)
        previous['policy_namespace']=LEGACY_NAMESPACE
        previous['policy_label']='Earlier native archive exits; includes F3 forecast-based selling. Separate from current live protection policy.'
        previous['original_history_sha256']=hashlib.sha256(raw).hexdigest()
        previous['read_only_export']=True
        for profile in previous.get('profiles',[]):
            profile['policy_namespace']=LEGACY_NAMESPACE
        return previous

    def _verify_app(self):
        if not self.app_manifest_sha256:return
        raw=(self.app_root/'offline-manifest.json').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.app_manifest_sha256:raise ValueError('Offline manifest changed')
        manifest=json.loads(raw)
        for item in manifest['files']:
            path=(self.app_root/item['path']).resolve()
            if not path.is_relative_to(self.app_root) or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('Offline code changed: '+item['path'])

    def _scope_profile(self,metadata,native,end_day,stale=False):
        shown={**metadata,**native};expected=days_between(START,end_day)
        rows={r['day']:r for r in native.get('days',[])}
        incomplete=any(d not in rows for d in expected)
        if stale or incomplete:
            shown['latest_attempt_failed']=bool(stale)
            shown['known_cash_asof_day']=native.get('days',[{}])[-1].get('day') if native.get('days') else None
            shown['ending_cash']=None
            shown['net_pnl']=None
            reason='LATEST_CITY_REPLAY_FAILED; prior history is retained' if stale else 'AWAITING_ANALYSIS_FOR_THIS_DAY'
            shown['days']=[rows.get(d,{'day':d,'status':'PENDING','reason':reason,'known_cash':None,
                'ending_cash':None,'net_pnl':None,'wins':None,'losses':None,'contracts':None,'fees':None,'reserved':None}) for d in expected]
        return shown

    def snapshot(self):
        with self.lock:
            result=json.loads(json.dumps(self.state,allow_nan=False))
        result['archive_connection']=self.auth_status
        result['today']=datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
        result['days']=days_between(START,result.get('end_day',START))
        result['freeze_release']=self.release.manifest['release_id']
        result['offline_release']=json.loads((self.app_root/'offline-manifest.json').read_bytes())['release_id'] if self.app_manifest_sha256 else 'TEST_ONLY'
        result['readiness']='HISTORICAL_CONDITIONAL_ONLY'
        result['policy_namespace']=POLICY_NAMESPACE
        result['previous_policy_history']={'policy_namespace':LEGACY_NAMESPACE,
            'preserved':self.legacy_state_path.is_file(), 'available':self.legacy_state_path.is_file(),
            'sha256':hashlib.sha256(self.legacy_state_path.read_bytes()).hexdigest() if self.legacy_state_path.is_file() else None,
            'description':'Earlier archive rules, including F3 forecast-based selling. Excluded from current-policy balances.'}
        jid=result.get('job',{}).get('id')
        if jid:
            result['job']['progress']={}
            for city in CITIES:
                path=self.jobs/jid/(city+'-worker.progress.json')
                if path.is_file():
                    try:result['job']['progress'][city]=json.loads(path.read_bytes())
                    except (ValueError,OSError):pass
        return result

    def start(self,end_day=None):
        end_day=end_day or datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
        if date.fromisoformat(end_day)<date.fromisoformat(START) or end_day>datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat():
            raise ValueError('Invalid history end date')
        with self.lock:
            if self.state.get('job',{}).get('status') in ('RUNNING','QUEUED'): return self.snapshot()
            self.release.verify()
            self._verify_app()
            job_id=datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
            job={'id':job_id,'status':'QUEUED','started_ns':time.time_ns(),'end_day':end_day,'cities':{c:'QUEUED' for c in CITIES},
                 'message':'Checking saved sources and downloading missing evidence.'}
            self.state['end_day']=end_day;self.state['job']=job
            self.state['profiles']=[self._scope_profile(p,p,end_day) for p in self.state.get('profiles',[])]
            self._save()
            threading.Thread(target=self._job,args=(job_id,end_day),daemon=True).start()
            return self.snapshot()

    def _run_city(self,city,job_id,end_day):
        self._verify_app()
        with self.lock:
            self.state['job']['cities'][city]='RUNNING';self._save()
        env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONIOENCODING']='utf-8'
        env['TEMP_DESK_SOURCE_MAP']=str(self.release.root/'source-map.json')
        env['TEMP_DESK_RUNTIME_ROOT']=str(self.runtime)
        env['OFFLINE_LA_REVIEW_PATH']=str(self.app_root/'evidence/la-offline-independent-review-v1.json')
        env['OFFLINE_LA_FEE_REVIEW_PATH']=str(self.app_root/'evidence/la-fee-temporal-producer-review-v1.json')
        env['OFFLINE_RELEASE_MANIFEST_SHA256']=hashlib.sha256((self.app_root/'offline-manifest.json').read_bytes()).hexdigest()
        env['PYTHONPATH']=str(self.app_root)+os.pathsep+self.release.manifest.get('library_path','')
        env.update(self.release.manifest.get('runtime_environment',{}))
        request={'city':city,'start_day':START,'end_day':end_day,'release_path':str(self.release.root),
                 'runtime_root':str(self.runtime),'job_id':job_id}
        process=subprocess.run([sys.executable,'-B','-m','tempdesk.offline_worker'],input=json.dumps(request),
                               capture_output=True,text=True,encoding='utf8',env=env,cwd=self.app_root,timeout=1800,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        capture=self.jobs/job_id/(city+'-worker-response.json');capture.parent.mkdir(parents=True,exist_ok=True)
        capture.write_text(process.stdout,encoding='utf8')
        try: value=json.loads(process.stdout)
        except Exception: raise RuntimeError('Malformed replay response for '+city) from None
        if process.returncode: raise RuntimeError(value.get('error_type','Replay failed'))
        return value

    def _validate(self,city,result,end_day):
        allowed={p['profile_id'] for p in self.release.manifest['profiles'] if p['city']==city}
        if result.get('orders_sent',0) or result.get('actual_orders',0) or result.get('new_fits',0):
            raise ValueError('Non-frozen paper history response rejected')
        if result.get('city')!=city or len(result.get('profiles',[]))!=len(allowed) or {p['profile_id'] for p in result.get('profiles',[])}!=allowed:
            raise ValueError('Replay identities differ from saved models')
        expected=days_between(START,end_day)
        for profile in result['profiles']:
            if profile['wallet_id']!=city+':'+profile['profile_id']: raise ValueError('Wallet identity mismatch')
            if [row['day'] for row in profile['days']]!=expected: raise ValueError('Missing calendar rows')
            if any(r['status']!='COMPLETE' for r in profile['days']) and (profile.get('ending_cash') is not None or profile.get('net_pnl') is not None):
                raise ValueError('Incomplete history cannot claim a complete profile balance')
            for key in ('ending_cash','known_cash','net_pnl','fees','reserved'):
                if profile.get(key) is not None and not Decimal(str(profile[key])).is_finite():raise ValueError('Nonfinite profile '+key)
            for row in profile['days']:
                if row['status'] not in ('COMPLETE','PENDING','UNKNOWN'): raise ValueError('Unsupported day status')
                if row['status']!='COMPLETE' and row.get('ending_cash') is not None:
                    raise ValueError('Incomplete historical day cannot claim a complete ending balance')
                for key in ('fees','contracts','wins','losses','reserved'):
                    if row.get(key) is not None:
                        value=Decimal(str(row[key]))
                        if not value.is_finite() or value<0:raise ValueError('Invalid '+key)
                for key in ('ending_cash','known_cash','net_pnl'):
                    if row.get(key) is not None and not Decimal(str(row[key])).is_finite():raise ValueError('Nonfinite '+key)
        # Every newly admitted run retains actual source hashes. Validate the
        # exact files once here, without repeating the native economic suite.
        seen=set()
        for pin in result.get('source_pins',[]):
            expected_hash=pin.get('sha256')
            if not expected_hash and pin.get('actual_sha256') and pin.get('declared_sha256')==pin['actual_sha256']:
                expected_hash=pin['actual_sha256']
            if not pin.get('path') or not expected_hash: raise ValueError('Malformed archive source pin')
            key=(pin['path'],expected_hash)
            if key in seen: continue
            seen.add(key);digest=hashlib.sha256()
            with Path(pin['path']).open('rb') as source:
                for block in iter(lambda:source.read(1_048_576),b''):digest.update(block)
            if digest.hexdigest()!=expected_hash:raise ValueError('Archive source changed after scoring')
        json.dumps(result,allow_nan=False)

    def _job(self,job_id,end_day):
        with self.lock: self.state['job']['status']='RUNNING';self._save()
        failures=[]
        # Three numerical children maximum; each city has independent namespaces.
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures={pool.submit(self.runner,c,job_id,end_day):c for c in CITIES}
            for f in as_completed(futures):
                city=futures[f]
                try:
                    result=mark_result(f.result());self._validate(city,result,end_day)
                    atomic_json(self.jobs/job_id/(city+'-result.json'),result)
                    with self.lock:
                        self.state['city_results'][city]=result
                        self.state['job']['cities'][city]='SAVED'
                except Exception as e:
                    failures.append({'city':city,'error_type':type(e).__name__,'reason':str(e)[:800]})
                    if 'result' in locals() and result.get('city')==city:
                        atomic_json(self.jobs/job_id/(city+'-unaccepted-result.json'),result)
                    with self.lock: self.state['job']['cities'][city]='FAILED'
                with self.lock:
                    profiles=[]
                    for c in CITIES:
                        by={p['profile_id']:p for p in self.state['city_results'].get(c,{}).get('profiles',[])}
                        for metadata in self.release.manifest['profiles']:
                            if metadata['city']==c:
                                if metadata['profile_id'] in by:
                                    native=by[metadata['profile_id']]
                                    shown=self._scope_profile(metadata,native,end_day,self.state['job']['cities'][c]=='FAILED')
                                    profiles.append(shown)
                                else: profiles.append({**metadata,'days':[],'ending_cash':None,'known_cash':100,
                                    'status':'PENDING','reason':'Waiting for city replay','wins':0,'losses':0,'contracts':0,'fees':0,'reserved':0})
                    self.state['profiles']=profiles;self.state['updated_ns']=time.time_ns();self._save()
        with self.lock:
            self.state['job'].update(status='FINISHED_WITH_ERRORS' if failures else 'FINISHED',finished_ns=time.time_ns(),failures=failures,
                message='Saved available analyses. Missing data and unfinished settlement remain pending for the next click.')
            self._save();atomic_json(self.jobs/job_id/'receipt.json',self.state['job'])
