"""Reuse passing hashes briefly only while every file identity remains unchanged."""
from pathlib import Path
import threading,time

class EvidenceCache:
 def __init__(self):self.lock=threading.RLock();self.checked=None;self.at=0;self.result=None
 def check(self,release):
  with self.lock:
   paths=[release.root/'manifest.json']+[release.root/r['path'] for r in release.manifest['files']]
   paths.extend(Path(r['path']) for r in release.manifest.get('local_only_external_pins',[])+release.manifest.get('runtime_pins',[]))
   def stamps():
    return [(str(p),s.st_size,s.st_mtime_ns,s.st_ctime_ns) for p in paths for s in (p.stat(),)]
   before=stamps()
   if self.checked==before and time.monotonic()-self.at<5:return self.result
   result=release.verify();after=stamps()
   if before!=after:raise ValueError('Frozen files changed during hash verification')
   self.checked=after;self.at=time.monotonic();self.result=result
   return result
