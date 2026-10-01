from copy import deepcopy
import json
from pathlib import Path
import pytest

from klax_lab.baseline import replay_model


def fixtures():
    policy = json.loads((Path(__file__).parents[1] / "configs/evaluation.json").read_text())
    policy["bootstrap_samples"] = 100
    contract = {"ticker": "A", "climate_date": "2025-01-05", "lower_integer_f": None, "upper_integer_f": 69,
                "open_time": "2025-01-04T15:00:00Z", "close_time": "2025-01-06T08:00:00Z"}
    from klax_lab.baseline import utc
    quote = {"ticker": "A", "climate_date": "2025-01-05", "end_period_ts": int(utc("2025-01-05T14:00:00Z").timestamp()),
             "yes_bid_close": "0.19", "yes_ask_close": "0.20", "source_sha256": "synthetic"}
    outcome = {"ticker": "A", "climate_date": "2025-01-05", "yes_outcome": 1, "mapping_consistent": True,
               "settlement_time": "2025-01-06T10:00:00Z"}
    return dict(predictions={"2025-01-05": (68., 2.)}, contracts=[contract], candles=[quote], outcomes=[outcome],
                eligible={"2025-01-05"}, policy=policy, model_version="fixture", dataset_version="fixture")


def test_outcomes_do_not_change_trade_choice_and_future_quotes_unused():
    values = fixtures()
    first = replay_model(**values)
    assert len(first["ledger"]) == 1
    future = {**values["candles"][0], "end_period_ts": values["candles"][0]["end_period_ts"] + 3600, "yes_ask_close": "0.90"}
    values["candles"].append(future)
    values["outcomes"][0]["yes_outcome"] = 0
    second = replay_model(**values)
    assert first["decisions"] == second["decisions"]
    assert first["ledger"][0]["net_profit"] > 0 > second["ledger"][0]["net_profit"]
    assert first["ledger"][0]["entry_price"] == second["ledger"][0]["entry_price"]


def test_daily_limit_across_contracts_and_stale_skip():
    values = fixtures()
    values["contracts"].append({**values["contracts"][0], "ticker": "B"})
    values["candles"].append({**values["candles"][0], "ticker": "B"})
    values["outcomes"].append({**values["outcomes"][0], "ticker": "B"})
    result = replay_model(**values)
    assert len(result["ledger"]) == 1 and result["ledger"][0]["ticker"] == "A"
    for quote in values["candles"]:
        quote["end_period_ts"] -= 7200
    assert not replay_model(**values)["ledger"]


def test_unverified_settlement_mapping_does_not_generate_profit():
    values = fixtures()
    values["outcomes"][0]["mapping_consistent"] = None
    assert not replay_model(**values)["ledger"]


def test_negative_slippage_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        replay_model(**fixtures(), slippage="-0.01")


def test_typed_research_side_policy_is_enforced_by_replay():
    values = fixtures()
    assert replay_model(**values)["ledger"][0]["side"] == "YES"
    values["policy"]["research_allowed_sides"] = ["NO"]
    result = replay_model(**values)
    assert not result["ledger"]
    assert result["summary"]["skipped_reasons"]["SIDE_POLICY"] == 1
