from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_research_data", ROOT / "scripts/bootstrap_research_data.py"
)
assert SPEC and SPEC.loader
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BOOTSTRAP
SPEC.loader.exec_module(BOOTSTRAP)


def test_stage_catalog_has_all_sources_and_no_execution_paths():
    catalog = BOOTSTRAP.stages()
    identifiers = {stage.stage_id for stage in catalog}
    assert identifiers == {
        "clilax",
        "hrrr-gefs-v3",
        "observations-v3",
        "weather-v3-normalized",
        "kalshi-metadata",
        "kalshi-hourly-2025",
        "kalshi-minute-development",
        "kalshi-public-trades",
        "friend-weather-2025",
        "v10-meteorology",
        "probalytics-depth",
        "local-model",
    }
    command_text = " ".join(
        item for stage in catalog for item in (stage.command or ())
    ).casefold()
    for forbidden in ("order", "settlement", "campaign", "protected_final", "websocket"):
        assert forbidden not in command_text


def test_paid_and_large_stages_are_opt_in():
    parser_args = type(
        "Arguments",
        (),
        {
            "only": None,
            "include_probalytics": False,
            "include_local_model": False,
        },
    )()
    selected = {stage.stage_id for stage in BOOTSTRAP.stages() if BOOTSTRAP._selected(stage, parser_args)}
    assert "probalytics-depth" not in selected
    assert "local-model" not in selected
    assert "hrrr-gefs-v3" in selected


def test_empty_tree_reports_missing_without_network(tmp_path):
    args = type(
        "Arguments",
        (),
        {
            "only": None,
            "include_probalytics": True,
            "include_local_model": True,
        },
    )()
    rows = BOOTSTRAP._status(tmp_path, args)
    assert rows
    assert all(row["status"] == "MISSING" for row in rows)


def test_v10_prerequisite_requires_both_calendar_partitions(tmp_path):
    ready, detail = BOOTSTRAP._calendar_weather_prerequisite(tmp_path)
    assert ready is False
    assert "0/184" in detail
