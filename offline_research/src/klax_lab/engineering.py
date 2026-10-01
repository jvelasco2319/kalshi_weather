"""Finite full-suite validation with immutable source and test evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from .provenance import inventory, sha256_file, write_json
from .readiness import code_fingerprint


def validation_inputs(root: Path) -> list[Path]:
    return [*sorted((root / "src/klax_lab").glob("*.py")),
            *sorted((root / "tests").glob("*.py")),
            root / "configs/evaluation.json", root / "configs/pilot.json",
            root / "requirements-local.lock", root / "pyproject.toml"]


def junit_summary(path: Path) -> dict:
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.findall("testsuite"))
    if not suites:
        raise ValueError("Full-suite test report contains no test suite")
    cases = [case for suite in suites for case in suite.findall("testcase")]
    tests = sum(int(suite.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)
    if not cases or not tests or failures or errors or skipped or any(
        case.find(key) is not None for case in cases for key in ("failure", "error", "skipped")
    ):
        raise ValueError("All collected engineering tests must pass without skips or errors")
    # Pytest counts successful unittest subtests in the suite's tests attribute,
    # while retaining only the parent testcase XML elements. Keep both counts;
    # their difference is not a missing-test failure.
    if tests < len(cases):
        raise ValueError("Reported test executions are fewer than the saved parent cases")
    return {"junit_test_cases": len(cases), "reported_executions": tests,
            "additional_subtest_executions": tests - len(cases),
            "failures": failures, "errors": errors, "skipped": skipped,
            "nodes": sorted(case.get("classname", "") + "::" + case.get("name", "") for case in cases)}


def run_validation(root: Path, timeout_seconds: int = 1800) -> dict:
    root = root.resolve()
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("Engineering validation needs a finite timeout of at most one hour")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = root / "runs/engineering_validation" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    sources = inventory(root, validation_inputs(root))
    log, xml = folder / "pytest.txt", folder / "pytest.xml"
    command = [sys.executable, "-m", "pytest", "-q", "--junitxml", str(xml)]
    report = {"status": "RUNNING", "scope": "Synthetic engineering checks; no historical profit evidence",
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "command": command,
              "input_artifacts": sources, "code_sha256": code_fingerprint(root)}
    write_json(folder / "summary.json", report)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    # External test filters must not make a partial run look like a full suite.
    env.pop("PYTEST_ADDOPTS", None)
    try:
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.run(command, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                     timeout=timeout_seconds, close_fds=True)
        report["returncode"] = process.returncode
        if process.returncode:
            raise RuntimeError("Engineering suite failed; inspect the immutable pytest log")
        report["test_result"] = junit_summary(xml)
        if inventory(root, validation_inputs(root)) != sources:
            raise RuntimeError("Source, policy, dependency or test files changed during validation")
        from .controller import run_fixture_campaign
        fixture = run_fixture_campaign(folder / "controller_fixture")
        report["controller_fixture"] = fixture
        report["fixture_artifacts"] = inventory(root, sorted((folder / "controller_fixture").rglob("*.json")))
        if inventory(root, validation_inputs(root)) != sources:
            raise RuntimeError("Validation inputs changed during controller fixture")
        report["status"] = "PASS"
    except Exception as error:
        report.update(status="FAIL", reason=type(error).__name__ + ": " + str(error))
    report.update(finished_at_utc=datetime.now(timezone.utc).isoformat(),
                  log_path=log.relative_to(root).as_posix(), log_sha256=sha256_file(log) if log.exists() else None,
                  junit_path=xml.relative_to(root).as_posix(), junit_sha256=sha256_file(xml) if xml.exists() else None)
    write_json(folder / "summary.json", report)
    write_json(root / "data/manifests/engineering_validation.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    result = run_validation(args.root, args.timeout_seconds)
    print(json.dumps({key: value for key, value in result.items() if key not in ("input_artifacts", "fixture_artifacts", "test_result")}, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
