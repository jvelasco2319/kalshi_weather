"""Restore unchanged native vendor bytes into the new application release.

Application code is ordinary reviewable Git source. Large frozen native assets
come from the original verified lossless packs; never use the author's PC.
"""
from pathlib import Path
import hashlib, json, shutil

ROOT = Path(__file__).resolve().parents[2]

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def install(root=ROOT, *, quiet=False, restore=True):
    root=Path(root).resolve();config=json.loads((root/'final_testing/deployment.json').read_bytes())
    spec=json.loads((root/'final_testing/application/registration.json').read_bytes())
    if sha(root/'final_testing/application/registration.json') != config['application_registration_sha256']:
        raise ValueError('Application registration changed')
    for item in spec['native_copies']:
        # The read-only mount deliberately resolves an exported asset back to
        # its logical provenance name. Validate these finite relative paths
        # lexically before the mount redirects reads; never follow author paths.
        if any(Path(item[k]).is_absolute() or '..' in Path(item[k]).parts for k in ('source','target')):
            raise ValueError('Unsafe application asset path')
        source=root/item['source'];target=root/item['target']
        if not source.is_relative_to(root/'final_testing/assets') or not target.is_relative_to(root/'final_testing/application/releases'):
            raise ValueError('Application asset path escaped its registered roots')
        if sha(source)!=item['sha256']:raise ValueError('Original native asset changed')
        if target.exists():
            if sha(target)!=item['sha256']:raise ValueError('Restored native application asset changed')
        else:
            if not restore:raise ValueError('Run setup to restore the application native assets')
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for key,name,digest_key in [('application_online','manifest.json','application_model_sha256'),
                                 ('application_offline','offline-manifest.json','application_offline_sha256')]:
        app=root/config[key]
        if sha(app/name)!=config[digest_key]:raise ValueError('Application manifest changed')
        for item in json.loads((app/name).read_bytes())['files']:
            path=(app/item['path']).resolve()
            if not path.is_relative_to(app) or sha(path)!=item['sha256']:
                raise ValueError('Application file changed: '+item['path'])
    if not quiet:print('Application code and unchanged native assets verified.')
    return {'native_copies':len(spec['native_copies']),'paper_only':True}

if __name__=='__main__':install()
