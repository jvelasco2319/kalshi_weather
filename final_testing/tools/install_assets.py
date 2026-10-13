"""Restore lossless, hash-checked frozen assets from the branch's small parts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4*1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def checked_target(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts[:2] == ('final_testing','assets'):
        raise ValueError('Unsafe asset path: '+relative)
    target = (root/path).resolve()
    if not target.is_relative_to((root/'final_testing/assets').resolve()):
        raise ValueError('Asset escaped its directory')
    return target


def install(root=ROOT):
    root = Path(root).resolve()
    manifest_path = root/'final_testing/assets/asset-manifest.json'
    pack_root = root/'final_testing/asset_packs'
    package = json.loads((pack_root/'manifest.json').read_bytes())
    if package.get('schema') != 1 or digest(manifest_path) != package['asset_manifest_sha256']:
        raise ValueError('Frozen asset registration changed')
    registry = json.loads(manifest_path.read_bytes())
    if registry.get('missing'):
        raise ValueError('Incomplete export cannot be installed')
    records = {r['path']:r for r in registry['files']}
    if len(records) != len(registry['files']):
        raise ValueError('Duplicate physical asset names')
    ready = True
    for name,record in records.items():
        target = checked_target(root,name)
        if not target.is_file():
            ready = False
        elif target.stat().st_size != record['bytes'] or digest(target) != record['sha256']:
            raise ValueError('Existing frozen file changed: '+name)
    if ready:
        print('All frozen assets are already restored and verified.')
        return {'files':len(records),'reused':True}
    cache = root/'final_testing/.cache';cache.mkdir(parents=True,exist_ok=True)
    archive_path = cache/'frozen-assets.zip'
    whole = hashlib.sha256();size = 0
    with archive_path.open('wb') as archive:
        for part in package['parts']:
            name = Path(part['path'])
            if name.is_absolute() or '..' in name.parts or len(name.parts) != 1:
                raise ValueError('Unsafe pack part')
            path = pack_root/name
            if path.stat().st_size != part['bytes'] or digest(path) != part['sha256']:
                raise ValueError('Asset pack part changed: '+str(name))
            with path.open('rb') as source:
                for block in iter(lambda: source.read(4*1024*1024),b''):
                    whole.update(block);size += len(block);archive.write(block)
    if whole.hexdigest() != package['zip_sha256'] or size != package['zip_bytes']:
        raise ValueError('Lossless asset archive does not match its seal')
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) != len(records) or set(e.filename for e in entries) != set(records):
            raise ValueError('Unexpected or duplicate archive member')
        for entry in entries:
            record = records[entry.filename]
            if entry.file_size != record['bytes'] or (entry.external_attr>>16)&0o170000 == 0o120000:
                raise ValueError('Unsupported asset member')
            target = checked_target(root,entry.filename)
            if target.exists():
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            fd,temporary_name=tempfile.mkstemp(prefix='.restore-',dir=target.parent)
            temporary=Path(temporary_name)
            import os
            with archive.open(entry) as source, os.fdopen(fd,'wb') as output:
                shutil.copyfileobj(source,output,4*1024*1024)
            if digest(temporary) != record['sha256']:
                raise ValueError('Restored asset does not match original bytes')
            temporary.replace(target)
    print(f'Restored {len(records)} original source/model/offline asset files.')
    return {'files':len(records),'reused':False}


if __name__ == '__main__':
    install()
