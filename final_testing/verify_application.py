"""Check active application workers and offline source gate without trading."""
from pathlib import Path
import argparse,json,os,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='final_testing/runtime/current-application-check.json');args=parser.parse_args()
    from final_testing.launch import environment,configuration
    from final_testing.tools.install_application import install
    install(ROOT,quiet=True);cfg=configuration();begin=time.time_ns();rows=[]
    programs={
      'online':"""from pathlib import Path
import json,os
root=Path(os.environ['TEMP_DESK_PORTABLE_ROOT']);cfg=json.loads((root/'final_testing/deployment.json').read_bytes())
from tempdesk.bridge import Release
r=Release(root/cfg['application_online']);out={}
try:
 for city in ('la','ny','miami','phoenix'):
  out[city]={}
  for action in ('describe','dependencies','prepare_runtime'):
   v=r.call(city,{'action':action},root/cfg['application_runtime']/'online/verification',timeout=120)
   assert v.get('status') not in ('BLOCKED','UNKNOWN','UNAVAILABLE','ERROR'), (city,action,v.get('status'))
   out[city][action]=v.get('status','READY')
 print(json.dumps({'mode':'online','passes':True,'workers':out,'freeze':r.verify(),'actual_orders':0}))
finally:r.close()
""",
      'offline':"""from pathlib import Path
import json,os,hashlib
root=Path(os.environ['TEMP_DESK_PORTABLE_ROOT']);cfg=json.loads((root/'final_testing/deployment.json').read_bytes());app=root/cfg['application_offline']
os.environ['OFFLINE_LA_REVIEW_PATH']=str(app/'evidence/la-offline-independent-review-v1.json')
os.environ['OFFLINE_LA_FEE_REVIEW_PATH']=str(app/'evidence/la-fee-temporal-producer-review-v1.json')
os.environ['OFFLINE_RELEASE_MANIFEST_SHA256']=hashlib.sha256((app/'offline-manifest.json').read_bytes()).hexdigest()
from tempdesk.offline_la import _review_gate
from tempdesk.offline_history import History
r,pins=_review_gate(root/cfg['application_online'])
h=History(app,root/cfg['application_online'],root/cfg['application_runtime']/'offline-history')
s=h.snapshot();assert s['policy_namespace']=='CURRENT_LIVE_PARITY_v1'
print(json.dumps({'mode':'offline','passes':True,'reviewed_source_pins':len(pins),'policy_namespace':s['policy_namespace'],'job_status':s['job']['status'],'actual_orders':0,'history_replayed':False}))
"""}
    for mode,program in programs.items():
        result=subprocess.run([sys.executable,'-B','-c',program],cwd=ROOT,env=environment(mode,cfg,cfg['online_port']),capture_output=True,text=True,encoding='utf8',timeout=240,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError(mode+' application check failed: '+result.stderr[-1800:])
        rows.append(json.loads(result.stdout))
    receipt={'passes':True,'started_ns':begin,'finished_ns':time.time_ns(),'checks':rows,'actual_orders':0,'new_fits':0}
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(receipt,indent=2),encoding='utf8');print(json.dumps(receipt))
if __name__=='__main__':main()
