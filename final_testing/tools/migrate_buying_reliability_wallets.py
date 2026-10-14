"""Explicit upgrade for unused paper wallets; never reset or import balances.

Stop both application servers first. This tool only rebinds an existing known
application's unused $100 wallets to the registered buying-reliability release.
Active, ambiguous, or economically changed wallets require a different reviewed
migration and are deliberately refused here.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import hashlib,json,socket,sqlite3,time
from .install_application import install

ROOT=Path(__file__).resolve().parents[2]
ALLOWED_OLD={'FOUR-CITY-PAPER-BET-RANGES-v1','FOUR-CITY-PAPER-LIVE-REPAIRS-v1'}
TARGET='FOUR-CITY-PAPER-BUYING-RELIABILITY-v2'
POLICY='FOUR_CITY_BUYING_RELIABILITY_v1'
CAP_FIELDS=('wallet_id','city','profile_id','initial_cash','daily_cap','daily_fraction','daily_cash_fraction',
 'stage_fraction','stages','fees','fee_rate','fee_quantum','depth_fraction','displayed_depth_fraction',
 'sizing','rule_id','model_id','point_state_sha256','family','weather_choice','closed_drawdown_stop',
 'probability_haircut','minimum_price_cents','maximum_price_cents','minimum_probability_gap',
 'maximum_expected_net_return','minimum_expected_net_return','maximum_spread_cents','side','allocation')

def verify_compatible(old,new):
    if old.get('release_id') not in ALLOWED_OLD:raise ValueError('Previous application is not an approved upgrade source')
    if new.get('release_id')!=TARGET or new.get('buying_reliability_policy')!=POLICY:
        raise ValueError('Target is not the registered buying-reliability application')
    if any(m.get('paper_only') is not True or m.get('actual_orders')!=0 for m in (old,new)):
        raise ValueError('Only a read-only paper application can use this migration')
    before={r['path']:r['sha256'] for r in old['files'] if r['path'].startswith('vendor/')}
    after={r['path']:r['sha256'] for r in new['files'] if r['path'].startswith('vendor/')}
    if not before or before!=after:raise ValueError('Pinned native source, settings or checkpoint SHA differs')
    identity=lambda m:{p['wallet_id']:{k:p[k] for k in CAP_FIELDS if k in p} for p in m['profiles']}
    if identity(old)!=identity(new):raise ValueError('Native wallet identities, budgets, sizing, fees or models differ')
    for k in ('checkpoint_month','no_automatic_month_fallback'):
        if old.get(k)!=new.get(k):raise ValueError('Frozen checkpoint or month fallback differs')

def _unused(state,new):
    if set(state['wallets'])!={p['wallet_id'] for p in new['profiles']}:raise ValueError('Persisted wallet set differs')
    profiles={p['wallet_id']:p for p in new['profiles']}
    for wid,w in state['wallets'].items():
        p=profiles[wid]
        if (w.get('wallet_id'),w.get('city'),w.get('profile_id'))!=(wid,p['city'],p['profile_id']):
            raise ValueError('Persisted wallet identity differs')
        if Decimal(str(w.get('initial_cash','0')))!=100 or Decimal(str(w.get('cash','0')))!=100:
            raise ValueError('Only unused independent $100 wallets may be rebound; no balance reset')
        if Decimal(str(w.get('realized','0')))!=0 or Decimal(str(w.get('fees','0')))!=0:
            raise ValueError('Wallet already has a financial history')
        if any(w.get(k) for k in ('orders','positions','history','policy_state')):
            raise ValueError('Active or nonempty native wallet state requires a separate reviewed migration')
        if any(Decimal(str(v))!=0 for v in w.get('daily_spend',{}).values()):
            raise ValueError('Wallet has prior fee-inclusive spending')
    if any(Decimal(str(v.get('consumed','0')))!=0 for v in state.get('capacity',{}).values()):
        raise ValueError('Shared physical depth was already consumed')

def migrate(path,old,new):
    verify_compatible(old,new);path=Path(path);db=sqlite3.connect(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        raw=db.execute('SELECT json FROM state WHERE id=1').fetchone()[0];state=json.loads(raw)
        if state.get('release_id')==new['release_id']:
            db.rollback();return {'already_current':True,'balances_reset':False}
        if state.get('release_id')!=old['release_id']:raise ValueError('Persisted previous release differs')
        _unused(state,new)
        previous=deepcopy(state);state['release_id']=new['release_id']
        if state['wallets']!=previous['wallets'] or state.get('capacity')!=previous.get('capacity'):
            raise AssertionError('Migration must preserve every wallet and capacity record exactly')
        digest=hashlib.sha256(raw.encode()).hexdigest()
        backup=path.parent/('before-buying-reliability-'+new['release_id']+'.json')
        with backup.open('x',encoding='utf8') as handle:handle.write(raw)
        receipt={'schema':'UNUSED_PAPER_WALLET_RELEASE_REBIND_v1','from_release':old['release_id'],
                 'to_release':new['release_id'],'at_ns':time.time_ns(),'previous_state_sha256':digest,
                 'backup':backup.name,'wallets_and_capacity_exactly_preserved':True,'balances_reset':False,
                 'real_orders':0,'unused_wallets_only':True}
        db.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(state,allow_nan=False),))
        db.execute('INSERT INTO journal(at_ns,kind,payload) VALUES(?,?,?)',
                   (receipt['at_ns'],'EXPLICIT_UNUSED_BUYING_RELIABILITY_REBIND',json.dumps(receipt,allow_nan=False)))
        db.commit();return receipt
    except Exception:
        db.rollback();raise
    finally:db.close()

def _verify_files(app,manifest):
    app=app.resolve()
    for row in manifest['files']:
        path=(app/row['path']).resolve()
        if not path.is_relative_to(app) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Application file differs from its registered manifest')

def _ensure_stopped(config):
    for key in ('online_port','offline_port'):
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as probe:
            probe.settimeout(.3)
            if probe.connect_ex(('127.0.0.1',int(config[key])))==0:
                raise ValueError('Stop both application servers before rebinding paper wallets')

def main():
    config=json.loads((ROOT/'final_testing/deployment.json').read_bytes());_ensure_stopped(config)
    install(ROOT)
    app=(ROOT/config['application_online']).resolve();new=json.loads((app/'manifest.json').read_bytes())
    path=ROOT/config['application_runtime']/'online/paper.sqlite3'
    if not path.exists():print('No previous wallets; normal startup can create the registered independent wallets.');return
    db=sqlite3.connect(path)
    try:state=json.loads(db.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
    finally:db.close()
    if state.get('release_id')==new['release_id']:print('Paper wallets already use the buying-reliability application.');return
    name=state.get('release_id','')
    if name not in ALLOWED_OLD:raise ValueError('Previous application is not an approved upgrade source')
    oldapp=ROOT/'final_testing/application/releases'/name;old=json.loads((oldapp/'manifest.json').read_bytes())
    _verify_files(oldapp,old);_verify_files(app,new)
    receipt=migrate(path,old,new)
    (path.parent/('buying-reliability-upgrade-'+new['release_id']+'.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print('Unused paper wallets rebound. Cash, fees, history, native state and capacity were preserved; no balance reset.')

if __name__=='__main__':main()
