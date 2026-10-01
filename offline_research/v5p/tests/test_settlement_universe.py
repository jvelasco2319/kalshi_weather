from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import socket

import pytest

import v5p.settlement_universe as settlement
from v5p.settlement_universe import (
    V5PUniverseError,
    build_outcome_blind_universe,
    canonical_hash,
    verify_frozen_workspace,
    write_frozen_artifacts,
)


def _event(day: date) -> str:
    return "KXHIGHLAX-" + day.strftime("%y%b%d").upper()


def _contracts(day: date) -> list[dict]:
    event = _event(day)
    written = f"{day.strftime('%B')} {day.day:02d}, {day.year}"
    common = (
        "If the highest temperature recorded in Los Angeles Airport, CA for "
        f"{written} as reported by the National Weather Service's "
        "Climatological Report (Daily), is {condition}, then the market resolves to Yes."
    )
    return [
        {
            "climate_date": day.isoformat(), "event_ticker": event,
            "ticker": event + "-T70", "market_type": "binary",
            "station_identity_screen": True, "status": "finalized", "strike_type": "less",
            "floor_strike": None, "cap_strike": 70,
            "rules_primary": common.format(condition="less than 70°"),
        },
        {
            "climate_date": day.isoformat(), "event_ticker": event,
            "ticker": event + "-B70.5", "market_type": "binary",
            "station_identity_screen": True, "status": "finalized", "strike_type": "between",
            "floor_strike": 70, "cap_strike": 71,
            "rules_primary": common.format(condition="between 70-71°"),
        },
        {
            "climate_date": day.isoformat(), "event_ticker": event,
            "ticker": event + "-T71", "market_type": "binary",
            "station_identity_screen": True, "status": "finalized", "strike_type": "greater",
            "floor_strike": 71, "cap_strike": None,
            "rules_primary": common.format(condition="greater than 71°"),
        },
    ]


def _fixture(first: date = date(2025, 7, 8), count: int = 10,
             *, acquisition_status: str = "COMPLETE", exact_rule: bool = False):
    days = [first + timedelta(days=offset) for offset in range(count)]
    metadata = {"contracts": [row for day in days for row in _contracts(day)]}
    candles = [{
        "status": "complete",
        "contracts": [
            {"climate_date": day.isoformat(), "ticker": _event(day) + "-B70.5",
             "rows": 10, "status": "downloaded"}
            for day in days
        ],
    }]
    weather = [{
        "status": "NORMALIZED_FEATURES_ONLY", "climate_date": day.isoformat(),
        "decision_time_utc": "18:00", "coverage": {
            "hrrr": {"complete": True}, "gefs": {"complete": True},
        },
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False, "network_used": False,
    } for day in days]
    climate = {
        "status": "downloaded", "product_count": count,
        "start_inclusive": first.isoformat(),
        "end_exclusive": (days[-1] + timedelta(days=1)).isoformat(),
    }
    settlement = {
        "ordinary_winning_contract_payout_usd": 1,
        "exchange_settlement_fee_usd": 0,
        "historical_source_observed_for_all_604_bounded_events": "NWS LAX daily climate report",
        "common_threshold_semantics": {
            "above": "strictly greater than", "below": "strictly less than",
            "between": "inclusive endpoints",
        },
        "semantic_equivalence_research_usable": True,
        "exact_event_rule_revision_bound": exact_rule,
    }
    evaluator = {
        "schema_version": "klax-v5p-economics-evaluator-bounds-v1",
        "fee_periods": [{
            "date_start": "2025-07-08", "date_end": "2026-08-31",
            "taker_multiplier": 1, "maker_multiplier": None,
            "rounding": "ceil total order fee to next cent",
            "evaluator_action": "ALLOW_TAKER_DIRECT_ONLY_WITH_FILL_EVIDENCE",
            "exactness": "taker_schedule_exact_at_date_granularity",
        }],
        "settlement_binding": settlement,
    }
    evaluator["self_sha256"] = canonical_hash(evaluator)
    bindings = {
        "weather_by_date": {day.isoformat(): {"sha256": f"w{offset:063d}"}
                            for offset, day in enumerate(days)},
        "candle_by_date": {day.isoformat(): {"sha256": f"c{offset:063d}"}
                           for offset, day in enumerate(days)},
        "clilax_envelope": {"sha256": "a" * 64},
        "economics_evaluator": {"sha256": "b" * 64},
    }
    return {
        "acquisition": {"status": acquisition_status}, "metadata": metadata,
        "candle_manifests": candles, "weather_manifests": weather,
        "climate_manifest": climate, "evaluator_bounds": evaluator,
        "safe_bindings": bindings,
    }


def _build(values: dict, *, require_closed: bool = True):
    return build_outcome_blind_universe(
        **values, require_acquisition_closed=require_closed,
    )


def test_bounded_rule_evidence_permits_v5p_analysis_but_not_promotion() -> None:
    universe, normalized = _build(_fixture())
    assert universe["eligible_date_count"] == 10
    assert universe["partial_analysis_ready"] is True
    assert universe["promotion_ready"] is False
    assert normalized["protected_confirmation_labels_read"] is False
    assert all(row["rule_evidence_grade"] == "BOUNDED_COMMON_NWS_LAX_SEMANTICS"
               for row in universe["eligible_records"])
    assert all(row["settlement_rule_revision_exact"] is False
               and row["promotion_ready"] is False for row in universe["eligible_records"])


def test_chronological_split_is_exact_disjoint_and_exhaustive() -> None:
    universe, _ = _build(_fixture())
    dates = universe["eligible_dates"]
    split = universe["split"]
    assert split["development_count"] == 7
    assert split["holdout_count"] == 3
    assert split["development_dates"] == dates[:7]
    assert split["holdout_dates"] == dates[7:]
    assert split["development_dates"] + split["holdout_dates"] == dates
    assert set(split["development_dates"]).isdisjoint(split["holdout_dates"])


def test_outcome_leakage_and_open_acquisition_are_denied() -> None:
    values = _fixture()
    values["metadata"]["contracts"][0]["yes_outcome"] = 1
    with pytest.raises(V5PUniverseError, match="outcome-bearing key denied"):
        _build(values)
    open_values = _fixture(acquisition_status="RUNNING")
    with pytest.raises(V5PUniverseError, match="acquisition closes"):
        _build(open_values)
    preview, _ = _build(open_values, require_closed=False)
    assert preview["status"] == "PROVISIONAL_ACQUISITION_OPEN"
    assert preview["labels"]["outcomes_opened"] is False


@pytest.mark.parametrize("field", ["daily_outcome", "holdout_labels"])
def test_compound_outcome_and_holdout_keys_are_denied(field: str) -> None:
    values = _fixture()
    values["metadata"]["contracts"][0][field] = False
    with pytest.raises(V5PUniverseError, match="outcome-bearing key denied"):
        _build(values)


@pytest.mark.parametrize(
    "relative",
    [
        "data/sealed/v5p_holdout/labels.json",
        "data/protected-final/daily.json",
        "data/protected_final/daily.json",
    ],
)
def test_compound_protected_paths_are_denied(tmp_path: Path, relative: str) -> None:
    with pytest.raises(V5PUniverseError, match="outcome or protected path denied"):
        settlement._safe_relative(tmp_path, tmp_path / relative)


def test_unreviewed_safe_source_schema_growth_is_denied() -> None:
    values = _fixture()
    values["metadata"]["unexpected_payload"] = "opaque"
    with pytest.raises(V5PUniverseError, match="unexpected fields"):
        _build(values)


def test_july_first_through_seventh_are_excluded() -> None:
    universe, _ = _build(_fixture(date(2025, 7, 1), 10))
    assert universe["eligible_dates"] == ["2025-07-08", "2025-07-09", "2025-07-10"]
    early = universe["exclusions"][:7]
    assert all("FEE_2025_07_01_TO_07_07_EXCLUDED" in row["codes"] for row in early)


def test_frozen_hashes_and_label_free_holdout_seal_are_verified(tmp_path: Path) -> None:
    universe, normalized = _build(_fixture())
    write_frozen_artifacts(tmp_path, universe, normalized)
    result = verify_frozen_workspace(tmp_path)
    assert result["status"] == "VERIFIED"
    assert result["holdout_count"] == 3
    seal_path = tmp_path / "data/sealed/v5p_holdout/seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    assert seal["status"] == "SEALED"
    assert seal["label_payload_present"] is False
    assert seal["label_payload_path"] is None
    assert seal["holdout_labels_opened"] is False
    universe_path = tmp_path / "data/manifests/v5p_outcome_blind_universe.json"
    tampered = json.loads(universe_path.read_text(encoding="utf-8"))
    tampered["eligible_date_count"] += 1
    universe_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(V5PUniverseError, match="self_sha256 mismatch"):
        verify_frozen_workspace(tmp_path)


def test_offline_builder_uses_no_network_and_places_zero_orders(monkeypatch) -> None:
    def denied(*_args, **_kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", denied)
    universe, normalized = _build(_fixture())
    assert universe["network_used"] is False
    assert universe["actual_orders_placed"] is False
    assert universe["protected_confirmation_labels_read"] is False
    assert normalized["network_used"] is False
    assert normalized["actual_orders_placed"] is False


def test_rolling_preview_has_distinct_path_schema_and_cannot_be_final(monkeypatch, tmp_path: Path) -> None:
    values = _fixture(acquisition_status="RUNNING")
    monkeypatch.setattr(
        settlement, "_workspace_inputs",
        lambda _root, _run_id: (
            values["acquisition"], values["metadata"], values["candle_manifests"],
            values["weather_manifests"], values["climate_manifest"],
            values["evaluator_bounds"], values["safe_bindings"],
        ),
    )
    preview = settlement.write_preview_workspace(tmp_path)
    path = tmp_path / "data/manifests/v5p_outcome_blind_universe_preview.json"
    assert path.is_file()
    assert preview["schema_version"] == "klax-v5p-outcome-blind-universe-preview-v1"
    assert preview["status"] == "ROLLING_PREVIEW_ACQUISITION_OPEN"
    assert preview["is_final_immutable_universe"] is False
    assert not (tmp_path / "data/manifests/v5p_outcome_blind_universe.json").exists()
    body = {key: item for key, item in preview.items() if key != "self_sha256"}
    assert preview["self_sha256"] == canonical_hash(body)
