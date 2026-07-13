from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from kalshi_weather.signal_room.app import create_app
from kalshi_weather.validation_journal import ValidationJournal

PACKAGE = Path("implementation") / "klax_probability_lab_live_audit"
SCHEMA_PATH = PACKAGE / "contracts" / "explainability_snapshot.schema.json"
FIXTURE_PATH = PACKAGE / "fixtures" / "sample_explainability_snapshot.json"
CANONICAL_KEYS = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]


def test_package_fixture_validates_against_canonical_schema() -> None:
    schema = _schema()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixture)


def test_live_latest_explainability_validates_and_has_required_invariants(tmp_path: Path) -> None:
    client = _client_with_complete_journal(tmp_path)
    payload = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/explainability/latest",
        params={"target": "2026-07-12"},
    ).json()

    Draft202012Validator(_schema(), format_checker=FormatChecker()).validate(payload)
    assert payload["eventTicker"] == "KXHIGHLAX-26JUL12"
    assert payload["strategyId"] == "klax-current-five-model-2026-07-11"
    assert payload["mode"] == "shadow"
    assert payload["captureHealth"]["orderPathReachable"] is False
    assert [model["modelKey"] for model in payload["models"]] == CANONICAL_KEYS

    for model in payload["models"]:
      temperatures = model["scenarioTemperaturesF"]
      weights = model["scenarioWeights"]
      assert len(temperatures) == len(weights)
      if weights:
          assert abs(sum(weights) - 1.0) < 1e-9
      yes_values = [row["pMeanYes"] for row in model["bracketProbabilities"]]
      if all(value is not None for value in yes_values):
          assert abs(sum(yes_values) - 1.0) < 0.02

    mix_weights = payload["mixture"]["scenarioWeights"]
    assert len(payload["mixture"]["scenarioTemperaturesF"]) == len(mix_weights)
    assert abs(sum(mix_weights) - 1.0) < 1e-9
    assert abs(sum(row["pMeanYes"] for row in payload["mixture"]["bracketProbabilities"]) - 1.0) < 0.02
    for row in payload["mixture"]["bracketProbabilities"]:
        assert row["pTradeYes"] <= min(row["mixtureLowerBoundYes"], row["weightedComponentLowerBoundYes"])
        assert row["pTradeNo"] <= min(row["mixtureLowerBoundNo"], row["weightedComponentLowerBoundNo"])

    assert payload["outcomeMap"]["verified"] is True
    orders = [row["order"] for row in payload["outcomeMap"]["brackets"]]
    assert orders == sorted(orders)

    for item in payload["economics"]:
        required = [row["requiredProbability"] for row in item["priceSensitivity"]]
        assert required == sorted(required)

    evaluated_at = datetime.fromisoformat(payload["evaluatedAt"].replace("Z", "+00:00"))
    for model in payload["models"]:
        for key in ("sourceAvailableAt", "receivedAt"):
            if model[key] is not None:
                assert datetime.fromisoformat(model[key].replace("Z", "+00:00")) <= evaluated_at


def test_canonical_explainability_routes_support_latest_index_and_replay_lookup(tmp_path: Path) -> None:
    client = _client_with_complete_journal(tmp_path)
    latest = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/explainability/latest",
        params={"target": "2026-07-12"},
    ).json()
    evaluations = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/evaluations",
        params={"target": "2026-07-12"},
    ).json()

    assert evaluations
    assert evaluations[-1]["evaluationId"] == latest["evaluationId"]

    specific_one = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/explainability",
        params={"target": "2026-07-12", "evaluation_id": latest["evaluationId"]},
    )
    specific_two = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/explainability",
        params={"target": "2026-07-12", "evaluation_id": latest["evaluationId"]},
    )
    assert specific_one.status_code == 200
    assert specific_one.json() == specific_two.json()

    missing = client.get(
        "/api/strategy/current/events/KXHIGHLAX-26JUL12/explainability",
        params={"target": "2026-07-12", "evaluation_id": "missing"},
    )
    assert missing.status_code == 404


def test_probability_lab_page_uses_local_assets_and_contains_approved_panels(tmp_path: Path) -> None:
    client = _client_with_complete_journal(tmp_path)
    html = client.get("/strategy/probability-lab").text

    required_text = [
        "Conservative trade probability",
        "Physical-high scenario distributions",
        "Model contribution ledger",
        "Probability funnel",
        "Equation trace",
        "Bracket probability matrix",
        "Market versus weather probability",
        "Price sensitivity",
        "Calculation and data health",
        "How to read this screen",
    ]
    for text in required_text:
        assert text in html
    assert "const DATA" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "/static/probability_lab.js" in html
    assert "Buy YES" in html
    assert "order controls" in html


def test_probability_lab_browser_bundle_does_not_contain_strategy_math_or_order_paths() -> None:
    source = Path("src/kalshi_weather/signal_room/static/probability_lab.js").read_text(
        encoding="utf-8"
    )
    blocked = [
        "BetaPPF",
        "Dirichlet",
        "function fee",
        "function requiredP",
        "function modeledROI",
        "trade_economics",
        "max_qualifying_price",
        "create_order",
        "cancel_order",
        "replace_order",
        "submit_order",
        "fetch(\"http",
        "fetch('http",
    ]
    assert not any(term in source for term in blocked)


def test_command_center_links_to_probability_lab_and_shows_eval_chip(tmp_path: Path) -> None:
    client = _client_with_complete_journal(tmp_path)
    html = client.get("/").text

    assert "/strategy/probability-lab" in html
    assert "commandEvalId" in html


def _client_with_complete_journal(tmp_path: Path) -> TestClient:
    journal_path = tmp_path / "validation.sqlite"
    journal = ValidationJournal(journal_path)
    journal.insert_snapshot(
        {
            "schema_version": "record_weather_market_v1",
            "experiment_id": "lax_model_validation",
            "captured_utc": "2026-07-12T19:31:05+00:00",
            "captured_local": "2026-07-12T12:31:05-07:00",
            "timezone": "America/Los_Angeles",
            "bucket_start_utc": "2026-07-12T19:30:00+00:00",
            "series": "KXHIGHLAX",
            "station": "KLAX",
            "target_date": "2026-07-12",
            "models": [
                _validation_model("ecmwf_ifs", "ok", 74.8),
                _validation_model("gfs013", "ok", 75.5),
                _validation_model("gfs_seamless", "ok", 71.3),
                _validation_model("nam", "ok", 70.8),
                _validation_model("nbm", "ok", 73.5),
            ],
            "markets": [
                _market("T72", "<=71"),
                _market("B72.5", "72-73"),
                _market("B74.5", "74-75"),
                _market("B76.5", "76-77"),
                _market("B78.5", "78-79"),
                _market("T79", ">=80"),
            ],
            "observation": {
                "target_date": "2026-07-12",
                "station": "KLAX",
                "source": "awc_metar",
                "latest_temp_f": 70.0,
                "latest_observation_utc": "2026-07-12T19:20:00+00:00",
                "high_so_far_f": 72.0,
                "final_high_f": None,
                "observation_count": 3,
                "error_message": None,
                "raw": {},
            },
        }
    )
    return TestClient(create_app(sqlite_path=journal_path, target_date=date(2026, 7, 12)))


def _validation_model(
    key: str,
    status: str,
    estimated_high: float | None,
) -> dict[str, object]:
    return {
        "model_key": key,
        "display_name": key,
        "provider": "test",
        "model_family": "test",
        "independence_group": "test",
        "source_type": "test",
        "fetch_status": status,
        "estimated_high_f": estimated_high,
        "estimated_bracket": "73-74" if estimated_high is not None else None,
        "uncertainty_spread_f": None,
        "error_message": None,
        "raw": {"created": datetime.now(timezone.utc).isoformat()},
    }


def _market(suffix: str, bracket_label: str) -> dict[str, object]:
    return {
        "ticker": f"KXHIGHLAX-26JUL12-{suffix}",
        "bracket_label": bracket_label,
        "yes_bid_cents": 4.0,
        "yes_ask_cents": 95.0,
        "no_bid_cents": 5.0,
        "no_ask_cents": 96.0,
        "yes_mid_cents": 49.5,
        "market_status": "active",
        "raw": {"market": {"event_ticker": "KXHIGHLAX-26JUL12", "yes_sub_title": bracket_label}},
    }


def _schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
