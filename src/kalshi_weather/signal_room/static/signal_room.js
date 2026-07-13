(function () {
  const MODEL_ORDER = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"];
  const MODEL_LABELS = {
    ecmwf_ifs: "ECMWF IFS",
    gfs013: "GFS 0.13",
    gfs_seamless: "GFS Seamless",
    nam: "NAM",
    nbm: "NBM",
  };
  const MODEL_COLORS = {
    ecmwf_ifs: "#5B8FF9",
    gfs013: "#F6BD16",
    gfs_seamless: "#E8684A",
    nam: "#6AA84F",
    nbm: "#C66DD4",
  };
  const root = document.body;
  const state = {
    paused: false,
    etag: null,
    snapshot: null,
    timeline: [],
    eventTicker: null,
    failureAt: null,
  };

  function $(selector) { return document.querySelector(selector); }
  function text(selector, value) { const node = $(selector); if (node) node.textContent = value; }
  function fmtTemp(value) { return value === null || value === undefined ? "--" : `${Number(value).toFixed(1)} F`; }
  function fmtPct(value, digits) { return value === null || value === undefined ? "Unavailable" : `${(Number(value) * 100).toFixed(digits)}%`; }
  function fmtMoney(value) { return value === null || value === undefined ? "--" : value; }
  function svgText(value) {
    return String(value ?? "").replace(/[&<>"]/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
    }[char]));
  }
  function asNumber(value) {
    const numeric = Number(String(value ?? "").replace(/[$,%]/g, ""));
    return Number.isFinite(numeric) ? numeric : null;
  }
  function pctNumber(value) {
    const numeric = asNumber(value);
    return numeric === null ? null : numeric / 100;
  }
  function fmtTime(value) {
    if (!value) return "--";
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Los_Angeles",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZoneName: "short",
    }).format(new Date(value));
  }

  async function loadEvents() {
    const res = await fetch("/api/v1/signal-room/events");
    const events = await res.json();
    const select = $("#eventSelect");
    select.replaceChildren();
    events.forEach((event) => {
      const option = document.createElement("option");
      option.value = event.ticker;
      option.textContent = `${event.ticker} - ${event.target_date}`;
      select.appendChild(option);
    });
    state.eventTicker = events[0] ? events[0].ticker : "auto";
  }

  async function loadTimeline() {
    if (!state.eventTicker) return;
    const res = await fetch(`/api/v1/signal-room/events/${encodeURIComponent(state.eventTicker)}/timeline?limit=100`);
    if (!res.ok) throw new Error(`Timeline request failed: ${res.status}`);
    state.timeline = await res.json();
    const range = $("#timeRange");
    range.max = String(Math.max(0, state.timeline.length - 1));
    if (Number(range.value) > Number(range.max)) range.value = range.max;
  }

  async function loadSnapshot(asOf) {
    if (!state.eventTicker) return;
    const params = new URLSearchParams();
    if (asOf) params.set("as_of", asOf);
    const headers = {};
    if (state.etag && !asOf) headers["If-None-Match"] = state.etag;
    const url = `/api/v1/signal-room/events/${encodeURIComponent(state.eventTicker)}/snapshot${params.toString() ? `?${params}` : ""}`;
    const res = await fetch(url, { headers });
    if (res.status === 304) return false;
    if (!res.ok) throw new Error(`Snapshot request failed: ${res.status}`);
    state.etag = res.headers.get("ETag");
    state.snapshot = await res.json();
    state.failureAt = null;
    render(state.snapshot);
    return true;
  }

  function render(snapshot) {
    const banner = $("#banner");
    if (snapshot.banner || snapshot.sample_mode || snapshot.replay_mode) {
      banner.hidden = false;
      banner.textContent = snapshot.banner || "Historical replay. Settlement truth is visually isolated from decision-time fields.";
    } else {
      banner.hidden = true;
    }
    text("#subtitle", `${snapshot.event.ticker} - ${snapshot.event.target_date} - ${snapshot.event.station}`);
    text("#modeChip", snapshot.strategy.mode.toUpperCase() + " MODE");
    text("#orderPathChip", snapshot.strategy.order_submission_reachable ? "order path reachable" : "order path disabled");
    text("#commandEvalId", ((snapshot.probability_lab || {}).evaluation_id || snapshot.revision || "--").slice(0, 8));
    text("#latestEval", fmtTime(snapshot.decision.evaluated_at));
    text("#decisionState", snapshot.decision.status.replaceAll("_", " "));
    $("#decisionState").className = `decision-state ${decisionClass(snapshot.decision.status)}`;
    text("#decisionReason", snapshot.decision.reason_text);
    text("#reasonCode", snapshot.decision.reason_code);
    text("#focusContract", snapshot.decision.focus_bracket ? `${snapshot.decision.focus_bracket} ${snapshot.decision.focus_side || ""}` : "No eligible contract");
    text("#focusAsk", fmtMoney(snapshot.decision.executable_price));
    text("#spreadV", fmtTemp(snapshot.risk.model_spread_f));
    $("#spreadV").className = `v ${spreadClass(snapshot.risk.model_spread_f)}`;
    text("#hurdleV", fmtPct(snapshot.risk.active_roi_hurdle, 0));
    text("#observedV", fmtTemp(snapshot.risk.observed_high_f));
    text("#leaderV", snapshot.risk.market_leader_bracket || "--");
    text("#chartNote", `Selected ${fmtTime(snapshot.decision.evaluated_at)}`);
    text("#feedCount", `${snapshot.readiness.tradable_feed_count} / ${snapshot.readiness.required_tradable_feed_count}`);
    text("#nbmMaturity", `${snapshot.readiness.nbm_completed_dates} / ${snapshot.readiness.nbm_next_maturity_threshold}`);
    text("#bookDepth", snapshot.readiness.orderbook_depth_available ? "Available" : "Unavailable");
    text("#executionState", snapshot.strategy.order_submission_reachable ? "Reachable" : "Disabled");
    text("#sourceLabel", `${snapshot.event.ticker} - ${fmtTime(snapshot.generated_at)} - ${snapshot.strategy.strategy_id}`);
    renderLegend(snapshot.models);
    renderChart(snapshot);
    renderModelCards(snapshot.models);
    renderGates(snapshot.gates);
    renderMarket(snapshot.market);
    renderProbabilityLab(snapshot);
  }

  function renderLegend(models) {
    const legend = $("#modelLegend");
    legend.replaceChildren();
    models.forEach((model) => {
      const item = document.createElement("span");
      const dot = document.createElement("i");
      dot.className = "dot";
      dot.style.setProperty("--c", model.color);
      item.append(dot, document.createTextNode(model.label));
      legend.appendChild(item);
    });
  }

  function renderChart(snapshot) {
    const chart = $("#modelChart");
    const rows = state.timeline.length ? state.timeline : [snapshotToTimeline(snapshot)];
    const values = [];
    rows.forEach((row) => MODEL_ORDER.forEach((key) => {
      const value = row.model_states ? row.model_states[key] : null;
      if (value !== null && value !== undefined) values.push(Number(value));
    }));
    const minY = values.length ? Math.floor(Math.min(...values) - 1) : 60;
    const maxY = values.length ? Math.ceil(Math.max(...values) + 1) : 80;
    const width = 900, height = 290, pad = { l: 46, r: 18, t: 18, b: 38 };
    const x = (i) => rows.length === 1 ? width / 2 : pad.l + i * (width - pad.l - pad.r) / (rows.length - 1);
    const y = (v) => pad.t + (maxY - v) * (height - pad.t - pad.b) / Math.max(1, maxY - minY);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="chartSvgTitle chartSvgDesc"><title id="chartSvgTitle">Five model state path</title><desc id="chartSvgDesc">Persisted current strategy model states over time, with gaps for missing values.</desc>`;
    for (let tick = minY; tick <= maxY; tick += Math.max(1, Math.ceil((maxY - minY) / 4))) {
      svg += `<line x1="${pad.l}" y1="${y(tick)}" x2="${width - pad.r}" y2="${y(tick)}" stroke="#213342"/><text x="${pad.l - 9}" y="${y(tick) + 4}" fill="#718ba0" font-size="11" text-anchor="end">${tick} F</text>`;
    }
    MODEL_ORDER.forEach((key) => {
      let segment = [];
      rows.forEach((row, i) => {
        const value = row.model_states ? row.model_states[key] : null;
        if (value === null || value === undefined) {
          if (segment.length > 1) svg += polyline(segment, MODEL_COLORS[key]);
          segment = [];
          return;
        }
        segment.push(`${x(i)},${y(Number(value))}`);
        svg += `<circle cx="${x(i)}" cy="${y(Number(value))}" r="3" fill="${MODEL_COLORS[key]}" stroke="#071018" stroke-width="1"/>`;
      });
      if (segment.length > 1) svg += polyline(segment, MODEL_COLORS[key]);
    });
    rows.forEach((row, i) => {
      svg += `<text x="${x(i)}" y="${height - 12}" fill="#718ba0" font-size="11" text-anchor="middle">${fmtTime(row.evaluated_at).replace(" PST", "").replace(" PDT", "")}</text>`;
    });
    svg += "</svg>";
    chart.innerHTML = svg;
  }

  function polyline(points, color) {
    return `<polyline points="${points.join(" ")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }

  function renderModelCards(models) {
    const wrap = $("#modelCards");
    wrap.replaceChildren();
    models.forEach((model) => {
      const card = document.createElement("article");
      card.className = "model";
      card.style.setProperty("--model", model.color);
      const statusClass = model.feed_status === "healthy" ? "" : (model.maturity_status === "provisional" ? "prov" : "bad");
      card.innerHTML = `<div class="name"></div><div class="reading"></div><div class="meta"></div><span class="status ${statusClass}"></span>`;
      card.querySelector(".name").textContent = model.label;
      card.querySelector(".reading").textContent = fmtTemp(model.state_f);
      card.querySelector(".meta").textContent = `${model.mapped_bracket || "Unavailable"} - prior ${model.prior_weight || "--"} - effective ${model.effective_weight || "--"}`;
      card.querySelector(".status").textContent = `${model.feed_status}: ${model.status_detail || model.maturity_status}`;
      wrap.appendChild(card);
    });
  }

  function renderGates(gates) {
    const wrap = $("#alerts");
    wrap.replaceChildren();
    gates.forEach((gate) => {
      const item = document.createElement("div");
      item.className = `alert ${gate.severity}`;
      const dot = document.createElement("i");
      const body = document.createElement("div");
      const title = document.createElement("strong");
      const detail = document.createElement("span");
      title.textContent = gate.label;
      detail.textContent = `${gate.code}: ${gate.detail}`;
      body.append(title, detail);
      item.append(dot, body);
      wrap.appendChild(item);
    });
  }

  function renderMarket(rows) {
    const tbody = $("#bookTable tbody");
    tbody.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.textContent = "Market probabilities and executable prices unavailable.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      if (row.candidate) tr.className = "focus";
      [
        row.bracket,
        row.yes_bid || "--",
        row.yes_ask || "--",
        fmtPct(row.p_safe_yes, 1),
        fmtPct(row.p_safe_no, 1),
        fmtPct(row.required_probability_yes, 1),
        row.modeled_net_roi_yes || row.modeled_net_roi_no || "Unavailable",
        row.status_code || (row.eligible ? "Eligible" : "Unavailable"),
      ].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }

  function renderProbabilityLab(snapshot) {
    const lab = snapshot.probability_lab || {};
    const calibration = lab.calibration || {};
    text("#labMode", lab.mode ? `${lab.mode.replaceAll("_", " ")} - ${lab.evaluation_id || "--"}` : "No probability lab payload.");
    text("#labEval", lab.evaluated_at ? `Evaluated ${fmtTime(lab.evaluated_at)}` : "");
    text("#labCalibration", calibration.source ? calibration.source.replaceAll("_", " ") : "--");
    text("#labEffectiveN", calibration.effective_sample_size !== undefined ? String(calibration.effective_sample_size) : "--");
    const warnings = (snapshot.gates || []).filter((gate) => gate.severity === "warning").length;
    text("#labWarnings", String(warnings));
    renderLabDistributions(lab.model_distributions || [], lab.brackets || []);
    renderLabWeights(lab.weights || []);
    renderLabFunnel(lab.probability_funnel || []);
    renderLabEquations(lab.equation_trace || {});
    renderLabSensitivity(lab.sensitivity || []);
  }

  function renderLabWeights(weights) {
    const wrap = $("#labWeights");
    wrap.replaceChildren();
    if (!weights.length) {
      wrap.textContent = "No model weights available.";
      return;
    }
    weights.forEach((item) => {
      const card = document.createElement("article");
      card.className = "lab-card";
      const key = item.model_key;
      card.style.setProperty("--model", MODEL_COLORS[key] || "#47d7e8");
      card.innerHTML = `<strong></strong><div class="lab-value"></div><span></span>`;
      card.querySelector("strong").textContent = MODEL_LABELS[key] || key;
      card.querySelector(".lab-value").textContent = fmtPct(item.effective_weight, 1);
      card.querySelector("span").textContent = `prior ${item.prior_weight ?? "--"} - dates ${item.completed_dates ?? 0} - ${item.maturity_status || "unknown"}`;
      wrap.appendChild(card);
    });
  }

  function renderLabDistributions(distributions, brackets) {
    const wrap = $("#labDistributionChart");
    wrap.replaceChildren();
    const bracketRows = brackets.length
      ? brackets
      : ((distributions[0] || {}).bracket_probabilities || []);
    const labels = bracketRows.map((row) => row.bracket || row.ticker).filter(Boolean);
    if (!labels.length || !distributions.length) {
      wrap.textContent = "No backend probability distributions available.";
      return;
    }

    const rows = MODEL_ORDER.map((key) => {
      const dist = distributions.find((item) => item.model_key === key);
      if (!dist) return null;
      const byLabel = {};
      (dist.bracket_probabilities || []).forEach((item) => {
        byLabel[item.bracket || item.ticker] = item.p_yes;
      });
      return {
        key,
        label: MODEL_LABELS[key] || key,
        color: MODEL_COLORS[key] || "#47d7e8",
        values: labels.map((label) => byLabel[label]),
      };
    }).filter(Boolean);

    const mixture = {
      key: "mixture",
      label: "Mixture",
      color: "#47d7e8",
      values: labels.map((label) => {
        const row = brackets.find((item) => (item.bracket || item.ticker) === label);
        return row ? row.p_mean_yes : null;
      }),
    };
    rows.push(mixture);
    wrap.innerHTML = probabilityHeatmapSvg(rows, labels);
  }

  function probabilityHeatmapSvg(rows, labels) {
    const width = 920;
    const rowHeight = 38;
    const pad = { l: 104, r: 18, t: 32, b: 54 };
    const height = pad.t + rows.length * rowHeight + pad.b;
    const cellWidth = (width - pad.l - pad.r) / Math.max(1, labels.length);
    const maxMixture = Math.max(...(rows[rows.length - 1].values || []).map((value) => Number(value || 0)));
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="labDistTitle labDistDesc"><title id="labDistTitle">Backend probability distribution matrix</title><desc id="labDistDesc">Model and mixture bracket probabilities produced by the probability lab endpoint.</desc>`;
    labels.forEach((label, i) => {
      const cx = pad.l + i * cellWidth + cellWidth / 2;
      svg += `<text x="${cx}" y="${height - 26}" fill="#89a2b6" font-size="10" text-anchor="middle">${svgText(label.replace(" F", ""))}</text>`;
    });
    rows.forEach((row, rowIndex) => {
      const y0 = pad.t + rowIndex * rowHeight;
      const cy = y0 + rowHeight / 2 + 4;
      svg += `<text x="${pad.l - 12}" y="${cy}" fill="${row.color}" font-size="11" text-anchor="end" font-weight="700">${svgText(row.label)}</text>`;
      row.values.forEach((value, i) => {
        const numeric = value === null || value === undefined ? null : Number(value);
        const x0 = pad.l + i * cellWidth + 2;
        const opacity = numeric === null ? 0.05 : Math.max(0.08, Math.min(0.88, numeric * 0.92 + 0.06));
        const isMixtureMax = row.key === "mixture" && numeric === maxMixture && maxMixture > 0;
        svg += `<rect x="${x0}" y="${y0 + 3}" width="${Math.max(4, cellWidth - 4)}" height="${rowHeight - 6}" rx="5" fill="#47d7e8" opacity="${opacity.toFixed(3)}" stroke="${isMixtureMax ? "#47d7e8" : "#213342"}" stroke-width="${isMixtureMax ? "1.5" : "1"}"><title>${svgText(row.label)} ${svgText(labels[i])}: ${fmtPct(numeric, 1)}</title></rect>`;
        svg += `<text x="${x0 + (cellWidth - 4) / 2}" y="${cy}" fill="#edf7ff" font-size="10" text-anchor="middle">${numeric === null ? "--" : svgText(fmtPct(numeric, numeric < 0.01 ? 2 : 1))}</text>`;
      });
    });
    svg += `<text x="${pad.l}" y="16" fill="#89a2b6" font-size="11">Backend model and mixture probabilities by settlement bracket</text>`;
    svg += "</svg>";
    return svg;
  }

  function renderLabFunnel(rows) {
    const wrap = $("#labFunnel");
    wrap.replaceChildren();
    if (!rows.length) {
      wrap.textContent = "No funnel stages available.";
      return;
    }
    rows.forEach((row) => {
      const item = document.createElement("div");
      item.className = "funnel-row";
      const label = document.createElement("span");
      const value = document.createElement("strong");
      label.textContent = String(row.stage || "").replaceAll("_", " ");
      value.textContent = String(row.value ?? "--");
      item.append(label, value);
      wrap.appendChild(item);
    });
  }

  function renderLabEquations(trace) {
    const wrap = $("#labEquations");
    wrap.replaceChildren();
    const entries = Object.entries(trace || {});
    if (!entries.length) {
      wrap.textContent = "No equation trace available.";
      return;
    }
    entries.forEach(([key, value]) => {
      const item = document.createElement("div");
      item.className = "equation";
      const label = document.createElement("strong");
      const code = document.createElement("code");
      label.textContent = key.replaceAll("_", " ");
      code.textContent = value;
      item.append(label, code);
      wrap.appendChild(item);
    });
  }

  function renderLabSensitivity(rows) {
    const wrap = $("#labSensitivity");
    wrap.replaceChildren();
    if (!rows.length) {
      wrap.textContent = "No focused side sensitivity available.";
      return;
    }
    const chart = document.createElement("div");
    chart.className = "lab-chart sensitivity-chart";
    chart.innerHTML = sensitivitySvg(rows);
    wrap.appendChild(chart);
    const table = document.createElement("table");
    table.className = "mini-table";
    table.innerHTML = "<thead><tr><th>Price</th><th>ROI</th><th>Required p</th></tr></thead><tbody></tbody>";
    rows.slice(0, 12).forEach((row) => {
      const tr = document.createElement("tr");
      [row.price || "--", row.roi || "--", fmtPct(row.required_probability, 1)].forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      table.querySelector("tbody").appendChild(tr);
    });
    wrap.appendChild(table);
  }

  function sensitivitySvg(rows) {
    const points = rows.map((row) => ({
      price: asNumber(row.price),
      priceLabel: row.price || "--",
      required: Number(row.required_probability),
      roi: pctNumber(row.roi),
      roiLabel: row.roi || "--",
    })).filter((row) => row.price !== null && Number.isFinite(row.required));
    if (!points.length) return "<p>No sensitivity points available.</p>";
    const width = 920;
    const height = 245;
    const pad = { l: 52, r: 20, t: 26, b: 44 };
    const minX = Math.min(...points.map((point) => point.price));
    const maxX = Math.max(...points.map((point) => point.price));
    const maxY = Math.max(0.05, Math.ceil(Math.max(...points.map((point) => point.required)) * 10) / 10);
    const x = (value) => pad.l + (value - minX) * (width - pad.l - pad.r) / Math.max(0.01, maxX - minX);
    const y = (value) => pad.t + (maxY - value) * (height - pad.t - pad.b) / Math.max(0.01, maxY);
    const path = points.map((point, index) => `${index ? "L" : "M"}${x(point.price).toFixed(2)},${y(point.required).toFixed(2)}`).join(" ");
    const maxAcceptable = [...points].reverse().find((point) => point.roi !== null && point.roi >= 0);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="labSensitivityTitle labSensitivityDesc"><title id="labSensitivityTitle">Price sensitivity chart</title><desc id="labSensitivityDesc">Required probability by quoted price from the backend sensitivity grid.</desc>`;
    for (let tick = 0; tick <= maxY + 1e-9; tick += maxY / 4) {
      svg += `<line x1="${pad.l}" y1="${y(tick)}" x2="${width - pad.r}" y2="${y(tick)}" stroke="#213342"/><text x="${pad.l - 8}" y="${y(tick) + 4}" fill="#89a2b6" font-size="10" text-anchor="end">${svgText(fmtPct(tick, 0))}</text>`;
    }
    svg += `<path d="${path}" fill="none" stroke="#47d7e8" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`;
    points.forEach((point) => {
      svg += `<circle cx="${x(point.price)}" cy="${y(point.required)}" r="3.3" fill="#47d7e8" stroke="#071018" stroke-width="1"><title>Price ${svgText(point.priceLabel)} requires ${svgText(fmtPct(point.required, 1))}; ROI ${svgText(point.roiLabel)}</title></circle>`;
    });
    svg += `<text x="${pad.l}" y="16" fill="#89a2b6" font-size="11">Required probability by quote price</text>`;
    if (maxAcceptable) {
      const labelX = Math.min(width - 180, Math.max(pad.l + 4, x(maxAcceptable.price) + 8));
      svg += `<line x1="${x(maxAcceptable.price)}" y1="${pad.t}" x2="${x(maxAcceptable.price)}" y2="${height - pad.b}" stroke="#f6bd60" stroke-dasharray="4 5"/><text x="${labelX}" y="${pad.t + 14}" fill="#f6bd60" font-size="11">max positive ROI ${svgText(maxAcceptable.priceLabel)}</text>`;
    }
    points.filter((_, index) => index === 0 || index === points.length - 1).forEach((point) => {
      svg += `<text x="${x(point.price)}" y="${height - 16}" fill="#89a2b6" font-size="10" text-anchor="middle">${svgText(point.priceLabel)}</text>`;
    });
    svg += "</svg>";
    return svg;
  }

  function snapshotToTimeline(snapshot) {
    const modelStates = {};
    snapshot.models.forEach((model) => { modelStates[model.model_key] = model.state_f; });
    return {
      evaluated_at: snapshot.decision.evaluated_at,
      model_states: modelStates,
      decision_status: snapshot.decision.status,
      reason_code: snapshot.decision.reason_code,
      revision: snapshot.revision,
      source_ids: [],
    };
  }

  function decisionClass(status) {
    if (status === "TRADE_CANDIDATE") return "trade";
    if (status === "NO_TRADE" || status === "DATA_INCOMPLETE") return "stop";
    return "shadow";
  }

  function spreadClass(value) {
    if (value === null || value === undefined) return "";
    if (Number(value) >= 4) return "bad";
    if (Number(value) >= 3) return "warn";
    return "";
  }

  async function refresh() {
    if (state.paused || document.hidden) return;
    try {
      await loadTimeline();
      const rendered = await loadSnapshot();
      if (!rendered && state.snapshot) render(state.snapshot);
      text("#refreshStatus", "Polling");
    } catch (error) {
      state.failureAt = new Date();
      text("#refreshStatus", `API unavailable since ${fmtTime(state.failureAt.toISOString())}`);
      const banner = $("#banner");
      banner.hidden = false;
      banner.textContent = `Last valid snapshot retained. ${error.message}`;
    }
  }

  async function init() {
    const replayMode = root.dataset.mode === "replay" || root.dataset.sampleMode === "true";
    $("#liveControls").hidden = replayMode;
    $("#replayControls").hidden = !replayMode;
    await loadEvents();
    await loadTimeline();
    if (replayMode && state.timeline.length) {
      const range = $("#timeRange");
      range.addEventListener("input", async () => {
        const point = state.timeline[Number(range.value)];
        text("#timePill", fmtTime(point.evaluated_at));
        await loadSnapshot(point.evaluated_at);
      });
      text("#timePill", fmtTime(state.timeline[0].evaluated_at));
      await loadSnapshot(state.timeline[0].evaluated_at);
    } else {
      await loadSnapshot();
      window.setInterval(refresh, Number(root.dataset.pollSeconds || 2) * 1000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
      $("#pauseButton").addEventListener("click", () => {
        state.paused = !state.paused;
        $("#pauseButton").textContent = state.paused ? "Resume" : "Pause";
        text("#refreshStatus", state.paused ? "Paused" : "Polling");
        if (!state.paused) refresh();
      });
    }
    document.querySelectorAll("[data-lab-tab]").forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll("[data-lab-tab]").forEach((tab) => tab.classList.remove("active"));
        document.querySelectorAll(".lab-panel").forEach((panel) => panel.classList.remove("active"));
        button.classList.add("active");
        const name = button.dataset.labTab;
        const panel = $(`#labPanel${name.charAt(0).toUpperCase()}${name.slice(1)}`);
        if (panel) panel.classList.add("active");
      });
    });
  }

  init().catch((error) => {
    const banner = $("#banner");
    banner.hidden = false;
    banner.textContent = `Dashboard failed to initialize: ${error.message}`;
  });
}());
