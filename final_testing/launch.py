"""Start the four-city dashboard and offline history from this clone."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def configuration():
    return json.loads((ROOT/'final_testing/deployment.json').read_bytes())


def environment(mode, config, online_port):
    env = os.environ.copy()
    env.pop('TEMP_DESK_PORTABLE_UNIT_TEST',None)
    env.update(TEMP_DESK_PORTABLE_ROOT=str(ROOT), TEMP_DESK_PORTABLE_MODE=mode,
        TEMP_DESK_PORTABLE_PYTHON_ALIAS=sys.executable,
        TEMP_DESK_PORTABLE_ONLINE_PORT=str(online_port),
        PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    env['PYTHONPATH'] = os.pathsep.join([str(ROOT/'final_testing/portable'), str(ROOT)])
    return env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--online-port', type=int)
    parser.add_argument('--offline-port', type=int)
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 12):
        raise SystemExit('Use Windows Python 3.12 for this frozen first release.')
    config = configuration()
    from final_testing.tools.install_application import install as verify_application
    verify_application(ROOT)
    model_root=str(ROOT/config['application_online'])
    offline_root=str(ROOT/config['application_offline'])
    runtime_root=str(ROOT/config['application_runtime'])
    online_port = args.online_port or config['online_port']
    offline_port = args.offline_port or config['offline_port']
    if online_port == offline_port or any(not 1024 <= p <= 65535 for p in (online_port, offline_port)):
        raise SystemExit('Use two distinct local ports from1024 through65535.')
    for port in (online_port, offline_port):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port)) == 0:
                raise SystemExit(f'Port {port} is already used. Choose a different pair.')
    if not (ROOT/'final_testing/assets/asset-manifest.json').is_file():
        raise SystemExit('Run final_testing/setup.ps1 first to restore the verified assets.')
    (ROOT/'runtime').mkdir(exist_ok=True)
    children = []
    entries = [('online','tempdesk.server',[
        '--release',model_root,
        '--runtime',runtime_root+'/online', '--port',str(online_port)]),
        ('offline','tempdesk.offline_server',[
        '--app-root',offline_root,'--release',model_root,
        '--runtime',runtime_root+'/offline-history','--port',str(offline_port)])]
    try:
        for mode,module,options in entries:
            children.append(subprocess.Popen([sys.executable,'-B','-m',module,*options],
                cwd=ROOT, env=environment(mode,config,online_port),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        print(f'Four-city dashboard: http://127.0.0.1:{offline_port}/?tab=offline',flush=True)
        print('Paper only. Keep this process running; Ctrl+C stops these two servers.',flush=True)
        opened = False
        while True:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError(f'Dashboard child stopped with exit code {child.returncode}.')
            if not args.no_browser and not opened:
                with socket.socket() as sock:
                    ready = sock.connect_ex(('127.0.0.1',offline_port)) == 0
                if ready:
                    webbrowser.open(f'http://127.0.0.1:{offline_port}/?tab=offline')
                    opened = True
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired: child.kill();child.wait()


if __name__ == '__main__':
    main()
