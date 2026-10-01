"""Source identity, as-of policy and independent-extraction boundary tests."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from klax_lab.forecast_dataset import (
    AvailabilityContradiction, acquisition_records, audit_training_day, normalize_forecasts, validate_feature, validate_lineage,
)


POLICY = {
    "weather_training": ["2024-01-01", "2024-12-31"],
    "selection": ["2025-01-05", "2025-06-30"],
    "protected_final": ["2025-07-01", "2025-12-31"],
    "climatology_training": ["2020-01-01", "2024-12-31"],
    "forecast_initialization_hour_utc": 0, "availability_delay_hours": 6, "decision_hour_utc": 14,
    "station": {"latitude": 33.93816, "longitude": -118.3866},
}
LEADS = [8, 9, 12, 15, 18, 21, 24, 27, 30, 31]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def make_fixture(root, target="2024-01-01"):
    initialized = datetime.fromisoformat(target).replace(tzinfo=timezone.utc)
    stamp = initialized.strftime("%Y%m%d")
    samples, sources = [], []
    for lead in LEADS:
        relative = Path(f"data/raw/weather/gfs/{stamp}/t00z/f{lead:03d}.2m_temperature.grib2")
        raw = root / relative
        raw.parent.mkdir(parents=True, exist_ok=True)
        body = ("synthetic GRIB bytes for test lead " + str(lead)).encode()
        raw.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        kelvin = 285 + lead / 100
        sample = {
            "edition": 2, "short_name": "2t", "level_type": "heightAboveGround", "level": 2.0,
            "step_type": "instant", "step_units": "h", "start_step": lead, "end_step": lead,
            "units": "K", "initialization": initialized.isoformat(),
            "valid_time": (initialized + timedelta(hours=lead)).isoformat(), "grid_type": "regular_ll",
            "number_of_points": 100, "product_template": 0, "centre": "kwbc", "sub_centre": 0,
            "generating_process": 96, "temperature_k": kelvin, "temperature_f": (kelvin - 273.15) * 1.8 + 32,
            "station_latitude": 33.93816, "station_longitude": -118.3866, "grid_latitude": 34.0,
            "grid_longitude": -118.5, "grid_distance_km": 12.5, "grid_index": 2,
            "extraction_method": "fixture", "eccodes_version": "test", "source_path": relative.as_posix(),
            "source_sha256": digest,
        }
        url = f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{stamp}/00/atmos/gfs.t00z.pgrb2.0p25.f{lead:03d}"
        line = f"1:100:d={stamp}00:TMP:2 m above ground:{lead} hour fcst:"
        index_text = line + f"\n2:{100 + len(body)}:d={stamp}00:UGRD:10 m above ground:{lead} hour fcst:\n"
        index = raw.parent / f"f{lead:03d}.idx"
        index.write_bytes(index_text.encode())
        field_meta = {"url": url, "bytes": len(body), "sha256": digest, "range_start": 100,
                      "range_end": 100 + len(body) - 1, "full_source_bytes": 1000}
        index_meta = {"url": url + ".idx", "bytes": len(index_text.encode()),
                      "sha256": hashlib.sha256(index_text.encode()).hexdigest(), "range_start": None, "range_end": None}
        write_json(raw.with_suffix(raw.suffix + ".json"), field_meta)
        write_json(index.with_suffix(index.suffix + ".json"), index_meta)
        write_json(raw.with_suffix(".point.json"), sample)
        samples.append(sample)
        sources.append({"field": field_meta, "index": index_meta, "index_line": line})
    row = {
        "model": "gfs", "target_date": target, "initialization": initialized.isoformat(),
        "assumed_available_at": (initialized + timedelta(hours=6)).isoformat(),
        "climate_day_start": (initialized + timedelta(hours=8)).isoformat(),
        "climate_day_end_exclusive": (initialized + timedelta(hours=32)).isoformat(),
        "historical_availability_proven": False, "planned_leads": LEADS, "sample_count": 10,
        "samples": samples, "sampled_max_temperature_f": max(s["temperature_f"] for s in samples),
    }
    report = {"target_date": target, "planned_leads": LEADS, "requested_leads": LEADS,
              "assumed_available_at": row["assumed_available_at"], "historical_availability_proven": False,
              "models": {"gfs": {"status": "COMPLETE_SAMPLED_PROXY", "samples": samples,
                                  "daily_feature": row, "sources": sources}}}
    write_json(root / f"data/manifests/weather/{target}_fixture.json", report)
    write_json(root / f"data/normalized/weather/gfs_{target}_sampled_temperature.json", row)
    return row


class ForecastValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.row = make_fixture(self.root)

    def decode(self, path, **kwargs):
        self.assertEqual(kwargs["expected_lead"], int(path.name[1:4]))
        return json.loads(path.with_suffix(".point.json").read_text())

    def records(self):
        result, errors = acquisition_records(self.root)
        self.assertEqual(errors, [])
        return result

    def source_last_modified(self, value):
        path = self.root / "data/manifests/weather/2024-01-01_fixture.json"
        manifest = json.loads(path.read_text())
        field = manifest["models"]["gfs"]["sources"][0]["field"]
        field["last_modified"] = value
        field["etag"] = '"fixture-etag"'
        raw = self.root / self.row["samples"][0]["source_path"]
        write_json(raw.with_suffix(raw.suffix + ".json"), field)
        write_json(path, manifest)

    def test_valid_row_has_full_lineage_and_training_decode(self):
        validate_feature(self.row, POLICY)
        record = validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)
        self.assertEqual(record["raw_fields_hashed"], 10)
        self.assertEqual(record["independently_redecoded_fields"], 10)

    def test_future_init_station_model_and_lead_fail(self):
        for mutation in ("future_init", "station", "model", "lead", "path", "units"):
            row = deepcopy(self.row)
            if mutation == "future_init": row["samples"][0]["initialization"] = "2024-01-02T00:00:00+00:00"
            if mutation == "station": row["samples"][0]["station_latitude"] = 0.0
            if mutation == "model": row["model"] = "nbm"
            if mutation == "lead": row["samples"][0]["end_step"] = 99
            if mutation == "path": row["samples"][0]["source_path"] = "../unrelated.grib2"
            if mutation == "units": row["samples"][0]["units"] = "C"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_feature(row, POLICY)

    def test_exact_availability_delay_and_aware_time_required(self):
        for available in ("2024-01-01T00:00:00Z", "2024-01-01T07:00:00Z", "2024-01-01T06:00:00"):
            row = deepcopy(self.row)
            row["assumed_available_at"] = available
            with self.subTest(available=available), self.assertRaises(ValueError):
                validate_feature(row, POLICY)

    def test_nonfinite_values_and_inconsistent_units_fail(self):
        for value in (float("nan"), float("inf"), True):
            row = deepcopy(self.row)
            row["sampled_max_temperature_f"] = value
            for sample in row["samples"]: sample["temperature_f"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_feature(row, POLICY)
        row = deepcopy(self.row)
        row["samples"][0]["temperature_f"] += 1
        with self.assertRaisesRegex(ValueError, "conversion"):
            validate_feature(row, POLICY)

    def test_missing_or_tampered_raw_bytes_fail(self):
        raw = self.root / self.row["samples"][0]["source_path"]
        raw.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "hash or byte"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)
        raw.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)

    def test_missing_manifest_and_modified_feature_fail(self):
        with self.assertRaisesRegex(ValueError, "No complete acquisition"):
            validate_lineage(self.root, self.row, {}, POLICY, decoder=self.decode)
        row = deepcopy(self.row)
        row["sampled_max_temperature_f"] += 1
        with self.assertRaisesRegex(ValueError, "immutable acquisition"):
            validate_lineage(self.root, row, self.records(), POLICY, decoder=self.decode)

    def test_point_cache_and_sidecar_tampering_fail(self):
        raw = self.root / self.row["samples"][0]["source_path"]
        point = raw.with_suffix(".point.json")
        value = json.loads(point.read_text())
        value["temperature_f"] += 1
        write_json(point, value)
        with self.assertRaisesRegex(ValueError, "point cache"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)
        write_json(point, self.row["samples"][0])
        sidecar = raw.with_suffix(raw.suffix + ".json")
        value = json.loads(sidecar.read_text())
        value["url"] = "https://unrelated.example/analysis.grib2"
        write_json(sidecar, value)
        with self.assertRaisesRegex(ValueError, "raw sidecar"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)

    def test_consistent_sidecar_tampering_cannot_bypass_original_index_range(self):
        path = self.root / "data/manifests/weather/2024-01-01_fixture.json"
        report = json.loads(path.read_text())
        field = report["models"]["gfs"]["sources"][0]["field"]
        field["range_start"] += 1
        field["range_end"] += 1
        raw = self.root / self.row["samples"][0]["source_path"]
        write_json(raw.with_suffix(raw.suffix + ".json"), field)
        write_json(path, report)
        with self.assertRaisesRegex(ValueError, "byte range disagrees"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)

    def test_independent_decode_detects_plausible_cached_temperature_error(self):
        def wrong_decode(path, **kwargs):
            result = self.decode(path, **kwargs)
            result["temperature_f"] += 0.1
            return result
        with self.assertRaisesRegex(ValueError, "Independent GRIB decode"):
            validate_lineage(self.root, self.row, self.records(), POLICY, decoder=wrong_decode)

    def test_source_last_modified_is_retained_but_does_not_prove_availability(self):
        self.source_last_modified("Mon, 01 Jan 2024 03:30:00 GMT")
        result = validate_lineage(self.root, self.row, self.records(), POLICY, decoder=self.decode)
        audit = result["source_availability_audit"]
        self.assertEqual(audit["last_modified_known"], 1)
        self.assertEqual(audit["last_modified_missing"], 19)
        self.assertEqual(audit["source_object_states"][0]["last_modified_utc"], "2024-01-01T03:30:00+00:00")
        self.assertEqual(audit["source_object_states"][0]["etag"], '"fixture-etag"')
        self.assertFalse(audit["historical_availability_proven"])

    def test_late_source_last_modified_quarantines_without_redecoding(self):
        self.source_last_modified("Mon, 01 Jan 2024 15:00:00 GMT")
        with self.assertRaises(AvailabilityContradiction) as context:
            validate_lineage(self.root, self.row, self.records(), POLICY,
                             decoder=lambda *a, **kw: self.fail("Contradiction must stop before decode"))
        self.assertEqual(context.exception.audit["last_modified_after_assumed"], 1)
        self.assertEqual(context.exception.audit["last_modified_after_decision"], 1)
        report = normalize_forecasts(self.root, POLICY)
        self.assertEqual(report["quarantined_availability_model_days"], 1)
        self.assertEqual(report["last_modified_audit"]["last_modified_after_assumed"], 1)
        self.assertEqual(report["source_lineage_validated_model_days"], 0)

    def test_audit_schedule_is_deterministic_and_training_only(self):
        self.assertTrue(audit_training_day(date(2024, 1, 1), POLICY))
        self.assertFalse(audit_training_day(date(2024, 1, 30), POLICY))
        self.assertTrue(audit_training_day(date(2024, 1, 31), POLICY))
        self.assertTrue(audit_training_day(date(2024, 12, 26), POLICY))
        self.assertFalse(audit_training_day(date(2025, 7, 1), POLICY))
        row = make_fixture(self.root, "2025-07-01")
        validate_feature(row, POLICY)
        result = validate_lineage(self.root, row, self.records(), POLICY,
                                  decoder=lambda *a, **kw: self.fail("Protected data must not be redecoded"))
        self.assertEqual(result["independently_redecoded_fields"], 0)

    def test_normalization_reports_scope_without_network_or_labels(self):
        with patch("klax_lab.forecast_dataset.extract_temperature", side_effect=self.decode), \
             patch("socket.create_connection", side_effect=AssertionError("offline")):
            report = normalize_forecasts(self.root, POLICY)
        self.assertEqual(report["source_lineage_validated_model_days"], 1)
        self.assertEqual(report["independent_decode_audit"]["fields_redecoded"], 10)
        self.assertEqual(report["invalid_feature_files"], [])
        self.assertEqual(report["status"], "PARTIAL_DOWNLOAD")
        self.assertFalse(report["source_availability_verified"])


if __name__ == "__main__":
    unittest.main()
