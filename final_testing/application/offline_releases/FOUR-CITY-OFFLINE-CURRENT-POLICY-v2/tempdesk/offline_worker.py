"""One isolated native city replay. JSON stdout only; diagnostics go to disk."""
from pathlib import Path
import contextlib, importlib, json, os, sys, time, traceback

def main():
    request=json.load(sys.stdin)
    city=request['city']
    if city not in ('la','ny','miami','phoenix'): raise ValueError('Unsupported city')
    runtime=Path(request['runtime_root'])
    runtime.mkdir(parents=True,exist_ok=True)
    log=runtime/'jobs'/request['job_id']/(city+'-worker.log');log.parent.mkdir(parents=True,exist_ok=True)
    def progress(value):
        from .offline_sources import atomic_json
        atomic_json(log.with_suffix('.progress.json'),{'city':city,'at_ns':time.time_ns(),**value})
    try:
        with log.open('a',encoding='utf8') as diagnostics, contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
            module=importlib.import_module('.offline_'+city,package='tempdesk')
            result=module.replay(request['start_day'],request['end_day'],request['release_path'],str(runtime),progress=progress)
        json.dump(result,sys.stdout,allow_nan=False)
    except Exception as e:
        with log.open('a',encoding='utf8') as diagnostics: traceback.print_exc(file=diagnostics)
        json.dump({'city':city,'status':'FAILED','error_type':type(e).__name__,
                   'reason':'City replay failed; saved prior history is retained. See local worker log.','profiles':[]},sys.stdout)
        sys.exit(1)
if __name__=='__main__': main()
