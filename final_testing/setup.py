"""Prepare a clone-local Windows Python3.12 environment; no model fitting.

This is an application installer, not a setuptools build entry point. --plan
prints intended commands without creating a venv, installing packages or assets.
The caller's current working directory and original author's Python paths are
irrelevant. Existing environments with system-site-package access are refused.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import argparse, hashlib, json, os, platform, struct, subprocess, sys, venv

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
ENVIRONMENT=ROOT/'.venv'
RUNTIME=HERE/'runtime'
REQUIREMENTS=HERE/'requirements.txt'
ASSET_INSTALLER=HERE/'tools/install_assets.py'

class SetupError(RuntimeError):pass

def dependencies(path=REQUIREMENTS):
    pins={}
    for line in Path(path).read_text(encoding='utf8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        if line.count('==')!=1:raise SetupError('Every dependency must have an exact version pin')
        name,version=line.split('==');pins[name]=version
    return pins

def clean_environment():
    env=os.environ.copy()
    for key in list(env):
        if key.upper() in {'PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','PYTHONUSERBASE','VIRTUAL_ENV'} or key.upper().startswith('PIP_'):
            env.pop(key,None)
    env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PIP_CONFIG_FILE=os.devnull)
    return env

def platform_check():
    if sys.version_info[:2]!=(3,12) or struct.calcsize('P')!=8 or platform.system()!='Windows':
        raise SetupError('Use 64-bit Windows Python 3.12 for this portable package')

def venv_python(root=ENVIRONMENT):return Path(root)/'Scripts/python.exe'

def cfg_values(root):
    p=Path(root)/'pyvenv.cfg'
    if not p.is_file():raise SetupError('Existing virtual environment has no pyvenv.cfg')
    return {k.strip().lower():v.strip() for line in p.read_text(encoding='utf8').splitlines()
        if '=' in line for k,v in [line.split('=',1)]}

def verify_environment(root):
    root=Path(root).resolve();python=venv_python(root)
    if cfg_values(root).get('include-system-site-packages','').lower()!='false':
        raise SetupError('The clone needs a clean virtual environment without system packages')
    code="import json,sys; print(json.dumps({'version':list(sys.version_info[:2]),'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
    p=subprocess.run([str(python),'-I','-B','-c',code],cwd=ROOT,env=clean_environment(),capture_output=True,text=True,check=True)
    info=json.loads(p.stdout)
    if info['version']!=[3,12] or Path(info['prefix']).resolve()!=root or info['prefix']==info['base_prefix']:
        raise SetupError('The selected Python is not the clone-local Python3.12 virtual environment')
    return info

def command_plan(wheelhouse=None):
    python=venv_python()
    pip=[str(python),'-I','-B','-m','pip','--isolated','--disable-pip-version-check','install',
        '--require-virtualenv','--no-input','--only-binary=:all:','--retries','2','--timeout','30','-r',str(REQUIREMENTS)]
    if wheelhouse:
        pip+=['--no-index','--find-links',str(Path(wheelhouse).resolve())]
    else:pip+=['--index-url','https://pypi.org/simple']
    return {'repo_root':str(ROOT),'venv_root':str(ENVIRONMENT),'runtime_root':str(RUNTIME),
        'python_baseline':'64-bit Windows CPython3.12','dependencies':dependencies(),
        'commands':[[sys.executable,'-B','-m','venv',str(ENVIRONMENT)],pip,[str(python),'-I','-B',str(ASSET_INSTALLER)]],
        'assets_installer_present':ASSET_INSTALLER.is_file(),'fits':0,'orders':0,
        'checkpoint_month':'2026-10','entry_pause':'NWS_PHYSICAL_TARGET_TWC_EQUIVALENCE_UNPROVED'}

def run(command):
    subprocess.run(command,cwd=ROOT,env=clean_environment(),check=True)

def installed_versions(python,pins):
    code=("import importlib.metadata,json; names="+repr(list(pins))+"; "
          "print(json.dumps({name:importlib.metadata.version(name) for name in names}))")
    p=subprocess.run([str(python),'-I','-B','-c',code],cwd=ROOT,env=clean_environment(),capture_output=True,text=True,check=True)
    actual=json.loads(p.stdout)
    if actual!=pins:raise SetupError('Installed dependency versions differ from the saved numerical runtime')
    return actual

def install(wheelhouse=None,skip_install=False):
    platform_check();plan=command_plan(wheelhouse)
    if not ASSET_INSTALLER.is_file():raise SetupError('Verified model asset installer is missing; the package is incomplete')
    if wheelhouse and not Path(wheelhouse).is_dir():raise SetupError('Wheelhouse directory does not exist')
    if not venv_python().is_file():
        if ENVIRONMENT.exists() and any(ENVIRONMENT.iterdir()):
            raise SetupError('An incomplete .venv already exists. Rename it before retrying setup')
        venv.EnvBuilder(with_pip=True,system_site_packages=False,clear=False).create(ENVIRONMENT)
    info=verify_environment(ENVIRONMENT)
    if not skip_install:run(plan['commands'][1])
    actual=installed_versions(venv_python(),plan['dependencies'])
    run(plan['commands'][2])
    RUNTIME.mkdir(parents=True,exist_ok=True)
    python=venv_python().resolve()
    receipt={'schema':'PORTABLE_SETUP_RUNTIME_V1','prepared_utc':datetime.now(timezone.utc).isoformat(),
        'repo_root':str(ROOT),'venv_root':str(ENVIRONMENT),'interpreter':str(python),
        'interpreter_sha256':hashlib.sha256(python.read_bytes()).hexdigest(),'bootstrap_python':platform.python_version(),
        'python_environment':info,'dependencies':actual,'system_site_packages':False,'assets_installed':True,
        'checkpoint_month':'2026-10','paper_only':True,'real_orders':0,'new_fits':0,
        'entry_pause':'NWS_PHYSICAL_TARGET_TWC_EQUIVALENCE_UNPROVED',
        'note':'Records this installation; it does not require the original author\'s executable hash.'}
    temp=RUNTIME/'setup-runtime.json.tmp';temp.write_text(json.dumps(receipt,indent=2),encoding='utf8');os.replace(temp,RUNTIME/'setup-runtime.json')
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',action='store_true',help='Print intended commands without changing files')
    parser.add_argument('--wheelhouse',help='Optional directory containing the exact pinned wheels')
    parser.add_argument('--skip-install',action='store_true',help='Check an already prepared clean venv, then verify/install local assets')
    args=parser.parse_args()
    try:
        if args.plan:print(json.dumps(command_plan(args.wheelhouse),indent=2));return 0
        result=install(args.wheelhouse,args.skip_install)
        print(json.dumps({'status':'READY','runtime_receipt':str(RUNTIME/'setup-runtime.json'),
            'checkpoint_month':result['checkpoint_month'],'paper_only':True,'next':'Run final_testing/Start.ps1'},indent=2));return 0
    except (SetupError,subprocess.CalledProcessError,OSError,ValueError) as error:
        print('Setup stopped: '+str(error),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
