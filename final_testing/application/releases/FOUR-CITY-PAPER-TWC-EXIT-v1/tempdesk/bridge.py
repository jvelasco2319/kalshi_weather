"""Isolated, hash-bound pure city workers. No exchange client exists here."""
from pathlib import Path
import hashlib,json,os,subprocess,sys

class IntegrityError(ValueError):pass
def runtime_guard(release,runtime):
 release=Path(release).resolve();runtime=Path(runtime).resolve()
 if runtime.is_relative_to(release):raise IntegrityError('Runtime must be outside the frozen release')
 if release.parent.name=='releases' and not runtime.is_relative_to(release.parent.parent/'runtime'):
  raise IntegrityError('Runtime must be inside Dashboard/runtime, outside every release')
 return runtime

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

class Release:
 def __init__(self,root):
  self.root=Path(root).resolve()
  self.manifest_sha256=sha(self.root/'manifest.json')
  self.manifest=json.loads((self.root/'manifest.json').read_bytes())
  self.verify()
 def verify(self):
  if sha(self.root/'manifest.json')!=self.manifest_sha256:raise IntegrityError('Frozen manifest changed after registration')
  for item in self.manifest['files']:
   path=(self.root/item['path']).resolve()
   if not path.is_relative_to(self.root) or not path.is_file() or sha(path)!=item['sha256']:
    raise IntegrityError('Frozen file missing or changed: '+item['path'])
  for item in self.manifest.get('local_only_external_pins',[])+self.manifest.get('runtime_pins',[]):
   p=Path(item['path'])
   if not p.is_file() or sha(p)!=item['sha256']:raise IntegrityError('Registered local-only LA source changed: '+str(p))
  return {'status':'VERIFIED','release_id':self.manifest['release_id'],'files':len(self.manifest['files'])}
 def call(self,city,request,runtime,timeout=45):
  # Worker and every referenced file are verified before each inference/action.
  self.verify()
  runtime=runtime_guard(self.root,runtime)
  if self.manifest.get('python') and sha(sys.executable)!=self.manifest['python']['sha256']:
   raise IntegrityError('Pinned Python runtime changed')
  env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONIOENCODING']='utf-8'
  env['TEMP_DESK_SOURCE_MAP']=str(self.root/'source-map.json')
  env['TEMP_DESK_RUNTIME_ROOT']=str(Path(runtime).resolve())
  env['TEMP_DESK_PAPER_ONLY']='1'
  env.update(self.manifest.get('runtime_environment',{}))
  env['PYTHONPATH']=self.manifest.get('library_path','')
  script=self.root/self.manifest['workers'][city]
  script=script.resolve()
  listed={item['path'] for item in self.manifest['files']}
  if not script.is_relative_to(self.root) or script.relative_to(self.root).as_posix() not in listed:
   raise IntegrityError('Worker must be a verified file inside this release')
  p=subprocess.run([sys.executable,'-B',str(script)],input=json.dumps(request,allow_nan=False),
   capture_output=True,text=True,encoding='utf8',env=env,timeout=timeout,
   cwd=str(self.root),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  if p.returncode:raise RuntimeError(f'{city} worker failed ({p.returncode}): '+p.stderr[-1200:])
  result=json.loads(p.stdout)
  if result.get('orders_sent',0) or result.get('actual_orders',0):raise IntegrityError('Non-paper response rejected')
  return result
