"""Independent security and arithmetic checks for the local V10 dashboard."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING

import pytest


class _IsolatedService:
    def __init__(self):
        self.entries = []
        self.started = threading.Event()
        self.release = threading.Event()

    def state(self):
        return {"orders": 0, "entries": list(self.entries)}

    def refresh(self):
        self.started.set()
        self.release.wait(3)
        return {"status": "REFRESHED"}

    def execute(self, action):
        return {"status": action.upper()}

    def record_trade(self, body):
        self.entries.append(dict(body))
        return {"recorded": True}


@pytest.fixture
def http_dashboard(tmp_path):
    from v10_ui.server import make_server
    service = _IsolatedService()
    server = make_server(tmp_path, port=0, service=service)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield server, service
    service.release.set()
    server.application.close()
    server.shutdown()
    server.server_close()
    worker.join(3)


def _http(server, method, path, body=None, headers=None):
    supplied = {"Host": f"127.0.0.1:{server.server_port}"}
    supplied.update(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        supplied.setdefault("Content-Type", "application/json")
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        value = response.read()
        return response.status, dict(response.headers), value
    finally:
        connection.close()


def _origin(server):
    return {"Origin": f"http://127.0.0.1:{server.server_port}"}


def test_server_is_loopback_and_state_cannot_enable_orders(http_dashboard):
    server, _ = http_dashboard
    assert server.server_address[0] == "127.0.0.1"
    code, headers, raw = _http(server, "GET", "/api/state")
    state = json.loads(raw)
    assert code == 200
    assert state["orders"] == 0
    assert state["session_token"] == server.application.token
    assert len(state["session_token"]) >= 32
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("path", ["/../configs/v10_online.json", "/%2e%2e/configs/v10_online.json",
                                  "/runs/v10_online/v10-online-20261002/registration.json",
                                  "/v10_ui/server.py", "/C:/Windows/win.ini", "/app.js/../server.py"])
def test_static_file_allowlist_cannot_read_project_or_arbitrary_files(http_dashboard, path):
    server, _ = http_dashboard
    code, _, raw = _http(server, "GET", path)
    assert code == 404
    assert json.loads(raw)["error"] == "Page not found."


def test_foreign_host_cannot_read_session_token(http_dashboard):
    server, _ = http_dashboard
    code, _, raw = _http(server, "GET", "/api/state", headers={"Host": "example.com"})
    assert code == 403
    assert "session_token" not in json.loads(raw)


@pytest.mark.parametrize("origin", [None, "null", "https://example.com", "http://localhost:1"])
def test_mutation_rejects_cross_site_and_opaque_origins(http_dashboard, origin):
    server, service = http_dashboard
    headers = {} if origin is None else {"Origin": origin}
    code, _, _ = _http(server, "POST", "/api/trade",
                        {"token": server.application.token, "kind": "practice"}, headers)
    assert code == 403
    assert service.entries == []


@pytest.mark.parametrize("token", ["", "wrong", 7, None])
def test_mutation_rejects_absent_or_wrong_session_token(http_dashboard, token):
    server, service = http_dashboard
    code, _, _ = _http(server, "POST", "/api/trade", {"token": token}, _origin(server))
    assert code == 403
    assert service.entries == []


def test_authenticated_action_and_trade_calls_are_bounded(http_dashboard):
    server, service = http_dashboard
    token = server.application.token
    code, _, _ = _http(server, "POST", "/api/action", {"token": token, "action": "refresh"}, _origin(server))
    assert code == 202
    assert service.started.wait(2)
    code, _, _ = _http(server, "POST", "/api/action", {"token": token, "action": "preview"}, _origin(server))
    assert code == 409
    code, _, _ = _http(server, "POST", "/api/trade", {"token": token, "kind": "practice"}, _origin(server))
    assert code == 409
    assert service.entries == []


def test_tracking_pause_requires_same_origin_token_and_boolean(http_dashboard):
    server, _ = http_dashboard
    token = server.application.token
    assert _http(server, "POST", "/api/tracking", {"token": token, "enabled": False})[0] == 403
    assert _http(server, "POST", "/api/tracking", {"token": "wrong", "enabled": False}, _origin(server))[0] == 403
    for value in ("true", 1, None):
        assert _http(server, "POST", "/api/tracking", {"token": token, "enabled": value}, _origin(server))[0] == 400
    assert _http(server, "POST", "/api/tracking", {"token": token, "enabled": False}, _origin(server))[0] == 200
    assert server.application.tracking.enabled is False


@pytest.mark.parametrize("action", ["buy", "shell", "place_order", "rm", "refresh;dir"])
def test_action_allowlist_never_runs_order_or_shell_commands(http_dashboard, action):
    server, service = http_dashboard
    code, _, _ = _http(server, "POST", "/api/action",
                        {"token": server.application.token, "action": action}, _origin(server))
    assert code == 400
    assert service.entries == []
    assert not service.started.is_set()


def test_mutation_rejects_non_json_and_excessive_payload(http_dashboard):
    server, service = http_dashboard
    code, _, _ = _http(server, "POST", "/api/trade", b"x", {**_origin(server), "Content-Type": "text/plain"})
    assert code == 415
    code, _, _ = _http(server, "POST", "/api/trade", b"x" * 16_385,
                        {**_origin(server), "Content-Type": "application/json"})
    assert code == 400
    assert service.entries == []


@pytest.fixture
def journal_service(tmp_path, monkeypatch):
    from v10_ui import service
    monkeypatch.setattr(service.runner, "report", lambda root: {
        "forecast_count": 0, "settled_count": 0, "pending_count": 0})
    clock = [datetime(2026, 10, 1, 22, tzinfo=timezone.utc)]
    check = lambda root: {"self_sha256": "isolated-test", "config": {}}
    dashboard = service.DashboardService(tmp_path, now=lambda: clock[0], registration_check=check)
    contracts = []
    shapes = [("less", None, 79, "T79"), ("between", 79, 80, "B79.5"),
              ("between", 81, 82, "B81.5"), ("between", 83, 84, "B83.5"),
              ("between", 85, 86, "B85.5"), ("greater", 86, None, "T86")]
    for strike, floor, cap, suffix in shapes:
        contracts.append({"ticker": f"KXHIGHLAX-26OCT01-{suffix}", "climate_date": "2026-10-01",
                          "event_ticker": "KXHIGHLAX-26OCT01", "strike_type": strike,
                          "floor_strike": floor, "cap_strike": cap,
                          "settlement_source": "The Weather Company"})
    quotes = [{"market_ticker": c["ticker"], "yes_bid": .34, "yes_ask": .35,
               "yes_bid_size": 100, "yes_ask_size": 100, "yes_midpoint": .345,
               "retrieved_at_utc": clock[0].isoformat()} for c in contracts]
    originals = [{"ticker": c["ticker"], "status": "active", "result": "",
                  "close_time": "2026-10-02T07:00:00+00:00"} for c in contracts]
    market = dashboard._display_market("2026-10-01", contracts, quotes,
                                      {"fee_type": "quadratic", "fee_multiplier": 1},
                                      originals, clock[0].isoformat())
    dashboard._markets["2026-10-01"] = market
    return dashboard, market, clock, check


def _entry(market, **extra):
    return {"kind": "practice", "side": "yes", "ticker": market["quotes"][0]["ticker"], **extra}


def _independent_fee(quantity, price, multiplier=1):
    return (Decimal("0.07") * Decimal(str(multiplier)) * quantity * Decimal(str(price))
            * (1-Decimal(str(price)))).quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def test_practice_budget_counts_fees_and_reduces_with_available_cash(journal_service):
    dashboard, market, _, _ = journal_service
    first = dashboard.record_trade(_entry(market))["entry"]
    assert first["quantity"] == 27
    assert Decimal(str(first["entry_fee"])) == _independent_fee(27, .35)
    assert first["entry_cost"] == pytest.approx(9.88)
    summary = dashboard.state()["trades"]["practice"]
    assert summary["cash"] == pytest.approx(90.12)
    assert summary["next_budget"] == pytest.approx(9.012)
    second = dashboard.record_trade(_entry(market))["entry"]
    assert second["quantity"] == 24
    assert second["entry_cost"] <= summary["next_budget"]
    assert second["quantity"]*.35 + float(_independent_fee(second["quantity"]+1, .35)) + .35 > summary["next_budget"]
    assert dashboard.state()["orders"] == 0


def test_practice_whole_contracts_cannot_exceed_fractional_visible_depth(journal_service):
    dashboard, market, _, _ = journal_service
    market["quotes"][0]["yes_ask_size"] = 2.9
    with pytest.raises(ValueError, match="Quantity exceeds"):
        dashboard.record_trade(_entry(market, quantity=3))
    recorded = dashboard.record_trade(_entry(market))["entry"]
    assert recorded["quantity"] == 2
    assert recorded["entry_cost"] == pytest.approx(.74)


@pytest.mark.parametrize("age", [121, -1])
def test_stale_and_future_quotes_block_new_practice_entries(journal_service, age):
    dashboard, market, clock, _ = journal_service
    market["quotes"][0]["retrieved_at_utc"] = (clock[0]-timedelta(seconds=age)).isoformat()
    with pytest.raises(ValueError, match="Refresh quotes"):
        dashboard.record_trade(_entry(market))
    assert dashboard.state()["trades"]["practice"]["cash"] == 100
    assert dashboard._events == []


@pytest.mark.parametrize("field,value", [("yes_ask", None), ("yes_ask_size", None),
                                        ("yes_ask_size", .99), ("close_time_utc", None),
                                        ("close_time_utc", "2026-10-01T21:59:00+00:00"),
                                        ("status", "closed")])
def test_unavailable_or_closed_entry_evidence_blocks_practice(journal_service, field, value):
    dashboard, market, _, _ = journal_service
    market["quotes"][0][field] = value
    with pytest.raises(ValueError):
        dashboard.record_trade(_entry(market))
    assert dashboard._events == []


@pytest.mark.parametrize("field,value", [("fee_type", "flat"), ("fee_multiplier", None),
                                        ("fee_multiplier", 0), ("fee_multiplier", "NaN")])
def test_unsupported_or_missing_fee_metadata_never_assumes_zero_fee(journal_service, field, value):
    dashboard, market, _, _ = journal_service
    market[field] = value
    with pytest.raises(ValueError):
        dashboard.record_trade(_entry(market))
    assert dashboard._cash() == 100


@pytest.mark.parametrize("field,value", [("yes_bid", None), ("yes_bid_size", None), ("yes_bid_size", 26)])
def test_missing_exit_bid_or_insufficient_size_does_not_invent_zero_pnl(journal_service, field, value):
    dashboard, market, _, _ = journal_service
    dashboard.record_trade(_entry(market))
    market["quotes"][0][field] = value
    trades = dashboard.state()["trades"]
    assert trades["items"][0]["marked_value"] is None
    assert trades["items"][0]["unrealized_pnl"] is None
    assert trades["practice"]["marked_equity"] is None
    assert trades["practice"]["unpriced_count"] == 1
    assert trades["practice"]["cash"] == pytest.approx(90.12)


def test_no_position_uses_complement_quote_and_side_depth(journal_service):
    dashboard, market, _, _ = journal_service
    result = dashboard.record_trade(_entry(market, side="no"))["entry"]
    assert result["entry_price"] == pytest.approx(.66)
    assert result["quantity"] == 14
    assert result["entry_fee"] == pytest.approx(.22)
    trade = dashboard.state()["trades"]["items"][0]
    assert trade["current_bid"] == pytest.approx(.65)
    assert trade["marked_value"] == pytest.approx(9.1)
    assert trade["unrealized_pnl"] == pytest.approx(-.36)


def test_manual_fractional_cent_and_subcent_fee_preserve_practice_cash_and_reload(journal_service):
    from v10_ui.service import DashboardService
    dashboard, market, clock, check = journal_service
    result = dashboard.record_trade(_entry(market, kind="manual", quantity=26,
                                          entry_price=.37170000000000003, entry_fee=.0041))["entry"]
    assert result["entry_price"] == pytest.approx(.3717)
    assert result["entry_fee"] == pytest.approx(.0041)
    assert result["entry_cost"] == pytest.approx(9.6683)
    assert dashboard._cash() == 100
    assert result["evidence"] == "USER_REPORTED_TRADE_NOT_ACCOUNT_VERIFIED"
    restored = DashboardService(dashboard.root, now=lambda:clock[0], registration_check=check)
    assert restored._cash() == 100
    assert len(restored._entries) == 1
    assert Decimal(restored._entries[0]["entry_fee"]) == Decimal(".0041")


def test_invalid_manual_money_rejected_before_append(journal_service):
    dashboard, market, _, _ = journal_service
    with pytest.raises(ValueError):
        dashboard.record_trade(_entry(market, kind="manual", quantity=26,
                                      entry_price=.123456, entry_fee=.0041))
    assert dashboard._events == []
    assert not list((dashboard.root/"runs/v10_ui/journal").glob("*.json"))


def _final(market, winner=0):
    market["updated_at_utc"] = "2026-10-02T08:01:00+00:00"
    market["status"] = "settled"
    for index, row in enumerate(market["raw_markets"]):
        row.update(status="finalized", result="yes" if index == winner else "no",
                   settlement_value_dollars="1.00" if index == winner else "0.00",
                   settlement_ts="2026-10-02T08:00:00+00:00", expiration_value="78.0")
    return {market["date"]: market}


def test_confirmed_binary_settlement_credits_cash_once_and_survives_reload(journal_service):
    from v10_ui.service import DashboardService
    dashboard, market, clock, check = journal_service
    entry = dashboard.record_trade(_entry(market))["entry"]
    settled = _final(market)
    dashboard._apply_settlements(settled, {})
    assert dashboard._cash() == Decimal("117.12")
    assert dashboard._settlements[entry["id"]]["payout"] == 27
    assert dashboard.state()["trades"]["practice"]["realized_pnl"] == pytest.approx(17.12)
    count = len(dashboard._events)
    dashboard._apply_settlements(settled, {})
    assert len(dashboard._events) == count
    restored = DashboardService(dashboard.root, now=lambda:clock[0], registration_check=check)
    assert restored._cash() == Decimal("117.12")
    assert len(restored._settlements) == 1


@pytest.mark.parametrize("mutation", ["two_winners", "nonbinary_payout", "future_settlement",
                                      "before_entry", "partial_final_value", "contradictory_values",
                                      "changed_contract"])
def test_unverified_or_contradictory_settlement_does_not_credit_practice_cash(journal_service, mutation):
    dashboard, market, _, _ = journal_service
    dashboard.record_trade(_entry(market))
    settled = _final(market)
    if mutation == "two_winners":
        market["raw_markets"][1].update(result="yes", settlement_value_dollars="1")
    elif mutation == "nonbinary_payout":
        market["raw_markets"][0]["settlement_value_dollars"] = ".5"
    elif mutation == "future_settlement":
        market["raw_markets"][0]["settlement_ts"] = "2026-10-02T09:00:00+00:00"
    elif mutation == "before_entry":
        market["raw_markets"][0]["settlement_ts"] = "2026-10-01T21:00:00+00:00"
    elif mutation == "partial_final_value":
        market["raw_markets"][0]["expiration_value"] = ""
    elif mutation == "contradictory_values":
        market["raw_markets"][0]["expiration_value"] = "77"
    elif mutation == "changed_contract":
        market["contracts"][0] = {**market["contracts"][0], "cap_strike": 78}
    dashboard._apply_settlements(settled, {})
    assert dashboard._cash() == Decimal("90.12")
    assert dashboard._settlements == {}


def test_day_rollover_hides_old_forecast_and_market(journal_service):
    dashboard, market, clock, _ = journal_service
    dashboard._forecast = {"kind": "preview", "date": "2026-10-01", "probabilities": [1/6]*6,
                           "tickers": [q["ticker"] for q in market["quotes"]], "excluded": True}
    clock[0] = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
    state = dashboard.state()
    assert state["date"] == "2026-10-02"
    assert state["forecast"]["kind"] == "none"
    assert state["market"]["quotes"] == []
    assert state["market"]["probabilities"] is None


@pytest.mark.parametrize("price,multiplier", [("0.000001", "1"), ("0.01", "1"),
                                             ("0.3717", "0.5"), ("0.5", "100"),
                                             ("0.9999", "1")])
def test_practice_sizing_is_admissible_maximum_for_fee_and_depth(journal_service, price, multiplier):
    dashboard, market, clock, _ = journal_service
    quote = market["quotes"][0]
    quote["yes_ask"] = float(price)
    quote["yes_ask_size"] = 2_000_000.9
    market["fee_multiplier"] = multiplier
    estimate = dashboard._practice_estimate(market, quote, "yes", clock[0])
    assert estimate["available"]
    quantity = estimate["quantity"]
    assert 1 <= quantity <= 1_000_000
    cost = quantity*Decimal(price)+_independent_fee(quantity, price, multiplier)
    assert cost <= Decimal("10")
    if quantity < 1_000_000:
        next_cost = (quantity+1)*Decimal(price)+_independent_fee(quantity+1, price, multiplier)
        assert next_cost > Decimal("10")
    recorded = dashboard.record_trade(_entry(market))["entry"]
    assert recorded["quantity"] == quantity


def test_unbound_raw_settlement_evidence_rejected_before_append(journal_service):
    dashboard, market, _, _ = journal_service
    dashboard.record_trade(_entry(market))
    raw = dashboard.root/"pretend-source.json"
    raw.write_text('{"market": "fixture"}', encoding="utf-8")
    before = len(dashboard._events)
    with pytest.raises(ValueError, match="(?i)raw.*binding"):
        dashboard._apply_settlements(_final(market), {"pretend-source.json": "0"*64})
    assert dashboard._cash() == Decimal("90.12")
    assert len(dashboard._events) == before
    assert len(list((dashboard.root/"runs/v10_ui/journal").glob("*.json"))) == before
