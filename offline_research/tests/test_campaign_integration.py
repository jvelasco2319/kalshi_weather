"""Synthetic outer campaign checks; no real market data or model inference.

Only the text worker's inference is replaced. Baseline and replication CLIs,
readiness validation, controller, task transitions, candidate experiment CLIs,
offline guards, artifact hashing and the one-use ticket execute normally.
The runtime bytes and capability report are explicitly invented test fixtures.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from klax_lab.campaign import Campaign, campaign_status, request_pause, run_campaign
from klax_lab.local_backend import (
    BACKEND, PROTOCOL, REQUIRED_FLAGS, LocalTextWorker, RuntimeFile, RuntimeSpec,
    canonical_json, parse_response, save_runtime_spec, verify_runtime,
)
from klax_lab.provenance import sha256_file, write_json
from klax_lab.readiness import publish_readiness
from test_baseline_integration import SOURCE_ROOT, build_synthetic_project
from test_campaign import FakeWorker as RegistryFakeWorker, setup_fixture


class FakeWorker(LocalTextWorker):
    """Keep real dispatch/readiness/schema gates, replace only native inference."""
    calls = 0
    pause_after = None

    def _run(self, packet, output_dir, timeout_seconds):
        type(self).calls += 1
        self._unchanged()
        options = packet["candidate_options"]
        # Explicit rejection makes the negative-result/report branch deterministic.
        action = "reject" if packet["role"] == "critic" else "propose" if options else "abstain"
        response = {"protocol": PROTOCOL, "task_id": packet["task_id"], "action": action,
                    "candidate": options[0] if action == "propose" else None,
                    "rationale": "Synthetic fixture response only; no model research or profitability evidence",
                    "evidence_ids": [row["evidence_id"] for row in packet["evidence"]],
                    "limitations": ["Fake inference on entirely invented weather and market observations"]}
        response = parse_response(canonical_json(response), packet, self.limits)
        output_dir.mkdir(parents=True, exist_ok=False)
        record = {"response": response, "process": {"elapsed_seconds": 0.01}, "synthetic_fixture": True}
        write_json(output_dir / "proposal.json", record)
        write_json(output_dir / "packet.json", packet)
        if type(self).calls == type(self).pause_after:
            request_pause(self.runtime.executable.path.parent.parent)
        return record


def synthetic_runtime(root):
    folder = root / "synthetic_runtime"
    folder.mkdir()
    def file(name, content):
        path = folder / name
        path.write_text(content, encoding="utf-8")
        return RuntimeFile(path, sha256_file(path))
    spec = RuntimeSpec(file("llama-completion.exe", "SYNTHETIC EXECUTABLE; NEVER EXECUTED"),
                       file("synthetic.gguf", "SYNTHETIC MODEL; NO MODEL WEIGHTS"),
                       file("help.txt", "\n".join(REQUIRED_FLAGS)),
                       (file("synthetic.dll", "SYNTHETIC LIBRARY; NEVER LOADED"),))
    spec_path = save_runtime_spec(root, spec, "data/models/runtime_spec.json")
    probe_path = root / "runs/local_worker_probe/synthetic/capability-report.json"
    write_json(probe_path, {
        "synthetic_fixture": "Invented capability record for integration gating only, not a real model probe",
        "status": "PASS", "synthetic": True, "backend": BACKEND,
        "runtime_sha256": verify_runtime(spec)["runtime_sha256"],
        "backend_code_sha256": sha256_file(root / "src/klax_lab/local_backend.py"),
        "tool_catalog": [], "model_canary_exposed": False,
        "negative_capability_tests_passed": True, "actual_model_probe_passed": True,
    })
    return spec_path, probe_path


def cli(root, command, *args):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    process = subprocess.run([sys.executable, "-m", "klax_lab.cli", command, "--root", str(root), *args],
                             cwd=root, env=environment, capture_output=True, text=True, timeout=90,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
    return json.loads(process.stdout)


def test_actual_synthetic_outer_pipeline_and_idempotent_ticket():
    with TemporaryDirectory(prefix="klax-synthetic-campaign-") as folder:
        root = Path(folder).resolve()
        build_synthetic_project(root)
        pilot = json.loads((SOURCE_ROOT / "configs/pilot.json").read_text(encoding="utf-8"))
        pilot["max_epochs"] = 1
        pilot["fixture_notice"] = "Synthetic integration only; this temporary policy is not the real pilot"
        write_json(root / "configs/pilot.json", pilot)
        baseline = cli(root, "baseline")
        baseline_path = Path(baseline["path"])
        replica = cli(root, "replicate", "--run-directory", str(baseline_path))
        assert replica["status"] == "PASS"
        spec_path, probe_path = synthetic_runtime(root)
        # The real full-suite run cannot recursively run itself inside this
        # integration test. Its acceptance envelope is covered separately by
        # test_acceptance; all historical-baseline and worker gates below remain
        # real synthetic integration checks, with no actual market data.
        with patch("klax_lab.acceptance.acceptance_evidence", return_value={
            "status": "PASS", "synthetic_fixture": "Acceptance envelope stub only; not real readiness evidence",
        }):
            ready = publish_readiness(root, baseline_path, probe_path, spec_path)
        assert ready["status"] == "READY_FOR_OFFLINE_CAMPAIGN", ready
        readiness_path = root / "data/manifests/readiness.json"
        FakeWorker.calls = 0
        FakeWorker.pause_after = 1
        with patch("klax_lab.campaign.LocalTextWorker", FakeWorker):
            paused = run_campaign(root, readiness_path)
            assert paused["status"] == "PAUSED"
            assert campaign_status(root)["status"] == "PAUSED"
            assert FakeWorker.calls == 1
            report = run_campaign(root, readiness_path, resume=True)
            assert report["campaign_id"] == paused["campaign_id"]
            assert FakeWorker.calls == 8
            calls = FakeWorker.calls
            assert run_campaign(root, readiness_path) == report
            assert FakeWorker.calls == calls
        FakeWorker.pause_after = None
        # Pipeline continuation must reuse the original one-use readiness binding
        # after a paused campaign completes, without rebuilding prerequisites.
        from klax_lab.pipeline import saved_completed_campaign
        frozen_readiness = readiness_path.read_bytes()
        with patch("klax_lab.campaign.LocalTextWorker", side_effect=AssertionError("Saved continuation constructed a worker")):
            assert saved_completed_campaign(root) == report
        assert readiness_path.read_bytes() == frozen_readiness
        assert report["candidate_count"] == 4
        assert report["local_model_calls"] == 8
        assert report["scientific_conclusion"] == "NO_IMPROVEMENT"
        assert report["champion"] is None
        assert not report["goal2_complete"]
        assert not report["protected_final_evaluated"]
        assert not (root / "data/normalized/protected_final").exists()
        output = Path(report["path"])
        ticket = json.loads((root / "data/manifests/offline_campaign_ticket.json").read_text())
        assert ticket["status"] == "COMPLETED"
        assert ticket["campaign_id"] == report["campaign_id"]
        assert ticket["binding"]["dataset_sha256"] == ready["dataset_sha256"]
        assert len(list((root / "runs/campaigns").iterdir())) == 1
        for item in ticket["artifacts"]:
            assert sha256_file(root / item["path"]) == item["sha256"]
        ledger = json.loads((output / "ledger.json").read_text())
        assert len(ledger["experiments"]) == 4
        assert all(row["state"] == "REJECTED" for row in ledger["hypotheses"])
        assert all(row["state"] == "SUCCEEDED" for row in ledger["tasks"])
        assert {row["spec"]["role"] for row in ledger["tasks"]} == {
            "auditor", "implementer", "critic", "replicator", "explorer", "synthesizer"}
        assert sum(row["actual"]["tokens"] for row in ledger["attempts"]) == 8 * pilot["context_tokens_per_call"]
        assert sum(row["actual"]["paid_micros"] for row in ledger["attempts"]) == 0
        assert len(ledger["messages"]) == 4
        for row in json.loads((output / "candidate_register.json").read_text()):
            experiment = Path(row["experiment_path"])
            assert experiment.name == row["experiment_id"]
            report_row = json.loads((experiment / "summary.json").read_text())
            assert len(report_row["predeclared_cost_sensitivity"]) == 12
            verification = json.loads(Path(row["replication_path"]).read_text())
            assert verification["status"] == "PASS"
            assert verification["primary_candidate_selection_verified"]
            assert verification["daywise_scores_verified"] == 177
            assert len(verification["scenarios"]) == 13
            assert "critic_rejected_or_abstained" in row["gate"]["reasons"]
            for guard_path in (experiment / "offline_checks.json", Path(row["replication_path"]).parent / "offline_checks.json"):
                checks = json.loads(guard_path.read_text())
                assert all(checks[key] is True for key in ("python_socket_denied", "protected_file_open_denied", "child_process_denied"))
        from klax_lab.reporting import write_research_report
        completion = write_research_report(root, output)
        assert completion["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
        assert completion["scientific_conclusion"] == "NO_IMPROVEMENT"
        assert completion["protected_final_evaluated"] is False
        assert completion["actual_account_gains"] == "not_measured_no_orders"
        assert "None of the 4 tested candidates" in Path(completion["document_path"]).read_text(encoding="utf-8")
        # Mutation of the saved completion cannot silently produce another run.
        (output / "summary.json").write_text("{}", encoding="utf-8")
        with patch("klax_lab.campaign.LocalTextWorker") as worker, pytest.raises(ValueError, match="artifact changed"):
            run_campaign(root, readiness_path)
        worker.assert_not_called()


@pytest.mark.parametrize("status", ["ACTIVE", "FAILED"])
def test_spent_unfinished_ticket_blocks_new_worker(status):
    from klax_lab.campaign import _ticket_binding
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        readiness, _ = setup_fixture(root)
        write_json(root / "data/manifests/offline_campaign_ticket.json", {
            "ticket_version": "offline-campaign-ticket-v1", "status": status,
            "binding": _ticket_binding(root, readiness), "campaign_id": "spent-pilot",
            "campaign_path": "runs/campaigns/spent-pilot"})
        with patch("klax_lab.campaign.LocalTextWorker") as worker, pytest.raises(ValueError, match="explicit failure audit"):
            run_campaign(root, readiness)
        worker.assert_not_called()


def test_ticket_is_reserved_before_failure_and_cannot_reset_search():
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        readiness, _ = setup_fixture(root)
        def fail_run(campaign):
            ticket = json.loads((root / "data/manifests/offline_campaign_ticket.json").read_text())
            assert ticket["status"] == "ACTIVE"
            assert ticket["campaign_id"] == campaign.id
            campaign.controller.close()
            raise RuntimeError("Synthetic dispatch failure")
        with patch("klax_lab.campaign.LocalTextWorker", RegistryFakeWorker), \
             patch("klax_lab.campaign.load_runtime_spec", return_value=None), patch.object(Campaign, "run", fail_run):
            with pytest.raises(RuntimeError, match="Synthetic dispatch failure"):
                run_campaign(root, readiness)
        ticket = json.loads((root / "data/manifests/offline_campaign_ticket.json").read_text())
        assert ticket["status"] == "FAILED"
        with patch("klax_lab.campaign.LocalTextWorker") as worker, pytest.raises(ValueError, match="explicit failure audit"):
            run_campaign(root, readiness)
        worker.assert_not_called()


def test_fixed_child_separates_warnings_from_json_stdout():
    with TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        readiness, _ = setup_fixture(root)
        with patch("klax_lab.campaign.LocalTextWorker", RegistryFakeWorker), \
             patch("klax_lab.campaign.load_runtime_spec", return_value=None):
            campaign = Campaign(root, readiness)
        def child(*args, stdout, stderr, **kwargs):
            stdout.write('{"synthetic": true}')
            stderr.write("Synthetic library warning; must not corrupt result JSON\n")
            return subprocess.CompletedProcess(args, 0)
        log = campaign.output / "synthetic.log"
        try:
            with patch("klax_lab.campaign.subprocess.run", child):
                result, _ = campaign.fixed_child([], log)
            assert result == {"synthetic": True}
            assert "library warning" in log.with_suffix(".log.stderr").read_text()
        finally:
            campaign.controller.close()
