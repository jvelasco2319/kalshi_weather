"""Synthetic validation envelopes, never historical performance evidence."""
from pathlib import Path
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from klax_lab.acceptance import REQUIRED_TESTS, verify_engineering
from klax_lab.engineering import junit_summary, run_validation


def synthetic_project(root):
    for folder in ("src/klax_lab", "tests", "configs"):
        (root / folder).mkdir(parents=True)
    for name, value in {
        "src/klax_lab/fixture.py": "# Synthetic placeholder; never used as project implementation\n",
        "tests/test_fixture.py": "# Synthetic test inventory only\n",
        "configs/evaluation.json": "{}", "configs/pilot.json": "{}",
        "requirements-local.lock": "# Synthetic dependencies\n", "pyproject.toml": "# Synthetic package\n",
    }.items():
        (root / name).write_text(value, encoding="utf-8")


def write_junit(path, omit=None, failure=False):
    names = [test for test in REQUIRED_TESTS.values() if test != omit]
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests=str(len(names)), failures=str(int(failure)), errors="0", skipped="0")
    for index, name in enumerate(names):
        case = ET.SubElement(suite, "testcase", classname="synthetic_fixture", name=name)
        if failure and index == 0: ET.SubElement(case, "failure", message="Intentional synthetic failure")
    ET.ElementTree(root).write(path, encoding="utf-8")


def passing_run(root, monkeypatch, omit=None, mutate=False):
    synthetic_project(root)

    def process(command, **kwargs):
        assert command[1:4] == ["-m", "pytest", "-q"]
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        xml = Path(command[command.index("--junitxml") + 1])
        write_junit(xml, omit=omit)
        kwargs["stdout"].write("SYNTHETIC test runner fixture; no real suite executed by this stub\n")
        if mutate: (root / "tests/test_fixture.py").write_text("# Changed during synthetic validation\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("klax_lab.engineering.subprocess.run", process)
    return run_validation(root)


def test_engineering_envelope_binds_full_inputs_junit_and_actual_controller_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_one_test")
    result = passing_run(tmp_path, monkeypatch)
    assert result["status"] == "PASS"
    proof = verify_engineering(tmp_path)
    assert proof["status"] == "PASS"
    assert set(proof["checks"]) == set(REQUIRED_TESTS)
    assert proof["artifacts"]


@pytest.mark.parametrize("target", ["src/klax_lab/fixture.py", "tests/test_fixture.py", "configs/evaluation.json", "requirements-local.lock"])
def test_engineering_evidence_rejects_changed_validation_inputs(tmp_path, monkeypatch, target):
    assert passing_run(tmp_path, monkeypatch)["status"] == "PASS"
    (tmp_path / target).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="current source|changed after validation"):
        verify_engineering(tmp_path)


def test_engineering_gate_rejects_missing_required_test_even_with_passing_exit(tmp_path, monkeypatch):
    omitted = REQUIRED_TESTS["holdout_path_denial"]
    assert passing_run(tmp_path, monkeypatch, omit=omitted)["status"] == "PASS"
    with pytest.raises(ValueError, match="Required engineering case not executed: holdout_path_denial"):
        verify_engineering(tmp_path)


def test_validation_mutation_during_test_execution_fails_closed(tmp_path, monkeypatch):
    result = passing_run(tmp_path, monkeypatch, mutate=True)
    assert result["status"] == "FAIL"
    assert "changed during validation" in result["reason"]


def test_changed_transcript_and_incomplete_junit_cannot_support_readiness(tmp_path, monkeypatch):
    result = passing_run(tmp_path, monkeypatch)
    (tmp_path / result["log_path"]).write_text("different transcript")
    with pytest.raises(ValueError, match="Acceptance evidence changed"):
        verify_engineering(tmp_path)
    xml = tmp_path / "failed.xml"
    write_junit(xml, failure=True)
    with pytest.raises(ValueError, match="must pass"):
        junit_summary(xml)


def test_junit_retains_successful_subtests_separately_from_parent_cases(tmp_path):
    xml = tmp_path / "subtests.xml"
    xml.write_text('<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0">'
                   '<testcase classname="synthetic" name="parent_test"/></testsuite></testsuites>')
    result = junit_summary(xml)
    assert result["junit_test_cases"] == 1
    assert result["reported_executions"] == 3
    assert result["additional_subtest_executions"] == 2
    xml.write_text('<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0">'
                   '<testcase classname="synthetic" name="parent_test"/></testsuite></testsuites>')
    with pytest.raises(ValueError): junit_summary(xml)
