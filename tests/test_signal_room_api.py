from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from kalshi_weather.cli import app as cli_app
from kalshi_weather.signal_room.api_models import SignalRoomSnapshot
from kalshi_weather.signal_room.app import create_app
from kalshi_weather.validation_journal import ValidationJournal

FIXTURE = Path(__file__).parent / "fixtures" / "signal_room_july7_replay.json"
EXPLAINABILITY_SCHEMA = (
    Path("implementation")
    / "klax_signal_room_repair_with_probability_lab"
    / "contracts"
    / "explainability_snapshot.schema.json"
)
CANONICAL_KEYS = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]


def test_live_signal_room_empty_store_fails_closed(tmp_path: Path) -> None:
    client = TestClient(create_app(sqlite_path=tmp_path / "missing.sqlite"))

    health = client.get("/api/v1/signal-room/health").json()
    assert health["status"] == "not_ready"
    assert health["database_present"] is False
    assert health["mode"] == "shadow"

    events = client.get("/api/v1/signal-room/events").json()
    assert len(events) == 1
    snapshot_response = client.get(
        f"/api/v1/signal-room/events/{events[0]['ticker']}/snapshot"
    )
    snapshot = snapshot_response.json()
    assert snapshot_response.status_code == 200
    assert snapshot["strategy"]["order_submission_reachable"] is False
    assert snapshot["decision"]["status"] == "DATA_INCOMPLETE"
    assert [model["model_key"] for model in snapshot["models"]] == CANONICAL_KEYS
    assert snapshot["market"] == []
    assert any(gate["severity"] == "block" for gate in snapshot["gates"])


def test_live_signal_room_events_honor_requested_target_date(tmp_path: Path) -> None:
    client = TestClient(
        create_app(sqlite_path=tmp_path / "missing.sqlite", target_date=date(2026, 7, 13))
    )

    events = client.get("/api/v1/signal-room/events").json()

    assert events == [
        {
            "ticker": "KXHIGHLAX-26JUL13",
            "target_date": "2026-07-13",
            "station": "KLAX",
            "status": "open",
        }
    ]
    assert client.get("/favicon.ico").status_code == 204


def test_signal_room_snapshot_etag_is_stable_for_unchanged_live_data(tmp_path: Path) -> None:
    client = TestClient(create_app(sqlite_path=tmp_path / "missing.sqlite"))
    event = client.get("/api/v1/signal-room/events").json()[0]["ticker"]

    first = client.get(f"/api/v1/signal-room/events/{event}/snapshot")
    second = client.get(
        f"/api/v1/signal-room/events/{event}/snapshot",
        headers={"If-None-Match": first.headers["etag"]},
    )

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.headers["etag"] == first.headers["etag"]


def test_signal_room_reads_current_validation_journal_rows(tmp_path: Path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    ValidationJournal(journal_path).insert_snapshot(
        {
            "schema_version": "record_weather_market_v1",
            "experiment_id": "lax_model_validation",
            "captured_utc": "2026-07-12T16:31:05+00:00",
            "captured_local": "2026-07-12T09:31:05-07:00",
            "timezone": "America/Los_Angeles",
            "bucket_start_utc": "2026-07-12T16:30:00+00:00",
            "series": "KXHIGHLAX",
            "station": "KLAX",
            "target_date": "2026-07-12",
            "models": [
                _validation_model("best_match", "ok", 71.0),
                _validation_model("ecmwf_ifs", "ok", 73.4),
                _validation_model("gfs013", "ok", 74.2),
                _validation_model("gfs_seamless", "ok", 72.9),
                _validation_model("nam", "error", None, "provider unavailable"),
                _validation_model("nam_conus", "ok", 75.0),
                _validation_model("nbm", "missing", None, "no active fetcher"),
            ],
            "markets": [
                {
                    "ticker": "KXHIGHLAX-26JUL12-B73.5",
                    "bracket_label": "73-74",
                    "yes_bid_cents": 23.0,
                    "yes_ask_cents": 27.0,
                    "no_bid_cents": 73.0,
                    "no_ask_cents": 77.0,
                    "yes_mid_cents": 25.0,
                    "market_status": "active",
                    "raw": {"market": {"event_ticker": "KXHIGHLAX-26JUL12"}},
                }
            ],
            "observation": {
                "target_date": "2026-07-12",
                "station": "KLAX",
                "source": "awc_metar",
                "latest_temp_f": 70.0,
                "latest_observation_utc": "2026-07-12T16:20:00+00:00",
                "high_so_far_f": 72.0,
                "final_high_f": None,
                "observation_count": 3,
                "error_message": None,
                "raw": {},
            },
        }
    )
    client = TestClient(create_app(sqlite_path=journal_path, target_date=date(2026, 7, 12)))

    events = client.get("/api/v1/signal-room/events").json()
    assert events[0]["ticker"] == "KXHIGHLAX-26JUL12"

    snapshot = client.get(
        "/api/v1/signal-room/events/KXHIGHLAX-26JUL12/snapshot",
        params={"target": "2026-07-12"},
    ).json()

    assert snapshot["decision"]["status"] == "DATA_INCOMPLETE"
    assert snapshot["decision"]["reason_code"] == "NO_TRADE_SETTLEMENT_RULES_UNVERIFIED"
    assert [model["model_key"] for model in snapshot["models"]] == CANONICAL_KEYS
    assert [model["state_f"] for model in snapshot["models"]] == [73.4, 74.2, 72.9, 75.0, None]
    assert snapshot["models"][3]["status_detail"].startswith("Shadow-evaluated")
    assert snapshot["risk"]["observed_high_f"] == 72.0
    assert snapshot["risk"]["model_spread_f"] == 2.1
    assert snapshot["market"][0]["yes_ask"] == "0.27"
    assert snapshot["market"][0]["p_safe_yes"] is None
    assert snapshot["capture_health"]["source"] == "validation_journal"

    timeline = client.get(
        "/api/v1/signal-room/events/KXHIGHLAX-26JUL12/timeline",
        params={"target": "2026-07-12"},
    ).json()
    assert timeline[0]["model_states"]["nam"] == 75.0
    assert timeline[0]["model_states"]["best_match"] if "best_match" in timeline[0]["model_states"] else True


def test_signal_room_shadow_evaluates_complete_validation_ladder(tmp_path: Path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    ValidationJournal(journal_path).insert_snapshot(
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
    client = TestClient(create_app(sqlite_path=journal_path, target_date=date(2026, 7, 12)))

    snapshot = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL12/snapshot").json()

    assert snapshot["decision"]["status"] in {"NO_TRADE", "SHADOW_ONLY"}
    assert snapshot["decision"]["reason_code"] != "NO_TRADE_PROBABILITY_UNCALIBRATED"
    assert snapshot["readiness"]["tradable_feed_count"] == 5
    assert snapshot["readiness"]["settlement_rules_verified"] is True
    assert snapshot["market"][2]["p_safe_yes"] is not None
    assert snapshot["market"][2]["modeled_net_roi_yes"] is not None
    assert snapshot["probability_lab"]["mode"] == "quote_shadow"
    assert snapshot["probability_lab"]["calibration"]["source"] == "launch_default_residual"
    assert snapshot["explainability"]["order_submission_reachable"] is False

    lab = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL12/probability-lab").json()
    explainability = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL12/explainability").json()
    assert lab["evaluation_id"] == snapshot["probability_lab"]["evaluation_id"]
    assert explainability["evaluation_id"] == lab["evaluation_id"]
    schema = json.loads(EXPLAINABILITY_SCHEMA.read_text(encoding="utf-8"))
    for key in schema["required"]:
        assert key in explainability
    assert explainability["order_submission_reachable"] is False
    assert explainability["live_trading_enabled"] is False


def test_signal_room_explains_when_journal_has_other_target_date(tmp_path: Path) -> None:
    journal_path = tmp_path / "validation.sqlite"
    ValidationJournal(journal_path).insert_snapshot(
        {
            "schema_version": "record_weather_market_v1",
            "experiment_id": "lax_model_validation",
            "captured_utc": "2026-07-11T22:31:05+00:00",
            "captured_local": "2026-07-11T15:31:05-07:00",
            "timezone": "America/Los_Angeles",
            "bucket_start_utc": "2026-07-11T22:30:00+00:00",
            "series": "KXHIGHLAX",
            "station": "KLAX",
            "target_date": "2026-07-11",
            "models": [],
            "markets": [],
            "observation": {},
        }
    )
    client = TestClient(create_app(sqlite_path=journal_path, target_date=date(2026, 7, 12)))

    snapshot = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL12/snapshot").json()

    assert snapshot["decision"]["reason_code"] == "NO_RECORDER_SNAPSHOT_FOR_TARGET"
    assert "2026-07-12" in snapshot["decision"]["reason_text"]
    assert "2026-07-11" in snapshot["banner"]


def test_sample_replay_uses_explicit_fixture_and_settlement_truth() -> None:
    client = TestClient(create_app(sample_fixture_path=FIXTURE, mode="replay"))

    events = client.get("/api/v1/signal-room/events").json()
    assert events == [
        {
            "ticker": "KXHIGHLAX-26JUL07",
            "target_date": "2026-07-07",
            "station": "KLAX",
            "status": "settled",
            "market_open_at": "2026-07-07T07:00:00Z",
            "market_close_at": "2026-07-08T06:59:59Z",
            "settlement_bracket": "73-74 F",
            "final_decimal_high_f": 73.9,
            "official_high_f": 73.9,
        }
    ]

    snapshot = client.get(
        "/api/v1/signal-room/events/KXHIGHLAX-26JUL07/snapshot"
    ).json()
    parsed = SignalRoomSnapshot.model_validate(snapshot)
    assert parsed.sample_mode is True
    assert parsed.replay_mode is True
    assert parsed.event.settlement_bracket == "73-74 F"
    assert parsed.event.final_decimal_high_f == 73.9
    assert parsed.market[1].settled_outcome == "YES"
    assert parsed.market[1].yes_ask == "0.68"
    assert parsed.market[1].modeled_net_roi_yes is None


def test_snapshot_schema_rejects_unknown_or_missing_model_slots() -> None:
    client = TestClient(create_app(sample_fixture_path=FIXTURE, mode="replay"))
    payload = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL07/snapshot").json()

    payload["models"][0]["model_key"] = "research_only_model"
    with pytest.raises(ValidationError):
        SignalRoomSnapshot.model_validate(payload)

    payload = client.get("/api/v1/signal-room/events/KXHIGHLAX-26JUL07/snapshot").json()
    payload["models"] = payload["models"][:-1]
    with pytest.raises(ValidationError):
        SignalRoomSnapshot.model_validate(payload)


def test_signal_room_routes_are_read_only_and_assets_are_local() -> None:
    app = create_app(sample_fixture_path=FIXTURE, mode="replay")
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        assert not methods.intersection(mutating_methods)

    client = TestClient(app)
    html = client.get("/").text
    assert "Prototype A" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "order controls" in html
    assert "Buy" not in html
    assert "Sell" not in html


def test_signal_room_code_does_not_import_order_submission_paths() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path("src/kalshi_weather/signal_room").rglob("*.py")
    )
    blocked_terms = ["create_order", "place_order", "OrderClient", "requests.post", ".post("]
    assert not any(term in source_text for term in blocked_terms)


def test_strategy_dashboard_cli_is_registered() -> None:
    result = CliRunner().invoke(cli_app, ["strategy-dashboard", "--help"])
    assert result.exit_code == 0
    assert "--sample-fixture" in result.output
    assert "--allow-remote" in result.output


def _validation_model(
    key: str,
    status: str,
    estimated_high: float | None,
    error: str | None = None,
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
        "error_message": error,
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
