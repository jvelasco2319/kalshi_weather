from __future__ import annotations

from datetime import date, datetime, time, timezone
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from klax_lab import candidate_model_v3, evaluator_v3
from klax_lab.candidate_model_v3 import CandidatePrediction
from klax_lab.final_evaluator_v3 import (
    FINAL_REPORT_VERSION, FINAL_TICKET_VERSION, FrozenProtectedFinalInputsV3,
    V3FinalEvaluationRefusal, _claim_ticket, _score_final, _summary,
    _source_hashes, _write_canonical, load_protected_final_bundle_v3,
    run_protected_final_evaluation_v3,
    verify_protected_final_evaluation_v3,
)
from klax_lab.provenance import sha256_file
from klax_lab.research_plan_v3 import make_plan_v3


UTC = timezone.utc
BOUNDS = ((None, 64), (65, 67), (68, 70), (71, 73), (74, 76), (77, None))


def _plan():
    return make_plan_v3(
        colony="execution_abstention", stage="economic_simulation",
        data_bundle_version="fixture-bundle", data_bundle_sha256="b" * 64,
        forecast_source_set="gefs_summary", feature_set="temperature_only",
        regime_model="pooled", minimum_regime_training_days=30,
        probability_family="gaussian_blend",
        calibration_operator="gaussian_integer_interval",
        market_residual_model="none", abstention_operator="fixed_uncertainty_buffer",
        decision_time_utc="15:00", entry_threshold=.10, sides="YES",
        uncertainty_buffer=0.0, maximum_interval_width_f=12.0,
        minimum_candle_volume=10, maximum_price_age_minutes=5,
        maximum_spread_cents=5, entry_price_floor_cents=5,
        entry_price_ceiling_cents=90,
    )


def _event(day: date) -> tuple[dict, dict]:
    event = f"KXHIGHLAX-{day:%y%b%d}".upper()
    contracts, outcomes = [], []
    stamp = int(datetime.combine(day, time(15), UTC).timestamp())
    high = 72
    for index, (lower, upper) in enumerate(BOUNDS):
        ticker = f"{event}-B{index}"
        contracts.append({
            "ticker": ticker,
            "interval": {"integer_lower_f": lower, "integer_upper_f": upper},
            "market": {"latest_completed_candle": {
                "ticker": ticker, "end_period_ts": stamp, "period_minutes": 1,
                "yes_bid_close": "0.04", "yes_ask_close": "0.05",
                "volume_contracts": "50", "evidence_grade": "B_aggregated_quote",
                "historical_depth_available": False,
                "hypothetical_fill_supported": False,
                "source_sha256": f"{index + 1:x}" * 64,
                "source_endpoint_type": "historical_market_candlesticks_1m",
            }},
        })
        outcomes.append({
            "ticker": ticker,
            "yes_outcome": int((lower is None or high >= lower)
                               and (upper is None or high <= upper)),
        })
    feature = {
        "schema_version": 1, "partition": "protected_final",
        "data_role": "protected_final_evaluation", "climate_date": day.isoformat(),
        "decision_time_utc": "15:00",
        "decision_at": datetime.combine(day, time(15), UTC).isoformat(),
        "event_ticker": event, "contracts": contracts, "observations": [],
        "forecasts": [{
            "model": "gefs", "member_id": "avg", "field_id": "temperature_2m",
            "initialized_at": datetime.combine(day, time(6), UTC).isoformat(),
            "available_at": datetime.combine(day, time(9), UTC).isoformat(),
            "valid_at": datetime.combine(day, time(18), UTC).isoformat(),
            "value": 72.0, "is_missing": False, "as_of_validated": True,
            "source_sha256": "b" * 64,
        }], "as_of_join_validated": True,
        "contains_settlement_label": False,
    }
    label = {
        "schema_version": 1, "partition": "protected_final",
        "data_role": "protected_final_evaluation_label",
        "climate_date": day.isoformat(), "reported_high_f": high,
        "event_ticker": event, "contracts": outcomes, "source_sha256": "a" * 64,
    }
    return feature, label


class FrozenModel:
    identity = "m" * 64

    def __init__(self, plan_sha256: str):
        self.plan_sha256 = plan_sha256
        self.calls = 0

    def predict(self, feature, bounds, *, market_probabilities=None):
        self.calls += 1
        probabilities = (.01, .01, .01, .94, .02, .01)
        return CandidatePrediction(
            probabilities, probabilities, "pooled", False, (), False, 10.0)


def _release(tmp_path: Path):
    plan = _plan()
    return SimpleNamespace(
        root=tmp_path, campaign_id="fixture-campaign",
        record=SimpleNamespace(candidate_id="v3-candidate-fixture", plan=plan),
        fitted_model=FrozenModel(plan.identity),
        reference_state={
            "temperature_counts": [{"temperature_f": 72, "count": 90}],
            "training_rows": 90,
        },
        final_authorization_sha256="f" * 64,
    )


def test_invalid_development_release_never_enters_protected_stage(
    tmp_path: Path, monkeypatch,
) -> None:
    calls = {"settlement": 0, "bundle": 0}

    def settlement(*args, **kwargs):
        calls["settlement"] += 1
        raise AssertionError("protected settlement builder was reached")

    def bundle(*args, **kwargs):
        calls["bundle"] += 1
        raise AssertionError("protected bundle loader was reached")

    monkeypatch.setattr("klax_lab.final_evaluator_v3.load_protected_final_bundle_v3", bundle)
    with pytest.raises(Exception):
        run_protected_final_evaluation_v3(
            tmp_path, "runs/campaigns_v3/missing", "data/manifests/missing.json",
            "runs/missing-ticket.json", "data/protected_final/manifest.json",
            settlement_builder=settlement)
    assert calls == {"settlement": 0, "bundle": 0}
    assert not (tmp_path / "runs/protected_final_v3").exists()


def test_atomic_claim_is_one_use_and_ambiguous_claim_refuses_reuse(tmp_path: Path) -> None:
    release = _release(tmp_path)
    output = tmp_path / "runs" / "protected_final_v3" / release.campaign_id
    path = _claim_ticket(release, output)
    saved = json.loads(path.read_text())
    assert saved["ticket_version"] == FINAL_TICKET_VERSION
    assert saved["status"] == "CLAIMED" and saved["evaluations_remaining"] == 0
    with pytest.raises(V3FinalEvaluationRefusal, match="already claimed"):
        _claim_ticket(release, output)


def test_failure_after_claim_is_terminal_and_does_not_retry_protected_read(
    tmp_path: Path, monkeypatch,
) -> None:
    release = _release(tmp_path)
    calls = {"builder": 0}
    monkeypatch.setattr(
        "klax_lab.final_evaluator_v3.validate_v3_final_release",
        lambda *args, **kwargs: release)

    def fail_builder(*args, **kwargs):
        calls["builder"] += 1
        raise RuntimeError("synthetic post-claim failure")

    arguments = (
        tmp_path, "runs/campaigns_v3/fixture-campaign", "readiness.json",
        "ticket.json", "data/protected_final/manifest.json",
    )
    with pytest.raises(RuntimeError, match="post-claim"):
        run_protected_final_evaluation_v3(*arguments, settlement_builder=fail_builder)
    state = json.loads((tmp_path / "runs/protected_final_v3/fixture-campaign/ticket-state.json").read_text())
    assert state["status"] == "FAILED"
    assert state["failure_is_terminal_and_not_retryable"] is True
    with pytest.raises(V3FinalEvaluationRefusal, match="already claimed"):
        run_protected_final_evaluation_v3(*arguments, settlement_builder=fail_builder)
    assert calls["builder"] == 1


def test_final_scoring_uses_only_frozen_model_and_never_calls_fit_or_selection(
    tmp_path: Path, monkeypatch,
) -> None:
    release = _release(tmp_path)
    feature, label = _event(date(2025, 7, 1))
    inputs = FrozenProtectedFinalInputsV3(
        "d" * 64, "i" * 64, {}, (feature,), (label,), ())

    def forbidden(*args, **kwargs):
        raise AssertionError("fit or calibration routine was called")

    monkeypatch.setattr(candidate_model_v3, "fit_candidate_model_v3", forbidden)
    monkeypatch.setattr(evaluator_v3, "fit_candidate_model_v3", forbidden)
    predictions, ledger, metrics = _score_final(release, inputs)
    assert release.fitted_model.calls == 1
    assert ledger["fitted_model_sha256"] == release.fitted_model.identity
    assert len(predictions["rows"]) == 1
    assert metrics["forecast_scores"]["scored_events"] == 1
    assert metrics["historical_assumed_fill"]["trade_count"] == 1
    assert "uncertainty" in metrics and "bootstrap_uncertainty" not in metrics["historical_assumed_fill"]
    source = inspect.getsource(_score_final)
    assert "fit_candidate_model_v3" not in source
    assert "fit_" not in source


def test_protected_bundle_requires_exact_settlement_and_full_interval_coverage(
    tmp_path: Path,
) -> None:
    release = _release(tmp_path)
    protected = tmp_path / "data/protected_final/frozen-v3"
    release.campaign_authorization = SimpleNamespace(
        protected_final_roots=((tmp_path / "data/protected_final").resolve(),))
    feature, label = _event(date(2025, 7, 1))
    exclusions = []
    day = date(2025, 7, 2)
    while day <= date(2025, 12, 31):
        exclusions.append({
            "climate_date": day.isoformat(), "reason": "fixture_exclusion",
            "source_sha256": "e" * 64,
        })
        day = date.fromordinal(day.toordinal() + 1)
    protected.mkdir(parents=True)
    features_path = protected / "features.jsonl"
    labels_path = protected / "labels.jsonl"
    exclusions_path = protected / "exclusions.json"
    features_path.write_text(json.dumps(feature, sort_keys=True) + "\n", encoding="utf-8")
    labels_path.write_text(json.dumps(label, sort_keys=True) + "\n", encoding="utf-8")
    exclusions_path.write_text(json.dumps(exclusions, sort_keys=True), encoding="utf-8")
    artifacts = {
        "features": {"path": features_path.relative_to(tmp_path).as_posix(),
                     "sha256": sha256_file(features_path), "bytes": features_path.stat().st_size,
                     "row_count": 1},
        "labels": {"path": labels_path.relative_to(tmp_path).as_posix(),
                   "sha256": sha256_file(labels_path), "bytes": labels_path.stat().st_size,
                   "row_count": 1},
        "exclusions": {"path": exclusions_path.relative_to(tmp_path).as_posix(),
                       "sha256": sha256_file(exclusions_path),
                       "bytes": exclusions_path.stat().st_size,
                       "row_count": len(exclusions)},
    }
    body = {
        "schema_version": "klax-v3-protected-final-bundle-v1",
        "scope": "protected_final_2025_07_01_through_2025_12_31",
        "partition": "protected_final", "start_inclusive": "2025-07-01",
        "end_inclusive": "2025-12-31", "network_used": False,
        "historical_only": True, "as_of_join_validated": True,
        "decision_times_utc": ["15:00"], "artifacts": artifacts,
        "source_object_sha256s": sorted(_source_hashes([[feature], [label], exclusions])),
    }
    manifest = {**body, "dataset_id": evaluator_v3.canonical_hash(body)}
    manifest_path = protected / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    rebuilt = tmp_path / "runs/final/rebuilt-settlement/protected_final/labels"
    rebuilt.mkdir(parents=True)
    target_path = rebuilt / "settlement_targets.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "climate_date": "2025-07-01", "reported_high_f": 72,
        "event_ticker": feature["event_ticker"], "source_sha256": "a" * 64,
    }]), target_path)
    rebuilt_exclusions_path = rebuilt / "settlement_target_exclusions.json"
    rebuilt_exclusions_path.write_text(json.dumps([
        {"climate_date": row["climate_date"], "reason": row["reason"]}
        for row in exclusions]), encoding="utf-8")
    settlement = {"partitions": {"protected_final": {
        "target_path": target_path.relative_to(tmp_path).as_posix(),
        "target_sha256": sha256_file(target_path),
        "exclusions_path": rebuilt_exclusions_path.relative_to(tmp_path).as_posix(),
        "exclusions_sha256": sha256_file(rebuilt_exclusions_path),
    }}}
    loaded = load_protected_final_bundle_v3(release, manifest_path, settlement)
    assert loaded.dataset_id == manifest["dataset_id"]
    assert len(loaded.features) == 1 and len(loaded.exclusions) == 183
    changed = json.loads(labels_path.read_text())
    changed["reported_high_f"] = 73
    labels_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(V3FinalEvaluationRefusal, match="artifact changed"):
        load_protected_final_bundle_v3(release, manifest_path, settlement)


def _verified_output(tmp_path: Path) -> Path:
    output = tmp_path / "final"
    core = output / "artifacts"
    predictions = {"rows": [{"probabilities": [0.25, 0.75]}],
                   "contains_final_labels": False}
    ledger = {
        "dataset_id": "d" * 64, "research_plan_sha256": "p" * 64,
        "fitted_model_sha256": "m" * 64, "decisions": [], "settlements": [],
        "historical_assumed_fill_only": True, "actual_orders_placed": False,
    }
    report = {
        "report_version": FINAL_REPORT_VERSION, "campaign_id": "campaign",
        "candidate_id": "candidate", "research_plan_sha256": "p" * 64,
        "fitted_model_sha256": "m" * 64,
        "final_authorization_sha256": "a" * 64,
        "protected_dataset_id": "d" * 64, "protected_final_evaluated": True,
        "protected_bundle_manifest_sha256": "b" * 64,
        "rebuilt_settlement_manifest_sha256": "c" * 64,
        "protected_final_evaluation_count": 1, "forecast_refit_performed": False,
        "calibration_refit_performed": False, "model_selection_performed": False,
        "threshold_tuning_performed": False, "network_used": False,
        "actual_orders_placed": False, "actual_account_gains_measured": False,
        "historical_assumed_fill": _summary([]),
        "cost_stress": evaluator_v3._stress_summary([]),
        "uncertainty": {
            "resamples": 10_000, "seed": 20260926,
            "unit": "independent_settlement_day", "one_sided_lower_95": None,
            "central_90_interval": None, "undefined_resamples": 10_000,
            "includes_no_trade_days": True,
        },
    }
    hashes = {
        "predictions.json": _write_canonical(core / "predictions.json", predictions),
        "ledger.json": _write_canonical(core / "ledger.json", ledger),
        "summary.json": _write_canonical(core / "summary.json", report),
    }
    (core / "report.md").write_text("fixture\n", encoding="utf-8")
    hashes["report.md"] = sha256_file(core / "report.md")
    _write_canonical(output / "artifact-manifest.json", {
        "manifest_version": "klax-v3-protected-final-artifacts-v1",
        "campaign_id": "campaign", "final_authorization_sha256": "a" * 64,
        "protected_dataset_id": "d" * 64,
        "protected_bundle_manifest_sha256": "b" * 64,
        "rebuilt_settlement_manifest_sha256": "c" * 64,
        "files": hashes,
    })
    return output


def test_independent_verifier_rejects_hash_tampering(tmp_path: Path) -> None:
    output = _verified_output(tmp_path)
    checked = verify_protected_final_evaluation_v3(output)
    assert checked["status"] == "PASS"
    ledger = output / "artifacts/ledger.json"
    value = json.loads(ledger.read_text())
    value["dataset_id"] = "0" * 64
    ledger.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(V3FinalEvaluationRefusal, match="artifact changed"):
        verify_protected_final_evaluation_v3(output)
