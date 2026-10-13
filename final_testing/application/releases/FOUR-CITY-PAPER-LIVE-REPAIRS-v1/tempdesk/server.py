"""Loopback-only dashboard. Refresh POST performs public GET collection only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import argparse,json,threading,sys
from .service import Service

def main():
 p=argparse.ArgumentParser();p.add_argument('--release',required=True);p.add_argument('--runtime',required=True);p.add_argument('--port',type=int,default=8915);a=p.parse_args()
 service=Service(a.release,a.runtime);static=Path(a.release)/'static'
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,body,kind='application/json',status=200):
   raw=body if isinstance(body,bytes) else json.dumps(body,allow_nan=False).encode()
   self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
   self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
  def do_GET(self):
   if self.path=='/api/state':return self.reply(service.snapshot())
   if self.path=='/health':return self.reply({'paper_only':True,'release':service.release.manifest['release_id'],'freeze':service.freeze})
   filename={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css'}.get(self.path)
   if not filename:return self.reply({'error':'not found'},status=404)
   kind={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','styles.css':'text/css; charset=utf-8'}[filename]
   return self.reply((static/filename).read_bytes(),kind)
  def do_POST(self):
   # Loopback Origin protection: a remote page cannot command this local service.
   origin=self.headers.get('Origin')
   if origin and origin not in (f'http://127.0.0.1:{a.port}',f'http://localhost:{a.port}'):
    return self.reply({'error':'foreign origin'},status=403)
   if self.path=='/api/refresh':return self.reply(service.refresh())
   return self.reply({'error':'not found'},status=404)
 server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
 worker=threading.Thread(target=service.run,daemon=True);worker.start()
 print(json.dumps({'url':f'http://127.0.0.1:{a.port}','release':service.release.manifest['release_id'],'paper_only':True}),flush=True)
 try:server.serve_forever()
 finally:
  service.stop.set();server.server_close();service.pool.shutdown(wait=False,cancel_futures=True)
  service.public_pool.shutdown(wait=False,cancel_futures=True)
  service.policy_pool.shutdown(wait=False,cancel_futures=True)
  service.clock_pool.shutdown(wait=False,cancel_futures=True)
  service.arrival_pool.shutdown(wait=False,cancel_futures=True)
  service.release.close()
if __name__=='__main__':main()
