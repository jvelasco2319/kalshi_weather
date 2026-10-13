"""Build lossless, bounded Git-sized parts; never alter source bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import zipfile
from install_assets import checked_target

ROOT = Path(__file__).resolve().parents[2]
PART_BYTES = 32*1024*1024


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4*1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def main():
    manifest_path = ROOT/'final_testing/assets/asset-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get('missing'):
        raise ValueError('Do not package unresolved mandatory dependencies')
    cache = ROOT/'final_testing/.cache';cache.mkdir(parents=True,exist_ok=True)
    archive_path = cache/'frozen-assets.zip'
    packs = ROOT/'final_testing/asset_packs';packs.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
        for record in manifest['files']:
            source = checked_target(ROOT,record['path'])
            name=source.name.casefold()
            if name.startswith('.env') or any(t in name for t in ('credentials','archive-connection','private-key','connection-token')) or source.suffix.casefold() in {'.pem','.key','.pfx','.p12'}:
                raise ValueError('Private connection material cannot be packaged')
            if source.stat().st_size != record['bytes'] or digest(source) != record['sha256']:
                raise ValueError('Frozen asset changed before packaging: '+record['path'])
            archive.write(source,arcname=record['path'])
    parts = []
    with archive_path.open('rb') as source:
        index = 0
        while block := source.read(PART_BYTES):
            path = packs/f'frozen-assets.zip.part{index:03d}'
            path.write_bytes(block)
            parts.append({'path':path.name,'bytes':len(block),'sha256':hashlib.sha256(block).hexdigest()})
            index += 1
    report = {'schema':1,'created_ns':time.time_ns(),'asset_manifest_sha256':digest(manifest_path),
        'zip_sha256':digest(archive_path),'zip_bytes':archive_path.stat().st_size,'parts':parts,
        'original_files':len(manifest['files']),'original_bytes':sum(r['bytes'] for r in manifest['files']),
        'lossless':True,'source_bytes_changed':False,'credentials_included':False}
    (packs/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='parts'}))


if __name__ == '__main__':
    main()
