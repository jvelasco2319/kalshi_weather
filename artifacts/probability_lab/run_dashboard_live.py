from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"C:\Users\jarve\OneDrive\Documents\kalshi_weather")
LOG = ROOT / "artifacts" / "probability_lab" / "dashboard_server.pythonw.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
_log_handle = LOG.open("a", encoding="utf-8", buffering=1)
sys.stdout = _log_handle
sys.stderr = _log_handle
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather.signal_room.cli import run_dashboard  # noqa: E402

run_dashboard(
    host="127.0.0.1",
    port=8765,
    event="auto",
    mode="live",
    target_date="2026-07-12",
    open_browser=False,
    poll_seconds=5,
    allow_remote=False,
    sqlite_path=str(ROOT / "journals" / "lax_model_validation.sqlite"),
    sample_fixture=None,
)
