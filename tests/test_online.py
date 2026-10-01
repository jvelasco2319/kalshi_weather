from datetime import date
from pathlib import Path

from kalshi_swarm.engine import score_record
from kalshi_swarm.live_report import render_live
from kalshi_swarm.public_data import live_record


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class Session:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []
    def get(self, url, params=None, timeout=30):
        self.calls.append((url, params, timeout))
        return Response(next(self.payloads))


def markets():
    rows = []
    bounds = [(None, 79, "less"), (79, 80, "between"), (81, 82, "between"), (83, 84, "between"), (85, 86, "between"), (86, None, "greater")]
    for index, (floor, cap, kind) in enumerate(bounds):
        rows.append({"ticker": f"KXHIGHLAX-26OCT01-B{index}", "event_ticker": "KXHIGHLAX-26OCT01", "title": f"Bracket {index}", "floor_strike": floor, "cap_strike": cap, "strike_type": kind, "yes_bid_dollars": "0.1000", "yes_ask_dollars": "0.1200", "no_bid_dollars": "0.8800", "no_ask_dollars": "0.9000", "updated_time": "2026-10-01T18:00:00Z"})
    return {"markets": rows, "cursor": ""}


def test_current_snapshot_uses_get_only_and_renders(tmp_path: Path):
    session = Session([
        markets(),
        {"hourly": {"temperature_2m": [79.0, 84.0, 83.0]}},
        {"daily": {"temperature_2m_max_member01": [82.0], "temperature_2m_max_member02": [85.0]}},
        [{"icaoId": "KLAX", "reportTime": "2026-10-01T18:00:00Z", "slp": 1010.0}, {"icaoId": "KDAG", "reportTime": "2026-10-01T18:00:00Z", "slp": 1007.0}],
    ])
    record = live_record(target=date(2026, 10, 1), session=session)
    scored = score_record(record)
    report = render_live({"record": record, "scored": scored}, tmp_path / "dashboard.html")
    assert len(record["quotes"]) == 6
    assert record["pressure_gradient_hpa"] == 3.0
    assert report.is_file()
    assert all(call[0].startswith("https://") for call in session.calls)


def test_online_modules_contain_no_order_transport():
    root = Path("src/kalshi_swarm")
    text = "\n".join((root / name).read_text(encoding="utf-8") for name in ("public_data.py", "online.py"))
    assert ".post(" not in text
    assert ".put(" not in text
    assert ".delete(" not in text
    assert "/orders" not in text
    assert "KALSHI-ACCESS-KEY" not in text
