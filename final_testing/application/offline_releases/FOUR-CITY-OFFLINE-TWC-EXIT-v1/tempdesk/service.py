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
  self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='city-public')
  self.public_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='temperature-and-market')
  self.busy=set();self.weather_busy=set();self.feed={};self.last_public={};self.last_weather={};self.clock_seen=set()
  self.clock_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='paper-clock')
  self.arrival_pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='paper-arrival')
  self.publish_seen=set()
  self.la_protection=self.release.manifest.get('la_exit_policy',{}).get('policy_id')==POLICY_ID
  self.policy_locks={p['wallet_id']:threading.RLock() for p in self.profiles}
  self.prefetch={};self.prefetch_busy=set();self.prefetch_seen=set()
  self.freeze=self.release.verify();self.freeze_checked=time.time_ns()
  cache=self.runtime/'feed.json'
  if cache.exists():
   self.feed=json.loads(cache.read_bytes())
   for x in self.feed.values():x['cached_after_restart']=True
 def save_feed(self):
  with self.lock:
   path=self.runtime/'feed.json';tmp=path.with_suffix('.tmp')
   tmp.write_text(json.dumps(self.feed,allow_nan=False),encoding='utf8');tmp.replace(path)
 def call(self,city,request,timeout=45):return self.release.call(city,request,self.runtime,timeout)
 def native_inputs(self,city,feed,for_forecast=False):
  if city!='la':return {'contracts':feed['market']['contracts'],'contracts_received_ns':feed['market']['contracts_received_ns']}
  m=feed['market'];r=m['receipt']
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
   'fee_policy':{'coefficient':'.07','multiplier':'1','verified':False,'source_ref':'LIVE_FEE_SOURCE_UNVERIFIED'}}
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
    self.feed[city]={**previous,'city':city,'day':day,'market':market,'quotes':quotes,'observed':observed,
     'public_updated_ns':time.time_ns(),'cached_after_restart':False,'public_error':None}
    self.save_feed()
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
    orders=self.ledger.reserve(p['wallet_id'],feed['day'],result,p['daily_cap'],decision_ns)
    monitoring.setdefault('native_decisions',{})[p['profile_id']]=result.get('position_checks',[])
    for order in orders:self.arrival_pool.submit(self.arrive,p,feed,{},order)
  with self.lock:
   self.feed.setdefault('la',{}).update(protection=monitoring);self.save_feed()
 def settle_known(self,city,market):
  # Actual official result receipt is the paper cash release clock.
  for p in self.profiles:
   if p['city']!=city:continue
   for c in market['contracts']:
    raw=c['raw']
    if raw.get('status') not in ('finalized','settled') or raw.get('result') not in ('yes','no'):continue
    w=self.ledger.snapshot()['wallets'][p['wallet_id']]
    if not any(pos['ticker']==c['ticker'] for pos in w['positions'].values()):continue
    native=None
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
      self.feed[city]['settlement_error']=str(error);continue
    self.ledger.settle(p['wallet_id'],raw,market['contracts_received_ns'],native)
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
     if old.get('native_publication_retained') or (inner.get('timely_native_capture') is True and existing.get('weather',{}).get('retained_before_native_cutoff')):
      forecasts[pid]=old
    retained=existing.get('weather',{})
    if retained.get('retained_before_native_cutoff'):weather=retained
    anchor=14 if city in ('ny','miami') else 17
    cutoff=int(datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=timezone.utc).timestamp()*1e9)+anchor*3600*10**9
    complete=weather.get('complete') is True or weather.get('complete_native_capture') is True
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
 def schedule_slot(self,p,day,now_ns):
  """Only actual prospective slots; never replay a missed clock as live."""
  dt=datetime.fromtimestamp(now_ns/1e9,timezone.utc);minute=dt.hour*60+dt.minute
  if dt.second!=0:return False
  city=p['city'];pid=p['profile_id']
  if city=='miami':return minute==14*60
  if city=='ny':return minute==14*60 if pid=='CONSERVATIVE_P09' else 14*60<=minute<=17*60
  if city=='phoenix':return minute in (17*60,17*60+15,17*60+30,18*60,19*60)
  return minute==18*60 if 'V8D' in pid else 17*60<=minute<=20*60
 def publish_city(self,city,feed,at_ns):
  """Issue from retained native inputs at actual time; never recapture a cutoff."""
  results={};nominal=at_ns//(60*10**9)*(60*10**9)
  for p in self.profiles:
   if p['city']!=city:continue
   request={'action':'forecast','day':feed['day'],'profile_id':p['profile_id'],'at_ns':time.time_ns(),
    **self.native_inputs(city,feed,for_forecast=True),'weather':feed.get('weather',{})}
   try:r=self.call(city,request,timeout=60)
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
    self.clock_pool.submit(self.decide_profile,p,fresh,r['actual_publication_ns'])
 def prefetch_city(self,city,feed,slot_ns):
  try:
   quotes=self.provider.books(feed['market']['contracts'])
   # Keep both receipt time and actual processing time; no catch-up quotes.
   with self.lock:self.prefetch[(city,slot_ns)]={'quotes':quotes,'completed_ns':time.time_ns()}
  finally:
   with self.lock:self.prefetch_busy.discard((city,slot_ns))
 def decide_profile(self,p,feed,at_ns):
  wid=p['wallet_id'];day=feed['day'];raw=feed.get('forecasts',{}).get(p['profile_id'],{})
  forecast=raw.get('forecast',raw)
  w=self.ledger.snapshot()['wallets'][wid]
  if any(o['status'] in ('PENDING','UNKNOWN') for o in w['orders'].values()):return
  # Never promote physical-only forecasts into TWC economic probability.
  if not feed.get('market',{}).get('settlement_target',{}).get('compatible') or forecast.get('trade_eligible') is not True:return
  if time.time_ns()-at_ns>=10**9:return # do not backdate delayed processing
  nominal=at_ns//(60*10**9)*(60*10**9)
  cached=self.prefetch.get((p['city'],nominal),{})
  quotes=cached.get('quotes',{})
  if not quotes or cached.get('completed_ns',nominal+1)>nominal:return
  if any(q.get('received_ns',nominal+1)>nominal for q in quotes.values()):return
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
  orders=self.ledger.reserve(wid,day,result,p['daily_cap'],actual)
  with self.lock:self.feed[p['city']].setdefault('decisions',{})[p['profile_id']]={'result':result,'at_ns':actual}
  for order in orders:self.arrival_pool.submit(self.arrive,p,feed,forecast,order)
 def arrive(self,p,feed,forecast,order):
  # Waiting is bounded to five seconds; new REST timestamps are never replaced.
  due=order['arrival_due_ns'];self.stop.wait(max(0,(due-time.time_ns())/1e9))
  if self.stop.is_set():return # persistent pending reservation survives restart
  try:
   physical=self.provider.book(order['ticker']);quote=self.ledger.remaining_book(physical);at=time.time_ns()
   w=self.ledger.snapshot()['wallets'][p['wallet_id']]
   action='impossible_exit_arrival' if order.get('exit_policy')==POLICY_ID else 'arrival'
   result=self.call(p['city'],{'action':action,'profile_id':p['profile_id'],'day':feed['day'],
    'at_ns':at,'frozen_order':order,'quotes':{order['ticker']:quote},'quote':quote,'forecast':forecast,
    'policy_state':w['policy_state'],'state':w['policy_state'],'available_cash':str(self.ledger.free(w)),
    **self.native_inputs(p['city'],feed)})
   self.ledger.reconcile(p['wallet_id'],order['order_id'],result,physical,at)
  except Exception as error:
   self.ledger.reconcile(p['wallet_id'],order['order_id'],{'status':'UNKNOWN','reason':str(error)}, {},time.time_ns())
 def run(self):
  self.refresh()
  while not self.stop.wait(.2):
   wall=time.time();ns=time.time_ns()
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
    if feed.get('market') and not feed.get('weather',{}).get('retained_before_native_cutoff') and wall-self.last_weather.get(city,0)>=900:self.request_weather(city)
   if self.freeze.get('status')=='VERIFIED':
    utc=datetime.fromtimestamp(ns/1e9,timezone.utc)
    for city in CITIES:
     hour=14 if city in ('ny','miami') else 17
     publication=(city,utc.date().isoformat(),utc.hour,utc.minute)
     if utc.second==0 and utc.minute==0 and utc.hour==hour and publication not in self.publish_seen:
      with self.lock:feed=deepcopy(self.feed.get(city,{}))
      if feed.get('market'):
       self.publish_seen.add(publication);self.clock_pool.submit(self.publish_city,city,feed,ns)
    for city in CITIES:
     with self.lock:feed=deepcopy(self.feed.get(city,{}))
     nominal=(ns//(60*10**9)+1)*(60*10**9)
     if nominal-ns<=3*10**9 and feed.get('market',{}).get('settlement_target',{}).get('compatible'):
      key=(city,nominal)
      if key not in self.prefetch_seen and any(self.schedule_slot(p,feed.get('day'),nominal) for p in self.profiles if p['city']==city):
       self.prefetch_seen.add(key);self.prefetch_busy.add(key);self.clock_pool.submit(self.prefetch_city,city,feed,nominal)
    for p in self.profiles:
     with self.lock:feed=deepcopy(self.feed.get(p['city'],{}))
     day=feed.get('day');slot=(p['wallet_id'],day,ns//(60*10**9))
     raw=feed.get('forecasts',{}).get(p['profile_id'],{});eligible=raw.get('forecast',raw).get('trade_eligible') is True
     if day and eligible and slot not in self.clock_seen and self.schedule_slot(p,day,ns):
      self.clock_seen.add(slot)
      def work(profile=p,captured=feed,clock=ns):
       try:self.decide_profile(profile,captured,clock)
       except Exception as error:
        with self.lock:self.feed[profile['city']].setdefault('decisions',{})[profile['profile_id']]={'status':'BLOCKED','error':str(error),'at_ns':time.time_ns()}
      self.clock_pool.submit(work)
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
    'weather_field_count':len(f.get('weather',{}).get('fields',[])),
    'wallets':[w for w in wallets if w['city']==city]})
  return {'at_ns':time.time_ns(),'freeze':self.freeze,'checkpoint_month':'2026-10','paper_only':True,
   'actual_orders':0,'cities':cities,'wallets':wallets,'total_initial':'900',
   'total_cash':str(sum((D(w['cash']) for w in wallets),D('0'))),'version':self.release.manifest['release_id']}
