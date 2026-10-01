"""Local, finite preparation and offline research commands. No trading paths."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys

from .provenance import write_json


def status(root: Path) -> dict:
    reports = {}
    for name in (
            "kalshi_integrity", "normalization_report", "climate_normalization",
            "forecast_normalization", "market_quality", "readiness",
            "project_completion", "v3_implementation_status"):
        path = root / "data/manifests" / (name + ".json")
        if path.exists():
            reports[name] = json.loads(path.read_text(encoding="utf-8"))
    weather = root / "data/normalized/weather"
    counts = {model: sum(1 for _ in weather.glob(model + "_*_sampled_temperature.json")) for model in ("gfs", "nbm")}
    v3 = reports.get("v3_implementation_status", {})
    return {"as_of_utc": datetime.now(timezone.utc).isoformat(), "scope": "historical_offline_only",
            "weather_feature_files": counts, "planned_weather_days_per_model": 731,
            "reports": reports, "goal1_complete": reports.get("readiness", {}).get("goal1_complete", False),
            "goal2_complete": reports.get("project_completion", {}).get("goal2_complete", False),
            "v3_status": v3.get("status", "NOT_REGISTERED"),
            "v3_ready_for_campaign": v3.get("ready_for_v3_campaign", False),
            "v3_protected_final_access_authorized": v3.get("protected_final_access_authorized", False),
            "trading_components": "absent", "remote_repository": "not_created"}


def run_guard_checks(root: Path) -> dict:
    """Check reviewed Python execution boundary, not surrounding agent tools."""
    from .offline import install_guard
    protected = (root / "data/normalized/protected_final", root / "data/raw",
                 root / "data/normalized/weather", root / "runs/protected_evaluator")
    install_guard(protected)
    checks = {}
    attempts = {
        "python_socket_denied": lambda: socket.socket(),
        "protected_file_open_denied": lambda: (protected[0] / "labels/outcomes.parquet").read_bytes(),
        "child_process_denied": lambda: subprocess.run([sys.executable, "-c", "pass"], check=True),
    }
    for key, action in attempts.items():
        try:
            action()
        except PermissionError:
            checks[key] = True
        else:
            raise RuntimeError("Offline boundary check failed: " + key)
    checks["scope"] = "Reviewed Python process only; not OS isolation or surrounding Codex tools"
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kalshi LAX historical research")
    parser.add_argument("command", choices=(
        "status", "prepare", "baseline", "replicate", "controller-fixture",
        "probe-worker", "readiness", "v3-status", "v3-fixtures",
    ))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--capability-probe", type=Path)
    parser.add_argument("--runtime-spec", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    policy = json.loads((root / "configs/evaluation.json").read_text(encoding="utf-8"))
    from .policy import validate_evaluation_policy
    validate_evaluation_policy(policy)
    if args.command == "status":
        report = status(root)
    elif args.command == "v3-status":
        from .v3_readiness import publish_v3_implementation_status
        report = publish_v3_implementation_status(root)
    elif args.command == "v3-fixtures":
        from .component_fixtures_v3 import publish_component_fixtures
        model, market = publish_component_fixtures(root)
        report = {
            "status": "PASS",
            "model_fixture_sha256": model["evidence_sha256"],
            "market_fixture_sha256": market["evidence_sha256"],
            "campaign_authorized": False,
            "protected_final_access_authorized": False,
        }
    elif args.command == "prepare":
        from .dataset import normalize
        from .climate import normalize_climate
        from .forecast_dataset import normalize_forecasts
        from .quality import check_market_quality
        report = {"market": normalize(root, policy), "climate": normalize_climate(root, policy), "weather": normalize_forecasts(root, policy), "market_quality": check_market_quality(root, policy)}
    elif args.command == "baseline":
        # Load approved native libraries before the irreversible Python audit hook.
        from .baseline import run_development_baselines
        checks = run_guard_checks(root)
        report = run_development_baselines(root, policy)
        write_json(Path(report["path"]) / "offline_checks.json", checks)
    elif args.command == "controller-fixture":
        from .controller import run_fixture_campaign
        destination = root / "runs" / ("controller-fixture-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        report = run_fixture_campaign(destination)
    elif args.command == "probe-worker":
        from .local_backend import (LocalTextWorker, load_runtime_spec,
                                    run_synthetic_capability_probe,
                                    run_synthetic_v2_protocol_probe,
                                    run_synthetic_v3_protocol_probe)
        from .provenance import sha256_file
        runtime_path = args.runtime_spec or root / "data/models/runtime_spec.json"
        worker = LocalTextWorker(load_runtime_spec(root, runtime_path))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = root / "runs/local_worker_probe" / stamp
        capability = run_synthetic_capability_probe(worker, destination)
        v2 = run_synthetic_v2_protocol_probe(worker, destination)
        v3 = run_synthetic_v3_protocol_probe(worker, destination)
        report = {"status": "PASS" if capability["status"] == v2["status"] == v3["status"] == "PASS" else "FAIL",
                  "capability": capability, "v2_protocol": v2, "v3_protocol": v3,
                  "path": str(destination), "goal1_complete": False, "goal2_complete": False}
        write_json(root / "data/manifests/local_worker_probe.json", {
            "status": report["status"],
            "capability_probe_path": (destination / "capability-report.json").relative_to(root).as_posix(),
            "capability_probe_sha256": sha256_file(destination / "capability-report.json"),
            "v2_protocol_probe_path": (destination / "v2-protocol-report.json").relative_to(root).as_posix(),
            "v2_protocol_probe_sha256": sha256_file(destination / "v2-protocol-report.json"),
            "v3_protocol_probe_path": (destination / "v3-protocol-report.json").relative_to(root).as_posix(),
            "v3_protocol_probe_sha256": sha256_file(destination / "v3-protocol-report.json"),
        })
    elif args.command == "replicate":
        if args.run_directory is None:
            parser.error("replicate requires --run-directory")
        from . import baseline as _native_preload
        from .replication import verify_development_run
        checks = run_guard_checks(root)
        report = verify_development_run(root, args.run_directory, output_path=args.run_directory / "independent_replication.json")
        write_json(args.run_directory / "replication_offline_checks.json", checks)
        print(json.dumps(report, indent=2, default=str, allow_nan=False))
        return 0 if report["status"] == "PASS" else 1
    else:
        from .readiness import publish_readiness, terminal_weather_run
        probe = args.capability_probe
        pointer = root / "data/manifests/local_worker_probe.json"
        if probe is None and pointer.exists():
            from .provenance import sha256_file
            record = json.loads(pointer.read_text(encoding="utf-8"))
            probe = (root / record["capability_probe_path"]).resolve()
            probe.relative_to(root)
            if sha256_file(probe) != record["capability_probe_sha256"]:
                raise ValueError("Local capability probe pointer is stale")
        if args.run_directory is not None and probe is not None:
            report = publish_readiness(root, args.run_directory.resolve(), probe.resolve(),
                args.runtime_spec or root / "data/models/runtime_spec.json")
        else:
            report = {"status": "NOT_READY_FOR_OFFLINE_CAMPAIGN", "goal1_complete": False,
                      "goal2_complete": False, "holdout_access_denied": False,
                      "weather_acquisition_terminal": terminal_weather_run(root) is not None,
                      "reason": "Supply the completed baseline run and a verified local worker capability probe"}
            write_json(root / "data/manifests/readiness.json", report)
    print(json.dumps(report, indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
