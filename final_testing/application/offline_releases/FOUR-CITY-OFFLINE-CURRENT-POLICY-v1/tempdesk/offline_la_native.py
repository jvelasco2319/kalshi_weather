"""Isolated LA historical accounting bridge; no live/demo mutation or fitting.

Native F3/F6 kernels and the V8D legacy basket kernel remain distinct. Archive
delivery/target/account assumptions are explicit, never production eligibility.
Import and preparation do not execute any financial decisions.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode=True
import ast,hashlib,importlib,importlib.util,json,threading,time
from urllib.parse import urlparse
from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from .parity_la_exit import current_exit, recorded_packets, protection_scope, verify_completed_report_window
NS=10**9
PROFILES=('F3_V10_PHYSICAL_FORECAST_EXIT','F6_HYBRID_STAGED5','V8D_NO18_SETTLEMENT')
_LOCK=threading.RLock()

class PendingSource(ValueError):
    def __init__(self,reason,**details):super().__init__(reason);self.reason=reason;self.details=details
EvidencePending=PendingSource
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'),parse_float=str)
def pin(path):
    p=Path(path).resolve();before=p.stat();h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(2*1024*1024),b''):h.update(chunk)
    after=p.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during pin')
    return dict(path=str(p),sha256=h.hexdigest(),bytes=after.st_size)
def module(name,path):
    if name in sys.modules:
        prior=sys.modules[name]
        if Path(prior.__file__).resolve()!=Path(path).resolve():raise ValueError('Historical runtime alias is occupied by another source')
        return prior
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def iso(value):
    seconds,fraction=divmod(int(value),NS)
    return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{fraction:09}Z'
def source_pins_for_review(la_root):
    """Inert original closure, independently pinned before any runtime import."""
    root=Path(la_root).resolve()
    configs={p:root/'runs/la-three-bot-oct11-20261012/modern'/(p+'.json') for p in PROFILES[:2]}
    files=[root/'runs/la-all-model-simulation-20261011/market/original-policy-adapter-v1.py',root/'runs/la-three-bot-oct11-20261012/market/oct11_sources_v1.py',root/'src/la_reality_sim/market.py',root/'src/la_reality_sim/weather.py',root/'src/la_reality_sim/legacy_adapter.py',*configs.values(),root/'experiments/LA-F3-OCT9-20261010/trader/f3_execution.py']
    for base in ('experiments/LA-F2-MAY-OCT9-20261010/trader','experiments/LA-F4-COLONIES-20261010-2H/trading'):
        files.extend(root/base/'fixed_kernel'/(k+'.py') for k in ('engine','native_books','replay'))
    files.extend(root/'experiments/LA-F4-COLONIES-20261010-2H/trading'/k for k in ('research.py','f3_execution.py','f4_execution.py','retained_transition_v4.py'))
    files.extend(root/'work/september-simulation-preparation-20261005/execution'/k for k in ('engine.py','policies.py','policies_v2.py','policies_v3.py'))
    files.append(root/'work/entry-exit-matrix-20261006/composed_policy_v1.py')
    return [pin(p) for p in files]
def load_context(la_root):
    root=Path(la_root).resolve();refs=source_pins_for_review(root);sys.path.insert(0,str(root/'src'))
    from la_reality_sim.market import load_native_runtime,load_market_episode
    from la_reality_sim.legacy_adapter import load_legacy_runtime,make_common_case,point_signal,LegacyArchivedBookProvider,common_parameters
    discovery=load_native_runtime(root);bridge=module('la_offline_original_bridge',root/'runs/la-all-model-simulation-20261011/market/original-policy-adapter-v1.py')
    strict=bridge.load_f2_kernel();retained=dict(engine=discovery['engine'],native_books=discovery['native'],replay=importlib.import_module('fixed_kernel.replay'))
    exact11=module('la_offline_exact11',root/'runs/la-three-bot-oct11-20261012/market/oct11_sources_v1.py')
    configs={p:root/'runs/la-three-bot-oct11-20261012/modern'/(p+'.json') for p in PROFILES[:2]}
    legacy=load_legacy_runtime(root)
    return dict(la_root=root,discovery=discovery,bridge=bridge,strict=strict,retained=retained,legacy=legacy,exact11=exact11,configs=configs,
        load_market_episode=load_market_episode,make_common_case=make_common_case,point_signal=point_signal,legacy_provider=LegacyArchivedBookProvider,
        common_parameters=common_parameters,code_pins=refs,source_cache={},actual_history_asof_ns=time.time_ns())

def _old_source(day,context):
    if day in context['source_cache']:return deepcopy(context['source_cache'][day])
    if '2026-10-01'<=day<='2026-10-10':source=context['load_market_episode'](day,context['la_root'],runtime=context['discovery'])
    elif day=='2026-10-11':source=context['exact11'].load_source(runtime=context['discovery'])
    else:raise PendingSource('HISTORICAL_BOOK_ARCHIVE_MISSING',day=day)
    context['source_cache'][day]=source;return deepcopy(source)
def invalidate_source(day,context):
    context['source_cache'].pop(day,None)
    context.setdefault('coverage_cache',{}).pop(day,None)
    context.setdefault('runtime_archive_cache',{}).pop(day,None)
def _folder(day,runtime_root):return Path(runtime_root)/'sources/la'/day
def _captured(path,receipt_path):
    receipt=read(receipt_path);immutable=receipt.get('immutable_path')
    if immutable:
        candidate=Path(immutable).resolve()
        if not candidate.is_relative_to(Path(path).resolve().parent/'captures'):raise ValueError('Captured source escaped its dated immutable directory')
        path=candidate
    actual=pin(path)
    if actual['sha256']!=receipt['sha256'] or actual['bytes']!=receipt['bytes']:raise ValueError('Downloaded raw source/receipt mismatch')
    for k in ('started_ns','received_ns'):
        if isinstance(receipt.get(k),bool) or not isinstance(receipt.get(k),int):raise ValueError('Actual source receipt clock required')
    if not 0<receipt['started_ns']<=receipt['received_ns']<=time.time_ns():raise ValueError('Noncausal source receipt clock')
    return read(path),receipt,actual
def _official(day,context,runtime_root,source=None):
    folder=_folder(day,runtime_root);path=folder/'official-event.json';rp=folder/'official-event-receipt.json'
    if not path.is_file() or not rp.is_file():return None
    value,receipt,ref=_captured(path,rp);markets=value.get('markets') or value.get('event',{}).get('markets',[]);expected='KXHIGHLAX-'+datetime.fromisoformat(day).strftime('%y%b%d').upper()
    if len(markets)!=6 or len({m.get('ticker') for m in markets})!=6 or {m.get('event_ticker') for m in markets}!={expected} or any(not m.get('ticker','').startswith(expected+'-') for m in markets):raise ValueError('Fresh official six-market identity mismatch')
    if receipt.get('source_url')!='https://external-api.kalshi.com/trade-api/v2/events/'+expected:raise ValueError('Official source endpoint mismatch')
    windows=[];times={};receipts=[]
    for m in markets:
        lo,hi=m.get('floor_strike'),m.get('cap_strike')
        if m['strike_type']=='less':hi=Decimal(str(hi))-1
        if m['strike_type']=='greater':lo=Decimal(str(lo))+1
        if 'Los Angeles' not in m.get('rules_primary','') or 'Weather Company' not in m.get('rules_primary',''):raise ValueError('Official dated station/provider rule mismatch')
        created=context['strict']['engine'].clock(m['created_time'])
        if created>receipt['received_ns']:raise ValueError('Future official definition clock')
        w=dict(ticker=m['ticker'],event_ticker=expected,climate_date=day,station='KLAX',settlement_authority='The Weather Company',lower_bound_f=lo,upper_bound_f=hi,rules_primary=m['rules_primary'],source_path=ref['path'],source_sha256=ref['sha256'],available_ns=created,definition_availability_basis='PUBLIC_CREATED_TIME_ARCHIVAL_PROXY_NOT_ACTUAL_HISTORICAL_DELIVERY')
        windows.append(w);times[m['ticker']]=dict(open_time=m['open_time'],close_time=m['close_time'])
        if m.get('status') in ('finalized','settled') and m.get('result') in ('yes','no') and m.get('settlement_ts'):
            if context['strict']['engine'].clock(m['settlement_ts'])>receipt['received_ns']:raise ValueError('Future public settlement timestamp')
            receipts.append(dict(ticker=m['ticker'],yes_result=m['result']=='yes',official=True,available_utc=m['settlement_ts'],source_ref=ref['path']+'#sha256='+ref['sha256'],captured_received_ns=receipt['received_ns'],availability_basis='OFFICIAL_SETTLEMENT_TS_ARCHIVAL_PUBLIC_AVAILABILITY_PROXY_NOT_PRIVATE_PAYOUT_RECEIPT'))
    if len(receipts)==6 and sum(r['yes_result'] for r in receipts)!=1:raise ValueError('Official final mutually exclusive brackets require exactly one YES')
    if source:
        old={w['ticker']:w for w in source['windows']}
        if set(old)!={w['ticker'] for w in windows}:raise ValueError('Fresh official ticker universe differs archived source')
        for w in windows:
            prior=old[w['ticker']]
            for key in ('lower_bound_f','upper_bound_f'):
                a,b=w[key],prior.get(key)
                if (None if a is None else Decimal(str(a)))!=(None if b is None else Decimal(str(b))):raise ValueError('Fresh official strikes differ archived source')
        old_receipts={r['ticker']:r for r in source.get('receipts',[])}
        for r in receipts:
            if r['ticker'] in old_receipts and r['yes_result']!=old_receipts[r['ticker']]['yes_result']:raise ValueError('Fresh official result conflicts accepted original binary')
    actual_rp=Path(receipt['immutable_path']).parent/'official-event-receipt.json' if receipt.get('immutable_path') else rp
    return dict(windows=windows,times=times,receipts=receipts,official_final=len(receipts)==6,pins=[ref,pin(actual_rp)],capture_received_ns=receipt['received_ns'])

def _runtime_archive(day,context,runtime_root):
    """Admit actual raw capture/completeness metadata, not price freshness."""
    folder=_folder(day,runtime_root);alias=folder/'native-books.jsonl';rp=folder/'native-books-receipt.json';st=folder/'source-status.json'
    result=dict(complete=False,present=alias.is_file() and rp.is_file(),reason='RICHER_RUNTIME_ARCHIVE_MISSING',force_acquire=False)
    if not result['present'] or not st.is_file():return result
    status=read(st);result['force_acquire']=bool(status.get('books_complete'))
    try:
        receipt=read(rp);path=Path(receipt.get('immutable_path',alias)).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Raw archive path escaped dated source directory')
        immutable_receipt=path.parent/'native-books-receipt.json'
        if immutable_receipt.is_file():rp=immutable_receipt;receipt=read(rp)
        cache=context.setdefault('runtime_archive_cache',{});key=(str(path),path.stat().st_size,path.stat().st_mtime_ns,pin(rp)['sha256'],pin(st)['sha256'])
        if cache.get(day,{}).get('key')==key:return deepcopy(cache[day]['value'])
        ref=pin(path);clock=context['strict']['native_books'];end=clock.clock(day+'T20:00:05Z')
        if ref['sha256']!=receipt.get('sha256') or ref['bytes']!=receipt.get('bytes'):raise ValueError('Raw archive capture/receipt hash or bytes differ')
        if receipt.get('source')!='PROBALYTICS_NATIVE_ARCHIVE' or receipt.get('format')!='jsonl' or receipt.get('actual_historical_delivery_proven') is not False:raise ValueError('Authentic native archive receipt schema required')
        if isinstance(receipt.get('rows'),bool) or not isinstance(receipt.get('rows'),int) or not 0<receipt['rows']<=500000:raise ValueError('Raw archive bounded row count missing/exceeded')
        if not all(isinstance(receipt.get(k),int) and not isinstance(receipt.get(k),bool) for k in ('started_ns','received_ns')) or not 0<receipt['started_ns']<=receipt['received_ns']<=time.time_ns():raise ValueError('Actual archive query clocks required')
        if receipt['received_ns']<end or clock.native_clock(receipt['source_start_utc'])>clock.clock(day+'T16:29:55Z') or clock.native_clock(receipt['source_end_utc'])<end:raise ValueError('Registered historical decision/arrival window incompletely captured')
        result=dict(complete=True,present=True,path=path,receipt_path=rp,receipt=receipt,pins=[ref,pin(rp)],reason=None,force_acquire=False,latest_attempt_errors=status.get('errors',[]),qualification='FULL_RAW_QUERY_CAPTURE_FOR_REGISTERED_WINDOW; does not prove an active feed, heartbeat or fresh quote')
        cache[day]=dict(key=key,value=result);return deepcopy(result)
    except (OSError,ValueError,KeyError,TypeError) as error:
        result['reason']='RICHER_RUNTIME_ARCHIVE_UNADMITTED: '+str(error);return result

def _selected_export_coverage(day,source,context):
    """Cheap metadata-only gap scan of exact selected JSON exports.

    This proves selected age/missing-grid limitations; it never infers complete
    raw-day coverage or provider inactivity from snapshot absence.
    """
    cache=context.setdefault('coverage_cache',{})
    if day in cache:return deepcopy(cache[day])
    paths=[Path(p) for p in source['paths']];result=dict(needs_richer_archive=False,status='RAW_SOURCE_OR_UNMEASURED_COVERAGE',gap_clocks_ns=[])
    if paths and all(p.suffix=='.json' for p in paths):
        selected={}
        for p in paths:
            packet=read(p);rows=packet.get('data',[]) if isinstance(packet,dict) else packet
            for row in rows:
                if isinstance(row,list) and len(row)>=13:selected.setdefault(int(row[0]),[]).append(row)
        start=context['strict']['engine'].clock(day+'T17:00:00Z');ticks={w['ticker'] for w in source['windows']};gaps=[]
        for at in (start+i*60*NS for i in range(181)):
            rows=selected.get(at,[]);pairs={(r[2],str(r[4][1]).lower()) for r in rows}
            missing=not {(t,s) for t in ticks for s in ('yes','no')}<=pairs
            all_stale=bool(rows) and all(at-int(r[7])>60*NS for r in rows)
            if missing or all_stale:gaps.append(dict(at_ns=at,reason='MISSING_SELECTED_PAIR_GRID' if missing else 'ALL_SELECTED_NATIVE_PAIRS_OLDER_THAN_UNCHANGED60S'))
        result=dict(needs_richer_archive=bool(gaps),status='EXACT_SELECTED_EXPORT_WITH_OBSERVED_SUPPORT_GAPS' if gaps else 'EXACT_SELECTED_EXPORT_GRID_WITHOUT_ALL_PAIR_AGE_GAPS',gap_clocks_ns=[x['at_ns'] for x in gaps],gaps=gaps,private_feed_inactivity_proven=False,raw_day_completeness_proven=False)
    cache[day]=result;return deepcopy(result)

def source_status(day,context,runtime_root):
    try:old=_old_source(day,context);present=all(Path(p).is_file() for p in old['paths']);final=len(old.get('receipts',[]))==6;paths=[str(p) for p in old['paths']]
    except (PendingSource,FileNotFoundError):old=None;present=final=False;paths=[]
    fresh=_official(day,context,runtime_root,old)
    if fresh:final=final or fresh['official_final']
    richer=_runtime_archive(day,context,runtime_root);coverage=_selected_export_coverage(day,old,context) if old and present else dict(needs_richer_archive=not richer['complete'],status='HISTORICAL_BOOK_ARCHIVE_MISSING')
    if richer['complete']:present=True;paths=[str(richer['path'])]
    repair=not richer['complete'] and (not present or coverage['needs_richer_archive'])
    return dict(day=day,books_present=present,official_final=final,need_acquire=repair or not final,official_only=present and not repair and not final,force_acquire=repair and richer['force_acquire'],source_paths=paths,reason='RICHER_ARCHIVE_REPAIR_NEEDED' if repair and present else None if present else 'HISTORICAL_BOOK_ARCHIVE_MISSING',coverage=coverage,richer_archive_complete=richer['complete'],richer_archive_reason=richer['reason'],new_financial_runs=0)

def _normalize_jsonl(day,context,runtime_root):
    archive=_runtime_archive(day,context,runtime_root)
    if not archive['complete']:raise PendingSource(archive['reason'],day=day)
    path=archive['path'];rp=archive['receipt_path']
    receipt=read(rp);ref=pin(path)
    if ref['sha256']!=receipt['sha256'] or ref['bytes']!=receipt['bytes'] or receipt.get('rows',500001)>500000:raise ValueError('Native download cap/hash mismatch')
    if receipt.get('source')!='PROBALYTICS_NATIVE_ARCHIVE' or receipt.get('format')!='jsonl' or receipt.get('actual_historical_delivery_proven') is not False:raise ValueError('Original native archive query receipt required')
    if not isinstance(receipt.get('started_ns'),int) or not isinstance(receipt.get('received_ns'),int) or not 0<receipt['started_ns']<=receipt['received_ns']<=time.time_ns():raise ValueError('Actual native query clocks required')
    native=context['strict']['native_books']
    if native.native_clock(receipt['source_start_utc'])>native.clock(day+'T16:29:55Z') or native.native_clock(receipt['source_end_utc'])<native.clock(day+'T20:00:05Z'):raise PendingSource('NATIVE_QUERY_WINDOW_INCOMPLETE',day=day)
    end=context['strict']['engine'].clock(day+'T20:00:05Z');capture_ns=int(receipt['received_ns'])
    if capture_ns<end:raise PendingSource('HISTORICAL_REPLAY_WINDOW_NOT_YET_COMPLETE',day=day)
    np=native.np;start=native.clock(day+'T17:00:00Z');grid=sorted({start+i*60*NS+s*NS for i in range(181) for s in (0,5)})
    producer=pin(__file__);raw_receipt=pin(rp)
    cache_id=hashlib.sha256((ref['sha256']+'|'+producer['sha256']+'|'+producer['path']+'|'+day+'|exact362').encode()).hexdigest()
    dest=Path(runtime_root)/'la-native-normalized'/day/(cache_id+'-exact362.json');seal_path=dest.with_suffix('.seal.json')
    basis=dict(schema='LA_EXACT_RAW_ARCHIVE_TRANSFORM_V1',day=day,raw_source_pin=ref,raw_receipt_pin=raw_receipt,producer_pin=producer,cutoffs_ns=grid,selection='LATEST_AVAILABLE_EVENT_THEN_PATH_THEN_INDEX_WITH_ALL_EXACT_TIES_BEFORE_QUALITY')
    if dest.is_file():
        if not seal_path.is_file():raise ValueError('Unsealed derived native source')
        seal=read(seal_path)
        if seal.get('basis')!=basis or seal.get('normalized_pin')!=pin(dest):raise ValueError('Derived native source provenance/hash mismatch')
        return [dest],[seal['normalized_pin'],ref,raw_receipt,pin(seal_path)]
    groups={}
    row_count=0
    with path.open(encoding='utf-8-sig') as f:
        for line in f:
            if not line.strip():continue
            row_count+=1
            row=json.loads(line,parse_float=str);row['event_ns']=native.native_clock(row['timestamp']);row['indexed_ns']=native.native_clock(row['indexed_at']);row['path_index']=int(row['path_index'])
            for key in ('outcome','bids','asks'):
                if isinstance(row[key],str):row[key]=json.loads(row[key],parse_float=str)
            ticker=row['market_platform_id'];groups.setdefault(ticker,[]).append(row)
    if row_count!=receipt['rows']:raise ValueError('Raw archive row count differs actual query receipt')
    retained=[]
    for ticker,rows in groups.items():
        event=np.array([r['event_ns'] for r in rows],dtype='int64');indexed=np.array([r['indexed_ns'] for r in rows],dtype='int64');paths=np.array([r['path_index'] for r in rows],dtype='int64');sides=np.array([r['outcome']['platform_id'].lower() for r in rows])
        for side in ('yes','no'):
            ids=np.flatnonzero(sides==side)
            if not len(ids):continue
            ordered=ids[np.lexsort((indexed[ids],paths[ids],event[ids]))];available=np.maximum(event[ordered],indexed[ordered]);by=np.argsort(available,kind='stable');latest=np.maximum.accumulate(by)
            for at in grid:
                pos=np.searchsorted(available[by],at,side='right')-1
                if pos<0:continue
                picked=ordered[latest[pos]];ties=ids[(event[ids]==event[picked])&(paths[ids]==paths[picked])&(indexed[ids]==indexed[picked])]
                for i in ties:
                    r=rows[int(i)];o=r['outcome'];levels=lambda key:[[str(x['price']),str(x['size'])] for x in r[key]]
                    retained.append([at,str(r['market_id']),ticker,r['platform'],[str(o['id']),o['platform_id'],o['name'],o['index']],levels('bids'),levels('asks'),r['event_ns'],r['indexed_ns'],str(r['hash']),r['state'],r['continuity'],r['path_index'],{'certification':'EXACT_QUERY_TARGET_FROM_COMPLETE_RAW_ARCHIVE','source_raw_sha256':ref['sha256']}])
    dest.parent.mkdir(parents=True,exist_ok=True);body=json.dumps(retained,separators=(',',':')).encode()
    with dest.open('xb') as f:f.write(body)
    normalized=pin(dest)
    with seal_path.open('x',encoding='utf8') as f:json.dump(dict(basis=basis,normalized_pin=normalized,selected_rows=len(retained),raw_rows=row_count),f,indent=2)
    return [dest],[normalized,ref,raw_receipt,pin(seal_path)]

def _source(day,context,runtime_root):
    try:source=_old_source(day,context)
    except (PendingSource,FileNotFoundError):
        fresh=_official(day,context,runtime_root)
        if not fresh:raise PendingSource('OFFICIAL_DATED_CONTRACT_SOURCE_MISSING',day=day)
        paths,refs=_normalize_jsonl(day,context,runtime_root);base=read(context['configs'][PROFILES[0]])['config'];target=dict(target_verified=True,rules_sha256=hashlib.sha256(json.dumps(fresh['windows'],default=str,sort_keys=True).encode()).hexdigest(),settlement_source='The Weather Company',available_utc=iso(max(w['available_ns'] for w in fresh['windows'])),availability_basis='PUBLIC_CREATED_TIME_ARCHIVAL_PROXY; actual historical first delivery unproven')
        source=dict(paths=paths,hashrefs=refs,windows=fresh['windows'],times=fresh['times'],receipts=fresh['receipts'],target=target,template=base,baseline_pin=fresh['pins'][0],raw_runtime_prepared=True)
        # Only a separately reviewed finite metadata admission can extend the
        # copied date guard. Coefficient/account method remain exactly frozen.
        if context['strict']['engine'].clock(base['fee_policy']['valid_through_utc'])<context['strict']['engine'].clock(day+'T20:00:05Z'):
            try:
                from .offline_la_fee import apply_temporal_admission
                revised,admission,pins=apply_temporal_admission(day,base['fee_policy'],runtime_root)
            except FileNotFoundError:raise PendingSource('DATED_NATIVE_FEE_REFERENCE_MISSING',day=day)
            except ValueError as error:raise PendingSource('CONDITIONAL_FEE_TEMPORAL_ADMISSION_INVALID',day=day,reason_detail=str(error))
            source['template']=deepcopy(base);source['template']['fee_policy']=revised;source['fee_temporal_admission']=admission;source['hashrefs']+=pins
    richer=_runtime_archive(day,context,runtime_root)
    if richer['complete']:
        # The same unchanged newest-before-quality normalizer admits all full
        # levels/flags/ties. A complete raw capture may still contain stale rows.
        fresh=_official(day,context,runtime_root,source)
        if fresh is None:raise PendingSource('AUTHENTIC_DATED_DEFINITION_REQUIRED_FOR_RICHER_ARCHIVE',day=day)
        previous=list(source.get('hashrefs',[]))
        if not source.pop('raw_runtime_prepared',False):
            paths,refs=_normalize_jsonl(day,context,runtime_root);source['paths']=paths;source['hashrefs']=refs
        source['runtime_archive_admission']=dict(source_class='AUTHENTIC_COMPLETE_REGISTERED_WINDOW_RAW_ARCHIVE',raw_source_pins=richer['pins'],superseded_snapshot_source_pins=previous,precedence='RICHER_RUNTIME_NATIVE_ARCHIVE',freshness_control_seconds=60,freshness_or_no_trade_claim=False)
    else:source['runtime_archive_admission']=dict(source_class='PRESERVED_PRIOR_ARCHIVE_FALLBACK',richer_runtime_unavailable=richer['reason'],coverage=_selected_export_coverage(day,source,context))
    fresh=_official(day,context,runtime_root,source)
    if fresh:
        if fresh['official_final'] or not source.get('receipts'):source['receipts']=fresh['receipts']
        source['overlay_pins']=fresh['pins'];source['official_capture_received_ns']=fresh['capture_received_ns']
    return source

def prepare(day,profile_id,forecasts_rows,context,runtime_root):
    if profile_id not in PROFILES or not '2026-10-01'<=day<='2026-10-31':raise PendingSource('OCTOBER_CHECKPOINT_SCOPE_ONLY',day=day)
    source=_source(day,context,runtime_root);saved=[deepcopy(r.get('forecast',r)) for r in forecasts_rows]
    rt=context['legacy'] if profile_id==PROFILES[2] else context['strict'] if profile_id==PROFILES[0] else context['retained'];e=context['strict']['engine']
    expected=[e.clock(day+f'T{h:02}:00:00Z') for h in ((18,) if profile_id==PROFILES[2] else (17,18))]
    for f in saved:
        if 'asof_ns' not in f:f['asof_ns']=e.clock(f['asof_utc'])
    saved.sort(key=lambda f:f['asof_ns'])
    if [f['asof_ns'] for f in saved]!=expected:raise PendingSource('ORIGINAL_FORECAST_CLOCK_SURFACE_MISSING',day=day,profile_id=profile_id)
    contracts=[dict(ticker=w['ticker'],event_id=w['event_ticker'],event_ticker=w['event_ticker'],climate_date=day,lower_f=w.get('lower_bound_f'),upper_f=w.get('upper_bound_f'),lower_inclusive=True,upper_inclusive=True,station='KLAX',settlement_source='The Weather Company',target_verified=True,**source['times'][w['ticker']]) for w in source['windows']]
    binding=None
    if profile_id==PROFILES[2]:cfg=context['common_parameters'](initial_cash='100',source_age_seconds=1800,latency_seconds=5)
    else:
        cfg=deepcopy(read(context['configs'][profile_id])['config'])
        if profile_id==PROFILES[1]:source['target'],binding=context['discovery']['lab'].bind_targets(contracts,saved,source,day)
        for key in ('entry_start_utc','entry_end_utc','confirmation_end_utc','exit_end_utc'):cfg[key]=day+cfg[key][10:]
        cfg['contract_target']=deepcopy(source['target']);cfg['fee_policy']=deepcopy(source['template']['fee_policy']);cfg['strategies'][0]['model_id']=saved[0]['model_id']
        if profile_id==PROFILES[1] and any(f['checkpoint_sha256']!=cfg['expected_checkpoint_sha256'] for f in saved):raise ValueError('Current frozen October F6 checkpoint differs')
    refs=context['code_pins']+source.get('hashrefs',[])+source.get('overlay_pins',[])+([source['baseline_pin']] if source.get('baseline_pin') else [])
    for p in source['paths']:
        ref=next((r for r in source.get('hashrefs',[]) if Path(r['path']).resolve()==Path(p).resolve()),None)
        if ref:context['strict']['native_books'].register_source_pin(p,ref.get('actual_sha256',ref.get('sha256')));context['discovery']['native'].register_source_pin(p,ref.get('actual_sha256',ref.get('sha256')))
    packets, protection_refs = recorded_packets(source, pin, context['actual_history_asof_ns'])
    close_proof = verify_completed_report_window(day, list(source['times'].values()), e)
    if close_proof is None:
        raise PendingSource('CURRENT_COMPLETED_REPORT_EXIT_TRADE_LIFE_EVIDENCE_REQUIRED',day=day,profile_id=profile_id)
    # Existing native archives stop at the entry-monitoring window. A genuine
    # pre-close report outside that grid requires trade-life books; fail closed
    # rather than silently treating an unobserved protection exit as settlement.
    if any(p['proof']['received_ns'] < max(e.clock(source['times'][w['ticker']].get('close_ns',source['times'][w['ticker']].get('close_time'))) for w in source['windows']) for p in packets):
        raise PendingSource('CURRENT_PROTECTION_FULL_TRADE_LIFE_BOOK_GRID_REQUIRED',day=day,profile_id=profile_id)
    refs += protection_refs
    return dict(day=day,profile_id=profile_id,source=source,contracts=contracts,config=cfg,runtime=rt,forecasts=saved,binding=binding,context=context,source_pins=refs,fee_temporal_admission=source.get('fee_temporal_admission'),runtime_archive_admission=source.get('runtime_archive_admission'),recorded_protection_packets=packets,completed_report_window_proof=close_proof,
        acquire_needed=len(source.get('receipts',[]))!=6,assumptions=['CONDITIONAL_ARCHIVAL_EXECUTION; actual queues/fills/private delivery unproven','NWS physical model to archived TWC target remains conditional; current strict-live target is incompatible','Saved October checkpoint reused retrospectively without refitting; creation/first-delivery is not backdated','Official settlement_ts is a historical public-availability proxy, never a private payout receipt'])

def _receipt_queue(state,source):
    prior=state.get('receipts',[]);by={r['ticker']:r for r in prior}
    for r in source.get('receipts',[]):
        if r['ticker'] in by and by[r['ticker']]['yes_result']!=r['yes_result']:raise ValueError('Conflicting official binary result')
        by[r['ticker']]=deepcopy(r)
    state['receipts']=list(by.values());return state['receipts']
def _closed_episode_totals(ledger):
    totals={}
    for r in ledger:
        key=(r.get('ticker'),r.get('side'));record=totals.setdefault(key,dict(bought=0,closed=0,net=Decimal(0)))
        if r.get('action')=='BUY':record['bought']+=r.get('quantity',0)
        modern=r.get('action') in ('SELL','OFFICIAL_SETTLEMENT')
        legacy=r.get('status')=='OFFICIAL_SETTLEMENT_UNDER_AVAILABILITY_EVIDENCE' or r.get('action')=='SELL'
        if not (modern or legacy) or not r.get('quantity'):continue
        net=Decimal(str(r['realized_net'])) if 'realized_net' in r else Decimal(str(r.get('proceeds',r.get('amount',0))))-Decimal(str(r.get('basis_removed',0)))
        record['closed']+=r['quantity'];record['net']+=net
    return {k:r['net'] for k,r in totals.items() if r['bought']>0 and r['closed']==r['bought']}
def _episode_counts(ledger):
    totals=_closed_episode_totals(ledger)
    return sum(v>0 for v in totals.values()),sum(v<0 for v in totals.values())
def _calendar_cash(ledger,settlement_clocks,cutoff):
    """Recorded native cash only; open inventory is neither profit nor loss."""
    value=Decimal('100')
    for r in ledger:
        at=r.get('arrival_ns',r.get('now_ns'))
        if r.get('action')=='OFFICIAL_SETTLEMENT':at=settlement_clocks.get(r['ticker'])
        if at is None or at>cutoff:continue
        if r.get('action')=='BUY':value-=Decimal(str(r.get('amount',0)))
        elif r.get('action')=='SELL':value+=Decimal(str(r.get('amount',0)))
        elif r.get('action')=='OFFICIAL_SETTLEMENT':value+=Decimal(str(r['payout']))
        elif r.get('status')=='OFFICIAL_SETTLEMENT_UNDER_AVAILABILITY_EVIDENCE':value+=Decimal(str(r['proceeds']))
    return value

def run_prepared(prepared,profile_id,state=None):
    """Execute only after facade's independently reviewed finite admission."""
    if profile_id!=prepared['profile_id']:raise ValueError('Prepared profile mismatch')
    with _LOCK:
        c=prepared['context'];day=prepared['day'];source=prepared['source'];e=c['strict']['engine'];now=e.clock(day+('T18:00:00Z' if profile_id==PROFILES[2] else 'T17:00:00Z'));end=e.clock(day+('T18:00:05Z' if profile_id==PROFILES[2] else 'T20:00:05Z'));next_entry=now+86400*NS
        state=state or {};receipts=_receipt_queue(state,source);eligible=[r for r in receipts if e.clock(r['available_utc'])<next_entry and e.clock(r['available_utc'])<=c['actual_history_asof_ns']];cutoff=max([end]+[e.clock(r['available_utc']) for r in eligible]);new_decisions=[];settlement_clocks=state.setdefault('settlement_observed_ns',{})
        for ref in c['code_pins']:
            if pin(ref['path'])['sha256']!=ref['sha256']:raise ValueError('Frozen historical accounting source changed')
        if profile_id!=PROFILES[2]:
            portfolio=state.get('portfolio');sid=prepared['config']['strategies'][0]['strategy_id']
            wallet=portfolio.wallets[sid] if portfolio else None;before=len(wallet.ledger) if wallet else 0;opening_cash=wallet.cash if wallet else Decimal('100');nd=len(portfolio.decisions) if portfolio else 0
            held={p.ticker for p in wallet.positions.values() if p.quantity} if wallet else set()
            for r in eligible:
                at=e.clock(r['available_utc'])
                if now<at<end and r['ticker'] in held and r['ticker'] not in {w['ticker'] for w in source['windows']}:raise PendingSource('PRIOR_PAYOUT_INTRADAY_INTERLEAVING_NOT_REGISTERED',day=day,ticker=r['ticker'])
            if portfolio:
                for r in eligible:
                    if e.clock(r['available_utc'])<now:
                        portfolio.settle(r['ticker'],r['yes_result'],r['source_ref'],e.clock(r['available_utc']),now,r.get('official',False));settlement_clocks.setdefault(r['ticker'],now)
            family=dict(kernel_family='F3' if profile_id==PROFILES[0] else 'F4',weather_mode='NONE' if profile_id==PROFILES[0] else 'HYBRID')
            with c['bridge'].portfolio_context(family,prepared['runtime'],prepared['forecasts']) as replay:
                with current_exit(replay,portfolio,prepared):
                    output,portfolio=replay.run_day(prepared['config'],prepared['contracts'],prepared['forecasts'],source['paths'],iso(end),eligible,portfolio=portfolio,return_portfolio=True,settlement_through_utc=iso(cutoff))
            for r in eligible:
                if r['ticker'] in portfolio.settlement_receipts:settlement_clocks.setdefault(r['ticker'],max(end,e.clock(r['available_utc'])))
            state['portfolio']=portfolio;wallet=portfolio.wallets[sid];ledger=wallet.ledger[before:];new_decisions=portfolio.decisions[nd:];all_ledger=wallet.ledger;cash=wallet.cash;reserved=wallet.reserved;open_contracts=sum(p.quantity for p in wallet.positions.values());unknown_execution=wallet.uncertain or any(o.status=='UNKNOWN_EXECUTION' for o in portfolio.orders.values());fees=sum((Decimal(str(r.get('fee',0))) for r in ledger),Decimal(0));contracts=sum(r.get('quantity',0) for r in ledger if r.get('action')=='BUY')
        else:
            case=state.get('legacy_case') or c['make_common_case'](c['legacy'],initial_cash='100',source_age_seconds=1800,latency_seconds=5);state['legacy_case']=case;engine=case['engine'];policy=case['policy'];wallet=engine.wallet;before=len(wallet.ledger);opening_cash=wallet.cash;processed=state.setdefault('processed_settlements',set())
            def settle_due(through):
                for r in sorted(eligible,key=lambda r:e.clock(r['available_utc'])):
                    identity=(r['ticker'],r['yes_result'],e.clock(r['available_utc']),r['source_ref'])
                    if identity not in processed and e.clock(r['available_utc'])<=through:
                        for side in ('YES','NO'):engine.settle(r['ticker'],side,int(r['yes_result'] if side=='YES' else not r['yes_result']),e.clock(r['available_utc']),max(engine.last_clock,e.clock(r['available_utc'])),r['source_ref'])
                        processed.add(identity)
            settle_due(now);provider=c['legacy_provider'](source['paths'],[now,end],legacy_runtime=c['legacy'],native_runtime=c['discovery']);raw=deepcopy(prepared['forecasts'][0]);original_status=raw.get('status','AVAILABLE')
            if original_status=='AVAILABLE_UNDER_AVAILABILITY_PROXY':raw['status']='AVAILABLE_UNDER_PROXY'
            signal=c['point_signal'](raw);signal['source_packet_original_status']=original_status
            windows={w['ticker']:dict(status='OPEN_UNDER_CONTRACT_WINDOW',available_ns=e.clock(w.get('available_ns',source['target']['available_utc'])),open_ns=e.clock(source['times'][w['ticker']].get('open_ns',source['times'][w['ticker']].get('open_time'))),close_ns=e.clock(source['times'][w['ticker']].get('close_ns',source['times'][w['ticker']].get('close_time')))) for w in source['windows']}
            ctx=c['legacy']['policies'].DecisionContext(now,day,{t:provider.at(t,now) for t in windows},signal,engine,market_status=windows,fee_multipliers={t:Decimal('1') for t in windows});decision=policy.evaluate(ctx);new_decisions=[dict(status=decision.status,reason=decision.reason,decision_ns=now)];orders=policy.submit(decision,ctx)
            for order in orders:
                report=engine.process_arrival(order.order_id,provider.at(order.intent.ticker,order.arrival_ns),order.arrival_ns,windows[order.intent.ticker]);policy.observe_execution(report,engine)
                if report['status']=='UNKNOWN_EXECUTION_EVIDENCE':state['unknown_execution']=True
            settle_due(cutoff);ledger=wallet.ledger[before:];all_ledger=wallet.ledger;cash=wallet.cash;reserved=sum(wallet.buy_reservations.values(),Decimal(0));open_contracts=sum(p.quantity for p in wallet.positions.values());unknown_execution=state.get('unknown_execution',False);fees=sum((Decimal(str(r.get('fee',0))) for r in ledger),Decimal(0));contracts=sum(r.get('quantity',0) for r in ledger if r.get('action')=='BUY')
        unknown_decisions=sum(d.get('status') in ('UNKNOWN','BLOCKED_TARGET') for d in new_decisions);unsupported=sum(bool(d.get('unavailable_opportunities_unknown')) for d in new_decisions)
        pending_orders=any(o.status=='PENDING' for o in (portfolio.orders.values() if profile_id!=PROFILES[2] else engine.orders.values()))
        resolved=not unknown_execution and not reserved and not open_contracts and not pending_orders
        pending_tickers={p.ticker for p in wallet.positions.values() if p.quantity};known_pending=bool(pending_tickers) and all(any(r['ticker']==t and e.clock(r['available_utc'])<=c['actual_history_asof_ns'] for r in receipts) for t in pending_tickers)
        totals=_closed_episode_totals(all_ledger);previous=state.setdefault('counted_closed_episodes',set());new={k:v for k,v in totals.items() if k not in previous};previous.update(new);wins=sum(v>0 for v in new.values());losses=sum(v<0 for v in new.values())
        ticks={w['ticker'] for w in source['windows']};origin={k:v for k,v in totals.items() if k[0] in ticks};bought={r['ticker'] for r in all_ledger if r.get('action')=='BUY' and r.get('quantity') and r['ticker'] in ticks};closed_ticks={k[0] for k in origin};origin_closed=bought<=closed_ticks
        known_origin=str(sum(origin.values(),Decimal(0))) if origin or (not bought and not unknown_decisions and not unsupported) else None
        eventual_origin=known_origin if origin_closed and not unknown_execution and not unknown_decisions and not unsupported else None
        midnight=e.clock(day+'T00:00:00Z')+86400*NS+7*3600*NS;calendar_limit=min(midnight,c['actual_history_asof_ns']);calendar_cash=_calendar_cash(all_ledger,settlement_clocks,calendar_limit)
        def emitted(rows):
            result=deepcopy(rows)
            for row in result:
                if row.get('action')=='OFFICIAL_SETTLEMENT':row['accounting_observed_ns']=settlement_clocks.get(row['ticker']);row['accounting_clock_basis']='UNCHANGED_NATIVE_SETTLE_CALL_CLOCK_SIDECAR; not an exchange/private receipt'
            return e.jsonable(result)
        return dict(state=state,cash=str(cash),opening_cash=str(opening_cash),reserved=str(reserved),open_contracts=open_contracts,pending_orders=pending_orders,unknown_execution=bool(unknown_execution),unknown_decisions=unknown_decisions,unsupported_opportunities=unsupported,ledger=emitted(ledger),decisions=e.jsonable(new_decisions),source_pins=prepared['source_pins'],calendar_cutoff_utc=iso(cutoff),calendar_cash=str(calendar_cash) if not unknown_execution else None,calendar_cash_cutoff_utc=iso(calendar_limit),fees=str(fees),contracts=contracts,wins=wins,losses=losses,net_pnl=str(cash-opening_cash) if resolved else None,net_semantics='PER_CALL_CASH_DELTA_FROM_OPENING_NATIVE_CASH; cumulative wallet return is cash minus initial100',eventual_origin_net=eventual_origin,known_eventual_origin_net=known_origin,closed_episode_net=str(sum(new.values(),Decimal(0))),financially_resolved=resolved,known_pending_payout=known_pending,continuation_supported=not unknown_execution and not reserved and not pending_orders and (resolved or known_pending),all_ledger=emitted(all_ledger),assumptions=prepared['assumptions'],reason='PENDING_SETTLEMENT' if open_contracts and not unknown_execution else 'UNKNOWN_EVIDENCE' if not resolved else 'CONDITIONAL_ARCHIVED_NATIVE_ACCOUNTING',strict_evidence_status='CONDITIONAL_ARCHIVE_NOT_STRICT_LIVE_COMPATIBILITY',exit_policy_evidence=protection_scope(prepared),actual_live_orders=0)
