"""Backend contract tests never contact Codex or another online service."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from klax_lab.codex_backend import (
    BackendBlocked, DISABLED_FEATURES, _model_response, _sanitize, assess_probe,
    build_command, child_environment, dispatch_research_packet, parse_events,
    run_synthetic_probe,
)


class CodexBackendTests(unittest.TestCase):
    def result(self, **overrides):
        return {"synthetic": True, "canary_read": False, "canary_text": None,
                "advertised_tools": [], "attempt_detail": "No read capability", **overrides}

    def test_command_never_enables_api_keys_model_override_or_sandbox_bypass(self):
        command = build_command("codex.exe", "packet", "schema.json", "result.json")
        joined = " ".join(command)
        for flag in ("--ignore-user-config", "--strict-config", "--ephemeral", "--output-schema", "--json"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertNotIn("--model", command)
        self.assertNotIn("-m", command)
        self.assertNotIn("danger-full-access", joined)
        self.assertNotIn("bypass", joined)
        self.assertNotIn("tools.view_image", joined)
        for feature in DISABLED_FEATURES:
            self.assertIn(f"features.{feature}=false", command)
        self.assertEqual(command[-1], "-")

    def test_environment_preserves_auth_location_without_reading_it_and_drops_api_overrides(self):
        parent = {"PATH": "runtime", "CODEX_HOME": "existing-auth-location", "OPENAI_API_KEY": "test-secret",
                  "AZURE_OPENAI_API_KEY": "test-secret2", "OPENAI_BASE_URL": "https://invalid.example"}
        result = child_environment(parent)
        self.assertEqual(result, {"PATH": "runtime", "CODEX_HOME": "existing-auth-location"})
        self.assertIn("OPENAI_API_KEY", parent)

    def test_clean_model_self_report_never_proves_tool_isolation(self):
        assessment = assess_probe([], self.result(), exit_code=0, timed_out=False, canary_value="SYNTHETIC_RANDOM")
        self.assertEqual(assessment["status"], "BLOCKED")
        self.assertFalse(assessment["backend_ready"])
        self.assertFalse(assessment["host_tool_catalog_verified"])
        self.assertFalse(assessment["holdout_access_denied"])

    def test_diagnostic_is_not_falsely_reported_as_a_tool_call(self):
        events = [{"type": "item.completed", "item": {"type": "error", "message": "Host disabled"}}]
        assessment = assess_probe(events, self.result(), exit_code=0, timed_out=False, canary_value="SYNTHETIC_RANDOM")
        self.assertEqual(assessment["observed_tool_item_types"], [])
        self.assertEqual(assessment["diagnostics"], ["Host disabled"])

    def test_actual_tool_activity_or_canary_exposure_is_rejected(self):
        events = [{"type": "item.completed", "item": {"type": "command_execution", "command": "synthetic-read"}}]
        assessment = assess_probe(events, self.result(canary_read=True, canary_text="SYNTHETIC_RANDOM"),
                                  exit_code=0, timed_out=False, canary_value="SYNTHETIC_RANDOM")
        self.assertTrue(assessment["exact_synthetic_canary_exposed"])
        self.assertEqual(assessment["observed_tool_item_types"], ["command_execution"])
        self.assertFalse(assessment["backend_ready"])

    def test_unknown_events_parse_errors_and_malformed_result_fail_closed(self):
        events, errors = parse_events('not-json\n{}\n{"type":"thread.started"}\n')
        self.assertEqual(len(events), 1)
        self.assertEqual(len(errors), 2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.write_text(json.dumps(self.result()), encoding="utf-8")
            self.assertIsNotNone(_model_response(path))
            path.write_text(json.dumps(self.result(extra="untrusted")), encoding="utf-8")
            self.assertIsNone(_model_response(path))
        assessment = assess_probe([{"type": "item.completed", "item": {"type": "new_unsafe_thing"}}], None,
                                  exit_code=1, timed_out=True, canary_value="SYNTHETIC_RANDOM")
        self.assertEqual(assessment["unknown_item_types"], ["new_unsafe_thing"])
        self.assertFalse(assessment["backend_ready"])

    def test_probe_budget_and_timeout_are_bounded_without_model_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "not-a-real-executable.exe"
            executable.write_text("synthetic placeholder", encoding="utf-8")
            output = Path(folder) / "probes"
            with patch("klax_lab.codex_backend.subprocess.Popen") as popen:
                process = popen.return_value
                process.communicate.return_value = ("", "Synthetic config failure")
                process.returncode = 1
                for _ in range(2):
                    assessment = run_synthetic_probe(executable, output, timeout_seconds=1)
                    self.assertEqual(assessment["status"], "BLOCKED")
                self.assertEqual(popen.call_count, 2)
                with self.assertRaises(BackendBlocked):
                    run_synthetic_probe(executable, output, timeout_seconds=1)
                with self.assertRaises(ValueError):
                    run_synthetic_probe(executable, output, timeout_seconds=121)
                self.assertEqual(popen.call_count, 2)

    def test_timed_out_probe_kills_child_and_stays_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "fake.exe"
            executable.write_text("synthetic", encoding="utf-8")
            with patch("klax_lab.codex_backend.subprocess.Popen") as popen:
                process = popen.return_value
                process.returncode = -1
                process.communicate.side_effect = [subprocess.TimeoutExpired("fake", 1), ("", "")]
                assessment = run_synthetic_probe(executable, Path(folder) / "probes", timeout_seconds=1)
                process.kill.assert_called_once()
                self.assertTrue(assessment["timed_out"])
                self.assertFalse(assessment["backend_ready"])

    def test_no_historical_dispatch_until_boundary_verified(self):
        with self.assertRaises(BackendBlocked):
            dispatch_research_packet({"historical": True})

    def test_logs_redact_common_credential_shapes(self):
        output = _sanitize("Bearer artificialsecret access_token=artificialvalue&x=1 sk-artificial_long_value")
        self.assertNotIn("artificialsecret", output)
        self.assertNotIn("artificialvalue", output)
        self.assertNotIn("sk-artificial", output)


if __name__ == "__main__":
    unittest.main()
