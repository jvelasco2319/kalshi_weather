from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile

import pytest

from past7_replay.engine import (
    REPLAY_RELATIVE,
    TARGET_DATES,
    _canonical_hash,
    _load_market_evidence,
    _load_outcomes,
    _load_target_universe,
    _load_training,
    _select_friend_yes,
    _select_no,
)
from v5a.development_search import _fee


ROOT = Path(__file__).resolve().parents[1]


def test_actual_outcome_blind_target_inputs_and_training_scope_are_exact():
    rows, contracts = _load_target_universe(ROOT)
    evidence = _load_market_evidence(ROOT / REPLAY_RELATIVE, rows)
    features, labels = _load_training(ROOT)
    assert list(rows) == list(TARGET_DATES)
    assert len(contracts) == 42
    assert len(evidence) == 52
    assert len(features) == len(labels) == 64
    assert features[0]["climate_date"] == "2026-06-01"
    assert features[-1]["climate_date"] == "2026-08-03"
    assert not ({row["climate_date"] for row in labels} & set(TARGET_DATES))


def test_decimal_fee_and_no_selector_are_exact_and_one_contract():
    day = "2026-09-21"
    contracts = [
        {"market_ticker": "a", "contract_side": "YES", "strike_type": "less", "floor_strike": None, "cap_strike": 70},
        {"market_ticker": "b", "contract_side": "YES", "strike_type": "greater", "floor_strike": 70, "cap_strike": None},
    ]
    evidence = {
        ("a", "NO"): {"market_ticker": "a", "contract_side": "NO", "entry_price_cents": 30,
                      "bid_price_cents": 29, "bid_size": "2", "ask_size": "3",
                      "quote_at_utc": day + "T18:00:05+00:00", "execution_evidence_grade": "A",
                      "execution_source_kind": "PAID_FULL_BOOK", "assumed_fill": False},
        ("b", "NO"): {"market_ticker": "b", "contract_side": "NO", "entry_price_cents": 50,
                      "bid_price_cents": 49, "bid_size": "2", "ask_size": "3",
                      "quote_at_utc": day + "T18:00:05+00:00", "execution_evidence_grade": "A",
                      "execution_source_kind": "PAID_FULL_BOOK", "assumed_fill": False},
    }
    result = _select_no(day, {"a": 0.05, "b": 0.95}, contracts, evidence,
                        {"allowed_sides": ["NO"], "selection_mode": "expected_profit"})
    assert result["status"] == "SELECTED"
    assert result["market_ticker"] == "a"
    assert result["fee_dollars"] == format(_fee(day, 30), "f")
    assert Decimal(result["entry_outlay_dollars"]) == Decimal("0.30") + _fee(day, 30)
    assert result["quantity"] == 1


def test_friend_selector_enforces_one_contract_cap():
    day = "2026-09-22"
    contracts = [
        {"market_ticker": "a", "contract_side": "YES", "strike_type": "less", "floor_strike": None, "cap_strike": 70},
        {"market_ticker": "b", "contract_side": "YES", "strike_type": "greater", "floor_strike": 70, "cap_strike": None},
    ]
    evidence = {}
    for ticker, price in (("a", 20), ("b", 25)):
        evidence[ticker, "YES"] = {
            "market_ticker": ticker, "contract_side": "YES", "entry_price_cents": price,
            "bid_price_cents": price - 1, "bid_size": "1", "ask_size": "1",
            "quote_at_utc": day + "T18:00:01+00:00", "execution_evidence_grade": "A",
            "execution_source_kind": "PAID_FULL_BOOK", "assumed_fill": False,
        }
    config = {"strategy": {"allowed_execution_grades": ["A", "B_PLUS", "B"],
                           "maximum_spread_cents": 5, "minimum_conservative_edge_dollars": 0.05}}
    result = _select_friend_yes(day, {"a": 0.45, "b": 0.55}, {"a": 0.40, "b": 0.50},
                                contracts, evidence, config)
    assert result["status"] == "SELECTED"
    assert result["quantity"] == 1
    assert result["market_ticker"] == "b"


def test_outcome_parser_requires_all_six_markets_each_day():
    (ROOT / "work").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "work") as folder:
        path = Path(folder) / "resolved.tsv"
        path.write_text(
            "market_ticker\ttitle\tcloses_at\tresolved_at\twinning_side\n"
            "KXHIGHLAX-26SEP21-X\tx\t2026-09-22 08:00:00\t2026-09-22 09:00:00\tyes\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="incomplete"):
            _load_outcomes(path, {"KXHIGHLAX-26SEP21-X"})


def test_canonical_hash_ignores_only_self_hash():
    value = {"a": 1, "self_sha256": "wrong"}
    digest = _canonical_hash(value)
    value["self_sha256"] = digest
    assert _canonical_hash(value) == digest
