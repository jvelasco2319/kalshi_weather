"""Synthetic unit checks only; no historical or protected labels are read."""
import copy

import pytest

from v5_successors.evaluation import _masked_rows, _normalise, validate_config


def base(version, method, parameters):
    return {
        "schema_version": "v5-successor-config-v1",
        "version": version,
        "method": method,
        "description": "synthetic",
        "common_scoring_dates": 44,
        "warmup_dates": 20,
        "allow_network": False,
        "allow_orders": False,
        "allow_protected_labels": False,
        "parameters": parameters,
    }


@pytest.mark.parametrize("config", [
    base("V5C", "consensus_veto", {"maximum_friend_yes_probability": .25}),
    base("V5D", "disagreement_abstention", {"maximum_probability_gap": .15, "maximum_source_high_range_f": 6.0}),
    base("V5E", "heavy_tail_probability", {"wide_component_weight": .2, "wide_sigma_multiplier": 2.0}),
    base("V5F", "fixed_stacked_probability", {"v5b_weight": .5, "friend_heavy_tail_weight": .5,
          "wide_component_weight": .2, "wide_sigma_multiplier": 2.0}),
])
def test_frozen_successor_configs_validate(config):
    assert validate_config(config) == config


def test_permissions_and_unknown_parameters_fail_closed():
    config = base("V5C", "consensus_veto", {"maximum_friend_yes_probability": .25})
    config["allow_orders"] = True
    with pytest.raises(ValueError, match="offline"):
        validate_config(config)
    config = base("V5C", "consensus_veto", {"maximum_friend_yes_probability": .25, "secret": 1})
    with pytest.raises(ValueError, match="parameter|consensus"):
        validate_config(config)


def test_probability_normalization_and_invalid_mass():
    assert _normalise({"a": 2.0, "b": 1.0}) == {"a": pytest.approx(2 / 3), "b": pytest.approx(1 / 3)}
    with pytest.raises(ValueError):
        _normalise({"a": 0.0, "b": 0.0})


def test_veto_masks_quotes_without_removing_contract_partition():
    rows = [{"market_ticker": "A", "contract_side": "YES", "execution_price_cents": 10, "bid_price_cents": 9},
            {"market_ticker": "A", "contract_side": "NO", "execution_price_cents": 90, "bid_price_cents": 89},
            {"market_ticker": "B", "contract_side": "YES", "execution_price_cents": 20, "bid_price_cents": 19}]
    context = {"scoring_dates": ["2026-01-01"], "market": {"rows_by_date": {"2026-01-01": copy.deepcopy(rows)}}}
    output = _masked_rows(context, {("2026-01-01", "A")})["2026-01-01"]
    assert len(output) == len(rows)
    assert all(row["execution_price_cents"] is None for row in output if row["market_ticker"] == "A")
    assert next(row for row in output if row["market_ticker"] == "B")["execution_price_cents"] == 20
    assert context["market"]["rows_by_date"]["2026-01-01"] == rows

