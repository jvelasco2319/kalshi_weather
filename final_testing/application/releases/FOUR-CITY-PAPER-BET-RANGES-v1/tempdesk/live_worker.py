"""Resident, hash-bound JSON-line city worker. Public paper actions only."""
from pathlib import Path
from contextlib import redirect_stdout
import argparse, importlib.util, io, json, sys, time, functools

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--city',required=True);parser.add_argument('--worker',required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
    if args.city=='la':
        from tempdesk.la_live import handler
    else:
        spec=importlib.util.spec_from_file_location('resident_'+args.city,args.worker)
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        if args.city=='ny':
            # Immutable source objects only. New wallets still receive fresh state.
            module.frozen_model=functools.lru_cache(maxsize=1)(module.frozen_model)
            module.wallet_class=functools.lru_cache(maxsize=1)(module.wallet_class)
        handler=module.handle if args.city=='phoenix' else module.dispatch
    for line in sys.stdin:
        if len(line)>16_000_000:raise ValueError('Bounded paper request exceeded')
        message=json.loads(line);request=message['request'];started=time.time_ns()
        actual=request.pop('_live_actual_dispatch_clock',False)
        if actual and 'at_ns' in request:
            request['requested_at_ns']=request['at_ns'];request['at_ns']=started
        try:
            with redirect_stdout(io.StringIO()):
                if request['action']=='prepare_runtime' and args.city!='la':
                    if args.city=='ny':module.frozen_model();module.wallet_class()
                    elif args.city=='miami':module.operators();module.states()
                    else:
                        for pid in module.PROFILES:module.native_policy(pid)
                    result={'status':'RESIDENT_READY','paper_only':True,'actual_orders':0}
                else:result=handler(request)
            if result.get('actual_orders',0) or result.get('orders_sent',0):raise ValueError('Nonpaper action rejected')
        except Exception as error:
            result={'status':'BLOCKED' if request.get('action')!='arrival' else 'UNKNOWN',
                    'error_type':type(error).__name__,'error':str(error),'orders':[],'paper_only':True,'actual_orders':0}
        print(json.dumps({'id':message['id'],'result':result,'worker_started_ns':started,'worker_finished_ns':time.time_ns()},default=str,allow_nan=False),flush=True)

if __name__=='__main__':main()
