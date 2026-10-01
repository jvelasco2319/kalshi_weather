from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from v5.common import V5IntegrityError, canonical_hash
from v5.sample_regime import audit_sample_regime, cross_review_sample_regime


ROOT = Path(__file__).resolve().parents[2]


def test_current_sample_audit_is_outcome_blind_and_fails_closed():
    result = audit_sample_regime(ROOT)
    assert result["status"] == "INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ"
    assert result["promotion_ready"] is False
    assert result["confirmation_pool"]["maximum_calendar_days"] == 427
    assert result["protected_confirmation_labels_read"] is False
    assert result["live_or_paper_orders_authorized"] is False
    assert result["actual_orders_placed"] is False
    assert cross_review_sample_regime(result)["status"] == "PASS"


def test_sample_cross_review_rejects_mutation():
    result = audit_sample_regime(ROOT)
    changed = deepcopy(result)
    changed["prelabel_counts"]["selected_trade_count"] = 30
    assert cross_review_sample_regime(changed)["status"] == "FAIL"


def test_selected_trade_manifest_requires_hash_and_no_label_read(tmp_path: Path):
    for relative in (
        "configs/v5_four_colony_verification_campaign.json",
        "data/manifests/v4_dataset_1330_1500_1800.json",
        "data/manifests/v4_five_fold_split_1330_1500_1800.json",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
    manifest_path = tmp_path / "data/manifests/v5_outcome_blind_selected_trades.json"
    bad = {
        "outcome_blind": True,
        "protected_confirmation_labels_read": True,
        "selected_trade_count": 30,
        "distinct_settlement_days": 30,
        "selected_trade_count_by_fold": [6, 6, 6, 6, 6],
    }
    bad["manifest_sha256"] = canonical_hash(bad)
    manifest_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(V5IntegrityError, match="outcome-blind"):
        audit_sample_regime(tmp_path)
