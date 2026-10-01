"""Synthetic-only preservation of the pinned collector's original algorithm."""
from copy import deepcopy
from hashlib import sha256
import json
from math import erf, sqrt
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

from klax_lab.collector_reference import (
    FLOAT_TOLERANCE, MODEL_WEIGHTS, SOURCE_HASHES, original_calculations, original_style_density,
    reference_dataset, reference_ensemble, reference_statistics,
    run_synthetic_reference, synthetic_fixtures, verify_reference_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]


class ReferenceAlgorithmTests(unittest.TestCase):
    def test_fractional_mean_uses_floor_ceil_ten_degree_grid(self):
        distribution = original_style_density(63.25)
        self.assertEqual((min(distribution), max(distribution)), (53, 74))
        self.assertAlmostEqual(sum(distribution.values()), 1.0)

    def test_original_point_density_is_preserved_instead_of_rounded_bin_cdf(self):
        density = original_style_density(64.0)
        integrated = erf(0.5 / (2 * sqrt(2)))
        self.assertGreater(abs(density[64] - integrated), 0.001)

    def test_original_weights_keep_both_gfs_entries(self):
        result = reference_ensemble({name: {index: 1.0} for index, name in enumerate(MODEL_WEIGHTS)})
        self.assertEqual(result, {0: 0.3, 1: 0.2, 2: 0.2, 3: 0.3})

    def test_available_model_weights_renormalize(self):
        result = reference_ensemble({"gfs_seamless": {60: 1.0}, "nbm": {70: 1.0}})
        self.assertEqual(result, {60: 0.4, 70: 0.6})

    def test_original_mixture_is_not_density_at_average_mean(self):
        mixture = reference_ensemble({"gfs": original_style_density(60), "nbm": original_style_density(80)})
        averaged = original_style_density(70)
        self.assertLess(mixture[70], averaged[70] / 1000)
        self.assertGreater(mixture[60], mixture[70])

    def test_statistics_keep_lowest_sorted_bin_on_exact_mode_tie(self):
        self.assertEqual(reference_statistics({60: 0.5, 70: 0.5}),
                         {"expected_temperature": 65, "most_likely_temperature": 60, "confidence": 0.5})

    def test_dataset_keeps_original_daily_high_and_missing_model_records(self):
        fixture = synthetic_fixtures()["gfs-nbm-only"]
        result = reference_dataset(fixture)
        self.assertEqual(result["models"]["gfs"]["daily_high"], fixture["daily_highs"]["gfs"])
        self.assertEqual(result["metadata"]["unavailable_models"], fixture["unavailable_models"])
        self.assertEqual(set(result["models"]), {"gfs", "nbm"})

    def test_unlabeled_or_inconsistent_inputs_are_rejected(self):
        fixture = synthetic_fixtures()["all-four-models"]
        for change in ("label", "sample", "model"):
            with self.subTest(change=change):
                invalid = deepcopy(fixture)
                if change == "label":
                    invalid["evidence_kind"] = "HISTORICAL"
                elif change == "sample":
                    invalid["daily_highs"]["gfs"]["daily_high_f"] += 1
                else:
                    del invalid["daily_highs"]["nam"]
                with self.assertRaises(ValueError):
                    reference_dataset(invalid)


class OriginalImportAndParseTests(unittest.TestCase):
    def test_real_pinned_import_and_saved_json_parse_with_network_denied(self):
        before = {relative: sha256((ROOT / "external/weather_data_collector" / relative).read_bytes()).hexdigest()
                  for relative in SOURCE_HASHES}
        sentinel = object()
        names = ("probability", "probability.distribution", "weather_pipeline", "config", "pygrib")
        imports_before = {name: sys.modules.get(name, sentinel) for name in names}
        with TemporaryDirectory() as directory, patch("socket.create_connection", side_effect=AssertionError("network forbidden")), patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP forbidden")):
            output = Path(directory) / "reference"
            report = run_synthetic_reference(ROOT, output)
            self.assertEqual(report["status"], "SYNTHETIC_REFERENCE_VERIFIED")
            self.assertFalse(report["historical_forecasts_recovered"])
            self.assertFalse(report["fetch_pipeline_imported"])
            self.assertEqual(len(report["cases"]), 2)
            for case in report["cases"].values():
                self.assertTrue(case["parsed_original_schema_pass"])
                self.assertLessEqual(case["maximum_absolute_difference"], FLOAT_TOLERANCE)
                artifact = case["original_output"]
                self.assertEqual(sha256(Path(artifact["path"]).read_bytes()).hexdigest(), artifact["sha256"])
                self.assertIn("SYNTHETIC", json.loads(Path(artifact["path"]).read_text())["metadata"]["location"]["city"])
            self.assertEqual(run_synthetic_reference(ROOT, output), report)
        self.assertEqual({name: sys.modules.get(name, sentinel) for name in names}, imports_before)
        self.assertEqual(before, {relative: sha256((ROOT / "external/weather_data_collector" / relative).read_bytes()).hexdigest()
                                  for relative in SOURCE_HASHES})

    def test_source_hash_mismatch_rejected_before_any_original_module_import(self):
        with patch.dict(SOURCE_HASHES, {"weather_pipeline.py": "0" * 64}), patch("klax_lab.collector_reference._import_verified") as imported:
            with self.assertRaisesRegex(ValueError, "source hash differs"):
                with original_calculations(ROOT):
                    pass
            imported.assert_not_called()

    def test_preserved_original_output_is_not_overwritten(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            run_synthetic_reference(ROOT, output)
            original = output / "original-output-all-four-models.json"
            original.write_text("corrupted synthetic evidence")
            with self.assertRaisesRegex(ValueError, "different content"):
                run_synthetic_reference(ROOT, output)
            self.assertEqual(original.read_text(), "corrupted synthetic evidence")

    def test_readiness_pointer_verifies_without_original_import_and_rejects_tampering(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in SOURCE_HASHES:
                target = root / "external/weather_data_collector" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "external/weather_data_collector" / relative, target)
            code = root / "src/klax_lab/collector_reference.py"
            code.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "src/klax_lab/collector_reference.py", code)
            run_synthetic_reference(root)
            pointer = json.loads((root / "data/manifests/collector_reference.json").read_text())
            with patch("klax_lab.collector_reference.original_calculations", side_effect=AssertionError("readiness cannot execute original source")):
                paths = verify_reference_artifacts(root, pointer)
            self.assertEqual(len(paths), 2)
            invalid = deepcopy(pointer)
            invalid["actual_original_schema_parse_executed"] = False
            with self.assertRaises(ValueError):
                verify_reference_artifacts(root, invalid)
            invalid = deepcopy(pointer)
            invalid["artifacts"][0]["path"] = str(root / "outside.json")
            with self.assertRaises(ValueError):
                verify_reference_artifacts(root, invalid)
            paths[0].write_text("changed")
            with self.assertRaises(ValueError):
                verify_reference_artifacts(root, pointer)


if __name__ == "__main__":
    unittest.main()
