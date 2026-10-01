from copy import deepcopy
from pathlib import Path

import pytest

from past7_replay.engine import _seal
from v10.protocol_freeze import V10ProtocolError, build, verify


ROOT = Path(__file__).resolve().parents[1]


def test_v10_protocol_registers_complete_finite_catalog_before_acquisition():
    value = build(ROOT)
    assert value["v8_remains_primary_and_unchanged"] is True
    assert value["additional_data_acquired"] is False
    assert value["settlement_labels_read_by_v10"] is False
    assert len(value["feature_blocks"]) == 6
    assert value["combination_rule"]["subset_count"] == 64
    assert value["combination_rule"]["total_candidate_count"] == 193
    assert value["additional_inputs"]["hrrr"]["registered_substitution"] == "temperature_925hpa_minus_temperature_2m"


def test_v10_protocol_rejects_resealed_safety_change():
    value = build(ROOT)
    value.pop("self_sha256")
    value["market_data_read_by_v10"] = True
    with pytest.raises(V10ProtocolError):
        verify(ROOT, _seal(deepcopy(value)))
