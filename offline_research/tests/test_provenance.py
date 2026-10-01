import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from klax_lab.provenance import freeze_manifest, verify_manifest


class ProvenanceTests(unittest.TestCase):
    def test_corruption_and_path_escape_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "input.csv"
            data.write_text("original")
            manifest = freeze_manifest(root, [data], {"split": "fixed"}, root / "manifest.json")
            verify_manifest(root, manifest)
            data.write_text("modified")
            with self.assertRaises(ValueError):
                verify_manifest(root, manifest)
            with self.assertRaises(ValueError):
                freeze_manifest(root, [data], {"split": "fixed"}, root / "manifest.json")

    def test_child_offline_and_holdout_denial(self):
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "protected.txt"
            secret.write_text("synthetic holdout")
            script = '''
import socket, subprocess, sys
from pathlib import Path
from klax_lab.offline import install_guard
install_guard((Path(sys.argv[1]),))
checks = [lambda: socket.create_connection(('example.com',443)), lambda: Path(sys.argv[1]).read_text(), lambda: subprocess.run([sys.executable,'-V'])]
for check in checks:
    try:
        check()
    except PermissionError:
        continue
    raise AssertionError('offline guard allowed prohibited operation')
print('3 prohibitions verified')
'''
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            result = subprocess.run([sys.executable, "-c", script, str(secret)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("3 prohibitions verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
