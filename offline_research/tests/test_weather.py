"""Synthetic-only unit tests; real historical compatibility is a separate pilot."""

from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from time import sleep
import unittest
from unittest.mock import Mock, patch

from klax_lab.acquire_weather import (
    IndexEntry, RateLimitedSession, TransferBudget, _cli_job_lock, _download, _load_cached, _write_immutable, acquire_day, archived_url,
    planned_leads, select_temperature_range, validate_range_response,
)
from klax_lab.grib_reader import (
    kelvin_to_fahrenheit, sampled_daily_feature, validate_metadata,
)

UTC = timezone.utc
INIT = datetime(2025, 1, 5, tzinfo=UTC)
DAY = INIT.date()


class ArchivePlanTests(unittest.TestCase):
    def test_url_matches_pinned_collectors_historical_archive(self):
        self.assertEqual(archived_url("gfs", INIT, 9), "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20250105/00/atmos/gfs.t00z.pgrb2.0p25.f009")
        self.assertEqual(archived_url("nbm", INIT, 9), "https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.20250105/00/core/blend.t00z.core.f009.co.grib2")
        with self.assertRaises(ValueError):
            archived_url("gfs_seamless", INIT, 9)
        with self.assertRaises(ValueError):
            archived_url("gfs", datetime.now(UTC), 9)

    def test_standard_day_boundaries_including_summer_and_dst(self):
        for day in (date(2025, 1, 5), date(2025, 3, 9), date(2025, 7, 1), date(2025, 11, 2)):
            init = datetime.combine(day, datetime.min.time(), UTC)
            self.assertEqual(planned_leads(day, init), [9, 12, 15, 18, 21, 24, 27, 30])
            self.assertEqual(planned_leads(day, init, True), [8, 9, 12, 15, 18, 21, 24, 27, 30, 31])
        with self.assertRaises(ValueError):
            planned_leads(DAY, INIT.replace(tzinfo=None))

    def test_index_rejects_ensemble_spread_and_bounded_final_record(self):
        text = "1:0:d=2025010500:RH:2 m above ground:9 hour fcst:\n2:100:d=2025010500:TMP:2 m above ground:9 hour fcst:\n3:200:d=2025010500:TMP:2 m above ground:9 hour fcst:ens std dev\n4:300:d=2025010500:TMAX:2 m above ground:3-9 hour max fcst:\n"
        field = select_temperature_range(text, INIT, 9)
        self.assertEqual((field.start, field.end), (100, 199))
        with self.assertRaises(ValueError):
            select_temperature_range("2:100:d=2025010500:TMP:2 m above ground:9 hour fcst:", INIT, 9)
        with self.assertRaises(ValueError):
            select_temperature_range(text, INIT, 12)

    def test_http_range_cannot_fall_back_to_full_file(self):
        self.assertEqual(validate_range_response(206, "bytes 100-199/1000", 100, 199), 1000)
        for status, header in ((200, "bytes 100-199/1000"), (206, "bytes 0-999/1000"), (206, None)):
            with self.assertRaises(ValueError):
                validate_range_response(status, header, 100, 199)

    def test_transfer_budget_is_global_and_fails_before_large_request(self):
        budget = TransferBudget(100)
        budget.transferred = 80
        budget.check(20)
        with self.assertRaises(RuntimeError):
            budget.check(21)


def metadata():
    return {"edition": 2, "short_name": "2t", "level_type": "heightAboveGround", "level": 2,
            "step_type": "instant", "start_step": 9, "end_step": 9, "step_units": "h", "units": "K",
            "initialization": INIT.isoformat(), "valid_time": (INIT + timedelta(hours=9)).isoformat(),
            "number_of_points": 20, "grid_type": "regular_ll", "product_template": 0,
            "temperature_f": 60.0}


class MetadataTests(unittest.TestCase):
    def test_units_and_reference_valid_time_are_verified(self):
        validate_metadata(metadata(), INIT, 9, DAY)
        self.assertAlmostEqual(kelvin_to_fahrenheit(273.15, "K"), 32)
        for key, value in (("units", "C"), ("step_type", "max"), ("level", 10), ("end_step", 12), ("product_template", 2), ("initialization", (INIT - timedelta(hours=6)).isoformat()), ("valid_time", (INIT + timedelta(hours=8)).isoformat())):
            record = metadata()
            record[key] = value
            with self.assertRaises(ValueError, msg=key):
                validate_metadata(record, INIT, 9, DAY)
        with self.assertRaises(ValueError):
            kelvin_to_fahrenheit(9999, "K")
        with self.assertRaises(ValueError):
            kelvin_to_fahrenheit(70, "F")

    def test_partial_daily_feature_is_not_accepted(self):
        with self.assertRaises(ValueError):
            sampled_daily_feature([metadata()], DAY, [9, 12])
        sample = metadata()
        feature = sampled_daily_feature([sample], DAY, [9])
        self.assertEqual(feature["sample_count"], 1)
        self.assertIn("not continuous daily high", feature["feature_definition"])

    def test_native_parser_can_decode_synthetic_grib(self):
        try:
            import eccodes
        except ImportError:
            self.skipTest("ecCodes optional in minimal stdlib environment")
        from klax_lab.grib_reader import extract_temperature
        from grib_test_support import new_regular_ll_sfc_grib2
        handle = new_regular_ll_sfc_grib2(eccodes)
        try:
            fields = {"Ni": 4, "Nj": 3, "latitudeOfFirstGridPointInDegrees": 35,
                      "latitudeOfLastGridPointInDegrees": 33, "longitudeOfFirstGridPointInDegrees": 240,
                      "longitudeOfLastGridPointInDegrees": 243, "iDirectionIncrementInDegrees": 1,
                      "jDirectionIncrementInDegrees": 1, "dataDate": 20250105, "dataTime": 0,
                      "typeOfLevel": "heightAboveGround", "level": 2, "shortName": "2t", "step": 9}
            for key, value in fields.items():
                eccodes.codes_set(handle, key, value)
            eccodes.codes_set_values(handle, [283.15] * 12)
            with TemporaryDirectory() as directory:
                path = Path(directory) / "synthetic.grib2"
                with path.open("wb") as stream:
                    eccodes.codes_write(handle, stream)
                result = extract_temperature(path, expected_init=INIT, expected_lead=9, target_day=DAY)
                self.assertAlmostEqual(result["temperature_f"], 50, places=3)
                self.assertEqual(result["level"], 2)
                self.assertEqual(result["number_of_points"], 12)
        finally:
            eccodes.codes_release(handle)


class FakeResponse:
    status_code = 206
    headers = {"Content-Range": "bytes 100-115/1000", "Content-Length": "16", "ETag": "synthetic"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield b"GRIBfixture!7777"


class CacheTests(unittest.TestCase):
    def test_verified_cache_reuse_makes_no_second_request(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.grib2"
            budget = TransferBudget(100)
            session = Mock()
            session.get.return_value = FakeResponse()
            first = _download(session, "https://synthetic.invalid/historical", path, budget, start=100, end=115)
            second = _download(session, "https://synthetic.invalid/historical", path, budget, start=100, end=115)
            self.assertEqual(first, second)
            self.assertEqual(session.get.call_count, 1)
            self.assertEqual(budget.transferred, 16)
            path.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                _load_cached(path, "https://synthetic.invalid/historical", 100, 115)

    def test_atomic_concurrent_identical_writes_and_conflict(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "immutable.json"
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda _: _write_immutable(path, b'{"synthetic":true}'), range(8)))
            self.assertEqual(path.read_bytes(), b'{"synthetic":true}')
            self.assertEqual(list(Path(directory).glob("*.part")), [])
            with self.assertRaises(ValueError):
                _write_immutable(path, b"different")


class ConcurrencyTests(unittest.TestCase):
    def test_reservations_prevent_concurrent_budget_oversubscription(self):
        budget = TransferBudget(100)
        entered = Barrier(5)
        finish = Event()

        def worker():
            with budget.reservation(25) as consume:
                entered.wait(timeout=5)
                finish.wait(timeout=5)
                consume(10)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker) for _ in range(4)]
            entered.wait(timeout=5)
            try:
                self.assertEqual(budget.reserved, 100)
                with self.assertRaises(RuntimeError):
                    with budget.reservation(1):
                        self.fail("concurrent budget exceeded")
            finally:
                finish.set()
            for future in futures:
                future.result()
        self.assertEqual(budget.transferred, 40)
        self.assertEqual(budget.reserved, 0)
        budget.check(60)

    def test_shared_request_limiter_gates_four_threads(self):
        clock = [0.0]
        pauses = []

        def advance(seconds):
            pauses.append(seconds)
            clock[0] += seconds

        with patch("klax_lab.acquire_weather.monotonic", side_effect=lambda: clock[0]), patch("klax_lab.acquire_weather.sleep", side_effect=advance), patch("requests.Session") as factory:
            factory.side_effect = Mock
            limiter = RateLimitedSession(2)
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda _: limiter.get("https://synthetic.invalid"), range(4)))
            self.assertEqual(pauses, [0.5, 0.5, 0.5])
            self.assertEqual(limiter.last_started, 1.5)
            self.assertEqual(sum(session.get.call_count for session in limiter._sessions), 4)
            limiter.close()

    def test_overlapping_cli_job_is_rejected(self):
        with TemporaryDirectory() as directory:
            with _cli_job_lock(Path(directory)):
                with self.assertRaises((RuntimeError, BlockingIOError)):
                    with _cli_job_lock(Path(directory)):
                        self.fail("a second job obtained the lock")
            with _cli_job_lock(Path(directory)):
                pass

    def test_completion_order_does_not_change_model_or_lead_order(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "external/weather_data_collector/weather_pipeline.py"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"synthetic source snapshot")
            for model in ("gfs", "nbm"):
                folder = root / model
                folder.mkdir()
                for lead in (9, 12):
                    point = metadata()
                    point.update({"start_step": lead, "end_step": lead, "valid_time": (INIT + timedelta(hours=lead)).isoformat(), "source_sha256": "synthetic"})
                    (folder / f"{lead}.point.json").write_text(json.dumps(point))

            def retrieve(root, model, initialization, lead, session, budget, offline):
                sleep(0.02 if lead == 9 else 0.001)
                return root / model / f"{lead}.grib2", {"sha256": "synthetic", "bytes": 0}, {}, IndexEntry("1", 0, 15, "synthetic")

            with patch("klax_lab.acquire_weather.PARENT_PIPELINE_SHA256", sha256(source.read_bytes()).hexdigest()), patch("klax_lab.acquire_weather._retrieve_field", side_effect=retrieve):
                result = acquire_day(root, DAY, ["nbm", "gfs"], leads_override=[12, 9], offline=True, verbose=False)
            self.assertEqual(list(result["models"]), ["gfs", "nbm"])
            self.assertEqual(result["requested_leads"], [9, 12])
            for model in result["models"].values():
                self.assertEqual([sample["end_step"] for sample in model["samples"]], [9, 12])
            self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
