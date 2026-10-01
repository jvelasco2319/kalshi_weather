"""Real orchestration restarts using explicitly synthetic fake-worker records."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from klax_lab.campaign import Campaign, campaign_status, request_pause, run_campaign
from klax_lab.provenance import sha256_file, write_json
from test_campaign import FakeWorker, fake_child, setup_fixture


def one_epoch(root):
    ready_path, pilot = setup_fixture(root)
    pilot["max_epochs"] = 1
    write_json(root / "configs/pilot.json", pilot)
    ready = json.loads(ready_path.read_text())
    ready["pilot_policy_sha256"] = sha256_file(root / "configs/pilot.json")
    write_json(ready_path, ready)
    return ready_path


class PausingWorker(FakeWorker):
    calls = 0
    def dispatch_one(self, controller, *args, **kwargs):
        type(self).calls += 1
        result = super().dispatch_one(controller, *args, **kwargs)
        if self.calls == 1:
            request_pause(controller.artifact_root)
        return result


def patched_worker(worker=FakeWorker):
    return patch("klax_lab.campaign.LocalTextWorker", worker)


def test_clean_pause_resumes_same_identity_and_reuses_successes():
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        ready = one_epoch(root)
        PausingWorker.calls = 0
        with patched_worker(PausingWorker), patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(Campaign, "fixed_child", fake_child):
            paused = run_campaign(root, ready)
            before = campaign_status(root)
            assert before["status"] == "PAUSED"
            assert before["spent"]["tokens"] == 16384
            ledger_before = json.loads((Path(paused["path"]) / "ledger.json").read_text())
            artifacts = {item["record"]["path"]: item["record"]["sha256"] for item in ledger_before["evidence"]}
            completed = run_campaign(root, ready, resume=True)
            assert completed["campaign_id"] == paused["campaign_id"]
            assert PausingWorker.calls == 8
            assert completed["local_model_calls"] == 8
            assert run_campaign(root, ready, resume=True) == completed
            assert PausingWorker.calls == 8
        assert all(sha256_file(root / path) == digest for path, digest in artifacts.items())
        after = campaign_status(root)
        assert after["spent"]["tokens"] == 8 * 16384
        assert before["created_at_unix"] == after["created_at_unix"]
        assert before["limits"] == after["limits"]
        ledger = json.loads((Path(completed["path"]) / "ledger.json").read_text())
        assert sum(row["kind"] == "CREATED" for row in ledger["events"]) == 1
        assert sum(row["kind"] == "RESUMED" for row in ledger["events"]) == 1
        assert all(row["state"] == "SUCCEEDED" for row in ledger["attempts"])


def test_actual_process_interruption_requires_review_and_preserves_spent_budget():
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        ready = one_epoch(root)
        source = Path(__file__).resolve().parents[1]
        script = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from klax_lab.campaign import Campaign, run_campaign
from test_campaign import FakeWorker, fake_child
class CrashWorker(FakeWorker):
    def dispatch_one(self, controller, campaign_id, worker_id, task_id, packet):
        assert controller.claim_task(campaign_id, worker_id, task_id=task_id)
        os._exit(91)
root=Path(sys.argv[1])
with patch('klax_lab.campaign.LocalTextWorker', CrashWorker), patch('klax_lab.campaign.load_runtime_spec', return_value=None), patch.object(Campaign, 'fixed_child', fake_child):
    run_campaign(root, root/'data/manifests/readiness.json')
"""
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(str(source / name) for name in ("src", "tests"))
        process = subprocess.run([sys.executable, "-c", script, str(root)], cwd=root, env=environment,
                                 capture_output=True, text=True, timeout=30,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert process.returncode == 91, process.stderr
        before = campaign_status(root)
        assert before["status"] == "ACTIVE"
        assert before["reserved"]["tokens"] == 16384
        with patched_worker() as worker, pytest.raises(ValueError, match="explicit recovery review"):
            run_campaign(root, ready, resume=True)
        evaluated = []
        def child(self, command, log, timeout=1200):
            evaluated.append(command[0])
            return fake_child(self, command, log, timeout)
        with patched_worker(), patch("klax_lab.campaign.load_runtime_spec", return_value=None), patch.object(Campaign, "fixed_child", child):
            completed = run_campaign(root, ready, resume=True, review_interrupted=True)
        after = campaign_status(root)
        assert completed["campaign_id"] == before["campaign_id"]
        assert completed["local_model_calls"] == 9  # Eight successes plus one unknown attempt.
        assert after["spent"]["tokens"] == 9 * 16384
        assert after["reserved"]["tokens"] == 0
        assert after["limits"] == before["limits"]
        assert evaluated.count("run") == 3  # The completed seed experiment was reused.
        assert evaluated.count("verify") == 4
        ledger = json.loads((Path(completed["path"]) / "ledger.json").read_text())
        failed = [row for row in ledger["attempts"] if row["state"] == "FAILED"]
        assert len(failed) == 1
        assert failed[0]["actual"] == failed[0]["reserved"]
        assert failed[0]["outcome"]["usage_unknown"] is True
        assert len(ledger["experiments"]) == 4


def test_committed_completion_recovers_ticket_without_any_dispatch():
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        ready = one_epoch(root)
        failed = False
        def interrupted_write(path, value):
            nonlocal failed
            if path.name == "offline_campaign_ticket.json" and value.get("status") == "COMPLETED" and not failed:
                failed = True
                raise OSError("Synthetic interruption after committed completion")
            write_json(path, value)
        with patched_worker(), patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(Campaign, "fixed_child", fake_child), patch("klax_lab.campaign.write_json", interrupted_write):
            with pytest.raises(OSError, match="after committed completion"):
                run_campaign(root, ready)
        status = campaign_status(root)
        assert status["status"] == "RESEARCH_CYCLE_COMPLETE"
        assert status["ticket_status"] == "FAILED"
        with patched_worker(), patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(Campaign, "fixed_child", side_effect=AssertionError("Completed experiment was redispatched")), \
             patch.object(FakeWorker, "dispatch_one", side_effect=AssertionError("Completed model task was redispatched")):
            result = run_campaign(root, ready, resume=True, review_interrupted=True)
        assert result["campaign_id"] == status["campaign_id"]
        assert campaign_status(root)["ticket_status"] == "COMPLETED"
        assert campaign_status(root)["spent"] == status["spent"]


@pytest.mark.parametrize("damage", ["artifact", "checkpoint", "policy", "registry_binding"])
def test_corrupted_or_stale_pause_fails_closed(damage):
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        ready = one_epoch(root)
        PausingWorker.calls = 0
        with patched_worker(PausingWorker), patch("klax_lab.campaign.load_runtime_spec", return_value=None), patch.object(Campaign, "fixed_child", fake_child):
            paused = run_campaign(root, ready)
            output = Path(paused["path"])
            if damage == "artifact":
                (output / "tasks/task-001/baseline-context.json").write_text("{}")
            elif damage in {"checkpoint", "registry_binding"}:
                with sqlite3.connect(output / "controller.sqlite3") as db:
                    if damage == "checkpoint":
                        db.execute("UPDATE coordinator_checkpoints SET payload='{}'")
                    else:
                        db.execute("UPDATE campaigns SET provenance='{}'")
                db.close()
            else:
                (root / "configs/pilot.json").write_text("{}")
            with pytest.raises(ValueError):
                run_campaign(root, ready, resume=True)
            assert PausingWorker.calls == 1
        assert json.loads((root / "data/manifests/offline_campaign_ticket.json").read_text())["status"] == "PAUSED"
