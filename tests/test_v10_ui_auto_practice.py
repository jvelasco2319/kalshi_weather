"""Synthetic cutoff fixtures: simulated entries, skips, cash and restart guards."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from v10_online.markets import canonical_contracts
from v10_online.storage import hash_file, write_immutable, read_verified
from v10_ui import service as ui, practice
from test_v10_ui_service import raw_markets

DAY = "2026-10-02"
CUTOFF = datetime(2026, 10, 2, 18, tzinfo=timezone.utc)
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture
def automatic(tmp_path, monkeypatch):
    monkeypatch.setattr(ui.runner, "report", lambda root: {"forecast_count": 0, "settled_count": 0, "pending_count": 0})
    frozen = tmp_path/practice.FREEZE
    frozen.parent.mkdir(parents=True)
    shutil.copyfile(PROJECT/practice.FREEZE, frozen)
    clock = [CUTOFF+timedelta(seconds=12)]
    registration = {"self_sha256": "fixture-registration", "config": {"date_start": DAY, "date_end": "2027-01-09"}}
    svc = ui.DashboardService(tmp_path, now=lambda: clock[0], registration_check=lambda root: registration)
    raw = raw_markets(DAY)
    for r in raw:
        r["close_time"] = (CUTOFF+timedelta(hours=12)).isoformat()
    contracts = canonical_contracts(raw, DAY, "The Weather Company")
    quotes = [{"market_ticker": c["ticker"], "yes_bid": .35, "yes_ask": .37,
               "yes_bid_size": 1000, "yes_ask_size": 1000, "yes_midpoint": .36,
               "retrieved_at_utc": (CUTOFF-timedelta(seconds=10)).isoformat()} for c in contracts]
    source = {"contracts": contracts, "quotes": quotes, "series": {"fee_type": "quadratic", "fee_multiplier": 1},
              "settlement_source": "The Weather Company"}
    cutoff_path = tmp_path/"sources/cutoff.json"
    write_immutable(cutoff_path, {"classification": "SYNTHETIC_TEST_ONLY", "market": source})
    prediction = {"status": "FROZEN_PROSPECTIVE_FORECAST", "prospective_eligible": True, "outcomes_read": False,
                  "registration_seal": registration["self_sha256"], "climate_date": DAY,
                  "decision_at_utc": CUTOFF.isoformat(), "published_at_utc": (CUTOFF+timedelta(seconds=1)).isoformat(),
                  "prediction": {"probabilities": [.5, .2, .1, .08, .07, .05], "tickers": [c["ticker"] for c in contracts],
                                 "pressure_state": "neutral", "decision_at_utc": CUTOFF.isoformat()},
                  "market": source, "raw_bindings": {"sources/cutoff.json": hash_file(cutoff_path)}}
    primary = tmp_path/ui.runner.RUN/"daily"/DAY/"prediction.json"
    write_immutable(primary, prediction)
    live_quotes = deepcopy(quotes)
    for q in live_quotes:
        q["retrieved_at_utc"] = (CUTOFF+timedelta(seconds=10)).isoformat()
    market = svc._display_market(DAY, contracts, live_quotes, source["series"], raw, clock[0].isoformat())
    receipt = tmp_path/"sources/arrival.json"
    write_immutable(receipt, {"classification": "SYNTHETIC_TEST_ONLY", "market": market})
    svc._markets[DAY] = market
    svc._market_bindings[DAY] = {"sources/arrival.json": hash_file(receipt)}
    return svc, clock, prediction, primary


def replace_primary(fixture, mutate):
    svc, clock, prediction, primary = fixture
    mutate(prediction)
    # Replacing synthetic fixture data is deliberately confined to tmp_path.
    primary.unlink()
    write_immutable(primary, prediction)


def test_one_fee_inclusive_entry_daily_survives_restart_and_concurrent_checks(automatic):
    svc, clock, _, _ = automatic
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: svc.evaluate_automatic_practice(), range(8)))
    assert all(r["status"] == "ENTERED" for r in results)
    assert len(svc._entries) == len(svc._events) == 1
    row = svc._entries[0]
    assert row["side"] == "no" and row["origin"] == "automatic"
    assert row["ticker"].endswith("T86")  # Highest single-contract expected profit.
    assert row["quantity"] == 15 and Decimal(row["entry_cost"]) == Decimal("9.99")
    assert row["model_probability"] == .95
    # Independent arithmetic, after the rounded fee and on total outlay.
    expected_fee = Decimal(".24")
    assert Decimal(row["entry_fee"]) == expected_fee
    assert row["expected_net_return"] == pytest.approx((15*.95-9.99)/9.99)
    assert row["fill_demonstrated"] is False
    assert svc.state()["trades"]["practice"]["cash"] == 90.01
    assert svc.state()["orders"] == 0
    restored = ui.DashboardService(svc.root, now=lambda: clock[0], registration_check=svc._registration_check)
    restored.evaluate_automatic_practice()
    assert len(restored._entries) == len(restored._events) == 1
    assert restored.state()["automatic_practice"]["status"] == "ENTERED"
    saved = read_verified(next((svc.root/ui.UI_RUN/"journal").glob("*.json")))
    assert saved["type"] == "auto_decision" and saved["entry"]["id"] == saved["decision"]["entry_id"]
    assert len(saved["decision"]["raw_bindings"]) == 4


@pytest.mark.parametrize("mutate,reason", [
    (lambda p: p.update(status="DIAGNOSTIC_PREVIEW_EXCLUDED", prospective_eligible=False), "primary"),
    (lambda p: p.update(outcomes_read=True), "primary"),
    (lambda p: p.update(registration_seal="another-model"), "primary"),
    (lambda p: p.update(published_at_utc=(CUTOFF+timedelta(seconds=61)).isoformat()), "primary"),
    (lambda p: p["market"]["quotes"][0].update(retrieved_at_utc=(CUTOFF+timedelta(seconds=1)).isoformat()), "after the cutoff"),
    (lambda p: p["market"]["quotes"][0].update(retrieved_at_utc=(CUTOFF-timedelta(seconds=61)).isoformat()), "stale"),
    (lambda p: p["prediction"].update(probabilities=[.2, .2, .2, .15, .15, .1]), "10 percentage points"),
    (lambda p: p["prediction"].update(probabilities=[.5]*6), "Invalid"),
])
def test_ineligible_forecast_is_skipped_once(automatic, mutate, reason):
    replace_primary(automatic, mutate)
    svc = automatic[0]
    result = svc.evaluate_automatic_practice()
    assert result["status"] == "SKIPPED" and reason in result["decision"]["reason"]
    svc.evaluate_automatic_practice()
    assert not svc._entries and len(svc._events) == 1
    assert svc.state()["trades"]["practice"]["cash"] == 100


def test_absent_primary_waits_then_skips_without_using_preview(automatic):
    svc, clock, prediction, primary = automatic
    primary.rename(primary.with_name("preview-fixture.json"))
    assert svc.evaluate_automatic_practice()["status"] == "WAITING"
    assert not svc._events
    clock[0] = CUTOFF+timedelta(seconds=121)
    assert svc.evaluate_automatic_practice()["status"] == "SKIPPED"
    assert not svc._entries


@pytest.mark.parametrize("stamp", [CUTOFF-timedelta(seconds=1), CUTOFF+timedelta(seconds=4), CUTOFF+timedelta(seconds=13)])
def test_wait_for_post_arrival_quote_and_reject_future_time(automatic, stamp):
    svc, clock, _, _ = automatic
    for q in svc._markets[DAY]["quotes"]:
        q["retrieved_at_utc"] = stamp.isoformat()
    assert svc.evaluate_automatic_practice()["status"] == "WAITING"
    clock[0] = CUTOFF+timedelta(seconds=120)
    for q in svc._markets[DAY]["quotes"]:
        q["retrieved_at_utc"] = (CUTOFF-timedelta(seconds=1)).isoformat()
    assert "No fresh" in svc.evaluate_automatic_practice()["decision"]["reason"]
    assert not svc._entries


@pytest.mark.parametrize("changes,reason", [
    ({"no_ask": .85}, "5-80"),
    ({"no_bid": .55}, "spread"),
    ({"no_ask_size": 0}, "size"),
    ({"no_bid": None}, "numeric"),
    ({"no_ask": .655}, "Fractional-cent"),
    ({"status": "closed"}, "not open"),
    ({"close_time_utc": CUTOFF.isoformat()}, "closed"),
    ({"no_ask": .8, "no_bid": .78}, "return limit"),
])
def test_fresh_quote_gates_and_no_reranking(automatic, changes, reason):
    svc = automatic[0]
    if reason == "return limit":
        replace_primary(automatic, lambda p: p["prediction"].update(probabilities=[.5, .1, .1, .1, .1, .1]))
    svc._markets[DAY]["quotes"][-1].update(changes)
    result = svc.evaluate_automatic_practice()
    assert result["status"] == "SKIPPED" and reason in result["decision"]["reason"]
    assert not svc._entries  # Do not fall back to a different attractive bracket.


def test_quantity_respects_ask_size_and_remaining_cash(automatic):
    svc = automatic[0]
    svc._markets[DAY]["quotes"][-1]["no_ask_size"] = 3
    prior = svc.record_trade({"kind": "practice", "ticker": svc._markets[DAY]["quotes"][0]["ticker"], "side": "yes"})
    cash_before = Decimal(100)-Decimal(str(prior["entry"]["entry_cost"]))
    result = svc.evaluate_automatic_practice()
    assert result["status"] == "ENTERED"
    auto = svc._entries[-1]
    assert auto["quantity"] == 3
    assert Decimal(auto["entry_cost"]) <= cash_before*Decimal(".1")
    assert result["decision"]["budget"] == float(cash_before*Decimal(".1"))


def test_no_backdated_entry_when_server_starts_late(automatic):
    svc, clock, _, _ = automatic
    clock[0] = CUTOFF+timedelta(hours=4)
    assert svc.evaluate_automatic_practice()["status"] == "SKIPPED"
    assert not svc._entries


def test_write_failure_cannot_change_cash_or_count(automatic, monkeypatch):
    svc = automatic[0]
    def fail(*args, **kwargs):
        raise OSError("Disk unavailable")
    monkeypatch.setattr(ui, "write_immutable", fail)
    with pytest.raises(OSError, match="Disk unavailable"):
        svc.evaluate_automatic_practice()
    assert not svc._entries and not svc._events and svc._cash() == 100


def test_second_server_cannot_duplicate_day_or_fork_journal(automatic):
    svc, clock, _, _ = automatic
    other = ui.DashboardService(svc.root, now=lambda: clock[0], registration_check=svc._registration_check)
    other._markets = deepcopy(svc._markets)
    other._market_bindings = deepcopy(svc._market_bindings)
    svc.evaluate_automatic_practice()
    with pytest.raises(RuntimeError, match="journal changed"):
        other.evaluate_automatic_practice()
    assert len(list((svc.root/ui.UI_RUN/"journal").glob("*.json"))) == 1
    restored = ui.DashboardService(svc.root, now=lambda: clock[0], registration_check=svc._registration_check)
    assert restored._cash() == Decimal("90.01")


def test_journal_lock_is_released_after_failed_write(automatic, monkeypatch):
    svc = automatic[0]
    original = ui.write_immutable
    monkeypatch.setattr(ui, "write_immutable", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Test write failure")))
    with pytest.raises(OSError):
        svc.evaluate_automatic_practice()
    monkeypatch.setattr(ui, "write_immutable", original)
    assert svc.evaluate_automatic_practice()["status"] == "ENTERED"


@pytest.mark.parametrize("change,reason", [
    (lambda svc: svc._markets[DAY].update(fee_type="unknown"), "fee type"),
    (lambda svc: svc._market_bindings.update({DAY: {}}), "provenance"),
    (lambda svc: svc._markets[DAY]["contracts"][0].update(rules_secondary="Changed rules"), "contracts changed"),
    (lambda svc: svc._markets[DAY].update(fee_multiplier=100), "below 10%"),
])
def test_arrival_metadata_failures_skip(automatic, change, reason):
    svc = automatic[0]
    change(svc)
    result = svc.evaluate_automatic_practice()
    assert result["status"] == "SKIPPED" and reason in result["decision"]["reason"]
    assert not svc._entries


def test_source_tampering_cannot_produce_fake_entry(automatic):
    svc = automatic[0]
    (svc.root/"sources/arrival.json").write_text("tampered synthetic source", encoding="utf-8")
    result = svc.evaluate_automatic_practice()
    assert result["status"] == "SKIPPED" and not svc._entries
    assert "arrival.json" in result["decision"]["reason"]


@pytest.mark.parametrize("stamp", ["2026-10-01T22:00:00+00:00", "2027-01-10T20:00:00+00:00", "2026-10-02T18:00:04+00:00"])
def test_outside_campaign_or_before_arrival_never_creates_entry(automatic, stamp):
    svc, clock, _, _ = automatic
    clock[0] = datetime.fromisoformat(stamp)
    svc.evaluate_automatic_practice()
    assert not svc._events and not svc._entries


def test_successful_auto_entry_uses_existing_binary_settlement_path(automatic):
    svc, clock, _, _ = automatic
    svc.evaluate_automatic_practice()
    market = deepcopy(svc._markets[DAY])
    for i, raw in enumerate(market["raw_markets"]):
        raw.update(status="finalized", result="yes" if i == 0 else "no", expiration_value="78",
                   settlement_value_dollars="1.0000" if i == 0 else "0.0000", settlement_ts=(CUTOFF+timedelta(hours=10)).isoformat())
    market.update(status="settled", updated_at_utc=(CUTOFF+timedelta(hours=11)).isoformat())
    svc._apply_settlements({DAY: market}, svc._market_bindings[DAY])
    svc._apply_settlements({DAY: market}, svc._market_bindings[DAY])
    assert len(svc._events) == 2
    assert svc._cash() == Decimal("105.01")
    assert svc.state()["trades"]["items"][0]["realized_pnl"] == 5.01
