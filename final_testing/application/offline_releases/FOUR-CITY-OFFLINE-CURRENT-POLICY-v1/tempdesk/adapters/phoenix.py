"""Frozen Phoenix paper interface. JSON stdin -> JSON stdout; never orders.

Inputs are normalized six half-open INTEGER Fahrenheit brackets. Native model
contracts use lower-inclusive/upper-exclusive bounds, which preserve exactly
the same integers as the exchange's original strikes. Quotes contain dollar
strings: bids are NO bids, asks are complementary YES bids, and each level is
[price,size]. A RECEIPT_CLOCK is kept as a local receipt clock; it is never
renamed to an exchange event. The original five-second freshness rule uses the
declared clock, with actual fill evidence explicitly unproved.

forecast requires the unchanged native frame/observation/atmosphere/hrrr14
packet and hash-bound actual pre-17UTC HTTP receipts. Dated production NWS
CLIPHX/KPHX rules can bind the saved physical target. TWC-only rules cannot.
decide takes forecast (or state.forecast), actual clock, frozen 10am opening
cash and current own available cash. Later clocks reuse the morning vector.
PUBLIC_REST_CLOCK_V1 permits actual decision processing at a native slot through
slot+1s, only from quotes received by the slot and fresh at actual processing.
Native numeric calculations use that nominal slot while actual decision_ns
remains separate. Arrival is actual decision+5s through +6s, never backdated.
Unknown quotes retain uncertainty; quantities are never resized.

All learned files and pure functions are read through source(). Setting
TEMP_DESK_SOURCE_MAP forbids falling back to original mutable file locations.
No account, signing, exchange transport, service, fit or outcome code is used.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace, ModuleType
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from contextlib import redirect_stdout
import ast, hashlib, importlib.util, io, json, math, os, sys, uuid, time
sys.dont_write_bytecode=True

BASE=Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Development/Phoenix/PHOENIX-ORIGINAL-DEMO-v2-20261011')
DASHBOARD=Path(__file__).resolve().parents[2]
PROFILES=('03-V10-Ensemble-Quarter-Kelly','01-V19-Temperature-Set-Quarter-Kelly')
NS=10**9;UTC=timezone.utc
_LOADER=None
TOP_PINS={
    'policy.py':('1bfa466feca61bd6ac5637771a20a6367eb7cbd32af5e0f29bf303a53a9fdfca',5778),
    'weather.py':('4aa72582aaf1b536ce6a186d29e028289959c01a85a11a20805b232ba690fa9b',13655)}

class Blocked(ValueError):
    def __init__(self,code,detail=''):
        self.code=code;super().__init__(detail or code)

def source(path):
    original=Path(path).resolve();mapping_path=os.environ.get('TEMP_DESK_SOURCE_MAP')
    if not mapping_path:return original
    data=json.loads(Path(mapping_path).read_bytes())
    mapping=data.get('mapping',data.get('sources',data))
    normalized={str(Path(k).resolve()).casefold():v for k,v in mapping.items()}
    value=normalized.get(str(original).casefold())
    if value is None:
        # checked() already returns a mapped copy; repeated resolution must be
        # idempotent without admitting any unlisted mutable source location.
        value=next((v for v in mapping.values() if isinstance(v,dict) and v.get('sha256')
            and str(Path(v['path']).resolve()).casefold()==str(original).casefold()),None)
    if value is None:raise Blocked('SOURCE_MAPPING_MISSING',str(original))
    target=Path(value['path'] if isinstance(value,dict) else value).resolve()
    if not target.is_file():raise Blocked('FROZEN_SOURCE_MISSING',str(target))
    if isinstance(value,dict) and value.get('sha256') and digest(target.read_bytes())!=value['sha256']:
        raise Blocked('FROZEN_SOURCE_CHANGED',str(target))
    return target

def digest(data):return hashlib.sha256(data).hexdigest()
def read(path):return json.loads(source(path).read_bytes(),parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
def checked(relative):
    original=BASE/relative
    pins={r['path']:r for r in read(BASE/'source-pins.json')['files']}
    item=pins.get(Path(relative).as_posix());actual=source(original);raw=actual.read_bytes()
    if item is None and relative in TOP_PINS:
        sha,size=TOP_PINS[relative];item={'sha256':sha,'bytes':size}
    if not item or len(raw)!=item['bytes'] or digest(raw)!=item['sha256']:
        raise Blocked('FROZEN_SOURCE_CHANGED',str(original))
    return actual

def ns(day,clock='17:00:00'):
    return int(datetime.fromisoformat(day+'T'+clock).replace(tzinfo=UTC).timestamp())*NS
def utc_ns(value):
    date=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if date.tzinfo is None:raise Blocked('UNTIMED_SOURCE_RECEIPT','Timezone-aware receipt required')
    delta=date.astimezone(UTC)-datetime(1970,1,1,tzinfo=UTC)
    return (delta.days*86400+delta.seconds)*NS+delta.microseconds*1000
def money(value):
    if value is None or isinstance(value,bool):raise Blocked('INVALID_CASH','Finite decimal required')
    number=Decimal(str(value))
    if not number.is_finite() or number<0:raise Blocked('INVALID_CASH','Nonnegative finite decimal required')
    return number
def day_check(day):
    if not isinstance(day,str) or datetime.fromisoformat(day).date().isoformat()!=day or day[:7]!='2026-10':
        raise Blocked('CHECKPOINT_MONTH_UNSUPPORTED','Only frozen October 2026 checkpoints exist; no automatic fit')
def runtime_root():return Path(os.environ.get('TEMP_DESK_RUNTIME_ROOT',str(DASHBOARD/'runtime'))).resolve()
def collected_json(path):
    """New generated data stays inside runtime; frozen sources use pinned reads."""
    p=Path(path).resolve()
    if p.is_relative_to(runtime_root()):
        if p.suffix!='.json' or p.stat().st_size>16_000_000:raise Blocked('INVALID_GENERATED_JSON')
        return json.loads(p.read_bytes(),parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
    return read(path)
def public_slot(day,actual):
    if type(actual) is not int:raise Blocked('UNTIMED_ACTUAL_DECISION')
    slots=[ns(day,clock) for clock in ('17:00:00','17:15:00','17:30:00','18:00:00','19:00:00')]
    found=[slot for slot in slots if slot<=actual<=slot+NS]
    if len(found)!=1:raise Blocked('PUBLIC_REST_DECISION_WINDOW_MISSED','Actual decision must be within native scheduled minute+1 second')
    return found[0]
def day_state(raw,day):
    state=deepcopy(raw);previous=state.get('day',state.get('forecast',{}).get('climate_date'))
    if previous is not None and previous!=day:
        for name in ('forecast','opening_cash','submitted','evaluated_slots'):state.pop(name,None)
        # An unobserved fill remains uncertain even after the climate day ends.
    state.setdefault('submitted',False);state.setdefault('evaluated_slots',[]);state['day']=day
    return state
def profile(pid):
    if pid not in PROFILES:raise Blocked('PROFILE_UNSUPPORTED',str(pid))
    return next(r for r in read(BASE/'profiles.json')['profiles'] if r['profile_id']==pid)

def compile_functions(relative,names,namespace):
    path=checked(relative);tree=ast.parse(path.read_text(encoding='utf8'))
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
    if {n.name for n in nodes}!=set(names):raise Blocked('FROZEN_CODE_CLOSURE_CHANGED',relative)
    env=dict(namespace)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec',flags=__import__('__future__').annotations.compiler_flag),env)
    return env

@lru_cache(maxsize=2)
def native_policy(pid):
    # Exact functions from the accepted original adapter, not a reimplementation
    # of qualification, fee, ranking, reservation, sizing or arrival formulas.
    env={'ROOT':BASE,'NS':NS,'compile_functions':compile_functions,'profile':profile,
         'checkpoint_day':day_check,'ns':utc_ns,
         'money':money,'checked':checked,'read':read,'SimpleNamespace':SimpleNamespace,
         'Mapping':Mapping,'deepcopy':deepcopy,'Decimal':Decimal,'ROUND_CEILING':ROUND_CEILING,'math':math}
    return compile_functions('policy.py',{'original_math','decision','arrival','missing_quote'},env)

def contracts_native(rows,day):
    day_check(day)
    if len(rows)!=6 or len({r['ticker'] for r in rows})!=6:raise Blocked('INCOMPLETE_BRACKETS','Six unique genuine Phoenix brackets required')
    normalized=sorted(rows,key=lambda r:float('-inf') if r['lower'] is None else float(r['lower']))
    answer=[]
    for i,row in enumerate(normalized):
        lower,upper=row['lower'],row['upper']
        for value in (lower,upper):
            if value is not None and (isinstance(value,bool) or Decimal(str(value))!=Decimal(str(value)).to_integral_value()):
                raise Blocked('BRACKET_NOT_INTEGER','Normalized bounds must be half-open whole-F integers')
        dated='KXHIGHTPHX-'+datetime.fromisoformat(day).strftime('%y%b%d').upper()+'-'
        if not row['ticker'].startswith(dated):raise Blocked('FOREIGN_CITY_OR_DATE_CONTRACT',row['ticker'])
        if lower is not None and upper is not None and lower>=upper:raise Blocked('INVALID_BRACKET')
        raw=row.get('raw',{});typ='less' if lower is None else 'greater' if upper is None else 'between'
        answer.append({'ticker':row['ticker'],'market_ticker':row['ticker'],'ordinal_index':i,
            'lower':lower,'upper':upper,'lower_inclusive':True,'upper_inclusive':False,
            'floor_strike':raw.get('floor_strike',lower),'cap_strike':raw.get('cap_strike',upper),
            'strike_type':raw.get('strike_type',typ),'label':row.get('label',row['ticker'])})
    if answer[0]['lower'] is not None or answer[-1]['upper'] is not None or any(a['upper']!=b['lower'] for a,b in zip(answer,answer[1:])):
        raise Blocked('INCOMPLETE_INTEGER_PARTITION','Brackets must cover all integers exactly once')
    return answer

def settlement_binding(rows, request=None):
    if request and request.get("paper_mapping_mode")=="VERIFIED_DAILY_CLI_PROXY_v1":
        from tempdesk.policy_receipts import verify
        proof=verify(request.get("target_binding"),"phoenix",request["day"],rows,request["at_ns"])
        return {"provider":"THE_WEATHER_COMPANY","station":"KPHX","target":"PAPER_DAILY_CLI_PROXY","mapping":request["target_binding"],"equivalence_guaranteed":False,"basis":proof["hypothesis"]}
    for row in rows:
        raw=row.get('raw',{})
        rules=(raw.get('rules_primary','')+'\n'+raw.get('rules_secondary','')).lower()
        if 'weather company' in rules or 'twc' in rules:
            raise Blocked('SETTLEMENT_TARGET_INCOMPATIBLE','TWC scalar equivalence to the saved KPHX NWS physical target is unproved')
        if not ('national weather service' in rules and ('daily climate' in rules or 'climatological report' in rules)
                and ('cliphx' in rules or 'kphx' in rules or 'sky harbor' in rules)):
            raise Blocked('SETTLEMENT_TARGET_UNVERIFIED','Dated NWS Daily Climate Report for KPHX/Sky Harbor/CLIPHX required')
    return {'provider':'NATIONAL_WEATHER_SERVICE','station':'KPHX','report':'CLIPHX','target':'PHYSICAL_INTEGER_DAILY_HIGH_F',
            'binding_basis':'INSPECTED_DATED_PUBLIC_NWS_RULES','TWC_equivalence_asserted':False,'actual_payout_receipt_proven':False}

def quote_native(value,at_ns):
    missing=native_policy(PROFILES[0])['missing_quote']()
    if not value:return {**missing,'source_age_clock':None,'exchange_event_time_proven':False}
    try:
        received=value['received_ns'];clock=value['source_age_clock']
        if type(received) is not int or not 0<=at_ns-received<=5*NS:raise Blocked('LATE_OR_FUTURE_BOOK_RECEIPT')
        if clock=='RECEIPT_CLOCK':stamp=received
        elif clock in ('SERVER_TIME','SERVER_TIME_CLOCK','EXCHANGE_EVENT_CLOCK'):
            stamp=value['server_time_ns']
            if type(stamp) is not int or not 0<=at_ns-stamp<=5*NS:raise Blocked('STALE_OR_FUTURE_BOOK_CLOCK')
        else:raise Blocked('UNKNOWN_BOOK_CLOCK')
        def ladder(name,reverse):
            items=[(money(r[0]),money(r[1])) for r in value[name]]
            if any(not 0<p<1 or p*100!=(p*100).to_integral_value() or q<=0 for p,q in items) or len({p for p,q in items})!=len(items):
                raise Blocked('INVALID_NATIVE_PRICE_OR_DEPTH')
            return sorted(items,reverse=reverse)
        bids=ladder('bids',True);asks=ladder('asks',False)
        bid=bids[0][0] if bids else None;ask=asks[0][0] if asks else None
        if bid!=(None if value.get('bid') is None else money(value['bid'])) or ask!=(None if value.get('ask') is None else money(value['ask'])):
            raise Blocked('QUOTE_TOP_LADDER_MISMATCH')
        if not asks:raise Blocked('GENUINE_EMPTY_ASK')
        return {'contract_side':'NO','strict_usable':True,'strict_failure_reasons':[],
            'best_bid_cents':None if bid is None else int(bid*100),'best_ask_cents':int(ask*100),
            'displayed_ask_quantity_contracts':str(asks[0][1]),'quote_at_utc':datetime.fromtimestamp(stamp/NS,UTC).isoformat(),
            'quote_age_ms':str(Decimal(at_ns-stamp)/10**6),'source_age_clock':clock,'received_ns':received,
            'server_time_ns':value.get('server_time_ns'),'exchange_event_time_proven':clock=='EXCHANGE_EVENT_CLOCK'}
    except (KeyError,ValueError,TypeError) as error:
        return {**missing,'strict_failure_reasons':[getattr(error,'code','INVALID_NATIVE_BOOK')],
            'source_age_clock':value.get('source_age_clock'),'received_ns':value.get('received_ns'),'exchange_event_time_proven':False}

def _raw_loader():
    global _LOADER
    if _LOADER is not None:return _LOADER
    prefix='pinned/profiles/01-V19-Temperature-Set-Quarter-Kelly/model/raw14/'
    descriptor=BASE/(prefix+'saved-hrrr14-23-bundle-v3.json')
    # Only filesystem reads are rebound; the complete original class/inference
    # method is compiled unchanged. Numerical core imports receive mapped files.
    class AssetPath(type(Path())):
        def read_bytes(self):return source(Path(str(self))).read_bytes()
    env=compile_functions(prefix+'saved_hrrr14_23_loader_v3.py',{'SavedPhoenixHRRR14'},
        {'Path':AssetPath,'datetime':datetime,'timezone':timezone,'hashlib':hashlib,'importlib':__import__('importlib'),'json':json,'math':math,'sys':sys})
    cls=env['SavedPhoenixHRRR14']
    def mapped_path(self,key):
        if key not in self.files or Path(key).is_absolute() or '..' in Path(key).parts:raise Blocked('FROZEN_COMPONENT_UNREGISTERED',key)
        target=Path(str(self.root))/key
        if not target.resolve().is_relative_to(Path(str(self.root)).resolve()):raise Blocked('FROZEN_COMPONENT_ESCAPE')
        actual=source(target);data=actual.read_bytes();bound=self.files[key]
        if len(data)!=bound['bytes'] or digest(data)!=bound['sha256']:raise Blocked('FROZEN_SOURCE_CHANGED',key)
        return actual
    cls.path=mapped_path
    _LOADER=cls(descriptor)
    return _LOADER

def _infer(day,weather,contracts):
    loader=_raw_loader()
    frame=loader.base.join_frame(weather['frame'],{'contracts':contracts,'source_ref':{'source':'DATED_NWS_PHYSICAL_RULES'}})
    raw=loader.forecast('2026-10',frame,weather['observation'],weather['atmosphere'],weather['hrrr14'])
    prefix='pinned/profiles/03-V10-Ensemble-Quarter-Kelly/model/uncertainty-laws/'
    bundle=read(checked(prefix+'saved-uncertainty-laws-bundle-v9-v2.json'))
    family=bundle['families']['EQUAL_WEIGHT_SELECTED_LAW_ENSEMBLE']['months']['2026-10']
    state=read(checked(prefix+family['ensemble_state']));loader.c.verify_seal(state)
    if state['weights']!=[.25]*4 or state['training_cutoff_ns']!=raw['trained_to_ns']:raise Blocked('ENSEMBLE_CHECKPOINT_CHANGED')
    parents=[]
    for name in family['parent_order']:
        p=bundle['families'][name]['months']['2026-10'];q=list(raw['probabilities_yes'].values())
        if p['kind']!='RAW_CONTROL':
            law=read(checked(prefix+p['law_state']));selection=read(checked(prefix+p['selection']))
            loader.c.verify_seal(law);loader.c.verify_seal(selection)
            if selection['selected_candidate']!=p['selected_candidate'] or law['origin_ns']!=raw['trained_to_ns']:raise Blocked('PARENT_CHECKPOINT_CHANGED')
            if p['selected_candidate']=='NEW_LAW':q=loader.m.raw_vector(raw['location_f'],law['law'],contracts)
            elif p['selected_candidate']!='RAW14_CONTROL':raise Blocked('PARENT_CHECKPOINT_UNSUPPORTED')
        parents.append(q)
    vector=[.25*math.fsum(p[i] for p in parents) for i in range(6)];total=math.fsum(vector);vector=[v/total for v in vector]
    loader.c.probability_vector(vector)
    ensemble={**raw,'model_id':family['model_id'],'variant_id':'EQUAL_WEIGHT_SELECTED_LAW_ENSEMBLE',
        'probabilities_yes':dict(zip([c['ticker'] for c in contracts],vector)),
        'ensemble_state_self_sha256':state['self_sha256'],'ensemble_weights':[.25]*4,
        'entropy':-math.fsum(v*math.log(v) for v in vector if v>0)/math.log(6),
        'gap':sorted(vector,reverse=True)[0]-sorted(vector,reverse=True)[1]}
    return {PROFILES[0]:ensemble,PROFILES[1]:raw}

def _weather_validate(day,packet):
    if not packet or packet.get('climate_date')!=day or packet.get('complete') is not True:
        raise Blocked('MISSING_NATIVE_WEATHER','Complete genuine same-day KPHX inputs required')
    origin=ns(day)
    for key in ('frame','observation','atmosphere','hrrr14'):
        if not packet.get(key):raise Blocked('MISSING_NATIVE_WEATHER_COMPONENT',key)
        available=packet[key].get('available_ns')
        if type(available) is not int or available>=origin:raise Blocked('LATE_NATIVE_WEATHER',key)
    if not packet.get('receipts'):raise Blocked('MISSING_ACTUAL_WEATHER_RECEIPTS')
    for r in packet['receipts']:
        try:
            received=utc_ns(r['received_utc'])
            started=utc_ns(r['started_utc'])
            raw=Path(r['raw_path'])
            if not raw.resolve().is_relative_to(runtime_root()):raise Blocked('WEATHER_RECEIPT_OUTSIDE_RUNTIME_ROOT')
            if received>=origin or started>received:raise Blocked('LATE_NATIVE_WEATHER_RECEIPT')
            if r.get('status') not in (200,206) or r.get('actual_local_HTTP_receipt') is not True or digest(raw.read_bytes())!=r['sha256'] or json.loads(raw.with_suffix('.receipt.json').read_bytes())!=r:
                raise Blocked('UNBOUND_ACTUAL_WEATHER_RECEIPT')
        except (KeyError,OSError) as error:raise Blocked('UNBOUND_ACTUAL_WEATHER_RECEIPT',str(error)) from error

def describe():
    checkpoints=read(BASE/'october-checkpoints.json')
    prefix='pinned/profiles/03-V10-Ensemble-Quarter-Kelly/model/uncertainty-laws/'
    ensemble=read(BASE/(prefix+'saved-uncertainty-laws-bundle-v9-v2.json'))['families']['EQUAL_WEIGHT_SELECTED_LAW_ENSEMBLE']['months']['2026-10']
    return {'ok':True,'city':'Phoenix','station':'KPHX','timezone':'America/Phoenix','checkpoint_month':'2026-10',
        'series':'KXHIGHTPHX',
        'profiles':[{'profile_id':pid,'family':profile(pid)['family'],'model_id':ensemble['model_id'] if pid==PROFILES[0] else checkpoints[pid]['point_model_id'],
            'point_state_sha256':checkpoints[pid]['point_self_sha256'],'rule_id':profile(pid)['rule_id'],
            'static_forecast_utc':'17:00','entry_clocks_utc':['17:00','17:15','17:30','18:00','19:00'],
            'sizing':'QUARTER_KELLY_CAPPED_10_PERCENT_OPENING_CASH','side':'NO','exit':'OFFICIAL_SETTLEMENT'} for pid in PROFILES],
        'fee_quantum':'0.0001','taker_fee_coefficient':'0.07','arrival_delay_seconds':5,'source_age_seconds':5,
        'new_fits':0,'orders_enabled':False,'paper_only':True,'late_clock_backdating':False,
        'public_timing_mode':'PUBLIC_REST_CLOCK_V1','decision_processing_grace_seconds':1,'arrival_processing_grace_seconds':1,
        'decision_quotes_must_arrive_by_nominal_slot':True}

def dependencies():
    files={'source-pins.json','profiles.json','october-checkpoints.json','policy.py','weather.py'}
    raw='pinned/profiles/01-V19-Temperature-Set-Quarter-Kelly/model/raw14/'
    files.update((raw+'saved_hrrr14_23_loader_v3.py',raw+'saved-hrrr14-23-bundle-v3.json',raw+'profile.json'))
    files.update(raw+'core/'+n+'.py' for n in ('city_profile','v10_contract_reference','v10_math_reference','city_v10_simplex_blend_v1','phoenix_source_model_base_v1','phoenix_monthly_models_v1'))
    b=read(BASE/(raw+'saved-hrrr14-23-bundle-v3.json'));cp=b['checkpoints']['2026-10'];files.update(raw+cp[k] for k in ('fitted_state','selection'))
    laws='pinned/profiles/03-V10-Ensemble-Quarter-Kelly/model/uncertainty-laws/'
    files.add(laws+'saved-uncertainty-laws-bundle-v9-v2.json');b=read(BASE/(laws+'saved-uncertainty-laws-bundle-v9-v2.json'));e=b['families']['EQUAL_WEIGHT_SELECTED_LAW_ENSEMBLE']['months']['2026-10'];files.add(laws+e['ensemble_state'])
    for n in e['parent_order']:
        c=b['families'][n]['months']['2026-10']
        if c['kind']!='RAW_CONTROL':files.update(laws+c[k] for k in ('law_state','selection'))
    files.update('pinned/execution/'+n+'.py' for n in ('p09_decimal_math','probability_transform','p09_order_math','p09_policy'))
    files.add('pinned/profiles/03-V10-Ensemble-Quarter-Kelly/native-package/weather/no_qualification_filter_v10.py')
    for pid in PROFILES:
        p='pinned/profiles/'+pid+'/native-package/trader/'
        files.update(p+n+'.py' for n in ('allocation_policy_v5','allocation_scheduled_entry_v5','generic_p09_side_v4'))
        if pid==PROFILES[1]:files.update((p+'conformal_yes_set_v19.py',p+'states/2026-10-conformal-threshold.json'))
    files.update('pinned/weather/'+name for name in ('shared_weather_v2.py','collect_nyc_weather_v1.py','phoenix_point_helpers_v1.py','collect_phoenix_temperatures_v1.py','collect_hrrr14_points_v1.py','collect_and_summarize_kphx_v1.py','derive_kphx_same_report_atmosphere_v1.py','city-grid-geometry-v1.json','hrrr-geometry.json'))
    return {'ok':True,'dependencies':[str(BASE/f) for f in sorted(files)],
        'pins':{str(BASE/f):digest(source(BASE/f).read_bytes()) for f in sorted(files)},'new_fits':0}

def collect_weather(request):
    from tempdesk.weather_retry import install as install_weather_retry
    install_weather_retry()
    day=request['day'];day_check(day);origin=ns(day);current=datetime.now(UTC)
    if current.astimezone(__import__('zoneinfo').ZoneInfo('America/Phoenix')).date().isoformat()!=day:
        raise Blocked('CURRENT_DAY_COLLECTION_REQUIRED','Historical receipt cannot be called prospective')
    if utc_ns(current)>=origin:raise Blocked('FORECAST_CUTOFF_MISSED','Native static forecast inputs must arrive before17UTC')
    # The owned bounded credential-free collector is imported; no DEMO module.
    common=ModuleType('common');common.ROOT=BASE;common.NS=NS;common.UTC=UTC
    common.now=lambda:datetime.now(UTC);common.ns=utc_ns
    common.utc=lambda value:datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(UTC)
    common.read=collected_json;common.checked=checked;common.digest=digest;common.canonical=lambda obj:json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    common.checkpoint_day=day_check;common.compile_functions=compile_functions
    def save(path,obj):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,default=json_default,allow_nan=False),encoding='utf8')
    common.save=save;sys.modules['common']=common
    # Preload the original public parser imports through frozen source mappings.
    for name in ('shared_weather_v2','collect_nyc_weather_v1','phoenix_point_helpers_v1'):
        spec=importlib.util.spec_from_file_location(name,checked('pinned/weather/'+name+'.py'));obj=importlib.util.module_from_spec(spec);sys.modules[name]=obj;spec.loader.exec_module(obj)
        if name=='phoenix_point_helpers_v1':
            oldread=obj.read
            obj.read=lambda p:read(BASE/'pinned/weather/city-grid-geometry-v1.json') if Path(p).name=='city-grid-geometry-v1.json' else oldread(p)
    spec=importlib.util.spec_from_file_location('_phoenix_public_collector',checked('weather.py'));collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
    original_module=collector.module
    def mapped_public_module(name,file):
        if file=='phoenix_point_helpers_v1.py':return sys.modules['phoenix_point_helpers_v1']
        return original_module(name,file)
    collector.module=mapped_public_module
    root=runtime_root();capture=Path(request.get('capture_dir',str(root/'weather'/'phoenix'))).resolve()
    if not capture.is_relative_to(root):raise Blocked('WEATHER_CAPTURE_OUTSIDE_RUNTIME_ROOT')
    folder=capture/day/(datetime.now(UTC).strftime('%H%M%S')+'-'+uuid.uuid4().hex[:8])
    packet=collector.collect(day,folder)
    return {'ok':bool(packet.get('complete')),'status':'READY' if packet.get('complete') else 'BLOCKED','weather':packet,
        'weather_path':str(folder/'weather-inputs.json'),'blockers':packet.get('errors',[]),'new_fits':0,'paper_only':True}

def forecast(request):
    day=request['day'];pid=request.get('profile_id',PROFILES[0]);profile(pid);day_check(day)
    rows=request.get('contracts',[]);contracts=contracts_native(rows,day);binding=None;target_blocker=None
    try:binding=settlement_binding(rows,request)
    except Blocked as error:target_blocker={'code':error.code,'detail':str(error)}
    received=request.get('contracts_received_ns')
    if received is None:
        clocks=[r.get('raw',{}).get('received_ns',r.get('raw',{}).get('_received_ns')) for r in rows]
        received=max(clocks) if all(type(v) is int for v in clocks) else None
    if type(received) is not int or received>=ns(day):raise Blocked('MISSING_OR_LATE_DEFINITION_RECEIPT','Six dated definitions must have genuine receipt before17UTC')
    packet=request.get('weather');_weather_validate(day,packet)
    actual_started_ns=time.time_ns()
    source_ready_ns=max(received,*(packet[k]['available_ns'] for k in ('frame','observation','atmosphere','hrrr14')),
        *(utc_ns(r['received_utc']) for r in packet['receipts']))
    if source_ready_ns>actual_started_ns:raise Blocked('SOURCE_NOT_AVAILABLE_AT_GENERATION','Cannot infer from future receipts or component availability')
    values=_infer(day,packet,contracts)
    # The static native morning vector must genuinely exist before 17 UTC.
    # Request creation/source receipt and completed inference are different
    # clocks. A request begun before the cutoff cannot backdate a late result.
    actual_generation_ns=time.time_ns()
    if actual_generation_ns<actual_started_ns:raise Blocked('NONMONOTONIC_FORECAST_GENERATION_CLOCK')
    available_ns=max(source_ready_ns,actual_generation_ns)
    for v in values.values():v.update(settlement_binding=binding,paper_only=True,actual_prospective_issuance=True,reconstruction_not_actual_historical_issuance=False,
        forecast_target='NWS_PHYSICAL_INTEGER_HIGH_F',trade_eligible=target_blocker is None)
    paper=request.get('paper_mapping_mode')=='VERIFIED_DAILY_CLI_PROXY_v1'
    at=request.get('at_ns',actual_started_ns)
    if type(at) is not int or at>actual_generation_ns:raise Blocked('INVALID_FORECAST_REQUEST_CLOCK')
    preparing=paper and target_blocker is None and request.get('purpose')=='prepare_publication' and available_ns<ns(day)
    # Paper morning vectors are prepared first, then admitted by the service at
    # the genuine publication slot. A fresh late monitor is never a buy signal.
    admitted=target_blocker is None and not paper and available_ns<ns(day)
    for value in values.values():
        value.update(trade_eligible=admitted,available_ns=available_ns,actual_generation_ns=actual_generation_ns,
            actual_generation_started_ns=actual_started_ns,forecast_request_ns=at,source_ready_ns=source_ready_ns,
            prepared_ready_ns=actual_generation_ns,prepared=preparing)
        if paper:value['target_equivalence_guaranteed']=False
    blockers=[target_blocker] if target_blocker else []
    if available_ns>=ns(day):blockers.append({'code':'NATIVE_FORECAST_COMPLETED_AFTER_CUTOFF','detail':'Static morning inference was not ready before17UTC'})
    return {'ok':True,'status':'NWS_PHYSICAL_FORECAST_ONLY' if target_blocker else 'READY','forecast':values[pid],'forecasts':values,
        'trade_eligible':admitted,'prepared':preparing,'prepared_ready_ns':actual_generation_ns,
        'blockers':blockers,'new_fits':0,'orders_enabled':False}

def decide(request):
    day=request['day'];pid=request['profile_id'];p=profile(pid);day_check(day);at=request['at_ns']
    contracts=contracts_native(request.get('contracts',[]),day);binding=settlement_binding(request['contracts'],request)
    state=day_state(request.get('state',{}),day);signal=request.get('forecast',state.get('forecast'))
    if state.get('unknown_execution'):
        return {'ok':True,'status':'UNKNOWN','orders':[],'reason':'UNRESOLVED_EXECUTION_RISK_LOCK','policy_state':state,'paper_only':True,'orders_enabled':False}
    if signal is None:
        weather=request.get('weather',{});signal=weather.get('forecasts',{}).get(pid) or weather.get('forecast')
    if not signal:raise Blocked('MISSING_FROZEN_FORECAST')
    if request.get('paper_mapping_mode')=='VERIFIED_DAILY_CLI_PROXY_v1':
        generated=signal.get('actual_generation_ns');ready=signal.get('prepared_ready_ns');available=signal.get('available_ns')
        if (signal.get('trade_eligible') is not True or any(type(v) is not int for v in (generated,ready,available))
            or not generated==ready<=available<ns(day)):
            raise Blocked('UNPROVED_PRE_CUTOFF_FORECAST_COMPLETION','Published morning vector needs its unchanged genuine preparation clock')
    if request.get('opening_cash') is None:raise Blocked('MISSING_FROZEN_10AM_OPENING_CASH')
    slot=public_slot(day,at)
    if slot in state['evaluated_slots']:
        return {'ok':True,'status':'NO_TRADE','orders':[],'reason':'NATIVE_SLOT_ALREADY_EVALUATED','policy_state':state,'orders_enabled':False,'paper_only':True}
    quotes={t:quote_native(v,at) for t,v in request.get('quotes',{}).items()}
    for ticker,q in quotes.items():
        if q.get('received_ns') is not None and q['received_ns']>slot:
            quotes[ticker]={**q,'strict_usable':False,'strict_failure_reasons':['SOURCE_NOT_AVAILABLE_AT_NOMINAL_NATIVE_SLOT']}
    used=bool(request.get('submitted',False) or state.get('submitted',False))
    native=native_policy(pid)['decision'](pid,day,slot,signal,contracts,quotes,money(request['opening_cash']),money(request['available_cash']),used)
    wrapped=[{'ticker':o['market_ticker'],'side':'NO','quantity':o['quantity'],'limit_price':str(Decimal(o['limit_price_cents'])/100),
        'reserved_outlay':o['maximum_reserved_outlay_dollars'],'arrival_delay_seconds':5,'decision_ns':at,
        'nominal_decision_ns':slot,'timing_mode':'PUBLIC_REST_CLOCK_V1','fee_rate':'.07','fee_quantum':'.0001',
        'profile_id':pid,'day':day,'native_order':o} for o in native['orders']]
    state.update(forecast=signal,opening_cash=str(request['opening_cash']),submitted=used or bool(wrapped),evaluated_slots=state['evaluated_slots']+[slot])
    unavailable=native.get('selection',{}).get('unavailable_market_sides',[])
    return {'ok':True,'status':'SUBMIT' if wrapped else 'UNKNOWN' if unavailable else 'NO_TRADE','orders':wrapped,
        'reason':native.get('reason',native.get('selection',{}).get('reason')),'native_result':native,'policy_state':state,
        'settlement_binding':binding,'source_age_clocks':{t:q.get('source_age_clock') for t,q in quotes.items()},
        'actual_execution_proven':False,'orders_enabled':False,'new_fits':0,'paper_only':True,
        'timing_mode':'PUBLIC_REST_CLOCK_V1','actual_decision_ns':at,'nominal_calculation_slot_ns':slot}

def arrival(request):
    day=request['day'];pid=request['profile_id'];profile(pid);day_check(day)
    frozen=request['frozen_order'];native=frozen.get('native_order',frozen)
    decision=frozen.get('decision_ns',request.get('state',{}).get('decision_ns'))
    if type(decision) is not int or type(request['at_ns']) is not int or request['at_ns']<decision+5*NS:
        raise Blocked('ARRIVAL_CLOCK_MISMATCH','Arrival cannot precede actual decision+five seconds')
    if native.get('contract_side')!='NO' or type(native.get('quantity')) is not int or native['quantity']<=0:raise Blocked('INVALID_FROZEN_ORDER')
    q=quote_native(request.get('quotes',{}).get(native['market_ticker']),request['at_ns'])
    if request['at_ns']>decision+6*NS:
        q={**q,'strict_usable':False,'strict_failure_reasons':['PUBLIC_REST_ARRIVAL_PROCESSING_WINDOW_MISSED']}
    result=native_policy(pid)['arrival'](pid,day,native,q)
    status={'HYPOTHETICAL_FILL':'FILLED','NOT_FILLED':'CANCELLED','UNKNOWN_EXECUTION':'UNKNOWN'}[result['execution_status']]
    filled=native['quantity'] if status=='FILLED' else 0
    state=day_state(request.get('state',{}),day);state.update(submitted=True,unknown_execution=status=='UNKNOWN')
    return {'ok':True,'status':status,'reason':result.get('execution_reason'),'ticker':native['market_ticker'],'side':'NO','quantity':filled,
        'principal':str(Decimal(result['fill_price_cents'])/100*filled) if filled else '0',
        'fee':result.get('fill_fee_dollars','0'),'reserved_outlay':native['maximum_reserved_outlay_dollars'],
        'native_result':result,'source_age_clock':q.get('source_age_clock'),'actual_execution_proven':False,'orders_enabled':False,
        'policy_state':state,'paper_only':True,'timing_mode':'PUBLIC_REST_CLOCK_V1','actual_arrival_ns':request['at_ns'],
        'earliest_actual_arrival_ns':decision+5*NS,'nominal_decision_ns':frozen.get('nominal_decision_ns'),
        'fills':[{'quantity':filled,'price':str(Decimal(result['fill_price_cents'])/100),'fee':result['fill_fee_dollars']}] if filled else []}

def json_default(obj):
    if isinstance(obj,(Decimal,Path)):return str(obj)
    raise TypeError(type(obj).__name__)

def handle(request):
    try:
        action=request.get('action','describe')
        if action=='describe':return describe()
        if action=='dependencies':return dependencies()
        if action not in ('collect_weather','forecast','decide','arrival'):raise Blocked('ACTION_UNSUPPORTED',action)
        with redirect_stdout(io.StringIO()):return globals()[action](request)
    except (ValueError,KeyError,TypeError,OSError,ImportError) as error:
        return {'ok':False,'status':'BLOCKED','orders':[],'blockers':[{'code':getattr(error,'code','NATIVE_INPUT_REJECTED'),'detail':str(error)}],
            'new_fits':0,'orders_enabled':False,'paper_only':True}

if __name__=='__main__':
    try:payload=json.load(sys.stdin);result=handle(payload)
    except Exception as error:result={'ok':False,'status':'BLOCKED','blockers':[{'code':'ADAPTER_FAILURE','detail':str(error)}],'orders_enabled':False,'new_fits':0}
    print(json.dumps(result,default=json_default,allow_nan=False,separators=(',',':')))
