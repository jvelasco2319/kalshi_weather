"""Rebind a UI-only release while preserving every paper-wallet record."""
from pathlib import Path
import hashlib,json,sqlite3,time
from copy import deepcopy
from .install_application import install
ROOT=Path(__file__).resolve().parents[2]

def verify_ui_only(old,new):
    before={r['path']:r['sha256'] for r in old['files']}
    after={r['path']:r['sha256'] for r in new['files']}
    if set(before)!=set(after) or any(before[p]!=after[p] for p in before if p not in ('static/app.js','static/styles.css')):
        raise ValueError('This upgrade changes executable financial/source code')
    ignored={'release_id','created_ns','files','parent_release_id','parent_manifest_sha256'}
    if {k:v for k,v in old.items() if k not in ignored}!={k:v for k,v in new.items() if k not in ignored}:
        raise ValueError('This upgrade changes model, wallet or trading controls')

def migrate(path,old,new):
    verify_ui_only(old,new)
    db=sqlite3.connect(path)
    try:
        raw=db.execute('SELECT json FROM state WHERE id=1').fetchone()[0];state=json.loads(raw)
        if state.get('release_id')==new['release_id']:return {'already_current':True}
        if state.get('release_id')!=old['release_id']:raise ValueError('Previous paper release differs')
        if set(state['wallets'])!={p['wallet_id'] for p in new['profiles']}:raise ValueError('Wallet identities differ')
        previous=deepcopy(state);state['release_id']=new['release_id']
        assert state['wallets']==previous['wallets'] and state.get('capacity')==previous.get('capacity')
        backup=Path(path).parent/('before-ui-'+new['release_id']+'.json')
        if backup.exists():raise ValueError('Existing migration backup must be retained')
        backup.write_text(raw,encoding='utf-8')
        receipt={'from_release':old['release_id'],'to_release':new['release_id'],'at_ns':time.time_ns(),
                 'wallets_and_capacity_exactly_preserved':True,'real_orders':0}
        with db:
            current=db.execute('SELECT json FROM state WHERE id=1').fetchone()[0]
            if current!=raw:raise ValueError('Stop the server before this migration')
            db.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(state),))
            db.execute('INSERT INTO journal(at_ns,kind,payload) VALUES(?,?,?)',(receipt['at_ns'],'EXPLICIT_UI_ONLY_RELEASE_MIGRATION',json.dumps(receipt)))
        return receipt
    finally:db.close()

def main():
    install(ROOT);config=json.loads((ROOT/'final_testing/deployment.json').read_bytes())
    app=ROOT/config['application_online'];new=json.loads((app/'manifest.json').read_bytes())
    path=ROOT/config['application_runtime']/'online/paper.sqlite3'
    if not path.exists():print('No previous wallets; normal startup is ready.');return
    db=sqlite3.connect(path)
    try:state=json.loads(db.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
    finally:db.close()
    if state.get('release_id')==new['release_id']:print('Paper wallets already use the current UI.');return
    name=state.get('release_id','')
    if not name or Path(name).name!=name:raise ValueError('Unexpected previous release')
    oldapp=ROOT/'final_testing/application/releases'/name
    old=json.loads((oldapp/'manifest.json').read_bytes())
    for row in old['files']:
        file=(oldapp/row['path']).resolve()
        if not file.is_relative_to(oldapp) or hashlib.sha256(file.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Previous application file differs')
    receipt=migrate(path,old,new)
    print('UI upgraded; all paper cash, orders, positions, history and capacity preserved.')
    (path.parent/('ui-upgrade-'+new['release_id']+'.json')).write_text(json.dumps(receipt,indent=2),encoding='utf-8')

if __name__=='__main__':main()
