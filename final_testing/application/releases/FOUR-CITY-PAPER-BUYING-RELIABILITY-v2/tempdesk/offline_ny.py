"""October-only archival New York replay, separate from the accepted live UI.

Real archives are conditional evidence: original delivery, fills and NWS/TWC
equivalence are not proved. A missing day never becomes a zero-return trade day.
No model fits, credential access or exchange order transport exist here.
"""
from __future__ import annotations
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import types
import uuid

sys.dont_write_bytecode = True
D = Decimal; Z = D(0); ONE = D(1); NS = 10**9
BASE = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot')
CAMPAIGN = BASE/'Development/New York/NY-REALITY-SIM-v1-20261012'
SOURCE_RECEIPT = CAMPAIGN/'source-admission-ready-v2.json'
SOURCE_RECEIPT_SHA = '79018360d85b1fb80b03c24c7b963eb23064381cf84d1e0f57065ae411c38cae'
PROFILES = ('CONSERVATIVE_P09', 'SAFE5_TARGET_ONLY')
ASSUMPTIONS = [
    'Archived event-time reconstruction; original first delivery and forecast issuance are unproved.',
    'Five-second native event age and five-second fixed-quantity FOK arrival; no invented fills.',
    'Frozen whole-degree NWS physical-high probabilities; equivalence to TWC settlement remains unproved.',
    'Native historical fee precision remains an assumption, not authenticated account evidence.',
    'Official binary settlement timestamps proxy paper cash release; calendar cash is not eventual origin profit.',
    'Each profile is a separate hypothetical simulation; summed nine-wallet balances do not prove simultaneous executable fills.',
    'Within each profile, only an observed native quantity increase restores consumed depth; market impact is unmodeled.',
    'Day net_pnl is calendar cash movement, including invested principal; it is not the profit or loss of a trade.',
]


def digest(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def read(path): return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n', encoding='utf8')
    tmp.replace(path)


def pin(path): return {'path':str(Path(path).resolve()), 'sha256':digest(path)}


def dates(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if start != '2026-10-01' or b<a or (b-a).days>3660:
        raise ValueError('HISTORY_FROM_OCTOBER_FIRST_DAY_REQUIRED')
    return [(a+timedelta(days=n)).isoformat() for n in range((b-a).days+1)]


def frozen_worker(release_path):
    release = Path(release_path).resolve(); manifest = read(release/'manifest.json')
    if manifest.get('checkpoint_month') != '2026-10' or manifest.get('paper_only') is not True:
        raise ValueError('EXPLICIT_FROZEN_OCTOBER_PAPER_RELEASE_REQUIRED')
    for item in manifest['files']:
        path = (release/item['path']).resolve()
        if not path.is_relative_to(release) or digest(path)!=item['sha256']:
            raise ValueError('FROZEN_RELEASE_HASH_FAILED:'+item['path'])
    rawmap = read(release/'source-map.json'); mapping = rawmap.get('mapping', rawmap)
    normalized = {str(Path(k).resolve()).casefold():v for k,v in mapping.items()}
    path = release/manifest['workers']['ny']
    spec = importlib.util.spec_from_file_location('ny_offline_frozen_'+uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    # A bound resolver avoids process-wide environment changes between city jobs.
    def resolver(original):
        key = str(Path(original).resolve()).casefold()
        if key not in normalized: raise ValueError('FROZEN_SOURCE_MISSING:'+str(original))
        item = normalized[key]; p = Path(item['path'] if isinstance(item,dict) else item)
        if not p.is_absolute(): p = release/p
        if isinstance(item,dict) and digest(p)!=item['sha256']:
            raise ValueError('FROZEN_MAPPED_SOURCE_CHANGED')
        return p
    module.source_path = resolver
    return module, [pin(release/'manifest.json'), pin(release/'source-map.json'), pin(path)]


def source_requirements(day):
    return {'city':'ny','day':day,'series':'KXHIGHNY','station':'KNYC',
        'native_clock_utc':'14:00:00','decision_minutes':181,'arrival_delay_seconds':5,
        'required_cutoffs':362,'book_window_utc':[day+'T13:59:55Z',day+'T17:00:05Z'],
        'book_columns':['market_id','market_platform_id','platform','outcome','bids','asks',
            'timestamp','indexed_at','hash','state','continuity','path_index'],
        'weather':'Exact NY 44 NOAA native objects / 68 fields, KNYC grid, frozen October11-feature chain',
        'official_result':'Six dated raw Kalshi markets and finalized binary settlement clocks',
        'missing_evidence_is_no_trade':False}


def inventory(start_day, end_day, runtime_root):
    calendar = dates(start_day,end_day)
    if digest(SOURCE_RECEIPT)!=SOURCE_RECEIPT_SHA: raise ValueError('CHECKED_ARCHIVE_RECEIPT_CHANGED')
    entries = read(SOURCE_RECEIPT)['files']; result={}; requests=[]
    for day in calendar:
        if day[:7]!='2026-10':
            result[day]={'status':'PENDING','reason':'CHECKPOINT_NOT_REGISTERED','source_paths':[]}
            continue
        runtime = Path(runtime_root)/'offline/ny'/day/'admission.json'
        try:
            item = read(runtime)['files'] if runtime.is_file() else entries.get(day)
            if not item: raise FileNotFoundError('ARCHIVE_DAY_NOT_ACQUIRED')
            for key in ('forecast','books'):
                p = Path(item[key]['path'])
                if not p.is_file() or digest(p)!=item[key]['sha256']:
                    raise ValueError('ARCHIVE_INPUT_HASH_FAILED')
            result[day] = {'status':'READY','files':item,'source_paths':[item[k]['path'] for k in ('forecast','books')]}
        except Exception as error:
            result[day] = {'status':'PENDING' if day>=datetime.now(timezone.utc).date().isoformat() else 'UNKNOWN',
                'reason':type(error).__name__+':ARCHIVE_SOURCE_NOT_READY','source_paths':[]}
            requests.append(source_requirements(day))
    return {'days':result,'requests':requests,'source_pins':[pin(SOURCE_RECEIPT)]}


def _definitions(raw, names, env):
    tree = ast.parse(raw); chosen = [n for n in tree.body
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    if {n.name for n in chosen}!=set(names): raise ValueError('PINNED_DEFINITION_MISSING')
    exec(compile(ast.Module(body=chosen,type_ignores=[]),'<pinned offline NY definitions>','exec'),env)


class SharedCapacity:
    def __init__(self): self.rows={}; self.contention=0
    def view(self, ticker, book):
        seen={p:q for p,q in book['asks']}; previous={p for t,p in self.rows if t==ticker}
        for price in previous|set(seen):
            key=(ticker,price); row=self.rows.get(key)
            if row is None: row={'displayed':Z,'consumed':Z,'event_ns':-1}; self.rows[key]=row
            if book['event_ns']>row['event_ns']:
                current=seen.get(price,Z); increase=max(Z,current-row['displayed'])
                row.update(displayed=current,consumed=max(Z,row['consumed']-increase),event_ns=book['event_ns'])
        return [(p,max(Z,q-self.rows[(ticker,p)]['consumed'])) for p,q in book['asks']]
    def consume(self,ticker,fills):
        for f in fills:
            price=D(f['price']); quantity=D(f['quantity']); row=self.rows[(ticker,price)]
            if quantity>row['displayed']-row['consumed']: raise ValueError('SHARED_PHYSICAL_DEPTH_OVERFILL')
            row['consumed']+=quantity


def native_kernel(module, capacity):
    env={'D':D,'Z':Z,'ZERO':Z,'ONE':ONE,'NS':NS,'NY':module.NY,'math':math,
        'Decimal':D,'ROUND_CEILING':ROUND_CEILING,'ROUND_FLOOR':ROUND_FLOOR,
        'datetime':datetime,'timezone':timezone,'Path':Path,'json':json,'sha256':sha256,
        'Counter':Counter,'number':module.numeric,'require':module.require,
        'PENDING':{'PENDING','UNKNOWN_EXECUTION'}}
    strict_ast=ast.parse(module.source_bytes(module.STRICT/'ny_safe.py'))
    parent=next(n for n in strict_ast.body if isinstance(n,ast.ClassDef) and n.name=='OfflineTrader')
    helper=ast.ClassDef(name='Helpers',bases=[],keywords=[],decorator_list=[],body=[deepcopy(n)
        for n in parent.body if isinstance(n,ast.FunctionDef) and n.name in
        ('reserved','exposure','remaining_budget','risk','confirmed')])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[helper],type_ignores=[])),'<pinned NY risk>','exec'),env)
    _definitions(module.source_bytes(module.STRICT/'ny_safe.py'),('blank_wallet',),env)
    env['strict']=types.SimpleNamespace(SafetyError=module.Blocked,PENDING=env['PENDING'],
        OfflineTrader=env['Helpers'],blank_wallet=env['blank_wallet'],NY=module.NY)
    raw=module.source_bytes(module.RESEARCH/'ny_replay_kernel_v1.py')
    _definitions(raw,('canonical','nstime','iso','floor','fee','require','ident','admit','ceil_cent'),env)
    raw_admit=env['admit']; ns=module.weather_math()['ns']
    env['nstime']=lambda s:ns(s.replace(' ','T')+('' if s.endswith('Z') else 'Z'))
    def admit(packet,ticker,at,*,safety=False):
        book=raw_admit(packet,ticker,at,safety=safety)
        book['asks']=capacity.view(ticker,book)
        return book
    env['admit']=admit
    tree=ast.parse(raw); cls=deepcopy(next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Wallet'))
    class SeparateArrival(ast.NodeTransformer):
        def visit_Expr(self,node):
            if isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='arrive': return ast.Pass()
            return self.generic_visit(node)
    SeparateArrival().visit(next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='original'))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'<pinned NY native wallet>','exec'),env)
    class JointWallet(env['Wallet']):
        def arrive(self,order,packet,forecast):
            full_supported=False
            try:
                full=raw_admit(packet,order['ticker'],order['arrival_ns'],safety=self.name.startswith('SAFE'))
                if self.name.startswith('SAFE'):
                    need=order['quantity']
                    for price,size in full['asks']:
                        if price>D(order['limit']): break
                        need-=min(need,int((size*self.depth).to_integral_value(rounding=ROUND_FLOOR)))
                    full_supported=need==0
                else:
                    price,size=full['asks'][0]
                    full_supported=price<=D(order['limit']) and int((size*self.depth).to_integral_value(rounding=ROUND_FLOOR))>=order['quantity']
            except module.Blocked:
                pass
            super().arrive(order,packet,forecast)
            if order['status']=='PAPER_ASSUMED_FILL': capacity.consume(order['ticker'],order['fills'])
            elif full_supported and order['status']=='ASSUMED_TERMINAL_NO_FILL' and 'DEPTH' in order.get('reason',''): capacity.contention+=1
    return JointWallet, env


def acquire_weather(day, release_path, runtime_root, progress=None):
    """Actual bounded NOAA archive acquisition; historical availability is a proxy."""
    if day[:7]!='2026-10': raise ValueError('EXPLICIT_OCTOBER_CHECKPOINT_REQUIRED')
    module,pins=frozen_worker(release_path); out=Path(runtime_root)/'offline/ny'/day
    cached=out/'weather-archive.json'
    if cached.is_file():
        data=read(cached)
        if data.get('complete_native_archive') is True and all(Path(p['path']).is_file() and digest(p['path'])==p['sha256'] for p in data.get('source_pins',[])):
            return data
    folder=out/('weather-capture-'+uuid.uuid4().hex); folder.mkdir(parents=True,exist_ok=False)
    loaded={}; prefix='_offline_ny_'+uuid.uuid4().hex+'_'
    for name in ('shared_weather_v1','shared_weather_v2','collect_nyc_weather_v1','extract_nyc_points_v2'):
        p=module.source_path(module.WEATHER/'vendor'/(name+'.py')); obj=types.ModuleType(prefix+name)
        obj.__file__=str(p); sys.modules[prefix+name]=obj
        tree=ast.parse(p.read_bytes()); tree.body=[n for n in tree.body if not isinstance(n,ast.If)]
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module in loaded: node.module=prefix+node.module
        exec(compile(tree,str(p),'exec'),obj.__dict__); loaded[name]=obj
    utils=loaded['shared_weather_v2']; collector=loaded['collect_nyc_weather_v1']; extractor=loaded['extract_nyc_points_v2']
    collector.DATA=folder; extractor.DATA=folder
    env={'datetime':datetime,'timedelta':timedelta,'UTC':timezone.utc,'require':module.require,
        'object_key':utils.object_key,'object_url':utils.object_url,'BROAD':utils.BROAD}
    _definitions(module.source_bytes(module.WEATHER/'weather.py'),('source_plan',),env)
    caps={'network_bytes':256*1024**2,'elapsed_seconds':480,'maximum_requests':160,
        'index_bytes_per_object':2*1024**2,'hrrr_field_bytes':8*1024**2,'gefs_field_bytes':4*1024**2}
    plan={'date':day,'objects':env['source_plan'](day),'caps':caps,'historical_original_delivery_proven':False}
    save(folder/'plan.json',plan); plan['_file_sha256']=digest(folder/'plan.json')
    budget=collector.Budget(folder/'network-budget.jsonl',caps,plan['_file_sha256']); results=[]; errors=[]
    if progress: progress({'city':'ny','day':day,'phase':'NOAA_ARCHIVE_DOWNLOAD'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(collector.collect_object,o,'offline-history',plan,budget,{}):o for o in plan['objects']}
        for future in as_completed(jobs):
            try: results.append(future.result())
            except Exception as error: errors.append({'object_id':jobs[future]['object_id'],'reason':type(error).__name__})
    registry=module.source_path(module.WEATHER/'vendor/city-source-profiles-v1.json')
    geometry=module.source_path(module.WEATHER/'vendor/city-grid-geometry-v1.json')
    extractor.init_worker(str(registry),digest(registry),str(geometry),digest(geometry))
    fields=[]; ns=module.weather_math()['ns']
    for result in results:
        errors.extend(result['errors'])
        for native in result['fields']:
            try:
                point_binding=extractor.extract(native); point=read(point_binding['path'])['points'][0]
                if point['station_id']!='KNYC' or point['is_missing']: raise ValueError('MISSING_KNYC_NATIVE_POINT')
                fields.append({'object_id':native['object_id'],'field_id':native['field_id'],'model':native['model'],
                    'member_id':native['member_id'],'lead_hours':native['lead_hours'],
                    'issue_ns':ns(native['native_metadata']['nominal_issue_utc']),
                    'valid_ns':ns(native['native_metadata']['valid_time_utc']),
                    'available_ns':ns(native['information_available_utc']),
                    **{k:point[k] for k in ('value','units','raw_value','raw_units','is_missing')},
                    'source_ref':{'native_field':native,'point_packet':point_binding,
                        'actual_archive_received_utc':native['original_retrieved_at_utc'],
                        'delivery_basis':'ARCHIVAL_LAST_MODIFIED_PLUS_INIT_6H_PROXY'}})
                pins.extend([pin(native['source_path']),pin(native['receipt_path']),pin(point_binding['path'])])
            except Exception as error: errors.append({'object_id':native['object_id'],'field_id':native['field_id'],'reason':type(error).__name__})
    complete=len(fields)==68 and not errors
    frame=module.native_frame(day,{'fields':fields}) if complete else None
    if frame:
        try: module.weather_math()['validate_frame'](frame)
        except Exception as error: complete=False; errors.append({'reason':type(error).__name__+':ARCHIVAL_AVAILABILITY_OR_FOOTPRINT'})
    data={'day':day,'complete_native_archive':complete,'frame':frame if complete else None,
        'fields':fields,'source_pins':pins,'errors':errors,'network_budget':budget.snapshot(),
        'historical_original_delivery_proven':False,'live_weather_ready':False,'fits':0}
    save(cached,data); return data


def normalize_raw_books(day, path, contracts, module):
    """Latest event, then path, then per-side indexed revision; quality comes later."""
    ns=module.weather_math()['ns']; tickers={c['ticker'] for c in contracts}; groups={t:{} for t in tickers}
    count=0
    with Path(path).open(encoding='utf8') as handle:
        for line in handle:
            if not line.strip(): continue
            count+=1
            if count>500000: raise ValueError('ARCHIVE_ROW_CEILING_INCOMPLETE')
            row=json.loads(line); ticker=row['market_platform_id']
            if ticker not in tickers: raise ValueError('FOREIGN_ARCHIVE_TICKER')
            stamp=ns(row['timestamp'].replace(' ','T')+('' if row['timestamp'].endswith('Z') else 'Z'))
            indexed=ns(row['indexed_at'].replace(' ','T')+('' if row['indexed_at'].endswith('Z') else 'Z'))
            key=(stamp,row['path_index']); side=row['outcome']['platform_id'].lower()
            group=groups[ticker].setdefault(key,{})
            if side not in group or indexed>group[side][0]: group[side]=(indexed,[row])
            elif indexed==group[side][0]: group[side][1].append(row)
    anchor=module.clock(day); samples={}; source=pin(path)
    for minute in range(181):
        for offset in (0,5):
            at=anchor+(minute*60+offset)*NS; samples[str(at)]={}
            for ticker in sorted(tickers):
                keys=[k for k in groups[ticker] if k[0]<=at]
                rows=[]
                if keys:
                    latest=max(keys); rows=[r for side in sorted(groups[ticker][latest]) for r in groups[ticker][latest][side][1]]
                samples[str(at)][ticker]={'source_rows':rows,'source_file':source,
                    'original_delivery_proven':False,'latest_archived_revision_used':True}
    return {'date':day,'samples':samples,'full_entry_window_source_captured':True,
        'original_first_delivery_proven':False,'raw_rows':count,'source_refs':[source]}


def normalize_acquired_day(day, acquired, weather, release_path, runtime_root):
    module,_=frozen_worker(release_path)
    if not acquired.get('books_complete') or not acquired.get('market_path') or not weather.get('complete_native_archive'):
        raise ValueError('INCOMPLETE_GENUINE_ARCHIVE_DAY')
    marketpath=Path(acquired['market_path']); markets=read(marketpath)['markets']
    rows=[]; settlements={}; ns=module.weather_math()['ns']
    for m in sorted(markets,key=lambda m:float('-inf') if m.get('floor_strike') is None else float(m['floor_strike'])):
        kind=m['strike_type']; rows.append({'ticker':m['ticker'],'lower':m.get('floor_strike'),
            'upper':m.get('cap_strike'),'lower_inclusive':kind in ('between','greater_or_equal'),
            'upper_inclusive':kind in ('between','less_or_equal'),'ordinal_index':len(rows)})
        proven=m.get('status') in ('finalized','settled') and m.get('result') in ('yes','no') and bool(m.get('settlement_ts'))
        proven=proven and D(str(m.get('settlement_value_dollars','-1')))==int(m.get('result')=='yes')
        settlements[m['ticker']]={'official_binary_result_proven_from_current_public_metadata':proven,
            'yes_result':int(m['result']=='yes') if proven else None,
            'settlement_ns':ns(m['settlement_ts']) if proven else None,'source':pin(marketpath),
            'actual_account_cash_receipt_proven':False}
    contracts=module.contract_partition(rows,day)
    # Frozen helper outputs inclusive physical integer bounds, retained as such.
    forecast=module.forecast({'day':day,'at_ns':module.clock(day),'contracts':contracts,
        'frame':weather['frame'],'historical_parity':True})['forecast']
    forecast['settlements']=settlements
    books=normalize_raw_books(day,acquired['books_path'],contracts,module)
    out=Path(runtime_root)/'offline/ny'/day; save(out/'forecast.json',forecast); save(out/'books.json',books)
    files={'forecast':pin(out/'forecast.json'),'books':pin(out/'books.json')}
    save(out/'admission.json',{'schema':'NY_OFFLINE_ARCHIVAL_DAY_V1','day':day,'files':files,
        'source_pins':weather['source_pins']+[pin(marketpath),pin(acquired['books_path'])],
        'fits':0,'live_ready':False,'assumptions':ASSUMPTIONS})
    return files


def _episode_stats(wallet, day, forecast, asof):
    orders=[o for o in wallet.orders if o['date']==day and o['status']=='PAPER_ASSUMED_FILL']
    totals={}
    for o in orders:
        t=o['ticker']; quantity,basis=totals.get(t,(0,Z)); totals[t]=(quantity+o['quantity'],basis+D(o['amount']))
    nets=[]; unsettled=False
    for ticker,(quantity,basis) in totals.items():
        r=forecast['settlements'].get(ticker,{})
        if not r.get('official_binary_result_proven_from_current_public_metadata') or r['settlement_ns']>asof:
            unsettled=True; continue
        nets.append(D(quantity*(1-r['yes_result']))-basis)
    return {'wins':sum(v>Z for v in nets),'losses':sum(v<Z for v in nets),'closed_episode_count':len(nets),
        'contracts':sum(o['quantity'] for o in orders),'fees':str(sum((D(o['fees']) for o in orders),Z)),
        'eventual_origin_net':None if unsettled else str(sum(nets,Z)),'unsettled_origin':unsettled}


def replay(start_day, end_day, release_path, runtime_root, progress=None):
    calendar=dates(start_day,end_day); runtime=Path(runtime_root).resolve(); asof=time.time_ns()
    module,source_pins=frozen_worker(release_path); source_pins.append(pin(Path(__file__)))
    found=inventory(start_day,end_day,runtime)
    source_pins+=found['source_pins']; requests=[]
    # Acquire missing evidence through the shared, credential-free worker API.
    for day in calendar:
        if found['days'][day]['status']=='READY': continue
        if day[:7]!='2026-10': continue
        if progress: progress({'city':'ny','day':day,'phase':'ACQUIRE_MISSING_ARCHIVE'})
        acquired={}; weather={}
        try:
            try:
                from .offline_sources import acquire_day
            except ImportError:
                from tempdesk.offline_sources import acquire_day
            acquired=acquire_day('ny',day,runtime)
            if not acquired.get('books_complete'): raise ValueError('BOOK_ARCHIVE_NOT_COMPLETE')
            weather=acquire_weather(day,release_path,runtime,progress)
            files=normalize_acquired_day(day,acquired,weather,release_path,runtime)
            found['days'][day]={'status':'READY','files':files,'source_paths':[files[k]['path'] for k in ('forecast','books')]}
        except Exception as error:
            if 'CONNECTION_REQUIRED' in acquired.get('errors',[]) or str(error)=='CONNECTION_REQUIRED':
                reason='ARCHIVE_CONNECTION_REQUIRED'
            elif acquired and not acquired.get('books_complete'):
                reason='BOOK_ARCHIVE_INCOMPLETE'
            elif weather and not weather.get('complete_native_archive'):
                reason='NOAA_WEATHER_ARCHIVE_INCOMPLETE'
            else:
                reason=type(error).__name__+':ARCHIVE_SOURCE_REJECTED'
            found['days'][day]['reason']=reason
            requests.append(source_requirements(day))
    forecasts={}
    for day,row in found['days'].items():
        if row['status']=='READY':
            forecast=read(row['files']['forecast']['path']); saved=module.frozen_model().load(day[:7])
            if forecast['date']!=day or forecast.get('state_self_sha256',forecast.get('fitted_state_self_sha256'))!=saved['state']['self_sha256']:
                raise ValueError('ARCHIVE_FORECAST_MONTH_STATE_MISMATCH')
            if forecast['forecast_event_ns']!=module.clock(day): raise ValueError('ARCHIVE_FORECAST_CLOCK_MISMATCH')
            forecasts[day]=forecast; source_pins.extend([row['files'][k] for k in ('forecast','books')])
    capacities={p:SharedCapacity() for p in PROFILES}; kernels={p:native_kernel(module,capacities[p]) for p in PROFILES}
    cfg=module.read_source(module.RESEARCH/'policy-registry-v1.json')['profiles']['SAFE5_TARGET_ONLY']
    model=module.frozen_model(); wallets={p:kernels[p][0](p,cfg,model,'.50' if p.startswith('SAFE') else '1') for p in PROFILES}
    pending_statuses={'PENDING','UNKNOWN_EXECUTION'}
    daily={p:[] for p in PROFILES}; gap=False; checkpoint_pending=False
    origin_prefix={p:Z for p in PROFILES}; origin_valid={p:True for p in PROFILES}
    previous_calendar_cash={p:D('100') for p in PROFILES}
    started=time.monotonic()
    for day in calendar:
        if time.monotonic()-started>600: raise TimeoutError('BOUNDED_NY_OFFLINE_REPLAY_DEADLINE')
        row=found['days'][day]; at=module.clock(day); close=min(module.clock(day,23,59)+59*NS,asof)
        for wallet in wallets.values(): wallet.settle_due(min(at,asof),forecasts)
        before={p:len(w.orders) for p,w in wallets.items()}
        missing=row['status']!='READY'
        if day[:7]!='2026-10': checkpoint_pending=True
        elif missing: gap=True
        if not gap and not checkpoint_pending and at<=asof:
            f=forecasts[day]; books=read(row['files']['books']['path'])
            if books['date']!=day or books.get('full_entry_window_source_captured') is not True:
                gap=True; missing=True; row['reason']='NATIVE_BOOK_WINDOW_UNPROVEN'; requests.append(source_requirements(day))
            else:
                samples=books['samples']
                required={str(at+(minute*60+offset)*NS) for minute in range(181) for offset in (0,5)}
                if not required.issubset(samples):
                    gap=True; missing=True; row['reason']='EXACT_CUTOFF_ENVELOPES_MISSING'; requests.append(source_requirements(day))
                else:
                    for minute in range(181):
                        clock=at+minute*60*NS
                        if clock>asof: break
                        queued=[]
                        for profile,w in wallets.items():
                            w.settle_due(clock,forecasts); count=len(w.orders)
                            if profile.endswith('P09'):
                                if minute==0: w.original(f,samples)
                            else:
                                prior=[o for o in w.orders if o['date']==day]
                                if not prior or not any(o['stage']==2 for o in prior) and any(o['status']=='PAPER_ASSUMED_FILL' for o in prior):
                                    w.safe_decide(f,samples.get(str(clock),{}),clock,2 if prior else 1)
                            queued.extend((profile,o) for o in w.orders[count:])
                        for profile,o in queued:
                            if o['arrival_ns']<=asof:
                                wallets[profile].arrive(o,samples.get(str(o['arrival_ns']),{}).get(o['ticker']),f)
                    del books
        for profile,w in wallets.items():
            w.settle_due(close,forecasts)
            stats=_episode_stats(w,day,forecasts[day],asof) if day in forecasts else {
                'wins':0,'losses':0,'contracts':0,'fees':'0','eventual_origin_net':None,'unsettled_origin':True,'closed_episode_count':0}
            unknown=any(o['status'] in pending_statuses for o in w.orders)
            source_unknown=gap; pending=checkpoint_pending or at>asof or stats['unsettled_origin']
            status='UNKNOWN' if source_unknown or unknown else 'PENDING' if pending else 'COMPLETE'
            reason=row.get('reason') if missing else 'EARLIER_SOURCE_GAP' if gap else 'UNKNOWN_EXECUTION_RESERVATION' if unknown else 'OFFICIAL_OR_NATIVE_CLOCK_PENDING' if pending else 'ARCHIVAL_CONDITIONAL_SIMULATION'
            if stats['eventual_origin_net'] is None or source_unknown or unknown or checkpoint_pending: origin_valid[profile]=False
            elif origin_valid[profile]: origin_prefix[profile]+=D(stats['eventual_origin_net'])
            closed_net=stats['eventual_origin_net'] if stats['closed_episode_count'] and not unknown and not stats['unsettled_origin'] else None
            daily[profile].append({'day':day,'status':status,'reason':reason,
                'ending_cash':w.s['cash'] if status=='COMPLETE' else None,'known_cash':w.s['cash'],
                'net_pnl':str(D(w.s['cash'])-previous_calendar_cash[profile]) if status=='COMPLETE' else None,
                'net_pnl_semantics':'DAILY_CALENDAR_CASH_MOVEMENT_INCLUDES_INVESTED_PRINCIPAL',
                'known_eventual_origin_net':closed_net,
                'closed_episode_count':stats['closed_episode_count'],
                **{k:stats[k] for k in ('wins','losses','contracts','fees','eventual_origin_net')},
                'eventual_origin_settled_cash':str(D('100')+origin_prefix[profile]) if origin_valid[profile] else None,
                'reserved':str(w.reserved(w.s)),'open_cost_basis':str(sum((D(p['basis']) for p in w.s['positions'].values()),Z)),
                'source_paths':row['source_paths'],'assumptions':ASSUMPTIONS,
                'known_cash_asof_ns':close,'known_cash_asof_day':datetime.fromtimestamp(close/NS,module.NY).date().isoformat(),
                'native_calendar_cutoff_ns':module.clock(day,23,59)+59*NS,'native_entry_clock_ns':at,
                'cash_cutoff_semantics':'NEW_YORK_CIVIL_DAY_END_CAPPED_AT_REPORT_ASOF',
                'conditional_results_not_live_validation':True})
            previous_calendar_cash[profile]=D(w.s['cash'])
        if progress: progress({'city':'ny','day':day,'phase':'REPLAYED','source_status':row['status']})
    profiles=[]
    for profile,w in wallets.items():
        w.settle_due(asof,forecasts)
        unknown=gap or any(o['status'] in pending_statuses for o in w.orders)
        pending=checkpoint_pending or any(p['quantity'] for p in w.s['positions'].values()) or module.clock(end_day)>asof
        complete=not unknown and not pending
        profiles.append({'profile_id':profile,'wallet_id':'ny:'+profile,'days':daily[profile],
            'ending_cash':w.s['cash'] if complete else None,'known_cash':w.s['cash'],
            'net_pnl':str(D(w.s['cash'])-D('100')) if complete else None,
            'wins':sum(d['wins'] for d in daily[profile]),'losses':sum(d['losses'] for d in daily[profile]),
            'contracts':sum(d['contracts'] for d in daily[profile]),'fees':str(sum((D(d['fees']) for d in daily[profile]),Z)),
            'reserved':str(w.reserved(w.s)),'assumptions':ASSUMPTIONS,
            'known_cash_asof_ns':asof,'known_cash_asof_day':datetime.fromtimestamp(asof/NS,module.NY).date().isoformat(),
            'complete_through_day':next((d['day'] for d in reversed(daily[profile]) if d['status']=='COMPLETE'),None),
            'status':'UNKNOWN' if unknown else 'PENDING' if pending else 'COMPLETE',
            'native_state':w.s,'orders':w.orders,'cash_trace':w.trace})
    pins=list({p['path']:p for p in source_pins}.values())
    return {'city':'ny','start_day':start_day,'end_day':end_day,'asof_ns':asof,
        'profiles':profiles,'requests':requests,'source_pins':pins,'assumptions':ASSUMPTIONS,
        'shared_capacity':{'scope':'SEPARATE_PROFILE_HYPOTHESES','simultaneous_nine_wallet_fills_proven':False,
            'contention_events':sum(c.contention for c in capacities.values()),
            'per_profile_reused_depth_no_fill':{p:c.contention for p,c in capacities.items()}},
        'new_fits':0,'actual_orders':0,'live_ready':False,'elapsed_seconds':time.monotonic()-started}
