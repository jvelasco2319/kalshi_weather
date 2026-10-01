from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .report import COLORS


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def render_live(snapshot: dict[str, Any], output: str | Path) -> Path:
    record, scored = snapshot["record"], snapshot["scored"]
    live = record["live"]
    weather = live["weather"]
    cards, rows = [], []
    for name in ("V5B", "V8", "V10"):
        model = scored["models"][name]
        decision = model["decision"]
        cards.append(f'''<article><span style="background:{COLORS[name]}">{name}</span><strong>{html.escape(decision.get('status',''))}</strong><h3>{html.escape(str(decision.get('label') or 'No trade'))}</h3><p>Expected return {_pct(decision.get('expected_net_return'))}<br>Model win probability {_pct(decision.get('model_probability'))}</p></article>''')
        bars = "".join(f'<i style="height:{max(2,value*220):.1f}px;background:{COLORS[name]}"><em>{value*100:.0f}%</em></i>' for value in model["probabilities"])
        rows.append(f'<div class="prob"><b>{name}</b><div>{bars}</div></div>')
    quote_rows = "".join(f"<tr><td>{q['bracket_index']+1}</td><td>{html.escape(str(q['ticker']))}</td><td>{q.get('yes_bid_cents')} / {q.get('yes_ask_cents')}</td><td>{q.get('no_bid_cents')} / {q.get('no_ask_cents')}</td><td>{html.escape(str(q.get('quote_captured_at_utc') or '—'))}</td></tr>" for q in record["quotes"])
    timing = "ON-TIME TEST SNAPSHOT" if live["within_five_minutes_of_frozen_decision_time"] else "OBSERVATION OUTSIDE 18:00 UTC TEST WINDOW"
    page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="refresh" content="60"><title>KLAX Live Paper Snapshot</title><style>
body{{margin:0;background:#09111f;color:#eef3f8;font:15px Segoe UI,Arial}}main{{max-width:1180px;margin:auto;padding:28px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:end}}h1{{margin:0;font-size:32px}}.status{{color:#f2b84b;font-weight:800}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:22px 0}}article,section{{background:#111d2e;border:1px solid #26354a;border-radius:15px;padding:18px}}article span{{color:#07101b;font-weight:900;border-radius:99px;padding:5px 10px}}article strong{{display:block;font-size:25px;margin-top:15px}}.prob{{display:grid;grid-template-columns:55px 1fr;align-items:end;margin:20px 0}}.prob>div{{display:grid;grid-template-columns:repeat(6,1fr);height:225px;gap:8px;align-items:end;border-bottom:1px solid #52647c}}.prob i{{display:block;position:relative;border-radius:7px 7px 0 0}}.prob em{{position:absolute;top:-20px;width:100%;text-align:center;font-style:normal;font-size:12px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #26354a;text-align:left}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.warn{{color:#f2b84b}}@media(max-width:760px){{.cards,.grid{{grid-template-columns:1fr}}}}</style></head><body><main>
<header><div><h1>KLAX current-data paper test</h1><p>{record['date']} · {live['market']['event_ticker']} · captured {live['captured_at_utc']}</p></div><div class="status">{timing}</div></header>
<p class="warn">READ-ONLY: this program has no order endpoint, credentials, or order-writing code.</p><div class="cards">{''.join(cards)}</div>
<div class="grid"><section><h2>Current weather signal</h2><p>HRRR high <b>{weather['hrrr_high_f']:.1f}°F</b><br>GEFS mean <b>{weather['gefs_mean_high_f']:.1f}°F</b> across {weather['gefs_member_count']} members<br>Disagreement <b>{weather['hrrr_gefs_disagreement_f']:+.1f}°F</b><br>Pressure gradient <b>{record.get('pressure_gradient_hpa')}</b> hPa · {scored['pressure_state']}</p><small>{html.escape(weather['base_probability_method'])}</small></section>
<section><h2>Test controls</h2><p>Frozen decision time: 18:00 UTC<br>Selector: NO only, 5–80¢, spread ≤5¢, return ≥10%<br>Maximum one paper candidate per model<br>Orders attempted: 0</p></section></div>
<section><h2>Six-bracket probabilities</h2>{''.join(rows)}</section><section><h2>Kalshi top of book</h2><table><thead><tr><th>#</th><th>Contract</th><th>YES bid/ask</th><th>NO bid/ask</th><th>Captured UTC</th></tr></thead><tbody>{quote_rows}</tbody></table></section>
</main></body></html>'''
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target
