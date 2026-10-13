"""Read-only legacy identities backed solely by an exported asset manifest.

This is a deployment bridge, not an execution sandbox. Hashes and logical source
identities stay unchanged. Python/filesystem entry points used by the accepted
packages are redirected; arbitrary third-party native filesystem APIs are not
claimed covered. Known pandas/Arrow/ctypes entry points are handled explicitly.
"""

from __future__ import annotations

import builtins
import contextlib
import errno
import hashlib
import importlib.machinery
import io
import json
import ntpath
import os
from pathlib import Path
import stat
import subprocess
import sys
import sysconfig
import threading
import time


class UnexportedLegacyPath(FileNotFoundError):
    """An original path is not available in this exported deployment."""

    def __init__(self, message):
        super().__init__(errno.ENOENT, message)


_ACTIVE = None
_ORIGINAL_OPEN = builtins.open
_ORIGINAL_IO_OPEN = io.open
_ORIGINAL_STAT = os.stat
_ORIGINAL_LSTAT = os.lstat
_ORIGINAL_GETCWD = os.getcwd
_ORIGINAL_REALPATH = os.path.realpath
_ORIGINAL_ABSPATH = os.path.abspath
_ORIGINAL_RESOLVE = Path.resolve


def _norm(value):
    text = os.fsdecode(os.fspath(value)).replace("\\", "/")
    # Path identities in the frozen JSON are Windows paths even in POSIX tests.
    if len(text) > 2 and text[1] == ":":
        return ntpath.normpath(text).replace("\\", "/").rstrip("/").casefold()
    return os.path.normpath(text).replace("\\", "/").rstrip("/").casefold()


def _within(value, prefix):
    return value == prefix or value.startswith(prefix + "/")


def _display(value):
    text = os.fsdecode(os.fspath(value)).replace("\\", "/")
    return ntpath.normpath(text).replace("\\", "/") if len(text) > 2 and text[1] == ":" else os.path.normpath(text)


class _Entry:
    def __init__(self, mount, logical):
        self._mount = mount
        self.path = logical
        self.name = logical.replace("\\", "/").rsplit("/", 1)[-1]

    def __fspath__(self): return self.path
    def stat(self, *, follow_symlinks=True): return self._mount.stat(self.path, follow_symlinks=follow_symlinks)
    def is_dir(self, *, follow_symlinks=True): return stat.S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)
    def is_file(self, *, follow_symlinks=True): return stat.S_ISREG(self.stat(follow_symlinks=follow_symlinks).st_mode)
    def is_symlink(self): return False
    def inode(self): return self.stat().st_ino


class _Scan:
    def __init__(self, entries): self._entries = iter(entries)
    def __iter__(self): return self
    def __next__(self): return next(self._entries)
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self): pass


class AssetMount:
    LEGACY_CODEX = "C:/Users/jarve/Documents/Codex"
    LEGACY_ANACONDA = "C:/Users/jarve/anaconda3/Library/bin"
    LEGACY_RUNTIME = LEGACY_CODEX + "/Kalshi Temp Bot/Dashboard/runtime"
    LEGACY_PYTHON = LEGACY_CODEX + "/Kalshi/_weather/_llm/.venv/Scripts/python.exe"

    def __init__(self, root, manifest_path=None, *, manifest=None):
        self.root = Path(_ORIGINAL_REALPATH(_ORIGINAL_ABSPATH(os.fspath(root))))
        self.manifest_path = Path(manifest_path or self.root / "final_testing/assets/asset-manifest.json")
        if manifest is None:
            with _ORIGINAL_OPEN(self.manifest_path, "rb") as stream: manifest = json.load(stream)
        if manifest.get("schema") != 1 or manifest.get("missing"):
            raise ValueError("Complete schema1 asset export is required")
        self.manifest = manifest
        self.runtime = self.root / "runtime/dashboard"
        # The original logical parent can be an immutable source directory;
        # create the distinct writable overlay's physical parents before mount.
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.current_python = _ORIGINAL_REALPATH(sys.executable)
        paths = sysconfig.get_paths()
        runtime_roots = {_norm(paths[k]) for k in ("purelib", "platlib", "stdlib", "platstdlib") if paths.get(k)}
        if sys.platform == "win32":
            # Windows stdlib extensions (_socket, _ssl, etc.) live separately
            # from Lib. Admit only the current interpreter's DLLs directories,
            # never the entire legacy/Codex runtime or unrelated data tree.
            runtime_roots.update(_norm(Path(prefix) / "DLLs") for prefix in (sys.base_exec_prefix, sys.exec_prefix))
        self.current_runtime_roots = tuple(sorted(runtime_roots))
        self.files = {}
        self.reverse = {}
        self.directories = {}
        self.directory_physical = {}
        self.root_maps = []
        self.verified = {}
        self._lock = threading.RLock()
        self._patches = []
        self._special_modules = set()
        self._checking_third_party = False
        self.python_alias = os.environ.get("TEMP_DESK_PORTABLE_PYTHON_ALIAS")
        for original, relative in manifest.get("roots", {}).items():
            physical = self._repo_path(relative)
            self.root_maps.append((_norm(original), _display(original), physical))
        if not self.root_maps:
            raise ValueError("Legacy roots are required")
        for record in manifest.get("files", []):
            original = _display(record["original_path"])
            key = _norm(original)
            if key in self.files: raise ValueError("Duplicate original asset identity")
            physical = self._repo_path(record["path"])
            if len(record.get("sha256", "")) != 64: raise ValueError("Asset SHA256 required")
            if int(record.get("bytes", -1)) < 0: raise ValueError("Asset size required")
            self.files[key] = {**record, "original_path": original, "physical": physical}
            self.reverse[_norm(physical)] = original
            parts = original.replace("\\", "/").split("/")
            for i in range(1, len(parts)):
                parent = "/".join(parts[:i]); child = "/".join(parts[:i + 1])
                if any(_within(_norm(parent), root_key) for root_key, _, _ in self.root_maps):
                    self.directories.setdefault(_norm(parent), {})[_norm(child)] = child
            for root_key, root_original, root_physical in self.root_maps:
                if _within(key, root_key):
                    # Export schema preserves original suffix beneath each root.
                    expected = root_physical.joinpath(*original[len(root_original):].lstrip("/").split("/"))
                    if _norm(expected) == _norm(physical):
                        current = physical.parent
                        for i in range(len(parts) - 1, len(root_original.split("/")) - 1, -1):
                            self.directory_physical.setdefault(_norm("/".join(parts[:i])), current)
                            current = current.parent
        self.root_maps.sort(key=lambda row: len(row[0]), reverse=True)
        self.legacy_prefixes = tuple({_norm(self.LEGACY_CODEX), _norm(self.LEGACY_ANACONDA), *(r[0] for r in self.root_maps)})

    def _repo_path(self, relative):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts: raise ValueError("Repo-relative asset path required")
        physical = Path(_ORIGINAL_REALPATH(os.fspath(self.root / path)))
        if not physical.is_relative_to(self.root): raise ValueError("Asset path escapes repository")
        return physical

    def _absolute(self, value):
        text = os.fsdecode(os.fspath(value))
        if (len(text) > 2 and text[1] == ":") or os.path.isabs(text): return _display(text)
        return _display(os.path.join(_ORIGINAL_GETCWD(), text))

    def logical(self, value):
        absolute = self._absolute(value); key = _norm(absolute)
        if key in self.reverse: return self.reverse[key]
        if _within(key, _norm(self.runtime)):
            tail = absolute[len(str(self.runtime).replace("\\", "/")):].lstrip("/")
            return self.LEGACY_RUNTIME + ("/" + tail if tail else "")
        for root_key, original, physical in self.root_maps:
            pkey = _norm(physical)
            if _within(key, pkey):
                tail = absolute[len(str(physical).replace("\\", "/")):].lstrip("/")
                return original + ("/" + tail if tail else "")
        return absolute

    def _verify(self, record):
        path = record["physical"]
        canonical = Path(_ORIGINAL_REALPATH(os.fspath(path)))
        if not canonical.is_relative_to(self.root):
            raise ValueError("Exported asset now escapes repository: " + record["original_path"])
        current = _ORIGINAL_STAT(path)
        marker = (current.st_size, current.st_mtime_ns, getattr(current, "st_ino", None))
        with self._lock:
            if self.verified.get(_norm(path)) == marker: return
            if current.st_size != int(record["bytes"]): raise ValueError("Exported asset size changed: " + record["original_path"])
            digest = hashlib.sha256()
            with _ORIGINAL_OPEN(path, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
            if digest.hexdigest() != record["sha256"]: raise ValueError("Exported asset hash changed: " + record["original_path"])
            self.verified[_norm(path)] = marker

    def physical(self, value, *, write=False, directory=False, verify=True):
        if isinstance(value, int) or value is None: return value
        logical = self.logical(value); key = _norm(logical)
        if key == _norm(self.LEGACY_PYTHON) and self.python_alias:
            if _norm(self.python_alias) != _norm(self.current_python):
                raise ValueError("Executable alias must identify the actual current interpreter")
            if write: raise PermissionError("The admitted current interpreter is read-only")
            return self.current_python
        if key == _norm(self.current_python):
            if write: raise PermissionError("The admitted current interpreter is read-only")
            return self.current_python
        if _within(key, _norm(self.LEGACY_RUNTIME)):
            tail = logical[len(self.LEGACY_RUNTIME):].lstrip("/")
            overlay = self.runtime.joinpath(*tail.split("/")) if tail else self.runtime
            if write or self._exists(overlay) or key not in self.files: return os.fspath(overlay)
            record = self.files[key]
            if verify: self._verify(record)
            return os.fspath(record["physical"])
        if key in self.files:
            if write: raise PermissionError("Immutable exported asset: " + logical)
            if verify: self._verify(self.files[key])
            return os.fspath(self.files[key]["physical"])
        if any(_within(key, prefix) for prefix in self.current_runtime_roots):
            if write: raise PermissionError("Current installed runtime is read-only through the asset bridge")
            return logical
        if key in self.directories or any(key == row[0] for row in self.root_maps):
            if write: raise PermissionError("Immutable exported directory: " + logical)
            if directory and key in self.directory_physical: return os.fspath(self.directory_physical[key])
            for root_key, original, physical in self.root_maps:
                if key == root_key: return os.fspath(physical)
            raise UnexportedLegacyPath("Virtual exported directory has no physical counterpart: " + logical)
        # The portable repository can itself reside beneath the legacy Codex
        # prefix on the author's machine; never remap its new source/runtime.
        if _within(key, _norm(self.root)): return os.fspath(value)
        if any(_within(key, prefix) for prefix in self.legacy_prefixes):
            raise UnexportedLegacyPath("Legacy file was not exported: " + logical)
        return os.fspath(value)

    @staticmethod
    def _exists(path):
        try: _ORIGINAL_STAT(path); return True
        except (FileNotFoundError, NotADirectoryError): return False

    def stat(self, path, *, follow_symlinks=True, **kwargs):
        if isinstance(path, int): return _ORIGINAL_STAT(path, follow_symlinks=follow_symlinks, **kwargs)
        key = _norm(self.logical(path))
        if (key in self.directories and key not in self.files
                and not _within(key, _norm(self.LEGACY_RUNTIME))
                and not any(_within(key, p) for p in self.current_runtime_roots)):
            physical = self.directory_physical.get(key, self.root)
            return _ORIGINAL_STAT(physical, follow_symlinks=follow_symlinks, **kwargs)
        mapped = self.physical(path, directory=True)
        return _ORIGINAL_STAT(mapped, follow_symlinks=follow_symlinks, **kwargs)

    def listdir(self, path="."):
        logical = self.logical(path); key = _norm(logical)
        if (key in self.directories and not _within(key, _norm(self.LEGACY_RUNTIME))
                and not any(_within(key, p) for p in self.current_runtime_roots)):
            return [child.replace("\\", "/").rsplit("/", 1)[-1] for child in self.directories[key].values()]
        mapped = self.physical(path, directory=True)
        original = self._saved(os, "listdir")
        try: names = list(original(mapped))
        except FileNotFoundError:
            if _within(key, _norm(self.LEGACY_RUNTIME)) and key in self.directories: names = []
            else: raise
        if _within(key, _norm(self.LEGACY_RUNTIME)):
            names += [child.replace("\\", "/").rsplit("/", 1)[-1] for child in self.directories.get(key, {}).values()]
        return list(dict.fromkeys(names))

    def scandir(self, path="."):
        logical = self.logical(path); key = _norm(logical)
        if ((key in self.directories and not any(_within(key, p) for p in self.current_runtime_roots))
                or _within(key, _norm(self.LEGACY_RUNTIME))):
            return _Scan([_Entry(self, logical.rstrip("/") + "/" + name) for name in self.listdir(path)])
        return self._saved(os, "scandir")(self.physical(path, directory=True))

    def _set(self, obj, name, replacement):
        original = getattr(obj, name)
        self._patches.append((obj, name, original))
        setattr(obj, name, replacement)
        return original

    def _saved(self, obj, name):
        return next(original for owner, attr, original in self._patches if owner is obj and attr == name)

    def map_environment(self, environment=None):
        env = dict(os.environ if environment is None else environment)
        for name in ("TEMP_DESK_SOURCE_MAP", "TEMP_DESK_RUNTIME_ROOT", "OFFLINE_LA_REVIEW_PATH", "OFFLINE_LA_FEE_REVIEW_PATH"):
            value = env.get(name)
            if value and not value.lstrip().startswith("{"):
                env[name] = self.physical(value, directory=name.endswith("RUNTIME_ROOT"))
        entries = []
        for value in env.get("PYTHONPATH", "").split(os.pathsep):
            if not value: continue
            try: entries.append(self.physical(value, directory=True))
            except UnexportedLegacyPath:
                # The old unexported cos dependency path is deployment-specific.
                # Current installed site-packages are the admitted replacement.
                if _norm(value) != _norm(self.LEGACY_CODEX + "/2026-10-08/cos/work/python-deps"): raise
        prepend = [str(self.root / "final_testing/portable"), str(self.root)]
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(prepend + entries))
        env["FINAL_TESTING_PORTABLE_ROOT"] = str(self.root)
        env["FINAL_TESTING_ASSET_MANIFEST"] = str(self.manifest_path)
        env["TEMP_DESK_PORTABLE_ROOT"] = str(self.root)
        env["TEMP_DESK_PORTABLE_ASSET_MANIFEST"] = str(self.manifest_path)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def map_argv(self, arguments):
        if isinstance(arguments, (str, bytes)):
            text = os.fsdecode(arguments).replace("\\", "/").casefold()
            if any(prefix in text for prefix in self.legacy_prefixes):
                raise UnexportedLegacyPath("Opaque legacy shell command cannot be safely relocated")
            return arguments
        result = []
        for value in arguments:
            text = os.fsdecode(os.fspath(value))
            if len(text) > 2 and text[1] == ":": result.append(self.physical(text, directory=True))
            elif _within(_norm(text), _norm(self.root)): result.append(self.physical(text, directory=True))
            else: result.append(value)
        return result

    def _third_party(self):
        if self._checking_third_party: return
        self._checking_third_party = True
        try:
            self._patch_third_party()
        finally:
            self._checking_third_party = False

    def _patch_third_party(self):
        for sqlite in (sys.modules.get("sqlite3"), sys.modules.get("sqlite3.dbapi2")):
            if not sqlite or not hasattr(sqlite, "connect") or (id(sqlite), "connect") in self._special_modules: continue
            self._special_modules.add((id(sqlite), "connect"))
            original_connect = sqlite.connect
            def connect(database, *args, _function=original_connect, **kwargs):
                if not isinstance(database, (str, bytes, os.PathLike)): return _function(database, *args, **kwargs)
                text = os.fsdecode(os.fspath(database))
                if text == ":memory:": return _function(database, *args, **kwargs)
                uri = kwargs.get("uri", args[6] if len(args) > 6 else False)
                parameters = {}
                if uri and text.startswith("file:"):
                    from urllib.parse import parse_qsl, unquote
                    body = text[5:]; path, _, query = body.partition("?")
                    if path.startswith("///"): path = path[3:]
                    elif path.startswith("//localhost/"): path = path[len("//localhost/"):]
                    elif path.startswith("//"): raise ValueError("Remote SQLite file URIs are not an exported asset")
                    text = unquote(path)
                    parameters = dict(parse_qsl(query, keep_blank_values=True))
                key = _norm(self.logical(text))
                immutable = key in self.files and not _within(key, _norm(self.LEGACY_RUNTIME))
                if immutable:
                    if parameters.get("mode") not in (None, "ro"):
                        raise PermissionError("Immutable exported SQLite cannot be opened writable")
                    mapped = self.physical(text)
                    parameters["mode"] = "ro"
                    # A closed, byte-sealed DB has no mutable journal companion.
                    parameters["immutable"] = "1"
                    uri = True
                else:
                    mapped = self.physical(text, write=parameters.get("mode") != "ro")
                if uri:
                    from urllib.parse import quote, urlencode
                    mapped = "file:" + quote(os.fsdecode(mapped).replace("\\", "/"), safe="/:")
                    if parameters: mapped += "?" + urlencode(parameters)
                    if len(args) > 6: args = (*args[:6], True, *args[7:])
                    else: kwargs["uri"] = True
                return _function(mapped, *args, **kwargs)
            self._set(sqlite, "connect", connect)
        pandas = sys.modules.get("pandas")
        if pandas and hasattr(pandas, "read_parquet") and (id(pandas), "read_parquet") not in self._special_modules:
            self._special_modules.add((id(pandas), "read_parquet"))
            original = pandas.read_parquet
            def read_parquet(path, *args, _function=original, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)): path = self.physical(path)
                return _function(path, *args, **kwargs)
            self._set(pandas, "read_parquet", read_parquet)
        arrow = sys.modules.get("pyarrow.parquet")
        if arrow:
            for name in ("read_table", "read_metadata", "read_schema"):
                if not hasattr(arrow, name) or (id(arrow), name) in self._special_modules: continue
                self._special_modules.add((id(arrow), name)); original = getattr(arrow, name)
                def wrapped(path, *args, _function=original, **kwargs):
                    if isinstance(path, (str, bytes, os.PathLike)): path = self.physical(path)
                    return _function(path, *args, **kwargs)
                self._set(arrow, name, wrapped)
            cls = getattr(arrow, "ParquetFile", None)
            if cls and (id(cls), "__init__") not in self._special_modules:
                self._special_modules.add((id(cls), "__init__")); original = cls.__init__
                def parquet_init(instance, source, *args, _function=original, **kwargs):
                    if isinstance(source, (str, bytes, os.PathLike)): source = self.physical(source)
                    return _function(instance, source, *args, **kwargs)
                self._set(cls, "__init__", parquet_init)
        ctypes = sys.modules.get("ctypes")
        if ctypes:
            for name in ("CDLL", "WinDLL", "PyDLL", "OleDLL"):
                cls = getattr(ctypes, name, None)
                if not cls or (id(cls), "__init__") in self._special_modules: continue
                self._special_modules.add((id(cls), "__init__")); original = cls.__init__
                def dll_init(instance, name, *args, _function=original, **kwargs):
                    if isinstance(name, (str, bytes, os.PathLike)) and (len(os.fsdecode(name)) > 2 and os.fsdecode(name)[1] == ":"):
                        name = self.physical(name)
                    return _function(instance, name, *args, **kwargs)
                self._set(cls, "__init__", dll_init)

    def install(self):
        global _ACTIVE
        if _ACTIVE is self: return self
        if _ACTIVE is not None: raise RuntimeError("Only one portable asset mount per process")
        self._set(builtins, "open", lambda file, mode="r", *a, **k: _ORIGINAL_OPEN(self.physical(file, write=any(x in mode for x in "wax+")), mode, *a, **k))
        self._set(io, "open", lambda file, mode="r", *a, **k: _ORIGINAL_IO_OPEN(self.physical(file, write=any(x in mode for x in "wax+")), mode, *a, **k))
        if hasattr(io, "open_code"):
            original_io_code = io.open_code
            self._set(io, "open_code", lambda path, _function=original_io_code: _function(self.physical(path)))
        import _io
        if hasattr(_io, "open_code"):
            original = _io.open_code
            self._set(_io, "open_code", lambda path, _function=original: _function(self.physical(path)))
        self._set(os, "stat", self.stat)
        self._set(os, "lstat", lambda p, **k: self.stat(p, follow_symlinks=False, **k))
        self._set(os, "listdir", self.listdir)
        self._set(os, "scandir", self.scandir)
        original_os_open = os.open
        def descriptor_open(path, flags, *a, **k):
            writes = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return original_os_open(self.physical(path, write=writes), flags, *a, **k)
        self._set(os, "open", descriptor_open)
        self._set(os, "access", lambda p, mode, **k: self._saved(os, "access")(self.physical(p, write=bool(mode & os.W_OK), directory=True), mode, **k))
        for name in ("mkdir", "makedirs", "remove", "unlink", "rmdir", "chmod", "utime", "chdir"):
            if not hasattr(os, name): continue
            original = getattr(os, name)
            def mutation(path, *a, _function=original, _name=name, **k):
                return _function(self.physical(path, write=_name != "chdir", directory=True), *a, **k)
            self._set(os, name, mutation)
        for name in ("rename", "replace"):
            original = getattr(os, name)
            def mutation2(src, dst, *a, _function=original, _name=name, **k):
                # Admission occurs once before any retry: sealed assets cannot
                # be a source or destination of a mutable replacement.
                source = self.physical(src, write=True)
                target = self.physical(dst, write=True)
                mutable_overlay = _within(_norm(self.logical(dst)), _norm(self.LEGACY_RUNTIME))
                for attempt in range(6):
                    try:
                        return _function(source, target, *a, **k)
                    except OSError as error:
                        if (_name != "replace" or not mutable_overlay
                                or getattr(error, "winerror", None) not in (5, 32)
                                or attempt == 5):
                            raise
                        # A reader can briefly hold progress.json without
                        # delete sharing on Windows. Maximum wait is0.20s.
                        time.sleep(.04)
            self._set(os, name, mutation2)
        if hasattr(os, "add_dll_directory"):
            original = os.add_dll_directory
            self._set(os, "add_dll_directory", lambda p, _function=original: _function(self.physical(p, directory=True)))
        def resolve(path, strict=False):
            absolute = self._absolute(path)
            logical = self.logical(absolute)
            if logical != absolute or any(_within(_norm(logical), p) for p in self.legacy_prefixes):
                # Non-strict resolution preserves an unknown identity; actual
                # open/stat denies it. strict=True must prove an exported file.
                if strict: self.stat(logical)
                return Path(logical)
            return _ORIGINAL_RESOLVE(path, strict=strict)
        self._set(Path, "resolve", resolve)
        import importlib._bootstrap_external as bootstrap
        self._set(bootstrap, "_path_stat", lambda p: self.stat(p))
        platform_os = bootstrap._os
        if platform_os is not os:
            self._set(platform_os, "listdir", self.listdir)
        for loader in (importlib.machinery.SourceFileLoader, importlib.machinery.SourcelessFileLoader):
            original = loader.get_data
            def get_data(instance, path, _function=original): return _function(instance, self.physical(path))
            self._set(loader, "get_data", get_data)
        original_popen = subprocess.Popen.__init__
        def popen(instance, args, *a, **kwargs):
            args = self.map_argv(args)
            if kwargs.get("cwd") is not None: kwargs["cwd"] = self.physical(kwargs["cwd"], directory=True)
            kwargs["env"] = self.map_environment(kwargs.get("env"))
            return original_popen(instance, args, *a, **kwargs)
        self._set(subprocess.Popen, "__init__", popen)
        original_import = builtins.__import__
        def importer(*a, **k):
            result = original_import(*a, **k)
            self._third_party()
            return result
        self._set(builtins, "__import__", importer)
        _ACTIVE = self
        self._third_party()
        return self

    def uninstall(self):
        global _ACTIVE
        for owner, name, original in reversed(self._patches): setattr(owner, name, original)
        self._patches.clear()
        self._special_modules.clear()
        if _ACTIVE is self: _ACTIVE = None

    def __enter__(self): return self.install()
    def __exit__(self, *_): self.uninstall()


def install(root, manifest_path=None):
    if _ACTIVE is not None:
        if _norm(root) != _norm(_ACTIVE.root): raise RuntimeError("Portable mount root changed")
        return _ACTIVE
    return AssetMount(root, manifest_path).install()


def install_from_env():
    root = os.environ.get("TEMP_DESK_PORTABLE_ROOT") or os.environ.get("FINAL_TESTING_PORTABLE_ROOT")
    if not root: return None
    manifest = os.environ.get("TEMP_DESK_PORTABLE_ASSET_MANIFEST") or os.environ.get("FINAL_TESTING_ASSET_MANIFEST")
    return install(root, manifest)


def physical(value, *, write=False, directory=True, verify=True):
    """Root deployment adapters must use an already installed sealed mount."""
    if _ACTIVE is None: raise RuntimeError("Portable asset mount is not installed")
    return _ACTIVE.physical(value, write=write, directory=directory, verify=verify)


def logical(value):
    if _ACTIVE is None: raise RuntimeError("Portable asset mount is not installed")
    return _ACTIVE.logical(value)
