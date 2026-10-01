from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


COLORS = {"V5B": "#7d8ca3", "V8": "#28b5a6", "V10": "#f2b84b"}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def render_report(result: dict[str, Any], output: str | Path, *, title: str = "KLAX Three-Model Test") -> Path:
    summaries = result["summaries"]
    cards = []
    bars = []
    for name in ("V5B", "V8", "V10"):
        row = summaries[name]
        cards.append(f'''<article class="card"><span class="tag" style="background:{COLORS[name]}">{name}</span>
<strong>{_pct(row['aggregate_realized_return'])}</strong><small>realized return</small>
<div>{row['selected_trades']} trades · {_pct(row['win_rate'])} wins</div>
<div>{_money(row['net_profit_dollars'])} net · Brier {row['mean_brier']:.3f}</div></article>''')
        width = max(0.0, min(100.0, (row["aggregate_realized_return"] or 0.0) * 100.0 + 50.0))
        bars.append(f'<div class="barrow"><b>{name}</b><div class="track"><i style="width:{width:.1f}%;background:{COLORS[name]}"></i></div><span>{_pct(row["aggregate_realized_return"])}</span></div>')
    latest = result["days"][-1]
    latest_rows = []
    for name in ("V5B", "V8", "V10"):
        d = latest["models"][name]["decision"]
        latest_rows.append(f"<tr><td>{name}</td><td>{html.escape(str(d.get('status')))}</td><td>{html.escape(str(d.get('label') or '—'))}</td><td>{_pct(d.get('expected_net_return'))}</td><td>{html.escape(str(d.get('evidence_grade') or '—'))}</td></tr>")
    payload = html.escape(json.dumps({"ranking": result["ranking"], "schema_version": result["schema_version"]}, indent=2))
    page = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title><style>
body{{margin:0;background:#09111f;color:#eef3f8;font:15px Segoe UI,Arial,sans-serif}}main{{max-width:1120px;margin:auto;padding:32px}}h1{{font-size:32px;margin:0}}.sub{{color:#9fb0c5;margin:8px 0 24px}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.card{{background:#111d2e;border:1px solid #26354a;border-radius:16px;padding:20px}}.tag{{color:#08101b;font-weight:800;border-radius:99px;padding:5px 10px}}.card strong{{display:block;font-size:34px;margin-top:18px}}small{{display:block;color:#9fb0c5;margin-bottom:18px}}section{{background:#111d2e;border:1px solid #26354a;border-radius:16px;padding:20px;margin-top:16px}}.barrow{{display:grid;grid-template-columns:50px 1fr 80px;gap:12px;align-items:center;margin:14px 0}}.track{{height:18px;background:#26354a;border-radius:9px;overflow:hidden}}.track i{{display:block;height:100%}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #26354a;text-align:left}}.note{{color:#f2b84b}}code{{color:#9bdcd5}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p class="sub">{len(result['days'])} dates · ranked {html.escape(' → '.join(result['ranking']))}</p>
<div class="grid">{''.join(cards)}</div><section><h2>Return comparison</h2>{''.join(bars)}</section>
<section><h2>Latest decision · {html.escape(latest['date'])}</h2><table><thead><tr><th>Model</th><th>Action</th><th>Contract</th><th>Expected return</th><th>Evidence</th></tr></thead><tbody>{''.join(latest_rows)}</tbody></table></section>
<section><h2>What to watch before online testing</h2><p class="note">Data time, model disagreement, pressure regime, spread, fee-adjusted edge, evidence grade, sample size, cumulative profit and drawdown.</p><pre>{payload}</pre></section>
</main></body></html>'''
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target

