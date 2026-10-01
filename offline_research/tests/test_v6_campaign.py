"""Synthetic V6 controller tests; no historical data or protected labels."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from v5b.campaign import digest
from v6 import campaign as c
from v6 import evaluation, research_specs


def good_result(spec):
    dates = [f"2030-01-{day:02}" for day in range(1, 32)] + [f"2030-02-{day:02}" for day in range(1, 14)]
    trades = [{"climate_date": day, "market_ticker": "T" + day, "contract_side": "NO",
        "quantity": 1, "entry_price_cents": 40, "fee_dollars": .02,
        "execution_evidence_grade": "B_PLUS"} for day in dates[:35]]
    by_date = {t["climate_date"]: t for t in trades}
    ledger = [({**by_date[day], "abstention": False} if day in by_date else
               {"climate_date": day, "abstention": True}) for day in dates]
    return {"selected_days": 35, "aggregate_realized_net_return": .2,
        "mean_expected_net_return": .2, "positive_fold_count": 4,
        "worst_nonempty_fold_return": -.05, "evidence_quality_score": .8,
        "multiclass_brier": .2, "baseline_multiclass_brier": .3,
        "adverse_stress": {"2": {"aggregate_realized_net_return": .1}},
        "best_day_removed_return": .1, "behavioral_ledger": ledger,
        "behavioral_fingerprint": digest(ledger), "trades": trades, "parameters": spec,
        "common_scoring_dates": dates,
        "precursor": {"method_forecast_count": 44, "policy_chain_multiclass_brier": .2,
            "reference_multiclass_brier": .3},
        "calibration": {"minimum_residual_count": 10}}


@pytest.fixture
def project(tmp_path, monkeypatch):
    clock = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(c, "now", lambda: clock[0])
    monkeypatch.setattr(c, "stamp", lambda: clock[0].isoformat())
    monkeypatch.setattr(evaluation, "load_development", lambda root: {"input_bindings": set()})
    monkeypatch.setattr(evaluation, "evaluate_candidate", lambda context, spec: good_result(spec))
    monkeypatch.setattr(evaluation, "reference_replay", lambda context: {"fixture": True})
    def evaluate(root, directory, state, spec):
        from v5b.campaign import seal, write
        candidate = research_specs.fingerprint(spec); attempt_id = candidate[:16] + "-fixture"
        attempt = seal({"attempt_id": attempt_id, "candidate_id": candidate,
            "parent_process_id": 1, "worker_process_id": 2, "started_at": clock[0].isoformat(),
            "deadline": state["deadline"], "private_result_path": "fixture",
            "worker_watchdog": "parent_liveness_and_absolute_deadline"})
        write(directory / "worker-attempts" / f"{attempt_id}.json", attempt)
        return good_result(spec), attempt
    monkeypatch.setattr(c, "_evaluate_with_deadline", evaluate)
    config = json.loads(Path(c.__file__).parents[1].joinpath("configs/v6_campaign.json").read_text())
    config["binding_files"] = []
    for name in (c.CONFIG, "data/manifests/v6_readiness.json", "scripts/control_v6_campaign.ps1",
                 "scripts/verify_v6_artifacts.py", "scripts/report_v6_campaign.py"):
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True)
        if name == c.CONFIG:
            path.write_text(json.dumps(config))
        elif name == "data/manifests/v6_readiness.json":
            readiness = {"status": "READY_OFFLINE_DEVELOPMENT_ONLY", "development_date_count": 64,
                "common_scoring_date_count": 44, "allow_network": False, "allow_orders": False,
                "allow_protected_labels": False, "bindings": {"data/manifests/v5a_holdout_seal.json": ""}}
            holdout = tmp_path / "data/manifests/v5a_holdout_seal.json"; holdout.write_text("fixture")
            from v5b.campaign import filehash, seal
            readiness["bindings"]["data/manifests/v5a_holdout_seal.json"] = filehash(holdout)
            path.write_text(json.dumps(seal(readiness)))
        else:
            path.write_text("fixture")
    return tmp_path, clock


def review_packet(directory, terminal=False):
    records = c.records_at(directory)
    groups = list(research_specs.COLONIES)
    reviews = []
    for group in groups:
        ids = [key for key, value in records.items() if value["hypothesis"]["colony"] != group]
        reviews.append({"colony": group, "reviewer": "agent-" + group,
            "candidate_ids": ids, "findings": ["Independent synthetic review of registered evidence."]})
    return {"epoch": c.checked(directory / "recovery-state.json")["epoch"],
        "terminal": terminal, "reviews": reviews, "proposals": []}


def commit_review(root, terminal=False):
    directory = c.locate(root); packet = review_packet(directory, terminal)
    path = root / ("terminal-input.json" if terminal else f"review-{packet['epoch']}.json")
    path.write_text(json.dumps(packet)); return c.review(root, path)


def test_exact_budget_and_group_guards():
    config = json.loads(Path(c.__file__).parents[1].joinpath("configs/v6_campaign.json").read_text())
    assert c.validate_config(config)["wall_seconds"] == 14400
    config["minimum_elapsed_before_scientific_finish_seconds"] = 1
    with pytest.raises(ValueError, match="budget differs"):
        c.validate_config(config)


def test_worker_watchdog_stops_on_parent_death_or_deadline():
    class Parent:
        def __init__(self, alive): self.alive = alive
        def is_alive(self): return self.alive
    deadline = datetime(2030, 1, 1, 1, tzinfo=timezone.utc).isoformat()
    assert c._worker_exit_code(Parent(True), deadline, datetime(2030, 1, 1, tzinfo=timezone.utc)) is None
    assert c._worker_exit_code(Parent(False), deadline, datetime(2030, 1, 1, tzinfo=timezone.utc)) == 125
    assert c._worker_exit_code(Parent(True), deadline, datetime(2030, 1, 1, 1, tzinfo=timezone.utc)) == 124


def test_full_finite_catalog_can_finish_early_only_after_terminal_review(project):
    root, clock = project
    state = c.register(root)
    reg = c.checked(c.locate(root) / "registration.json")
    assert len(reg["catalog_fingerprints"]) == 28
    assert datetime.fromisoformat(reg["not_before"]) > clock[0]
    expected_counts = [4, 13, 22, 28]
    for index, expected in enumerate(expected_counts, 1):
        state = c.epoch(root)
        assert state["unique_candidates"] == expected
        if index < 4:
            assert state["status"] == "AWAITING_AGENT_REVIEW"
            state = commit_review(root)
            assert state["status"] == "READY"
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"
    assert c.checked(c.locate(root) / "exhaustion-certificate-draft.json")["complete"] is False
    with pytest.raises(ValueError, match="terminal actual review"):
        c.freeze(root)
    state = commit_review(root, terminal=True)
    assert state["status"] == "COMPLETE" and state["completed_at"] < state["not_before"]
    certificate = c.checked(c.locate(root) / "exhaustion-certificate.json")
    assert certificate["complete"] is True and len(certificate["evaluated_fingerprints"]) == 28
    frozen = c.freeze(root)
    assert frozen["holdout_access_authorized"] is False
    assert len(frozen["ranked_candidate_ids"]) <= 3


def test_precursors_are_never_promoted(project):
    root, _ = project; c.register(root); state = c.epoch(root)
    records = c.records_at(c.locate(root))
    assert len(records) == 4 and state["status"] == "AWAITING_AGENT_REVIEW"
    assert all("precursor_only" in item["gate_failures"] and not item["development_screen_passed"]
               for item in records.values())


def test_candidate_timestamps_and_behavioral_equivalence_are_bound(project):
    root, _ = project; c.register(root); c.epoch(root)
    records = c.records_at(c.locate(root))
    assert all(item["evaluated_started_at"] <= item["evaluated_completed_at"] for item in records.values())
    assert len({item["behavioral_fingerprint"] for item in records.values()}) == 1
    ranking = c.checked(c.locate(root) / "method-ranking.json")
    assert ranking["unique_behavior_count"] == 1


def test_failed_precursor_objectively_blocks_registered_descendants(project, monkeypatch):
    root, _ = project
    def score(root_arg, directory, state, spec):
        result = good_result(spec)
        if spec["stage"] == "precursor" and spec["method_id"] == "gefs_spread_equal_blend":
            result["precursor"]["policy_chain_multiclass_brier"] = .4
        from v5b.campaign import seal, write
        candidate = research_specs.fingerprint(spec); attempt_id = candidate[:16] + "-blocked"
        attempt = seal({"attempt_id": attempt_id, "candidate_id": candidate,
            "parent_process_id": 1, "worker_process_id": 2, "started_at": state["created_at"],
            "deadline": state["deadline"], "private_result_path": "fixture",
            "worker_watchdog": "parent_liveness_and_absolute_deadline"})
        write(directory / "worker-attempts" / f"{attempt_id}.json", attempt)
        return result, attempt
    monkeypatch.setattr(c, "_evaluate_with_deadline", score)
    c.register(root)
    state = c.epoch(root)
    assert state["unique_candidates"] == 4
    commit_review(root)
    state = c.epoch(root); assert state["unique_candidates"] == 13
    commit_review(root)
    state = c.epoch(root)
    assert state["unique_candidates"] == 22 and state["status"] == "AWAITING_TERMINAL_REVIEW"
    draft = c.checked(c.locate(root) / "exhaustion-certificate-draft.json")
    assert len(draft["blocked"]) == 6 and not draft["missing"]
    assert {x["prerequisite_id"] for x in draft["blocked"].values()} == {"precursor_passed"}
