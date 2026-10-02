"""Clean-clone restoration and protection of registered scientific inputs."""
from hashlib import sha256
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from scripts.prepare_v10_dashboard import MANIFEST, prepare

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("b")
    manifest = json.loads((ROOT / MANIFEST).read_text())
    sources = {str(MANIFEST)}
    sources.update(relative for relative in manifest["input_bindings"] if relative not in manifest["runtime_assets"])
    sources.update(asset["source"] for asset in manifest["runtime_assets"].values())
    for relative in sources:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    return tmp_path, manifest


def test_clean_clone_restores_exact_frozen_inputs_without_live_records(bundle):
    root, manifest = bundle
    result = prepare(root)
    assert result["verified_inputs"] == 34
    assert result["restored_assets"] == 9
    assert result["model_refitted"] is False
    for relative, expected in manifest["input_bindings"].items():
        assert sha256((root / relative).read_bytes()).hexdigest() == expected
    assert not (root / "runs/v10_ui").exists()
    assert not (root / "runs/v10_online").exists()
    assert not (root / "data").exists()
    assert prepare(root, verify_only=True)["restored_assets"] == 0


def test_changed_existing_input_is_rejected_and_never_replaced(bundle):
    root, manifest = bundle
    prepare(root)
    target = root / next(iter(manifest["runtime_assets"]))
    target.write_bytes(b"changed existing evidence")
    with pytest.raises(ValueError, match="missing or changed"):
        prepare(root)
    assert target.read_bytes() == b"changed existing evidence"


def test_clean_dashboard_boot_needs_only_bundled_inputs(bundle):
    from v10_online import runner
    from v10_ui.server import make_server
    from v10_ui.service import DashboardService
    root, _ = bundle
    prepare(root)
    registration = runner.register(root)
    assert registration["orders"] == 0
    assert runner.register(root) == registration
    service = DashboardService(root, now=lambda: datetime(2026, 10, 1, 20, tzinfo=timezone.utc))
    server = make_server(root, port=0, service=service)
    try:
        state = server.application.service.state()
        assert state["orders"] == 0
        assert state["trades"]["practice"]["cash"] == 100
        assert state["month"]["planned_days"] == 30
        assert state["month"]["forecasts_saved"] == 0
    finally:
        server.application.close()
        server.server_close()


@pytest.mark.parametrize("kind", ["code", "asset"])
def test_tampered_bundle_is_rejected_before_runtime_writes(bundle, kind):
    root, manifest = bundle
    relative = "v10_online/model.py" if kind == "code" else next(iter(manifest["runtime_assets"].values()))["source"]
    (root / relative).write_bytes(b"changed bundle")
    with pytest.raises(ValueError, match="missing or changed"):
        prepare(root)
    assert not (root / "runs").exists()


def test_verify_only_does_not_restore_missing_inputs(bundle):
    root, _ = bundle
    with pytest.raises(ValueError, match="Run setup"):
        prepare(root, verify_only=True)
    assert not (root / "runs").exists()


def test_manifest_cannot_escape_project(bundle):
    root, manifest = bundle
    manifest["input_bindings"] = {"../outside": "0" * 64}
    (root / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes the project"):
        prepare(root)
    assert not (root / "runs").exists()
