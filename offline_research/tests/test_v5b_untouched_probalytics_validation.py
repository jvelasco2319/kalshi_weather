from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/validate_v5b_untouched_probalytics.py"
SPEC = importlib.util.spec_from_file_location("v5b_paid_validation", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_top_returns_first_depth_level() -> None:
    assert MODULE.top([]) == (None, None)
    assert MODULE.top([{"price": 0.43, "size": 12.5}]) == (0.43, 12.5)


def test_validator_is_bound_to_separate_untouched_paths() -> None:
    assert MODULE.RAW_ROOT.as_posix().endswith("data/raw/v5b_untouched/probalytics")
    assert MODULE.NORMALIZED_ROOT.as_posix().endswith("data/normalized/v5b_untouched_probalytics")
    assert MODULE.OFFSETS == (0, 5, 30, 60)
