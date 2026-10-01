from pathlib import Path
import json

import pytest

from klax_lab.component_fixtures_v3 import (
    MARKET_MANIFEST,
    MODEL_MANIFEST,
    build_market_fixture_evidence,
    build_model_fixture_evidence,
    publish_component_fixtures,
    verify_component_fixtures,
)
from klax_lab.provenance import canonical_hash


PROJECT = Path(__file__).resolve().parents[1]


def test_model_fixture_covers_registered_numerical_invariants_deterministically() -> None:
    first = build_model_fixture_evidence(PROJECT)
    second = build_model_fixture_evidence(PROJECT)
    assert first == second
    assert first["status"] == "ENGINEERING_FIXTURES_PASS"
    assert first["synthetic_engineering_fixtures"] is True
    assert first["campaign_or_profit_evidence"] is False
    assert first["protected_final_read"] is False
    assert all(first["invariants"].values())
    body = {key: value for key, value in first.items() if key != "evidence_sha256"}
    assert canonical_hash(body) == first["evidence_sha256"]


def test_market_fixture_exercises_asof_and_selective_controls() -> None:
    evidence = build_market_fixture_evidence(PROJECT)
    assert evidence["status"] == "ENGINEERING_FIXTURES_PASS"
    assert all(evidence["invariants"].values())
    assert set(evidence["outputs"]["decision_reasons"].values()) == {
        "ACCEPTED", "MINUTE_VOLUME_BELOW_CONTROL", "MINUTE_SPREAD_ABOVE_CONTROL",
        "SIDE_POLICY", "ENTRY_PRICE_OUTSIDE_CONTROL_BAND",
    }


def test_fixture_publisher_writes_separate_noncampaign_manifests(tmp_path: Path) -> None:
    model, market = publish_component_fixtures(PROJECT, tmp_path)
    assert (tmp_path / MODEL_MANIFEST).is_file()
    assert (tmp_path / MARKET_MANIFEST).is_file()
    assert model["campaign_or_profit_evidence"] is False
    assert market["campaign_or_profit_evidence"] is False
    assert verify_component_fixtures(PROJECT, tmp_path)["status"] == "PASS"


def test_fixture_verifier_reexecutes_and_rejects_saved_tampering(tmp_path: Path) -> None:
    publish_component_fixtures(PROJECT, tmp_path)
    path = tmp_path / MODEL_MANIFEST
    value = json.loads(path.read_text(encoding="utf-8"))
    value["outputs"]["regime_accuracy"] = 0.0
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="stale or modified"):
        verify_component_fixtures(PROJECT, tmp_path)
