"""Additive offline UI. Existing online service and wallets are never imported."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.parse import urlparse
import argparse, hashlib, json, secrets, threading, time
from .offline_history import History
from .offline_sources import atomic_json

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app-root',required=True)
    parser.add_argument('--release',required=True);parser.add_argument('--runtime',required=True)
    parser.add_argument('--port',type=int,default=8920);args=parser.parse_args()
    app_root=Path(args.app_root).resolve();runtime=Path(args.runtime).resolve()
    manifest=json.loads((app_root/'offline-manifest.json').read_bytes())
    for item in manifest['files']:
        p=(app_root/item['path']).resolve()
        if not p.is_relative_to(app_root) or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Offline release changed: '+item['path'])
    if hashlib.sha256((Path(args.release)/'manifest.json').read_bytes()).hexdigest()!=manifest['model_manifest_sha256']:
        raise ValueError('Model release does not match offline registration')
    history=History(app_root,args.release,runtime)
    auth={'value':None};auth_lock=threading.Lock();query_lock=threading.Lock();token=secrets.token_urlsafe(40)
    def sql_query(sql,credentials):
        upper=sql.lstrip().upper()
        if not upper.startswith('SELECT ') or ';' in sql or any(x in upper for x in ('INSERT ','ALTER ','DROP ','CREATE ','SYSTEM ','INTO OUTFILE','URL(','REMOTE(','FILE(')):
            raise ValueError('Only bounded archive SELECT queries are supported')
        import requests
        with query_lock:
            response=requests.post('https://clickhouse.probalytics.io:8443/',params={'database':'probalytics'},
                                   data=sql.encode(),auth=credentials,timeout=180,stream=True)
            if response.status_code!=200: raise ConnectionError('Read-only archive connection rejected')
            body=bytearray()
            for chunk in response.iter_content(1_048_576):
                body.extend(chunk)
                if len(body)>128_000_000: raise ValueError('Archive response exceeded bounded size')
            return bytes(body)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def allowed(self):
            return self.headers.get('Host') in (f'127.0.0.1:{args.port}',f'localhost:{args.port}')
        def reply(self,value,status=200,kind='application/json'):
            raw=value if isinstance(value,bytes) else json.dumps(value,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if not self.allowed(): return self.reply({'error':'Invalid host'},403)
            route=urlparse(self.path).path
            if route=='/api/offline': return self.reply(history.snapshot())
            if route=='/api/offline/export':
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Disposition','attachment; filename="tempdesk-offline-history.json"')
                self.send_header('Cache-Control','no-store');self.end_headers()
                return self.wfile.write(json.dumps(history.snapshot(),indent=2,allow_nan=False).encode())
            if route=='/api/state':
                try:
                    with urlopen('http://127.0.0.1:8915/api/state',timeout=12) as r: return self.reply(r.read())
                except Exception: return self.reply({'error':'Original online monitoring service is unavailable'},503)
            if route=='/health': return self.reply({'offline_release':manifest['release_id'],'model_release':history.release.manifest['release_id'],
                                                   'separate_online_service':True,'real_orders':0})
            file={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css','/offline.js':'offline.js'}.get(route)
            if not file: return self.reply({'error':'Not found'},404)
            mime={'.html':'text/html','.js':'text/javascript','.css':'text/css'}[Path(file).suffix]
            return self.reply((app_root/'static'/file).read_bytes(),kind=mime+'; charset=utf-8')
        def do_POST(self):
            if not self.allowed(): return self.reply({'error':'Invalid host'},403)
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'):
                return self.reply({'error':'Foreign origin'},403)
            if self.path=='/api/refresh':
                try:
                    with urlopen(Request('http://127.0.0.1:8915/api/refresh',data=b'',method='POST'),timeout=15) as r: return self.reply(r.read())
                except Exception: return self.reply({'error':'Original monitor is unavailable'},503)
            if self.path=='/api/offline/start':
                try: return self.reply(history.start())
                except Exception: return self.reply({'error':'Freeze or history registration failed. Saved history is retained.'},409)
            length=int(self.headers.get('Content-Length','0'))
            if length<=0 or length>1_000_000: return self.reply({'error':'Invalid body size'},413)
            try: data=json.loads(self.rfile.read(length))
            except Exception: return self.reply({'error':'Invalid JSON'},400)
            if self.path=='/api/offline/connect':
                # Human must click this form. User previously reported automated
                # acceptance closing their app. Nothing is written to disk.
                if not isinstance(data.get('username'),str) or not isinstance(data.get('password'),str):
                    return self.reply({'error':'Enter your read-only Probalytics login'},400)
                credentials=(data['username'],data['password'])
                try: sql_query('SELECT 1 FORMAT JSONEachRow',credentials)
                except Exception: return self.reply({'error':'Read-only connection could not be verified. Check the login.'},409)
                with auth_lock: auth['value']=credentials;history.auth_status='CONNECTED'
                return self.reply({'status':'CONNECTED','credentials_saved':False})
            if self.path=='/api/offline/disconnect':
                with auth_lock: auth['value']=None;history.auth_status='DISCONNECTED'
                return self.reply({'status':'DISCONNECTED'})
            if self.path=='/api/offline/query':
                if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                    return self.reply({'error':'Local worker authorization required'},401)
                with auth_lock: credentials=auth['value']
                if not credentials: return self.reply({'error':'CONNECTION_REQUIRED'},409)
                try: return self.reply(sql_query(data.get('sql',''),credentials),kind='application/octet-stream')
                except ValueError: return self.reply({'error':'Unsupported or oversized archive query'},400)
                except Exception: return self.reply({'error':'Archive query failed'},502)
            return self.reply({'error':'Not found'},404)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    atomic_json(runtime/'archive-connection.json',{'query_url':f'http://127.0.0.1:{args.port}/api/offline/query','token':token,
                                                  'credentials_persisted':False})
    print(json.dumps({'url':f'http://127.0.0.1:{args.port}/','offline_release':manifest['release_id'],'online_unchanged':True}),flush=True)
    try: server.serve_forever()
    finally:
        with auth_lock: auth['value']=None
        server.server_close()
if __name__=='__main__': main()
