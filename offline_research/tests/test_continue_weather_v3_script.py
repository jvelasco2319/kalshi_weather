from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/continue_weather_v3.ps1"


def _completion_function() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("function Complete-AcquisitionAttempt {")
    end = text.index("function Assert-LaunchIntentBinding {", start)
    return text[start:end]


def _run_fixture(tmp_path: Path, *, current_status: str, requested_status: str) -> dict:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the handoff-script fixture")
    progress = {
        "days_complete": 2,
        "last_complete_date": "2025-01-06",
        "new_transfer_bytes": 999,
        "days": [
            {"date": "2025-01-05", "network_requests": 3, "new_transfer_bytes": 11},
            {"date": "2025-01-06", "network_requests": 5, "new_transfer_bytes": 13},
        ],
    }
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(progress), encoding="utf-8")
    journal = {
        "updated_at_utc": "2026-09-26T13:38:09Z",
        "completion_metrics_correction": {"path": "correction.json", "sha256": "a" * 64},
        "attempts": [{
            "pid": 123,
            "terminal_status": current_status,
            "ended_at_utc": "2026-09-26T13:38:00Z",
            "network_requests": 10232,
            "new_transfer_bytes": 6448502823,
        }],
    }
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    output_path = tmp_path / "result.json"
    harness = tmp_path / "fixture.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$progressPath = '{progress_path}'\n"
        f"$journalPath = '{journal_path}'\n"
        "$bulkStdoutPath = Join-Path $PSScriptRoot 'absent.stdout'\n"
        "$bulkStderrPath = Join-Path $PSScriptRoot 'absent.stderr'\n"
        "$script:writeCount = 0\n"
        "function Read-AcquisitionJournal { Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json }\n"
        "function Write-AcquisitionJournal { param($Journal); $script:writeCount += 1; $script:written = $Journal }\n"
        + _completion_function()
        + "\n$failure = $null\n"
        + f"try {{ Complete-AcquisitionAttempt -ProcessId 123 -TerminalStatus '{requested_status}' }} catch {{ $failure = $_.Exception.Message }}\n"
        + "$value = if ($null -ne $script:written) { $script:written } else { Read-AcquisitionJournal }\n"
        + "$result = [ordered]@{ write_count = $script:writeCount; failure = $failure; journal = $value }\n"
        + f"$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath '{output_path}' -Encoding utf8\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output_path.read_text(encoding="utf-8-sig"))


def test_completion_is_byte_stable_when_terminal_status_already_matches(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        current_status="COMPLETE_543_DAYS_CACHE_VERIFIED",
        requested_status="COMPLETE_543_DAYS_CACHE_VERIFIED",
    )
    attempt = result["journal"]["attempts"][0]
    assert result["write_count"] == 0
    assert result["failure"] is None
    assert attempt["ended_at_utc"] == "2026-09-26T13:38:00Z"
    assert attempt["network_requests"] == 10232
    assert attempt["new_transfer_bytes"] == 6448502823
    assert result["journal"]["completion_metrics_correction"]["sha256"] == "a" * 64


def test_completion_rejects_incompatible_terminal_rewrite(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        current_status="NONTRANSIENT_FAILURE",
        requested_status="COMPLETE_543_DAYS_CACHE_VERIFIED",
    )
    assert result["write_count"] == 0
    assert "already terminal" in result["failure"]
    assert result["journal"]["attempts"][0]["terminal_status"] == "NONTRANSIENT_FAILURE"


def test_completion_sums_authoritative_daily_transfer_bytes(tmp_path: Path) -> None:
    result = _run_fixture(
        tmp_path,
        current_status="RUNNING_RESUME_FROM_VERIFIED_CACHE",
        requested_status="COMPLETE_543_DAYS_CACHE_VERIFIED",
    )
    attempt = result["journal"]["attempts"][0]
    assert result["write_count"] == 1
    assert result["failure"] is None
    assert attempt["terminal_status"] == "COMPLETE_543_DAYS_CACHE_VERIFIED"
    assert attempt["network_requests"] == 8
    assert attempt["new_transfer_bytes"] == 24
    assert attempt["committed_days"] == 2
    assert attempt["last_committed_date"] == "2025-01-06"
