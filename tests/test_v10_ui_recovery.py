"""Historical source boundaries, restart recovery and causal replay economics."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

import pytest

from v10_online import runner
from v10_online.markets import canonical_contracts
from v10_online.model import FrozenV10, validated_forecasts, FrozenModelError
from v10_online.storage import read_verified, write_immutable
from v10_ui import recovery as r
from test_v10_online_model import forecasts
from test_v10_ui_service import raw_markets

DAY = "2026-10-01"
NOW = datetime(2026, 10, 4, 14, tzinfo=timezone.utc)
PARAMETERS = {"minimum_probability_gap": .1, "minimum_expected_net_return": .1,
              "minimum_price_cents": 5, "maximum_price_cents": 80, "maximum_spread_cents": 5}


def prediction(day=DAY, values=None):
    contracts = canonical_contracts(raw_markets(day), day, "The Weather Company")
    return {"probabilities": values or [.6, .1, .09, .08, .07, .06],
            "tickers": [c["ticker"] for c in contracts], "model_bindings": {}, "pressure_state": "neutral"}


def bar(day=DAY, bid="0.50", ask="0.52", end=None):
    return {"end_period_ts": int(runner.cutoff(day).timestamp()) if end is None else end,
            "yes_bid": {"close_dollars": bid}, "yes_ask": {"close_dollars": ask}}


def final_markets(day=DAY, settled=None):
    markets = raw_markets(day)
    for i, m in enumerate(markets):
        m.update(status="settled", result="yes" if i == 0 else "no", settlement_value_dollars="1" if i == 0 else "0",
                 settlement_ts=settled or day+"T23:00:00+00:00", expiration_value="76")
    return markets


@pytest.mark.parametrize("suffix", ["/series/KXHIGHLAX/markets/", "/historical/markets/"])
def test_public_recovery_allowlist_exact_cutoff_only(suffix):
    cutoff = int(runner.cutoff(DAY).timestamp())
    url = r.KALSHI+suffix+"KXHIGHLAX-26OCT01-T79/candlesticks?start_ts="+str(cutoff-60)+"&end_ts="+str(cutoff)+"&period_interval=1"
    r.recovery_url(url)
    for changed in (url.replace(str(cutoff), str(cutoff+60)), url.replace("period_interval=1", "period_interval=60"),
                    url.replace("KXHIGHLAX", "KXHIGHNY"), url+"&include_latest_before_start=true",
                    url.replace("https://", "https://user:password@"), url+"#fragment"):
        with pytest.raises(ValueError):
            r.recovery_url(changed)
    r.recovery_url(r.KALSHI+"/historical/markets?event_ticker=KXHIGHLAX-26OCT01&limit=100")
    for path in ("/portfolio/orders", "/historical/markets?series_ticker=KXHIGHLAX", "/historical/markets?event_ticker=KXHIGHLAX-26OCT01&limit=1000"):
        with pytest.raises(ValueError):
            r.recovery_url(r.KALSHI+path)


def test_candle_requires_exact_bar_close_and_never_invents_size():
    ticker = prediction()["tickers"][0]
    quote = r.cutoff_quote({"ticker": ticker, "candlesticks": [bar()]}, ticker, DAY)
    assert Decimal(quote["no_ask"]) == Decimal(".50")
    assert Decimal(quote["no_bid"]) == Decimal(".48")
    assert quote["no_ask_size"] is None
    for bars in ([bar(end=int(runner.cutoff(DAY).timestamp())+60)], [bar(), bar()], [bar(bid=None)], [bar(bid=".60", ask=".50")], []):
        with pytest.raises((ValueError, TypeError)):
            r.cutoff_quote({"candlesticks": bars}, ticker, DAY)


def test_estimate_selector_is_no_only_and_missing_evidence_is_not_a_skip():
    p = prediction()
    quotes = [r.cutoff_quote({"candlesticks": [bar()]}, t, DAY) for t in p["tickers"]]
    series = {"fee_type": "quadratic", "fee_multiplier": 1}
    chosen = r.estimate_selection(p, quotes, series, PARAMETERS)
    assert chosen["side"] == "no" and chosen["status"] == "ESTIMATED_ENTRY"
    assert chosen["ticker"] == p["tickers"][-1]
    assert r.estimate_selection(p, quotes[:-1], series, PARAMETERS)["status"] == "UNAVAILABLE"
    assert r.estimate_selection(prediction(values=[.3, .25, .2, .1, .1, .05]), [], series, PARAMETERS)["status"] == "SKIPPED"
    for q in quotes:
        q.update(no_ask=".501", no_bid=".48")
    assert r.estimate_selection(p, quotes, series, PARAMETERS)["status"] == "SKIPPED"


def test_settlement_validates_partition_rules_payouts_times_and_scores():
    markets = final_markets()
    contracts = canonical_contracts(markets, DAY, "The Weather Company")
    saved = {"date": DAY, "contracts": contracts, "settlement_source": "The Weather Company",
             "prediction": prediction(), "labels": [r.label(c) for c in contracts]}
    score = r.score_settlement(markets, saved, NOW.isoformat())
    assert score["correct"] and score["reported_high_f"] == 76
    assert score["brier"] == pytest.approx(.193)
    pending = deepcopy(markets); pending[0]["status"] = "closed"
    assert r.score_settlement(pending, saved, NOW.isoformat()) is None
    for field, value in (("result", "yes"), ("settlement_value_dollars", ".50"),
                         ("settlement_ts", "2026-10-01T17:59:00Z"), ("expiration_value", "77"),
                         ("rules_secondary", "changed rules")):
        changed = deepcopy(markets); changed[1][field] = value
        with pytest.raises(ValueError):
            r.score_settlement(changed, saved, NOW.isoformat())


def saved_day(day, settled=None):
    p = prediction(day)
    contracts = canonical_contracts(raw_markets(day), day, "The Weather Company")
    forecast = {"date": day, "prediction": p, "labels": [r.label(c) for c in contracts],
                "recovered_at_utc": NOW.isoformat()}
    return {"forecast": forecast,
            "trade": {"status": "ESTIMATED_ENTRY", "reason": "Assumed entry", "ticker": p["tickers"][-1],
                      "entry_price": ".50", "fee_multiplier": "1"},
            "settlement": {"settled_at_utc": settled or day+"T23:00:00+00:00", "results": {t: "yes" if i == 0 else "no" for i, t in enumerate(p["tickers"])},
                           "correct": True, "official_range": forecast["labels"][0], "reported_high_f": 76,
                           "brier": .2, "log_loss": .5}}


def test_multiple_days_cash_waits_for_settlement_and_stake_includes_fee():
    days = {day: saved_day(day) for day in (DAY, "2026-10-02", "2026-10-03")}
    days[DAY]["settlement"]["settled_at_utc"] = "2026-10-03T23:30:00Z"
    result = r.portfolio(days, list(days))
    rows = result["days"]
    assert rows[0]["quantity"] == 19 and rows[0]["entry_cost"] == 9.84
    assert rows[1]["budget"] == pytest.approx(9.016)  # First payout not yet available.
    assert rows[2]["budget"] == pytest.approx(9.836)  # Second payout is available.
    assert all(row["entry_cost"] <= row["budget"] for row in rows)
    assert result["summary"]["cash"] == pytest.approx(100+result["summary"]["estimated_pnl"])
    assert result["history"][-1]["value"] == result["summary"]["estimated_pnl"]
    missing = r.portfolio(days, [DAY, "2026-10-02", "2026-10-03", "2026-10-04"])
    assert missing["summary"]["incomplete"] and missing["summary"]["unknown_days"] == 1


class Weights:
    def predict(self, day, contracts, rows, observations):
        assert all("result" not in c and "expiration_value" not in c for c in contracts)
        validated_forecasts(day, rows)
        return prediction(day)


class Client:
    missing_quotes = False
    pending = False
    calls = []
    def __init__(self, archive):
        self.archive, self.receipts, self.requests, self.bytes = archive, [], 0, 0

    def fetch(self, *args, **kwargs):
        raise AssertionError("Observation collection is injected in these tests")

    def get_json(self, url):
        r.recovery_url(url)
        type(self).calls.append(url)
        if "/candlesticks?" in url:
            value = {"candlesticks": [] if self.missing_quotes else [bar()]}
        elif "/series/" in url:
            value = {"series": {"ticker": "KXHIGHLAX", "fee_type": "quadratic", "fee_multiplier": 1}}
        else:
            markets = final_markets()
            if self.pending:
                markets[0]["status"] = "closed"
            value = {"markets": markets}
        raw = json.dumps(value).encode()
        self.archive.mkdir(parents=True, exist_ok=True)
        self.requests += 1
        name = f"{self.requests}.raw"; (self.archive/name).write_bytes(raw)
        receipt = write_immutable(self.archive/(name+".json"), {"raw_path": name, "sha256": sha256(raw).hexdigest(),
                      "url": url, "retrieved_at_utc": NOW.isoformat(), "method": "GET", "credentials_used": False})
        self.receipts.append(receipt)
        return value


@pytest.fixture
def store(tmp_path, monkeypatch):
    Client.missing_quotes, Client.pending, Client.calls = False, False, []
    monkeypatch.setattr(r.practice, "policy", lambda root: (PARAMETERS, {}))
    monkeypatch.setattr(r.weather, "collect_observations", lambda *args: {"observations": []})
    monkeypatch.setattr(r.RecoveryStore, "_forecasts", lambda self, day, folder, client: (forecasts(day), {}))
    config = {"date_start": "2026-10-02", "date_end": "2026-10-31"}
    return r.RecoveryStore(tmp_path, config, now=lambda: NOW, client_factory=Client, model_factory=lambda root: Weights())


def test_recovery_separates_live_test_and_journal_and_reuses_saved_forecast(store, tmp_path):
    store.recover_day(DAY)
    path = tmp_path/r.RUN/DAY/"forecast.json"
    first = read_verified(path)
    assert first["prospective_eligible"] is False and first["orders"] == 0
    assert first["recovered_at_utc"] > first["decision_at_utc"]
    assert not (tmp_path/runner.RUN/"daily"/DAY).exists()
    assert not (tmp_path/"runs/v10_ui/journal").exists()
    restart = r.RecoveryStore(tmp_path, store.config, now=lambda: NOW, client_factory=Client, model_factory=lambda root: Weights())
    calls = len(Client.calls)
    restart.recover_day(DAY)
    assert len(Client.calls) == calls and read_verified(path) == first
    assert restart.state()["days"][0]["estimated_pnl"] == pytest.approx(9.16)
    assert all("portfolio" not in url for url in Client.calls)


def test_missing_quotes_retry_without_recomputing_forecast_or_inventing_trade(store, tmp_path):
    Client.missing_quotes = True
    store.recover_day(DAY)
    path = tmp_path/r.RUN/DAY/"forecast.json"
    first = read_verified(path)
    assert not path.with_name("trade.json").exists()
    assert store.state()["days"][0]["trade_status"] == "UNAVAILABLE"
    assert store.state()["days"][0]["correct"] is True
    Client.missing_quotes = False
    store.recover_day(DAY)
    assert read_verified(path) == first and path.with_name("trade.json").exists()


def test_pending_official_results_are_rechecked_only_after_forecast_saved(store, tmp_path):
    Client.pending = True
    store.recover_day(DAY)
    path = tmp_path/r.RUN/DAY/"forecast.json"
    assert store.state()["days"][0]["correct"] is None
    assert not path.with_name("settlement.json").exists()
    Client.pending = False
    store.recover_day(DAY)
    assert store.state()["days"][0]["correct"] is True


def test_recovery_scope_ends_yesterday_in_pacific_and_pauses_at_live_cutoff(store):
    assert store.dates() == [DAY, "2026-10-02", "2026-10-03"]
    with pytest.raises(ValueError):
        store.recover_day("2026-10-04")
    store._now = lambda: datetime(2026, 10, 4, 18, tzinfo=timezone.utc)
    called = []
    store.recover_day = lambda day: called.append(day)
    store._work([DAY])
    assert called == [] and "paused" in store.state()["message"]


def test_atomic_publication_preserves_prior_evidence_and_ignores_partial_writes(tmp_path):
    path = tmp_path/"day"/"forecast.json"
    first = r._save(path, {"value": 1})
    with pytest.raises(FileExistsError):
        r._save(path, {"value": 2})
    assert read_verified(path) == first
    assert list(path.parent.glob("*.partial"))


def test_background_worker_keeps_state_responsive_and_duplicate_refresh_single(store):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def recover(day):
        calls.append(day)
        started.set()
        release.wait(3)
        if len(calls) == 3:
            finished.set()
    store.recover_day = recover
    store.start()
    assert started.wait(2)
    assert store.state()["busy"] and store.state()["current_date"] == DAY
    store.start()
    assert calls == [DAY]
    release.set()
    assert finished.wait(3)


def test_close_interrupts_recovery_without_starting_another_date(store):
    called = []
    def recover(day):
        called.append(day)
        store.close()
    store.recover_day = recover
    store._work(store.dates())
    assert called == [DAY] and store.state()["busy"] is False


def test_reuses_bound_live_inputs_and_requires_exact_original_prediction(store, tmp_path, monkeypatch):
    contracts = canonical_contracts(raw_markets(DAY), DAY, "The Weather Company")
    folder = tmp_path/runner.RUN/"daily"/DAY
    stage_path = folder/"staging/fixture/stage.json"
    registration = {"self_sha256": "registered", "created_at_utc": "2026-09-30T00:00:00Z",
                    "config": {"date_start": DAY, "date_end": "2026-10-31", "maximum_publication_latency_seconds": 60}}
    monkeypatch.setattr(r.runner, "registration", lambda root: registration)
    stage = write_immutable(stage_path, {"climate_date": DAY, "status": "STAGED", "registration_seal": "registered",
                                       "contracts": contracts, "weather": {"forecasts": forecasts(DAY)}, "raw_bindings": {}})
    primary_path = folder/"prediction.json"
    primary = write_immutable(primary_path, {"climate_date": DAY, "status": "FROZEN_PROSPECTIVE_FORECAST",
        "prospective_eligible": True, "outcomes_read": False, "orders": 0, "registration_seal": "registered",
        "decision_at_utc": runner.cutoff(DAY).isoformat(), "published_at_utc": DAY+"T18:00:01Z",
        "market": {"contracts": contracts}, "observations": {"observations": []}, "prediction": prediction(),
        "raw_bindings": {stage_path.relative_to(tmp_path).as_posix(): r.hash_file(stage_path)}})
    monkeypatch.setattr(store, "_forecasts", lambda *args: pytest.fail("Live forecast weather downloaded again"))
    monkeypatch.setattr(r.weather, "collect_observations", lambda *args: pytest.fail("Live pressure inputs replaced"))
    store.recover_day(DAY)
    replay = read_verified(tmp_path/r.RUN/DAY/"forecast.json")
    assert replay["prediction"] == primary["prediction"] and replay["input_basis"] == "SAVED_LIVE_INPUTS"
    assert read_verified(primary_path) == primary
    assert primary_path.relative_to(tmp_path).as_posix() in replay["raw_bindings"]


def test_invalid_live_forecast_cannot_be_replaced_with_a_hindsight_capture(store, tmp_path, monkeypatch):
    path = tmp_path/runner.RUN/"daily"/DAY/"prediction.json"
    write_immutable(path, {"status": "DIAGNOSTIC_PREVIEW_EXCLUDED", "raw_bindings": {}})
    monkeypatch.setattr(r.runner, "registration", lambda root: {"created_at_utc": "2026-09-30T00:00:00Z", "self_sha256": "fixture",
        "config": {"date_start": DAY, "date_end": "2026-10-31"}})
    with pytest.raises(ValueError, match="admitted"):
        store.recover_day(DAY)
    assert not (tmp_path/r.RUN/DAY/"forecast.json").exists()


def test_fixed_historical_inputs_match_frozen_model_and_future_availability_fails():
    root = Path(__file__).resolve().parents[1]
    model = FrozenV10.load(root)
    contracts = canonical_contracts(raw_markets(DAY), DAY, "The Weather Company")
    inputs = forecasts(DAY)
    result = model.predict(DAY, contracts, inputs, [])
    assert result == model.predict(DAY, contracts, deepcopy(inputs), [])
    assert result["target_updates_used"] is False and result["outcomes_read"] is False
    inputs[0]["information_available_at_utc"] = DAY+"T18:01:00Z"
    with pytest.raises(FrozenModelError):
        model.predict(DAY, contracts, inputs, [])


def test_temperature_archive_cache_resumes_after_partial_download(tmp_path):
    from test_v10_ui_monitoring_forecast import Client as WeatherClient
    class Interrupted(WeatherClient):
        def fetch(self, *args, **kwargs):
            if self.requests >= 4:
                raise ValueError("interrupted")
            return super().fetch(*args, **kwargs)
    config = {"date_start": "2026-10-02", "date_end": "2026-10-31"}
    store = r.RecoveryStore(tmp_path, config, now=lambda: NOW)
    folder = tmp_path/r.RUN/DAY
    first = Interrupted(folder/"attempts/first/raw")
    with pytest.raises(ValueError):
        store._forecasts(DAY, folder, first)
    assert len(list((folder/"inputs").glob("*.json"))) == 2
    later = WeatherClient(folder/"attempts/later/raw")
    rows, bindings = store._forecasts(DAY, folder, later)
    assert len(rows) == 20 and later.requests == 36
    assert len(validated_forecasts(DAY, rows)) == 20
    runner.verify_raw(tmp_path, bindings)
    for row in rows:
        assert row["source_retrieved_at_utc"] > row["information_available_at_utc"]
        assert row["historical_availability_proven"] is False
