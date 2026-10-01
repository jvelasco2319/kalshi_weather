"""One finite authorized historical job from archive repair through reporting.

This is an ordinary batch pipeline, not a scheduler or live monitor. The real
agent campaign runs only after the independently checked readiness gate passes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .provenance import canonical_hash, inventory, write_json
from .readiness import terminal_weather_run


@contextmanager
def job_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def saved_completed_campaign(root: Path) -> dict:
    """Verify a finished ticket without constructing a worker or republishing readiness."""
    from .campaign import _ticket_binding
    v2_path = root / "data/manifests/offline_campaign_v2_ticket.json"
    if v2_path.exists():
        from .campaign_v2 import _completed
        lock, path, completed = root / "data/offline_campaign_v2.lock", v2_path, _completed
    else:
        from .campaign import _completed_ticket_result
        lock, path, completed = (root / "data/offline_campaign.lock",
                                 root / "data/manifests/offline_campaign_ticket.json",
                                 _completed_ticket_result)
    with job_lock(lock):
        ticket = json.loads(path.read_text(encoding="utf-8"))
        if ticket.get("status") != "COMPLETED":
            raise RuntimeError("Only a completed campaign can enter the saved-result continuation")
        binding = _ticket_binding(root, root / "data/manifests/readiness.json")
        return completed(root, ticket, binding)


def run(root: Path, timeout_hours: float = 7) -> dict:
    if not 0 < timeout_hours <= 8:
        raise ValueError("Finite pipeline requires a deadline no longer than8hours")
    root = root.resolve()
    def current_code_policy_hash():
        paths = [*sorted((root / "src/klax_lab").glob("*.py")), root / "configs/evaluation.json", root / "configs/pilot.json"]
        return canonical_hash(inventory(root, paths))
    code_hash = current_code_policy_hash()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "runs" / ("historical-pipeline-" + stamp)
    output.mkdir(parents=True)
    state = {"status": "WAITING_FOR_HISTORICAL_ARCHIVE", "started_at_utc": datetime.now(timezone.utc).isoformat(),
             "code_policy_sha256": code_hash, "timeout_hours": timeout_hours,
             "goal1_complete": False, "goal2_complete": False, "steps": []}
    state_path = output / "state.json"
    deadline = time.monotonic() + timeout_hours * 3600

    def save():
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(state_path, state)

    def unchanged():
        if current_code_policy_hash() != code_hash:
            raise RuntimeError("Code or policy changed after pipeline launch; review and restart explicitly")

    def step(command: str, extra: list[str] = ()):
        unchanged()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Historical batch deadline reached")
        state["status"] = "RUNNING_" + command.upper().replace("-", "_")
        save()
        log = output / (command + ".log")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        error_log = log.with_suffix(".stderr.log")
        with log.open("w", encoding="utf-8") as stream, error_log.open("w", encoding="utf-8") as errors:
            result = subprocess.run([sys.executable, "-m", "klax_lab.cli", command, "--root", str(root), *extra],
                                    cwd=root, env=env, stdout=stream, stderr=errors,
                                    timeout=min(1800, remaining), close_fds=True)
        state["steps"].append({"command": command, "returncode": result.returncode, "log": str(log.relative_to(root))})
        save()
        if result.returncode:
            raise RuntimeError(f"{command} failed; inspect {log.name}")

    def module_step(name: str, module: str, extra: list[str] = (), timeout: int = 1800):
        unchanged()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Historical batch deadline reached")
        state["status"] = "RUNNING_" + name.upper()
        save()
        log = output / (name + ".log")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root / "src")
        error_log = log.with_suffix(".stderr.log")
        with log.open("w", encoding="utf-8") as stream, error_log.open("w", encoding="utf-8") as errors:
            result = subprocess.run([sys.executable, "-m", "klax_lab." + module, "--root", str(root), *extra],
                cwd=root, env=env, stdout=stream, stderr=errors,
                timeout=min(timeout, remaining), close_fds=True)
        state["steps"].append({"command": name, "returncode": result.returncode, "log": str(log.relative_to(root))})
        save()
        if result.returncode:
            raise RuntimeError(f"{name} failed; inspect {log.name}")
        return json.loads(log.read_text(encoding="utf-8"))

    save()
    try:
        with job_lock(root / "data/historical_pipeline.lock"):
            v2_ticket = root / "data/manifests/offline_campaign_v2_ticket.json"
            v1_ticket = root / "data/manifests/offline_campaign_ticket.json"
            ticket_path = v2_ticket if v2_ticket.exists() or not v1_ticket.exists() else v1_ticket
            if ticket_path.exists():
                ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
                state["existing_ticket_status"] = ticket.get("status")
                if ticket.get("status") == "PAUSED":
                    state.update(status="OFFLINE_CAMPAIGN_PAUSED", resume_required=True,
                        resume_command="python -m klax_lab.campaign --root . --action resume")
                    return state
                if ticket.get("status") != "COMPLETED":
                    raise RuntimeError("Existing campaign needs explicit interruption review/resume; frozen readiness is preserved")
                unchanged()
                state["status"] = "VERIFYING_COMPLETED_CAMPAIGN"
                save()
                campaign = saved_completed_campaign(root)
                log = output / "reused_campaign.json"
                write_json(log, campaign)
                state["steps"].append({"command": "reuse_completed_campaign", "returncode": 0, "log": str(log.relative_to(root))})
                state["original_readiness_preserved"] = True
            else:
                while terminal_weather_run(root) is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Historical archive did not finish within the finite pipeline deadline")
                    time.sleep(min(30, max(0, deadline - time.monotonic())))
                unchanged()
                # Repair only explicitly incomplete historical dates. The final
                # whole-range pass is offline and must transfer zero bytes.
                repair = module_step("weather_repair", "repair_weather", timeout=1800)
                if repair["status"] not in ("COMPLETE", "INCOMPLETE") or repair["integrity_transfer_bytes"] != 0:
                    raise RuntimeError("Historical archive repair/integrity gate failed")
                state["weather_repair"] = repair
                # Remaining gaps stay explicit; registered per-partition coverage
                # and quarantine gates decide whether the data can support analysis.
                step("prepare")
                step("baseline")
                baseline = json.loads((output / "baseline.log").read_text(encoding="utf-8"))
                state["baseline_run"] = baseline["path"]
                step("replicate", ["--run-directory", baseline["path"]])
                step("readiness", ["--run-directory", baseline["path"]])
                ready = json.loads((output / "readiness.log").read_text(encoding="utf-8"))
                if ready.get("status") != "READY_FOR_OFFLINE_CAMPAIGN":
                    raise RuntimeError("Goal 1 readiness did not pass; no historical model dispatch")
                state["goal1_complete"] = True
                campaign = module_step("campaign", "campaign", timeout=21600)
            state["goal1_complete"] = True
            state["campaign_run"] = campaign["path"]
            if campaign.get("status") == "PAUSED":
                state.update(status="OFFLINE_CAMPAIGN_PAUSED", resume_required=True,
                    resume_command="python -m klax_lab.campaign --root . --action resume")
                # A clean operator pause is unfinished work, not a completed
                # research cycle and never permission to score the holdout.
                return state
            if campaign.get("status") != "RESEARCH_CYCLE_COMPLETE":
                raise RuntimeError("Campaign did not complete its bounded evidence cycle")
            final_args = []
            if campaign.get("champion") is not None:
                final = module_step("protected_final", "final_evaluation",
                    ["--campaign-summary", str(Path(campaign["path"]) / "summary.json")])
                state["protected_final_report"] = final["report_path"]
                final_args = ["--final-report", final["report_path"]]
            report = module_step("report", "reporting", ["--campaign-directory", campaign["path"], *final_args])
            if report.get("status") != "OFFLINE_CAMPAIGN_COMPLETE":
                raise RuntimeError("The final research report is incomplete")
            state.update(status="OFFLINE_CAMPAIGN_COMPLETE", goal2_complete=True, report=report)
            write_json(root / "data/manifests/project_completion.json", report)
    except Exception as error:
        state["status"] = "STOPPED_WITH_ACTION_REQUIRED"
        state["error"] = f"{type(error).__name__}: {error}"
    finally:
        state["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout-hours", type=float, default=7)
    args = parser.parse_args()
    result = run(args.root, args.timeout_hours)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "OFFLINE_CAMPAIGN_COMPLETE" else 1)
