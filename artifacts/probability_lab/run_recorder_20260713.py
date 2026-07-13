from __future__ import annotations

import os
import runpy
import sys
from multiprocessing import freeze_support
from pathlib import Path

ROOT = Path(r"C:\Users\jarve\OneDrive\Documents\kalshi_weather")
sys.path.insert(0, str(ROOT / "src"))

LOG_DIR = ROOT / "artifacts" / "probability_lab"
LOG_DIR.mkdir(parents=True, exist_ok=True)
sys.stdout = (LOG_DIR / "recorder_20260713.out.log").open("a", encoding="utf-8", buffering=1)
sys.stderr = (LOG_DIR / "recorder_20260713.err.log").open("a", encoding="utf-8", buffering=1)

os.environ.setdefault("HERBIE_MODEL_TIMEOUT_SECONDS", "900")


def main() -> None:
    sys.argv = [
        "kalshi_weather.cli",
        "record-weather-market-loop",
        "--target-date",
        "2026-07-13",
        "--interval-seconds",
        "5",
        "--journal-path",
        "journals/lax_model_validation.sqlite",
        "--jsonl-path",
        "journals/lax_model_validation.jsonl",
        "--models",
        "ecmwf_ifs,gfs013,gfs_seamless,nam,nbm",
        "--replace-existing-bucket",
    ]
    runpy.run_module("kalshi_weather.cli", run_name="__main__")


if __name__ == "__main__":
    freeze_support()
    main()
