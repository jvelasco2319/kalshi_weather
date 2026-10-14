"""Local paper dashboard coordinator. Public data + pinned city inference only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from copy import deepcopy
import json,threading,time,traceback,hashlib
from .bridge import Release,IntegrityError,runtime_guard
from .ledger import Ledger
from .public_data import PublicData,CITIES
from .impossible_exit import POLICY_ID,INTERVAL_SECONDS,assess,market_monitor
from .policy_receipts import build as build_policy,verify as verify_policy
from .execution_clock import (POLICY_VERSION as BUYING_POLICY_VERSION, NS, MINUTE, entry_slots, slot_end,
 publication_slots, window_metadata, in_slot, prepared_ready)

D=Decimal
class Service:
 def __init__(self,release,runtime):
  self.release=Release(release);self.runtime=runtime_guard(self.release.root,runtime);self.runtime.mkdir(parents=True,exist_ok=True)
  self.profiles=self.release.manifest['profiles'];self.ledger=Ledger(self.runtime/'paper.sqlite3',self.profiles)
  saved=self.ledger.snapshot()
  if saved.get('release_id') not in (None,self.release.manifest['release_id']):raise IntegrityError('Paper ledger belongs to another release; explicit migration required')
  if saved.get('release_id') is None:
   saved['release_id']=self.release.manifest['release_id'];self.ledger.commit(saved,'RELEASE_BOUND',{'release_id':saved['release_id']})
  self.provider=PublicData(self.runtime/'public');self.lock=threading.RLock();self.stop=threading.Event()
  self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='city-weather')
  self.public_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='temperature-and-market')
  self.busy=set();self.weather_busy=set();self.feed={};self.last_public={};self.last_weather={};self.clock_seen=set()
  self.clock_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='paper-clock')
  # Native14UTC maximum: NY P09 six + SAFE one + two Miami baskets six each.
  self.arrival_pool=ThreadPoolExecutor(max_workers=19,thread_name_prefix='paper-arrival')
  self.publish_seen=set()
  self.la_protection=self.release.manifest.get('la_exit_policy',{}).get('policy_id')==POLICY_ID
  self.operational=self.release.manifest.get('operational_repairs')=='FOUR_CITY_LIVE_PAPER_REPAIRS_v1'
  self.policy_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='public-policy')
  self.policy_busy=set();self.last_policy={};self.preparing=set();self.last_prepare={}
  self.started_ns=time.time_ns()
  self.scheduler_status='STARTING';self.scheduler_error=None;self.scheduler_updated_ns=None
  self.policy_locks={p['wallet_id']:self.ledger.wallet_transaction(p['wallet_id']) for p in self.profiles}
  self.prefetch={};self.prefetch_busy=set();self.prefetch_seen=set()
  self.freeze=self.release.verify();self.freeze_checked=time.time_ns()
  cache=self.runtime/'feed.json'
  if cache.exists():
   self.feed=json.loads(cache.read_bytes())
   for x in self.feed.values():x['cached_after_restart']=True
  if self.operational:
   for city in CITIES:self.policy_pool.submit(self.warm_city,city)
  self.recovery_thread=threading.Thread(target=self.recover_pending_orders,daemon=True,name='paper-order-recovery')
  self.recovery_thread.start()
 def recover_pending_orders(self):
  for plan in self.ledger.recover_orders(time.time_ns()):
   wid,oid,city=plan['wallet_id'],plan['order_id'],plan['city']
   p=next(p for p in self.profiles if p['wallet_id']==wid)
   try:
    with self.ledger.wallet_transaction(wid):
     w=self.ledger.snapshot()['wallets'][wid];order=w['orders'][oid]
     life=order.get('lifecycle',{});action=plan['action']
     if action=='RESUME_FUTURE_UNATTEMPTED':
      ctx=life['arrival_context']
      self.submit_guarded(self.arrival_pool,city,'RECOVERED_ARRIVAL',self.arrive,p,ctx['feed'],ctx.get('forecast',{}),order)
      self.execution_event(city,'STARTUP_RECOVERY',status='FUTURE_ARRIVAL_RESUMED',wallet_id=wid,order_id=oid)
     elif action=='COMMIT_RECORDED_RESULT':
      with self.ledger.capacity_transaction():
       self.ledger.reconcile(wid,oid,life['result'],life['quote'],life['actual_arrival_ns'],expected_version=life['result_base_version'])
      self.execution_event(city,'STARTUP_RECOVERY',status='RECORDED_RESULT_COMMITTED',wallet_id=wid,order_id=oid)
     elif action=='RETAIN_UNKNOWN':
      self.execution_event(city,'STARTUP_RECOVERY',status='UNKNOWN_RESERVATION_RETAINED',wallet_id=wid,order_id=oid)
     else:
      status='CANCELLED' if action.startswith('CANCEL_') else 'UNKNOWN'
      self.recover_order(p,order,status,action)
   except Exception as error:
    self.execution_event(city,'STARTUP_RECOVERY',status='BLOCKED_RESERVATION_RETAINED',wallet_id=wid,order_id=oid,reason=str(error))
 def recover_order(self,p,order,status,reason):
  wid=p['wallet_id'];now=time.time_ns()
  with self.ledger.wallet_transaction(wid):
   w=self.ledger.snapshot()['wallets'][wid];native=None
   if p['city']=='la':
    result=self.call('la',{'action':'recover_pending','day':order['day'],'profile_id':p['profile_id'],
       'order_id':order['order_id'],'recovery_status':status,'recovery_reason':reason,'at_ns':now,'policy_state':w['policy_state']})
    if result.get('status') not in ('CANCELLED','UNKNOWN') or not result.get('policy_state'):
     raise ValueError('Native recovery did not return a coherent sealed LA state')
    native=result['policy_state']
   outcome=self.ledger.recovery_transition(wid,order['order_id'],status,reason,time.time_ns(),policy_state=native,expected_version=self.ledger.version(w))
   self.execution_event(p['city'],'ORDER_RECOVERY',wallet_id=wid,order_id=order['order_id'],**outcome)
 def execution_event(self,city,phase,**values):
  with self.lock:
   feed=self.feed.setdefault(city,{})
   rows=feed.setdefault('execution_events',[])
   rows.append({'phase':phase,'at_ns':time.time_ns(),**values})
   del rows[:-64]
   self.save_feed()
 def submit_guarded(self,pool,city,phase,method,*args):
  def task():
   try:return method(*args)
   except Exception as error:
    self.execution_event(city,phase,status='BLOCKED',reason=f'{type(error).__name__}: {error}')
    with self.lock:
     self.feed.setdefault(city,{})['execution_error']={'phase':phase,'reason':str(error),'at_ns':time.time_ns()}
     self.save_feed()
  return pool.submit(task)
 def warm_city(self,city):
  try:
   result=self.call(city,{'action':'prepare_runtime'},timeout=90)
   if result.get('status')!='RESIDENT_READY':raise ValueError(result)
   with self.lock:self.feed.setdefault(city,{})['worker_status']='READY';self.save_feed()
  except Exception as e:
   with self.lock:self.feed.setdefault(city,{})['worker_status']='BLOCKED';self.feed[city]['worker_error']=str(e);self.save_feed()
 def save_feed(self):
  with self.lock:
   path=self.runtime/'feed.json';tmp=path.with_suffix('.tmp')
   tmp.write_text(json.dumps(self.feed,allow_nan=False),encoding='utf8');tmp.replace(path)
 def call(self,city,request,timeout=45):
  request=deepcopy(request)
  if getattr(self,'operational',False) and request.get('action') not in ('describe','dependencies','collect_weather'):
   request['_live_actual_dispatch_clock']=True
  return self.release.call(city,request,self.runtime,timeout)
 def definition_market(self,city,feed):
  m=deepcopy(feed['market']);snapshot=feed.get('definitions')
  if snapshot:
   if snapshot['rules_sha256']!=__import__('tempdesk.impossible_exit',fromlist=['rules_hash']).rules_hash(m['contracts']) or {(c['ticker'],c['lower'],c['upper']) for c in snapshot['contracts']}!={(c['ticker'],c['lower'],c['upper']) for c in m['contracts']}:
    raise ValueError('Dated market definitions changed after native capture')
   current={c['ticker']:c for c in m['contracts']}
   m['contracts']=deepcopy(snapshot['contracts'])
   for c in m['contracts']:c['raw']['status']=current[c['ticker']]['raw']['status']
   m['contracts_received_ns']=snapshot['contracts_received_ns'];m['receipt']=snapshot['receipt']
  return m
 def bind_policy(self,city,feed):
  try:
   binding=build_policy(self.provider,city,feed['market'],self.runtime/'policy-receipts')
   proof=verify_policy(binding,city,feed['day'],feed['market']['contracts'],time.time_ns())
   with self.lock:
    existing=self.feed.get(city,{})
    if existing.get('day')==feed['day']:
     existing.update(target_binding=binding,policy_status='VERIFIED_DAILY_CLI_PROXY_FOR_PAPER',policy_error=None,
                     matched_report_days=len(proof['pairs']))
     existing['market']['settlement_target'].update(compatible=True,status='VERIFIED_DAILY_CLI_PROXY_FOR_PAPER',
       explanation=proof['hypothesis'],equivalence_guaranteed=False)
     self.save_feed()
  except Exception as e:
   with self.lock:self.feed.setdefault(city,{})['policy_error']=str(e);self.feed[city]['policy_status']='POLICY_EVIDENCE_PENDING';self.save_feed()
  finally:
   with self.lock:self.policy_busy.discard(city);self.last_policy[city]=time.time()
 def request_policy(self,city,feed):
  with self.lock:
   if city in self.policy_busy:return
   self.policy_busy.add(city);self.policy_pool.submit(self.bind_policy,city,deepcopy(feed))
 def native_inputs(self,city,feed,for_forecast=False):
  m=self.definition_market(city,feed) if getattr(self,'operational',False) else feed['market']
  common={}
  if feed.get('target_binding'):
   proof=verify_policy(feed['target_binding'],city,feed['day'],m['contracts'],time.time_ns())
   common={'target_binding':feed['target_binding'],'paper_mapping_mode':'VERIFIED_DAILY_CLI_PROXY_v1',
           'mapping_contracts':m['contracts'],'fee_policy':proof['fee_policy']}
  if city!='la':return {'contracts':m['contracts'],'contracts_received_ns':m['contracts_received_ns'],**common}
  r=m['receipt']
  # Preserve integer receipt ns as authoritative. A datetime string has only
  # microseconds; ceiling plus one microsecond avoids a native float parser
  # rounding availability to BEFORE the actual receipt.
  stamp=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=(r['received_ns']+999)//1000+1)).isoformat()
  rules='\n'.join(c['raw'].get('rules_primary','')+'\n'+c['raw'].get('rules_secondary','') for c in m['contracts'])
  target={'environment':'PUBLIC_PRODUCTION_PAPER','station':'KLAX','provider':'TWC','precision':'daily_max_integer_f',
   'verified':m['settlement_target']['provider']=='THE_WEATHER_COMPANY','dated_contract_definition':True,
   'climate_date':feed['day'],'rules_sha256':hashlib.sha256(rules.encode()).hexdigest(),
   'source_sha256':r['response_sha256'],'source_raw_path':r['raw_capture'],'actual_public_receipt':True,
   'received_ns':r['received_ns'],'received_at_utc':stamp,'available_utc':stamp,
   'display_clock_precision':'CONSERVATIVE_MICROSECOND_CEILING_PLUS_1US_INTEGER_RECEIPT_AUTHORITATIVE',
   'physical_to_settlement_equivalence_proven':False}
  contracts=[{**c,'lower_f':c['lower'],'upper_f':None if c['upper'] is None else c['upper']-1,
   'lower_inclusive':True,'upper_inclusive':True,'event_id':m['event_ticker']} for c in m['contracts']]
  if for_forecast:
   # API outcomes (even empty keys) are provenance, never weather-model input.
   contracts=[{k:c[k] for k in ('ticker','label','lower_f','upper_f','lower_inclusive','upper_inclusive','event_id')} for c in contracts]
  return {'contracts':contracts,'contracts_received_ns':m['contracts_received_ns'],'target_receipt':target,
   'fee_policy':{'coefficient':'.07','multiplier':'1','verified':False,'source_ref':'LIVE_FEE_SOURCE_UNVERIFIED'},**common}
 def refresh(self):
  with self.lock:
   for city in CITIES:
    if city not in self.busy:
     self.busy.add(city);self.last_public[city]=time.time()
     getattr(self,'public_pool',self.pool).submit(self.refresh_city,city)
  return {'status':'REFRESH_STARTED','public_GET_only':True,'orders_sent':0}
 def refresh_city(self,city):
  now=datetime.now(ZoneInfo(CITIES[city]['zone']));day=now.date().isoformat()
  try:
   market=self.provider.markets(city,day);quotes=self.provider.books(market['contracts'])
   try:observed=self.provider.settlement_observations(CITIES[city]['station'],day,market)
   except Exception as error:observed={'status':'OBSERVATIONS_UNAVAILABLE','values':[],'error':str(error)}
   with self.lock:
    previous=self.feed.get(city,{}) if self.feed.get(city,{}).get('day')==day else {}
    if getattr(self,'operational',False) and market['layout_complete']:
     if not previous.get('definitions'):
      from .impossible_exit import rules_hash
      previous['definitions']={'contracts':deepcopy(market['contracts']),'contracts_received_ns':market['contracts_received_ns'],
       'receipt':market['receipt'],'rules_sha256':rules_hash(market['contracts'])}
     if previous.get('target_binding'):
      try:
       proof=verify_policy(previous['target_binding'],city,day,market['contracts'],time.time_ns())
       market['settlement_target'].update(compatible=True,status='VERIFIED_DAILY_CLI_PROXY_FOR_PAPER',explanation=proof['hypothesis'],equivalence_guaranteed=False)
      except Exception:previous.pop('target_binding',None)
    self.feed[city]={**previous,'city':city,'day':day,'market':market,'quotes':quotes,'observed':observed,
     'public_updated_ns':time.time_ns(),'cached_after_restart':False,'public_error':None}
    self.save_feed()
   if getattr(self,'operational',False) and not self.feed[city].get('target_binding') and time.time()-self.last_policy.get(city,0)>900:self.request_policy(city,self.feed[city])
   self.settle_known(city,market)
   days={pos['day'] for p in self.profiles if p['city']==city for pos in self.ledger.snapshot()['wallets'][p['wallet_id']]['positions'].values()}
   for oldday in sorted(days-{day}):self.settle_known(city,self.provider.markets(city,oldday))
   if city=='la' and self.la_protection:self.check_impossible_positions()
  except Exception as error:
   with self.lock:
    self.feed.setdefault(city,{'city':city,'day':day})['public_error']=f'{type(error).__name__}: {error}'
    self.save_feed()
  finally:
   with self.lock:self.busy.discard(city)
 def check_impossible_positions(self):
  """No forecast threshold. No proof means HOLD, regardless of physical NWS high."""
  with self.lock:feed=deepcopy(self.feed.get('la',{}))
  market=feed.get('market',{});observed=feed.get('observed',{});now=time.time_ns()
  monitoring=market_monitor(market,observed,now);proof=observed.get('settlement_high_floor')
  for p in self.profiles:
   if p['city']!='la':continue
   with self.policy_locks[p['wallet_id']]:
    w=self.ledger.snapshot()['wallets'][p['wallet_id']]
    if any(o['status'] in ('PENDING','UNKNOWN') for o in w['orders'].values()):
     monitoring['positions'].append({'wallet_id':p['wallet_id'],'status':'HOLD','reason':'PENDING_OR_UNKNOWN_ORDER'});continue
    checks=[]
    for pos in w['positions'].values():
     c=next((c for c in market.get('contracts',[]) if c['ticker']==pos['ticker']),None)
     if c is None:continue
     signal=assess(pos,c,proof,market,now)
     checks.append(signal);monitoring['positions'].append({k:v for k,v in {**signal,'wallet_id':p['wallet_id']}.items() if k!='proof'})
    if not any(s['definitely_impossible'] for s in checks):continue
    quotes={t:self.ledger.remaining_book(q) for t,q in feed.get('quotes',{}).items() if q.get('received_ns')}
    request={'action':'impossible_exit','profile_id':p['profile_id'],'day':feed['day'],'at_ns':time.time_ns(),
     'settlement_high_floor':proof,'protection_market':market,'quotes':quotes,
     **self.native_inputs('la',feed),**self.ledger.request_cash(p['wallet_id'],feed['day'])}
    result=self.call('la',request);decision_ns=time.time_ns()
    orders=self.ledger.reserve(p['wallet_id'],feed['day'],result,p['daily_cap'],decision_ns,
     expected_version=request['policy_state_version'],arrival_context={'feed':feed,'forecast':{}})
    monitoring.setdefault('native_decisions',{})[p['profile_id']]=result.get('position_checks',[])
    for order in orders:self.submit_guarded(self.arrival_pool,'la','PROTECTIVE_ARRIVAL',self.arrive,p,feed,{},order)
  with self.lock:
   self.feed.setdefault('la',{}).update(protection=monitoring);self.save_feed()
 def settle_known(self,city,market):
  # Serialize native settlement and common cash with decisions/arrivals.
  for p in self.profiles:
   if p['city']!=city:continue
   wid=p['wallet_id']
   with self.ledger.wallet_transaction(wid):
    for c in market['contracts']:
     raw=c['raw']
     if raw.get('status') not in ('finalized','settled') or raw.get('result') not in ('yes','no'):continue
     w=self.ledger.snapshot()['wallets'][wid]
     if not any(pos['ticker']==c['ticker'] for pos in w['positions'].values()):continue
     native=None;version=self.ledger.version(w)
     if city in ('ny','la'):
      try:
       receipt=market['receipt']
       request={'action':'settle','day':market['day'],'profile_id':p['profile_id'],
        'at_ns':time.time_ns(),'policy_state':w['policy_state'],
        'settlement':{'ticker':c['ticker'],'available_ns':market['contracts_received_ns'],
         'source_receipt':{'path':receipt['raw_capture'],'sha256':receipt['response_sha256'],'received_ns':receipt['received_ns']}}}
       if city=='la':request['official_receipts']=[{'ticker':c['ticker'],'source_receipt':receipt}]
       result=self.call(city,request)
       if result.get('status')=='BLOCKED':raise ValueError(result)
       native=result.get('policy_state')
      except Exception as error:
       self.execution_event(city,'SETTLEMENT',status='BLOCKED',wallet_id=wid,reason=str(error));continue
     self.ledger.settle(wid,raw,market['contracts_received_ns'],native,expected_version=version)
 def collect_city(self,city):
  try:
   with self.lock:feed=deepcopy(self.feed.get(city,{}))
   day=feed.get('day',datetime.now(ZoneInfo(CITIES[city]['zone'])).date().isoformat())
   if day[:7]!='2026-10':
    with self.lock:self.feed.setdefault(city,{})['weather_status']='NEW_MONTH_CHECKPOINT_REQUIRED'
    return
   request={'action':'collect_weather','day':day,'capture_dir':str(self.runtime/city/'weather'),
    'cached_packet':feed.get('weather',{}),'previous_packet':feed.get('weather',{})}
   result=self.call(city,request,timeout=600)
   weather=result.get('weather',result.get('packet',result))
   forecasts={}
   if feed.get('market',{}).get('layout_complete'):
    for p in self.profiles:
     if p['city']!=city:continue
     if getattr(self,'operational',False) and (time.time()%60<2 or time.time()%60>=50):continue
     try:
      request={'action':'forecast','day':day,'profile_id':p['profile_id'],'weather':weather,
       **self.native_inputs(city,feed,for_forecast=True),'at_ns':time.time_ns()}
      if city=='la':
       request['frame']=weather.get('frame') or {'climate_date':day,'forecasts':weather.get('forecasts',{}).get('forecasts',[]),
        'observations':weather.get('observations',{}).get('observations',[]),'observation_target_compatible':False,'availability_mode':'actual_receipt'}
      r=self.call(city,request,timeout=90)
      forecasts[p['profile_id']]=r
     except Exception as error:forecasts[p['profile_id']]={'status':'UNAVAILABLE','error':f'{type(error).__name__}: {error}'}
   with self.lock:
    existing=self.feed.setdefault(city,{'city':city,'day':day})
    # Keep a genuine fixed publication until expiry/day rollover. A later
    # monitoring capture cannot replace the native decision snapshot.
    for pid,old in existing.get('forecasts',{}).items():
     inner=old.get('forecast',old)
     # F6 is an actual rolling signal. Preserve a ready publication for the
     # static/scheduled models, while F6 may adopt newer genuine observations.
     if city=='la' and pid=='F6_HYBRID_STAGED5' and forecasts.get(pid,{}).get('forecast',{}).get('trade_eligible') is True:continue
     if old.get('native_publication_retained') or (inner.get('timely_native_capture') is True and existing.get('weather',{}).get('retained_before_native_cutoff')):
      forecasts[pid]=old
    retained=existing.get('weather',{})
    if retained.get('retained_before_native_cutoff') and city!='la':weather=retained
    anchor=14 if city in ('ny','miami') else 17
    cutoff=int(datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=timezone.utc).timestamp()*1e9)+anchor*3600*10**9
    complete=weather.get('complete') is True or weather.get('complete_native_capture') is True or weather.get('forecasts',{}).get('complete') is True
    if complete and time.time_ns()<cutoff:weather['retained_before_native_cutoff']=True
    existing.update(weather=weather,forecasts=forecasts,
     weather_updated_ns=time.time_ns(),weather_status=result.get('status','CAPTURED'),weather_error=None)
    self.last_weather[city]=time.time();self.save_feed()
  except Exception as error:
   with self.lock:
    self.feed.setdefault(city,{})['weather_error']=f'{type(error).__name__}: {error}'
    self.last_weather[city]=time.time();self.save_feed()
  finally:
   with self.lock:self.weather_busy.discard(city)
 def request_weather(self,city):
  with self.lock:
   if city in self.weather_busy:return
   self.weather_busy.add(city);self.pool.submit(self.collect_city,city)
 def prepare_publication(self,city,feed,nominal):
  results={}
  try:
   for p in self.profiles:
    if p['city']!=city or city=='la' and 'V8D' in p['profile_id'] and nominal%86400000000000!=18*3600*10**9:continue
    request={'action':'forecast','purpose':'prepare_publication','publication_utc':datetime.fromtimestamp(nominal/1e9,timezone.utc).isoformat(),
      'day':feed['day'],'profile_id':p['profile_id'],'at_ns':time.time_ns(),**self.native_inputs(city,feed,True),'weather':feed.get('weather',{})}
    if city=='la':request['frame']=feed.get('weather',{}).get('frame')
    result=self.call(city,request,timeout=90);finished=time.time_ns()
    inner=result.get('forecast',result)
    ready=inner.get('prepared_ready_ns',finished)
    result['prepared_ready_ns']=ready
    inner.setdefault('prepared_ready_ns',ready)
    inner.setdefault('actual_generation_ns',finished)
    # Native readiness is genuinely recorded before its publication slot.
    result['preparation_completed_ns']=finished
    if finished>=nominal:
     inner['trade_eligible']=False;result['prepared']=False
     result.setdefault('blockers',[]).append('FORECAST_PREPARATION_COMPLETED_AFTER_NATIVE_SLOT')
    results[p['profile_id']]=result
   with self.lock:
    if self.feed.get(city,{}).get('day')==feed['day']:
     self.feed[city].setdefault('prepared_forecasts',{})[str(nominal)]=results;self.save_feed()
  except Exception as e:
   with self.lock:self.feed.setdefault(city,{})['preparation_error']=str(e)
  finally:
   with self.lock:self.preparing.discard((city,nominal));self.last_prepare[(city,nominal)]=time.time()
 def schedule_slot(self,p,day,now_ns):
  return in_slot(p,day,now_ns)
 def publish_city(self,city,feed,at_ns):
  """Issue from retained native inputs at actual time; never recapture a cutoff."""
  results={};nominal=at_ns//(60*10**9)*(60*10**9)
  for p in self.profiles:
   if p['city']!=city:continue
   if getattr(self,'operational',False) and city=='la' and 'V8D' in p['profile_id'] and nominal%(86400*10**9)!=18*3600*10**9:continue
   request={'action':'forecast','day':feed['day'],'profile_id':p['profile_id'],'at_ns':time.time_ns(),
    **self.native_inputs(city,feed,for_forecast=True),'weather':feed.get('weather',{})}
   if city=='la':request['frame']=feed.get('weather',{}).get('frame')
   try:
    prepared=feed.get('prepared_forecasts',{}).get(str(nominal),{}).get(p['profile_id']) if getattr(self,'operational',False) else None
    if not prepared:raise ValueError('PREPARED_NATIVE_FORECAST_UNAVAILABLE')
    r=deepcopy(prepared)
    if prepared:
     inner=r.get('forecast',r)
     okay,reason=prepared_ready(r,city,nominal)
     if not okay:raise ValueError(reason)
     published=time.time_ns()
     if not nominal<=published<nominal+NS:raise ValueError('ACTUAL_PUBLICATION_SLOT_MISSED')
     # Availability is preparation, not publication. Phoenix's static native
     # gate requires the actual completed signal before17 UTC.
     if city!='phoenix':inner.setdefault('available_ns',r['prepared_ready_ns'])
     inner.update(trade_eligible=True,actual_publication_ns=published,timely_native_capture=True,forecast_event_ns=published,prepared=False)
     r['prepared']=False
     if city=='la':inner['available_utc']=datetime.now(timezone.utc).isoformat()
   except Exception as e:r={'status':'UNAVAILABLE','error':str(e)}
   completed=time.time_ns();inner=r.get('forecast',r)
   timely=nominal<=completed<nominal+10**9
   r['actual_publication_ns']=completed;r['native_publication_admitted']=timely and inner.get('trade_eligible') is True
   r['native_publication_retained']=timely and bool(inner.get('probabilities_yes')) and (
    inner.get('timely_native_capture') is True or inner.get('weather_trade_eligible') is True or inner.get('actual_prospective_issuance') is True)
   if not timely:
    inner['trade_eligible']=False
    r.setdefault('blockers',[]).append('ACTUAL_PUBLICATION_SLOT_MISSED')
   results[p['profile_id']]=r
  with self.lock:
   self.feed[city].setdefault('forecasts',{}).update(results);self.save_feed()
   fresh=deepcopy(self.feed[city])
  # The first buy is dispatched AFTER its prospective publication completes,
  # within the same genuine native slot. It never receives the previous feed.
  for p in self.profiles:
   if p['city']!=city:continue
   r=results.get(p['profile_id'],{})
   if r.get('native_publication_admitted'):
    key=(p['wallet_id'],fresh['day'],nominal//(60*10**9))
    with self.lock:self.clock_seen.add(key)
    self.submit_guarded(self.clock_pool,city,'DECISION',self.decide_profile,p,fresh,r['actual_publication_ns'])
 def prefetch_city(self,city,feed,slot_ns):
  try:
   if getattr(self,'operational',False):self.release.evidence_cache.check(self.release)
   quotes=self.provider.books(feed['market']['contracts'])
   # Keep both receipt time and actual processing time; no catch-up quotes.
   with self.lock:self.prefetch[(city,slot_ns)]={'quotes':quotes,'completed_ns':time.time_ns()}
  finally:
   with self.lock:self.prefetch_busy.discard((city,slot_ns))
 def decide_profile(self,p,feed,at_ns):
  with self.ledger.wallet_transaction(p['wallet_id']):return self._decide_locked(p,feed,at_ns)
 def _decide_locked(self,p,feed,at_ns):
  wid=p['wallet_id'];day=feed['day'];raw=feed.get('forecasts',{}).get(p['profile_id'],{})
  forecast=raw.get('forecast',raw)
  w=self.ledger.snapshot()['wallets'][wid]
  def skip(reason):
   self.execution_event(p['city'],'DECISION',status='SKIPPED',wallet_id=wid,reason=reason)
  if any(o['status'] in ('PENDING','UNKNOWN') for o in w['orders'].values()):return skip('PENDING_OR_UNKNOWN_ORDER')
  # Never promote physical-only forecasts into TWC economic probability.
  if not feed.get('market',{}).get('settlement_target',{}).get('compatible') or forecast.get('trade_eligible') is not True:return skip('FORECAST_OR_TARGET_NOT_READY')
  if not self.schedule_slot(p,day,time.time_ns()):return skip('ACTUAL_NATIVE_BUY_SLOT_MISSED')
  if time.time_ns()-at_ns>=NS:return skip('DECISION_DISPATCH_TOO_LATE')
  nominal=at_ns//(60*10**9)*(60*10**9)
  cached=self.prefetch.get((p['city'],nominal),{})
  quotes=cached.get('quotes',{})
  if not quotes or cached.get('completed_ns',nominal+1)>nominal:return skip('PRE_SLOT_BOOK_CAPTURE_UNAVAILABLE')
  if any(q.get('received_ns',nominal+1)>nominal for q in quotes.values() if q.get('status')=='AVAILABLE'):return skip('BOOK_FIRST_RECEIVED_AFTER_SLOT')
  actual=time.time_ns()
  request={'action':'decide','profile_id':p['profile_id'],'day':day,'at_ns':actual,
   'forecast':forecast,'weather':feed.get('weather',{}),**self.native_inputs(p['city'],feed),
   'quotes':{t:self.ledger.remaining_book(q) for t,q in quotes.items() if q.get('status')=='AVAILABLE'},
   **self.ledger.request_cash(wid,day)}
  result=self.call(p['city'],request)
  completed=time.time_ns()
  if completed-actual>=10**9 or completed-nominal>=10**9:
   # A result computed after its genuine native slot is not a live intent.
   with self.lock:self.feed[p['city']].setdefault('decisions',{})[p['profile_id']]={
    'status':'MISSED_PROCESSING_SLOT','at_ns':completed,'orders':[],'actual_orders':0}
   return
  actual=result.get('actual_decision_ns',result.get('worker_started_ns',actual))
  orders=self.ledger.reserve(wid,day,result,p['daily_cap'],actual,
   expected_version=request['policy_state_version'],arrival_context={'feed':feed,'forecast':forecast})
  with self.lock:self.feed[p['city']].setdefault('decisions',{})[p['profile_id']]={'result':result,'at_ns':actual}
  self.execution_event(p['city'],'DECISION',status=result.get('status'),wallet_id=wid,
   nominal_ns=nominal,dispatch_ns=request['at_ns'],completed_ns=completed,orders_created=len(orders))
  for order in orders:self.submit_guarded(self.arrival_pool,p['city'],'ARRIVAL',self.arrive,p,feed,forecast,order)
 def arrive(self,p,feed,forecast,order):
  # Prepare a genuine recent book before the due time. Its original receipt
  # remains authoritative; native five-second freshness and arrival still apply.
  wid=p['wallet_id'];oid=order['order_id'];due=order['arrival_due_ns']
  self.ledger.prepare_arrival(wid,oid,time.time_ns())
  # Original LA transport requires a newly received arrival book at/after the
  # due time; its native120s response bound stays intact. Other native cities
  # allow a recently received pre-arrival book within their original freshness.
  lead=0 if p['city']=='la' or order.get('action')=='SELL' else 2*NS
  self.stop.wait(max(0,(due-lead-time.time_ns())/NS))
  if self.stop.is_set():return
  try:
   physical=self.provider.book(order['ticker'])
   self.stop.wait(max(0,(due-time.time_ns())/NS))
   if self.stop.is_set():return
   with self.ledger.wallet_transaction(wid),self.ledger.capacity_transaction():
    at=time.time_ns();version=self.ledger.begin_attempt(wid,oid,at)
    quote=self.ledger.remaining_book(physical)
    w=self.ledger.snapshot()['wallets'][wid]
    action='impossible_exit_arrival' if order.get('exit_policy')==POLICY_ID else 'arrival'
    result=self.call(p['city'],{'action':action,'profile_id':p['profile_id'],'day':feed['day'],
     'at_ns':at,'frozen_order':order,'quotes':{order['ticker']:quote},'quote':quote,'forecast':forecast,
     'policy_state':w['policy_state'],'state':w['policy_state'],'available_cash':str(self.ledger.free(w)),
     **self.native_inputs(p['city'],feed)})
    actual=result.get('actual_arrival_ns',result.get('worker_started_ns',at))
    if p['city']=='la' and result.get('status') not in ('FILLED','CANCELLED'):
     recovered=self.call('la',{'action':'recover_pending','day':order['day'],'profile_id':p['profile_id'],
      'order_id':oid,'recovery_status':'UNKNOWN','recovery_reason':result.get('reason','UNRESOLVED_EXECUTION'),
      'at_ns':time.time_ns(),'policy_state':w['policy_state']})
     if recovered.get('status')!='UNKNOWN' or not recovered.get('policy_state'):raise ValueError('Unknown LA arrival could not retain native reservation')
     result['policy_state']=recovered['policy_state']
    self.ledger.record_arrival_result(wid,oid,result,physical,actual,expected_version=version)
    self.ledger.reconcile(wid,oid,result,physical,actual,expected_version=version)
   self.execution_event(p['city'],'ARRIVAL',status=result.get('status'),wallet_id=wid,order_id=oid,
    quote_received_ns=physical['received_ns'],due_ns=due,actual_arrival_ns=actual,reason=result.get('reason'))
  except Exception as error:
   with self.ledger.wallet_transaction(wid):
    stored=self.ledger.snapshot()['wallets'][wid]['orders'][oid]
    if stored['status'] not in ('PENDING','UNKNOWN'):return
    unattempted=stored.get('lifecycle',{}).get('phase') in ('INTENT_RECORDED','ARRIVAL_PREPARING')
    self.recover_order(p,stored,'CANCELLED' if unattempted else 'UNKNOWN',
     'UNATTEMPTED_PAPER_ARRIVAL_FAILED: '+str(error) if unattempted else 'NATIVE_EXECUTION_OR_COMMIT_UNCERTAIN: '+str(error))
 def run(self):
  self.scheduler_status='RUNNING'
  try:self._run_loop()
  except Exception as error:
   self.scheduler_status='BLOCKED';self.scheduler_error=f'{type(error).__name__}: {error}'
   for city in CITIES:self.execution_event(city,'SCHEDULER',status='BLOCKED',reason=self.scheduler_error)
  finally:
   if self.scheduler_status=='RUNNING':self.scheduler_status='STOPPED'
 def _run_loop(self):
  self.refresh()
  while not self.stop.wait(.2):
   wall=time.time();ns=time.time_ns()
   self.scheduler_updated_ns=ns
   if ns-self.freeze_checked>60*10**9:
    try:self.freeze=self.release.verify()
    except Exception as error:self.freeze={'status':'BLOCKED','error':str(error)}
    self.freeze_checked=ns
   for city,cfg in CITIES.items():
    local=datetime.now(ZoneInfo(cfg['zone']));interval=INTERVAL_SECONDS if city=='la' and getattr(self,'la_protection',False) else 60 if 10<=local.hour<17 else 900
    if wall-self.last_public.get(city,0)>=interval:
     with self.lock:
      if city not in self.busy:
       self.busy.add(city);self.last_public[city]=wall
       getattr(self,'public_pool',self.pool).submit(self.refresh_city,city)
    with self.lock:feed=deepcopy(self.feed.get(city,{}))
    weather_interval=300 if city=='la' and getattr(self,'operational',False) else 900
    if feed.get('market') and (city=='la' and getattr(self,'operational',False) or not feed.get('weather',{}).get('retained_before_native_cutoff')) and wall-self.last_weather.get(city,0)>=weather_interval:self.request_weather(city)
   if self.freeze.get('status')=='VERIFIED':
    for city in CITIES:
     with self.lock:current=deepcopy(self.feed.get(city,{}))
     day=current.get('day')
     if not day or day[:7]!='2026-10':continue
     slots=publication_slots(city,day)
     upcoming=next((value for value in slots if value>ns),0)
     if getattr(self,'operational',False) and current.get('target_binding') and 0<upcoming-ns<=300*NS:
      key=(city,upcoming);prepared=current.get('prepared_forecasts',{}).get(str(upcoming),{})
      ready=bool(prepared) and all(prepared_ready(r,city,upcoming)[0] for r in prepared.values())
      if not ready and key not in self.preparing and wall-self.last_prepare.get(key,0)>=30:
       self.preparing.add(key);self.submit_guarded(self.clock_pool,city,'PREPARATION',self.prepare_publication,city,current,upcoming)
     nominal=ns//MINUTE*MINUTE;publication=(city,day,nominal)
     if nominal in slots and nominal<=ns<nominal+NS and publication not in self.publish_seen:
      if current.get('market'):
       self.publish_seen.add(publication);self.submit_guarded(self.clock_pool,city,'PUBLICATION',self.publish_city,city,current,ns)
    for city in CITIES:
     with self.lock:feed=deepcopy(self.feed.get(city,{}))
     nominal=(ns//(60*10**9)+1)*(60*10**9)
     if nominal-ns<=3*10**9 and feed.get('market',{}).get('settlement_target',{}).get('compatible'):
      key=(city,nominal)
      if key not in self.prefetch_seen and any(self.schedule_slot(p,feed.get('day'),nominal) for p in self.profiles if p['city']==city):
       self.prefetch_seen.add(key);self.prefetch_busy.add(key);self.submit_guarded(getattr(self,'public_pool',self.clock_pool),city,'BOOK_PREPARATION',self.prefetch_city,city,feed,nominal)
    for p in self.profiles:
     with self.lock:feed=deepcopy(self.feed.get(p['city'],{}))
     day=feed.get('day');slot=(p['wallet_id'],day,ns//(60*10**9))
     raw=feed.get('forecasts',{}).get(p['profile_id'],{});eligible=raw.get('forecast',raw).get('trade_eligible') is True
     if day and eligible and slot not in self.clock_seen and self.schedule_slot(p,day,ns):
      self.clock_seen.add(slot)
      self.submit_guarded(self.clock_pool,p['city'],'DECISION',self.decide_profile,p,feed,ns)
 def execution_metadata(self,city,feed,state,now):
  day=feed.get('day');profiles={}
  for p in self.profiles:
   if p['city']!=city:continue
   pid=p['profile_id'];w=state['wallets'][p['wallet_id']]
   slots=entry_slots(p,day) if day and day[:7]=='2026-10' else []
   raw=feed.get('forecasts',{}).get(pid,{})
   prepared=[r.get(pid) for r in feed.get('prepared_forecasts',{}).values() if r.get(pid)]
   unresolved=any(o['status']=='UNKNOWN' for o in w['orders'].values())
   pending=any(o['status']=='PENDING' for o in w['orders'].values())
   window=window_metadata(p,day,now)
   profiles[pid]={**window,'entry_windows':[{'start_ns':s,'end_ns':slot_end(p,day,s)} for s in slots],
    'next_window_ns':window.get('next_buy_ns'),
    'preparation_status':'PREPARED' if prepared and any(r.get('prepared') is True for r in prepared) else 'WAITING_FOR_PREPARATION',
    'publication_status':'ADMITTED' if raw.get('native_publication_admitted') else raw.get('status','WAITING_FOR_PUBLICATION'),
    'recovery_status':'UNKNOWN_RESERVATION_RETAINED' if unresolved else 'PENDING_ARRIVAL' if pending else 'CLEAR',
    'policy_state_version':self.ledger.version(w)}
  return {'checkpoint_month':'2026-10','policy_version':BUYING_POLICY_VERSION,'profiles':profiles}
 def snapshot(self):
  with self.lock:
   feed=deepcopy(self.feed);busy=set(self.busy);weather_busy=set(self.weather_busy)
  state=self.ledger.snapshot();wallets=[]
  for p in self.profiles:
   w=state['wallets'][p['wallet_id']];wallets.append({**p,**w,'free_cash':str(self.ledger.free(w)),
    'reserved':str(D(w['cash'])-self.ledger.free(w))})
  cities=[]
  for city,config in CITIES.items():
   f=feed.get(city,{});market=f.get('market',{});obs=f.get('observed',{});values=obs.get('values',[])
   forecasts={}
   for pid,result in f.get('forecasts',{}).items():
    inner=result.get('forecast',result.get('display_estimate',result))
    forecasts[pid]={k:inner.get(k) for k in ('status','model_id','location_f','probabilities_yes','trade_eligible','blockers','error','timely_native_capture')}
    if inner.get('display_probabilities_yes'):forecasts[pid]['probabilities_yes']=inner['display_probabilities_yes']
    if forecasts[pid].get('location_f') is None:forecasts[pid]['location_f']=inner.get('mu')
    if result.get('reason'):forecasts[pid]['blockers']=[result['reason']]
    if result.get('blockers'):forecasts[pid]['blockers']=result['blockers']
   age=(time.time_ns()-f['public_updated_ns'])/1e9 if f.get('public_updated_ns') else None
   cities.append({**config,'city_id':city,'day':f.get('day'),'local_time':datetime.now(ZoneInfo(config['zone'])).isoformat(),
    'market':{k:market.get(k) for k in ('event_ticker','layout_complete','settlement_target','contracts_received_ns')},
    'contracts':[{'ticker':c['ticker'],'label':c['label'],'lower':c['lower'],'upper':c['upper'],
     'status':c['raw'].get('status'),'result':c['raw'].get('result')} for c in market.get('contracts',[])],
    'quotes':{t:{k:q.get(k) for k in ('bid','ask','yes_bids','received_ns','status','source_age_clock')} for t,q in f.get('quotes',{}).items()},
    'observed':values,'latest_temperature_f':values[-1]['temperature_f'] if values else None,
    'observed_high_f':max((v['temperature_f'] for v in values),default=None),'observations_status':obs.get('status'),
    'observation_provider':obs.get('provider'),'observation_product':obs.get('observation_product'),
    'daily_settlement_report':obs.get('daily_report'),
    'forecasts':forecasts,'public_updated_ns':f.get('public_updated_ns'),'public_age_seconds':age,
    'public_error':f.get('public_error'),'refreshing':city in busy,'weather_collecting':city in weather_busy,
    'weather_status':f.get('weather_status'),'weather_error':f.get('weather_error'),
    'weather_updated_ns':f.get('weather_updated_ns'),'decisions':f.get('decisions',{}),
    'protection':f.get('protection'),
    'policy_status':f.get('policy_status'),'policy_error':f.get('policy_error'),'matched_report_days':f.get('matched_report_days'),
    'definition_captured_ns':f.get('definitions',{}).get('contracts_received_ns'),
    'execution_events':f.get('execution_events',[]),'execution_error':f.get('execution_error'),
    'execution_metadata':self.execution_metadata(city,f,state,time.time_ns()),
    'weather_field_count':len(f.get('weather',{}).get('fields',[])) or len(f.get('weather',{}).get('forecasts',{}).get('forecasts',[])),
    'wallets':[w for w in wallets if w['city']==city]})
  return {'at_ns':time.time_ns(),'freeze':self.freeze,'checkpoint_month':'2026-10','paper_only':True,
   'scheduler':{'status':getattr(self,'scheduler_status','STARTING'),'error':getattr(self,'scheduler_error',None),'updated_ns':getattr(self,'scheduler_updated_ns',None)},
   'actual_orders':0,'cities':cities,'wallets':wallets,'total_initial':'900',
   'total_cash':str(sum((D(w['cash']) for w in wallets),D('0'))),'version':self.release.manifest['release_id']}
