(function () {
  const MODEL_ORDER = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"];
  const MODEL_COLORS = {
    ecmwf_ifs: "#5B8FF9",
    gfs013: "#F6BD16",
    gfs_seamless: "#E8684A",
    nam: "#6AA84F",
    nbm: "#C66DD4",
    mixture: "#47d7e8",
  };
  const MODEL_LABELS = {
    ecmwf_ifs: "ECMWF IFS",
    gfs013: "GFS 0.13",
    gfs_seamless: "GFS Seamless",
    nam: "NAM",
    nbm: "NBM",
  };

  const root = document.body;
  const state = {
    snapshot: null,
    events: [],
    evaluations: [],
    weightHistory: [],
    eventTicker: null,
    targetDate: null,
    selectedMarketTicker: null,
    side: null,
    modelKey: "ecmwf_ifs",
    live: true,
    failureAt: null,
  };

  function $(selector) { return document.querySelector(selector); }
  function text(selector, value) { const node = $(selector); if (node) node.textContent = value; }
  function esc(value) {
    return String(value ?? "").replace(/[&<>"]/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
    }[char]));
  }
  function num(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function fmtPct(value, digits) {
    const numeric = num(value);
    return numeric === null ? "--" : `${(numeric * 100).toFixed(digits ?? 1)}%`;
  }
  function fmtPp(value) {
    const numeric = num(value);
    if (numeric === null) return "--";
    const sign = numeric >= 0 ? "+" : "";
    return `${sign}${(numeric * 100).toFixed(1)}pp`;
  }
  function fmtMoney(value) {
    const numeric = num(value);
    return numeric === null ? "--" : numeric.toFixed(2);
  }
  function fmtTemp(value) {
    const numeric = num(value);
    return numeric === null ? "--" : `${numeric.toFixed(1)} F`;
  }
  function fmtNumber(value, digits) {
    const numeric = num(value);
    return numeric === null ? "--" : numeric.toFixed(digits ?? 2);
  }
  function stageLabel(value) {
    return String(value || "--").replaceAll("_", " ").toUpperCase();
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
  function fmtAge(value) {
    if (!value) return "--";
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    return `${Math.floor(minutes / 60)}h`;
  }

  async function getJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function targetQuery() {
    return state.targetDate ? `?target=${encodeURIComponent(state.targetDate)}` : "";
  }

  async function loadEvents() {
    state.events = await getJson("/api/v1/signal-room/events");
    const select = $("#labEventSelect");
    select.replaceChildren();
    state.events.forEach((event) => {
      const option = document.createElement("option");
      option.value = event.ticker;
      option.dataset.targetDate = event.target_date || "";
      option.textContent = `${event.ticker} - ${event.target_date}`;
      select.appendChild(option);
    });
    const first = state.events[0];
    if (first) {
      state.eventTicker = first.ticker;
      state.targetDate = first.target_date || root.dataset.targetDate || null;
    }
  }

  async function loadEvaluations() {
    if (!state.eventTicker) return;
    const url = `/api/strategy/current/events/${encodeURIComponent(state.eventTicker)}/evaluations${targetQuery()}`;
    state.evaluations = await getJson(url);
    const range = $("#labEvaluationRange");
    range.max = String(Math.max(0, state.evaluations.length - 1));
    range.value = String(Math.max(0, state.evaluations.length - 1));
  }

  async function loadWeightHistory() {
    if (!state.eventTicker) return;
    const url = `/api/strategy/current/events/${encodeURIComponent(state.eventTicker)}/weighting/history${targetQuery()}`;
    state.weightHistory = await getJson(url);
  }

  function withWeighting(payload, weightingPayload) {
    return {
      ...payload,
      weighting: weightingPayload.weighting || {},
      weightingModes: weightingPayload.weighting_modes || {},
      weightingEquationTrace: weightingPayload.equation_trace || {},
    };
  }

  async function loadLatest() {
    if (!state.eventTicker) return;
    const base = `/api/strategy/current/events/${encodeURIComponent(state.eventTicker)}`;
    const bundle = await getJson(`${base}/probability-lab/latest${targetQuery()}`);
    acceptSnapshot(withWeighting(bundle.explainability, bundle.weighting));
    state.live = true;
    render();
  }

  async function loadEvaluation(evaluationId) {
    if (!state.eventTicker || !evaluationId) return;
    const params = new URLSearchParams();
    params.set("evaluation_id", evaluationId);
    if (state.targetDate) params.set("target", state.targetDate);
    const base = `/api/strategy/current/events/${encodeURIComponent(state.eventTicker)}`;
    const bundle = await getJson(`${base}/probability-lab?${params}`);
    acceptSnapshot(withWeighting(bundle.explainability, bundle.weighting));
    state.live = false;
    render();
  }

  function acceptSnapshot(payload) {
    const priorMarket = state.selectedMarketTicker;
    const priorModel = state.modelKey;
    const priorSide = state.side;
    state.snapshot = payload;
    state.failureAt = null;
    populateContracts(payload);
    populateModels(payload);
    state.selectedMarketTicker = marketExists(priorMarket) ? priorMarket : payload.selectedMarketTicker || firstMarketTicker(payload);
    state.modelKey = modelExists(priorModel) ? priorModel : firstModelKey(payload);
    state.side = priorSide || payload.selectedSide || "yes";
    $("#labContractSelect").value = state.selectedMarketTicker || "";
    $("#labModelSelect").value = state.modelKey || "";
    $("#labSideSelect").value = state.side || "yes";
  }

  function marketExists(ticker) {
    return Boolean(ticker && state.snapshot && state.snapshot.outcomeMap.brackets.some((item) => item.marketTicker === ticker));
  }
  function modelExists(key) {
    return Boolean(key && state.snapshot && state.snapshot.models.some((item) => item.modelKey === key));
  }
  function firstMarketTicker(payload) {
    return ((payload.outcomeMap || {}).brackets || [])[0]?.marketTicker || null;
  }
  function firstModelKey(payload) {
    return (payload.models || [])[0]?.modelKey || "ecmwf_ifs";
  }

  function populateContracts(payload) {
    const select = $("#labContractSelect");
    const current = select.value;
    select.replaceChildren();
    (payload.outcomeMap.brackets || []).forEach((bracket) => {
      const option = document.createElement("option");
      option.value = bracket.marketTicker;
      option.textContent = bracket.label;
      select.appendChild(option);
    });
    if (current && marketExists(current)) select.value = current;
  }

  function populateModels(payload) {
    const select = $("#labModelSelect");
    const current = select.value;
    select.replaceChildren();
    (payload.models || []).forEach((model) => {
      const option = document.createElement("option");
      option.value = model.modelKey;
      option.textContent = model.label;
      select.appendChild(option);
    });
    if (current && modelExists(current)) select.value = current;
  }

  function render() {
    const snap = state.snapshot;
    if (!snap) return;
    const selectedBracket = bracketByTicker(state.selectedMarketTicker);
    text("#labSubtitle", `${snap.eventTicker} - ${snap.targetDate} - KLAX`);
    text("#labModeChip", `${snap.mode.toUpperCase()} MODE`);
    text("#labModelCount", `${snap.models.filter((model) => model.scenarioTemperaturesF.length).length}`);
    text("#labEvalAge", fmtAge(snap.evaluatedAt));
    text("#labOrderPath", snap.captureHealth.orderPathReachable ? "order path reachable" : "order path disabled");
    text("#labTimePill", `${fmtTime(snap.evaluatedAt)} ${snap.evaluationId.slice(0, 8)}`);
    text("#labRefreshStatus", state.live ? "Live polling" : "Replay frozen");
    text("#labFooterState", `${snap.eventTicker} - ${snap.evaluationId} - ${snap.analysisState} / ${snap.executionState}`);
    const banner = $("#labBanner");
    if (root.dataset.sampleMode === "true") {
      banner.hidden = false;
      banner.textContent = "Replay fixture mode is explicit. Live mode never falls back to fixture data.";
    } else if (snap.analysisState !== "ANALYSIS_READY") {
      banner.hidden = false;
      banner.textContent = `${snap.analysisState}: ${snap.finalReasonCode}`;
    } else {
      banner.hidden = true;
    }
    renderHero(snap, selectedBracket);
    renderWeightStatus(snap);
    renderWeightLegend();
    renderWeightHistory();
    renderWeightAttribution(snap);
    renderCounterfactuals(snap);
    renderDistributionLegend(snap);
    renderDistributionChart(snap);
    renderLedger(snap);
    renderFunnel(snap, selectedBracket);
    renderEquations(snap);
    renderMatrix(snap);
    renderMarketWeather(snap);
    renderSensitivity(snap);
    renderAudit(snap);
  }

  function selectedEconomics() {
    const snap = state.snapshot;
    if (!snap) return null;
    return snap.economics.find((item) => item.marketTicker === state.selectedMarketTicker && item.side === state.side) || null;
  }

  function bracketByTicker(ticker) {
    return ((state.snapshot || {}).outcomeMap?.brackets || []).find((item) => item.marketTicker === ticker) || null;
  }

  function mixtureByTicker(ticker) {
    return ((state.snapshot || {}).mixture?.bracketProbabilities || []).find((item) => item.marketTicker === ticker) || null;
  }

  function modelProbability(model, ticker) {
    return (model.bracketProbabilities || []).find((item) => item.marketTicker === ticker) || null;
  }

  function weightingModel(modelKey) {
    return (state.snapshot?.weighting?.models || []).find((item) => item.modelKey === modelKey) || null;
  }

  function renderWeightStatus(snap) {
    const weighting = snap.weighting || {};
    const stage = weighting.stage || {};
    text("#weightStage", stageLabel(stage.stageId));
    text("#weightMode", stageLabel(weighting.primaryMode));
    text("#weightRevision", weighting.weightingRevision || "--");
    const transition = stage.transitionFromStage
      ? `${stageLabel(stage.transitionFromStage)} -> ${stageLabel(stage.stageId)} (${fmtPct(stage.transitionAlpha, 0)})`
      : "STABLE";
    text("#weightTransition", transition);
    text("#weightReadiness", weighting.status || "--");
  }

  function renderWeightLegend() {
    const legend = $("#weightLegend");
    legend.replaceChildren();
    MODEL_ORDER.forEach((key) => {
      const item = document.createElement("span");
      item.innerHTML = `<i style="background:${MODEL_COLORS[key]}"></i>${esc(MODEL_LABELS[key])}`;
      legend.appendChild(item);
    });
  }

  function renderWeightHistory() {
    const chart = $("#weightHistoryChart");
    const rows = (state.weightHistory || []).filter((row) => (row.weighting?.models || []).length);
    text("#weightHistorySource", `${rows.length} immutable evaluations`);
    if (!rows.length) {
      chart.textContent = "No persisted weight history is available for this event.";
      return;
    }
    const width = 940, height = 285, pad = { l: 50, r: 18, t: 25, b: 48 };
    const x = (index) => pad.l + index * (width - pad.l - pad.r) / Math.max(1, rows.length - 1);
    const y = (value) => pad.t + (.4 - Math.max(0, Math.min(.4, Number(value || 0)))) * (height - pad.t - pad.b) / .4;
    let svg = `<svg viewBox="0 0 ${width} ${height}" aria-hidden="true">`;
    [0, .1, .2, .3, .4].forEach((tick) => {
      svg += `<line x1="${pad.l}" y1="${y(tick)}" x2="${width - pad.r}" y2="${y(tick)}" stroke="#213342"/>`;
      svg += `<text x="${pad.l - 8}" y="${y(tick) + 4}" fill="#89a2b6" font-size="10" text-anchor="end">${Math.round(tick * 100)}%</text>`;
    });
    rows.forEach((row, index) => {
      const stage = row.weighting?.stage?.stageId;
      const priorStage = index ? rows[index - 1].weighting?.stage?.stageId : null;
      if (index === 0 || stage !== priorStage) {
        svg += `<line x1="${x(index)}" y1="${pad.t}" x2="${x(index)}" y2="${height - pad.b}" stroke="#54738a" stroke-dasharray="3 4"/>`;
        svg += `<text x="${x(index) + 4}" y="${pad.t + 10}" fill="#89a2b6" font-size="9">${esc(stageLabel(stage))}</text>`;
      }
    });
    MODEL_ORDER.forEach((key) => {
      const points = rows.map((row, index) => {
        const model = (row.weighting.models || []).find((item) => item.modelKey === key);
        return `${x(index).toFixed(2)},${y(model?.finalWeight).toFixed(2)}`;
      });
      svg += `<polyline points="${points.join(" ")}" fill="none" stroke="${MODEL_COLORS[key]}" stroke-width="2.2"/>`;
      rows.forEach((row, index) => {
        const model = (row.weighting.models || []).find((item) => item.modelKey === key);
        if (key === "nbm" && model?.maturityCap === 0) {
          svg += `<circle cx="${x(index)}" cy="${y(model.finalWeight)}" r="3" fill="${MODEL_COLORS[key]}"/>`;
        }
      });
    });
    const labelIndexes = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
    labelIndexes.forEach((index) => {
      svg += `<text x="${x(index)}" y="${height - 18}" fill="#89a2b6" font-size="10" text-anchor="middle">${esc(fmtTime(rows[index].evaluated_at))}</text>`;
    });
    chart.innerHTML = `${svg}</svg>`;
  }

  function renderWeightAttribution(snap) {
    const wrap = $("#weightAttribution");
    const model = weightingModel(state.modelKey);
    text("#attributionModel", MODEL_LABELS[state.modelKey] || state.modelKey || "--");
    if (!model) {
      wrap.textContent = "No backend attribution is available for the selected model.";
      return;
    }
    const capLabels = [
      model.individualCapApplied ? "individual applied" : "individual clear",
      model.familyCapApplied ? "family applied" : "family clear",
      model.maturityCapApplied ? "maturity applied" : "maturity clear",
    ].join("; ");
    const steps = [
      ["Stage prior", fmtPct(model.stagePrior, 1), `${model.stageHistoryDates} prior dates`],
      ["Reliability multiplier", fmtNumber(model.reliabilityMultiplier, 3), `n-eff ${fmtNumber(model.stageNEff, 1)}`],
      ["Pre-cap influence", fmtPct(model.preCapWeight, 1), "Backend product"],
      ["Cap redistribution", capLabels, `maturity ceiling ${fmtPct(model.maturityCap, 0)}`],
      ["Final effective weight", fmtPct(model.finalWeight, 1), model.weightingStatus || "--"],
    ];
    wrap.innerHTML = steps.map((step, index) => `${index ? '<div class="attribution-arrow">v</div>' : ""}<div class="attribution-step"><span>${esc(step[0])}</span><strong>${esc(step[1])}</strong><small>${esc(step[2])}</small></div>`).join("");
  }

  function renderCounterfactuals(snap) {
    const wrap = $("#counterfactualComparison");
    const selected = bracketByTicker(state.selectedMarketTicker);
    const economics = selectedEconomics();
    const sideKey = state.side === "yes" ? "p_safe_yes" : "p_safe_no";
    text("#counterfactualContract", `${selected?.label || "--"} / ${String(state.side || "yes").toUpperCase()}`);
    const modeRows = snap.weighting?.counterfactuals || [];
    const rows = modeRows.map((mode) => {
      const output = snap.weightingModes?.[mode.mode] || {};
      const bracket = output.bracket_probabilities?.[state.selectedMarketTicker] || {};
      return {
        label: stageLabel(mode.mode),
        probability: bracket[sideKey],
        primary: Boolean(mode.isPrimary),
      };
    });
    if (economics) {
      rows.push({
        label: "MARKET REQUIRED PROBABILITY",
        probability: economics.requiredProbability,
        primary: false,
      });
    }
    wrap.innerHTML = rows.map((row) => `<div class="counterfactual-row${row.primary ? " primary" : ""}"><span>${esc(row.label)}${row.primary ? " / PRIMARY" : ""}</span><strong>${fmtPct(row.probability, 1)}</strong></div>`).join("");
  }

  function renderHero(snap, selectedBracket) {
    const econ = selectedEconomics();
    const probability = econ?.pSafe ?? null;
    const required = econ?.requiredProbability ?? null;
    const edge = probability !== null && required !== null ? probability - required : null;
    text("#heroPSafe", fmtPct(probability, 1));
    text("#heroPNote", `${(state.side || "yes").toUpperCase()} ${selectedBracket?.label || "--"} from backend economics`);
    text("#heroAsk", fmtMoney(econ?.price));
    text("#heroAskNote", econ ? `${econ.priceBasis.replaceAll("_", " ")} - max ${fmtMoney(econ.maxAcceptablePrice)}` : "--");
    text("#heroRequired", fmtPct(required, 1));
    text("#heroEdge", fmtPp(edge));
    $("#heroEdge").className = `hero-value ${edge === null ? "" : edge >= 0 ? "good" : "bad"}`;
    text("#heroRoi", fmtPct(econ?.modeledNetRoi, 1));
    $("#heroRoi").className = `hero-value ${econ?.modeledNetRoi === null || econ?.modeledNetRoi === undefined ? "" : econ.modeledNetRoi >= 0 ? "good" : "bad"}`;
    text("#heroDecision", snap.executionState.replaceAll("_", " "));
    $("#heroDecision").className = `hero-value ${snap.executionState === "SHADOW_CANDIDATE" ? "good" : snap.executionState === "BLOCKED" ? "bad" : "warn"}`;
    text("#heroReason", snap.finalReasonCode);
  }

  function renderDistributionLegend(snap) {
    const legend = $("#distributionLegend");
    legend.replaceChildren();
    snap.models.forEach((model) => {
      const item = document.createElement("span");
      item.innerHTML = `<i class="dot" style="--c:${MODEL_COLORS[model.modelKey] || "#47d7e8"}"></i>${esc(model.label)}`;
      legend.appendChild(item);
    });
    const mix = document.createElement("span");
    mix.innerHTML = `<i class="dot" style="--c:${MODEL_COLORS.mixture}"></i>Mixture`;
    legend.appendChild(mix);
  }

  function renderDistributionChart(snap) {
    const rows = [
      ...snap.models.map((model) => ({
        key: model.modelKey,
        label: model.label,
        color: MODEL_COLORS[model.modelKey] || "#89a2b6",
        temps: model.scenarioTemperaturesF || [],
        weights: model.scenarioWeights || [],
      })),
      {
        key: "mixture",
        label: "Mixture",
        color: MODEL_COLORS.mixture,
        temps: snap.mixture.scenarioTemperaturesF || [],
        weights: snap.mixture.scenarioWeights || [],
      },
    ];
    const temps = rows.flatMap((row) => row.temps);
    const chart = $("#distributionChart");
    if (!temps.length) {
      chart.textContent = "No backend scenario temperatures are available for this evaluation.";
      return;
    }
    const bracketBounds = snap.outcomeMap.brackets.flatMap((bracket) => [bracket.lowerBoundF, bracket.upperBoundF]).filter((value) => value !== null);
    const observed = snap.station.observedHighF;
    const minX = Math.floor(Math.min(...temps, ...bracketBounds, observed ?? Infinity) - 1);
    const maxX = Math.ceil(Math.max(...temps, ...bracketBounds, observed ?? -Infinity) + 1);
    const bins = [];
    for (let value = minX; value <= maxX; value += 1) bins.push(value);
    if (bins.length < 2) bins.push(minX + 1);
    const histRows = rows.map((row) => ({ ...row, hist: histogram(row.temps, row.weights, bins) }));
    const maxY = Math.max(...histRows.flatMap((row) => row.hist.map((point) => point.y)), 0.01);
    const width = 940, height = 330, pad = { l: 52, r: 18, t: 18, b: 42 };
    const x = (value) => pad.l + (value - minX) * (width - pad.l - pad.r) / Math.max(1, maxX - minX);
    const y = (value) => pad.t + (maxY - value) * (height - pad.t - pad.b) / maxY;
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Backend scenario distribution chart">`;
    snap.outcomeMap.brackets.forEach((bracket, index) => {
      const left = bracket.lowerBoundF === null ? minX : bracket.lowerBoundF - 0.5;
      const right = bracket.upperBoundF === null ? maxX : bracket.upperBoundF + 0.5;
      const fill = index % 2 ? "rgba(71,215,232,.05)" : "rgba(255,255,255,.025)";
      svg += `<rect x="${x(Math.max(minX, left))}" y="${pad.t}" width="${Math.max(0, x(Math.min(maxX, right)) - x(Math.max(minX, left)))}" height="${height - pad.t - pad.b}" fill="${fill}"/>`;
    });
    for (let tick = minX; tick <= maxX; tick += Math.max(1, Math.ceil((maxX - minX) / 8))) {
      svg += `<line x1="${x(tick)}" y1="${pad.t}" x2="${x(tick)}" y2="${height - pad.b}" stroke="#1c2d3a"/><text x="${x(tick)}" y="${height - 14}" fill="#7892a5" font-size="10" text-anchor="middle">${tick}F</text>`;
    }
    histRows.forEach((row) => {
      if (!row.hist.length) return;
      const path = row.hist.map((point, index) => `${index ? "L" : "M"}${x(point.x).toFixed(2)},${y(point.y).toFixed(2)}`).join(" ");
      const widthLine = row.key === "mixture" ? 3 : 1.8;
      const opacity = row.key === "mixture" ? 1 : .72;
      svg += `<path d="${path}" fill="none" stroke="${row.color}" stroke-width="${widthLine}" opacity="${opacity}" stroke-linejoin="round" stroke-linecap="round"/>`;
    });
    if (observed !== null && observed !== undefined) {
      svg += `<line x1="${x(observed)}" y1="${pad.t}" x2="${x(observed)}" y2="${height - pad.b}" stroke="#edf7ff" stroke-width="1.5" stroke-dasharray="5 5"/><text x="${Math.min(x(observed) + 7, width - 145)}" y="${pad.t + 14}" fill="#edf7ff" font-size="10">observed ${fmtTemp(observed)}</text>`;
    }
    svg += `<text x="${width - pad.r}" y="${pad.t + 15}" fill="#47d7e8" font-size="10" text-anchor="end">weighted mixture</text></svg>`;
    chart.innerHTML = svg;
  }

  function histogram(temps, weights, bins) {
    if (!temps.length || !weights.length) return [];
    const values = new Array(Math.max(0, bins.length - 1)).fill(0);
    temps.forEach((temp, index) => {
      const weight = Number(weights[index] || 0);
      let bucket = bins.findIndex((left, binIndex) => binIndex < bins.length - 1 && temp >= left && temp < bins[binIndex + 1]);
      if (bucket < 0 && temp === bins[bins.length - 1]) bucket = bins.length - 2;
      if (bucket >= 0) values[bucket] += weight;
    });
    return values.map((value, index) => ({ x: (bins[index] + bins[index + 1]) / 2, y: value }));
  }

  function renderLedger(snap) {
    const wrap = $("#modelLedger");
    const columns = ["Model", "Fixed prior", "Stage prior", "History", "n-eff", "Stage loss", "Shrunk loss", "Reliability", "Pre-cap", "Individual cap", "Family cap", "Maturity cap", "Final weight", "Selected probability"];
    wrap.style.gridTemplateColumns = "1.45fr repeat(12, .72fr) 1fr";
    wrap.innerHTML = columns.map((column) => `<div class="ledger-cell head">${esc(column)}</div>`).join("");
    snap.models.forEach((model) => {
      const weight = weightingModel(model.modelKey) || {};
      const probability = modelProbability(model, state.selectedMarketTicker);
      const meanKey = state.side === "yes" ? "pMeanYes" : "pMeanNo";
      const safeKey = state.side === "yes" ? "pSafeYes" : "pSafeNo";
      const selected = probability ? `${fmtPct(probability[meanKey], 1)} / safe ${fmtPct(probability[safeKey], 1)}` : "--";
      const color = MODEL_COLORS[model.modelKey] || "#47d7e8";
      const cells = [
        `<span class="model-chip"><i style="--c:${color}"></i><strong>${esc(model.label)}</strong></span><br><small>${esc(weight.weightingStatus || model.eligibility)}${weight.exclusionReason ? ` - ${esc(weight.exclusionReason)}` : ""}</small>`,
        fmtPct(weight.fixedPrior, 1),
        fmtPct(weight.stagePrior, 1),
        String(weight.stageHistoryDates ?? "--"),
        fmtNumber(weight.stageNEff, 1),
        fmtNumber(weight.stageLogLoss, 3),
        fmtNumber(weight.shrunkLogLoss, 3),
        fmtNumber(weight.reliabilityMultiplier, 3),
        fmtPct(weight.preCapWeight, 1),
        weight.individualCapApplied ? "Applied" : "Clear",
        weight.familyCapApplied ? "Applied" : "Clear",
        model.modelKey === "nbm" ? `${fmtPct(weight.maturityCap, 0)}${weight.maturityCapApplied ? " / applied" : ""}` : "--",
        `${fmtPct(weight.finalWeight, 1)}<div class="weightbar"><i style="--c:${color};--w:${Math.max(0, Math.min(100, Number(weight.finalWeight || 0) * 100))}%"></i></div>`,
        selected,
      ];
      wrap.insertAdjacentHTML("beforeend", cells.map((cell) => `<div class="ledger-cell">${cell}</div>`).join(""));
    });
  }

  function renderFunnel(snap, selectedBracket) {
    const wrap = $("#probabilityFunnel");
    const mixture = mixtureByTicker(state.selectedMarketTicker);
    const econ = selectedEconomics();
    if (!mixture || !econ) {
      wrap.textContent = "No backend probability funnel is available for this selection.";
      return;
    }
    const sideSuffix = state.side === "yes" ? "Yes" : "No";
    const stages = [
      ["Mixture posterior mean", mixture[`pMean${sideSuffix}`], "Backend weighted mixture"],
      ["Mixture-count lower bound", mixture[`mixtureLowerBound${sideSuffix}`], "Backend conservative bound"],
      ["Weighted component lower bound", mixture[`weightedComponentLowerBound${sideSuffix}`], "Backend component bound"],
      ["Final pTrade", mixture[`pTrade${sideSuffix}`], "Backend final conservative probability"],
      ["Required probability", econ.requiredProbability, `${econ.priceBasis.replaceAll("_", " ")} price ${fmtMoney(econ.price)}`],
    ];
    const edge = econ.pSafe !== null && econ.requiredProbability !== null ? econ.pSafe - econ.requiredProbability : null;
    stages.push(["Probability edge", edge, "Display-only difference"]);
    stages.push([snap.executionState.replaceAll("_", " "), edge, snap.finalReasonCode]);
    wrap.innerHTML = stages.map((stage, index) => {
      const finalClass = index >= stages.length - 2 ? ` final ${edge !== null && edge >= 0 ? "good" : "bad"}` : "";
      return `${index ? '<div class="flow-arrow">v</div>' : ""}<div class="flow-node${finalClass}"><div><strong>${esc(stage[0])}</strong><span>${esc(stage[2])}</span></div><div class="num">${index === stages.length - 2 || index === stages.length - 1 ? fmtPp(stage[1]) : fmtPct(stage[1], 1)}</div></div>`;
    }).join("");
  }

  function renderEquations(snap) {
    const wrap = $("#equationTrace");
    const rows = (snap.equations || []).filter((item) => {
      const scope = item.scope || {};
      if (scope.modelKey && scope.modelKey !== state.modelKey) return false;
      if (scope.marketTicker && scope.marketTicker !== state.selectedMarketTicker) return false;
      if (scope.side && scope.side !== state.side) return false;
      return true;
    }).slice(0, 14);
    if (!rows.length) {
      wrap.textContent = "No backend equation rows match the selected controls.";
      return;
    }
    wrap.innerHTML = rows.map((row) => {
      const statusClass = row.status === "available" ? "good" : "bad";
      const result = row.substitutedExpression || (row.missingInputs || []).join(", ") || "--";
      return `<div class="eq ${statusClass}"><div class="eq-top"><span class="eq-name">${esc(row.label)}</span><span class="eq-result">${esc(formatEquationResult(row.result, row.units))}</span></div><div class="formula">${esc(row.formula)}</div><div class="plug">${esc(result)}</div></div>`;
    }).join("");
  }

  function formatEquationResult(result, units) {
    if (result === null || result === undefined) return "--";
    if (units === "probability" || units === "ratio") return fmtPct(result, 1);
    if (units === "dollars") return fmtMoney(result);
    if (units === "F") return fmtTemp(result);
    return String(result);
  }

  function renderMatrix(snap) {
    const wrap = $("#probabilityMatrix");
    const brackets = snap.outcomeMap.brackets || [];
    const mode = $("#matrixMode").value || "pMeanYes";
    const headers = ["Distribution", ...brackets.map((item) => item.label)];
    wrap.style.gridTemplateColumns = `1.25fr repeat(${brackets.length}, 1fr)`;
    let html = headers.map((label) => `<div class="mx head">${esc(label.replace(" F", ""))}</div>`).join("");
    snap.models.forEach((model) => {
      html += `<div class="mx label"><span class="model-chip"><i style="--c:${MODEL_COLORS[model.modelKey]}"></i>${esc(model.label)}</span></div>`;
      brackets.forEach((bracket) => {
        const probability = modelProbability(model, bracket.marketTicker);
        const value = probability ? probability[mode] : null;
        html += matrixCell(value, bracket.marketTicker === state.selectedMarketTicker, MODEL_COLORS[model.modelKey], "backend model");
      });
    });
    const mixtureRows = [
      ["Weighted mixture mean", "mean", "pMean"],
      ["Final conservative pTrade", "trade", "pTrade"],
    ];
    mixtureRows.forEach(([label, type, prefix]) => {
      html += `<div class="mx label"><strong>${esc(label)}</strong></div>`;
      brackets.forEach((bracket) => {
        const probability = mixtureByTicker(bracket.marketTicker);
        const suffix = mode.endsWith("No") ? "No" : "Yes";
        const value = probability ? probability[`${prefix}${suffix}`] : null;
        html += matrixCell(value, bracket.marketTicker === state.selectedMarketTicker, type === "mean" ? "#54738a" : "#47d7e8", type);
      });
    });
    wrap.innerHTML = html;
  }

  function matrixCell(value, selected, color, label) {
    const width = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value * 100));
    return `<div class="mx ${selected ? "selected" : ""}" style="--fill:${color};--w:${width}%"><strong>${fmtPct(value, value !== null && value < .01 ? 2 : 1)}</strong><small>${esc(label)}</small><i class="bar"></i></div>`;
  }

  function renderMarketWeather(snap) {
    const chart = $("#marketWeatherChart");
    const brackets = snap.outcomeMap.brackets || [];
    if (!brackets.length) {
      chart.textContent = "No verified outcome map is available.";
      return;
    }
    const width = 760, height = 320, pad = { l: 50, r: 15, t: 28, b: 48 };
    const groupWidth = (width - pad.l - pad.r) / brackets.length;
    const y = (value) => pad.t + (1 - Math.max(0, Math.min(1, value || 0))) * (height - pad.t - pad.b);
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Market versus weather probability chart">`;
    for (let tick = 0; tick <= 1.0001; tick += .25) {
      svg += `<line x1="${pad.l}" y1="${y(tick)}" x2="${width - pad.r}" y2="${y(tick)}" stroke="#1c2d3a"/><text x="${pad.l - 8}" y="${y(tick) + 4}" fill="#7892a5" font-size="10" text-anchor="end">${fmtPct(tick, 0)}</text>`;
    }
    brackets.forEach((bracket, index) => {
      const x0 = pad.l + index * groupWidth;
      const mixture = mixtureByTicker(bracket.marketTicker);
      const econ = snap.economics.find((item) => item.marketTicker === bracket.marketTicker && item.side === state.side);
      const suffix = state.side === "yes" ? "Yes" : "No";
      const values = [
        mixture ? mixture[`pMean${suffix}`] : null,
        mixture ? mixture[`pTrade${suffix}`] : null,
        econ?.price,
        econ?.requiredProbability,
      ];
      const colors = ["#54738a", "#47d7e8", "#f6bd60", "#ff7b72"];
      const barWidth = Math.min(15, (groupWidth - 18) / 4);
      values.forEach((value, barIndex) => {
        const v = Math.max(0, Math.min(1, value || 0));
        svg += `<rect x="${x0 + 9 + barIndex * (barWidth + 3)}" y="${y(v)}" width="${barWidth}" height="${y(0) - y(v)}" rx="3" fill="${colors[barIndex]}" opacity="${barIndex === 1 ? 1 : .82}"><title>${esc(bracket.label)} ${fmtPct(value, 1)}</title></rect>`;
      });
      svg += `<text x="${x0 + groupWidth / 2}" y="${height - 17}" fill="${bracket.marketTicker === state.selectedMarketTicker ? "#edf7ff" : "#7892a5"}" font-size="9" text-anchor="middle">${esc(bracket.label.replace(" F", ""))}</text>`;
      if (bracket.marketTicker === state.selectedMarketTicker) {
        svg += `<rect x="${x0 + 3}" y="${pad.t - 6}" width="${groupWidth - 6}" height="${height - pad.t - pad.b + 12}" fill="none" stroke="#47d7e8" rx="8"/>`;
      }
    });
    svg += `<g transform="translate(${pad.l},10)"><circle cx="0" cy="0" r="4" fill="#54738a"/><text x="8" y="4" fill="#89a2b6" font-size="9">mean</text><circle cx="58" cy="0" r="4" fill="#47d7e8"/><text x="66" y="4" fill="#89a2b6" font-size="9">pTrade</text><circle cx="132" cy="0" r="4" fill="#f6bd60"/><text x="140" y="4" fill="#89a2b6" font-size="9">price</text><circle cx="190" cy="0" r="4" fill="#ff7b72"/><text x="198" y="4" fill="#89a2b6" font-size="9">required</text></g></svg>`;
    chart.innerHTML = svg;
  }

  function renderSensitivity() {
    const chart = $("#priceSensitivityChart");
    const econ = selectedEconomics();
    const rows = econ?.priceSensitivity || [];
    text("#sensitivityBasis", econ ? `${econ.priceBasis.replaceAll("_", " ")} - fee grid` : "Backend grid");
    if (!econ || !rows.length) {
      chart.textContent = "No backend price-sensitivity rows are available for this selection.";
      return;
    }
    const width = 760, height = 305, pad = { l: 52, r: 20, t: 28, b: 45 };
    const prices = rows.map((row) => row.price).filter((value) => value !== null);
    const maxY = Math.max(.05, ...rows.map((row) => row.requiredProbability || 0), econ.pSafe || 0);
    const minX = Math.min(...prices);
    const maxX = Math.max(...prices);
    const x = (value) => pad.l + (value - minX) * (width - pad.l - pad.r) / Math.max(.01, maxX - minX);
    const y = (value) => pad.t + (maxY - Math.max(0, Math.min(maxY, value || 0))) * (height - pad.t - pad.b) / maxY;
    const path = rows.map((row, index) => `${index ? "L" : "M"}${x(row.price).toFixed(2)},${y(row.requiredProbability).toFixed(2)}`).join(" ");
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Price sensitivity chart">`;
    for (let tick = 0; tick <= maxY + 1e-9; tick += maxY / 4) {
      svg += `<line x1="${pad.l}" y1="${y(tick)}" x2="${width - pad.r}" y2="${y(tick)}" stroke="#1c2d3a"/><text x="${pad.l - 8}" y="${y(tick) + 4}" fill="#7892a5" font-size="10" text-anchor="end">${fmtPct(tick, 0)}</text>`;
    }
    svg += `<path d="${path}" fill="none" stroke="#47d7e8" stroke-width="2.7" stroke-linejoin="round" stroke-linecap="round"/>`;
    rows.forEach((row) => {
      svg += `<circle cx="${x(row.price)}" cy="${y(row.requiredProbability)}" r="3" fill="#47d7e8" stroke="#071018"><title>Price ${fmtMoney(row.price)} requires ${fmtPct(row.requiredProbability, 1)}</title></circle>`;
    });
    if (econ.pSafe !== null && econ.pSafe !== undefined) {
      svg += `<line x1="${pad.l}" y1="${y(econ.pSafe)}" x2="${width - pad.r}" y2="${y(econ.pSafe)}" stroke="#edf7ff" stroke-dasharray="6 5"/><text x="${width - pad.r}" y="${y(econ.pSafe) - 7}" fill="#edf7ff" font-size="10" text-anchor="end">pSafe ${fmtPct(econ.pSafe, 1)}</text>`;
    }
    if (econ.price !== null && econ.price !== undefined) {
      svg += `<line x1="${x(econ.price)}" y1="${pad.t}" x2="${x(econ.price)}" y2="${height - pad.b}" stroke="#f6bd60" stroke-width="2"/><text x="${Math.min(x(econ.price) + 5, width - 95)}" y="${pad.t + 14}" fill="#f6bd60" font-size="10">price ${fmtMoney(econ.price)}</text>`;
    }
    if (econ.maxAcceptablePrice !== null && econ.maxAcceptablePrice !== undefined && econ.maxAcceptablePrice >= minX && econ.maxAcceptablePrice <= maxX) {
      svg += `<line x1="${x(econ.maxAcceptablePrice)}" y1="${pad.t}" x2="${x(econ.maxAcceptablePrice)}" y2="${height - pad.b}" stroke="#53d69d" stroke-dasharray="4 5"/><text x="${pad.l}" y="${pad.t + 14}" fill="#53d69d" font-size="10">max acceptable ${fmtMoney(econ.maxAcceptablePrice)}</text>`;
    }
    [minX, maxX].forEach((price) => {
      svg += `<text x="${x(price)}" y="${height - 16}" fill="#89a2b6" font-size="10" text-anchor="middle">${fmtMoney(price)}</text>`;
    });
    svg += "</svg>";
    chart.innerHTML = svg;
  }

  function renderAudit(snap) {
    const wrap = $("#auditGrid");
    const gateRows = (snap.gates || []).map((gate) => ({
      key: gate.gateCode,
      value: gate.status,
      detail: gate.detail,
      cls: gate.status === "pass" ? "goodtxt" : gate.status === "fail" ? "badtxt" : "warntxt",
    }));
    const sourceRows = (snap.captureHealth.sources || []).map((source) => ({
      key: source.sourceKey,
      value: source.status,
      detail: source.detail,
      cls: source.status === "healthy" ? "goodtxt" : source.status === "missing" || source.status === "invalid" ? "badtxt" : "warntxt",
    }));
    const rows = [...gateRows, ...sourceRows].slice(0, 18);
    wrap.innerHTML = rows.map((row) => `<div class="audit-item"><div class="k">${esc(row.key.replaceAll("_", " "))}</div><div class="v ${row.cls}">${esc(row.value)}</div><div class="s">${esc(row.detail)}</div></div>`).join("");
  }

  async function refreshLive() {
    if (!state.live || document.hidden || !state.eventTicker) return;
    try {
      const base = `/api/strategy/current/events/${encodeURIComponent(state.eventTicker)}`;
      const bundle = await getJson(`${base}/probability-lab/latest${targetQuery()}`);
      const payload = withWeighting(bundle.explainability, bundle.weighting);
      if (!state.snapshot || payload.evaluationId !== state.snapshot.evaluationId) {
        acceptSnapshot(payload);
        await loadWeightHistory();
      }
      render();
    } catch (error) {
      state.failureAt = new Date();
      text("#labRefreshStatus", `API unavailable since ${fmtTime(state.failureAt.toISOString())}`);
      const banner = $("#labBanner");
      banner.hidden = false;
      banner.textContent = `Last complete evaluation retained. ${error.message}`;
    }
  }

  async function initializeEvent() {
    await loadLatest();
    await loadEvaluations();
    await loadWeightHistory();
    render();
  }

  async function init() {
    await loadEvents();
    if (!state.eventTicker) {
      text("#labRefreshStatus", "No event available");
      return;
    }
    await initializeEvent();
    $("#labEventSelect").addEventListener("change", async (event) => {
      const option = event.target.selectedOptions[0];
      state.eventTicker = option.value;
      state.targetDate = option.dataset.targetDate || null;
      state.live = true;
      await initializeEvent();
    });
    $("#labEvaluationRange").addEventListener("input", async (event) => {
      const item = state.evaluations[Number(event.target.value)];
      if (item) await loadEvaluation(item.evaluationId);
    });
    $("#labContractSelect").addEventListener("change", (event) => {
      state.selectedMarketTicker = event.target.value;
      render();
    });
    $("#labSideSelect").addEventListener("change", (event) => {
      state.side = event.target.value;
      render();
    });
    $("#labModelSelect").addEventListener("change", (event) => {
      state.modelKey = event.target.value;
      render();
    });
    $("#matrixMode").addEventListener("change", render);
    $("#labLiveButton").addEventListener("click", async () => {
      state.live = true;
      await loadLatest();
      await loadEvaluations();
      await loadWeightHistory();
    });
    window.setInterval(refreshLive, Math.max(5, Number(root.dataset.pollSeconds || 5)) * 1000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshLive(); });
  }

  init().catch((error) => {
    console.error("Probability Lab initialization failed", error);
    const banner = $("#labBanner");
    banner.hidden = false;
    banner.textContent = `Probability Lab failed to initialize: ${error.message}`;
    text("#labRefreshStatus", "Initialization failed");
  });
}());
