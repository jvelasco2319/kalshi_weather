from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from klax_lab.v3_readiness import (
    REQUIRED_COMPONENT_MANIFESTS,
    V3_REPORT,
    publish_v3_implementation_status,
    validate_v3_registration,
)


PROJECT = Path(__file__).resolve().parents[1]


def fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    (tmp_path / "schemas").mkdir()
    shutil.copy2(PROJECT / "configs/v3_goal.json", tmp_path / "configs/v3_goal.json")
    shutil.copy2(PROJECT / "schemas/research-plan-v3.schema.json",
                 tmp_path / "schemas/research-plan-v3.schema.json")
    return tmp_path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def test_repository_v3_registration_is_complete_but_not_ready() -> None:
    result = validate_v3_registration(PROJECT)
    assert result["status"] == "PASS"
    assert len(result["colonies"]) == 6
    assert result["development_promotion_gates"]["minimum_primary_capital_weighted_net_return"] == .10
    assert "direct_market_residual_model" in result["registered_recommendations"]
    assert result["pending_executable_capabilities"] == []


def test_progress_report_lists_missing_components_and_preserves_v2_readiness(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    v2 = root / "data/manifests/readiness.json"
    v2.parent.mkdir(parents=True)
    original = b'{"status":"READY_FOR_OFFLINE_CAMPAIGN","immutable":"v2"}\n'
    v2.write_bytes(original)

    report = publish_v3_implementation_status(root)

    assert report["status"] == "V3_IMPLEMENTATION_IN_PROGRESS"
    assert report["ready_for_v3_campaign"] is False
    assert report["v3_campaign_authorized"] is False
    assert report["protected_final_access_authorized"] is False
    assert report["component_manifest_check"]["missing_count"] == len(REQUIRED_COMPONENT_MANIFESTS)
    assert v2.read_bytes() == original
    assert (root / V3_REPORT).is_file()


def test_manifest_presence_never_promotes_registration_to_readiness(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    for item in REQUIRED_COMPONENT_MANIFESTS:
        path = root / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"synthetic":"presence is not readiness"}', encoding="utf-8")
    report = publish_v3_implementation_status(root)
    assert report["component_manifest_check"]["missing_count"] == 0
    assert report["ready_for_v3_campaign"] is False
    assert report["v3_campaign_authorized"] is False


@pytest.mark.parametrize(("path", "weakened"), [
    (("objective", "minimum_estimated_net_expected_return_per_trade_on_entry_outlay"), .09),
    (("development_promotion_gates", "minimum_estimated_net_expected_return_for_each_selected_trade"), .09),
    (("development_promotion_gates", "minimum_primary_capital_weighted_net_return"), .09),
])
def test_ten_percent_gates_cannot_be_weakened(tmp_path: Path, path: tuple[str, str],
                                              weakened: float) -> None:
    root = fixture_root(tmp_path)
    config_path = root / "configs/v3_goal.json"
    config = load(config_path)
    config[path[0]][path[1]] = weakened
    save(config_path, config)
    with pytest.raises(ValueError, match="return|Objective|10%|Capital"):
        validate_v3_registration(root)


def test_schema_cannot_offer_entry_threshold_below_ten_percent(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    path = root / "schemas/research-plan-v3.schema.json"
    schema = load(path)
    schema["properties"]["entry_threshold"]["enum"].insert(0, .05)
    save(path, schema)
    with pytest.raises(ValueError, match="10% entry threshold"):
        validate_v3_registration(root)


@pytest.mark.parametrize(("mutation", "message"), [
    ("folds", "five folds"),
    ("bootstrap", "bootstrap"),
    ("stress", "Stress adverse price"),
    ("replication", "replication"),
    ("critic", "critic"),
])
def test_required_promotion_controls_cannot_be_removed(tmp_path: Path, mutation: str,
                                                       message: str) -> None:
    root = fixture_root(tmp_path)
    path = root / "configs/v3_goal.json"
    config = load(path)
    gates = config["development_promotion_gates"]
    if mutation == "folds":
        gates["chronological_folds"]["minimum_folds_with_strictly_positive_capital_weighted_return"] = 3
    elif mutation == "bootstrap":
        gates["bootstrap"]["minimum_lower_bound_exclusive"] = -.01
    elif mutation == "stress":
        gates["cost_stress"]["additional_adverse_price_per_contract_dollars"] = .01
    elif mutation == "replication":
        gates["require_independent_numerical_replication"] = False
    else:
        gates["require_critic_nonrejection"] = False
    save(path, config)
    with pytest.raises(ValueError, match=message):
        validate_v3_registration(root)


def test_colonies_must_agree_between_config_and_schema(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    path = root / "schemas/research-plan-v3.schema.json"
    schema = load(path)
    schema["properties"]["colony"]["enum"].pop()
    save(path, schema)
    with pytest.raises(ValueError, match="same six colonies"):
        validate_v3_registration(root)


def test_schema_must_keep_member_and_summary_gefs_evidence_distinct(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    path = root / "schemas/research-plan-v3.schema.json"
    schema = load(path)
    schema["properties"]["forecast_source_set"]["enum"].remove("gefs_summary")
    save(path, schema)
    with pytest.raises(ValueError, match="HRRR or GEFS source choices"):
        validate_v3_registration(root)


@pytest.mark.parametrize("mutation", ["zero_budget", "enlarged_budget", "missing_stop"])
def test_budgets_and_stopping_rules_must_remain_finite(tmp_path: Path, mutation: str) -> None:
    root = fixture_root(tmp_path)
    path = root / "configs/v3_goal.json"
    config = load(path)
    if mutation == "zero_budget":
        config["campaign_budget"]["maximum_epochs"] = 0
    elif mutation == "enlarged_budget":
        config["campaign_budget"]["maximum_distinct_executed_candidates"] = 600
    else:
        config["stopping_rule"].remove("wall_time_budget_exhausted")
    save(path, config)
    with pytest.raises(ValueError, match="budget|stopping"):
        validate_v3_registration(root)


@pytest.mark.parametrize(("section", "key", "message"), [
    ("data_priorities", "minute_kalshi_candles_and_public_trades", "data priorities"),
    ("probability_families", "ordered_logistic_bracket_probabilities", "probability capabilities"),
    ("selective_policy_controls", "minimum_liquidity_evidence", "selective policy controls"),
    ("settlement", "measurement_precision_and_integer_bin_mapping", "settlement semantics"),
])
def test_every_recommendation_must_remain_registered(tmp_path: Path, section: str, key: str,
                                                     message: str) -> None:
    root = fixture_root(tmp_path)
    path = root / "configs/v3_goal.json"
    config = load(path)
    if section == "data_priorities":
        config[section].remove(key)
    elif section == "settlement":
        config["exact_settlement_target"]["required_semantics"].remove(key)
    else:
        config["model_architecture"][section].remove(key)
    save(path, config)
    with pytest.raises(ValueError, match=message):
        validate_v3_registration(root)


@pytest.mark.parametrize("mutation", ["historical", "live", "paper", "final_use", "calibration_overlap"])
def test_offline_trading_and_one_use_final_boundaries_are_explicit(tmp_path: Path,
                                                                   mutation: str) -> None:
    root = fixture_root(tmp_path)
    path = root / "configs/v3_goal.json"
    config = load(path)
    if mutation == "historical":
        config["objective"]["historical_only"] = False
    elif mutation == "live":
        config["objective"]["online_trading"] = True
    elif mutation == "paper":
        config["objective"]["paper_orders"] = True
    elif mutation == "calibration_overlap":
        config["partitions"]["development_evaluation_start_inclusive"] = "2025-02-03"
    else:
        config["partitions"]["protected_final_maximum_evaluations"] = 2
    save(path, config)
    with pytest.raises(ValueError, match="historical-only|protected final"):
        validate_v3_registration(root)


def test_v3_report_refuses_to_overwrite_v2_readiness(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    v2 = root / "data/manifests/readiness.json"
    v2.parent.mkdir(parents=True)
    v2.write_text('{"historical":"v2"}', encoding="utf-8")
    with pytest.raises(ValueError, match="cannot overwrite V2 readiness"):
        publish_v3_implementation_status(root, v2)
    assert load(v2) == {"historical": "v2"}
