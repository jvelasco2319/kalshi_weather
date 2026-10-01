from datetime import date
import json
from pathlib import Path

import pytest

from v7_shadow.campaign import V7CampaignError, target_dates, validate_config


ROOT = Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT / "configs/v7_six_week_shadow.json").read_text(encoding="utf-8"))


def test_registered_window_is_42_future_dates():
    values = target_dates(config())
    assert values[0] == "2026-09-29"
    assert values[-1] == "2026-11-09"
    assert len(values) == 42
    assert date.fromisoformat(values[0]) > date(2026, 9, 28)


def test_catalog_and_zero_order_boundary_validate():
    values = validate_config(config())
    assert len(values) == 42
    assert config()["order_authorization_count"] == 0


def test_enabling_orders_is_rejected():
    value = config()
    value["paper_orders_allowed"] = True
    with pytest.raises(V7CampaignError, match="prohibited capability"):
        validate_config(value)

