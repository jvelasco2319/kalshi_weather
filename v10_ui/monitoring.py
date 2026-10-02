"""Calendar views derived from saved forecasts and the practice journal."""
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def tomorrow_day(now):
    return (now.astimezone(PACIFIC).date()+timedelta(days=1)).isoformat()


def month_ids(now, config):
    if not config.get("date_start") or not config.get("date_end"):
        return []
    start = date.fromisoformat(config["date_start"]).replace(day=1)
    end = min(now.astimezone(PACIFIC).date().replace(day=1), date.fromisoformat(config["date_end"]).replace(day=1))
    result = []
    while start <= end:
        result.append(start.isoformat()[:7])
        start = (start+timedelta(days=32)).replace(day=1)
    return result


def month_progress(now, config, forecast_dates, scores, decisions, entries, settlements, *, target_month=None):
    today = date.fromisoformat(target_month+"-01") if target_month else now.astimezone(PACIFIC).date()
    dates = []
    if config.get("date_start") and config.get("date_end"):
        lower = max(today.replace(day=1), date.fromisoformat(config["date_start"]))
        upper = min(today.replace(day=calendar.monthrange(today.year, today.month)[1]), date.fromisoformat(config["date_end"]))
        dates = [(lower+timedelta(days=i)).isoformat() for i in range(max(0, (upper-lower).days+1))]
    scored = {r["date"]: r for r in scores if r["date"] in dates}
    auto_entries = {e["date"]: e for e in entries if e.get("origin") == "automatic" and e["date"] in dates}
    rows = []
    for day in dates:
        cutoff = datetime.fromisoformat(day+"T18:00:00+00:00")
        decision, trade, score = decisions.get(day), auto_entries.get(day), scored.get(day)
        settlement = settlements.get(trade["id"]) if trade else None
        pnl = Decimal(str(settlement["payout"]))-Decimal(trade["entry_cost"]) if settlement else None
        rows.append({"date": day, "forecast": "saved" if day in forecast_dates else "missing" if now > cutoff+timedelta(seconds=60) else "scheduled",
                     "practice": decision["status"].lower() if decision else "missing" if now > cutoff+timedelta(seconds=120) else "scheduled",
                     "reason": decision["reason"] if decision else None, "forecast_correct": score["correct"] if score else None,
                     "trade_settled": settlement is not None, "realized_practice_pnl": float(pnl) if pnl is not None else None})
    settled = [r for r in rows if r["trade_settled"]]
    return {"month_id": today.isoformat()[:7], "month": today.strftime("%B %Y"), "start_date": dates[0] if dates else None, "end_date": dates[-1] if dates else None,
            "planned_days": len(dates), "forecasts_saved": sum(r["forecast"] == "saved" for r in rows),
            "decisions_recorded": sum(r["practice"] in {"entered", "skipped"} for r in rows),
            "automatic_entries": len(auto_entries), "skipped_days": sum(r["practice"] == "skipped" for r in rows),
            "missing_forecasts": sum(r["forecast"] == "missing" for r in rows),
            "missing_practice_checks": sum(r["practice"] == "missing" for r in rows),
            "forecasts_settled": len(scored), "forecast_accuracy": sum(bool(r["correct"]) for r in scored.values())/len(scored) if scored else None,
            "automatic_entries_settled": len(settled), "realized_practice_pnl": sum(r["realized_practice_pnl"] for r in settled) if settled else None,
            "days": rows}
