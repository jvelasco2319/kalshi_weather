from pathlib import Path
from datetime import UTC, datetime, timedelta
import copy
import json
from types import SimpleNamespace

import pytest

from v5p.analysis import (
    V5PAnalysisError,
    freeze_outcome_blind_universe,
    reconstruct_frozen_leader,
    sample_label,
    verify_universe,
)
from v5p.controller import (
    V5PControllerError,
    _build_universe_rows,
    _load_config,
    _load_probability_readiness,
    start,
)
import v5p.controller as controller


ROOT = Path(__file__).resolve().parents[2]
FAKE_SHA = "0" * 64


def _row(day: int) -> dict:
    date = f"2026-06-{day:02d}"
    return {
        "climate_date": date,
        "event_ticker": f"KXHIGHLAX-26JUN{day:02d}",
        "decision_time_utc": "18:00",
        "probability_ready": True,
        "market_ready": True,
        "fee_rule_ready": True,
        "settlement_source_ready": True,
        "station_identity_screen": True,
        "execution_evidence_grade": "B",
        "settlement_evidence_grade": "BOUNDED_COMMON_NWS_LAX_SEMANTICS",
        "settlement_rule_revision_exact": False,
        "date_promotion_ready": False,
        "source_bindings": [
            {"path": f"safe/{date}.json", "bytes": 1, "sha256": FAKE_SHA}
        ],
        "exclusion_reasons": [],
    }


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "NO_PARTIAL_EVIDENCE_SAMPLE"),
        (1, "ANECDOTAL_PARTIAL_EVIDENCE"),
        (9, "ANECDOTAL_PARTIAL_EVIDENCE"),
        (10, "PRELIMINARY_PARTIAL_EVIDENCE"),
        (29, "PRELIMINARY_PARTIAL_EVIDENCE"),
        (30, "SUPPORTED_PARTIAL_EVIDENCE"),
    ],
)
def test_sample_labels_are_registered(count: int, expected: str) -> None:
    assert sample_label(count) == expected


def test_rolling_universe_has_no_provisional_holdout_or_tuning() -> None:
    value = freeze_outcome_blind_universe(
        [_row(day) for day in range(1, 7)],
        config_sha256=FAKE_SHA,
        acquisition_closed=False,
    )
    verify_universe(value)
    assert value["eligible_date_count"] == 6
    assert value["development_dates"] == []
    assert value["holdout_dates"] == []
    assert value["optimization_permitted"] is False
    assert {row["partition"] for row in value["records"]} == {"rolling_descriptive"}


def test_closed_universe_refreezes_chronological_70_30() -> None:
    value = freeze_outcome_blind_universe(
        [_row(day) for day in range(1, 11)],
        config_sha256=FAKE_SHA,
        acquisition_closed=True,
    )
    assert value["development_dates"] == [f"2026-06-{day:02d}" for day in range(1, 8)]
    assert value["holdout_dates"] == [f"2026-06-{day:02d}" for day in range(8, 11)]
    assert value["optimization_permitted"] is True
    assert value["holdout_access_authorized"] is False


def test_universe_rejects_outcome_bearing_fields() -> None:
    row = _row(1)
    row["outcome"] = "YES"
    with pytest.raises(V5PAnalysisError, match="fields differ"):
        freeze_outcome_blind_universe(
            [row], config_sha256=FAKE_SHA, acquisition_closed=False,
        )


@pytest.mark.parametrize(
    "path",
    [
        "data/sealed/v5p_holdout/labels.json",
        "data/protected-final/source.json",
        "safe/labels.json",
    ],
)
def test_compound_protected_source_paths_are_denied(path: str) -> None:
    with pytest.raises(V5PControllerError, match="protected storage"):
        controller._assert_safe_source_path(path)


@pytest.mark.parametrize("key", ["daily_outcome", "holdout_labels", "realized-profit"])
def test_compound_outcome_keys_are_denied(key: str) -> None:
    with pytest.raises(V5PControllerError, match="outcome-bearing field"):
        controller._assert_no_outcome_payload({key: 1})


def test_preview_verifier_rejects_added_outcome_payload() -> None:
    preview = json.loads(
        (ROOT / controller.SETTLEMENT_PREVIEW_PATH).read_text(encoding="utf-8")
    )
    tampered = copy.deepcopy(preview)
    tampered["eligible_records"][0]["daily_outcome"] = "YES"
    with pytest.raises(V5PControllerError, match="outcome-bearing field"):
        controller._verify_settlement_source(ROOT, tampered, final=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("settlement_rule_revision_exact", True),
        ("promotion_ready", True),
        ("rule_evidence_grade", "EXACT_REVISION"),
    ],
)
def test_preview_verifier_binds_grade_exactness_and_promotion(
    field: str, value: object,
) -> None:
    preview = json.loads(
        (ROOT / controller.SETTLEMENT_PREVIEW_PATH).read_text(encoding="utf-8")
    )
    tampered = copy.deepcopy(preview)
    tampered["eligible_records"][0][field] = value
    with pytest.raises(V5PControllerError, match="grade/exactness/promotion"):
        controller._verify_settlement_source(ROOT, tampered, final=False)


def test_preview_verifier_recomputes_top_level_promotion() -> None:
    preview = json.loads(
        (ROOT / controller.SETTLEMENT_PREVIEW_PATH).read_text(encoding="utf-8")
    )
    preview["promotion_ready"] = True
    with pytest.raises(V5PControllerError, match="top-level promotion"):
        controller._verify_settlement_source(ROOT, preview, final=False)


def test_exact_rule_cannot_carry_bounded_uncertainty() -> None:
    preview = json.loads(
        (ROOT / controller.SETTLEMENT_PREVIEW_PATH).read_text(encoding="utf-8")
    )
    row = preview["eligible_records"][0]
    row["rule_evidence_grade"] = "EXACT_REVISION"
    row["settlement_rule_revision_exact"] = True
    row["promotion_ready"] = True
    nested = row["settlement_rule_bound"]
    nested.pop("ordinary_winning_contract_payout_usd")
    nested.pop("exchange_settlement_fee_usd")
    nested["rule_evidence_grade"] = "EXACT_REVISION"
    nested["exact_event_rule_revision_bound"] = True
    nested["promotion_ready"] = True
    nested["rule_sha256"] = "a" * 64
    # Both row and nested binding deliberately retain bounded uncertainty.
    with pytest.raises(V5PControllerError, match="grade/exactness/promotion"):
        controller._verify_settlement_source(ROOT, preview, final=False)


def test_real_safe_preview_and_frozen_leader_are_consumable_without_refit() -> None:
    readiness = _load_probability_readiness(ROOT)
    rows = _build_universe_rows(ROOT, readiness)
    config, _ = _load_config(ROOT)
    leader = reconstruct_frozen_leader(ROOT, config, readiness)
    assert len(rows) >= 1
    assert all(row["settlement_source_ready"] for row in rows)
    assert leader.binding["candidate_id"] == config["frozen_candidate"]["candidate_id"]
    assert leader.binding["refit_performed"] is False
    assert leader.binding["protected_confirmation_labels_read"] is False


def test_one_shot_start_cannot_begin_the_clock(tmp_path: Path) -> None:
    with pytest.raises(V5PControllerError, match="supervisor lease"):
        start(tmp_path, "test-campaign")


def test_process_lease_is_exclusive_and_stale_identity_is_replaced(tmp_path: Path) -> None:
    output = tmp_path / "analysis"
    output.mkdir()
    first = controller._acquire_process_lease(output, "test")
    with pytest.raises(V5PControllerError, match="identity-bound"):
        controller._acquire_process_lease(output, "test")
    controller._release_process_lease(output, first)
    stale = controller.hash_bound({
        "version": "klax-v5p-analysis-process-lease-v1",
        "campaign_id": "test",
        "pid": 2_000_000_000,
        "process_start_identity": "windows-filetime-stale",
        "command_identity_sha256": FAKE_SHA,
        "nonce": "stale",
    }, "lease_sha256")
    controller.atomic_write_json(output / "controller-process.lock", stale)
    replacement = controller._acquire_process_lease(output, "test")
    assert replacement["lease_sha256"] != stale["lease_sha256"]
    assert list(output.glob("controller-process.lock.stale.*"))
    controller._release_process_lease(output, replacement)


def test_supervisor_start_and_resume_preserve_clock_then_refreeze_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "runs/campaigns_v5p/test/analysis"
    readiness_file = tmp_path / controller.PROBABILITY_READINESS_PATH
    readiness_file.parent.mkdir(parents=True)
    readiness_file.write_text("{}\n", encoding="utf-8")
    config_record = {"path": "config.json", "bytes": 1, "sha256": FAKE_SHA}
    open_readiness = {"readiness_sha256": "1" * 64}
    closed_readiness = {"readiness_sha256": "2" * 64}
    leader = SimpleNamespace(binding={
        "binding_sha256": "3" * 64,
        "candidate_id": "frozen-test",
        "refit_performed": False,
    })
    monkeypatch.setattr(controller, "_campaign", lambda root, campaign_id: ("test", output))
    monkeypatch.setattr(controller, "_load_config", lambda root: ({}, config_record))
    monkeypatch.setattr(controller, "_load_probability_readiness", lambda root: open_readiness)
    monkeypatch.setattr(controller, "_acquisition_closed", lambda value: value is closed_readiness)
    monkeypatch.setattr(controller, "reconstruct_frozen_leader", lambda *args: leader)
    monkeypatch.setattr(controller, "_current_universe_rows", lambda *args: [_row(day) for day in range(1, 7)])
    monkeypatch.setattr(controller, "_write_colonies", lambda *args: {})
    launched = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    output.mkdir(parents=True, exist_ok=True)
    lease = controller._acquire_process_lease(output, "test")
    state = controller.start(tmp_path, "test", now=launched, supervisor_lease=lease)
    assert state["phase"] == "rolling"
    assert state["development_date_count"] == 0
    assert state["holdout_date_count"] == 0
    assert state["analysis_absolute_deadline_utc"] == "2026-09-28T00:00:00Z"
    with pytest.raises(V5PControllerError, match="mutation requires"):
        controller.resume(tmp_path, "test", now=launched + timedelta(seconds=1))

    charged = {key: value for key, value in state.items() if key != "state_sha256"}
    charged["elapsed_wall_seconds_charged"] = 1000.0
    charged["updated_at_utc"] = "2026-09-27T12:01:00Z"
    controller._write_state(output, charged)
    rolling = controller.resume(
        tmp_path, "test", now=launched + timedelta(minutes=5),
        supervisor_lease=lease,
    )
    assert rolling["elapsed_wall_seconds_charged"] == 1000.0

    monkeypatch.setattr(controller, "_load_probability_readiness", lambda root: closed_readiness)
    monkeypatch.setattr(controller, "_current_universe_rows", lambda *args: [_row(day) for day in range(1, 11)])
    resumed = controller.resume(
        tmp_path, "test", now=launched + timedelta(minutes=6),
        supervisor_lease=lease,
    )
    assert resumed["phase"] == "development"
    assert resumed["development_date_count"] == 7
    assert resumed["holdout_date_count"] == 3
    assert resumed["holdout_status"] == "SEALED_STRATEGY_NOT_FROZEN"
    assert resumed["analysis_absolute_deadline_utc"] == state["analysis_absolute_deadline_utc"]
    with pytest.raises(V5PControllerError, match="cannot move backward"):
        controller.resume(
            tmp_path, "test", now=launched + timedelta(minutes=5),
            supervisor_lease=lease,
        )
    controller._release_process_lease(output, lease)
