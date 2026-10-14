"""One serial resident process per city; timeout stops only the owned worker."""
import json, subprocess, threading, queue, time, uuid
from pathlib import Path

class WarmWorker:
    def __init__(self,python,script,city,worker,env,cwd):
        self.lock=threading.Lock();self.messages=queue.Queue();self.dead=False
        self.process=subprocess.Popen([python,'-B',str(script),'--city',city,'--worker',str(worker)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf8',
            env=env,cwd=cwd,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),bufsize=1)
        self.errors=[]
        def read():
            for line in self.process.stdout:
                try:self.messages.put(json.loads(line))
                except Exception:self.messages.put({'error':'Invalid resident worker output'})
            self.messages.put({'error':'Resident worker stopped'})
        def errors():
            for line in self.process.stderr:
                self.errors.append(line[-500:]);self.errors[:]=self.errors[-8:]
        threading.Thread(target=read,daemon=True).start();threading.Thread(target=errors,daemon=True).start()
    def call(self,request,timeout):
        with self.lock:
            if self.dead or self.process.poll() is not None:raise RuntimeError('Resident city worker stopped')
            identifier=uuid.uuid4().hex
            self.process.stdin.write(json.dumps({'id':identifier,'request':request},allow_nan=False)+'\n');self.process.stdin.flush()
            try:message=self.messages.get(timeout=timeout)
            except queue.Empty:
                self.close();raise TimeoutError('Paper worker timeout; no automatic financial redispatch')
            if message.get('error') or message.get('id')!=identifier:
                self.close();raise RuntimeError('Resident worker reply identity failed')
            result=message['result'];result['worker_started_ns']=message['worker_started_ns'];result['worker_finished_ns']=message['worker_finished_ns']
            return result
    def close(self):
        self.dead=True
        try:self.process.stdin.close()
        except (OSError,ValueError):pass
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if __import__('os').name=='nt':
                subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
            else:self.process.terminate()
