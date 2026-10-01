"""Synthetic terminal acquisition fixtures; no historical repair or HTTP occurs."""

from datetime import timedelta
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from klax_lab.acquire_weather import _cli_job_lock
from klax_lab.repair_weather import (
    END, EXPECTED_DAYS, LEADS, MODELS, START,
    RepairBlocked, _range_rows, main, repair_weather,
)

SOURCE_ID = "20260925T010000000000Z"
INTEGRITY_ID = "20260925T020000000000Z"
GAP = "2024-04-23"


def write_range(root: Path, run_id=SOURCE_ID, gaps=(GAP,), status=None):
    manifest_directory = root / "data/manifests/weather"
    progress = manifest_directory / "progress"
    progress.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for offset in range(EXPECTED_DAYS):
        day = (START + timedelta(days=offset)).isoformat()
        complete = day not in gaps
        by_month.setdefault(day[:7], []).append({"date": day, "complete": complete, "errors": [] if complete else [{"model": "nbm", "lead": 15, "error": "synthetic reset"}]})
    paths = []
    for month, rows in by_month.items():
        path = progress / f"{month}_{run_id}.json"
        path.write_text(json.dumps({"run_id": run_id, "month": month, "days": rows}))
        paths.append(str(path))
    manifest = {"run_id": run_id, "start_date": START.isoformat(), "end_date": END.isoformat(), "model_sources": list(MODELS), "status": status or ("COMPLETED_WITH_GAPS" if gaps else "COMPLETED"), "days_complete": EXPECTED_DAYS - len(gaps), "days_with_errors": len(gaps), "monthly_progress": paths, "new_transfer_bytes": 0}
    path = manifest_directory / f"range_{run_id}.json"
    path.write_text(json.dumps(manifest))
    return path, {**manifest, "manifest_path": str(path)}


def day_report(complete):
    return {"requested_leads": LEADS, "errors": [] if complete else [{"error": "synthetic reset"}], "models": {model: {"status": "COMPLETE_SAMPLED_PROXY" if complete else "PARTIAL_COMPATIBILITY_PROBE"} for model in MODELS}, "new_transfer_bytes": 0, "manifest_path": "synthetic day evidence"}


class RepairValidationTests(unittest.TestCase):
    def test_active_weather_job_blocks_before_any_acquisition(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with _cli_job_lock(root), patch("klax_lab.repair_weather.acquire_day") as acquire:
                with self.assertRaises(RuntimeError):
                    repair_weather(root)
                acquire.assert_not_called()
            self.assertFalse((root / "data/manifests/weather/repair").exists())

    def test_nonterminal_acquisition_cannot_start_repairs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root, status="RUNNING")
            with patch("klax_lab.repair_weather.RateLimitedSession") as session:
                with self.assertRaises(RepairBlocked):
                    repair_weather(root, source)
                session.assert_not_called()

    def test_duplicate_or_out_of_scope_daily_records_are_rejected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest = write_range(root)
            monthly_path = Path(manifest["monthly_progress"][0])
            monthly = json.loads(monthly_path.read_text())
            monthly["days"][0]["date"] = monthly["days"][1]["date"]
            monthly_path.write_text(json.dumps(monthly))
            with self.assertRaises(ValueError):
                _range_rows(root, manifest)

    def test_manifest_paths_cannot_escape_project_evidence_directory(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest = write_range(root)
            other = root / "outside.json"
            other.write_text("{}")
            manifest["monthly_progress"][0] = str(other)
            with self.assertRaises(ValueError):
                _range_rows(root, manifest)


class RepairExecutionTests(unittest.TestCase):
    def test_repairs_exact_gap_once_then_verifies_entire_range_offline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root)

            def integrity(*args, **kwargs):
                self.assertEqual(args[1:4], (START, END, MODELS))
                self.assertTrue(kwargs["offline"])
                self.assertTrue(kwargs["include_boundary_samples"])
                return write_range(root, INTEGRITY_ID, gaps=())[1]

            with patch("klax_lab.repair_weather.RateLimitedSession"), patch("klax_lab.repair_weather.acquire_day", return_value=day_report(True)) as acquire, patch("klax_lab.repair_weather.acquire_range", side_effect=integrity):
                result = repair_weather(root, source)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["integrity_days_complete"], 731)
            self.assertEqual(result["integrity_transfer_bytes"], 0)
            self.assertEqual(acquire.call_count, 1)
            args, kwargs = acquire.call_args
            self.assertEqual(args[1].isoformat(), GAP)
            self.assertEqual(args[2], MODELS)
            self.assertTrue(kwargs["include_boundary_samples"])
            self.assertEqual(kwargs["download_workers"], 4)
            with patch("klax_lab.repair_weather.acquire_day") as repeat, patch("klax_lab.repair_weather.acquire_range") as repeat_integrity:
                cached = repair_weather(root)
            self.assertTrue(cached["cached_completion"])
            repeat.assert_not_called()
            repeat_integrity.assert_not_called()

    def test_two_failures_are_persisted_and_restart_cannot_retry_again(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root)
            sequence = [INTEGRITY_ID, "20260925T030000000000Z", "20260925T040000000000Z"]

            def integrity(*args, **kwargs):
                return write_range(root, sequence.pop(0), gaps=(GAP,))[1]

            with patch("klax_lab.repair_weather.RateLimitedSession"), patch("klax_lab.repair_weather.acquire_day", return_value=day_report(False)) as acquire, patch("klax_lab.repair_weather.acquire_range", side_effect=integrity):
                first = repair_weather(root, source)
                self.assertEqual(first["status"], "INCOMPLETE")
                self.assertEqual(first["attempt_count"], 2)
                second = repair_weather(root)
                third = repair_weather(root)
            self.assertEqual(acquire.call_count, 2)
            self.assertEqual(second["attempt_count"], 2)
            self.assertEqual(third["remaining_gap_dates"], [GAP])

    def test_zero_original_gaps_constructs_no_http_session(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root, gaps=())
            with patch("klax_lab.repair_weather.RateLimitedSession") as session, patch("klax_lab.repair_weather.acquire_day") as acquire, patch("klax_lab.repair_weather.acquire_range", side_effect=lambda *a, **k: write_range(root, INTEGRITY_ID, gaps=())[1]):
                result = repair_weather(root, source)
            session.assert_not_called()
            acquire.assert_not_called()
            self.assertEqual(result["status"], "COMPLETE")

    def test_discarded_repair_transfer_is_counted_across_resumes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root)

            def failed_download(*args, **kwargs):
                with kwargs["transfer_budget"].reservation(7) as consume:
                    consume(7)
                raise ConnectionError("synthetic transfer failed after seven bytes")

            with patch("klax_lab.repair_weather.RateLimitedSession"), patch("klax_lab.repair_weather.acquire_day", side_effect=failed_download), patch("klax_lab.repair_weather.acquire_range", side_effect=lambda *a, **k: write_range(root, INTEGRITY_ID, gaps=(GAP,))[1]):
                result = repair_weather(root, source)
            journal = json.loads(Path(result["journal_path"]).read_text())
            self.assertEqual(journal["repair_transfer_bytes_total"], 14)
            self.assertEqual(journal["weather_bytes_at_repair_start"], 0)
            self.assertEqual(result["attempt_count"], 2)

    def test_cli_incomplete_is_success_only_after_full_offline_integrity(self):
        base = {"status": "INCOMPLETE", "integrity_days_total": 731, "integrity_transfer_bytes": 0, "integrity_manifest": "synthetic.json"}
        cases = [(base, 0), ({**base, "integrity_days_total": 730}, 2), ({**base, "integrity_transfer_bytes": 1}, 2), ({**base, "status": "BLOCKED"}, 2)]
        for result, expected_exit in cases:
            with self.subTest(result=result), patch("sys.argv", ["repair_weather", "--root", "synthetic-root"]), patch("klax_lab.repair_weather.repair_weather", return_value=result), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), expected_exit)
                self.assertEqual(json.loads(output.getvalue()), result)

    def test_offline_integrity_must_transfer_zero_bytes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = write_range(root, gaps=())

            def invalid_integrity(*args, **kwargs):
                path, manifest = write_range(root, INTEGRITY_ID, gaps=())
                manifest["new_transfer_bytes"] = 1
                path.write_text(json.dumps(manifest))
                return manifest

            with patch("klax_lab.repair_weather.acquire_range", side_effect=invalid_integrity):
                with self.assertRaises(ValueError):
                    repair_weather(root, source)


if __name__ == "__main__":
    unittest.main()
