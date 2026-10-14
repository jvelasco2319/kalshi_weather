"""Explicit LOCAL_ONLY LA compatibility shim; all external reads are hash-bound.

The common bridge verifies every registered LA external pin first. This shim
rechecks the complete source map against both copies and originals before
loading the original local-only inference package. No mutable fallback exists.
The native policy itself honors mappings. No authentication or order transport.
"""
from pathlib import Path
import hashlib,json,os,runpy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tempdesk.la_impossible_native import hard_exit,hard_arrival
ORIGINAL=Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles/online/LA_TRIO_UI_v1/la_trio_cli.py')
if __name__=='__main__':
 setting=os.environ.get('TEMP_DESK_SOURCE_MAP')
 if not setting:raise SystemExit('Local-only LA bridge requires an exact source map')
 data=json.loads(Path(setting).read_bytes());mapping=data['mapping']
 for original,item in mapping.items():
  p=Path(original)
  if 'Los Angeles' not in p.parts:continue
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
   raise SystemExit('Local-only LA external dependency changed: '+str(p))
  if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
   raise SystemExit('Frozen LA dependency copy changed')
 if str(ORIGINAL.resolve()) not in mapping:raise SystemExit('LA CLI must be explicitly pinned')
 request=json.loads(sys.stdin.read())
 if request.get('action')=='collect_weather':
  from tempdesk.la_live import handler
  print(json.dumps(handler(request),default=str,allow_nan=False))
 elif request.get('action')=='exit':
  print(json.dumps({'status':'HOLD','reason':'NEW_LA_POLICY_HOLDS_INCREASINGLY_UNLIKELY_POSITIONS','orders':[],
                    'policy_state':request.get('policy_state'),'paper_only':True,'actual_orders':0}))
 elif request.get('action') in ('impossible_exit','impossible_exit_arrival'):
  policy=ORIGINAL.with_name('paper_trader_adapter.py')
  copied=Path(mapping[str(policy.resolve())]['path'])
  sys.path.insert(0,str(copied.parent))
  from source_gate import verify_external_sources
  verify_external_sources()
  native=runpy.run_path(str(copied))
  response=hard_exit(native,request) if request['action']=='impossible_exit' else hard_arrival(native,request)
  print(json.dumps(response,default=str,allow_nan=False))
 else:
  # The original CLI consumes stdin; provide the same bounded JSON unchanged.
  import io
  sys.stdin=io.StringIO(json.dumps(request,allow_nan=False))
  sys.argv=[str(ORIGINAL)]
  runpy.run_path(str(ORIGINAL),run_name='__main__')
