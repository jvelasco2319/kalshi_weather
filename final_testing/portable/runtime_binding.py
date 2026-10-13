"""Admit a relocated deployment without pretending it is the original runtime.

Source/model/data files remain byte-identical, including their source receipts.
Only the interpreter/library deployment and local HTTP ports are rebound.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import urllib.request

_INSTALLED = False


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    if sys.version_info[:2] != (3, 12) or sys.platform != 'win32':
        raise RuntimeError('This frozen deployment currently requires Windows Python 3.12.')
    from final_testing.portable.mount import physical
    config_path = Path(os.environ['TEMP_DESK_PORTABLE_ROOT'])/'final_testing/deployment.json'
    config = json.loads(config_path.read_bytes())
    expected_packages = {}
    for line in (config_path.parent/'requirements.txt').read_text(encoding='utf8').splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name,version=line.strip().split('==');expected_packages[name]=version
    installed_packages={name:importlib.metadata.version(name) for name in expected_packages}
    if installed_packages!=expected_packages:
        raise RuntimeError('Installed packages differ from the registered deployment.')
    for root_key,file_name,hash_key in (
        ('model_legacy_root','manifest.json','source_model_manifest_sha256'),
        ('offline_legacy_root','offline-manifest.json','source_offline_manifest_sha256')):
        actual=hashlib.sha256(Path(config[root_key],file_name).read_bytes()).hexdigest()
        if actual!=config[hash_key]:
            raise RuntimeError('A registered frozen release differs from this deployment.')
    app_key = 'offline_legacy_root' if os.environ.get('TEMP_DESK_PORTABLE_MODE') == 'offline' else 'model_legacy_root'
    from final_testing.tools.install_application import install as verify_application
    verify_application(Path(os.environ['TEMP_DESK_PORTABLE_ROOT']), quiet=True, restore=False)
    # Verification must stay silent in city workers whose stdout is JSON only.
    app = Path(os.environ['TEMP_DESK_PORTABLE_ROOT']) / config['application_offline' if os.environ.get('TEMP_DESK_PORTABLE_MODE') == 'offline' else 'application_online']
    if str(app) not in sys.path:
        sys.path.insert(0, str(app))
    bridge = importlib.import_module('tempdesk.bridge')
    original_init = bridge.Release.__init__
    interpreter_sha = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()

    def portable_init(self, root):
        original_init(self, root)
        original_runtime = self.manifest.get('python')
        self.manifest['source_registered_runtime'] = original_runtime
        self.manifest['python'] = {'path': sys.executable, 'sha256': interpreter_sha}
        self.manifest['library_path'] = os.pathsep.join(p for p in sys.path if p and 'site-packages' in p)
        self.manifest['portable_deployment'] = {
            'mode': 'SOURCE_IDENTICAL_NEW_RUNTIME_BINDING',
            'actual_python_version': sys.version,
            'actual_python_sha256': interpreter_sha,
            'actual_packages': installed_packages,
            'source_manifest_sha256': self.manifest_sha256,
            'source_runtime_identity_claimed': False,
            'model_weights_changed': False,
        }

    bridge.Release.__init__ = portable_init
    if os.environ.get('TEMP_DESK_PORTABLE_MODE') == 'offline':
        # Resolve the sealed application's explicitly relative model reference
        # for LA's unchanged gate. This is deployment metadata, not model math.
        offline_la = importlib.import_module('tempdesk.offline_la')
        original_read = offline_la._read
        model_root = Path(os.environ['TEMP_DESK_PORTABLE_ROOT']) / config['application_online']
        expected_reference = 'REPO_RELATIVE:' + config['application_online']
        def application_read(path):
            value = original_read(path)
            if Path(path).resolve() == (app/'offline-manifest.json').resolve():
                if (value.get('model_release_path') != expected_reference or
                    value.get('model_manifest_sha256') != config['application_model_sha256']):
                    raise RuntimeError('Portable offline model binding differs from registration.')
                value = {**value, 'model_release_path': str(model_root)}
            return value
        offline_la._read = application_read
    original_urlopen = urllib.request.urlopen
    port = int(os.environ.get('TEMP_DESK_PORTABLE_ONLINE_PORT', config['online_port']))
    if not 1024 <= port <= 65535:
        raise ValueError('Invalid local online port')

    def local_urlopen(request, *args, **kwargs):
        old = 'http://127.0.0.1:8915/'
        new = f'http://127.0.0.1:{port}/'
        if isinstance(request, str) and request.startswith(old):
            request = new + request[len(old):]
        elif isinstance(request, urllib.request.Request) and request.full_url.startswith(old):
            request = urllib.request.Request(new + request.full_url[len(old):],
                data=request.data, headers=dict(request.header_items()), method=request.get_method())
        return original_urlopen(request, *args, **kwargs)

    urllib.request.urlopen = local_urlopen
    os.environ['TEMP_DESK_PORTABLE_ADMITTED']='1'
    _INSTALLED = True
