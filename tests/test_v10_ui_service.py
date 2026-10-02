"""Dashboard journal, source boundaries and quote arithmetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from v10_online.markets import canonical_contracts, event_ticker
from v10_online.storage import write_immutable
from v10_ui import service as ui

DAY = "2026-10-01"
NOW = datetime(2026, 10, 1, 22, tzinfo=timezone.utc)


def raw_markets(day=DAY):
    event = event_ticker(day)
    result = []
    for index, (kind, lower, upper, suffix) in enumerate([
            ("less", None, 79, "T79"), ("between", 79, 80, "B79.5"),
            ("between", 81, 82, "B81.5"), ("between", 83, 84, "B83.5"),
            ("between", 85, 86, "B85.5"), ("greater", 86, None, "T86")]):
        criterion = f"less than {upper}" if kind == "less" else f"greater than {lower}" if kind == "greater" else f"between {lower}-{upper}"
        d = datetime.fromisoformat(day)
        human = f"{d:%b} {d.day}, {d.year}"
        result.append({"ticker": event+"-"+suffix, "event_ticker": event,
                       "market_type": "binary", "strike_type": kind,
                       "floor_strike": lower, "cap_strike": upper,
                       "rules_primary": f"If the maximum temperature recorded at Los Angeles (CLILAX) for {human}, is {criterion}"+chr(176)+" fahrenheit according to The Weather Company, then the market resolves to Yes.",
                       "rules_secondary": "Fixture rules", "status": "active", "result": "",
                       "close_time": (NOW+timedelta(hours=12)).isoformat(), "expiration_value": ""})
    return result


def displayed(svc, *, bid=.35, ask=.37, size=1000, stamp=None):
    raw = raw_markets()
    contracts = canonical_contracts(raw, DAY, "The Weather Company")
    quotes = [{"market_ticker": c["ticker"], "yes_bid": bid, "yes_ask": ask,
               "yes_bid_size": size if bid is not None else None,
               "yes_ask_size": size if ask is not None else None,
               "yes_midpoint": (bid+ask)/2 if bid is not None and ask is not None else None,
               "retrieved_at_utc": (stamp or NOW).isoformat()} for c in contracts]
    return svc._display_market(DAY, contracts, quotes,
                               {"fee_type": "quadratic", "fee_multiplier": 1}, raw, NOW.isoformat())


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.runner, "report", lambda root: {"forecast_count": 0, "settled_count": 0, "pending_count": 0})
    check = lambda root: {"self_sha256": "fixture-registration", "config": {"date_start": "2026-10-02", "date_end": "2027-01-09"}}
    svc = ui.DashboardService(tmp_path, now=lambda: NOW, registration_check=check)
    svc._markets[DAY] = displayed(svc)
    return svc


def entry(svc, kind="practice", **extra):
    payload = {"kind": kind, "ticker": svc._markets[DAY]["quotes"][0]["ticker"], "side": "yes"}
    if kind == "manual":
        payload.update(quantity=5, entry_price=.40, entry_fee=.20)
    return svc.record_trade({**payload, **extra})["entry"]


def test_practice_wallet_uses_exact_cost_including_fee(dashboard):
    state = dashboard.state()
    assert state["trades"]["practice"]["cash"] == 100
    assert state["trades"]["practice"]["next_budget"] == 10
    trade = entry(dashboard)
    assert trade["quantity"] == 25
    assert trade["entry_fee"] == .41
    assert trade["entry_cost"] == 9.66
    assert trade["fill_demonstrated"] is False
    next_state = dashboard.state()
    assert next_state["trades"]["practice"]["cash"] == 90.34
    assert next_state["trades"]["practice"]["next_budget"] == 9.034
    assert next_state["trades"]["practice"]["marked_equity"] == 99.09


def test_practice_quantity_respects_depth_and_fee_budget(dashboard):
    dashboard._markets[DAY] = displayed(dashboard, size=3)
    assert entry(dashboard)["quantity"] == 3
    with pytest.raises(ValueError, match="Quantity exceeds"):
        entry(dashboard, quantity=4)
    assert len(dashboard._events) == 1


def test_fractional_cent_price_is_bounded_and_fee_inclusive(dashboard):
    dashboard._markets[DAY] = displayed(dashboard, bid=0, ask=.000001, size=1_000_000_000)
    estimate = dashboard.state()["market"]["quotes"][0]["practice_estimates"]["yes"]
    assert estimate["quantity"] == 1_000_000
    assert estimate["cost"] == 1.07
    assert estimate["cost"] <= estimate["budget"]
    assert entry(dashboard)["quantity"] == 1_000_000


def test_manual_is_separate_and_handles_fractional_cent_input(dashboard):
    trade = entry(dashboard, kind="manual", quantity=26, entry_price=.37170000000000003, entry_fee=.43)
    assert trade["entry_price"] == .3717
    assert trade["entry_cost"] == 10.0942
    state = dashboard.state()
    assert state["trades"]["practice"]["cash"] == 100
    assert state["trades"]["manual"]["count"] == 1
    restored = ui.DashboardService(dashboard.root, now=lambda: NOW, registration_check=dashboard._registration_check)
    assert restored.state()["trades"]["manual"]["count"] == 1


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(fee_type="unknown"),
    lambda m: m.update(fee_multiplier=0),
    lambda m: m.update(fee_multiplier=float("nan")),
    lambda m: m.update(status="closed"),
    lambda m: m["quotes"][0].update(close_time_utc=(NOW-timedelta(seconds=1)).isoformat()),
    lambda m: m["quotes"][0].update(retrieved_at_utc=(NOW-timedelta(seconds=121)).isoformat()),
    lambda m: m["quotes"][0].update(retrieved_at_utc=(NOW+timedelta(seconds=1)).isoformat()),
    lambda m: m["quotes"][0].update(yes_ask=None),
    lambda m: m["quotes"][0].update(yes_ask_size=.9),
])
def test_practice_rejects_unknown_fee_stale_or_ineligible_quote(dashboard, mutation):
    mutation(dashboard._markets[DAY])
    with pytest.raises(ValueError):
        entry(dashboard)
    assert not list((dashboard.root/ui.UI_RUN/"journal").glob("*.json"))


@pytest.mark.parametrize("field,value", [("yes_bid", None), ("yes_bid_size", 1),
                                        ("retrieved_at_utc", (NOW-timedelta(seconds=121)).isoformat())])
def test_missing_bid_depth_or_freshness_is_unpriced(dashboard, field, value):
    entry(dashboard)
    dashboard._markets[DAY]["quotes"][0][field] = value
    state = dashboard.state()
    assert state["trades"]["items"][0]["marked_value"] is None
    assert state["trades"]["items"][0]["unrealized_pnl"] is None
    assert state["trades"]["practice"]["marked_equity"] is None


def settled_market(svc):
    market = deepcopy(svc._markets[DAY])
    for index, raw in enumerate(market["raw_markets"]):
        raw.update(status="finalized", result="yes" if index == 0 else "no",
                   settlement_value_dollars="1.0000" if index == 0 else "0.0000",
                   settlement_ts=(NOW+timedelta(hours=1)).isoformat(), expiration_value="78")
    market["updated_at_utc"] = (NOW+timedelta(hours=2)).isoformat()
    market["status"] = "settled"
    return market


def test_final_binary_settlement_credit_is_once_and_survives_reload(dashboard):
    entry(dashboard)
    market = settled_market(dashboard)
    dashboard._apply_settlements({DAY: market}, {})
    dashboard._apply_settlements({DAY: market}, {})
    state = dashboard.state()
    assert len(dashboard._events) == 2
    assert state["trades"]["practice"]["cash"] == 115.34
    assert state["trades"]["items"][0]["realized_pnl"] == 15.34
    assert len(state["trades"]["history"]) == 1
    restored = ui.DashboardService(dashboard.root, now=lambda: NOW, registration_check=dashboard._registration_check)
    assert restored.state()["trades"]["practice"]["cash"] == 115.34


@pytest.mark.parametrize("mutation", [
    lambda m: m["raw_markets"][0].update(settlement_value_dollars=".5"),
    lambda m: m["raw_markets"][1].update(result="yes"),
    lambda m: m["raw_markets"][0].update(settlement_ts=(NOW-timedelta(seconds=1)).isoformat()),
    lambda m: m["raw_markets"][0].update(settlement_ts=(NOW+timedelta(hours=3)).isoformat()),
    lambda m: m["raw_markets"][0].update(expiration_value="79"),
    lambda m: m["contracts"][0].update(rules_secondary="Changed rule"),
])
def test_nonbinary_contradictory_or_unbound_settlement_is_not_credited(dashboard, mutation):
    entry(dashboard)
    market = settled_market(dashboard)
    mutation(market)
    dashboard._apply_settlements({DAY: market}, {})
    assert len(dashboard._events) == 1
    assert dashboard.state()["trades"]["practice"]["cash"] == 90.34


def test_invalid_append_does_not_poison_journal(dashboard):
    with pytest.raises(ValueError, match="Unknown"):
        dashboard._append_event({"type": "garbage"})
    assert not list((dashboard.root/ui.UI_RUN/"journal").glob("*.json"))
    entry(dashboard)
    assert len(dashboard._events) == 1


def test_manual_requires_entered_fee_and_integer_quantity(dashboard):
    for body in ({"quantity": 1.5}, {"entry_fee": None}, {"entry_price": float("nan")}, {"executed_at_utc": (NOW+timedelta(seconds=1)).isoformat()}):
        with pytest.raises(ValueError):
            entry(dashboard, kind="manual", **body)
    assert not dashboard._events


def test_one_sided_book_has_no_probability_and_no_is_complement(dashboard):
    market = displayed(dashboard, bid=.35, ask=None)
    assert market["probabilities"] is None
    assert market["quotes"][0]["no_bid"] is None
    assert market["quotes"][0]["no_ask"] == .65


def test_live_observations_are_pacific_day_display_only():
    text = "station,valid,tmpf,metar\nLAX,2026-10-01 06:53,65,KLAX test\nLAX,2026-10-01 07:53,66,KLAX test\nLAX,2026-10-01 21:53,72,KLAX test\nLAX,2026-10-01 22:00,73,KLAX test\nDAG,2026-10-01 21:53,90,KDAG test\n"
    points = ui._observations(text.encode(), DAY, (NOW+timedelta(minutes=10)).isoformat())
    assert [p["temperature_f"] for p in points] == [66, 72]
    url = ui._observation_url(DAY, NOW)
    query = parse_qs(urlsplit(url).query)
    assert query["sts"] == ["2026-10-01T07:00:00Z"]
    assert query["ets"] == ["2026-10-01T22:00:00Z"]
    ui.PublicClient.__module__  # The frozen, credential-free client is reused.


def test_new_day_never_relabels_previous_forecast_or_weather(dashboard):
    dashboard._forecast = {"kind": "preview", "date": DAY, "probabilities": [1, 0, 0, 0, 0, 0]}
    dashboard._observed = {"date": DAY, "observations": [{"time": NOW.isoformat(), "temperature_f": 72}]}
    dashboard._now = lambda: NOW+timedelta(hours=12)
    state = dashboard.state()
    assert state["forecast"]["kind"] == "none"
    assert state["market"]["status"] == "unavailable"
    assert state["weather"]["observations"] == []


def test_journal_chain_tampering_is_detected(dashboard):
    entry(dashboard)
    path = next((dashboard.root/ui.UI_RUN/"journal").glob("*.json"))
    text = path.read_text(encoding="utf-8").replace('"practice"', '"manual"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="seal differs"):
        ui.DashboardService(dashboard.root, now=lambda: NOW, registration_check=dashboard._registration_check)


class ArchivedFixtureClient:
    """Uses the real immutable receipt shape while replacing public sources."""
    def __init__(self, archive):
        self.archive = archive
        self.receipts = []
        self.requests = 0
        self.bytes = 0

    def fetch(self, url, maximum_bytes, headers=None):
        from v10_online.transport import validate_url
        validate_url(url)
        if "asos.py" in url:
            body = b"station,valid,tmpf,metar\nLAX,2026-10-01 07:53,66,KLAX fixture\nLAX,2026-10-01 20:53,72,KLAX fixture\n"
        elif "/series/" in url:
            body = json.dumps({"series": {"ticker": "KXHIGHLAX", "settlement_sources": [{"name": "The Weather Company"}], "fee_type": "quadratic", "fee_multiplier": 1}}).encode()
        elif "/orderbook" in url:
            body = json.dumps({"orderbook_fp": {"yes_dollars": [[".3500", "1000"]], "no_dollars": [[".6300", "1000"]]}}).encode()
        else:
            target = getattr(self, "market_day", DAY)
            markets = raw_markets(target) if getattr(self, "listed", True) else []
            if target != DAY:
                for row in markets:
                    row["close_time"] = (datetime.fromisoformat(target+"T08:00:00+00:00")+timedelta(days=1)).isoformat()
            body = json.dumps({"markets": markets, "cursor": ""}).encode()
        self.requests += 1
        self.bytes += len(body)
        assert len(body) <= maximum_bytes
        self.archive.mkdir(parents=True, exist_ok=True)
        name = f"{self.requests:03d}.raw"
        (self.archive/name).write_bytes(body)
        receipt = write_immutable(self.archive/(name+".json"), {"raw_path": name, "sha256": sha256(body).hexdigest(),
                                  "retrieved_at_utc": NOW.isoformat(), "url": url, "method": "GET", "bytes": len(body), "credentials_used": False})
        self.receipts.append(receipt)
        return {"body": body, **receipt}

    def get_json(self, url):
        return json.loads(self.fetch(url, maximum_bytes=1_000_000)["body"])


def test_finite_refresh_archives_sources_without_recording_forecast_or_trade(dashboard):
    dashboard._client_factory = ArchivedFixtureClient
    state = dashboard.refresh()
    assert state["market"]["quotes"][0]["yes_ask"] == .37
    assert state["market"]["quotes"][0]["fresh"] is True
    assert state["market"]["quotes"][0]["practice_estimates"]["yes"]["quantity"] == 25
    assert state["weather"]["observed_high_f"] == 72
    assert state["forecast"]["kind"] == "none"
    assert state["trades"]["items"] == []
    snapshot = next((dashboard.root/ui.UI_RUN/"snapshots").glob("*/snapshot.json"))
    saved = ui.read_verified(snapshot)
    assert saved["request_count"] == 9
    assert len(saved["raw_bindings"]) == 18
    ui.runner.verify_raw(dashboard.root, saved["raw_bindings"])
    assert saved["orders"] == 0
    assert saved["forecast_updated"] is False
    assert not (dashboard.root/ui.runner.RUN/"daily"/DAY/"prediction.json").exists()


def test_separate_market_and_weather_snapshots_preserve_receipt_bindings(dashboard):
    dashboard._client_factory = ArchivedFixtureClient
    dashboard.refresh()
    weather = deepcopy(dashboard._observed)
    dashboard._now = lambda: NOW+timedelta(seconds=30)
    dashboard.refresh_market()
    assert dashboard._observed == weather
    latest = max((ui.read_verified(path) for path in (dashboard.root/ui.UI_RUN/"snapshots").glob("*/snapshot.json")), key=lambda item: item["created_at_utc"])
    assert latest["request_count"] == 8
    assert latest["source_part"] == "market"
    assert len(latest["raw_bindings"]) == 18
    ui.runner.verify_raw(dashboard.root, latest["raw_bindings"])
    quotes = deepcopy(dashboard._markets[DAY])
    dashboard._now = lambda: NOW+timedelta(minutes=15)
    dashboard.refresh_weather()
    assert dashboard._markets[DAY] == quotes
    latest = max((ui.read_verified(path) for path in (dashboard.root/ui.UI_RUN/"snapshots").glob("*/snapshot.json")), key=lambda item: item["created_at_utc"])
    assert latest["request_count"] == 1
    assert latest["source_part"] == "weather"
    assert len(latest["raw_bindings"]) == 18
    ui.runner.verify_raw(dashboard.root, latest["raw_bindings"])
    assert dashboard._forecast["kind"] == "none"
    assert dashboard._events == []
