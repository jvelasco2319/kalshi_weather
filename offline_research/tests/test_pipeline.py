from types import SimpleNamespace
import json
import pytest
from klax_lab import pipeline


def project(tmp_path):
    (tmp_path / "src/klax_lab").mkdir(parents=True)
    (tmp_path / "src/klax_lab/fixture.py").write_text("# synthetic fixture\n")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/evaluation.json").write_text("{}")
    (tmp_path / "configs/pilot.json").write_text("{}")
    return tmp_path


def test_finite_wait_stops_without_launching_analysis(tmp_path, monkeypatch):
    root = project(tmp_path)
    clock = [0.]
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    monkeypatch.setattr(pipeline, "terminal_weather_run", lambda root: None)
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Must not spawn")))
    state = pipeline.run(root, timeout_hours=0.001)
    assert state["status"] == "STOPPED_WITH_ACTION_REQUIRED"
    assert "deadline" in state["error"] and not state["steps"]


def test_code_change_requires_explicit_review_and_restart(tmp_path, monkeypatch):
    root = project(tmp_path)
    def finished(_root):
        (root / "src/klax_lab/fixture.py").write_text("# changed after launch\n")
        return {"status": "COMPLETED"}
    monkeypatch.setattr(pipeline, "terminal_weather_run", finished)
    state = pipeline.run(root, timeout_hours=0.001)
    assert state["status"] == "STOPPED_WITH_ACTION_REQUIRED"
    assert "Code or policy changed" in state["error"] and not state["steps"]


def test_added_source_file_changes_the_launch_snapshot(tmp_path, monkeypatch):
    root = project(tmp_path)
    def finished(_root):
        (root / "src/klax_lab/new.py").write_text("# added after launch\n")
        return {"status": "COMPLETED"}
    monkeypatch.setattr(pipeline, "terminal_weather_run", finished)
    state = pipeline.run(root, timeout_hours=0.001)
    assert state["status"] == "STOPPED_WITH_ACTION_REQUIRED"
    assert "Code or policy changed" in state["error"] and not state["steps"]


def test_failed_readiness_never_dispatches_agents(tmp_path, monkeypatch):
    root = project(tmp_path)
    called = []
    monkeypatch.setattr(pipeline, "terminal_weather_run", lambda root: {"status": "COMPLETED_WITH_GAPS"})
    def execute(argv, **kwargs):
        name = argv[3] if argv[2] == "klax_lab.cli" else argv[2]
        called.append(name)
        if name == "klax_lab.repair_weather":
            report = {"status": "INCOMPLETE", "integrity_transfer_bytes": 0, "remaining_gap_count": 1}
        elif name == "baseline":
            report = {"path": str(root / "runs/baselines-synthetic")}
        elif name == "readiness":
            report = {"status": "NOT_READY_FOR_OFFLINE_CAMPAIGN"}
        elif name in ("prepare", "replicate"):
            report = {"status": "PASS"}
        else:
            raise AssertionError("Agent/final module must never run before readiness")
        json.dump(report, kwargs["stdout"])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.subprocess, "run", execute)
    state = pipeline.run(root, timeout_hours=.01)
    assert state["status"] == "STOPPED_WITH_ACTION_REQUIRED"
    assert state["goal1_complete"] is False and state["goal2_complete"] is False
    assert called[-1] == "readiness"
    assert state["weather_repair"]["remaining_gap_count"] == 1


def test_clean_campaign_pause_never_opens_final_or_reports_completion(tmp_path, monkeypatch):
    root = project(tmp_path)
    called = []
    monkeypatch.setattr(pipeline, "terminal_weather_run", lambda root: {"status": "COMPLETED"})
    def execute(argv, **kwargs):
        name = argv[3] if argv[2] == "klax_lab.cli" else argv[2]
        called.append(name)
        if name == "klax_lab.repair_weather":
            report = {"status": "COMPLETE", "integrity_transfer_bytes": 0}
        elif name == "baseline":
            report = {"path": str(root / "runs/baselines-synthetic")}
        elif name == "readiness":
            report = {"status": "READY_FOR_OFFLINE_CAMPAIGN"}
        elif name == "klax_lab.campaign":
            report = {"status": "PAUSED", "path": str(root / "runs/campaigns/synthetic"), "goal2_complete": False}
        elif name in ("prepare", "replicate"):
            report = {"status": "PASS"}
        else:
            raise AssertionError("Final evaluation and reporting must not follow an unfinished campaign")
        json.dump(report, kwargs["stdout"])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.subprocess, "run", execute)
    state = pipeline.run(root, timeout_hours=.01)
    assert state["status"] == "OFFLINE_CAMPAIGN_PAUSED"
    assert state["goal1_complete"] and not state["goal2_complete"]
    assert state["resume_required"] and "finished_at_utc" in state
    assert called[-1] == "klax_lab.campaign"
    assert not (root / "data/manifests/project_completion.json").exists()


@pytest.mark.parametrize("status", ["PAUSED", "ACTIVE", "FAILED"])
def test_existing_unfinished_ticket_preserves_readiness_without_rebuilding(tmp_path, monkeypatch, status):
    root = project(tmp_path)
    manifests = root / "data/manifests"
    manifests.mkdir(parents=True)
    (manifests / "offline_campaign_ticket.json").write_text(json.dumps({"status": status}))
    ready = manifests / "readiness.json"
    ready.write_text('{"synthetic_fixture":"exact original readiness bytes"}')
    original = ready.read_bytes()
    monkeypatch.setattr(pipeline, "terminal_weather_run", lambda root: (_ for _ in ()).throw(AssertionError("Cannot reacquire")))
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Cannot rebuild or dispatch")))
    result = pipeline.run(root, timeout_hours=.01)
    assert result["status"] == ("OFFLINE_CAMPAIGN_PAUSED" if status == "PAUSED" else "STOPPED_WITH_ACTION_REQUIRED")
    assert ready.read_bytes() == original
    assert not result["goal2_complete"] and not result["steps"]


def test_completed_campaign_continues_to_report_without_republishing_readiness(tmp_path, monkeypatch):
    root = project(tmp_path)
    manifests = root / "data/manifests"
    manifests.mkdir(parents=True)
    (manifests / "offline_campaign_ticket.json").write_text('{"status":"COMPLETED"}')
    ready = manifests / "readiness.json"
    ready.write_text('{"synthetic_fixture":"exact original readiness bytes"}')
    original = ready.read_bytes()
    campaign = {"status": "RESEARCH_CYCLE_COMPLETE", "path": str(root / "runs/campaigns/synthetic"), "champion": None}
    monkeypatch.setattr(pipeline, "saved_completed_campaign", lambda root: campaign)
    monkeypatch.setattr(pipeline, "terminal_weather_run", lambda root: (_ for _ in ()).throw(AssertionError("No repeated prerequisite")))
    called = []
    def execute(argv, **kwargs):
        called.append(argv[2])
        assert argv[2] == "klax_lab.reporting"
        json.dump({"status": "OFFLINE_CAMPAIGN_COMPLETE", "synthetic_fixture": True}, kwargs["stdout"])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.subprocess, "run", execute)
    result = pipeline.run(root, timeout_hours=.01)
    assert result["status"] == "OFFLINE_CAMPAIGN_COMPLETE"
    assert result["original_readiness_preserved"]
    assert ready.read_bytes() == original
    assert called == ["klax_lab.reporting"]
