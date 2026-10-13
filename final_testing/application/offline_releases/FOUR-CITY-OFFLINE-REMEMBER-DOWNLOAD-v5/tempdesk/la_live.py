"""Prospective LA transport/mapping overlay; original model/native bytes fixed."""
from pathlib import Path
from copy import deepcopy
import ast, hashlib, importlib, json, os, sys, time
from .policy_receipts import verify
from .la_impossible_native import hard_exit, hard_arrival
from .la_weather_repair import collect

ORIGINAL=Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles/online/LA_TRIO_UI_v1/paper_trader_adapter.py')
_LOADED=None
_SESSION_STARTED_NS=time.time_ns()

def load():
    global _LOADED
    if _LOADED:return _LOADED
    mapping=json.loads(Path(os.environ['TEMP_DESK_SOURCE_MAP']).read_bytes())['mapping']
    entry=mapping[str(ORIGINAL.resolve())];sys.path.insert(0,str(Path(entry['path']).parent))
    from source_gate import verify_external_sources
    verify_external_sources()
    native=importlib.import_module('paper_trader_adapter');forecaster=importlib.import_module('forecast_adapter');inputs=importlib.import_module('live_inputs')
    original_state=native._state
    def session_state(request,rt):
        body=original_state(request,rt)
        if not request.get('policy_state'):
            body['session_started_ns']=min(request['at_ns'],_SESSION_STARTED_NS)
        return body
    native._state=session_state
    # The original V8D transport required an exact nanosecond and thus could
    # never admit an actual wall-clock request. Keep 18UTC's first second,
    # quotes, filters, quantities and money; use actual decision/arrival clocks.
    tree=ast.parse(Path(entry['path']).read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='decide')
    changed=deepcopy(fn);count=0
    for node in ast.walk(changed):
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='end' for t in node.targets):
            node.value=ast.parse("dayclock(day,18 if profile==PROFILES[2] else 20)+(NS if profile==PROFILES[2] else 0)",mode='eval').body;count+=1
        if isinstance(node,ast.If) and ast.unparse(node.test)=='now != begin':
            node.test=ast.parse('not begin <= now < begin+NS',mode='eval').body;count+=1
    if count!=2:raise ValueError('V8D exact two timing guards no longer match reviewed source')
    changed.name='prospective_decide';ast.fix_missing_locations(changed)
    exec(compile(ast.Module(body=[changed],type_ignores=[]),'<LA first-second actual-clock transport v2>','exec'),native.__dict__)
    legacy=native.runtime(native.PROFILES[2])['policy_class']
    original_tree=ast.parse(native.resolved(native.LEGACY/'policies.py').read_bytes())
    original_method=next(n for n in ast.walk(original_tree) if isinstance(n,ast.FunctionDef) and n.name=='_entry_time')
    timed=deepcopy(original_method);guards=0
    for node in ast.walk(timed):
        if isinstance(node,ast.If) and ast.unparse(node.test)=='context.asof_ns % (60 * NS)':
            node.test=ast.parse('context.asof_ns % (60 * NS) >= NS',mode='eval').body;guards+=1
    if guards!=1:raise ValueError('Exact single legacy minute-boundary guard changed')
    env=dict(legacy._entry_time.__globals__);ast.fix_missing_locations(timed)
    exec(compile(ast.Module(body=[timed],type_ignores=[]),'<V8D actual first-second entry guard>','exec'),env)
    legacy._entry_time=env['_entry_time']
    _LOADED=(native,forecaster,inputs);return _LOADED

def handler(request):
    native,forecaster,inputs=load();action=request['action'];profile=request.get('profile_id')
    if action=='prepare_runtime':
        for p in native.PROFILES:native.runtime(p)
        forecaster._load_cached()
        return {'status':'RESIDENT_READY','paper_only':True,'actual_orders':0}
    if action in ('describe','dependencies'):return native.dispatch(action,request)
    if action=='collect_weather':return collect(request,inputs)
    if action=='exit':return dict(status='HOLD',reason='IMPOSSIBLE_ONLY_EXIT_POLICY',orders=[],policy_state=request.get('policy_state'),paper_only=True)
    if action in ('impossible_exit','impossible_exit_arrival'):
        return (hard_exit if action=='impossible_exit' else hard_arrival)(vars(native),request)
    if action in ('settle','settlement'):return native.dispatch(action,request)
    proof=verify(request.get('target_binding'),'la',request['day'],request.get('mapping_contracts',request['contracts']),request['at_ns'])
    if action=='forecast':
        supplied=deepcopy(request);preparing=supplied.get('purpose')=='prepare_publication'
        if profile==native.PROFILES[1] and preparing:
            supplied['purpose']='monitoring';supplied.pop('publication_utc',None)
        result=forecaster.forecast(supplied)
        display=result.get('display_estimate')
        if not display:return result
        f=deepcopy(display)
        for k in list(f):
            if k.startswith('display_'):f[k[len('display_'):]]=f.pop(k)
        f.update(target_compatible=True,trade_eligible=profile==native.PROFILES[1] and not preparing,monitoring_only=preparing or profile!=native.PROFILES[1],
                 status='PREPARED_PAPER_DAILY_CLI_PROXY' if preparing else 'PAPER_DAILY_CLI_PROXY_FORECAST',
                 paper_target_mapping=request['target_binding'],target_equivalence_guaranteed=False,
                 forecast_scope=proof['hypothesis'],physical_forecast_projection=True)
        if preparing:
            f.update(scheduled_publication_ns=int(__import__('datetime').datetime.fromisoformat(request['publication_utc'].replace('Z','+00:00')).timestamp()*1e9),publication_ready=True)
        return dict(profile_id=profile,status=f['status'],forecast=f,trade_eligible=f['trade_eligible'],
                    prepared=preparing,paper_only=True,new_fits=0,source_receipts=result.get('source_receipts',[]))
    if action in ('decide','arrival'):
        if action=='decide' and request.get('fee_policy',{}).get('verified') is not True:raise ValueError('Documented paper fee receipt required')
        result=(native.prospective_decide if action=='decide' else native.arrival)(request)
        result.update(paper_target_mapping=request['target_binding'],target_equivalence_guaranteed=False,
                      timing_mode='ACTUAL_FIRST_SECOND_NATIVE_CLOCK_v2')
        return result
    raise ValueError('Unknown LA paper action')
