"""Focused relocated deployment tests; no original model/source mutation."""

from pathlib import Path
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from final_testing.portable.mount import AssetMount, UnexportedLegacyPath


REPO = Path(__file__).resolve().parents[1]
LEGACY = AssetMount.LEGACY_CODEX


class PortableMountTests(unittest.TestCase):
    def setUp(self):
        temp_root = REPO / "runtime/portable-mount-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root)
        self.root = Path(self.temporary.name) / "relocated clone with spaces"
        self.root.mkdir()
        self.records = []
        self._old_alias = os.environ.get("TEMP_DESK_PORTABLE_PYTHON_ALIAS")
        os.environ["TEMP_DESK_PORTABLE_PYTHON_ALIAS"] = sys.executable
        self.add(LEGACY + "/saved/temperature.json", b'{"temperature":72.25}')
        self.add(LEGACY + "/saved/portable_fixture.py",
                 b'from pathlib import Path\nVALUE=72.25\nLOGICAL=str(Path(__file__).resolve())\n')
        self.add(AssetMount.LEGACY_RUNTIME + "/sealed.json", b'{"sealed":true}')
        self.manifest = {"schema": 1, "files": self.records, "missing": [],
                         "roots": {LEGACY: "final_testing/assets/codex",
                                   AssetMount.LEGACY_ANACONDA: "final_testing/assets/anaconda-bin"}}
        target = self.root / "final_testing/assets/asset-manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.manifest))
        self.mount = AssetMount(self.root)

    def tearDown(self):
        self.mount.uninstall()
        if self._old_alias is None: os.environ.pop("TEMP_DESK_PORTABLE_PYTHON_ALIAS", None)
        else: os.environ["TEMP_DESK_PORTABLE_PYTHON_ALIAS"] = self._old_alias
        self.temporary.cleanup()

    def add(self, original, content):
        suffix = original[len(LEGACY):].lstrip("/")
        path = self.root / "final_testing/assets/codex" / suffix
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.records.append({"original_path": original, "path": path.relative_to(self.root).as_posix(),
                             "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        return path

    def test_exact_bytes_logical_identity_stat_and_virtual_directory(self):
        logical = LEGACY + "/saved/temperature.json"
        physical = self.root / self.records[0]["path"]
        with self.mount:
            self.assertEqual(Path(logical).read_bytes(), b'{"temperature":72.25}')
            self.assertEqual(Path(physical).read_bytes(), b'{"temperature":72.25}')
            self.assertEqual(str(Path(physical).resolve()).replace("\\", "/"), logical)
            self.assertEqual(Path(logical).stat().st_size, self.records[0]["bytes"])
            self.assertEqual(set(os.listdir(LEGACY + "/saved")), {"temperature.json", "portable_fixture.py"})
            self.assertTrue(Path(LEGACY + "/saved").is_dir())
            self.assertEqual({p.name for p in Path(LEGACY + "/saved").glob("*.json")}, {"temperature.json"})

    def test_unexported_original_location_never_falls_back(self):
        # This location exists on the developer PC. It is absent from our
        # miniature export and must still be denied without opening its bytes.
        unexported = LEGACY + "/Kalshi Temp Bot/Dashboard/README.txt"
        with self.mount:
            with self.assertRaises(UnexportedLegacyPath): builtins_open(unexported)
            with self.assertRaises(UnexportedLegacyPath): os.stat(unexported)
            self.assertFalse(Path(unexported).exists())

    def test_immutable_writes_and_tampered_hash_are_rejected(self):
        logical = LEGACY + "/saved/temperature.json"
        with self.mount:
            with self.assertRaises(PermissionError): Path(logical).write_text("tampered")
        physical = self.root / self.records[0]["path"]
        physical.write_bytes(b'{"temperature":99.25}')  # same length; hash matters
        with self.mount:
            with self.assertRaisesRegex(ValueError, "hash changed"): Path(logical).read_bytes()

    def test_runtime_overlay_baseline_and_parent_directories(self):
        logical = AssetMount.LEGACY_RUNTIME + "/sealed.json"
        with self.mount:
            self.assertEqual(json.loads(Path(logical).read_bytes()), {"sealed": True})
            self.assertEqual(os.listdir(AssetMount.LEGACY_RUNTIME), ["sealed.json"])
            Path(AssetMount.LEGACY_RUNTIME).mkdir(parents=True, exist_ok=True)
            Path(logical).write_text('{"new":true}')
            self.assertEqual(json.loads(Path(logical).read_bytes()), {"new": True})
            self.assertEqual(json.loads((self.root / "runtime/dashboard/sealed.json").read_bytes()), {"new": True})
            self.assertEqual(str((self.root / "runtime/dashboard/sealed.json").resolve()).replace("\\", "/"), logical)

    def test_source_import_uses_exported_code_with_original_file_identity(self):
        sys.path.insert(0, LEGACY + "/saved")
        try:
            with self.mount:
                module = importlib.import_module("portable_fixture")
                self.assertEqual(module.VALUE, 72.25)
                self.assertEqual(module.LOGICAL.replace("\\", "/"), LEGACY + "/saved/portable_fixture.py")
        finally:
            sys.path.remove(LEGACY + "/saved")
            sys.modules.pop("portable_fixture", None)
            sys.path_importer_cache.pop(LEGACY + "/saved", None)

    def test_child_remount_and_cwd_and_executable_alias(self):
        portable = self.root / "final_testing/portable"
        portable.mkdir(parents=True)
        for name in ("__init__.py", "mount.py", "sitecustomize.py"):
            shutil.copyfile(REPO / "final_testing/portable" / name, portable / name)
        program = "import json;from pathlib import Path;print(json.dumps({'value':Path(" + repr(LEGACY + "/saved/temperature.json") + ").read_text(),'identity':str(Path(" + repr(LEGACY + "/saved/portable_fixture.py") + ").resolve())}))"
        with self.mount:
            result = subprocess.run([AssetMount.LEGACY_PYTHON, "-B", "-c", program],
                                    cwd=LEGACY + "/saved", text=True, capture_output=True, check=True,
                                    env={**os.environ, "TEMP_DESK_PORTABLE_UNIT_TEST": "filesystem"})
        output = json.loads(result.stdout)
        self.assertEqual(json.loads(output["value"]), {"temperature": 72.25})
        self.assertEqual(output["identity"].replace("\\", "/"), LEGACY + "/saved/portable_fixture.py")
        self.assertNotIn("Error in sitecustomize", result.stderr)

    def test_real_child_runtime_failure_stops_before_requested_program(self):
        portable = self.root / "final_testing/portable"
        portable.mkdir(parents=True)
        for name in ("__init__.py", "mount.py", "sitecustomize.py"):
            shutil.copyfile(REPO / "final_testing/portable" / name, portable / name)
        (portable / "runtime_binding.py").write_text("def install():\n raise RuntimeError('fixture version mismatch')\n")
        environment = dict(os.environ)
        environment.pop("TEMP_DESK_PORTABLE_UNIT_TEST", None)
        with self.mount:
            result = subprocess.run([AssetMount.LEGACY_PYTHON, "-B", "-c", "print('APPLICATION_RAN')"],
                                    cwd=self.root, text=True, capture_output=True, env=environment)
        self.assertEqual(result.returncode, 78)
        self.assertNotIn("APPLICATION_RAN", result.stdout)
        self.assertEqual(result.stderr.strip(), "Portable bootstrap failed: RuntimeError")

    def test_current_windows_native_socket_and_metadata_bootstrap(self):
        if sys.platform != "win32": self.skipTest("Windows current-runtime extension layout")
        portable = self.root / "final_testing/portable"
        portable.mkdir(parents=True)
        for name in ("__init__.py", "mount.py", "sitecustomize.py"):
            shutil.copyfile(REPO / "final_testing/portable" / name, portable / name)
        # Reproduce the real export's registered Codex-runtime prefix. Its DLLs
        # are current-runtime dependencies, not source assets or old data.
        runtime_prefix = str(Path(sys.base_prefix).parents[1])
        self.manifest["roots"][runtime_prefix] = "final_testing/assets/codex-runtime"
        (self.root / "final_testing/assets/asset-manifest.json").write_text(json.dumps(self.manifest))
        self.mount = AssetMount(self.root)
        self.assertIn(str(Path(sys.base_exec_prefix) / "DLLs").replace("\\", "/").casefold(), self.mount.current_runtime_roots)
        program = "import json,socket,_socket,importlib.metadata;print(json.dumps({'native_socket':bool(_socket.socket),'numpy':importlib.metadata.version('numpy')}))"
        with self.mount:
            result = subprocess.run([AssetMount.LEGACY_PYTHON, "-B", "-c", program],
                text=True, capture_output=True, check=True,
                env={**os.environ, "TEMP_DESK_PORTABLE_UNIT_TEST": "filesystem"})
        self.assertTrue(json.loads(result.stdout)["native_socket"])
        self.assertTrue(json.loads(result.stdout)["numpy"])
        self.assertNotIn("Portable bootstrap failed", result.stderr)

    def test_current_executable_identity_is_readonly_even_outside_relocated_repo(self):
        expected = hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        with self.mount:
            self.assertEqual(hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(), expected)
            with self.assertRaises(PermissionError): self.mount.physical(sys.executable, write=True)
            with self.assertRaises(PermissionError): self.mount.physical(AssetMount.LEGACY_PYTHON, write=True)

    def test_mutable_progress_replace_retries_transient_windows_error_only(self):
        native = os.replace
        attempts = []
        def transient(source, destination, *args, **kwargs):
            attempts.append((source, destination))
            if len(attempts) < 3:
                error = PermissionError("fixture Windows sharing conflict")
                error.winerror = 5
                raise error
            return native(source, destination, *args, **kwargs)
        with patch.object(os, "replace", transient):
            with self.mount:
                source = AssetMount.LEGACY_RUNTIME + "/progress.tmp"
                destination = AssetMount.LEGACY_RUNTIME + "/progress.json"
                Path(source).write_text('{"actual_timestamp":123456}')
                os.replace(source, destination)
                self.assertEqual(Path(destination).read_text(), '{"actual_timestamp":123456}')
                self.assertEqual(len(attempts), 3)
                # Immutable admission precedes retries or native invocation.
                attempts.clear()
                with self.assertRaises(PermissionError): os.replace(destination, LEGACY + "/saved/temperature.json")
                self.assertEqual(attempts, [])

    def test_mutable_replace_retry_is_bounded_and_unrelated_paths_not_retried(self):
        attempts = []
        def always_conflicted(source, destination, *args, **kwargs):
            attempts.append((source, destination))
            error = PermissionError("fixture Windows sharing conflict")
            error.winerror = 32
            raise error
        with patch.object(os, "replace", always_conflicted):
            with self.mount:
                with self.assertRaises(PermissionError):
                    os.replace(AssetMount.LEGACY_RUNTIME + "/progress.tmp", AssetMount.LEGACY_RUNTIME + "/progress.json")
                self.assertEqual(len(attempts), 6)
                attempts.clear()
                with self.assertRaises(PermissionError): os.replace(self.root / "outside.tmp", self.root / "outside.json")
                self.assertEqual(len(attempts), 1)

    def test_nested_legacy_root_has_short_physical_directory_and_same_identity(self):
        logical_root = LEGACY + "/saved/compact"
        physical_root = self.root / "final_testing/assets/n"
        physical_root.mkdir(parents=True)
        content = b'{"native":true}'
        target = physical_root / "native.json"
        target.write_bytes(content)
        self.records.append({"original_path": logical_root + "/native.json",
            "path": target.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        self.manifest["roots"][logical_root] = "final_testing/assets/n"
        self.mount = AssetMount(self.root, manifest=self.manifest)
        with self.mount:
            self.assertEqual(Path(self.mount.physical(logical_root, directory=True)), physical_root)
            self.assertEqual(str(target.resolve()).replace("\\", "/"), logical_root + "/native.json")
            self.assertEqual(Path(logical_root + "/native.json").read_bytes(), content)
            self.assertEqual(os.listdir(logical_root), ["native.json"])
            self.assertEqual([p.name for p in Path(logical_root).glob("*.json")], ["native.json"])

    def test_environment_paths_and_no_original_dependency_fallback(self):
        env = self.mount.map_environment({"TEMP_DESK_RUNTIME_ROOT": AssetMount.LEGACY_RUNTIME,
             "PYTHONPATH": LEGACY + "/2026-10-08/cos/work/python-deps"})
        self.assertEqual(env["TEMP_DESK_RUNTIME_ROOT"], str(self.root / "runtime/dashboard"))
        self.assertEqual(env["TEMP_DESK_PORTABLE_ROOT"], str(self.root))
        self.assertNotIn("cos/work/python-deps", env["PYTHONPATH"])
        with self.assertRaises(UnexportedLegacyPath): self.mount.physical(LEGACY + "/old/.env")

    def test_native_pandas_arrow_paths_are_redirected_and_hash_bound(self):
        import pandas as pd
        import pyarrow.parquet as pq
        source = self.root / "fixture.parquet"
        pd.DataFrame({"temperature": [72.25]}).to_parquet(source)
        logical = LEGACY + "/saved/observation.parquet"
        self.add(logical, source.read_bytes())
        self.mount = AssetMount(self.root, manifest=self.manifest)
        with self.mount:
            self.assertEqual(pd.read_parquet(logical).temperature.iloc[0], 72.25)
            self.assertEqual(pq.read_table(logical).to_pydict(), {"temperature": [72.25]})
            self.assertEqual(pq.ParquetFile(logical).read().to_pydict(), {"temperature": [72.25]})
            with self.assertRaises(UnexportedLegacyPath): pd.read_parquet(LEGACY + "/saved/not_exported.parquet")

    def test_low_level_descriptor_open_obeys_source_and_overlay_policy(self):
        with self.mount:
            fd = os.open(LEGACY + "/saved/temperature.json", os.O_RDONLY)
            try: self.assertEqual(os.read(fd, 100), b'{"temperature":72.25}')
            finally: os.close(fd)
            with self.assertRaises(PermissionError): os.open(LEGACY + "/saved/temperature.json", os.O_WRONLY)
            destination = AssetMount.LEGACY_RUNTIME + "/new.json"
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT)
            os.write(fd, b"{}");os.close(fd)
            self.assertEqual(Path(destination).read_bytes(), b"{}")

    def test_native_sqlite_connect_and_readonly_uri_have_no_legacy_fallback(self):
        import sqlite3
        source = self.root / "fixture.sqlite3"
        connection = sqlite3.connect(source)
        connection.execute("CREATE TABLE native (temperature REAL)")
        connection.execute("INSERT INTO native VALUES (72.25)")
        connection.commit(); connection.close()
        logical = LEGACY + "/saved/quote-index.sqlite3"
        self.add(logical, source.read_bytes())
        self.mount = AssetMount(self.root, manifest=self.manifest)
        with self.mount:
            for path, kwargs in ((logical, {}), ("file:" + logical + "?mode=ro", {"uri": True})):
                connection = sqlite3.connect(path, **kwargs)
                self.assertEqual(connection.execute("SELECT temperature FROM native").fetchone(), (72.25,))
                with self.assertRaises(sqlite3.OperationalError): connection.execute("UPDATE native SET temperature=99")
                connection.close()
            with self.assertRaises(UnexportedLegacyPath): sqlite3.connect(LEGACY + "/saved/not_exported.sqlite3")


def builtins_open(path):
    with open(path, "rb") as source: return source.read()


if __name__ == "__main__": unittest.main()
