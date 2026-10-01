"""End-to-end engineering fixture; every weather/market value is synthetic.

Build a complete development calendar in a temporary project, then exercise the
actual baseline, cost-sensitivity, artifact, manifest and replication pipeline.
No actual input dataset, protected-final value, or online service is accessed.
Synthetic returns are never evidence about Kalshi profitability.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from klax_lab.baseline import run_development_baselines
from klax_lab.models import BASELINE_DEFINITIONS
from klax_lab.replication import verify_development_run


UTC = timezone.utc
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _days(start: str, end: str):
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        yield current
        current += timedelta(days=1)


def _synthetic_partition(days: list[date], split: str) -> dict[str, list[dict]]:
    tables = {name: [] for name in ("forecasts", "contracts", "candles", "climate", "climate_versions", "outcomes", "reconciliation")}
    for index, target in enumerate(days):
        day = target.isoformat()
        midnight = datetime(target.year, target.month, target.day, tzinfo=UTC)
        # Completely invented temperature pattern, unrelated to actual weather.
        temperature = 75 if index % 5 == 0 else 74
        issued = midnight + timedelta(days=1, hours=9)
        settled = midnight + timedelta(days=1, hours=12)
        climate_hash = _hash(f"SYNTHETIC NWS {day} {temperature}")
        source_hash = _hash(f"SYNTHETIC API {day} {temperature}")
        event = f"SYNTHETIC-EVENT-{day}"
        jitter = ((index % 3) - 1) * 0.25 if split == "weather_training" else 0.0
        tables["forecasts"].append({
            "climate_date": day, "partition": split, "gfs": 73.5 + jitter, "nbm": 74.5 - jitter,
            "available_at": (midnight + timedelta(hours=6)).isoformat(),
            "historical_availability_proven": False, "feature_role": "SYNTHETIC_INTEGRATION_FIXTURE",
        })
        label = {
            "climate_date": day, "partition": split, "tmax_f": temperature,
            "issued_at": issued.isoformat(), "available_at": issued.isoformat(),
            "source_sha256": climate_hash, "label_role": "SYNTHETIC_NWS_COPY",
        }
        tables["climate"].append(label)
        tables["climate_versions"].append(dict(label))
        # LOW and HIGH are complementary integer-temperature contracts. The LOW
        # quote alternates between cheap YES and cheap NO. HIGH is less favorable,
        # exercising the daily highest-EV choice across both contracts and sides.
        for suffix, lower, upper in (("LOW", None, 74), ("HIGH", 75, None)):
            ticker = f"SYNTHETIC-{day}-{suffix}"
            yes_outcome = int(temperature <= 74) if suffix == "LOW" else int(temperature >= 75)
            tables["contracts"].append({
                "ticker": ticker, "event_ticker": event, "climate_date": day, "partition": split,
                "lower_integer_f": lower, "upper_integer_f": upper,
                "open_time": (midnight - timedelta(days=1) + timedelta(hours=14)).isoformat(),
                "close_time": (midnight + timedelta(days=1, hours=8)).isoformat(),
                "source_sha256": source_hash, "historical_rule_revision_verified": False,
            })
            bid, ask = (("0.79", "0.80") if index % 2 == 0 else ("0.19", "0.20")) if suffix == "LOW" else ("0.45", "0.55")
            tables["candles"].append({
                "ticker": ticker, "climate_date": day, "partition": split,
                "end_period_ts": int((midnight + timedelta(hours=14)).timestamp()),
                "yes_bid_close": bid, "yes_ask_close": ask, "period_minutes": 60,
                "evidence_grade": "B", "source_sha256": _hash(f"SYNTHETIC QUOTE {ticker} {bid} {ask}"),
            })
            tables["outcomes"].append({
                "ticker": ticker, "event_ticker": event, "climate_date": day, "partition": split,
                "yes_outcome": yes_outcome, "expiration_value_f": float(temperature),
                "settlement_temperature_f": float(temperature), "event_temperature_status": "unique",
                "temperature_reference_basis": "own_explicit_expiration",
                "temperature_reference_tickers": [ticker], "temperature_reference_source_hashes": [source_hash],
                "mapping_consistent": True, "economic_eligible_mapping": True,
                "mapping_validation_basis": "explicit_API_event_temperature", "mapping_climate_source_sha256": None,
                "source_sha256": source_hash, "settlement_time": settled.isoformat(),
            })
            tables["reconciliation"].append({
                "ticker": ticker, "climate_date": day, "partition": split,
                "settlement_time_parseable": True, "climate_as_of_settlement_available": True,
                "climate_matches_expiration": True, "climate_matches_binary_result": True,
                "settlement_label_reconciled": True, "climate_source_sha256": climate_hash,
            })
    return tables


def build_synthetic_project(root: Path) -> dict:
    # Read the real registered *configuration*, never the real historical data.
    policy = json.loads((SOURCE_ROOT / "configs/evaluation.json").read_text(encoding="utf-8"))
    policy["bootstrap_samples"] = 32
    policy["fixture_notice"] = "ALL INPUTS AND RETURNS ARE SYNTHETIC; ENGINEERING TEST ONLY"
    _save_json(root / "configs/evaluation.json", policy)
    for source in (SOURCE_ROOT / "src/klax_lab").glob("*.py"):
        destination = root / "src/klax_lab" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    counts = {}
    for split in ("weather_training", "selection"):
        days = list(_days(*policy[split]))
        counts[split] = len(days)
        for name, rows in _synthetic_partition(days, split).items():
            category = "features" if name in ("forecasts", "contracts", "candles") else "labels"
            path = root / "data/normalized" / split / category / f"{name}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows), path)
    # This is a synthetic terminal-state fixture, not an acquisition claim. No
    # protected-final tables are constructed, and no source is downloaded.
    _save_json(root / "data/manifests/weather/range_synthetic.json", {
        "synthetic_fixture": True, "start_date": "2024-01-01", "end_date": "2025-12-31",
        "model_sources": ["gfs", "nbm"], "status": "COMPLETED", "days_complete": 731,
        "days_with_errors": 0, "last_attempted_date": "2025-12-31",
    })
    _save_json(root / "data/manifests/forecast_normalization.json", {
        "synthetic_fixture": True, "complete_model_pairs_by_partition": counts,
        "invalid_acquisition_manifests": [], "source_availability_verified": False,
    })
    _save_json(root / "data/manifests/market_quality.json", {
        "synthetic_fixture": True, "partitions": {"selection": {"critical_integrity_passed": True}},
    })
    return policy


class BaselineIntegrationTests(unittest.TestCase):
    def test_full_development_pipeline_and_independent_replication(self):
        with tempfile.TemporaryDirectory(prefix="klax-synthetic-integration-") as folder:
            root = Path(folder)
            policy = build_synthetic_project(root)
            result = run_development_baselines(root, policy)
            self.assertEqual(result["status"], "DEVELOPMENT_BASELINES_COMPLETE")
            self.assertEqual(result["training_days"], 366)
            self.assertEqual(result["eligible_selection_days"], 177)
            self.assertEqual(set(result["models"]), set(BASELINE_DEFINITIONS))
            self.assertFalse(result["protected_final_evaluated"])
            self.assertFalse(result["goal1_complete"])
            self.assertFalse(result["goal2_complete"])
            self.assertFalse((root / "data/normalized/protected_final").exists())
            run = Path(result["path"])
            for name, model in result["models"].items():
                self.assertEqual(model["forecast_scores"]["weather_days"], 177)
                self.assertEqual(model["forecast_scores"]["contract_count"], 354)
                self.assertGreater(model["historical_assumed_fill"]["trade_count"], 0)
                self.assertEqual({r["path"] for r in model["forecast_artifacts"]},
                                 {f"{name}_{kind}.json" for kind in ("predictions", "contract_probabilities", "daywise_scores")})
                for record in model["forecast_artifacts"]:
                    path = run / record["path"]
                    self.assertEqual(path.stat().st_size, record["bytes"])
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
                scenarios = model["predeclared_cost_sensitivity"]
                self.assertEqual(len(scenarios), 12)
                self.assertEqual(len({row["scenario_id"] for row in scenarios}), 12)
                ledger = json.loads((run / f"{name}_ledger.json").read_text(encoding="utf-8"))
                # Both bought sides and observed win/loss outcomes are exercised.
                self.assertEqual({row["side"] for row in ledger}, {"YES", "NO"})
                self.assertTrue(any(row["payout"] == "0" for row in ledger))
                self.assertTrue(any(row["payout"] == "1" for row in ledger))
                for row in scenarios:
                    self.assertTrue((run / "sensitivity" / f"{name}_{row['scenario_id']}_ledger.json").is_file())
            report = verify_development_run(root, run, output_path=run / "replication.json")
            self.assertEqual(report["status"], "PASS", report["failed_checks"][:12])
            self.assertEqual(set(report["models"]), set(BASELINE_DEFINITIONS))
            self.assertGreater(report["checks"], 1000)
            self.assertTrue(report["saved_prediction_artifacts_verified"])
            for name in BASELINE_DEFINITIONS:
                self.assertEqual(report["models"][name]["status"], "PASS")
                self.assertEqual(report["saved_forecast_checks"][name]["forecast_days_verified"], 177)
                self.assertEqual(report["saved_forecast_checks"][name]["contract_probabilities_verified"], 354)
                self.assertEqual(report["saved_forecast_checks"][name]["daywise_scores_verified"], 177)
                scenarios = report["sensitivity_ledger_checks"][name]
                self.assertEqual(len(scenarios), 12)
                self.assertTrue(all(row["status"] == "PASS" for row in scenarios))
                self.assertTrue(all(row["candidate_selection_verified"] is False for row in scenarios))
            self.assertFalse(report["independent_refit_performed"])
            self.assertFalse(report["protected_final_evaluated"])
            self.assertFalse(report["goal2_complete"])
            self.assertTrue(all("protected_final" not in row["path"] for row in report["artifact_hashes"]))
            self.assertTrue((run / "replication.json").is_file())


if __name__ == "__main__":
    unittest.main()
