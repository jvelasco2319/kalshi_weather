'use strict';

const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const PT = 'America/Los_Angeles';
const timeFormatter = new Intl.DateTimeFormat('en-US', {timeZone: PT, hour: 'numeric', minute: '2-digit', hour12: true});
const dateFormatter = new Intl.DateTimeFormat('en-US', {timeZone: PT, month: 'short', day: 'numeric', year: 'numeric'});
const chartTimeFormatter = new Intl.DateTimeFormat('en-US', {timeZone: PT, hour: 'numeric', hour12: true});
let state = null;
let requestInFlight = false;
let localBusy = false;
let actionError = '';
let lastRenderKey = '';
let toastTimer = null;
let selectedGraph = 'v10';
let pollTimer = null;
let followManualJob = false;
let screenUpdatedAt = null;
let pollRequested = false;
let selectedMonitoringMonth = null;

function nextScreenRefresh(now) {
  const hour = Number(new Intl.DateTimeFormat('en-US', {timeZone: PT, hour: '2-digit', hourCycle: 'h23'}).format(now));
  const interval = hour >= 10 && hour < 17 ? 60000 : 900000;
  return new Date((Math.floor(now.getTime() / interval) + 1) * interval);
}
function scheduleNextPoll() {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  const now = new Date();
  const delay = followManualJob && state && state.job && state.job.busy ? 5000 : Math.max(500, nextScreenRefresh(now) - now);
  pollTimer = setTimeout(pollState, delay);
}

function number(value) {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
function validTime(value) {
  if (!value) return null;
  const time = new Date(value);
  return Number.isFinite(time.getTime()) ? time : null;
}
function timeText(value) {
  const time = validTime(value);
  return time ? timeFormatter.format(time) + ' PT' : 'time unavailable';
}
function fullTimeText(value) {
  const time = validTime(value);
  return time ? dateFormatter.format(time) + ', ' + timeFormatter.format(time) + ' PT' : 'time unavailable';
}
function dateText(value) {
  if (!value) return 'Date unavailable';
  const parsed = new Date(String(value).slice(0, 10) + 'T12:00:00-07:00');
  return Number.isFinite(parsed.getTime()) ? dateFormatter.format(parsed) : 'Date unavailable';
}
function money(value) {
  const n = number(value);
  return n === null ? '\u2014' : (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2);
}
function feeMoney(value) {
  const n = number(value);
  if (n === null) return '\u2014';
  return '$' + n.toFixed(n * 100 % 1 === 0 ? 2 : 4);
}
function signedMoney(value) {
  const n = number(value);
  return n === null ? '\u2014' : (n > 0 ? '+$' : n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2);
}
function percent(value) {
  const n = number(value);
  return n === null ? '\u2014' : (n * 100).toFixed(n > 0 && n < 0.01 ? 1 : 0) + '%';
}
function cents(value) {
  const n = number(value);
  if (n === null) return '\u2014';
  return (n * 100).toFixed((n * 100) % 1 === 0 ? 0 : 1) + '\u00a2';
}
function temperature(value) {
  const n = number(value);
  return n === null ? '\u2014' : n.toFixed(n % 1 === 0 ? 0 : 1) + '\u00b0F';
}
function write(id, value) {
  $(id).textContent = String(value === null || value === undefined ? '' : value);
}
function show(id, value) {
  $(id).hidden = !value;
}
function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}
function svgElement(tag, attrs, text) {
  const result = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) result.setAttribute(key, String(value));
  if (text !== undefined) result.textContent = String(text);
  return result;
}
function pnlClass(node, value) {
  const n = number(value);
  node.classList.remove('positive', 'negative', 'muted');
  node.classList.add(n === null ? 'muted' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'muted');
}
function quoteAge(market) {
  const time = validTime(market && market.updated_at_utc);
  const now = validTime(state && state.now_utc) || new Date();
  return time ? Math.max(0, (now - time) / 1000) : null;
}
function quoteFresh(market) {
  const age = quoteAge(market);
  return age !== null && age <= 120;
}
function ageText(age) {
  if (age === null) return 'not refreshed';
  if (age < 60) return Math.floor(age) + 's ago';
  if (age < 3600) return Math.floor(age / 60) + ' min ago';
  if (age < 86400) return Math.floor(age / 3600) + 'h ago';
  return Math.floor(age / 86400) + ' days ago';
}
function marketQuotes() {
  return Array.isArray(state && state.market && state.market.quotes) ? state.market.quotes : [];
}
function quoteTicker(quote) {
  return quote.ticker || quote.market_ticker || '';
}
function quoteLabel(quote) {
  return quote.label || quote.bracket_label || quoteTicker(quote);
}
function displayedForecast() {
  const official = state.forecast || {};
  if (official.kind === 'primary' && !official.excluded) return official;
  return state.monitoring_forecast && state.monitoring_forecast.latest || official;
}
function forecastMatchesMarket() {
  return state && state.market && displayedForecast().date === state.market.date;
}
function forecastProbability(ticker) {
  const forecast = displayedForecast();
  if (!forecastMatchesMarket() || !Array.isArray(forecast.tickers) || !Array.isArray(forecast.probabilities)) return null;
  return number(forecast.probabilities[forecast.tickers.indexOf(ticker)]);
}
function noAsk(quote) {
  if (number(quote.no_ask) !== null) return number(quote.no_ask);
  const bid = number(quote.yes_bid);
  return bid === null ? null : 1 - bid;
}
function noAskSize(quote) {
  if (number(quote.no_ask_size) !== null) return number(quote.no_ask_size);
  return number(quote.yes_bid_size);
}
function topIndex(probabilities) {
  if (!Array.isArray(probabilities)) return -1;
  let index = -1;
  let max = -Infinity;
  probabilities.forEach((value, i) => {
    const n = number(value);
    if (n !== null && n > max) {max = n; index = i;}
  });
  return index;
}

function renderStatus() {
  const market = state.market || {};
  const age = quoteAge(market);
  const fresh = quoteFresh(market);
  $('connection-status').className = 'connection-line ' + (fresh ? 'fresh' : 'stale');
  const kind = market.status ? String(market.status).replace(/_/g, ' ') : 'Saved snapshot';
  write('connection-text', age === null ? 'No market snapshot yet \u00b7 refresh to load public data' : (fresh ? 'Fresh market snapshot' : 'Saved market snapshot \u00b7 stale') + ' \u00b7 ' + ageText(age) + ' \u00b7 ' + kind);
  write('quote-status', age === null ? 'No quotes' : fresh ? 'Fresh quotes' : 'Stale quotes');
  $('quote-status').className = 'pill ' + (fresh ? 'primary-pill' : 'preview-pill');
  write('market-date', dateText(market.date || state.date));
  const job = state.job || {};
  const busy = localBusy || job.busy;
  const jobMessage = job.message || (busy ? 'Updating public data...' : '');
  write('job-banner', jobMessage);
  show('job-banner', Boolean(jobMessage && busy));
  const error = job.error || actionError;
  write('error-banner', error ? String(error) + ' Saved data remains visible; check its timestamps.' : '');
  show('error-banner', Boolean(error));
  write('data-notice', typeof state.notice === 'string' ? state.notice : Array.isArray(state.notice) ? state.notice.join(' ') : '');
  show('data-notice', Boolean($('data-notice').textContent));
  document.querySelectorAll('[data-action]').forEach(button => {button.disabled = Boolean(busy);});
  const test = state.test || {};
  const now = new Date(state.now_utc).getTime();
  const cutoff = new Date(state.date + 'T18:00:00Z').getTime();
  let captureReason = '';
  if (state.forecast && state.forecast.kind === 'primary') captureReason = "Today's test forecast is already saved.";
  else if (test.first_date && state.date < test.first_date) captureReason = 'The test begins ' + dateText(test.first_date) + '.';
  else if (test.last_date && state.date > test.last_date) captureReason = 'The registered test window has ended.';
  else if (now >= cutoff) captureReason = "Today's forecast cutoff has passed.";
  else if (cutoff - now > 20 * 60 * 1000) captureReason = 'Start collection about 15 minutes before the cutoff.';
  const captureButton = document.querySelector('[data-action="run"]');
  if (captureButton) {captureButton.disabled = Boolean(busy || captureReason); captureButton.title = captureReason;}
  let next = new Date(state.now_utc);
  next.setUTCHours(18, 0, 0, 0);
  if (next.getTime() <= now) next.setUTCDate(next.getUTCDate() + 1);
  if (test.first_date && next.toISOString().slice(0, 10) < test.first_date) next = new Date(test.first_date + 'T18:00:00Z');
  const nextWithinWindow = !test.last_date || next.toISOString().slice(0, 10) <= test.last_date;
  write('capture-guidance', (captureReason ? captureReason + ' ' : '') + (nextWithinWindow ? 'Next cutoff: ' + fullTimeText(next.toISOString()) + '. Start about 15 minutes before. ' : '') + 'No orders are sent.');
  $('record-trade-button').disabled = Boolean(busy);
  if ($('empty-record-button')) $('empty-record-button').disabled = Boolean(busy);
  write('refresh-button', '');
  const refresh = $('refresh-button');
  const icon = svgElement('svg', {viewBox: '0 0 24 24', 'aria-hidden': 'true'});
  icon.append(svgElement('path', {d: 'M20 7v5h-5M4 17v-5h5M5.2 8a7 7 0 0 1 11.5-3L20 8M4 16l3.3 3A7 7 0 0 0 18.8 16'}));
  refresh.append(icon, element('span', '', busy && job.action === 'refresh' ? 'Refreshing...' : 'Refresh market'));
  renderTracking();
}

function renderTracking() {
  const tracking = state.tracking || {};
  const enabled = Boolean(tracking.enabled && tracking.running);
  const interval = tracking.ui_interval_seconds === 60 ? 'every minute' : 'every 15 minutes';
  write('tracking-status', enabled ? 'Automatic tracking on \u00b7 screen updates ' + interval : 'Automatic tracking paused');
  write('tracking-timing', 'Screen refreshed ' + timeText(screenUpdatedAt) + ' \u00b7 next ' + timeText(nextScreenRefresh(new Date()).toISOString()) + '. Today\'s prices every 30 seconds; tomorrow\'s market, weather and new HRRR/GEFS runs checked every 15 minutes. 10 AM\u20135 PM Pacific: screen updates every minute.');
  write('tracking-toggle', enabled ? 'Pause tracking' : 'Resume tracking');
  $('tracking-toggle').disabled = !state.tracking;
  const errors = Object.entries(tracking.sources || {}).filter(([, source]) => source.error).map(([name, source]) => name + ': ' + source.error);
  if (tracking.daily_forecast && tracking.daily_forecast.error) errors.push(tracking.daily_forecast.error);
  write('tracking-error', errors.join(' \u00b7 '));
  show('tracking-error', Boolean(errors.length));
}

function renderWeatherContext() {
  const weather = state.weather || {};
  const context = weather.context || {};
  const lax = context.lax || {};
  const layers = Array.isArray(lax.cloud_layers) ? lax.cloud_layers : [];
  const labels = {CLR: 'Clear', SKC: 'Clear', NSC: 'No significant cloud', NCD: 'No cloud detected', FEW: 'Few clouds', SCT: 'Scattered', BKN: 'Broken clouds', OVC: 'Overcast', VV: 'Sky obscured'};
  const ranks = {CLR: 0, SKC: 0, NSC: 0, NCD: 0, FEW: 1, SCT: 2, BKN: 3, OVC: 4, VV: 5};
  const most = layers.slice().sort((a, b) => (ranks[b.coverage] || 0) - (ranks[a.coverage] || 0))[0];
  write('context-clouds', most ? labels[most.coverage] || '\u2014' : '\u2014');
  write('context-ceiling', number(lax.cloud_ceiling_ft) !== null ? number(lax.cloud_ceiling_ft).toLocaleString() + ' ft' : layers.length && layers.every(layer => !['BKN', 'OVC', 'VV'].includes(layer.coverage)) ? 'None reported' : '\u2014');
  const speed = number(lax.wind_speed_kt), direction = number(lax.wind_direction_degrees);
  const compass = direction === null ? 'Direction unavailable' : ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(direction / 45) % 8];
  write('context-wind', speed === null ? '\u2014' : speed === 0 ? 'Calm' : compass + ' ' + speed.toFixed(0) + ' kt');
  const gradient = number(context.pressure_difference_hpa);
  write('context-pressure', gradient === null ? '\u2014' : (gradient > 0 ? '+' : '') + gradient.toFixed(1) + ' hPa');
  const points = validSeries(weather.observations);
  const latest = points[points.length - 1];
  const previous = latest ? points.filter(point => {
    const age = (new Date(latest.time) - new Date(point.time)) / 60000;
    return age >= 30 && age <= 90;
  }).sort((a, b) => Math.abs((new Date(latest.time) - new Date(a.time)) / 60000 - 60) - Math.abs((new Date(latest.time) - new Date(b.time)) / 60000 - 60))[0] : null;
  const change = previous ? latest.temperature_f - previous.temperature_f : null;
  const minutes = previous ? Math.round((new Date(latest.time) - new Date(previous.time)) / 60000) : null;
  write('context-trend', change === null ? '\u2014' : (change > 0 ? '+' : '') + change.toFixed(1) + '\u00b0F / ' + minutes + ' min');
  write('weather-context-time', lax.observed_at_utc ? 'LAX report ' + fullTimeText(lax.observed_at_utc) + ' \u00b7 fetched ' + timeText(weather.updated_at_utc) : 'No LAX condition report available');
  write('weather-context-note', 'Pressure difference = LAX minus Daggett' + (context.daggett ? ' (Daggett report ' + timeText(context.daggett.observed_at_utc) + ')' : '') + '. Reports retain a 15-minute availability delay. The high in saved readings is preliminary; settlement follows Kalshi\'s official result. These conditions do not change the fixed V10 prediction.');
}

function forecastWaitStatus() {
  const test = state.test || {};
  const day = state.date;
  if (!day || !test.first_date || !test.last_date || day < test.first_date || day > test.last_date) return null;
  const cutoff = validTime(day + 'T18:00:00Z');
  const now = validTime(state.now_utc) || new Date();
  if (!cutoff) return null;
  const starts = new Date(cutoff.getTime() - 15 * 60000);
  if (now < cutoff && state.tracking && state.tracking.enabled === false) {
    return {kind: 'Tracking paused', description: "Today's V10 forecast has not been collected.", detail: 'Resume tracking before ' + timeText(cutoff.toISOString()) + ' to collect the scheduled forecast.'};
  }
  if (now < new Date(cutoff.getTime() + 60000) && state.job && state.job.busy && state.job.action === 'run') {
    return {kind: 'Collecting forecast', description: "Today's V10 inputs are being collected.", detail: 'Daily decision: ' + timeText(cutoff.toISOString()) + '. The saved forecast appears after the cutoff.'};
  }
  if (now < cutoff) {
    return {kind: 'Scheduled', description: "Today's V10 forecast is scheduled for " + timeText(cutoff.toISOString()) + '.', detail: 'Automatic collection starts at ' + timeText(starts.toISOString()) + '. Market prices and observations update separately.', collectionStart: starts.toISOString()};
  }
  if (now < new Date(cutoff.getTime() + 60000)) {
    return {kind: 'Awaiting forecast', description: 'Waiting for the on-time forecast to be saved.', detail: 'Daily decision: ' + timeText(cutoff.toISOString()) + '. Publication is allowed for one minute after the cutoff.'};
  }
  return {kind: 'No saved forecast', description: 'No on-time V10 forecast was saved for today.', detail: "Today's cutoff was " + timeText(cutoff.toISOString()) + '. A later preview is excluded from the daily test.'};
}

function renderOverview() {
  const forecast = displayedForecast();
  const market = state.market || {};
  const quotes = marketQuotes();
  const index = topIndex(forecast.probabilities);
  const kind = forecast.kind || 'none';
  const isPrimary = kind === 'primary' && !forecast.excluded;
  const waiting = index < 0 ? forecastWaitStatus() : null;
  write('forecast-kind', index < 0 ? waiting ? waiting.kind : 'No forecast' : isPrimary ? 'Saved test forecast' : kind === 'monitor' ? 'Monitoring preview' : 'Preview \u00b7 excluded from test');
  $('forecast-kind').className = 'pill ' + (isPrimary ? 'primary-pill' : 'preview-pill');
  let forecastLabel = '\u2014';
  if (index >= 0) {
    const ticker = (forecast.tickers || [])[index];
    const quote = quotes.find(q => quoteTicker(q) === ticker);
    forecastLabel = Array.isArray(forecast.labels) && forecast.labels[index] ? forecast.labels[index] : quote ? quoteLabel(quote) : ticker || 'Range unavailable';
  }
  write('forecast-bracket', forecastLabel);
  write('forecast-description', index < 0 ? waiting ? waiting.description : 'No V10 forecast is saved yet.' : percent(forecast.probabilities[index]) + ' chance \u00b7 most likely range' + (!forecastMatchesMarket() ? ' for ' + dateText(forecast.date) : ''));
  write('forecast-time', index < 0 ? waiting ? waiting.detail : 'Load a preview or collect a test forecast.' : 'Captured ' + fullTimeText(forecast.created_at_utc) + (forecast.decision_at_utc ? ' \u00b7 ' + (kind === 'monitor' ? 'fake-trade check ' : 'cutoff ') + timeText(forecast.decision_at_utc) : ''));

  const obs = validSeries(state.weather && state.weather.observations);
  const latest = obs.length ? obs[obs.length - 1] : null;
  const suppliedHigh = number(state.weather && state.weather.observed_high_f);
  const high = suppliedHigh !== null ? suppliedHigh : obs.length ? Math.max(...obs.map(p => p.temperature_f)) : null;
  write('observed-temperature', temperature(latest ? latest.temperature_f : null));
  write('observed-description', latest ? (state.weather && state.weather.observations_day_complete ? 'High so far ' : 'High in saved readings ') + temperature(high) : 'No observed temperature available');
  write('observed-time', latest ? 'Last reading ' + fullTimeText(latest.time) : 'Actual readings, not a forecast');

  const marketIndex = topIndex(market.probabilities);
  const bestBidQuote = quotes.filter(quote => number(quote.yes_bid) !== null).sort((a, b) => number(b.yes_bid) - number(a.yes_bid))[0];
  write('market-bracket', marketIndex >= 0 && quotes[marketIndex] ? quoteLabel(quotes[marketIndex]) : bestBidQuote ? quoteLabel(bestBidQuote) : 'Unavailable');
  write('market-description', marketIndex >= 0 ? percent(market.probabilities[marketIndex]) + ' market chance \u00b7 leading range' : bestBidQuote ? 'Highest YES bid: ' + cents(bestBidQuote.yes_bid) : quotes.length ? 'Market probability unavailable' : 'No market quotes available');
  write('market-time', marketIndex < 0 && bestBidQuote ? 'Market probability unavailable; this is a quote. ' + timeText(market.updated_at_utc) : market.updated_at_utc ? 'Quotes ' + fullTimeText(market.updated_at_utc) : 'Refresh to load public Kalshi quotes');
}

function renderBrackets() {
  const quotes = marketQuotes();
  const market = state.market || {};
  const forecast = displayedForecast();
  const tbody = $('bracket-rows');
  tbody.replaceChildren();
  if (!quotes.length) {
    const row = element('tr');
    const cell = element('td', 'empty-table', 'No market ranges are available. Refresh the public market snapshot.');
    cell.colSpan = 3;
    row.append(cell); tbody.append(row);
  }
  const probabilities = Array.isArray(market.probabilities) ? market.probabilities : null;
  const maxForecast = forecastMatchesMarket() && Array.isArray(forecast.probabilities) ? Math.max(...forecast.probabilities.map(p => number(p) === null ? -1 : number(p))) : -1;
  quotes.forEach((quote, index) => {
    const ticker = quoteTicker(quote);
    const p = forecastProbability(ticker);
    const row = element('tr', p !== null && p === maxForecast ? 'leading-range' : '');
    const labelCell = element('td');
    const name = element('div', 'bracket-name');
    name.append(element('i', 'range-dot'), element('span', '', quoteLabel(quote)));
    labelCell.append(name);
    if (p !== null && p === maxForecast) labelCell.append(element('div', 'best-label', 'V10 most likely'));
    row.append(labelCell);
    [{price: quote.yes_ask, size: quote.yes_ask_size, bid: quote.yes_bid, bidSize: quote.yes_bid_size}, {price: noAsk(quote), size: noAskSize(quote), bid: quote.no_bid, bidSize: quote.no_bid_size}].forEach(item => {
      const cell = element('td', 'quote-cell');
      cell.append(element('span', 'quote-number', cents(item.price)));
      if (number(item.price) !== null && number(item.size) !== null) cell.append(element('span', 'quote-size', number(item.size).toLocaleString() + ' available'));
      else if (number(item.price) === null) cell.append(element('span', 'quote-size', 'No ask'));
      cell.append(element('span', 'quote-exit', number(item.bid) === null ? 'No sell bid' : 'Sell bid ' + cents(item.bid) + (number(item.bidSize) === null ? '' : ' \u00b7 ' + number(item.bidSize).toLocaleString() + ' available')));
      row.append(cell);
    });
    tbody.append(row);
  });
  drawProbabilityComparison(quotes, probabilities);
  write('probability-date', dateText(market.date || state.date));
  const availableForecast = topIndex(forecast.probabilities) >= 0;
  write('probability-subtitle', availableForecast && !forecastMatchesMarket() ? 'The saved forecast is for ' + dateText(forecast.date) + '. It is not compared with this market.' : forecast.kind === 'monitor' ? 'Monitoring preview from newer runs \u00b7 excluded from daily scores and trade decisions. Captured ' + fullTimeText(forecast.created_at_utc) + '.' : availableForecast && (forecast.kind !== 'primary' || forecast.excluded) ? 'V10 preview \u00b7 excluded from the test. Captured ' + fullTimeText(forecast.created_at_utc) + '.' : 'Compare V10\'s chance with the market, then see the quoted entry price.');
  write('market-probability-note', probabilities ? 'Market chance is the normalized midpoint of complete two-sided YES quotes.' : 'Market probability unavailable: at least one range lacks a valid two-sided quote. Missing quotes stay blank.');
}

function drawProbabilityComparison(quotes, marketProbabilities) {
  const container = $('probability-chart');
  container.replaceChildren();
  if (!quotes.length) {
    container.append(element('div', 'probability-empty', 'Refresh the market to load temperature ranges.'));
    container.setAttribute('aria-label', 'No market ranges available for comparison.');
    write('probability-chart-note', '');
    return;
  }
  const rows = quotes.map((quote, index) => ({
    label: quoteLabel(quote),
    forecast: forecastProbability(quoteTicker(quote)),
    market: marketProbabilities ? number(marketProbabilities[index]) : null
  }));
  const values = rows.flatMap(row => [row.forecast, row.market]).filter(value => value !== null);
  const upper = Math.max(...values, 0) > 0.5 ? 1 : 0.5;
  const axis = element('div', 'probability-axis');
  const plot = element('div', 'probability-plot');
  const grid = element('div', 'probability-grid');
  for (let step = 5; step >= 0; step--) {
    const value = upper * step / 5;
    const tick = element('span', '', percent(value));
    tick.style.top = (100 - step * 20) + '%';
    axis.append(tick);
    const line = element('div', 'probability-gridline');
    line.style.top = (100 - step * 20) + '%';
    grid.append(line);
  }
  plot.append(grid);
  const groups = element('div', 'probability-groups');
  rows.forEach(row => {
    const group = element('div', 'probability-group');
    const pair = element('div', 'probability-pair');
    [['forecast', 'V10'], ['market', 'Kalshi']].forEach(([kind, label]) => {
      const value = row[kind];
      const column = element('div', 'probability-column ' + kind + (value === null ? ' missing' : ''));
      column.style.height = value === null ? '0%' : Math.max(0, Math.min(100, value / upper * 100)) + '%';
      column.setAttribute('aria-label', label + ' ' + row.label + ': ' + (value === null ? 'unavailable' : percent(value)));
      column.title = label + ' chance: ' + (value === null ? 'unavailable' : percent(value));
      column.append(element('span', 'probability-value', percent(value)));
      pair.append(column);
    });
    const range = element('div', 'probability-range', row.label.replace(/ F$/, '\u00b0F'));
    group.append(pair, range);
    groups.append(group);
  });
  plot.append(groups);
  container.append(axis, plot);
  container.setAttribute('aria-label', 'V10 and Kalshi probabilities for ' + dateText(state.market.date || state.date) + '. ' + rows.map(row => row.label + ': V10 ' + (row.forecast === null ? 'unavailable' : percent(row.forecast)) + ', Kalshi ' + (row.market === null ? 'unavailable' : percent(row.market))).join('. '));
  const hasMarket = rows.some(row => row.market !== null);
  const hasForecast = rows.some(row => row.forecast !== null);
  write('probability-chart-note', !hasMarket ? 'Kalshi chance unavailable. Dashed marks show missing data; they do not mean 0%.' : !hasForecast ? 'V10 forecast unavailable for this market date.' : 'Both bars use the same market date. Taller bars mean a higher estimated chance.');
}

function validSeries(series) {
  if (!Array.isArray(series)) return [];
  return series.map(point => ({time: point.time || point.observed_at || point.valid_time, temperature_f: number(point.temperature_f !== undefined ? point.temperature_f : point.temp_f)})).filter(point => validTime(point.time) && point.temperature_f !== null).sort((a, b) => validTime(a.time) - validTime(b.time));
}
function comparisonModels() {
  const comparison = state && state.weather && state.weather.comparison;
  return comparison && Array.isArray(comparison.models) ? comparison.models : [];
}
function comparisonColor(model, index) {
  return typeof model.color === 'string' && /^#[0-9a-f]{6}$/i.test(model.color) ? model.color : ['#b599ef', '#79b9ec', '#edc77f', '#eea5be'][index % 4];
}
function comparisonMatchesDay() {
  const weather = state && state.weather || {};
  const comparison = weather.comparison || {};
  return !comparison.date || !weather.date || comparison.date === weather.date;
}
function drawTemperature(comparisonView = false) {
  const container = $(comparisonView ? 'comparison-chart' : 'temperature-chart');
  const weather = state.weather || {};
  const series = comparisonView ? [
    {name: 'Observed', data: validSeries(weather.observations), color: '#66dfc2', width: 3, dashed: false},
    ...comparisonModels().map((model, index) => ({name: model.label || model.id || 'Unnamed model', data: comparisonMatchesDay() ? validSeries(model.points) : [], color: comparisonColor(model, index), width: 2, dashed: true, alias: Boolean(model.alias_of)}))
  ] : [
    {name: 'Observed', data: validSeries(weather.observations), color: '#66dfc2', width: 3, dashed: false},
    ...['hrrr', 'gefs'].map(id => {
      const feed = weather.hourly_forecasts || {};
      const model = (feed.models || []).find(m => m.id === id) || {};
      return {name: id === 'hrrr' ? 'HRRR forecast' : 'GEFS mean', data: feed.date === state.date ? validSeries(model.points) : [], color: id === 'hrrr' ? '#88aefb' : '#edc77f', width: 2, dashed: true};
    })
  ];
  const points = series.flatMap(s => s.data);
  container.replaceChildren();
  if (!points.length) {
    const empty = element('div', 'chart-empty');
    empty.append(element('span', 'empty-icon', '~'), element('strong', '', comparisonView ? 'No comparison readings yet' : 'No weather readings yet'), element('span', '', comparisonView ? 'Refresh other models to save their forecasts.' : 'Refresh the market to load observations.'));
    container.append(empty);
    container.setAttribute('aria-label', 'No observed readings or weather forecast points are available.');
    return;
  }
  const w = 920, h = 280, left = 40, right = 17, top = 17, bottom = 37;
  let minX = Math.min(...points.map(p => validTime(p.time).getTime()));
  let maxX = Math.max(...points.map(p => validTime(p.time).getTime()));
  if (maxX === minX) {minX -= 3600000; maxX += 3600000;}
  const temps = points.map(p => p.temperature_f);
  const low = Math.min(...temps), high = Math.max(...temps);
  const step = high - low > 25 ? 10 : high - low > 12 ? 5 : 2;
  const minY = Math.floor((low - 1) / step) * step;
  const maxY = Math.ceil((high + 1) / step) * step;
  const x = value => left + (validTime(value).getTime() - minX) / (maxX - minX) * (w - left - right);
  const y = value => h - bottom - (value - minY) / (maxY - minY) * (h - bottom - top);
  const svg = svgElement('svg', {viewBox: '0 0 ' + w + ' ' + h, preserveAspectRatio: 'none', 'aria-hidden': 'true'});
  for (let value = minY; value <= maxY; value += step) {
    const coord = y(value);
    svg.append(svgElement('line', {x1: left, x2: w - right, y1: coord, y2: coord, class: 'chart-gridline'}));
    svg.append(svgElement('text', {x: left - 10, y: coord + 3, 'text-anchor': 'end', class: 'chart-axis'}, value));
  }
  const spanHours = (maxX - minX) / 3600000;
  const tickStepHours = spanHours > 36 ? 12 : spanHours > 15 ? 6 : spanHours > 8 ? 3 : 2;
  const tickStart = Math.ceil(minX / (tickStepHours * 3600000)) * tickStepHours * 3600000;
  for (let value = tickStart; value <= maxX; value += tickStepHours * 3600000) {
    const coord = x(new Date(value).toISOString());
    svg.append(svgElement('text', {x: coord, y: h - 12, 'text-anchor': 'middle', class: 'chart-axis'}, chartTimeFormatter.format(new Date(value))));
  }
  series.slice().reverse().forEach(s => {
    if (!s.data.length) return;
    const pathData = s.data.map((point, i) => (i === 0 ? 'M' : 'L') + x(point.time).toFixed(2) + ',' + y(point.temperature_f).toFixed(2)).join(' ');
    const path = svgElement('path', {d: pathData, fill: 'none', stroke: s.color, 'stroke-width': s.width, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke'});
    if (s.dashed) path.setAttribute('stroke-dasharray', s.alias ? '1 6' : '6 5');
    if (s.alias) path.append(svgElement('title', {}, s.name + ': same forecast run; ' + s.data.length + ' saved points.'));
    svg.append(path);
    if (!s.alias) s.data.forEach(point => {
      const circle = svgElement('circle', {cx: x(point.time), cy: y(point.temperature_f), r: s.name === 'Observed' ? 3 : 2.5, fill: s.color, stroke: '#141d29', 'stroke-width': 1});
      circle.append(svgElement('title', {}, s.name + ': ' + temperature(point.temperature_f) + ' at ' + fullTimeText(point.time)));
      svg.append(circle);
    });
  });
  const last = series[0].data.at(-1);
  if (last && x(last.time) < w - 87) svg.append(svgElement('text', {x: x(last.time) + 9, y: y(last.temperature_f) - 9, fill: '#8ae9d2', 'font-size': 11, 'font-family': 'Segoe UI, Arial, sans-serif'}, temperature(last.temperature_f)));
  container.append(svg);
  container.setAttribute('aria-label', 'Temperature over time in Pacific time. ' + series.map(s => s.name + ': ' + s.data.length + ' points').join('. ') + '. Observed range ' + temperature(series[0].data.length ? Math.min(...series[0].data.map(p => p.temperature_f)) : null) + ' to ' + temperature(series[0].data.length ? Math.max(...series[0].data.map(p => p.temperature_f)) : null) + '.');
  const dates = new Set(points.map(p => dateFormatter.format(validTime(p.time))));
  const live = weather.hourly_forecasts || {};
  const note = 'Latest available weather forecasts. HRRR runs hourly; GEFS mean runs every six hours. V10 keeps its fixed daily inputs.' + (live.updated_at_utc ? ' Last forecast check ' + fullTimeText(live.updated_at_utc) + '.' : ' Waiting for the first forecast check.') + (dates.size > 1 ? ' Points span ' + [...dates].join(' and ') + '.' : '');
  if (!comparisonView) write('weather-note', note);
}

function renderHourlyForecasts() {
  const feed = state.weather && state.weather.hourly_forecasts || {};
  const rows = $('hourly-forecast-scores'); rows.replaceChildren();
  (feed.models || []).forEach(model => {
    const row = element('div', 'comparison-score');
    const name = element('span', 'comparison-score-name');
    const dot = element('i', 'legend-dot'); dot.style.backgroundColor = model.color;
    name.append(dot, document.createTextNode(model.label || model.id));
    const error = number(model.metrics && model.metrics.mae_f);
    const matched = number(model.metrics && model.metrics.matched_points) || 0;
    row.append(name, element('strong', 'comparison-score-value' + (matched ? '' : ' muted'), matched && error !== null ? error.toFixed(2) + '\u00b0' : '\u2014'));
    const available = Array.isArray(model.points) && model.points.length;
    const detail = (available ? 'Issued ' + fullTimeText(model.issue_time_utc) + ' \u00b7 saved ' + timeText(model.retrieved_at_utc) : model.message || 'Forecast not available yet') + (matched ? ' \u00b7 ' + matched + ' matched readings' : ' \u00b7 no score yet') + (model.retained_previous_run ? ' \u00b7 keeping the last available run' : '');
    row.append(element('div', 'comparison-score-detail' + (available ? '' : ' warning'), detail));
    rows.append(row);
  });
  if (!(feed.models || []).length) rows.append(element('p', 'comparison-empty', 'Waiting for the first HRRR/GEFS forecast check.'));
}

function renderComparison() {
  const comparison = state.weather && state.weather.comparison || {};
  const models = comparisonModels();
  const sharedTimes = number(comparison.shared_matched_points) > 0;
  write('comparison-score-label', sharedTimes ? 'Today\'s error at the same times' : 'Today\'s average temperature error');
  write('comparison-rules', 'Only readings after the forecast was saved count. ' + (sharedTimes ? 'Errors use the same observation times across models. ' : 'Models report at different intervals, so matched reading counts can differ. ') + 'Today\'s readings cannot establish a winning model. These comparisons do not change V10.');
  const legend = $('comparison-legend');
  legend.replaceChildren();
  const observed = element('span');
  observed.append(element('i', 'legend-dot observed-dot'), element('span', '', 'Observed'));
  legend.append(observed);
  const scores = $('comparison-scores');
  scores.replaceChildren();
  models.forEach((model, index) => {
    const label = model.label || model.id || 'Unnamed model';
    const points = comparisonMatchesDay() ? validSeries(model.points) : [];
    const status = String(model.status || (points.length ? 'saved' : 'unavailable')).replace(/_/g, ' ').toLowerCase();
    const line = element('span');
    const marker = element('i', 'legend-line'); marker.style.backgroundColor = comparisonColor(model, index);
    line.append(marker, element('span', '', label));
    if (!points.length) line.append(element('span', 'comparison-legend-status', '(unavailable)'));
    else if (model.alias_of) line.append(element('span', 'comparison-legend-status', '(same run)'));
    legend.append(line);

    const row = element('div', 'comparison-score');
    const name = element('span', 'comparison-score-name');
    const dot = element('i', 'legend-dot'); dot.style.backgroundColor = comparisonColor(model, index);
    name.append(dot, element('span', '', label));
    const metrics = model.metrics || {};
    const matched = number(sharedTimes ? metrics.shared_matched_points : metrics.matched_points) || 0;
    const error = number(sharedTimes ? metrics.shared_mae_f : metrics.mae_f);
    const scored = comparisonMatchesDay() && matched > 0 && error !== null;
    row.append(name, element('strong', 'comparison-score-value' + (scored ? '' : ' muted'), scored ? error.toFixed(2) + '\u00b0' : '\u2014'));
    let detail = scored ? matched + (sharedTimes ? ' same-time ' : ' matched ') + (matched === 1 ? 'reading' : 'readings') : 'No score yet';
    if (!points.length) detail += ' \u00b7 ' + (model.message || model.reason || status);
    else if (model.retrieved_at_utc) detail += ' \u00b7 saved ' + timeText(model.retrieved_at_utc);
    if (model.issue_time_utc) detail += ' \u00b7 issued ' + fullTimeText(model.issue_time_utc);
    const info = element('div', 'comparison-score-detail' + (!points.length ? ' warning' : ''), detail);
    row.append(info);
    if (model.alias_of) {
      const source = models.find(candidate => candidate.id === model.alias_of);
      row.append(element('div', 'comparison-score-detail', 'Same forecast run as ' + (source ? source.label || source.id : model.alias_of) + '; not an independent model.'));
    }
    scores.append(row);
  });
  if (!models.length) scores.append(element('p', 'comparison-empty', 'No comparison forecasts saved. Refresh other models to load the available feeds.'));
  let notice = typeof comparison.notice === 'string' ? comparison.notice : Array.isArray(comparison.notice) ? comparison.notice.join(' ') : models.length ? 'The same LAX observations are shown in both views.' : 'Comparison models have not been loaded yet.';
  const currentHour = Number(new Intl.DateTimeFormat('en-US', {timeZone: PT, hour: '2-digit', hourCycle: 'h23'}).format(validTime(state.now_utc) || new Date()));
  if (!models.some(model => validSeries(model.points).length) && currentHour < 6 && state.tracking && state.tracking.enabled) notice += ' Automatic comparison capture starts at 6 AM PT.';
  if (!comparisonMatchesDay()) notice = 'Comparison forecasts are for ' + dateText(comparison.date) + ', so they are not plotted against today\'s observations. ' + notice;
  write('comparison-notice', notice);
  drawTemperature(true);
}

function selectGraph(view, focus = false) {
  selectedGraph = view === 'comparison' ? 'comparison' : 'v10';
  write('temperature-description', selectedGraph === 'v10' ? 'Actual readings beside the latest HRRR and GEFS forecasts.' : 'The same actual readings beside the comparison forecasts.');
  ['v10', 'comparison'].forEach(kind => {
    const button = $(kind + '-graph-tab');
    const selected = kind === selectedGraph;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(kind + '-graph-panel').hidden = !selected;
  });
  if (focus) $(selectedGraph + '-graph-tab').focus();
}

function renderAutomaticPractice() {
  const auto = state.automatic_practice || {};
  const tracking = state.tracking || {};
  const enabled = tracking.enabled && tracking.running;
  write('auto-practice-status', enabled ? 'Automatic fake trades on' : 'Automatic fake trades paused');
  const decision = auto.decision || auto.latest_decision;
  let detail = enabled ? (auto.next_at_utc ? 'Next check: ' + fullTimeText(auto.next_at_utc) + '.' : 'The registered test period has ended.') : 'Resume tracking to enable the daily practice check.';
  if (decision) {
    const trade = (state.trades && state.trades.items || []).find(item => item.id === decision.entry_id);
    const result = decision.status === 'ENTERED' && trade ? 'Auto practice: NO ' + trade.label + ' \u00b7 ' + trade.quantity + ' contracts \u00b7 ' + money(trade.entry_cost) + ' including fees. Estimated return ' + percent(trade.expected_net_return) + '.' : 'Skipped ' + dateText(decision.date) + ': ' + (decision.reason || 'No eligible trade') + '.';
    detail = result + ' ' + detail;
  }
  if (tracking.practice_error) detail = 'Practice check error: ' + tracking.practice_error + '. ' + detail;
  write('auto-practice-detail', detail);
}

function renderAccounts() {
  const trades = state.trades || {};
  const practice = trades.practice || trades.summary || {};
  const initial = number(practice.initial_balance);
  const cash = number(practice.cash);
  const equity = number(practice.marked_equity);
  const count = Array.isArray(trades.items) ? trades.items.filter(item => (item.kind || item.mode) === 'practice').length : 0;
  write('practice-equity', money(equity !== null ? equity : count === 0 ? cash !== null ? cash : initial : null));
  write('practice-equity-note', number(practice.unpriced_count) > 0 ? 'Equity unavailable while open entries are unpriced' : count > 0 ? 'Cash + marked practice entries' : 'Starting balance \u00b7 practice only');
  write('practice-cash', money(cash !== null ? cash : initial));
  write('practice-budget', money(practice.next_budget));
  write('practice-open-pnl', count ? signedMoney(practice.unrealized_pnl) : '\u2014');
  pnlClass($('practice-open-pnl'), count ? practice.unrealized_pnl : null);
  write('practice-settled-pnl', count ? signedMoney(practice.realized_pnl) : '\u2014');
  pnlClass($('practice-settled-pnl'), count ? practice.realized_pnl : null);
  const unpriced = number(practice.unpriced_count) || 0;
  write('practice-unpriced', unpriced + ' open ' + (unpriced === 1 ? 'entry needs' : 'entries need') + ' a fresh exit quote with enough size.');
  show('practice-unpriced', unpriced > 0);
  const manual = trades.manual || {};
  show('manual-summary', number(manual.count) > 0);
  write('manual-summary-pnl', 'Open ' + signedMoney(manual.unrealized_pnl) + ' \u00b7 settled ' + signedMoney(manual.realized_pnl));
  write('manual-summary-detail', (number(manual.count) || 0) + ' recorded ' + (number(manual.count) === 1 ? 'entry' : 'entries') + ' \u00b7 not linked to your account' + (number(manual.unpriced_count) > 0 ? ' \u00b7 ' + manual.unpriced_count + ' unpriced' : ''));
}

function tradeField(item, fields) {
  for (const field of fields) if (item[field] !== undefined) return item[field];
  return null;
}
function renderTrades() {
  const trades = state.trades || {};
  const items = Array.isArray(trades.items) ? trades.items : [];
  write('trade-count', items.length + (items.length === 1 ? ' entry' : ' entries'));
  const list = $('trade-list');
  list.replaceChildren();
  if (!items.length) {
    const empty = element('div', 'trade-empty');
    const icon = element('div', 'empty-trade-icon');
    const svg = svgElement('svg', {viewBox: '0 0 28 28', 'aria-hidden': 'true'});
    svg.append(svgElement('path', {d: 'M5 21V7M5 21h18M9 17l5-5 4 2 5-7'}));
    icon.append(svg);
    const description = element('div');
    description.append(element('strong', '', 'No trades recorded yet'), element('p', '', 'The daily check will add an eligible fake trade here. You can also record an entry yourself.'));
    const button = element('button', 'button button-secondary', 'Record first trade');
    button.type = 'button'; button.id = 'empty-record-button'; button.addEventListener('click', openTradeDialog);
    button.disabled = Boolean(localBusy || (state.job && state.job.busy));
    empty.append(icon, description, button); list.append(empty);
  }
  items.slice().reverse().forEach(item => {
    const kind = item.kind || item.mode;
    const isPractice = kind === 'practice';
    const closed = ['settled', 'closed', 'finalized'].includes(String(item.status).toLowerCase());
    const card = element('article', 'trade-item');
    const titleColumn = element('div');
    const title = element('div', 'trade-title');
    const label = item.label || item.bracket_label || (marketQuotes().find(q => quoteTicker(q) === item.ticker) || {}).label || item.ticker;
    title.append(element('strong', '', String(item.side || '').toUpperCase() + ' ' + label), element('span', 'pill ' + (isPractice ? 'primary-pill' : 'quiet-pill'), item.origin === 'automatic' ? 'Auto practice' : isPractice ? 'Practice' : 'Recorded real'));
    titleColumn.append(title, element('div', 'trade-position', (number(item.quantity) || 0) + ' contracts \u00b7 entry ' + cents(item.entry_price) + (item.date ? ' \u00b7 ' + dateText(item.date) : '')));
    if (item.origin === 'automatic') titleColumn.append(element('div', 'trade-position', 'V10 estimated win chance ' + percent(item.model_probability) + ' \u00b7 estimated return ' + percent(item.expected_net_return) + ' after fees'));
    const cost = tradeField(item, ['entry_cost', 'cost', 'entry_outlay']);
    const entryValue = element('div', 'trade-value');
    entryValue.append(element('span', 'label', 'Entry cost'), element('strong', '', money(cost)), element('span', 'sub-label', 'Including ' + feeMoney(item.entry_fee) + (isPractice ? ' estimated fee' : ' actual fee')));
    const mark = tradeField(item, ['marked_value', 'current_value', 'settlement_value']);
    const markValue = element('div', 'trade-value');
    markValue.append(element('span', 'label', closed ? 'Settlement value' : 'At current exit bid'), element('strong', '', money(mark)), element('span', 'sub-label', closed ? 'Official contract result' : number(mark) === null ? item.mark_reason || 'Fresh bid / size unavailable' : 'Before any exit fee'));
    const change = closed ? item.realized_pnl : item.unrealized_pnl;
    const pnl = element('div', 'trade-value');
    const profit = element('strong', '', signedMoney(change)); pnlClass(profit, change);
    pnl.append(element('span', 'label', closed ? 'Settled profit / loss' : 'Open profit / loss'), profit, element('span', 'trade-status', closed ? 'Settled' : number(mark) === null ? 'Open \u00b7 unpriced' : 'Open \u00b7 estimate'));
    card.append(titleColumn, entryValue, markValue, pnl); list.append(card);
  });
  write('trade-mark-note', 'Entries are tracked to settlement. Practice fees are estimates. Open profit / loss uses bid prices before any exit fee, only when enough size is available. Recorded real results use your entered details and the contract payout; they are not verified account results.');
  drawPracticeHistory(trades.history);
}

function drawPracticeHistory(history) {
  const container = $('trade-history-chart');
  const isRealizedHistory = Array.isArray(history) && history.some(row => row.realized_pnl !== undefined);
  const valueOf = row => isRealizedHistory ? number(row.realized_pnl) : number(row.marked_equity !== undefined ? row.marked_equity : row.equity);
  const rows = Array.isArray(history) ? history.filter(row => validTime(row.time || row.time_utc || row.at_utc) && valueOf(row) !== null) : [];
  if (rows.length < 2) {show('trade-history-chart', false); return;}
  const points = rows.map(row => ({time: row.time || row.time_utc || row.at_utc, value: valueOf(row)}));
  drawSmallLine(container, points, isRealizedHistory ? 'Cumulative settled practice profit / loss' : 'Practice account value', '#66dfc2', isRealizedHistory ? signedMoney : money);
  show('trade-history-chart', true);
}
function drawSmallLine(container, points, name, color, formatter) {
  container.replaceChildren();
  const width = 960, height = 115, left = 48, right = 12, top = 20, bottom = 23;
  const values = points.map(p => p.value);
  let low = Math.min(...values), high = Math.max(...values);
  const pad = Math.max((high - low) * 0.15, name.includes('accuracy') ? 0.05 : 1);
  low -= pad; high += pad;
  const timeValues = points.map(p => validTime(p.time).getTime());
  const minX = Math.min(...timeValues), maxX = Math.max(...timeValues);
  const x = i => left + (maxX > minX ? (timeValues[i] - minX) / (maxX - minX) : i / Math.max(1, points.length - 1)) * (width - left - right);
  const y = value => height - bottom - (value - low) / (high - low) * (height - bottom - top);
  const svg = svgElement('svg', {viewBox: '0 0 ' + width + ' ' + height, preserveAspectRatio: 'none', role: 'img', 'aria-label': name + ' from ' + fullTimeText(points[0].time) + ' to ' + fullTimeText(points.at(-1).time)});
  svg.append(svgElement('text', {x: left, y: 10, class: 'chart-label'}, name));
  [low + pad, high - pad].forEach(value => {
    svg.append(svgElement('line', {x1: left, x2: width - right, y1: y(value), y2: y(value), class: 'chart-gridline'}));
    svg.append(svgElement('text', {x: left - 8, y: y(value) + 3, 'text-anchor': 'end', class: 'chart-axis'}, formatter(value)));
  });
  svg.append(svgElement('path', {d: points.map((point, i) => (i ? 'L' : 'M') + x(i).toFixed(2) + ',' + y(point.value).toFixed(2)).join(' '), stroke: color, fill: 'none', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke'}));
  points.forEach((point, i) => {
    const circle = svgElement('circle', {cx: x(i), cy: y(point.value), r: 2.5, fill: color});
    circle.append(svgElement('title', {}, formatter(point.value) + ' at ' + fullTimeText(point.time))); svg.append(circle);
  });
  svg.append(svgElement('text', {x: left, y: height - 3, class: 'chart-axis'}, fullTimeText(points[0].time)));
  svg.append(svgElement('text', {x: width - right, y: height - 3, 'text-anchor': 'end', class: 'chart-axis'}, fullTimeText(points.at(-1).time)));
  container.append(svg);
}

function renderTomorrow() {
  const market = state.tomorrow_market || {};
  write('tomorrow-date', dateText(market.date));
  const quotes = Array.isArray(market.quotes) ? market.quotes : [];
  const receipt = market.updated_at_utc ? ' Last check ' + fullTimeText(market.updated_at_utc) + '.' : '';
  const available = market.status === 'open' && quotes.length === 6;
  const notOpenYet = quotes.length === 6 && quotes.every(q => q.status === 'initialized');
  const fresh = available && quotes.every(q => q.fresh);
  write('tomorrow-status', (notOpenYet ? 'Listed; trading has not opened yet. Prices will appear when Kalshi opens this market.' : available ? (fresh ? 'Market available \u00b7 six ranges saved.' : 'Saved prices need a new check.') : market.status === 'not_listed' ? 'Not listed yet. The automatic check will keep looking.' : market.status === 'unavailable' ? 'Waiting for the first automatic check.' : 'Market is ' + (market.status || 'unavailable') + '.') + receipt);
  show('tomorrow-details', quotes.length > 0);
  const rows = $('tomorrow-rows'); rows.replaceChildren();
  quotes.forEach(q => {
    const row = element('tr');
    row.append(element('td', '', q.label), element('td', '', cents(q.yes_ask)), element('td', '', cents(q.no_ask)));
    rows.append(row);
  });
}

function renderMonth() {
  const months = state.monitoring_months && state.monitoring_months.length ? state.monitoring_months : [state.month || {}];
  if (!months.some(m => m.month_id === selectedMonitoringMonth)) selectedMonitoringMonth = months.at(-1).month_id;
  const month = months.find(m => m.month_id === selectedMonitoringMonth) || {};
  const select = $('monitoring-month'); select.replaceChildren();
  months.forEach(m => {const option = element('option', '', m.month || 'This month'); option.value = m.month_id; select.append(option);});
  select.value = selectedMonitoringMonth;
  write('month-title', (month.month || 'This month') + ' monitoring');
  write('month-summary', (number(month.decisions_recorded) || 0) + ' of ' + (number(month.planned_days) || 0) + ' daily checks recorded \u00b7 ' + (number(month.skipped_days) || 0) + ' skipped.');
  write('month-forecasts', number(month.forecasts_saved) || 0);
  write('month-trades', number(month.automatic_entries) || 0);
  write('month-pnl', signedMoney(month.realized_practice_pnl));
  pnlClass($('month-pnl'), month.realized_practice_pnl);
  write('month-coverage', (month.start_date ? dateText(month.start_date) + ' through ' + dateText(month.end_date) + '. ' : '') + (number(month.missing_forecasts) || 0) + ' missed forecasts; ' + (number(month.missing_practice_checks) || 0) + ' missing practice checks. The computer must be awake and the server running. Results and skipped days stay saved across restarts.');
  const rows = $('month-rows'); rows.replaceChildren();
  (month.days || []).forEach(day => {
    const row = element('tr');
    const practice = day.practice === 'entered' ? 'Auto practice' : day.practice === 'skipped' ? 'Skipped: ' + (day.reason || 'No eligible trade') : day.practice === 'missing' ? 'No saved check' : 'Scheduled';
    const forecast = day.forecast === 'saved' ? 'Saved' : day.forecast === 'missing' ? 'Missed' : 'Scheduled';
    const result = day.forecast_correct === true ? 'Correct range' : day.forecast_correct === false ? 'Different range' : 'Pending';
    row.append(element('td', '', dateText(day.date)), element('td', '', forecast), element('td', '', practice), element('td', '', result), element('td', '', signedMoney(day.realized_practice_pnl)));
    rows.append(row);
  });
}

function renderTest() {
  const test = state.test || {};
  write('test-forecasts', number(test.forecast_count) || 0);
  write('test-settled', number(test.settled_count) || 0);
  write('test-pending', number(test.pending_count) || 0);
  const days = Array.isArray(test.days) ? test.days.filter(row => typeof row.correct === 'boolean') : [];
  const accuracy = days.length ? days.filter(row => row.correct).length / days.length : number(test.accuracy);
  write('test-accuracy', percent(accuracy));
  write('test-note', number(test.settled_count) > 0 ? 'Accuracy tracks whether V10\'s most likely range matched the official result. Forecast scores and trade profit are measured separately.' : 'No scored days yet. Forecast accuracy and trade profit are measured separately.');
  show('test-chart', days.length >= 2);
  if (days.length >= 2) {
    let correct = 0;
    const points = days.slice().sort((a, b) => String(a.date).localeCompare(String(b.date))).map((row, i) => {correct += row.correct ? 1 : 0; return {time: String(row.date).slice(0, 10) + 'T20:00:00Z', value: correct / (i + 1)};});
    drawSmallLine($('test-chart'), points, 'Cumulative top-range accuracy', '#88aefb', percent);
  }
  const source = state.market && state.market.source;
  write('source-description', 'This market uses ' + (typeof source === 'string' ? source : source && (source.name || source.source) ? source.name || source.source : 'the settlement source in its Kalshi contract') + '. LAX observations help follow the weather; the official contract result decides settlement. V10 was calibrated on older NWS daily highs; today\'s source rules may differ, so this is a new-data test.');
  const forecast = state.forecast || {};
  write('cutoff-description', 'The daily test cutoff is 18:00 UTC' + (forecast.decision_at_utc ? ' (' + timeText(forecast.decision_at_utc) + ' for this date)' : '') + '. A forecast collected late is a preview and cannot count in the test.');
  write('source-times', 'Market snapshot: ' + fullTimeText(state.market && state.market.updated_at_utc) + '. Weather snapshot: ' + fullTimeText(state.weather && state.weather.updated_at_utc) + '. Forecast capture: ' + fullTimeText(forecast.created_at_utc) + '.');
  write('reference-note', state.reference_sha ? 'Layout inspired by your GitHub main UI (' + String(state.reference_sha).slice(0, 7) + '), reduced to one page.' : 'One page for the market, weather, and recorded trades.');
}

function renderMonitoring() {
  const monitoring = state.monitoring_forecast || {};
  const history = Array.isArray(monitoring.history) ? monitoring.history : [];
  write('monitoring-summary', history.length ? history.length + ' saved preview' + (history.length === 1 ? '' : 's') + ' \u00b7 last ' + timeText(history[history.length - 1].created_at_utc) : 'Waiting for the first estimate');
  write('monitoring-note', 'Each new complete HRRR or GEFS run triggers a V10 preview with the same saved weights and newer forecast inputs. This timing is experimental. The automatic fake-trade check stays at 18:00 UTC (' + timeText(state.date + 'T18:00:00Z') + ' today) and uses the official daily forecast. Earlier forecast samples are retained once their target time passes. Official capture takes priority near the cutoff.');
  const pressure = monitoring.latest && monitoring.latest.pressure_evidence;
  if (pressure) $('monitoring-note').textContent += pressure.pressure_missing ? ' Pressure was unavailable for the latest preview; V10\'s saved neutral adjustment was used.' : ' Latest pressure adjustment: ' + pressure.pressure_and_flow + '.';
  write('monitoring-error', monitoring.error || ''); show('monitoring-error', Boolean(monitoring.error));
  const rows = $('monitoring-rows'); rows.replaceChildren();
  history.slice().reverse().forEach(saved => {
    const index = topIndex(saved.probabilities), row = element('tr');
    [timeText(saved.created_at_utc), fullTimeText(saved.source_cycles.hrrr).replace(/, \d{4},/, ','), fullTimeText(saved.source_cycles.gefs).replace(/, \d{4},/, ','), saved.labels[index] || saved.tickers[index], percent(saved.probabilities[index])].forEach(value => row.append(element('td', '', value)));
    rows.append(row);
  });
  if (!history.length) {
    const row = element('tr'), cell = element('td', 'empty-table', 'No monitoring estimate saved yet. New runs are checked every 15 minutes.');
    cell.colSpan = 5; row.append(cell); rows.append(row);
  }
}

function render() {
  renderStatus();
  renderAutomaticPractice();
  const renderKey = JSON.stringify([state.market, state.tomorrow_market, state.month, state.monitoring_months, state.forecast, state.monitoring_forecast, state.weather, state.trades, state.test, state.reference_sha]);
  if (renderKey === lastRenderKey) {if ($('trade-dialog').open) updateTradeForm(); return;}
  lastRenderKey = renderKey;
  renderOverview(); renderMonitoring(); renderWeatherContext(); renderBrackets(); renderTomorrow(); renderMonth(); drawTemperature(); renderHourlyForecasts(); renderComparison(); renderAccounts(); renderTrades(); renderTest();
  if ($('trade-dialog').open) updateTradeForm();
}
async function parseResponse(response) {
  let result;
  try {result = await response.json();} catch (_) {throw new Error('The local dashboard returned an unreadable response.');}
  if (!response.ok || result.error) throw new Error(result.error || result.message || 'The request could not be completed.');
  return result;
}
async function pollState() {
  if (requestInFlight) {pollRequested = true; return;}
  requestInFlight = true;
  try {
    state = await parseResponse(await fetch('/api/state', {cache: 'no-store', credentials: 'same-origin'}));
    screenUpdatedAt = new Date().toISOString();
    if (!state.job || !state.job.busy) followManualJob = false;
    render();
  } catch (error) {
    $('connection-status').className = 'connection-line error';
    write('connection-text', 'Local dashboard connection unavailable. Saved values may be stale.');
    write('error-banner', error.message); show('error-banner', true);
  } finally {
    requestInFlight = false;
    if (pollRequested && !document.hidden) {pollRequested = false; clearTimeout(pollTimer); pollTimer = setTimeout(pollState, 0);}
    else {pollRequested = false; scheduleNextPoll();}
  }
}
async function runAction(action) {
  if (!state || localBusy || (state.job && state.job.busy)) return;
  actionError = '';
  localBusy = true;
  renderStatus();
  write('job-banner', action === 'forecasts' ? 'Checking for new HRRR and GEFS weather forecasts...' : action === 'preview' ? 'Loading a weather preview. This can take a minute; the preview will be excluded from the test.' : action === 'comparison' ? 'Saving the other weather-model forecasts. Scores will use later observations only.' : action === 'run' ? 'Collecting the frozen test forecast within its registered cutoff...' : action === 'results' ? 'Checking official results for saved test forecasts...' : 'Refreshing public market quotes and LAX observations...');
  show('job-banner', true);
  try {
    await parseResponse(await fetch('/api/action', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, token: state.session_token})}));
    followManualJob = true;
    await pollState();
  } catch (error) {actionError = error.message;}
  finally {localBusy = false; if (state) renderStatus();}
}
function notify(message) {
  write('toast', message); show('toast', true);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => show('toast', false), 6500);
}
function openTradeDialog() {
  if (!state) return;
  const selected = $('trade-ticker').value;
  $('trade-ticker').replaceChildren(element('option', '', 'Choose a range'));
  $('trade-ticker').firstChild.value = '';
  marketQuotes().forEach(quote => {
    const option = element('option', '', quoteLabel(quote)); option.value = quoteTicker(quote); $('trade-ticker').append(option);
  });
  if (marketQuotes().some(quote => quoteTicker(quote) === selected)) $('trade-ticker').value = selected;
  show('trade-form-error', false);
  updateTradeForm();
  if (!$('trade-dialog').open) $('trade-dialog').showModal();
}
function updateTradeForm() {
  if (!state) return;
  const manual = $('trade-mode').value === 'manual';
  show('manual-fields', manual); show('practice-form-note', !manual); show('practice-entry-preview', !manual);
  ['trade-quantity', 'trade-entry-price', 'trade-entry-fee'].forEach(id => {$(id).required = manual; $(id).disabled = !manual;});
  $('trade-executed-time').disabled = !manual;
  $('trade-executed-time').min = String(state.market && state.market.date || state.date) + 'T00:00';
  write('trade-form-disclaimer', manual ? 'Results are computed from the details you enter and the official contract payout, not verified against your account.' : 'Hypothetical entry held to settlement. Practice fees are estimates; the current quote does not demonstrate a fill.');
  write('save-trade-button', manual ? 'Record existing real trade' : 'Record practice entry');
  const practice = state.trades && (state.trades.practice || state.trades.summary) || {};
  write('practice-form-note', 'Practice starts at ' + money(practice.initial_balance) + '. This entry can use up to ' + money(practice.next_budget) + ' (10% of available cash), including estimated fees.');
  write('practice-entry-budget', 'Up to ' + money(practice.next_budget));
  const quote = marketQuotes().find(q => quoteTicker(q) === $('trade-ticker').value);
  const side = $('trade-side').value;
  const estimate = quote && quote.practice_estimates && quote.practice_estimates[side];
  const price = quote ? side === 'yes' ? number(quote.yes_ask) : noAsk(quote) : null;
  write('practice-entry-price', cents(estimate && estimate.price !== undefined ? estimate.price : price));
  const available = Boolean(estimate && estimate.available && quoteFresh(state.market));
  $('save-trade-button').disabled = !manual && !available;
  const reason = !quote ? 'Choose a temperature range.' : !quoteFresh(state.market) ? 'Refresh the market first. Practice entries need quotes less than two minutes old.' : !estimate ? 'Practice sizing is unavailable for this quote.' : !estimate.available ? estimate.reason || 'This entry needs an ask with enough size and available cash.' : estimate.quantity + ' hypothetical contracts \u00b7 total ' + money(estimate.cost) + ', including ' + money(estimate.fee) + ' estimated fee.';
  if (!manual) {write('practice-entry-budget', reason);}
}
async function saveTrade(event) {
  event.preventDefault();
  if (!state || !$('trade-form').reportValidity()) return;
  const kind = $('trade-mode').value;
  const payload = {token: state.session_token, kind, ticker: $('trade-ticker').value, side: $('trade-side').value};
  if (kind === 'manual') {
    payload.quantity = number($('trade-quantity').value);
    payload.entry_price = number($('trade-entry-price').value) / 100;
    payload.entry_fee = number($('trade-entry-fee').value);
    if ($('trade-executed-time').value) {
      try {payload.executed_at_utc = pacificLocalToUtc($('trade-executed-time').value);}
      catch (error) {write('trade-form-error', error.message); show('trade-form-error', true); return;}
    }
  }
  $('save-trade-button').disabled = true;
  show('trade-form-error', false);
  try {
    await parseResponse(await fetch('/api/trade', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)}));
    $('trade-dialog').close();
    await pollState();
    notify(kind === 'practice' ? 'Practice entry recorded. No order was sent.' : 'Existing real trade recorded in the manual journal.');
  } catch (error) {write('trade-form-error', error.message); show('trade-form-error', true);}
  finally {updateTradeForm();}
}

function pacificLocalToUtc(value) {
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) throw new Error('Enter a valid Pacific date and time.');
  const wanted = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5]), Number(match[6] || 0));
  const formatter = new Intl.DateTimeFormat('en-US', {timeZone: PT, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
  let candidate = wanted;
  let parts;
  for (let i = 0; i < 3; i++) {
    parts = Object.fromEntries(formatter.formatToParts(new Date(candidate)).map(part => [part.type, part.value]));
    const displayed = Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second));
    if (displayed === wanted) return new Date(candidate).toISOString();
    candidate += wanted - displayed;
  }
  throw new Error('This Pacific time does not exist because of a daylight-saving change. Check the entry time.');
}

document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => runAction(button.dataset.action)));
document.querySelectorAll('[data-graph]').forEach(button => {
  button.addEventListener('click', () => selectGraph(button.dataset.graph));
  button.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    selectGraph(event.key === 'Home' ? 'v10' : event.key === 'End' ? 'comparison' : selectedGraph === 'v10' ? 'comparison' : 'v10', true);
  });
});
$('record-trade-button').addEventListener('click', openTradeDialog);
$('empty-record-button').addEventListener('click', openTradeDialog);
$('close-dialog-button').addEventListener('click', () => $('trade-dialog').close());
$('trade-dialog').addEventListener('click', event => {if (event.target === $('trade-dialog')) {const rect = $('trade-dialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('trade-dialog').close();}});
['trade-mode', 'trade-ticker', 'trade-side'].forEach(id => $(id).addEventListener('change', () => {show('trade-form-error', false); updateTradeForm();}));
$('trade-form').addEventListener('submit', saveTrade);
$('tracking-toggle').addEventListener('click', async () => {
  if (!state || !state.tracking) return;
  $('tracking-toggle').disabled = true;
  try {
    await parseResponse(await fetch('/api/tracking', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: state.session_token, enabled: !state.tracking.enabled})}));
    await pollState();
  } catch (error) {notify(error.message);}
  finally {$('tracking-toggle').disabled = false;}
});
$('monitoring-month').addEventListener('change', () => {selectedMonitoringMonth = $('monitoring-month').value; renderMonth();});
pollState();
document.addEventListener('visibilitychange', () => {if (!document.hidden) pollState(); else clearTimeout(pollTimer);});
