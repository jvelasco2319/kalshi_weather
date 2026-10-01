from copy import deepcopy
from pathlib import Path

import pytest

from past7_replay.engine import _seal
from v9.protocol_freeze import V9ProtocolError, build, verify


ROOT = Path(__file__).resolve().parents[1]


def test_v9_protocol_preserves_v8_and_physical_requirements():
    value = build(ROOT)
    assert value["v8_remains_primary_and_unchanged"] is True
    assert value["v9_candidate_fitted"] is False
    assert len(value["candidate_catalog"]) == 6
    required = value["required_additional_data"]
    assert "cloud layers and sky cover" in required["observed_klax"]["fields"]
    assert "2 m dewpoint" in required["forecast_vertical_structure"]["klax_fields"]
    assert required["inland_to_coast_pressure_gradient"]["inland_point"] == "KDAG"
    assert value["economic_confirmation"]["minimum_genuinely_new_grade_a_days"] == 100


def test_v9_protocol_rejects_resealed_v8_replacement():
    value = build(ROOT)
    value.pop("self_sha256")
    value["v8_remains_primary_and_unchanged"] = False
    with pytest.raises(V9ProtocolError):
        verify(ROOT, _seal(deepcopy(value)))
