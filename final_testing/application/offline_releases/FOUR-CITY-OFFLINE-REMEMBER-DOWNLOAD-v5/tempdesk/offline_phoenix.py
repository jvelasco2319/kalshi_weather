"""Phoenix chronological offline history; never a live/order transport adapter.

Conditional archive replay reproduces the admitted event-availability proxy.
Literal archive indexed clocks remain in every quote and strict evidence stays
unproved. Official binary payouts are used without assuming that TWC's target
is interchangeable with the frozen NWS physical forecast. The accepted live
release is read only. Missing sources are requests, not invented no-trade days.

Call in a separate numerical process: native pure functions own an isolated
conformal module namespace. Runtime supplements are normalized JSON files in
runtime_root/history/phoenix/YYYY-MM-DD/{markets,books,forecasts,weather}.json.
No historical downloader can stamp its present receipt as a past receipt.
"""
from __future__ import annotations
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from dataclasses import replace
from functools import lru_cache
import ast, hashlib, importlib.util, json, math, os, re, sys, time, types, uuid

BASE=Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot')
OLD=BASE/'Development/Phoenix/PHOENIX-OCT1-10-SAFETY-REPLAY-v1-20261011'
PRIOR=BASE/'Development/Phoenix/PHOENIX-REALITY-SIM-v1-20261012'
ENGINE=BASE/'Phoenix/Development/PHOENIX-V10-P09-v1/runtime/reference_trader/kernels/engine.py'
ENGINE_SHA='a3e80d08332d40c601fd169dd22f5e915ffb951e9968108d2b3359ea6a184494'
PROFILES=('03-V10-Ensemble-Quarter-Kelly','01-V19-Temperature-Set-Quarter-Kelly')
NS=10**9
ASSUMPTIONS=[
 'CONDITIONAL_ARCHIVE_REPLAY: archived event time is an availability proxy; literal indexed times retained.',
 'Historical first delivery and actual fills are unproved; native five-second freshness and full-quantity top-depth FOK apply.',
 'Frozen NWS KPHX physical forecasts are tested against dated official binary payouts; TWC/NWS scalar equivalence is unproved.',
 'Native quarter Kelly, ten-percent opening-cash ceiling, fees rounded up to $0.0001, one submission per day, hold to settlement.',
 'Independent $100 wallets start October 1 without resets; official settlement clocks control reusable cash.',
 'Eventual settlement profit is attributed to entry day; calendar cash is reported separately.'
]

class EvidencePending(ValueError):pass

def ns(value):
    if isinstance(value,int):return value
    matched=re.fullmatch(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})?',str(value))
    if not matched:raise ValueError('Native exact nanosecond clock required')
    base,fraction,offset=matched.groups();dt=datetime.fromisoformat(base.replace(' ','T')+(''+offset if offset and offset!='Z' else '+00:00'))
    return int(dt.timestamp())*NS+int((fraction or '0').ljust(9,'0'))

def days_between(start_day,end_day):
    a=date.fromisoformat(start_day);b=date.fromisoformat(end_day)
    if a!=date(2026,10,1) or b<a:
        raise ValueError('Offline wallets must start 2026-10-01 without a reset')
    return [(a+timedelta(days=i)).isoformat() for i in range((b-a).days+1)]

def clocks(day):
    return [ns(day+'T'+c+':00Z') for c in ('17:00','17:15','17:30','18:00','19:00')]

def load_json(path):return json.loads(Path(path).read_bytes(),parse_float=str)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj;spec.loader.exec_module(obj);return obj

def frozen_adapter(release,name):
    adapter=module(Path(release)/'tempdesk/adapters/phoenix.py',name)
    # One immutable release/job was already hash-verified. Cache its exact map
    # resolutions and parsed learned JSON; source algorithms stay unchanged.
    adapter.source=lru_cache(maxsize=256)(adapter.source)
    adapter.read=lru_cache(maxsize=256)(adapter.read)
    return adapter

def history_runtime(runtime_root):
    root=Path(runtime_root).resolve();allowed=(BASE/'Dashboard/runtime/offline-history').resolve()
    if not root.is_relative_to(allowed):raise EvidencePending('OFFLINE_CAPTURE_OUTSIDE_REGISTERED_HISTORY_RUNTIME')
    return root

class Catalog:
    def __init__(self,runtime_root):
        self.runtime=Path(runtime_root).resolve();self.root=self.runtime/'history/phoenix';self.pins={};self.expected={};self.cache={};self.forecast_sources={}
        # Reuse exact accepted source seals rather than importing an old driver.
        for filename in ('03-FULL_TOP.json','01-FULL_TOP.json'):
            p=PRIOR/'results/october'/filename
            if p.is_file():
                for r in load_json(p).get('source_pins',[]):
                    if r.get('path') and r.get('sha256'):self.expected[str(Path(r['path']).resolve()).casefold()]=r['sha256']

    def read(self,path):
        p=Path(path).resolve()
        if str(p) in self.cache:return self.cache[str(p)]
        actual=sha(p);expected=self.expected.get(str(p).casefold())
        if expected and actual!=expected:raise EvidencePending('ACCEPTED_ARCHIVE_SOURCE_HASH_CHANGED: '+str(p))
        self.pins[str(p)]={'path':str(p),'sha256':actual}
        self.cache[str(p)]=load_json(p);return self.cache[str(p)]

    def paths(self,day):
        supplemental=self.root/day
        if day<='2026-10-10':
            official=OLD/'sources/kalshi-official'/('KXHIGHTPHX-26OCT'+day[-2:]+'.json')
            refreshed=official.with_name(official.stem+'-settlement-refresh.json')
            if refreshed.is_file():official=refreshed
            book=OLD/'sources/retained-clock-books'/f'{day}.json'
        elif day=='2026-10-11':
            official=PRIOR/'inputs/oct11-official-market-v1.json';book=PRIOR/'inputs/oct11-native-books-v1.json'
        else:official=book=None
        downloaded=self.runtime/'sources/phoenix'/day
        # A fresh broker acquisition must actually replace an incomplete legacy
        # source on retry. Never fall back past a newer invalid acquisition.
        if (downloaded/'official-event.json').is_file():official=downloaded/'official-event.json'
        if (downloaded/'native-books.jsonl').is_file():book=downloaded/'native-books.jsonl'
        weather=downloaded/'weather/weather-inputs.json'
        return {key:(supplemental/(key+'.json') if (supplemental/(key+'.json')).is_file() else fallback)
            for key,fallback in {'markets':official,'books':book,'forecasts':None,'weather':weather if weather.is_file() else None}.items()}

    def forecasts(self,day,paths,adapter,contracts):
        if paths['forecasts']:
            packet=self.read(paths['forecasts'])
            self.forecast_sources[day]=[str(paths['forecasts'])]
            return packet.get('forecasts',packet)
        if day<='2026-10-10':
            values={}
            for pid,name in zip(PROFILES,('ensemble-oct1-10.json','raw14-oct1-10.json')):
                p=OLD/'sources'/name
                if p.is_file():
                    row=next((r for r in self.read(p)['rows'] if r['climate_date']==day),None)
                    if row:values[pid]=row
            if len(values)==2:
                self.forecast_sources[day]=[str(OLD/'sources'/n) for n in ('ensemble-oct1-10.json','raw14-oct1-10.json')]
                return values
        if day=='2026-10-11':
            p=PRIOR/'inputs/oct11-forecasts-v1.json'
            if p.is_file():
                packet=self.read(p);self.forecast_sources[day]=[str(p)];return dict(zip(PROFILES,(packet['ensemble'],packet['raw'])))
        if paths['weather']:
            packet=self.read(paths['weather'])
            self.forecast_sources[day]=[str(paths['weather'])]+packet.get('source_paths',[])
            for receipt in packet.get('receipts',[]):
                for p in (Path(receipt['raw_path']),Path(receipt['raw_path']).with_suffix('.receipt.json')):
                    if not p.resolve().is_relative_to(self.runtime):raise EvidencePending('WEATHER_SOURCE_OUTSIDE_HISTORY_RUNTIME')
                    self.pins[str(p)]={'path':str(p),'sha256':sha(p)}
            return infer_archive_weather(day,packet,contracts,adapter)
        raise EvidencePending('MISSING_FROZEN_NATIVE_FORECAST_AND_ARCHIVED_WEATHER')

def infer_archive_weather(day,packet,contracts,adapter):
    if packet.get('climate_date')!=day or packet.get('complete') is not True or packet.get('new_fits')!=0:
        raise EvidencePending('INCOMPLETE_NATIVE_ARCHIVE_WEATHER')
    if packet.get('availability_mode')!='HISTORICAL_ISSUE_PUBLICATION_PROXY':
        raise EvidencePending('HISTORICAL_WEATHER_AVAILABILITY_BASIS_REQUIRED')
    for key in ('frame','observation','atmosphere','hrrr14'):
        r=packet.get(key,{})
        if r.get('climate_date')!=day or type(r.get('available_ns')) is not int or r['available_ns']>=ns(day+'T17:00:00Z'):
            raise EvidencePending('MISSING_OR_LATE_ARCHIVE_WEATHER_COMPONENT: '+key)
    if packet.get('historical_first_receipt_proven') is not False or not packet.get('source_paths'):
        raise EvidencePending('EXPLICIT_UNPROVED_ARCHIVE_RECEIPTS_AND_RAW_SOURCE_PATHS_REQUIRED')
    for path in packet['source_paths']:
        if not Path(path).is_file():raise EvidencePending('MISSING_NATIVE_WEATHER_SOURCE: '+str(path))
    if not packet.get('receipts'):raise EvidencePending('MISSING_ACTUAL_ARCHIVE_ACQUISITION_RECEIPTS')
    for r in packet['receipts']:
        raw=Path(r['raw_path'])
        if r.get('status') not in (200,206) or r.get('actual_local_HTTP_receipt') is not True or sha(raw)!=r.get('sha256'):
            raise EvidencePending('ARCHIVE_ACQUISITION_HASH_OR_STATUS_MISMATCH')
        if ns(r['started_utc'])>ns(r['received_utc']) or load_json(raw.with_suffix('.receipt.json'))!=r:
            raise EvidencePending('ARCHIVE_ACQUISITION_RECEIPT_MISMATCH')
    model_contracts=[{**c,'lower':None if c['strike_type']=='less' else c['floor_strike'],
        'upper':None if c['strike_type']=='greater' else c['cap_strike'],
        'lower_inclusive':c['strike_type']!='greater','upper_inclusive':c['strike_type']!='less','ordinal_index':i}
        for i,c in enumerate(contracts)]
    result=adapter._infer(day,packet,model_contracts)
    for r in result.values():r.update(reconstruction_not_actual_historical_issuance=True,actual_prospective_issuance=False,new_fits=0)
    return result

def select_clock_books_jsonl(path,day,catalog):
    """Literal prior SQL argMax(event,path,index) per side; keep tied copies.

    The explicit historical event-availability proxy is not an indexed-clock
    eligibility assertion. Newest invalid payloads never fall back to older ones.
    """
    path=Path(path);catalog.pins[str(path)]={'path':str(path),'sha256':sha(path)}
    targets=sorted(clocks(day)+[at+5*NS for at in clocks(day)]);selected={at:{} for at in targets}
    count=0
    with path.open(encoding='utf8') as stream:
        for line in stream:
            if not line.strip():continue
            count+=1
            if count>500000:raise EvidencePending('NATIVE_ARCHIVE_ROW_CEILING_REACHED')
            row=json.loads(line,parse_float=str);event=ns(row['timestamp']);indexed=ns(row['indexed_at']);pi=int(row['path_index'])
            side=str(row['outcome']['platform_id']).upper();ticker=row['market_platform_id']
            if side not in ('YES','NO') or not ticker.startswith('KXHIGHTPHX-26OCT'+day[-2:]+'-'):
                raise EvidencePending('NATIVE_ARCHIVE_FOREIGN_IDENTITY')
            key=(event,pi,indexed);fingerprint=json.dumps(row,sort_keys=True,separators=(',',':'))
            for at in targets:
                if not 0<=at-event<=5*NS:continue
                by=selected[at].setdefault(ticker,{});prior=by.get(side)
                if prior is None or key>prior['key']:by[side]={'key':key,'copies':{fingerprint:row}}
                elif key==prior['key']:prior['copies'][fingerprint]=row
    return {str(at):{ticker:[r for side in by.values() for r in side['copies'].values()] for ticker,by in items.items()}
        for at,items in selected.items()}

HISTORICAL_WEATHER_REPLACEMENTS={
    "if start.astimezone(ZoneInfo('America/Phoenix')).date().isoformat()!=day: raise ValueError('Live capture accepts current Phoenix climate day only; historical retrieval cannot be called prospective')":"# Authorized historical archive capture; actual receipt clocks stay actual-now.",
    "available=max(issue+6*3600*NS,published,ns(r['received_utc']),ns(ir['received_utc']))":"available=max(issue+6*3600*NS,published)",
    "'availability_basis':'max(original nominal issue+6h, publication, actual index and field HTTP receipt)'":"'availability_basis':'HISTORICAL_ISSUE_PLUS6H_AND_PUBLICATION_PROXY_NOT_FIRST_RECEIPT'",
    "for v in frame['fields']: v['source_ref']['availability_basis']='REAL_LOCAL_HTTP_RECEIPT_AND_ORIGINAL_NOMINAL_ISSUE_PLUS6H'":"for v in frame['fields']: v['source_ref']['availability_basis']='HISTORICAL_ISSUE_PLUS6H_AND_PUBLICATION_PROXY_NOT_FIRST_RECEIPT'",
    "row['available_ns']=max(row['available_ns'],*(ns(r['received_utc']) for r in refs))":"# Original assemble already uses genuine S3 Last-Modified timestamps.",
    "row.update(S3_publication_availability_proxy_not_actual_bot_first_receipt=False,actual_local_HTTP_receipt=True)":"row.update(S3_publication_availability_proxy_not_actual_bot_first_receipt=True,actual_local_HTTP_receipt=True)",
    "end=now(); begin=utc(day+'T07:00:00Z')":"end=utc(day+'T17:00:00Z'); begin=utc(day+'T07:00:00Z')",
    "available=max(obs['available_ns'] or 0,ns(receipt['received_utc']))":"available=obs['available_ns'] or 0",
    "obs.update(available_ns=available,source_available_ns=available,actual_first_delivery_proven=True,availability_proxy='REAL_LOCAL_HTTP_RECEIPT_WITH_ORIGINAL_OBS_PLUS15MIN_FLOOR')":"obs.update(available_ns=available,source_available_ns=available,actual_first_delivery_proven=False,availability_proxy='HISTORICAL_OBSERVATION_PLUS15MIN_PROXY_NOT_FIRST_DELIVERY')",
    "atmosphere.update(available_ns=max(atmosphere['available_ns'],ns(receipt['received_utc'])),actual_first_delivery_proven=True)":"atmosphere.update(actual_first_delivery_proven=False)"
}

def historical_collector_source(original):
    """Ten named timing/provenance seams; leave native weather math unchanged."""
    for before,after in HISTORICAL_WEATHER_REPLACEMENTS.items():
        if original.count(before)!=1:raise EvidencePending('FROZEN_HISTORICAL_COLLECTOR_SEAM_CHANGED: '+before)
        original=original.replace(before,after,1)
    return original

def acquire_weather(day,release_path,runtime_root):
    """Bounded public read-only archive collection with actual present receipts.

    Native Capture enforces105 requests/220MiB, approved NOAA/IEM hosts and
    four download workers. No credential transport, fits or historical first
    delivery assertion. Failures remain in the native packet and are pending.
    """
    if date.fromisoformat(day).strftime('%Y-%m')!='2026-10':return {'status':'PENDING','reason':'CHECKPOINT_MONTH_UNAVAILABLE','new_fits':0}
    if ns(day+'T17:00:00Z')>time.time_ns():return {'status':'PENDING','reason':'NATIVE_MORNING_CUTOFF_NOT_YET_REACHED','new_fits':0}
    release=Path(release_path).resolve();runtime=history_runtime(runtime_root)
    if runtime.is_relative_to(release) or release.is_relative_to(runtime):raise ValueError('Runtime must remain outside release')
    from tempdesk.bridge import Release
    Release(release);os.environ['TEMP_DESK_SOURCE_MAP']=str(release/'source-map.json');os.environ['TEMP_DESK_RUNTIME_ROOT']=str(runtime)
    manifest=load_json(release/'manifest.json');sys.path.insert(0,manifest['library_path']);os.environ.update(manifest.get('runtime_environment',{}))
    adapter=frozen_adapter(release,'_offline_phoenix_acquisition_adapter')
    common=types.ModuleType('common');common.ROOT=adapter.BASE;common.NS=NS;common.UTC=timezone.utc
    common.now=lambda:datetime.now(timezone.utc);common.ns=adapter.utc_ns
    common.utc=lambda v:datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(timezone.utc)
    common.read=adapter.read;common.checked=adapter.checked;common.digest=adapter.digest
    common.canonical=lambda obj:json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    common.checkpoint_day=adapter.day_check;common.compile_functions=adapter.compile_functions
    def save(path,obj):
        p=Path(path).resolve()
        if not p.is_relative_to(runtime):raise EvidencePending('ARCHIVE_CAPTURE_OUTSIDE_RUNTIME')
        p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,default=str,allow_nan=False),encoding='utf8')
    common.save=save;sys.modules['common']=common
    for name in ('shared_weather_v2','collect_nyc_weather_v1','phoenix_point_helpers_v1'):
        obj=module(adapter.checked('pinned/weather/'+name+'.py'),name)
        if name=='phoenix_point_helpers_v1':
            oldread=obj.read
            obj.read=lambda p:adapter.read(adapter.BASE/'pinned/weather/city-grid-geometry-v1.json') if Path(p).name=='city-grid-geometry-v1.json' else oldread(p)
    original=adapter.checked('weather.py').read_text(encoding='utf8');collector=types.ModuleType('_offline_phoenix_historical_collector')
    collector.__file__=str(adapter.checked('weather.py'));sys.modules[collector.__name__]=collector
    exec(compile(historical_collector_source(original),collector.__file__+'::HISTORICAL_TIMING_SEAMS','exec'),collector.__dict__)
    oldmodule=collector.module
    collector.module=lambda name,file:sys.modules['phoenix_point_helpers_v1'] if file=='phoenix_point_helpers_v1.py' else oldmodule(name,file)
    canonical_folder=runtime/'sources/phoenix'/day/'weather'
    folder=canonical_folder/'attempts'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True,exist_ok=True)
    packet=collector.collect(day,folder)
    packet.update(availability_mode='HISTORICAL_ISSUE_PUBLICATION_PROXY',historical_first_receipt_proven=False,
        actual_prospective_issuance=False,new_fits=0,source_paths=[r['raw_path'] for r in packet['receipts']],
        archive_collector_source_sha256=hashlib.sha256(original.encode()).hexdigest(),
        timing_seams=list(HISTORICAL_WEATHER_REPLACEMENTS),actual_capture_utc=datetime.now(timezone.utc).isoformat())
    save(folder/'weather-inputs.json',packet);save(canonical_folder/'weather-inputs.json',packet)
    return {'status':'DOWNLOADED' if packet.get('complete') else 'PENDING','weather_path':str(canonical_folder/'weather-inputs.json'),
        'reason':None if packet.get('complete') else 'INCOMPLETE_NATIVE_ARCHIVE_WEATHER','errors':packet.get('errors',[]),
        'requests':packet['request_count'],'bytes':packet['source_bytes'],'actual_historical_delivery_proven':False,'new_fits':0}

def acquire(day,release_path,runtime_root):
    if day[:7]!='2026-10':return {'city':'phoenix','day':day,'status':'PENDING','reason':'CHECKPOINT_NOT_REGISTERED','new_fits':0}
    runtime_root=history_runtime(runtime_root)
    from tempdesk.offline_sources import acquire_day
    market=acquire_day('phoenix',day,runtime_root)
    if not market.get('books_complete'):
        return {'city':'phoenix','day':day,'market':market,'weather':{'status':'PENDING','reason':'ARCHIVED_BOOKS_NOT_READY_WEATHER_DOWNLOAD_DEFERRED'},'read_only':True,'new_fits':0}
    weather_path=Path(runtime_root)/'sources/phoenix'/day/'weather/weather-inputs.json'
    if weather_path.is_file() and load_json(weather_path).get('complete'):weather={'status':'REUSED','weather_path':str(weather_path)}
    else:
        try:weather=acquire_weather(day,release_path,runtime_root)
        except Exception as error:weather={'status':'PENDING','reason':'WEATHER_ACQUISITION_'+type(error).__name__,'detail':str(error)}
    return {'city':'phoenix','day':day,'market':market,'weather':weather,'read_only':True,'new_fits':0}

def normalize_book(rows,ticker,market_id,engine,source):
    if isinstance(rows,dict):
        if rows.get('unknown_reason'):return None,rows['unknown_reason']
        rows=[r.get('native',r) for r in rows.get('native_rows',[])]
    if not isinstance(rows,list) or len(rows)!=2:return None,'INCOMPLETE_OR_CONFLICTING_NATIVE_PAIR'
    pair={str(r.get('outcome',{}).get('platform_id','')).upper():r for r in rows}
    if set(pair)!={'YES','NO'}:return None,'INCOMPLETE_OR_CONFLICTING_NATIVE_PAIR'
    y,n=pair['YES'],pair['NO'];reason=None
    if any(r.get('market_platform_id')!=ticker or r.get('market_id')!=market_id or r.get('platform')!='KALSHI' for r in rows):reason='NATIVE_MARKET_IDENTITY_MISMATCH'
    if (y.get('timestamp'),int(y.get('path_index',-1)))!=(n.get('timestamp'),int(n.get('path_index',-1))):reason='NONATOMIC_EVENT_OR_PATH_PAIR'
    if y['outcome']['id']==n['outcome']['id'] or y['outcome']['index']==n['outcome']['index']:reason='BINARY_OUTCOME_IDENTITY_CONFLICT'
    def levels(r,key):return tuple(engine.Level(D(str(v['price'])),D(str(v['size']))) for v in r[key])
    try:
        book=engine.BookSnapshot(ticker,market_id,ns(y['timestamp']),int(y['path_index']),ns(y['indexed_at']),ns(n['indexed_at']),
            y['state'],n['state'],y['continuity'],n['continuity'],str(y['hash']),str(n['hash']),
            levels(y,'bids'),levels(y,'asks'),levels(n,'bids'),levels(n,'asks'),source,reason is None,reason)
        return book,reason
    except (ValueError,KeyError,TypeError):return None,'MALFORMED_SELECTED_NATIVE_PAIR'

def native_quote(book,reason,at,engine):
    missing={'contract_side':'NO','strict_usable':False,'strict_failure_reasons':[reason or 'MISSING_SELECTED_NATIVE_PAIR'],
        'best_ask_cents':None,'best_bid_cents':None,'displayed_ask_quantity_contracts':None,'quote_at_utc':None,'quote_age_ms':None}
    if book is None:return missing
    admission=engine.validate_snapshot(replace(book,yes_indexed_ns=book.event_ns,no_indexed_ns=book.event_ns),at,5)
    ask=book.no_asks[0].price*100 if book.no_asks else None;bid=book.no_bids[0].price*100 if book.no_bids else None
    cent=ask is not None and ask==ask.to_integral_value() and (bid is None or bid==bid.to_integral_value())
    good=admission.valid and cent and bool(book.no_asks)
    literal=engine.validate_snapshot(book,at,5)
    return {**missing,'strict_usable':good,'strict_failure_reasons':[] if good else [admission.reason if not admission.valid else 'ABSENT_OR_NONCENT_TOP_PRICE'],
        'best_ask_cents':int(ask) if cent else None,'best_bid_cents':int(bid) if cent and bid is not None else None,
        'displayed_ask_quantity_contracts':str(book.no_asks[0].size.to_integral_value(rounding=__import__('decimal').ROUND_FLOOR)) if book.no_asks else None,
        'quote_age_ms':str(D(at-book.event_ns)/1000000),'quote_event_ns':book.event_ns,
        'quote_at_utc':datetime.fromtimestamp(book.event_ns/NS,timezone.utc).isoformat(),
        'original_indexed_ns':{'YES':book.yes_indexed_ns,'NO':book.no_indexed_ns},'original_hashes':{'YES':book.yes_hash,'NO':book.no_hash},
        'native_identity':book.identity,'source_ref':book.source_ref,'event_availability_proxy':True,'historical_first_receipt_proven':False,
        'literal_indexed_clock_admission':literal.valid,'literal_indexed_clock_reason':literal.reason}

def load_day(day,catalog,adapter,engine):
    paths=catalog.paths(day);missing=[key for key in ('markets','books') if paths[key] is None or not paths[key].is_file()]
    if missing:raise EvidencePending('MISSING_ARCHIVED_'+','.join(missing).upper())
    for kind,raw_name,receipt_name in (('markets','official-event.json','official-event-receipt.json'),('books','native-books.jsonl','native-books-receipt.json')):
        raw=paths[kind]
        if raw.name==raw_name:
            receipt_path=raw.parent/receipt_name
            if not receipt_path.is_file() or catalog.read(receipt_path).get('sha256')!=sha(raw):
                raise EvidencePending('ARCHIVE_ACQUISITION_RECEIPT_HASH_MISMATCH: '+kind)
    market=catalog.read(paths['markets']);markets=market.get('markets',market.get('data',[]))
    if len(markets)!=6 or len({m['ticker'] for m in markets})!=6:raise EvidencePending('INCOMPLETE_DATED_SIX_MARKET_DEFINITIONS')
    prefix='KXHIGHTPHX-26OCT'+day[-2:]+'-'
    if any(not m['ticker'].startswith(prefix) for m in markets):raise EvidencePending('DATED_MARKET_IDENTITY_MISMATCH')
    contracts=[{'market_ticker':m['ticker'],'ticker':m['ticker'],'strike_type':m['strike_type'],
        'floor_strike':m.get('floor_strike'),'cap_strike':m.get('cap_strike'),'open_ns':ns(m['open_time']),'close_ns':ns(m['close_time'])} for m in markets]
    contracts.sort(key=lambda c:float('-inf') if c['floor_strike'] is None else float(c['floor_strike']))
    if paths['books'].suffix=='.jsonl':
        status_path=paths['books'].parent/'source-status.json'
        if not status_path.is_file() or not catalog.read(status_path).get('books_complete'):
            raise EvidencePending('ARCHIVE_FULL_DAY_CAPTURE_INCOMPLETE')
    packet=select_clock_books_jsonl(paths['books'],day,catalog) if paths['books'].suffix=='.jsonl' else catalog.read(paths['books']);ids={};books={}
    for at,items in packet.items():
        if not str(at).isdigit() or int(at) not in set(clocks(day)+[a+5*NS for a in clocks(day)]):continue
        normalized={}
        for ticker,rr in items.items():
            rows=[r.get('native',r) for r in rr.get('native_rows',[])] if isinstance(rr,dict) else rr
            for r in rows:
                market_id=r.get('market_id');prior=ids.setdefault(ticker,market_id)
                if market_id!=prior:raise EvidencePending('NATIVE_MARKET_ID_CHANGED_WITHIN_DAY')
            normalized[ticker]=normalize_book(rr,ticker,ids.get(ticker),engine,str(paths['books']))
        books[int(at)]=normalized
    forecasts=catalog.forecasts(day,paths,adapter,contracts)
    for pid in PROFILES:
        r=forecasts.get(pid)
        if not r or r.get('climate_date')!=day or set(r.get('probabilities_yes',{}))!={c['ticker'] for c in contracts}:
            raise EvidencePending('MISSING_OR_MISMATCHED_FROZEN_FORECAST: '+pid)
        r['probabilities_yes']={t:float(q) for t,q in r['probabilities_yes'].items()}
        if pid==PROFILES[0]:r['ensemble_weights']=[float(v) for v in r.get('ensemble_weights',[])]
    payouts=[]
    finalized=all(m.get('status') in ('settled','finalized') and m.get('result') in ('yes','no') and m.get('settlement_ts') for m in markets)
    if finalized and sum(m['result']=='yes' for m in markets)!=1:raise EvidencePending('INVALID_OFFICIAL_BINARY_WINNER_LAYOUT')
    for m in markets:
        if m.get('status') in ('settled','finalized') and m.get('result') in ('yes','no') and m.get('settlement_ts'):
            payouts.append({'at':ns(m['settlement_ts']),'ticker':m['ticker'],'climate_date':day,'no_payout':D(1 if m['result']=='no' else 0),'source_ref':str(paths['markets'])})
    target_warning=any('weather company' in (m.get('rules_primary','')+' '+m.get('rules_secondary','')).lower() for m in markets)
    return {'contracts':contracts,'forecasts':forecasts,'books':books,'payouts':payouts,'target_warning':target_warning,
        'sources':list(dict.fromkeys([str(p) for p in paths.values() if p and p.is_file()]+catalog.forecast_sources.get(day,[])))}

def request_for(day,reason):
    return {'city':'phoenix','day':day,'series':'KXHIGHTPHX','components':['DATED_SIX_MARKET_DEFINITIONS_AND_OFFICIAL_BINARY_PAYOUTS','NATIVE_ATOMIC_YES_NO_BOOKS','FROZEN_NATIVE_WEATHER'],
        'cutoffs_ns':sorted(clocks(day)+[a+5*NS for a in clocks(day)]),'weather_components':['HRRR06Z_GEFS00Z44_FIELDS','KPHX_PRE17UTC_MORNING_TEMP_AND_SAME_REPORT_ATMOSPHERE','HRRR14Z_NATIVE_15_TO30UTC_POINTS'],
        'reason':reason,'read_only':True,'new_fits':0,'target_equivalence_required_for_live':True}

def inventory(start_day,end_day,runtime_root):
    cat=Catalog(runtime_root);rows=[]
    for day in days_between(start_day,end_day):
        p=cat.paths(day);rows.append({'day':day,'paths':{k:str(v) if v else None for k,v in p.items()},
            'books_present':bool(p['books'] and p['books'].is_file()),'markets_present':bool(p['markets'] and p['markets'].is_file()),
            'saved_forecasts_present':day<='2026-10-11' or bool(p['forecasts'])})
    return {'city':'phoenix','days':rows,'profiles':list(PROFILES),'assumptions':ASSUMPTIONS}

def replay(start_day,end_day,release_path,runtime_root,progress=None):
    days=days_between(start_day,end_day);release=Path(release_path).resolve();runtime=history_runtime(runtime_root)
    if runtime.is_relative_to(release) or release.is_relative_to(runtime):raise ValueError('Runtime and immutable release must be separate')
    # The release bridge verifies its Python/worker/map/vendor hashes before use.
    from tempdesk.bridge import Release
    frozen=Release(release)
    os.environ['TEMP_DESK_SOURCE_MAP']=str(release/'source-map.json');os.environ['TEMP_DESK_RUNTIME_ROOT']=str(runtime)
    manifest=load_json(release/'manifest.json');sys.path.insert(0,manifest['library_path']);os.environ.update(manifest.get('runtime_environment',{}))
    adapter=frozen_adapter(release,'_offline_frozen_phoenix')
    if sha(ENGINE)!=ENGINE_SHA:raise EvidencePending('ACCEPTED_NATIVE_ARCHIVE_VALIDATOR_CHANGED')
    engine=module(ENGINE,'_offline_phoenix_engine');catalog=Catalog(runtime);catalog.pins[str(ENGINE)]={'path':str(ENGINE),'sha256':ENGINE_SHA}
    data={};requests=[]
    for day in days:
        if day[:7]!='2026-10':
            data[day]={'pending':'CHECKPOINT_NOT_REGISTERED'}
            requests.append({'city':'phoenix','day':day,'component':'MONTH_CHECKPOINT','status':'PENDING','reason':'CHECKPOINT_NOT_REGISTERED','new_fits':0})
            if progress:progress({'city':'phoenix','phase':'source_inventory','day':day,'pending':True,'reason':'CHECKPOINT_NOT_REGISTERED'})
            continue
        try:data[day]=load_day(day,catalog,adapter,engine)
        except (EvidencePending,ValueError,KeyError,OSError) as e:
            if progress:progress({'city':'phoenix','phase':'acquisition','day':day,'reason':str(e)})
            acquisition=acquire(day,release,runtime)
            # Fresh runtime captures may now support a missing dated input.
            catalog.cache.clear()
            try:data[day]=load_day(day,catalog,adapter,engine)
            except (EvidencePending,ValueError,KeyError,OSError) as refreshed:
                data[day]={'pending':str(refreshed)};request=request_for(day,str(refreshed));request['acquisition']=acquisition;requests.append(request)
        if progress:progress({'city':'phoenix','phase':'source_inventory','day':day,'pending':bool(data[day].get('pending'))})
    results=[run_wallet(pid,days,data,adapter,engine,progress) for pid in PROFILES]
    return {'city':'phoenix','profiles':results,'requests':requests,'source_pins':list(catalog.pins.values()),'assumptions':ASSUMPTIONS,
        'mode':'CONDITIONAL_ARCHIVE_REPLAY','strict_evidence_status':'UNPROVED_FIRST_DELIVERY_AND_EXECUTION','paper_only':True,'new_fits':0,'orders_sent':0}

def run_wallet(pid,days,data,adapter,engine,progress=None):
    native=adapter.native_policy(pid);cash=D(100);reserved=D(0);positions={};submitted=set();opening={};risk_lock=False;missing_lock=False
    ledger=[];rows={day:{'day':day,'status':'COMPLETE','reason':None,'ending_cash':None,'known_cash':'100','net_pnl':'0',
        'wins':0,'losses':0,'contracts':0,'fees':'0','reserved':'0','source_paths':data[day].get('sources',[]),'assumptions':ASSUMPTIONS,
        'strict_evidence_status':'UNPROVED_FIRST_DELIVERY_AND_EXECUTION','unknown_opportunities':0} for day in days}
    events=[];actual_now=time.time_ns()
    for day in days:
        for at in clocks(day):events.append((at,1,'ENTRY',day))
        for r in data[day].get('payouts',[]):
            if r['at']<=actual_now:events.append((r['at'],0,'SETTLEMENT',r))
        events.append((ns((date.fromisoformat(day)+timedelta(days=1)).isoformat()+'T07:00:00Z'),2,'CALENDAR_END',day))
    events.sort(key=lambda e:(e[0],e[1],str(e[3])))
    for at,_,kind,item in events:
        if kind=='CALENDAR_END':
            rows[item]['calendar_cash']=str(cash)
            if at>actual_now:rows[item].update(status='PENDING',reason=rows[item]['reason'] or 'CALENDAR_CUTOFF_NOT_YET_REACHED')
            continue
        if kind=='SETTLEMENT':
            p=positions.pop(item['ticker'],None)
            if p:
                proceeds=D(p['quantity'])*item['no_payout'];cash+=proceeds;net=proceeds-p['basis'];row=rows[p['day']]
                row['net_pnl']=str(D(row['net_pnl'] or 0)+net);row['wins']+=int(net>0);row['losses']+=int(net<0)
                ledger.append({'kind':'SETTLEMENT','at_ns':at,'day':p['day'],'ticker':item['ticker'],'quantity':p['quantity'],'payout':str(proceeds),'basis':str(p['basis']),'net':str(net),'cash_after':str(cash),'source_ref':item['source_ref']})
            continue
        day=item;row=rows[day];d=data[day];opening.setdefault(day,cash-reserved)
        if at>actual_now:
            row.update(status='PENDING',reason=row['reason'] or 'NATIVE_ENTRY_CLOCK_NOT_YET_REACHED',net_pnl=None);continue
        if d.get('pending'):
            missing_lock=True;row.update(status='PENDING',reason=d['pending'],net_pnl=None);continue
        if risk_lock or missing_lock:
            row.update(status='UNKNOWN' if risk_lock else 'PENDING',reason='UNRESOLVED_NATIVE_EXECUTION_RISK_LOCK' if risk_lock else 'EARLIER_CALENDAR_SOURCE_GAP',net_pnl=None);continue
        if day in submitted:continue
        contracts=d['contracts'];quotes={}
        for c in contracts:
            book,reason=d['books'].get(at,{}).get(c['ticker'],(None,None));q=native_quote(book,reason,at,engine)
            if not c['open_ns']<=at<c['close_ns']:q.update(strict_usable=False,strict_failure_reasons=['OUTSIDE_DATED_CONTRACT_WINDOW'])
            quotes[c['ticker']]=q
        try:decision=native['decision'](pid,day,at,d['forecasts'][pid],contracts,quotes,opening[day],cash-reserved,False)
        except (ValueError,KeyError,TypeError) as e:
            row.update(status='PENDING',reason='FROZEN_FORECAST_OR_NATIVE_INPUT_REJECTED: '+str(e),net_pnl=None);missing_lock=True;continue
        unavailable=decision.get('selection',{}).get('unavailable_market_sides',[])
        if unavailable:row['unknown_opportunities']+=len(unavailable)
        ledger.append({'kind':'DECISION','day':day,'at_ns':at,'opening_cash':str(opening[day]),'available_cash':str(cash-reserved),'native_result':decision,'quotes':quotes})
        if not decision.get('orders'):continue
        if len(decision['orders'])!=1:raise ValueError('Native one-best submission invariant')
        order=decision['orders'][0];ticker=order['market_ticker'];q=int(order['quantity']);reserve=D(order['maximum_reserved_outlay_dollars'])
        if reserve>opening[day]*D('.10') or reserve>cash-reserved:raise ValueError('Native fee-inclusive daily cash cap')
        submitted.add(day);reserved+=reserve;arrival=at+5*NS;book,reason=d['books'].get(arrival,{}).get(ticker,(None,None));aq=native_quote(book,reason,arrival,engine)
        result=native['arrival'](pid,day,order,aq);status=result['execution_status']
        if status=='UNKNOWN_EXECUTION':risk_lock=True;row.update(status='UNKNOWN',reason='UNKNOWN_SUBMITTED_ORDER_ARRIVAL',net_pnl=None,reserved=str(reserve))
        else:
            reserved-=reserve
            if status=='HYPOTHETICAL_FILL':
                basis=D(result['fill_entry_outlay_dollars']);fee=D(result['fill_fee_dollars']);cash-=basis
                if basis>reserve or cash<0 or q>D(aq['displayed_ask_quantity_contracts']):raise ValueError('Native cash/depth FOK invariant')
                positions[ticker]={'quantity':q,'basis':basis,'day':day};row['contracts']+=q;row['fees']=str(D(row['fees'])+fee)
        ledger.append({'kind':'BUY','day':day,'decision_ns':at,'arrival_ns':arrival,'frozen_order':order,'native_result':result,'arrival_quote':aq,'cash_after':str(cash),'reserved_after':str(reserved)})
        if progress:progress({'city':'phoenix','profile_id':pid,'phase':'replay','day':day,'status':status,'known_cash':str(cash)})
    for p in positions.values():rows[p['day']].update(status='PENDING',reason='OFFICIAL_PAYOUT_PENDING',net_pnl=None)
    cumulative=D(100);complete_prefix=True;previous_calendar_cash=D(100)
    for day in days:
        row=rows[day]
        if row['status']=='COMPLETE' and row['contracts']==0 and row['unknown_opportunities']:
            row.update(status='UNKNOWN',reason='UNAVAILABLE_NATIVE_OPPORTUNITIES_NO_SUPPORTED_SUBMISSION',net_pnl=None)
        if row['status']!='COMPLETE':complete_prefix=False
        row['eventual_origin_net']=row['net_pnl']
        if row['eventual_origin_net'] is not None:cumulative+=D(row['eventual_origin_net'])
        row['eventual_origin_settled_cash']=str(cumulative) if complete_prefix else None
        calendar_cash=D(row.get('calendar_cash',str(cash)))
        row['ending_cash']=str(calendar_cash) if complete_prefix else None
        row['net_pnl']=str(calendar_cash-previous_calendar_cash) if complete_prefix else None
        row['known_cash']=str(calendar_cash);previous_calendar_cash=calendar_cash
        if data[day].get('target_warning'):row['target_warning']='TWC_OFFICIAL_PAYOUT_VS_NWS_PHYSICAL_FORECAST_EQUIVALENCE_UNPROVED'
    costs=sum((D(r['native_result']['fill_entry_outlay_dollars']) for r in ledger if r['kind']=='BUY' and r['native_result']['execution_status']=='HYPOTHETICAL_FILL'),D(0))
    payouts=sum((D(r['payout']) for r in ledger if r['kind']=='SETTLEMENT'),D(0))
    if cash!=D(100)-costs+payouts:raise ValueError('Offline Phoenix cash accounting identity')
    return {'profile_id':pid,'wallet_id':'phoenix:'+pid,'days':list(rows.values()),'initial_cash':'100','ending_cash':rows[days[-1]]['ending_cash'],
        'known_cash':str(cash),'wins':sum(r['wins'] for r in rows.values()),'losses':sum(r['losses'] for r in rows.values()),
        'contracts':sum(r['contracts'] for r in rows.values()),'fees':str(sum((D(r['fees']) for r in rows.values()),D(0))),'reserved':str(reserved),
        'open_positions':{t:{**p,'basis':str(p['basis'])} for t,p in positions.items()},'ledger':ledger,'assumptions':ASSUMPTIONS,
        'cash_accounting_passed':True,'native_unresolved_execution':risk_lock,'calendar_source_gap':missing_lock,'supported_trade_settlement_balance':str(D(100)+sum((D(r['net']) for r in ledger if r['kind']=='SETTLEMENT'),D(0)))}
