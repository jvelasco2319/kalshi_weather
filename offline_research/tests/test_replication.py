"""Synthetic ledger audits with hand-computable probabilities, fees and PnL."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from klax_lab.replication import (
    TABLE_PATHS, _canonical_hash, independent_prediction, independent_probability,
    verify_development_run, verify_frozen_manifest, verify_model_records, verify_saved_forecasts,
)


def fixture():
    day = "2025-01-05"
    cutoff = "2025-01-05T14:00:00+00:00"
    entry = "2025-01-05T14:01:00+00:00"
    settled = "2025-01-06T12:00:00+00:00"
    policy = {
        "weather_training": ["2024-01-01", "2024-12-31"],
        "selection": ["2025-01-05", "2025-06-30"],
        "protected_final": ["2025-07-01", "2025-12-31"],
        "model_fit_cutoff_utc": "2025-01-05T00:00:00Z",
        "forecast_initialization_hour_utc": 0, "availability_delay_hours": 6,
        "decision_hour_utc": 14, "execution_delay_seconds": 60,
        "max_quote_age_seconds": 3660, "reference_quantity": 1,
        "target_expected_net_return": "0.10", "slippage_per_contract": "0.01",
        "fee_scenario": {"rate": "0.07", "rounding": "0.01"},
        "sensitivity_slippage": ["0.01"], "sensitivity_fee_rate": ["0.07"], "sensitivity_quantities": [1],
    }
    model = {"name": "gfs_bias_corrected", "definition": "Saved synthetic parameters; no refit claimed",
             "training_days": 1, "bias": 0.0, "sd": 2.0}
    version = _canonical_hash(model)
    dataset_version = "d" * 64
    contracts, outcomes, candles, reconciliation = [], [], [], []
    for ticker, ask, bid in (("SYNTH-A", "0.40", "0.35"), ("SYNTH-B", "0.42", "0.40")):
        contracts.append({"ticker": ticker, "climate_date": day, "lower_integer_f": None,
                          "upper_integer_f": 74, "open_time": "2025-01-04T14:00:00Z", "close_time": "2025-01-06T08:00:00Z"})
        outcomes.append({"ticker": ticker, "event_ticker": "SYNTH-EVENT", "climate_date": day, "yes_outcome": 1, "mapping_consistent": True,
                         "expiration_value_f": 74.0, "settlement_temperature_f": 74.0, "settlement_time": settled})
        reconciliation.append({"ticker": ticker, "climate_date": day, "climate_matches_expiration": True,
                               "settlement_label_reconciled": True, "climate_source_sha256": "a" * 64})
        candles.append({"ticker": ticker, "climate_date": day, "end_period_ts": int(datetime.fromisoformat(cutoff).timestamp()),
                        "yes_ask_close": ask, "yes_bid_close": bid, "source_sha256": "c" * 64})
    tables = {
        "training_forecasts": [{"climate_date": "2024-01-01", "gfs": 74.5, "nbm": 74.5, "available_at": "2024-01-01T06:00:00Z"}],
        "training_climate": [{"climate_date": "2024-01-01", "tmax_f": 74, "available_at": "2024-01-02T09:00:00Z"}],
        "forecasts": [{"climate_date": day, "gfs": 74.5, "nbm": 74.5, "available_at": "2025-01-05T06:00:00Z"}],
        "climate": [{"climate_date": day, "tmax_f": 74}],
        "climate_versions": [{"climate_date": day, "tmax_f": 74, "issued_at": "2025-01-06T09:00:00Z",
                              "available_at": "2025-01-06T09:00:00Z", "source_sha256": "a" * 64}],
        "contracts": contracts, "outcomes": outcomes, "candles": candles, "reconciliation": reconciliation,
    }
    # The rounded upper boundary is74.5, exactly the Gaussian mean. Thus p=.5.
    # Fees at these four prices are exactly2 cents after upward cent rounding.
    decisions = []
    for ticker, prices in (("SYNTH-A", {"YES": "0.41", "NO": "0.66"}), ("SYNTH-B", {"YES": "0.43", "NO": "0.61"})):
        for side, text_price in prices.items():
            price = Decimal(text_price)
            outlay = price + Decimal("0.02")
            profit = Decimal("0.5") - outlay
            roi = profit / outlay
            accepted = side == "YES"
            decisions.append({
                "decision_id": f"{version}:{day}:{ticker}:{side}", "ticker": ticker, "climate_date": day,
                "status": "ACCEPTED" if accepted else "SKIPPED", "reason": "ASSUMED_FILL_SCENARIO" if accepted else "EXPECTED_RETURN_BELOW_TARGET",
                "information_cutoff": cutoff, "decision_at": entry, "dataset_version": dataset_version,
                "model_version": version, "side": side, "quantity": 1, "entry_price": text_price,
                "entry_fee": "0.02", "settlement_cost": "0", "entry_outlay": str(outlay),
                "purchased_probability": "0.5", "expected_net_profit": str(profit), "expected_net_return": str(roi),
                "target_return": "0.10", "evidence_grade": "B", "price_source": "c" * 64,
                "selected": ticker == "SYNTH-A" and side == "YES",
            })
    chosen = decisions[0]
    # Winning YES pays1 dollar;1-.43=.57 profit. No account transaction occurred.
    ledger = [{"decision_id": chosen["decision_id"], "ticker": "SYNTH-A", "climate_date": day,
               "side": "YES", "quantity": 1, "entry_price": "0.41", "entry_fee": "0.02", "entry_outlay": "0.43",
               "payout": "1", "settlement_cost": "0", "net_profit": "0.57", "net_return": str(Decimal("0.57") / Decimal("0.43")),
               "expected_net_return": chosen["expected_net_return"], "settled_at": settled, "evidence_grade": "B",
               "simulation_label": "Historical simulation only; not actual account gains."}]
    return dict(model=model, decisions=decisions, ledger=ledger, policy=policy, tables=tables, dataset_version=dataset_version)


class ReplicationTests(unittest.TestCase):
    def failed(self, report, code):
        self.assertEqual(report["status"], "FAIL")
        self.assertIn(code, {row["check"] for row in report["failed_checks"]})

    def test_complete_base_audit_matches_hand_arithmetic(self):
        report = verify_model_records(**fixture())
        self.assertEqual(report["status"], "PASS", report["failed_checks"])
        self.assertEqual(report["recomputed_totals"]["total_net_profit"], "0.57")
        self.assertFalse(report["independent_refit_performed"])
        self.assertFalse(report["goal2_complete"])

    def saved_forecast_fixture(self):
        from klax_lab.baseline import forecast_artifacts, probability_scores
        candidate = fixture()
        tables = candidate["tables"]
        predicted = {row["climate_date"]: independent_prediction(candidate["model"], row) for row in tables["forecasts"]}
        labels = {row["climate_date"]: row for row in tables["climate"]}
        eligible = set(predicted)
        artifacts = forecast_artifacts(predicted, tables["forecasts"], tables["contracts"], tables["outcomes"], labels,
                                       eligible, _canonical_hash(candidate["model"]), candidate["dataset_version"])
        return dict(model=candidate["model"], tables=tables, dataset_version=candidate["dataset_version"], **artifacts,
                    summary=probability_scores(predicted, tables["contracts"], tables["outcomes"], labels, eligible))

    def test_full_saved_forecast_universe_includes_unselected_contract(self):
        values = self.saved_forecast_fixture()
        report = verify_saved_forecasts(**values)
        self.assertEqual(report["status"], "PASS", report["failed_checks"])
        self.assertTrue(report["saved_prediction_artifacts_verified"])
        self.assertEqual(report["contract_probabilities_verified"], 2)
        self.assertEqual([r["yes_probability"] for r in values["contract_probabilities"]], [.5, .5])
        self.assertEqual(values["daywise_scores"][0]["brier_sum"], .5)
        values["contract_probabilities"].pop()
        self.failed(verify_saved_forecasts(**values), "saved_probability_coverage")

    def test_saved_forecast_value_lineage_and_score_corruption_are_detected(self):
        faults = [("predictions", "mean_f", 999., "saved_forecast_mean"),
                  ("predictions", "sd_f", float("nan"), "saved_forecast_spread"),
                  ("predictions", "model_sha256", "altered", "saved_forecast_lineage"),
                  ("predictions", "feature_available_at", "changed", "saved_forecast_availability"),
                  ("contract_probabilities", "yes_probability", .99, "saved_contract_probability"),
                  ("contract_probabilities", "yes_outcome", 0, "saved_probability_outcome"),
                  ("daywise_scores", "crps_f", 999., "saved_daywise_crps"),
                  ("daywise_scores", "brier_sum", .25, "saved_daywise_brier_sum")]
        for artifact, field, value, expected in faults:
            with self.subTest(artifact=artifact, field=field):
                values = self.saved_forecast_fixture()
                values[artifact][0][field] = value
                self.failed(verify_saved_forecasts(**values), expected)

    def test_normal_distribution_bounds_and_saved_model_variants(self):
        self.assertEqual(independent_probability(74.5, 2, None, 74), 0.5)
        self.assertEqual(independent_probability(74.5, 2, 75, None), 0.5)
        self.assertAlmostEqual(independent_probability(75.5, 1, 75, 76), 0.6826894921370859)
        row = {"climate_date": "2025-01-05", "gfs": 70, "nbm": 74}
        self.assertEqual(independent_prediction({"name": "equal_blend_bias_corrected", "bias": 1, "sd": 3}, row), (73, 3))
        seasonal = {"name": "seasonal_climatology", "seasonal_labels": [{"position": 5, "tmax_f": 72}] * 30}
        self.assertEqual(independent_prediction(seasonal, row), (72, 1))

    def test_tampered_fee_profit_and_payout_are_detected(self):
        for field, code in (("entry_fee", "ledger_entry_fee"), ("net_profit", "ledger_net_profit"), ("payout", "ledger_payout")):
            candidate = fixture()
            candidate["ledger"][0][field] = "999"
            self.failed(verify_model_records(**candidate), code)

    def test_payout_comes_from_actual_binary_outcome_not_temperature_proxy(self):
        candidate = fixture()
        candidate["tables"]["outcomes"][0]["yes_outcome"] = 0
        self.failed(verify_model_records(**candidate), "ledger_payout")

    def test_probability_and_expected_profit_mismatch_are_detected(self):
        candidate = fixture()
        candidate["decisions"][0]["purchased_probability"] = "0.75"
        report = verify_model_records(**candidate)
        self.failed(report, "independent_probability")
        self.failed(report, "decision_expected_net_profit")

    def test_lower_edge_candidate_cannot_replace_highest_ev_choice(self):
        candidate = fixture()
        candidate["decisions"][0]["selected"] = False
        candidate["decisions"][2]["selected"] = True
        self.failed(verify_model_records(**candidate), "highest_ev_daily_selection")

    def test_omitted_unselected_candidate_is_detected(self):
        candidate = fixture()
        candidate["decisions"].pop()
        self.failed(verify_model_records(**candidate), "complete_candidate_universe")

    def test_duplicate_day_trade_and_mismatched_ledger_are_detected(self):
        candidate = fixture()
        duplicate = deepcopy(candidate["ledger"][0])
        duplicate["decision_id"] += "duplicate"
        candidate["ledger"].append(duplicate)
        report = verify_model_records(**candidate)
        self.failed(report, "daily_entry_cap")
        self.failed(report, "ledger_matches_selection")

    def test_future_quote_cannot_support_saved_decision(self):
        candidate = fixture()
        candidate["tables"]["candles"][0]["end_period_ts"] += 3600
        self.failed(verify_model_records(**candidate), "complete_candidate_universe")

    def test_late_training_revision_and_forecast_release_fail(self):
        candidate = fixture()
        candidate["tables"]["training_climate"][0]["available_at"] = "2026-01-01T00:00:00Z"
        self.failed(verify_model_records(**candidate), "training_label_asof")
        candidate = fixture()
        candidate["tables"]["forecasts"][0]["available_at"] = "2025-01-05T15:00:00Z"
        self.failed(verify_model_records(**candidate), "forecast_asof")

    def test_unverified_mapping_and_preentry_settlement_fail(self):
        candidate = fixture()
        candidate["tables"]["outcomes"][0]["mapping_consistent"] = None
        self.failed(verify_model_records(**candidate), "settlement_mapping")
        candidate = fixture()
        candidate["tables"]["outcomes"][0]["settlement_time"] = "2025-01-05T10:00:00Z"
        candidate["ledger"][0]["settled_at"] = "2025-01-05T10:00:00Z"
        self.failed(verify_model_records(**candidate), "settlement_after_entry")

    def test_mutated_model_parameters_change_model_identity(self):
        candidate = fixture()
        candidate["model"]["bias"] = 5
        self.failed(verify_model_records(**candidate), "complete_candidate_universe")

    def test_missing_api_temperature_requires_asof_nws_lineage_without_changing_payout(self):
        candidate = fixture()
        for row in candidate["tables"]["outcomes"]:
            row.update(expiration_value_f=None, settlement_temperature_f=None,
                       mapping_validation_basis="NWS_report_as_of_settlement_against_actual_binary_result",
                       mapping_climate_source_sha256="a" * 64)
        self.assertEqual(verify_model_records(**candidate)["status"], "PASS")
        candidate["tables"]["climate_versions"][0]["available_at"] = "2025-01-07T00:00:00Z"
        self.failed(verify_model_records(**candidate), "climate_asof_settlement")

    def test_conflicting_explicit_temperatures_cannot_be_rescued_as_missing(self):
        candidate = fixture()
        candidate["tables"]["outcomes"][0]["expiration_value_f"] = 73.0
        for row in candidate["tables"]["outcomes"]:
            row.update(settlement_temperature_f=None,
                       mapping_validation_basis="NWS_report_as_of_settlement_against_actual_binary_result",
                       mapping_climate_source_sha256="a" * 64)
        self.failed(verify_model_records(**candidate), "conflicting_event_temperatures")

    def test_manifest_corruption_path_escape_and_holdout_paths_fail_before_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "synthetic.txt"
            source.write_text("synthetic bytes", encoding="utf-8")
            body = {"files": [{"path": "synthetic.txt", "bytes": source.stat().st_size,
                                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}], "policy": {"synthetic": True}}
            manifest = {**body, "version": _canonical_hash(body)}
            self.assertEqual(verify_frozen_manifest(root, manifest)["status"], "PASS")
            source.write_text("corrupted", encoding="utf-8")
            self.failed(verify_frozen_manifest(root, manifest), "frozen_file_hash")
            for forbidden in ("../outside", "data/normalized/protected_final/labels/outcomes.parquet"):
                candidate = deepcopy(manifest)
                candidate["files"][0]["path"] = forbidden
                self.failed(verify_frozen_manifest(root, candidate), "manifest_structure")

    def test_filesystem_wrapper_verifies_synthetic_saved_run(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        candidate = fixture()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            records = []
            for name, relative in TABLE_PATHS.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(pa.Table.from_pylist(candidate["tables"][name]), path)
                records.append({"path": relative, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            body = {"files": records, "policy": {"evaluation": candidate["policy"]}}
            manifest = {**body, "version": _canonical_hash(body)}
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            candidate["dataset_version"] = manifest["version"]
            for decision in candidate["decisions"]:
                decision["dataset_version"] = manifest["version"]
            model_report = verify_model_records(**candidate)
            self.assertEqual(model_report["status"], "PASS", model_report["failed_checks"])
            run = root / "runs/synthetic-baseline"
            run.mkdir(parents=True)
            name = candidate["model"]["name"]
            assumptions = {"slippage": "0.01", "fee_rate": "0.07", "quantity": 1}
            scenario_id = _canonical_hash(assumptions)[:12]
            files = {
                "summary.json": {"dataset_version": manifest["version"], "models": {name: {
                    "historical_assumed_fill": model_report["recomputed_totals"],
                    "predeclared_cost_sensitivity": [{"scenario_id": scenario_id, **assumptions, "summary": model_report["recomputed_totals"]}]}}},
                "fitted_models.json": {name: candidate["model"]},
                f"{name}_decisions.json": candidate["decisions"],
                f"{name}_ledger.json": candidate["ledger"],
                f"sensitivity/{name}_{scenario_id}_ledger.json": candidate["ledger"],
            }
            for name, content in files.items():
                (run / name).parent.mkdir(parents=True, exist_ok=True)
                (run / name).write_text(json.dumps(content), encoding="utf-8")
            from klax_lab.baseline import forecast_artifacts, probability_scores
            from klax_lab.provenance import inventory, write_json
            name = candidate["model"]["name"]
            tables = candidate["tables"]
            predicted = {r["climate_date"]: independent_prediction(candidate["model"], r) for r in tables["forecasts"]}
            labels = {r["climate_date"]: r for r in tables["climate"]}
            eligible = set(predicted)
            artifacts = forecast_artifacts(predicted, tables["forecasts"], tables["contracts"], tables["outcomes"], labels,
                                           eligible, _canonical_hash(candidate["model"]), manifest["version"])
            for kind, rows in artifacts.items(): write_json(run / (name + "_" + kind + ".json"), rows)
            write_json(run / "selection_eligibility.json", {"eligible_days": sorted(eligible), "excluded": []})
            summary = files["summary.json"]
            summary["eligibility_artifact"] = inventory(run, [run / "selection_eligibility.json"])[0]
            summary["models"][name]["model_sha256"] = _canonical_hash(candidate["model"])
            summary["models"][name]["forecast_artifacts"] = inventory(run, [run / (name + "_" + kind + ".json") for kind in artifacts])
            summary["models"][name]["forecast_scores"] = probability_scores(predicted, tables["contracts"], tables["outcomes"], labels, eligible)
            write_json(run / "summary.json", summary)
            report = verify_development_run(root, run, manifest_path, run / "replication.json")
            self.assertEqual(report["status"], "PASS", report["failed_checks"])
            self.assertTrue((run / "replication.json").is_file())
            self.assertGreater(len(report["artifact_hashes"]), 3)
            self.assertFalse(report["protected_final_evaluated"])
            scenario = report["sensitivity_ledger_checks"][candidate["model"]["name"]][0]
            self.assertEqual(scenario["status"], "PASS")
            self.assertFalse(scenario["candidate_selection_verified"])
            self.assertTrue(report["saved_prediction_artifacts_verified"])
            altered_path = run / (name + "_predictions.json")
            altered = json.loads(altered_path.read_text())
            altered[0]["mean_f"] += 1
            write_json(altered_path, altered)
            changed = verify_development_run(root, run, manifest_path)
            self.failed(changed, "run_verification")
            self.assertFalse(changed["saved_prediction_artifacts_verified"])
            # Updating the inventory cannot make a numerically wrong forecast pass.
            summary["models"][name]["forecast_artifacts"] = inventory(run, [run / (name + "_" + kind + ".json") for kind in artifacts])
            write_json(run / "summary.json", summary)
            changed = verify_development_run(root, run, manifest_path)
            self.failed(changed, "saved_forecast_mean")
            self.assertFalse(changed["saved_prediction_artifacts_verified"])


if __name__ == "__main__":
    unittest.main()
