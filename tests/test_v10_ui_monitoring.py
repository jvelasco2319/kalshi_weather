"""Tomorrow listings, Pacific rollover and a month of saved evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from v10_ui import service as ui
from v10_ui.monitoring import tomorrow_day, month_progress, month_ids
from test_v10_ui_service import dashboard, ArchivedFixtureClient, DAY, NOW


class TomorrowClient(ArchivedFixtureClient):
    market_day = "2026-10-02"


class UnlistedClient(TomorrowClient):
    listed = False


def test_future_market_saved_separately_with_all_receipts_and_reloaded(dashboard):
    dashboard._client_factory = ArchivedFixtureClient
    dashboard.refresh()
    current = deepcopy(dashboard._markets[DAY])
    weather = deepcopy(dashboard._observed)
    dashboard._now = lambda: NOW+timedelta(seconds=1)
    dashboard._client_factory = TomorrowClient
    result = dashboard.refresh_tomorrow()
    assert result["tomorrow_market"]["date"] == "2026-10-02"
    assert result["tomorrow_market"]["status"] == "open"
    assert len(result["tomorrow_market"]["quotes"]) == 6
    assert result["tomorrow_market"]["quotes"][0]["yes_ask"] == .37
    assert dashboard._markets[DAY] == current and dashboard._observed == weather
    assert not dashboard._events and result["orders"] == 0
    saved = max((ui.read_verified(p) for p in (dashboard.root/ui.UI_RUN/"snapshots").glob("*/snapshot.json")), key=lambda r:r["created_at_utc"])
    assert saved["source_part"] == "tomorrow" and saved["request_count"] == 8
    assert set(saved["markets"]) == {DAY, "2026-10-02"}
    assert len(saved["raw_bindings"]) == 34
    ui.runner.verify_raw(dashboard.root, saved["raw_bindings"])
    # A later today-only snapshot must keep tomorrow's original receipts too.
    dashboard._now = lambda: NOW+timedelta(seconds=30)
    dashboard._client_factory = ArchivedFixtureClient
    dashboard.refresh_market()
    restored = ui.DashboardService(dashboard.root, now=dashboard._now, registration_check=dashboard._registration_check)
    assert len(restored.state()["tomorrow_market"]["quotes"]) == 6
    assert restored.state()["tomorrow_market"]["updated_at_utc"] == NOW.isoformat()


def test_not_yet_listed_is_an_archived_empty_check_not_an_error(dashboard):
    dashboard._client_factory = UnlistedClient
    state = dashboard.refresh_tomorrow()
    assert state["tomorrow_market"]["status"] == "not_listed"
    assert state["tomorrow_market"]["quotes"] == []
    saved = ui.read_verified(next((dashboard.root/ui.UI_RUN/"snapshots").glob("*/snapshot.json")))
    assert saved["request_count"] == 2
    assert len(saved["market_raw_bindings"]["2026-10-02"]) == 4
    assert dashboard._markets[DAY]["status"] == "open"


def test_midnight_promotes_right_date_without_relabeling_forecasts(dashboard):
    dashboard._client_factory = TomorrowClient
    dashboard.refresh_tomorrow()
    dashboard._forecast = {"kind": "preview", "date": DAY, "probabilities": [1,0,0,0,0,0]}
    dashboard._now = lambda: datetime(2026,10,2,7,tzinfo=timezone.utc)
    state = dashboard.state()
    assert state["date"] == "2026-10-02"
    assert state["market"]["event_ticker"] == "KXHIGHLAX-26OCT02"
    assert not state["market"]["quotes"][0]["fresh"]
    assert not state["market"]["quotes"][0]["practice_estimates"]["no"]["available"]
    assert state["tomorrow_market"]["date"] == "2026-10-03" and state["tomorrow_market"]["quotes"] == []
    assert state["forecast"]["kind"] == "none" and state["weather"]["observations"] == []
    assert state["month"]["planned_days"] == 30


@pytest.mark.parametrize("stamp,target", [
    ("2026-10-02T06:59:59+00:00", "2026-10-02"),
    ("2026-10-02T07:00:00+00:00", "2026-10-03"),
    ("2026-11-01T06:59:59+00:00", "2026-11-01"),
    ("2026-11-01T07:00:00+00:00", "2026-11-02"),
    ("2026-11-02T07:59:59+00:00", "2026-11-02"),
    ("2026-11-02T08:00:00+00:00", "2026-11-03"),
    ("2027-01-01T09:00:00+00:00", "2027-01-02"),
])
def test_tomorrow_uses_pacific_calendar_including_dst(stamp,target):
    assert tomorrow_day(datetime.fromisoformat(stamp)) == target


def test_month_counts_forecast_scores_and_fake_profit_separately():
    config = {"date_start":"2026-10-02", "date_end":"2027-01-09"}
    now = datetime(2026,10,5,20,tzinfo=timezone.utc)
    decisions = {"2026-10-02":{"status":"ENTERED","reason":"Eligible"}, "2026-10-03":{"status":"SKIPPED","reason":"Low confidence"}}
    entries = [{"id":"auto","date":"2026-10-02","origin":"automatic","entry_cost":"9.99"},
               {"id":"manual-practice","date":"2026-10-03","entry_cost":"8"}]
    scores = [{"date":"2026-10-02","correct":False}, {"date":"2026-10-03","correct":True}]
    result = month_progress(now,config,{"2026-10-02","2026-10-03"},scores,decisions,entries,{"auto":{"payout":15},"manual-practice":{"payout":0}})
    assert result["planned_days"] == 30 and result["decisions_recorded"] == 2
    assert result["forecasts_saved"] == 2 and result["forecasts_settled"] == 2
    assert result["automatic_entries"] == result["skipped_days"] == 1
    assert result["missing_forecasts"] == result["missing_practice_checks"] == 2
    assert result["forecast_accuracy"] == .5
    assert result["realized_practice_pnl"] == 5.01  # Excludes the other practice loss.
    assert result["days"][0]["forecast_correct"] is False
    assert result["days"][0]["realized_practice_pnl"] == 5.01
    assert result["days"][-1]["forecast"] == "scheduled"


@pytest.mark.parametrize("stamp,planned", [("2026-10-01T22:00:00+00:00",30), ("2026-11-02T20:00:00+00:00",30), ("2027-01-02T20:00:00+00:00",9), ("2027-02-02T20:00:00+00:00",0)])
def test_calendar_month_respects_unchanged_campaign_window(stamp,planned):
    result = month_progress(datetime.fromisoformat(stamp),{"date_start":"2026-10-02","date_end":"2027-01-09"},set(),[],{},[],{})
    assert result["planned_days"] == planned
    assert result["forecast_accuracy"] is None and result["realized_practice_pnl"] is None


def test_entire_october_has_one_row_per_day_and_no_missing_checks():
    dates = {f"2026-10-{d:02d}" for d in range(2,32)}
    decisions = {d:{"status":"SKIPPED","reason":"No eligible trade"} for d in dates}
    result = month_progress(datetime(2026,11,1,6,tzinfo=timezone.utc),{"date_start":"2026-10-02","date_end":"2027-01-09"},dates,[],decisions,[],{})
    assert result["month"] == "October 2026"
    assert result["forecasts_saved"] == result["decisions_recorded"] == result["skipped_days"] == 30
    assert result["missing_forecasts"] == result["missing_practice_checks"] == 0
    assert len({r["date"] for r in result["days"]}) == 30


def test_october_remains_inspectable_and_counts_later_settlement_in_november():
    now = datetime(2026,11,2,20,tzinfo=timezone.utc)
    config = {"date_start":"2026-10-02","date_end":"2027-01-09"}
    assert month_ids(now,config) == ["2026-10","2026-11"]
    result = month_progress(now,config,{"2026-10-31"},[],{"2026-10-31":{"status":"ENTERED","reason":"Eligible"}},
                            [{"id":"last","date":"2026-10-31","origin":"automatic","entry_cost":"9.99"}],
                            {"last":{"payout":15}},target_month="2026-10")
    assert result["month"] == "October 2026" and result["realized_practice_pnl"] == 5.01
    assert month_ids(datetime(2027,2,2,20,tzinfo=timezone.utc),config) == ["2026-10","2026-11","2026-12","2027-01"]
