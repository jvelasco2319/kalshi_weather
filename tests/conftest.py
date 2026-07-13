from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TEST_TMP_ROOT = ROOT / ".test-artifacts" / "pytest-tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(TEST_TMP_ROOT))
