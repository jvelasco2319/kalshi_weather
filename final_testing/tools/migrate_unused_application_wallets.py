"""Explicit unused-wallet upgrade. Active paper positions/history never migrate."""
from pathlib import Path
import hashlib,json,sqlite3,time
from .install_application import install
ROOT=Path(__file__).resolve().parents[2]

def main():
 install(ROOT)
 config=json.loads((ROOT/'final_testing/deployment.json').read_bytes())
 manifest=json.loads((ROOT/config['application_online']/'manifest.json').read_bytes())
 path=ROOT/config['application_runtime']/'online/paper.sqlite3'
 if not path.exists():print('No previous paper wallets; normal startup is ready.');return
 db=sqlite3.connect(path)
 try:
  raw=db.execute('SELECT json FROM state WHERE id=1').fetchone()[0];state=json.loads(raw)
  if state.get('release_id')==manifest['release_id']:print('Paper wallets already belong to this application.');return
  if state.get('release_id')!='FOUR-CITY-PAPER-TWC-EXIT-v1':raise ValueError('Unexpected previous paper release')
  oldapp=ROOT/'final_testing/application/releases'/state['release_id']
  old=json.loads((oldapp/'manifest.json').read_bytes())
  oldmap=json.loads((oldapp/'source-map.json').read_bytes())['mapping'];newmap=json.loads((ROOT/config['application_online']/'source-map.json').read_bytes())['mapping']
  if {k:v['sha256'] for k,v in oldmap.items()}!={k:v['sha256'] for k,v in newmap.items()}:raise ValueError('Native model identity differs')
  expected={p['wallet_id'] for p in manifest['profiles']}
  if set(state['wallets'])!=expected or len(expected)!=9:raise ValueError('Unexpected wallet identities')
  for w in state['wallets'].values():
   if w['orders'] or w['positions'] or w['history'] or w['cash']!='100' or w['fees']!='0' or w['realized']!='0':raise ValueError('Active wallets require a separate reviewed migration; retain the previous application')
  receipt=dict(at_ns=time.time_ns(),from_release=state['release_id'],to_release=manifest['release_id'],wallets_preserved=9,cash_before='900',cash_after='900',old_state_sha256=hashlib.sha256(raw.encode()).hexdigest(),actual_orders=0)
  backup=path.parent/('before-'+manifest['release_id']+'.json')
  if backup.exists():raise ValueError('Existing upgrade backup must not be overwritten')
  backup.write_text(raw,encoding='utf8');state['release_id']=manifest['release_id']
  with db:
   db.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(state),))
   db.execute('INSERT INTO journal(at_ns,kind,payload) VALUES(?,?,?)',(receipt['at_ns'],'EXPLICIT_UNUSED_APPLICATION_REPAIR_MIGRATION',json.dumps(receipt)))
  (path.parent/'repair-migration.json').write_text(json.dumps(receipt,indent=2),encoding='utf8');print('Nine unused $100 wallets preserved and rebound to the repaired application.')
 finally:db.close()

if __name__=='__main__':main()
