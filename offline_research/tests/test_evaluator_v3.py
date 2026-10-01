from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

import pytest

from klax_lab.campaign_v3 import CANDIDATE_ARTIFACTS, CandidateEvaluationBundle, V3EvaluationContext
from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from klax_lab.evaluation import FeeScenario
from klax_lab.evaluator_v3 import (
    EVALUATOR_VERSION, FrozenEvaluationInputsV3, OfflineCandidateEvaluatorV3,
    evaluation_policy_record, evaluation_policy_sha256, primary_fee_scenario,
    verify_candidate_evaluation_artifacts,
)
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.replication_v3 import independently_recompute_candidate
from klax_lab.promotion_v3 import V3PromotionPolicy, evaluate_v3_promotion
from klax_lab.research_plan_v3 import compile_plan_v3, make_plan_v3


UTC = timezone.utc
BOUNDS = ((None, 64), (65, 67), (68, 70), (71, 73), (74, 76), (77, None))


def _forecast_rows(signal: float, day_index: int) -> list[dict]:
    rows = []
    for lead in (9, 15, 21, 27):
        rows.extend((
            {
                "model": "gefs", "field_id": "temperature_2m", "member_id": "avg",
                "lead_hours": lead, "value": 72 + signal + lead * .01,
                "is_missing": False, "as_of_validated": True,
            },
            {
                "model": "gefs", "field_id": "temperature_2m", "member_id": "spr",
                "lead_hours": lead, "value": 1.5 + (day_index % 5) * .05 + lead * .001,
                "is_missing": False, "as_of_validated": True,
            },
        ))
    return rows


def _contracts(day: date, *, volume: str, cheap_side: str) -> tuple[list[dict], list[dict]]:
    event = f"KXHIGHLAX-{day:%y%b%d}".upper()
    features, labels = [], []
    for index, (lower, upper) in enumerate(BOUNDS):
        ticker = f"{event}-B{index}"
        stamp = int(datetime.combine(day, time(15), UTC).timestamp())
        if cheap_side == "YES":
            bid, ask = "0.04", "0.05"
        else:
            bid, ask = "0.95", "0.96"
        candle = {
            "ticker": ticker, "end_period_ts": stamp, "period_minutes": 1,
            "yes_bid_close": bid, "yes_ask_close": ask,
            "volume_contracts": volume, "evidence_grade": "B_aggregated_quote",
            "historical_depth_available": False, "hypothetical_fill_supported": False,
            "source_sha256": f"{index + 1:x}" * 64,
            "source_endpoint_type": "historical_market_candlesticks_1m",
        }
        features.append({
            "ticker": ticker,
            "interval": {"integer_lower_f": lower, "integer_upper_f": upper},
            "market": {"latest_completed_candle": candle},
        })
        labels.append({"ticker": ticker, "yes_outcome": 0})
    return features, labels


def _feature(day: date, role: str, signal: float, index: int, *, volume: str = "50",
             cheap_side: str = "YES") -> dict:
    row = {
        "schema_version": 1,
        "partition": "weather_training" if role == "weather_model_training" else "selection",
        "data_role": role,
        "climate_date": day.isoformat(),
        "decision_time_utc": "15:00",
        "decision_at": datetime.combine(day, time(15), UTC).isoformat(),
        "forecasts": _forecast_rows(signal, index),
        "observations": [],
        "as_of_join_validated": True,
        "contains_settlement_label": False,
    }
    if role != "weather_model_training":
        contracts, _ = _contracts(day, volume=volume, cheap_side=cheap_side)
        row.update({"event_ticker": contracts[0]["ticker"].rsplit("-", 1)[0],
                    "contracts": contracts})
    else:
        row["contains_market_evidence"] = False
    return row


def _label(day: date, role: str, high: int, *, volume: str = "50",
           cheap_side: str = "YES") -> dict:
    row = {
        "schema_version": 1,
        "partition": "weather_training" if role == "weather_model_training_label" else "selection",
        "data_role": role,
        "climate_date": day.isoformat(),
        "reported_high_f": high,
    }
    if role != "weather_model_training_label":
        contracts, labels = _contracts(day, volume=volume, cheap_side=cheap_side)
        for contract, outcome in zip(contracts, labels):
            lower = contract["interval"]["integer_lower_f"]
            upper = contract["interval"]["integer_upper_f"]
            outcome["yes_outcome"] = int(
                (lower is None or high >= lower) and (upper is None or high <= upper))
        row.update({"event_ticker": contracts[0]["ticker"].rsplit("-", 1)[0],
                    "contracts": labels})
    return row


def frozen_inputs(*, volume: str = "50", cheap_side: str = "YES") -> FrozenEvaluationInputsV3:
    train_features, train_labels = [], []
    start = date(2024, 1, 1)
    for index in range(90):
        day = start + timedelta(days=index)
        signal = (index % 30 - 15) / 3
        high = round(72 + signal + (index % 3 - 1) * .2)
        train_features.append(_feature(day, "weather_model_training", signal, index))
        train_labels.append(_label(day, "weather_model_training_label", high))

    calibration_features, calibration_labels = [], []
    start = date(2025, 1, 5)
    for index in range(30):
        day = start + timedelta(days=index)
        signal = (index % 15 - 7) / 2
        high = round(72 + signal)
        calibration_features.append(_feature(
            day, "development_calibration", signal, index,
            volume=volume, cheap_side=cheap_side))
        calibration_labels.append(_label(
            day, "development_calibration_label", high,
            volume=volume, cheap_side=cheap_side))

    evaluation_features, evaluation_labels = [], []
    start = date(2025, 2, 4)
    for index in range(10):
        day = start + timedelta(days=index)
        signal = (index - 4.5) / 2
        high = round(72 + signal)
        evaluation_features.append(_feature(
            day, "development_evaluation", signal, index,
            volume=volume, cheap_side=cheap_side))
        evaluation_labels.append(_label(
            day, "development_evaluation_label", high,
            volume=volume, cheap_side=cheap_side))
    evaluation_dates = [row["climate_date"] for row in evaluation_labels]
    calibration_dates = [row["climate_date"] for row in calibration_labels]
    folds = {
        "folds_id": "f" * 64,
        "calibration_dates": calibration_dates,
        "folds": [
            {"fold_id": f"v3-evaluation-{index + 1}", "fold_index": index + 1,
             "dates": evaluation_dates[index * 2:(index + 1) * 2]}
            for index in range(5)
        ],
    }
    return FrozenEvaluationInputsV3(
        "d" * 64, {}, tuple(train_features), tuple(train_labels),
        tuple(calibration_features), tuple(calibration_labels),
        tuple(evaluation_features), tuple(evaluation_labels), folds,
    )


def plan(*, sides: str = "YES", minimum_volume: int = 10):
    return make_plan_v3(
        colony="execution_abstention", stage="economic_simulation",
        data_bundle_version="fixture-bundle", data_bundle_sha256="b" * 64,
        forecast_source_set="gefs_summary", feature_set="temperature_only",
        regime_model="pooled", minimum_regime_training_days=30,
        probability_family="gaussian_blend",
        calibration_operator="gaussian_integer_interval",
        market_residual_model="none", abstention_operator="fixed_uncertainty_buffer",
        decision_time_utc="15:00", entry_threshold=.10, sides=sides,
        uncertainty_buffer=0.0, maximum_interval_width_f=12.0,
        minimum_candle_volume=minimum_volume, maximum_price_age_minutes=5,
        maximum_spread_cents=5, entry_price_floor_cents=5,
        entry_price_ceiling_cents=90,
    )


def context() -> V3EvaluationContext:
    return V3EvaluationContext(
        campaign_id="fixture-campaign", scope="development_evaluation_only",
        data_bundle_version="fixture-bundle", data_bundle_sha256="b" * 64,
        partition_contract={
            "training_end_inclusive": "2024-12-31",
            "development_calibration_start_inclusive": "2025-01-05",
            "development_calibration_end_inclusive": "2025-02-03",
            "development_evaluation_start_inclusive": "2025-02-04",
            "development_evaluation_end_inclusive": "2025-06-30",
            "development_fold_count": 5,
        },
        partition_contract_sha256="c" * 64, protected_final_roots=("data/protected_final",),
    )


def evaluate(tmp_path: Path, *, sides: str = "YES", volume: str = "50",
             minimum_volume: int = 10) -> CandidateEvaluationBundle:
    candidate_plan = plan(sides=sides, minimum_volume=minimum_volume)
    evaluator = OfflineCandidateEvaluatorV3(
        frozen_inputs(volume=volume, cheap_side=sides), tmp_path / "artifacts",
        primary_fee_scenario(),
        "fixture-bundle", "b" * 64,
    )
    return evaluator.evaluate(
        plan=candidate_plan,
        execution_manifest=compile_plan_v3(candidate_plan).execution_manifest,
        context=context(),
    )


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_real_replay_selects_requested_side_and_at_most_one_purchase_per_event(
    tmp_path: Path, side: str,
) -> None:
    bundle = evaluate(tmp_path, sides=side)
    economics = bundle.candidate["historical_assumed_fill"]
    assert economics["trade_count"] == 10
    assert len(economics["selected_event_ids"]) == len(set(economics["selected_event_ids"]))
    artifact = tmp_path / "artifacts" / bundle.candidate["research_plan_sha256"]
    ledger = __import__("json").loads((artifact / "ledger.json").read_text())
    assert {row["side"] for row in ledger["settlements"]} == {side}
    assert all(float(value) >= .10 for value in economics["selected_trade_expected_net_returns"])


def test_no_trade_case_fails_closed_without_fabricating_bootstrap_return(tmp_path: Path) -> None:
    bundle = evaluate(tmp_path, sides="YES", volume="0", minimum_volume=10)
    economics = bundle.candidate["historical_assumed_fill"]
    assert economics["trade_count"] == 0
    assert economics["capital_weighted_return"] is None
    assert economics["bootstrap"]["lower_95"] is None
    assert economics["bootstrap"]["undefined_resamples"] == 10_000
    assert all(row["trade_count"] == 0 and row["capital_weighted_return"] is None
               for row in bundle.folds)
    assert all(value == {} for value in economics["contribution_breakdowns"].values())


def test_fold_breakdown_and_remove_best_day_evidence_is_complete(tmp_path: Path) -> None:
    bundle = evaluate(tmp_path)
    economics = bundle.candidate["historical_assumed_fill"]
    assert len(bundle.folds) == 5
    assert sum(row["trade_count"] for row in bundle.folds) == economics["trade_count"]
    assert sorted(day for row in bundle.folds for day in row["selected_settlement_days"]) == sorted(
        economics["selected_settlement_days"])
    assert set(economics["contribution_breakdowns"]) == {
        "calendar_month", "weather_regime", "entry_price_band", "purchase_side",
        "decision_time",
    }
    assert economics["capital_weighted_return_after_removing_most_profitable_day"] is not None
    assert bundle.candidate["forecast_scores"]["probability_conservation_passed"] is True
    assert bundle.reference["forecast_scores"]["probability_conservation_passed"] is True


def test_artifacts_are_deterministic_canonical_and_campaign_bundle_compatible(tmp_path: Path) -> None:
    first = evaluate(tmp_path)
    second = evaluate(tmp_path)
    assert isinstance(first, CandidateEvaluationBundle)
    assert first == second
    assert set(first.artifact_sha256s) == CANDIDATE_ARTIFACTS
    assert first.artifact_sha256s["evaluation_sha256"] == canonical_hash({
        "candidate": first.candidate, "reference": first.reference,
        "folds": first.folds, "stress_return": first.stress_return,
    })
    artifact = tmp_path / "artifacts" / first.candidate["research_plan_sha256"]
    verified = verify_candidate_evaluation_artifacts(
        artifact, expected_plan_sha256=first.candidate["research_plan_sha256"],
        expected_dataset_id="d" * 64,
    )
    assert verified["status"] == "PASS"
    assert verified["artifact_sha256s"] == first.artifact_sha256s
    compiled = json.loads((artifact / "compiled_manifest.json").read_text())
    state = compiled["fitted_model_state"]
    assert state["contains_training_or_calibration_rows"] is False
    assert state["contains_development_labels_or_outcomes"] is False
    assert state["protected_final_used_for_fit"] is False
    assert "climate_date" not in json.dumps(state, sort_keys=True)
    assert fitted_candidate_model_from_state(state).identity == compiled["fitted_model_sha256"]
    tampered_state = json.loads(json.dumps(state))
    tampered_state["state_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="modified"):
        fitted_candidate_model_from_state(tampered_state)
    gate = evaluate_v3_promotion(
        first.candidate, first.reference, first.folds, first.stress_return,
        V3PromotionPolicy(), independently_verified=False, critic_allowed=False,
    )
    assert gate["passed"] is False
    assert "insufficient_simulated_trades" in gate["reasons"]


def test_independent_recomputation_passes_and_detects_corrupted_payout(
    tmp_path: Path,
) -> None:
    candidate_plan = plan()
    inputs = frozen_inputs()
    fee = primary_fee_scenario()
    artifact_root = tmp_path / "discovery"
    evaluator = OfflineCandidateEvaluatorV3(
        inputs, artifact_root, fee, "fixture-bundle", "b" * 64)
    evaluator.evaluate(
        plan=candidate_plan,
        execution_manifest=compile_plan_v3(candidate_plan).execution_manifest,
        context=context(),
    )
    discovery = artifact_root / candidate_plan.identity
    verification = tmp_path / "independent-verification" / candidate_plan.identity
    result = independently_recompute_candidate(
        plan=candidate_plan, inputs=inputs, fee_scenario=fee,
        primary_artifact_directory=discovery,
        output_directory=verification,
        partition_contract_sha256="c" * 64,
    )
    assert result["status"] == "PASS"
    assert result["differences"] == []

    ledger_path = discovery / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["settlements"]
    ledger["settlements"][0]["payout"] = "0.123"
    ledger_path.write_text(json.dumps(
        ledger, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False), encoding="utf-8")
    corrupted = independently_recompute_candidate(
        plan=candidate_plan, inputs=inputs, fee_scenario=fee,
        primary_artifact_directory=discovery,
        output_directory=verification,
        partition_contract_sha256="c" * 64,
    )
    assert corrupted["status"] == "FAIL"
    assert "payouts" in corrupted["differences"]


def test_partition_leakage_and_protected_paths_are_rejected(tmp_path: Path) -> None:
    source = frozen_inputs()
    leaked_labels = list(source.evaluation_labels)
    leaked_labels[0] = {**leaked_labels[0], "climate_date": "2025-02-03"}
    leaked = replace(source, evaluation_labels=tuple(leaked_labels))
    with pytest.raises(ValueError, match="partition"):
        leaked.validate("15:00")
    with pytest.raises(ValueError, match="protected-final"):
        FrozenEvaluationInputsV3.from_directory(tmp_path / "data" / "protected_final")


def test_policy_hash_is_explicit_stable_and_offline() -> None:
    record = evaluation_policy_record()
    assert record["policy_version"] == EVALUATOR_VERSION
    assert record["network_permitted"] is False
    assert record["protected_final_permitted"] is False
    assert record["maximum_selected_purchases_per_event"] == 1
    assert record["primary_fee_scenario"]["rate"] == "0.07"
    assert record["bootstrap"]["resamples"] == 10_000
    assert evaluation_policy_sha256() == canonical_hash(record)
    with pytest.raises(ValueError, match="fee scenario"):
        OfflineCandidateEvaluatorV3(
            frozen_inputs(), Path("artifacts"), FeeScenario("weaker", "0", "fixture"),
            "fixture-bundle", "b" * 64,
        )


def test_bound_loader_connects_dataset_identity_to_readiness_bundle(
    tmp_path: Path, monkeypatch,
) -> None:
    project = tmp_path / "project"
    dataset = project / "data" / "frozen" / "v3"
    manifests = project / "data" / "manifests"
    dataset.mkdir(parents=True)
    manifests.mkdir(parents=True)
    files = {
        "dataset_component": manifests / "v3_frozen_dataset.json",
        "fold_component": manifests / "v3_five_fold_split.json",
        "frozen_dataset_manifest": dataset / "manifest.json",
        "frozen_fold_artifact": dataset / "folds.json",
    }
    for index, path in enumerate(files.values()):
        path.write_text(json.dumps({"fixture": index}), encoding="utf-8")
    body = {
        "schema_version": "klax-v3-data-bundle-v1",
        "scope": "weather_training_calibration_and_scored_development_only",
        "dataset_id": "d" * 64,
        "folds_id": "f" * 64,
        **{
            name: {"path": path.relative_to(project).as_posix(),
                   "sha256": sha256_file(path)}
            for name, path in files.items()
        },
        "network_used": False,
        "protected_final_read": False,
    }
    bundle = {**body, "bundle_sha256": canonical_hash(body)}
    bundle_path = manifests / "v3_data_bundle.json"
    write_json(bundle_path, bundle)
    source = frozen_inputs()
    monkeypatch.setattr(
        "klax_lab.evaluator_v3.FrozenEvaluationInputsV3.from_directory",
        lambda path: source,
    )
    evaluator = OfflineCandidateEvaluatorV3.from_bound_directory(
        project_root=project, dataset_root=dataset, artifact_root=project / "runs",
        fee_scenario=primary_fee_scenario(),
        data_bundle_manifest=bundle_path,
        expected_bundle_version="d" * 64,
        expected_bundle_sha256=bundle["bundle_sha256"],
    )
    assert evaluator.data_bundle_version == "d" * 64
    assert evaluator.data_bundle_sha256 == bundle["bundle_sha256"]
    files["frozen_fold_artifact"].write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="artifact changed"):
        OfflineCandidateEvaluatorV3.from_bound_directory(
            project_root=project, dataset_root=dataset, artifact_root=project / "runs",
            fee_scenario=primary_fee_scenario(),
            data_bundle_manifest=bundle_path,
            expected_bundle_version="d" * 64,
            expected_bundle_sha256=bundle["bundle_sha256"],
        )
