"""Isolated native checks against the relocated immutable source bundle."""
from __future__ import annotations
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('describe','history'))
    parser.add_argument('--end-day',default='2026-10-12')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if os.environ.get('TEMP_DESK_PORTABLE_ADMITTED')!='1':
        raise RuntimeError('The relocated runtime was not admitted before execution')
    root=Path(os.environ['TEMP_DESK_PORTABLE_ROOT'])
    config=json.loads((root/'final_testing/deployment.json').read_bytes())
    from tempdesk.bridge import Release
    release=Release(config['model_legacy_root'])
    if release.manifest_sha256!=config['source_model_manifest_sha256']:
        raise ValueError('Source model release identity differs')
    begin=time.time_ns()
    if args.action=='describe':
        results={}
        for city in ('la','ny','miami','phoenix'):
            results[city]=release.call(city,{'action':'describe'},config['runtime_legacy_root']+'/portable-check',timeout=180)
            response=results[city]
            expected={p['profile_id'] for p in release.manifest['profiles'] if p['city']==city}
            got={p['profile_id'] for p in response.get('profiles',[])}
            if expected!=got or response.get('error_type') or response.get('status') in ('UNKNOWN','BLOCKED','ERROR'):
                raise ValueError('Frozen city descriptions are incomplete: '+city)
        reply={'passes':True,'action':'describe','results':results,'elapsed_seconds':(time.time_ns()-begin)/1e9,
               'source_model_manifest_sha256':release.manifest_sha256,'new_fits':0,'real_orders':0}
    else:
        from tempdesk.offline_history import History
        app=Path(config['offline_legacy_root'])
        history=History(app,config['model_legacy_root'],config['runtime_legacy_root']+'/offline-history')
        state=history.start(args.end_day)
        deadline=time.monotonic()+1800
        while state['job']['status'] in ('RUNNING','QUEUED') and time.monotonic()<deadline:
            time.sleep(.5);state=history.snapshot()
        if state['job']['status']!='FINISHED':
            raise ValueError('Relocated replay did not finish: '+json.dumps(state['job']))
        expected=json.loads((root/'final_testing/expected-history.json').read_bytes())
        if args.end_day!=expected['end_day']:
            raise ValueError('This reproducibility check has a fixed registered historical endpoint')
        observed={p['wallet_id']:p for p in state['profiles']}
        if set(observed)!=set(expected['profiles']):
            raise ValueError('The nine archived strategy identities differ')
        for wallet,baseline in expected['profiles'].items():
            got=observed[wallet]
            for key in ('known_cash','ending_cash','wins','losses','contracts','fees','reserved'):
                a,b=got.get(key),baseline.get(key)
                if (a is None)!=(b is None) or (a is not None and Decimal(str(a))!=Decimal(str(b))):
                    raise ValueError(f'Relocated {wallet} differs in {key}: {a} vs {b}')
            for day,reference in zip(got['days'],baseline['days'],strict=True):
                for key in ('day','status'):
                    if day[key]!=reference[key]:raise ValueError(f'Relocated {wallet} calendar evidence differs')
                for key in ('known_cash','ending_cash','contracts','fees','wins','losses'):
                    a,b=day.get(key),reference.get(key)
                    if (a is None)!=(b is None) or (a is not None and Decimal(str(a))!=Decimal(str(b))):
                        raise ValueError(f'Relocated {wallet} {day["day"]} differs in {key}')
        if not any(p.get('contracts',0) for p in state['profiles']):
            raise ValueError('An all-unknown/no-execution history cannot establish relocation parity')
        reply={'passes':True,'action':'history','snapshot':state,'elapsed_seconds':(time.time_ns()-begin)/1e9,
               'nine_archived_wallets_and_daily_evidence_match':True,'new_fits':0,'real_orders':0}
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(reply,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps({'passes':reply['passes'],'action':args.action,'output':str(path),'elapsed_seconds':reply['elapsed_seconds']}))


if __name__=='__main__':
    main()
