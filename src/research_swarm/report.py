import html


def render(path, registration, state, ranked, accounting, candidate_states=None, resources=None, claims=()):
    rows, bars, seen = [], [], set()
    metric = registration["spec"].get("primary_metric", {"name": "Artifact-based findings"})["name"]
    direction = registration["spec"].get("primary_metric", {"direction": "No numerical ranking"})["direction"]
    values = [abs(r["result"].get("metrics", {}).get(metric, 0)) for r in ranked]
    scale = max(values, default=1) or 1
    distinct = 0
    for candidate in ranked:
        behavior = candidate["behavior_sha256"]
        duplicate = behavior in seen
        seen.add(behavior)
        distinct += int(candidate["all_gates_passed"] and not duplicate)
        value = candidate["result"].get("metrics", {}).get(metric)
        label = "ERROR" if candidate["error"] else "PASS" if candidate["all_gates_passed"] else "FAILED GATES"
        if duplicate:
            label += " · same behavior"
        color = "#42d3a6" if candidate["all_gates_passed"] else "#f6b35d"
        score = "unavailable" if value is None else f"{value:.5g}"
        bars.append(f'<div class="bar"><span>{html.escape(candidate["hypothesis"]["title"])}</span><i style="width:{0 if value is None else abs(value)/scale*100:.2f}%;background:{color}"></i><b>{score}</b></div>')
        axes = (candidate_states or {}).get(candidate["candidate_id"], {})
        statuses = ' / '.join(str(axes.get(key, 'pending')) for key in ('execution', 'research_outcome', 'review', 'verification', 'acceptance', 'applicability'))
        rows.append(f'<tr><td>{html.escape(candidate["hypothesis"]["colony"])}</td><td>{html.escape(candidate["candidate_id"][:24])}</td><td>{label}</td><td>{score}</td><td>{candidate["replication_verified"]}</td><td>{html.escape(statuses)}</td></tr>')
    colonies = ''.join(f'<tr><td>{html.escape(name)}</td><td>{details["status"]}</td><td>{details["resource_share"]:.1%}</td><td>{html.escape(str(details["parent_colony"] or "original"))}</td></tr>' for name, details in state['colonies'].items())
    claim_rows = ''.join(f'<tr><td>{html.escape(c["id"])}</td><td>{c["version"]}</td><td>{html.escape(c["payload"]["claim"])}</td><td>{html.escape(c["payload"]["axes"]["research_outcome"])}</td><td>{html.escape(c["payload"]["axes"]["acceptance"])}</td><td>{html.escape(c["applicability"])}</td></tr>' for c in claims)
    text = f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Research evidence</title><style>
body{{background:#0c1524;color:#e9f0f8;font:16px Segoe UI,Arial;margin:0}}main{{max-width:1100px;margin:auto;padding:32px}}section{{background:#152238;padding:20px;border-radius:14px;margin:16px 0}}.cards{{display:flex;gap:18px;flex-wrap:wrap}}.cards b{{font-size:28px;display:block}}.cards div{{flex:1;min-width:140px;background:#152238;padding:20px;border-radius:14px}}.bar{{margin:18px 0;display:grid;grid-template-columns:250px 1fr 90px;align-items:center;gap:12px}}.bar i{{display:block;height:18px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #2b3e57}}.note{{color:#f6b35d}}@media(max-width:750px){{.bar{{grid-template-columns:1fr}}}}</style></head><body><main>
<h1>{html.escape(registration['spec']['problem_id'])}</h1><p>{html.escape(registration['spec']['objective'])}</p><p class="note">{html.escape(registration['spec']['scope'])}</p>
<div class="cards"><div><b>{state['status']}</b>campaign state</div><div><b>{state['epoch']}</b>epochs</div><div><b>{state['attempt_count']}</b>attempts</div><div><b>{distinct}</b>distinct development passes</div></div>
<section><h2>{html.escape(metric)} · {html.escape(direction)}</h2>{''.join(bars) or 'No numerical experiment has run.'}</section>
<section><h2>Versioned claims</h2><table><tr><th>Claim ID</th><th>Version</th><th>Statement</th><th>Research outcome</th><th>Acceptance</th><th>Applicability</th></tr>{claim_rows or '<tr><td colspan="6">No claims submitted.</td></tr>'}</table></section>
<section><h2>Evidence ledger</h2><p>State order: execution / research outcome / review / verification / acceptance / applicability. Numerical reproduction has limited scope.</p><table><tr><th>Colony</th><th>Candidate</th><th>Gates</th><th>Metric</th><th>Reproduced</th><th>Separate states</th></tr>{''.join(rows)}</table></section>
<section><h2>Reserved resources</h2><p>{html.escape(str(resources or {}))}</p><p>Protected acceptance boundary: {html.escape(registration['spec']['runtime']['permission_boundary'])}. Provider calls are host-managed; these local records are not an automatic provider bill.</p></section>
<section><h2>Hourly reflection and resources</h2><p>Completed checkpoints: {state['reflection_count']}. Next: {state['next_reflection_at']}.</p><table><tr><th>Colony</th><th>Status</th><th>Experiment share</th><th>Parent</th></tr>{colonies}</table><p>Shares allocate finite experiment slots; they do not create more hosted agent capacity.</p></section>
<section><h2>Research boundary</h2><p>Catalog: {accounting['catalog_size']}; unexamined: {accounting['unexamined']}; exhaustive: {accounting['exhaustive']}.</p><p>Duplicate proposals skipped: {state['duplicates_skipped']}. Deadline: {registration['deadline']}.</p><p>Development passes require separate new confirmation. An agent task file is a request, not evidence that an agent worked. No automatic model calls occur in this runner.</p></section></main></body></html>'''
    path.write_text(text, encoding="utf-8")
