"""Inventory/copy a finite, pinned operational asset closure without rewriting it.

Default execution is inventory only. The coordinator reviews its size/missing
list before invoking --copy. Raw assets are later losslessly packed by the
repository's separate chunked-release helper; this tool never uploads anything.
"""
from __future__ import annotations

import argparse
import ast
from collections import deque
from datetime import date
import glob
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import sys

CODEX = Path('C:/Users/jarve/Documents/Codex')
BOT = CODEX / 'Kalshi Temp Bot'
DASH = BOT / 'Dashboard'
LIVE = DASH / 'releases/FOUR-CITY-PAPER-F1F-20261012'
OFFLINE = DASH / 'offline_releases/FOUR-CITY-OFFLINE-H1B-20261012'
MIAMI = BOT / 'Miami/Frozen/MIAMI-V10-P09-v1'
MIA_CAMPAIGN = BOT / 'Development/Miami/MIAMI-REALITY-SIM-v1-20261012'
PHX_OLD = BOT / 'Development/Phoenix/PHOENIX-OCT1-10-SAFETY-REPLAY-v1-20261011'
PHX_PRIOR = BOT / 'Development/Phoenix/PHOENIX-REALITY-SIM-v1-20261012'
NY_READY = BOT / 'Development/New York/NY-REALITY-SIM-v1-20261012/source-admission-ready-v2.json'
ROOTS = {
    # Longest approved prefix wins. These two deep roots retain the original
    # identity/bytes while leaving room for Windows' atomic-copy suffix.
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard/runtime/offline-history/history': 'final_testing/assets/oh',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard/runtime/offline-history': 'final_testing/assets/o',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard/runtime': 'final_testing/assets/r',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard': 'final_testing/assets/d',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles/runs/la-paper-freeze-20261008/source-bytes': 'final_testing/assets/ls',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles': 'final_testing/assets/l',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/New York': 'final_testing/assets/n',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Miami': 'final_testing/assets/m',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Phoenix': 'final_testing/assets/p',
    'C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Development': 'final_testing/assets/dev',
    'C:/Users/jarve/Documents/Codex/Kalshi/_weather/_llm': 'final_testing/assets/w',
    'C:/Users/jarve/Documents/Codex/2026-10-07/kalshi-nyc-generalization': 'final_testing/assets/g',
    'C:/Users/jarve/Documents/Codex': 'final_testing/assets/codex',
    'C:/Users/jarve/anaconda3/Library/bin': 'final_testing/assets/anaconda-bin',
    'C:/Users/jarve/.cache/codex-runtimes/codex-primary-runtime': 'final_testing/assets/codex-runtime',
}
MAX_JSON = 8_000_000
MAX_JSON_NODES = 500_000
MAX_FILES = 12_000
MAX_BYTES = 5_000_000_000
GIT_FILE_WARNING = 100_000_000
DB_ARCHIVE_NAMES = {'quote-index.sqlite3', 'selected-native-rows.sqlite3'}
PROVENANCE_KEYS = {
    'source_ref', 'source_refs', 'source_locators', 'locators',
    'provenance', 'lineage', 'parent_artifacts', 'source_parent_pins',
    'raw_source_ref', 'raw_source_refs', 'native_selected_snapshot_evidence',
    'source_pins', 'parent_raw_parquet_paths', 'raw_parquet_parents',
}
ENV_KEYS = {'password', 'passwd', 'db_password', 'api_key', 'secret_key',
            'private_key', 'access_token', 'refresh_token', 'bearer_token'}
ESSENTIAL_CODECS = set()
OPERATIONAL_JSON = {'source-map.json', 'compiled_manifest.json', 'saved-calibration-bundle.json',
                    'source-bindings.json', 'source_bindings.json'}


def canonical(path):
    return str(Path(path).resolve()).replace('\\', '/')


def windows_path(value):
    return isinstance(value, str) and bool(re.match(r'^[A-Za-z]:[\\/]', value))


def original_identity(value):
    return ntpath.normpath(value).replace('\\', '/')


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.inventory.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf8')
    os.replace(temporary, path)


def blocked(path, *, declared_codec=False):
    """File identities only; no credential files are opened for inspection."""
    p = Path(path)
    name = p.name.lower()
    parts = {x.lower() for x in p.parts}
    if parts & {'.git', '__pycache__', 'node_modules', '.aws', '.ssh'}:
        return 'PRIVATE_OR_GENERATED_DIRECTORY'
    if name.startswith('.env') or p.suffix.lower() in {'.pem', '.key', '.pfx', '.p12', '.log', '.pyc'}:
        return 'SECRET_OR_LOG_OR_GENERATED_FILE'
    if any(word in name for word in ('archive-connection', 'bridge-start', 'credentials', 'credential', 'connect-token', 'connection-token', 'login-details')):
        return 'PRIVATE_CONNECTION_FILE'
    if name in {'server-process.json', 'connection.json', 'connection-info.json', 'connect.html', 'accept.html'}:
        return 'LOCAL_SERVER_CONNECTION_STATE'
    if p.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.db-wal', '.db-shm'} and name not in DB_ARCHIVE_NAMES:
        return 'ONLINE_OR_UNDECLARED_DATABASE'
    if name in DB_ARCHIVE_NAMES and 'sources' not in parts:
        return 'DATABASE_OUTSIDE_IMMUTABLE_SOURCE_CLOSURE'
    if p.suffix.lower() in {'.exe'}:
        return 'REINSTALLABLE_INTERPRETER_OR_TOOL'
    if ('.venv' in parts or 'site-packages' in parts or 'codex-primary-runtime' in parts) and not declared_codec:
        return 'REINSTALLABLE_ENVIRONMENT_NOT_AN_ASSET'
    return None


def default_seeds():
    """Current UI, October replay inputs and exact saved-test fixtures only."""
    rows = []
    def add(path, **extra):
        rows.append({'path': str(path), 'required': True, **extra})
    for folder in (LIVE, OFFLINE):
        for p in sorted(folder.rglob('*')):
            if p.is_file():
                selected_json = p == LIVE / 'manifest.json' or p == OFFLINE / 'offline-manifest.json' or p.name in OPERATIONAL_JSON
                add(p, follow_json=selected_json and 'evidence' not in p.parts)
    # The nine expressly declared codec/CFFI byte bindings, not their envs.
    if (LIVE / 'manifest.json').is_file():
        for item in read_json(LIVE / 'manifest.json').get('runtime_pins', []):
            if item.get('path'):
                ESSENTIAL_CODECS.add(original_identity(item['path']).lower())
                add(item['path'], sha256=item.get('sha256'), declared_codec=True, follow_json=False)
    mia_data = [MIAMI / 'runtime/reference_trader/kernels/engine.py', MIAMI / 'FROZEN-MANIFEST.json',
        MIAMI / 'experiments/MIAMI-V10-P09-v2-1h/model/portable-v2/dated-layouts.json',
        MIAMI / 'sources/month-10/all-six-native-v1/quote-index.sqlite3',
        MIAMI / 'sources/month-10/all-six-native-v1/selected-native-rows.sqlite3',
        MIA_CAMPAIGN / 'sources/october-packet-v1.json', MIA_CAMPAIGN / 'sources/october-tail-books-v1.json',
        MIA_CAMPAIGN / 'sources/october-source-pins-v1.json']
    mia_data += [MIAMI / f'weather/daily/2026-10-{i:02d}.json' for i in range(1, 9)]
    mia_data += [MIA_CAMPAIGN / f'sources/oct-tail/joined-weather/2026-10-{i:02d}.json' for i in range(9, 12)]
    mia_data += [MIA_CAMPAIGN / f'sources/oct-tail/forecasts/{p}.json' for p in ('SELECTED_CHAMPION', 'RAW_ORIGINAL')]
    mia_data += [MIA_CAMPAIGN / f'runs/2026-10/FULL_NATIVE/{p}/{s}' for p in ('SELECTED_CHAMPION', 'RAW_ORIGINAL') for s in ('metrics-v1.json', 'wallets/wallets.json')]
    mia_data += [BOT / 'Development/Shared/MULTICITY-REALITY-SIM-v1-20261012/sources/kalshi-official/KXHIGHMIA-26OCT11.json',
        MIAMI / 'experiments/MIAMI-V10-P09-v2-1h/model/portable-v2/forecasts/2026-10.json',
        MIAMI / 'model/fit-run-v1/2026-10/forecasts.json']
    for p in mia_data:
        add(p, follow_json=False)  # Old authority/lineage paths are opaque.
    for p in (MIA_CAMPAIGN / 'results').glob('*'):
        if p.is_file():
            add(p, follow_json=False)
    for filename in ('test_miami_adapter.py', 'test_offline_miami.py'):
        add(DASH / 'tests' / filename, follow_json=False)
    # NYC audited dictionaries are usable directly; parent Parquet references
    # are provenance and are not reopened by the current offline kernel.
    if NY_READY.is_file():
        add(NY_READY, follow_json=False)
        for day, group in read_json(NY_READY)['files'].items():
            if not day.startswith('2026-10-') or day > '2026-10-11':
                continue
            for key in ('forecast', 'books'):
                item = group[key]
                add(item['path'], sha256=item.get('sha256'), follow_json=False)
    else:
        add(NY_READY, follow_json=False)
    phx = [PHX_OLD / f'sources/{s}' for s in ('raw14-oct1-10.json', 'ensemble-oct1-10.json')]
    phx += [PHX_OLD / f'sources/retained-clock-books/2026-10-{i:02d}.json' for i in range(1, 11)]
    phx += [PHX_OLD / f'sources/kalshi-official/KXHIGHTPHX-26OCT{i:02d}.json' for i in range(1, 10)]
    phx += [PHX_OLD / f'sources/kalshi-official/{s}' for s in ('KXHIGHTPHX-26OCT10.json', 'KXHIGHTPHX-26OCT10-settlement-refresh.json', 'KXHIGHTPHX-26OCT10-settlement-refresh.receipt.json')]
    phx += [PHX_PRIOR / f'inputs/{s}' for s in ('oct11-native-books-v1.json', 'oct11-official-market-v1.json', 'oct11-forecasts-v1.json')]
    phx += [PHX_PRIOR / f'results/october/{s}' for s in ('03-FULL_TOP.json', '01-FULL_TOP.json')]
    phx += [BOT / 'Phoenix/Development/PHOENIX-V10-P09-v1/runtime/reference_trader/kernels/engine.py']
    phx += [BOT / 'Development/Phoenix/PHOENIX-ORIGINAL-DEMO-v2-20261011/pinned/numerical-fixtures/oct09.json']
    for p in phx:
        add(p, follow_json=False)
    dlls = {'blosc.dll': 'a21580c0c6eb967a9442f4e0559203c99781a530679bd8c7c6c408c4f27c7b11',
        'liblz4.dll': '201269203caf42be76ef92c36a1cfbb9680e744bc0b28459849c0ba74b5748c3',
        'zlib.dll': 'ab781e8b12674b578eb8db318f933b3d7e9b68eeb67337f259d76d3e7d722ba4',
        'zstd.dll': 'be5a011e36657a266e96bf287cf4ff806dcec43b5b720bb1e80d10a28f91f294',
        'VCRUNTIME140.dll': '0205071c36c17f1efbd70178c852cb7d49985c484202752b8704b7ac6b184e60'}
    for name, sha in dlls.items():
        add(Path('C:/Users/jarve/anaconda3/Library/bin') / name, sha256=sha, declared_codec=True, follow_json=False)
    # Saved History results/captures from the currently accepted offline UI.
    # No whole runtime sweep: connection JSON, online wallets and logs excluded.
    history = DASH / 'runtime/offline-history'
    for name in ('history.json', 'acceptance.json', 'browser-qa-v1.json', 'copied-H1B-button-validation-v1.json'):
        if (history / name).is_file():
            add(history / name, follow_json=False)
    for folder in (history / 'sources', history / 'history', history / 'verification', history / 'jobs'):
        for p in sorted(folder.rglob('*')):
            if p.is_file() and not blocked(p):
                add(p, follow_json=False)
    return rows


class Inventory:
    def __init__(self, repo, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
        self.repo = Path(repo).resolve()
        self.pending = deque()
        self.queued = set()
        self.seen = {}
        self.files = {}
        self.missing = []
        self.skipped = []
        self.opaque = []
        self.large_files = []
        self.max_files, self.max_bytes = max_files, max_bytes
        self.total_bytes = 0
        self.import_roots = [LIVE, OFFLINE, DASH / 'tempdesk']

    def enqueue(self, path, **options):
        # Shared manifests repeat the same identity/pin hundreds of times.
        # Deduplicate before any stat/resolve/hash work, retaining a separate
        # queue admission if a later reference requires JSON following.
        key = (original_identity(str(path)).lower(), options.get('sha256', options.get('expected_sha')),
               bool(options.get('follow_json', True)), bool(options.get('declared_codec')))
        if key in self.queued:
            return
        self.queued.add(key)
        self.pending.append({'path': str(path), **options})

    def relative(self, path):
        identity = original_identity(canonical(path))
        for root, target in sorted(ROOTS.items(), key=lambda item: len(original_identity(item[0])), reverse=True):
            base = original_identity(root)
            if identity.lower() == base.lower():
                return target
            if identity.lower().startswith(base.lower() + '/'):
                return target + identity[len(base):]
        raise ValueError('OUTSIDE_DECLARED_ASSET_ROOTS')

    def skip(self, path, reason, source=None):
        self.skipped.append({'original_path': original_identity(str(path)), 'reason': reason, 'source': source})

    def add_extra(self, path):
        value = read_json(path)
        for root in value.get('import_roots', []) if isinstance(value, dict) else []:
            p = Path(root).resolve()
            self.relative(p)  # Roots may not expand outside the explicit scope.
            self.import_roots.append(p)
        items = value if isinstance(value, list) else value.get('files', value.get('paths', value.get('seeds', [])))
        for item in items:
            self.enqueue(**({'path': item, 'follow_json': False, 'required': True} if isinstance(item, str) else item))

    def _json(self, path, row):
        if path.stat().st_size > MAX_JSON:
            self.skip(path, 'JSON_REFERENCE_SCAN_SIZE_CAP_METADATA_BYTES_STILL_INCLUDED')
            return
        try:
            value = read_json(path)
        except (ValueError, UnicodeDecodeError):
            self.skip(path, 'NOT_A_JSON_REFERENCE_DOCUMENT')
            return
        stack, count = [(value, (), False)], 0
        while stack:
            item, chain, provenance = stack.pop()
            count += 1
            if count > MAX_JSON_NODES:
                self.skip(path, 'JSON_REFERENCE_NODE_CAP')
                break
            if isinstance(item, dict):
                if any(str(k).lower() in ENV_KEYS and v not in (None, '', False) for k, v in item.items()):
                    raise ValueError('SECRET_FIELD_IN_SELECTED_JSON: ' + str(path))
                # A pin binds one source identity; its hash is checked later.
                if not provenance and windows_path(item.get('path')):
                    self.enqueue(item['path'], expected_sha=item.get('sha256'),
                                 follow_json=Path(item['path']).name in OPERATIONAL_JSON,
                                 required=True, discovered_from=canonical(path))
                for key, sub in item.items():
                    key = str(key).lower()
                    if key == 'path' and windows_path(sub):
                        continue
                    child_provenance = provenance or key in PROVENANCE_KEYS
                    if child_provenance:
                        # Preserve the parent bytes. Do not chase historical
                        # raw, chart/font, study-driver or old output lineage.
                        todo, scanned = [sub], 0
                        while todo and scanned < 20_000:
                            provenance_item = todo.pop()
                            scanned += 1
                            if isinstance(provenance_item, dict):
                                todo.extend(provenance_item.values())
                            elif isinstance(provenance_item, list):
                                todo.extend(provenance_item)
                            elif windows_path(provenance_item):
                                self.opaque.append({'original_path': original_identity(provenance_item),
                                    'source': canonical(path), 'field': key,
                                    'reason': 'HISTORICAL_PROVENANCE_NOT_A_RUNTIME_DEPENDENCY'})
                        continue
                    stack.append((sub, chain + (key,), child_provenance))
            elif isinstance(item, list):
                stack.extend((sub, chain, provenance) for sub in item)
            elif windows_path(item) and not provenance:
                self.enqueue(item, follow_json=Path(item).name in OPERATIONAL_JSON,
                             required=True, discovered_from=canonical(path))

    def _python(self, path):
        if path.stat().st_size > MAX_JSON:
            return
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        except (ValueError, SyntaxError, UnicodeDecodeError):
            return
        roots = [path.parent, *[p for p in self.import_roots if path.is_relative_to(p)]]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
            if isinstance(node, ast.ImportFrom) and node.level:
                roots_here = [path.parent]
                for _ in range(node.level - 1):
                    roots_here = [r.parent for r in roots_here]
            else:
                roots_here = roots
            for name in names:
                for root in roots_here:
                    stem = root.joinpath(*name.split('.')) if name else root
                    options = [stem.with_suffix('.py'), stem / '__init__.py'] if name else [stem / '__init__.py']
                    for candidate in options:
                        if candidate.is_file() and not blocked(candidate):
                            self.enqueue(candidate, follow_json=False, required=True, discovered_from=canonical(path))
                    if isinstance(node, ast.ImportFrom) and node.level:
                        for alias in node.names:
                            candidate = stem / (alias.name + '.py')
                            if candidate.is_file() and not blocked(candidate):
                                self.enqueue(candidate, follow_json=False, required=True, discovered_from=canonical(path))
        # Preserve package markers for every selected module, without copying
        # all sibling source files or surrounding projects.
        for parent in path.parents:
            if not any(parent.is_relative_to(Path(root)) for root in ROOTS):
                break
            package = parent / '__init__.py'
            if package.is_file() and not blocked(package):
                self.enqueue(package, follow_json=False, required=True, discovered_from=canonical(path))

    def collect(self):
        while self.pending:
            row = self.pending.popleft()
            text = original_identity(row['path'])
            # Exact finite templates are resolved only under approved roots;
            # arbitrary directory references remain opaque metadata.
            if re.search(r'\{[A-Za-z_][A-Za-z0-9_]*\}', text):
                pattern = re.sub(r'\{[A-Za-z_][A-Za-z0-9_]*\}', '*', text)
                matches = glob.glob(pattern)
                if len(matches) > 500:
                    self.missing.append({'original_path': text, 'reason': 'TEMPLATE_MATCH_CAP', 'source': row.get('discovered_from')})
                elif not matches:
                    self.missing.append({'original_path': text, 'reason': 'TEMPLATE_HAS_NO_AUTHENTIC_SOURCE', 'source': row.get('discovered_from')})
                else:
                    for p in matches:
                        self.enqueue(p, **{k: v for k, v in row.items() if k not in ('path', 'expected_sha', 'sha256')})
                continue
            identity = text.lower()
            declared_codec = row.get('declared_codec') or identity in ESSENTIAL_CODECS
            forbidden = blocked(text, declared_codec=bool(declared_codec))
            if forbidden:
                self.skip(text, forbidden, row.get('discovered_from'))
                continue
            path = Path(text).resolve()
            try:
                target = self.relative(path)
            except ValueError as exc:
                self.missing.append({'original_path': text, 'reason': str(exc), 'source': row.get('discovered_from')})
                continue
            if path.is_relative_to(self.repo):
                self.skip(text, 'EXPORT_DESTINATION_SELF_REFERENCE')
                continue
            if path.is_dir():
                self.skip(text, 'OPAQUE_DIRECTORY_REFERENCE_NOT_RECURSIVELY_EXPORTED', row.get('discovered_from'))
                continue
            if not path.is_file():
                self.missing.append({'original_path': text, 'reason': 'SOURCE_FILE_ABSENT', 'source': row.get('discovered_from')})
                continue
            expected = row.get('sha256', row.get('expected_sha'))
            if identity in self.seen:
                old = self.files.get(identity)
                if old and expected and old['sha256'] != expected:
                    self.missing.append({'original_path': text, 'reason': 'SOURCE_PIN_CONFLICT', 'source': row.get('discovered_from')})
                if row.get('follow_json') and not self.seen[identity] and path.suffix.lower() == '.json':
                    self.seen[identity] = True
                    self._json(path, row)
                continue
            size = path.stat().st_size
            if len(self.files) >= self.max_files or self.total_bytes + size > self.max_bytes:
                self.missing.append({'original_path': text, 'reason': 'FINITE_EXPORT_FILE_OR_BYTE_CAP', 'source': row.get('discovered_from')})
                continue
            sha = sha_file(path)
            if expected and sha != expected:
                self.missing.append({'original_path': text, 'reason': 'SOURCE_PIN_MISMATCH', 'source': row.get('discovered_from')})
                continue
            self.files[identity] = {'original_path': canonical(path), 'path': target, 'sha256': sha, 'bytes': size}
            self.seen[identity] = bool(row.get('follow_json', True))
            self.total_bytes += size
            if len(self.files) % 200 == 0:
                print(json.dumps({'phase': 'INVENTORY', 'files': len(self.files), 'bytes': self.total_bytes,
                    'pending_unique_references': len(self.pending), 'missing': len(self.missing)}), flush=True)
            if size > GIT_FILE_WARNING:
                self.large_files.append({'original_path': canonical(path), 'bytes': size,
                    'requires_lossless_chunk_pack': True, 'ordinary_git_file_forbidden': True})
            if path.suffix.lower() == '.json' and row.get('follow_json', True):
                self._json(path, row)
            elif path.suffix.lower() == '.py' and not declared_codec:
                self._python(path)
        # Deduplicate metadata receipts, never the actual source identities.
        dedupe = lambda rows: list({json.dumps(r, sort_keys=True): r for r in rows}.values())
        return {'schema': 1, 'files': sorted(self.files.values(), key=lambda r: r['original_path'].lower()),
            'roots': ROOTS, 'missing': dedupe(self.missing), 'total_bytes': self.total_bytes,
            'large_files': self.large_files, 'skipped_references': dedupe(self.skipped),
            'opaque_provenance_references': dedupe(self.opaque),
            'source_bytes_rewritten': False, 'inventory_only': True,
            'credential_or_live_wallet_export_authorized': False,
            'limits': {'files': self.max_files, 'bytes': self.max_bytes, 'json_scan_bytes': MAX_JSON, 'json_nodes': MAX_JSON_NODES}}

    def copy(self, manifest, large_file_plan=None):
        if manifest['large_files'] and large_file_plan != 'lossless-zip-32m':
            raise ValueError('FILES_OVER_100MB_REQUIRE_REGISTERED_LOSSLESS_CHUNK_PLAN')
        for row in manifest['files']:
            original = Path(row['original_path'])
            destination = (self.repo / row['path']).resolve()
            if not destination.is_relative_to(self.repo / 'final_testing/assets'):
                raise ValueError('EXPORT_DESTINATION_ESCAPES_ASSETS')
            if sha_file(original) != row['sha256']:
                raise ValueError('SOURCE_CHANGED_AFTER_INVENTORY')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_file() and sha_file(destination) == row['sha256']:
                continue
            temporary = destination.with_name(destination.name + '.asset-copy.tmp')
            shutil.copyfile(original, temporary)
            if sha_file(temporary) != row['sha256']:
                raise ValueError('EXPORTED_BYTES_DIFFER')
            os.replace(temporary, destination)
        manifest['inventory_only'] = False
        manifest['large_file_plan'] = large_file_plan
        manifest['copied_files'] = len(manifest['files'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--extra-seeds', type=Path, action='append', default=[])
    parser.add_argument('--no-default-seeds', action='store_true')
    parser.add_argument('--copy', action='store_true')
    parser.add_argument('--large-file-plan', choices=['lossless-zip-32m'])
    parser.add_argument('--max-files', type=int, default=MAX_FILES)
    parser.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    args = parser.parse_args(argv)
    inventory = Inventory(args.repo, max_files=args.max_files, max_bytes=args.max_bytes)
    if not args.no_default_seeds:
        for row in default_seeds():
            inventory.enqueue(**row)
    extras = args.extra_seeds
    default_la = args.repo / 'final_testing/tools/la-extra-seeds.json'
    if not args.no_default_seeds and default_la.is_file() and default_la not in extras:
        extras = [default_la, *extras]
    for extra in extras:
        inventory.add_extra(extra)
    manifest = inventory.collect()
    if args.copy:
        inventory.copy(manifest, args.large_file_plan)
    target = args.repo / 'final_testing/assets/asset-manifest.json'
    atomic_json(target, manifest)
    print(json.dumps({'manifest': str(target), 'files': len(manifest['files']), 'total_bytes': manifest['total_bytes'],
                      'missing': len(manifest['missing']), 'large_files': len(manifest['large_files']),
                      'copied': not manifest['inventory_only']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
