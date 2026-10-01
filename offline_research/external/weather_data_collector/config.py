"""Filesystem locations used by the weather forecast pipeline."""

from pathlib import Path


# --------------------------------------------------
# Project paths
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"

RAW_DATA_DIR = DATA_DIR / "raw"

PROCESSED_DATA_DIR = DATA_DIR / "processed"

JSON_OUTPUT_DIR = PROCESSED_DATA_DIR / "forecasts"


# Create directories if they do not exist

for directory in [
    RAW_DATA_DIR,
    PROCESSED_DATA_DIR,
    JSON_OUTPUT_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)

