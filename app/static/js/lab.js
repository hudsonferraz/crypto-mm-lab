// Simulation lab UI (specs/009 US1, US2, US3; FR-001, FR-006, FR-007, FR-014, FR-022).
// Each browser has its own random visitor id; the server only shows its own runs.
"use strict";

const LAB = {
  options: null,
  source: "bundled",
  currentRunId: null,
  pollTimer: null,
  listTimer: null,
  runs: [],
  details: new Map(), // run_id -> finished run (results never change)
  selected: new Set(),
  started: false,
};
const MAX_COMPARE = 6;
const SERIES_SLOTS = 8;

function visitorId() {
  const key = "mmlab-visitor";
  let id = null;
  try {
    id = localStorage.getItem(key);
  } catch (_) {}
  if (!id || !/^[0-9a-f]{32}$/.test(id)) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    try {
      localStorage.setItem(key, id);
    } catch (_) {}
  }
  return id;
}

// A run keeps the color it got first, wherever it appears (dataviz: color follows the entity).
function runColor(runId) {
  const key = "mmlab-run-colors";
  let map = {};
  try {
    map = JSON.parse(localStorage.getItem(key) || "{}");
  } catch (_) {}
  if (map[runId] === undefined) {
    map.__next = ((map.__next ?? -1) + 1) % SERIES_SLOTS;
    map[runId] = map.__next;
    try {
      localStorage.setItem(key, JSON.stringify(map));
    } catch (_) {}
  }
  return `var(--series-${map[runId] + 1})`;
}

async function labFetch(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Visitor-Id": visitorId() };
  const response = await fetch(path, { ...options, headers });
  let body = null;
  try {
    body = await response.json();
  } catch (_) {}
  if (!response.ok) {
    const detail = body?.detail;
    const error = new Error(
      typeof detail === "string" ? detail : detail?.message || `HTTP ${response.status}`,
    );
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return body;
}

const $ = (id) => document.getElementById(id);
const labMoney = (value, ccy = "", digits = 2) => {
  if (value === null || value === undefined) return "-";
  const n = Number(value);
  const text = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${n < 0 ? "−" : ""}${text}${ccy ? ` ${ccy}` : ""}`;
};
const labSigned = (value, ccy = "", digits = 2) => {
  if (value === null || value === undefined) return "-";
  const n = Number(value);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${labMoney(Math.abs(n), ccy, digits)}`;
};
const signClass = (value) => (value > 0 ? "positive" : value < 0 ? "negative" : "");
const seconds = (t) => `${Math.round(t)} s`;
const quoteCcy = (symbol) => (symbol || "").split("/")[1] || "";
const labelFor = (list, id) => list.find((item) => item.id === id)?.label || id;

function showMessage(text, kind = "error") {
  const box = $("lab-message");
  box.className = `lab-message show ${kind}`;
  box.innerHTML = text;
}

function clearMessage() {
  $("lab-message").className = "lab-message";
  $("lab-message").innerHTML = "";
}

// ------------------------------------------------------------------ form

function renderForm() {
  const o = LAB.options;
  $("lab-bundle").innerHTML = o.bundles
    .map((b) => `<option value="${esc(b.id)}">${esc(b.label)}</option>`)
    .join("");
  $("lab-exchange").innerHTML = o.exchanges
    .map((e) => `<option value="${esc(e.id)}">${esc(e.label)}</option>`)
    .join("");
  $("lab-strategy").innerHTML = o.strategies
    .map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`)
    .join("");
  $("lab-fill").innerHTML = o.fill_models
    .map((m) => `<option value="${esc(m.id)}"${m.id === "trade_fill" ? " selected" : ""}>${esc(m.label)}</option>`)
    .join("");
  const [dLo, dHi] = o.limits.duration_sec;
  const duration = $("lab-duration");
  duration.min = dLo;
  duration.max = dHi;
  const [sLo, sHi] = o.limits.spread_bps;
  $("lab-spread").min = sLo;
  $("lab-spread").max = sHi;
  $("lab-limits").textContent =
    `Up to ${o.limits.concurrent_recordings} live recordings run at once and ${o.limits.queue_size} wait in line. ` +
    `Each browser: one run at a time, ${o.limits.runs_per_hour} live recordings and ` +
    `${o.limits.bundled_runs_per_hour} bundled runs per hour. Runs are kept ${o.limits.keep_hours} hours in memory.`;
  $("lab-decisions").innerHTML = o.decisions
    .map((d) => `<div class="card"><h3>${esc(d.title)}</h3><p>${esc(d.text)}</p></div>`)
    .join("");
  renderPairs();
  renderBundleInfo();
  renderFillHelp();
  setSource(LAB.source);
}

function renderPairs() {
  const exchange = LAB.options.exchanges.find((e) => e.id === $("lab-exchange").value);
  const pairs = exchange ? exchange.pairs : [];
  $("lab-pair").innerHTML = pairs
    .map((p) => `<option value="${esc(p.symbol)}">${esc(p.symbol)}${p.verified ? "" : " (not verified)"}</option>`)
    .join("");
  $("lab-pair-hint").textContent = pairs.length
    ? `${pairs.length} pairs listed by ${exchange.label}. A snapshot every ${exchange.interval_sec} s.`
    : "No pairs available on this exchange right now.";
  renderPairRules();
}

function renderPairRules() {
  const exchange = LAB.options.exchanges.find((e) => e.id === $("lab-exchange").value);
  const pair = exchange?.pairs.find((p) => p.symbol === $("lab-pair").value);
  $("lab-pair-rules").textContent = pair
    ? pair.verified
      ? `Market rules: price step ${pair.price_step}, size step ${pair.amount_step}` +
        `${pair.min_amount ? `, minimum size ${pair.min_amount}` : ""}.`
      : "The market list could not be loaded, so this pair is not verified."
    : "";
}

function renderBundleInfo() {
  const bundle = LAB.options.bundles.find((b) => b.id === $("lab-bundle").value);
  $("lab-bundle-hint").textContent = bundle
    ? `${bundle.snapshot_count} order book snapshots and ${bundle.trade_count.toLocaleString()} public trades, recorded ${new Date(bundle.recorded_at).toLocaleString()}.`
    : "";
}

function renderFillHelp() {
  const model = LAB.options.fill_models.find((m) => m.id === $("lab-fill").value);
  $("lab-fill-hint").textContent = model ? model.help : "";
}

function setSource(source) {
  LAB.source = source;
  document.querySelectorAll("#lab-source button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.source === source));
  });
  $("lab-bundled-fields").hidden = source !== "bundled";
  $("lab-live-fields").hidden = source !== "live";
  $("lab-run").textContent = source === "live" ? "Record and simulate" : "Run simulation";
}

function formRequest() {
  const base = {
    strategy: $("lab-strategy").value,
    spread_bps: Number($("lab-spread").value),
    fill_model: $("lab-fill").value,
  };
  if (LAB.source === "bundled") return { ...base, source: "bundled", bundle_id: $("lab-bundle").value };
  return {
    ...base,
    source: "live",
    exchange: $("lab-exchange").value,
    symbol: $("lab-pair").value,
    duration_sec: Number($("lab-duration").value),
  };
}

async function submitRun(event) {
  event.preventDefault();
  clearMessage();
  const button = $("lab-run");
  button.disabled = true;
  try {
    const run = await labFetch("/lab/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formRequest()),
    });
    openRun(run.run_id, run);
    refreshRunList();
  } catch (error) {
    const d = error.detail || {};
    if (d.limit === "active_run") {
      showMessage(`${esc(d.message)}. <button type="button" class="link" data-open="${esc(d.run_id)}">Open it</button>`, "info");
    } else if (d.retry_after_sec) {
      const minutes = Math.ceil(d.retry_after_sec / 60);
      showMessage(`${esc(d.message)}. Try again in about ${minutes} minute${minutes === 1 ? "" : "s"}.`);
    } else {
      showMessage(esc(error.message));
    }
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ one run

function openRun(runId, run = null) {
  LAB.currentRunId = runId;
  clearTimeout(LAB.pollTimer);
  if (run) renderRun(run);
  pollRun();
  renderRunList();
}

async function pollRun() {
  const runId = LAB.currentRunId;
  if (!runId) return;
  try {
    const run = LAB.details.get(runId) || (await labFetch(`/lab/runs/${runId}`));
    if (runId !== LAB.currentRunId) return;
    if (run.stage === "done" || run.stage === "failed") LAB.details.set(runId, run);
    renderRun(run);
    if (run.stage !== "done" && run.stage !== "failed") {
      LAB.pollTimer = setTimeout(pollRun, 1000);
    } else {
      refreshRunList();
    }
  } catch (error) {
    if (error.status === 404) {
      $("lab-result").innerHTML = `<div class="empty-state"><strong>Run not found</strong>It may have expired: runs are kept a limited time in memory.</div>`;
      LAB.currentRunId = null;
    } else {
      LAB.pollTimer = setTimeout(pollRun, 3000);
    }
  }
}

// Short name for legends: the label alone is the same for every run on one recording.
function runName(run) {
  const symbol = run.result?.recording.symbol || run.request.symbol || "";
  const fill = labelFor(LAB.options.fill_models, run.request.fill_model).split(" (")[0];
  return `${symbol} · ${run.request.spread_bps} bps · ${fill}`;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function describeRequest(request) {
  const strategy = labelFor(LAB.options.strategies, request.strategy);
  const fill = labelFor(LAB.options.fill_models, request.fill_model);
  return `${strategy} · ${request.spread_bps} bps spread · ${fill}`;
}

function renderStages(run) {
  const stages = run.request.source === "live"
    ? ["waiting", "recording", "simulating", "done"]
    : ["simulating", "done"];
  const index = stages.indexOf(run.stage === "failed" ? "done" : run.stage);
  const names = { waiting: "Waiting", recording: "Recording", simulating: "Simulating", done: run.stage === "failed" ? "Failed" : "Done" };
  return `<ol class="stages" aria-label="Run stages">${stages
    .map((s, i) => `<li class="${i === index ? "current" : i < index ? "past" : ""}"${i === index ? ' aria-current="step"' : ""}>${names[s]}</li>`)
    .join("")}</ol>`;
}

function renderRun(run) {
  const target = $("lab-result");
  const head = `<div class="result-head"><div><h2 style="margin:0">${esc(run.label)}</h2>
    <div class="muted">${esc(describeRequest(run.request))}</div></div>
    <span class="badge ${run.stage === "done" ? "badge-ok" : run.stage === "failed" ? "badge-reverted" : "badge-live"}">${esc(run.stage)}</span></div>
    ${renderStages(run)}`;
  if (run.stage === "waiting") {
    target.innerHTML = `${head}<p>Waiting in line${run.queue_position ? `: position ${run.queue_position}` : ""}. Recordings start as soon as a slot frees up.</p>`;
    return;
  }
  if (run.stage === "recording") {
    const total = run.request.duration_sec || 1;
    const left = run.seconds_left ?? total;
    const done = Math.min(100, Math.max(0, ((total - left) / total) * 100));
    target.innerHTML = `${head}<p>Recording the live ${esc(run.request.symbol)} book and public trades: ${Math.ceil(left)} s left.</p>
      <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${done.toFixed(0)}"><span style="width:${done}%"></span></div>`;
    return;
  }
  if (run.stage === "simulating") {
    target.innerHTML = `${head}<p>Replaying the recording through the strategy…</p>`;
    return;
  }
  if (run.stage === "failed") {
    target.innerHTML = `${head}<div class="error-banner" style="display:block">${esc(run.error || "The run failed.")}</div>`;
    return;
  }
  renderResult(target, head, run);
}

function renderResult(target, head, run) {
  const r = run.result;
  const ccy = quoteCcy(r.recording.symbol);
  const move = ((r.end_mid - r.start_mid) / r.start_mid) * 10_000;
  target.innerHTML = `${head}
    <div class="grid">
      <div class="card"><div class="label">Strategy against holding</div>
        <div class="value ${signClass(r.edge_vs_hold)}">${labSigned(r.edge_vs_hold, ccy)}</div>
        <span class="sub">final ${labMoney(r.final_value, ccy)} · holding ${labMoney(r.hold_value, ccy)}</span></div>
      <div class="card"><div class="label">Fills</div><div class="value">${r.fill_count}</div>
        <span class="sub">${plural(r.buy_count, "buy")} · ${plural(r.sell_count, "sell")} · quote size ${r.quote_size}</span></div>
      <div class="card"><div class="label">Fees paid</div><div class="value">${labMoney(r.fees, ccy)}</div>
        <span class="sub">maker fee 2 bps of each fill</span></div>
      <div class="card"><div class="label">Price move</div><div class="value ${signClass(move)}">${move >= 0 ? "+" : "−"}${Math.abs(move).toFixed(1)} bps</div>
        <span class="sub">${seconds(r.duration_sec)} · ${r.snapshot_count} snapshots · ${r.trade_count.toLocaleString()} trades</span></div>
    </div>
    <ul class="summary-list">${r.summary.map((line) => `<li>${esc(line)}</li>`).join("")}</ul>
    <h3 style="margin-top:var(--space-5)">Value change against the start (${esc(ccy)})</h3>
    <div class="chart-legend"><span><span class="run-swatch" style="background:var(--series-1)"></span>Strategy</span>
      <span><span class="run-swatch" style="background:var(--ink-3)"></span>Holding the start (dashed)</span></div>
    <div class="chart-wrap"><svg class="lab-chart" id="lab-pnl-chart" role="img" aria-label="Strategy value change and holding value change over the run"></svg></div>
    <h3 style="margin-top:var(--space-4)">Coin held (${esc(r.recording.symbol.split("/")[0])})</h3>
    <div class="chart-wrap"><svg class="lab-chart small" id="lab-base-chart" role="img" aria-label="Base inventory over the run"></svg></div>
    <details class="muted"><summary>Show chart data</summary>
      <div class="table-wrap"><table><thead><tr><th class="numeric">Time</th><th class="numeric">Strategy</th><th class="numeric">Holding</th><th class="numeric">Coin held</th></tr></thead>
      <tbody>${r.series.map((p) => `<tr><td class="numeric">${seconds(p.t)}</td><td class="numeric">${labSigned(p.value_pnl)}</td><td class="numeric">${labSigned(p.hold_pnl)}</td><td class="numeric">${Number(p.base).toPrecision(6)}</td></tr>`).join("")}</tbody></table></div>
    </details>
    <h3 style="margin-top:var(--space-5)">Fills</h3>
    <div class="table-wrap"><table><thead><tr><th class="numeric">Time</th><th>Side</th><th class="numeric">Price</th><th class="numeric">Size</th><th class="numeric">Fee</th></tr></thead>
      <tbody>${r.fills.length ? r.fills.slice(0, 100).map((f) => `<tr><td class="numeric">${seconds(f.t)}</td>
        <td class="${f.side === "buy" ? "side-bid" : "side-ask"}">${f.side}</td><td class="numeric">${f.price}</td>
        <td class="numeric">${f.size}</td><td class="numeric">${labMoney(f.fee, "", 4)}</td></tr>`).join("") : "<tr><td colspan='5'>No fills</td></tr>"}</tbody></table></div>
    ${r.fills.length > 100 ? `<p class="muted">Showing the first 100 of ${r.fill_count} fills.</p>` : ""}
    <p class="muted">Recording: ${esc(r.recording.label)} (${esc(r.recording.source)}). Price step ${r.recording.price_step ?? "none"}, size step ${r.recording.amount_step ?? "none"}.</p>`;
  drawRunCharts(r);
}

function drawRunCharts(r) {
  const pnl = $("lab-pnl-chart");
  if (!pnl || !pnl.parentElement.offsetParent) return;
  const ccy = quoteCcy(r.recording.symbol);
  drawLineChart(pnl, [
    { name: "Strategy", color: "var(--series-1)", points: r.series.map((p) => ({ x: p.t, y: p.value_pnl })) },
    { name: "Holding", color: "var(--ink-3)", dash: "5 4", points: r.series.map((p) => ({ x: p.t, y: p.hold_pnl })) },
  ], { xFormat: seconds, yFormat: (y) => labSigned(y, "", 2), tooltipTitle: (x) => `${seconds(x)} · ${ccy}`, zeroLine: true });
  drawLineChart($("lab-base-chart"), [
    { name: "Coin held", color: "var(--series-1)", points: r.series.map((p) => ({ x: p.t, y: p.base })) },
  ], { xFormat: seconds, yFormat: (y) => Number(y).toPrecision(4) });
}

// ------------------------------------------------------------------ run list and comparison

async function refreshRunList() {
  try {
    const payload = await labFetch("/lab/runs");
    LAB.runs = payload.runs;
    const ids = new Set(LAB.runs.map((run) => run.run_id));
    [...LAB.selected].forEach((id) => ids.has(id) || LAB.selected.delete(id));
    renderRunList();
    renderComparison();
  } catch (_) {}
}

function renderRunList() {
  const list = $("lab-runs");
  if (!LAB.runs.length) {
    list.innerHTML = `<p class="muted">No runs yet. Your runs appear here; nobody else can see them.</p>`;
    return;
  }
  list.innerHTML = LAB.runs.map((run) => {
    const h = run.headline;
    const line = run.stage === "done" && h
      ? `<span class="${signClass(h.edge_vs_hold)}">${labSigned(h.edge_vs_hold)}</span> against holding · ${plural(h.fill_count, "fill")}`
      : run.stage === "failed" ? `<span class="negative">failed</span>` : esc(run.stage);
    const selectable = run.stage === "done";
    const checked = LAB.selected.has(run.run_id);
    return `<div class="run-item${run.run_id === LAB.currentRunId ? " selected" : ""}">
      <input type="checkbox" aria-label="Compare ${esc(run.label)}" data-compare="${run.run_id}" ${checked ? "checked" : ""} ${selectable ? "" : "disabled"}>
      <div><div class="run-title"><span class="run-swatch" style="background:${runColor(run.run_id)}"></span>${esc(run.label)}</div>
        <div class="muted">${esc(describeRequest(run.request))}</div><div class="muted">${line}</div></div>
      <button type="button" class="link" data-open="${run.run_id}">Open</button></div>`;
  }).join("");
}

async function renderComparison() {
  const box = $("lab-compare");
  const ids = LAB.runs.filter((run) => LAB.selected.has(run.run_id)).map((run) => run.run_id);
  if (ids.length < 2) {
    box.innerHTML = `<p class="muted">Tick two to ${MAX_COMPARE} finished runs to compare them side by side.</p>`;
    return;
  }
  const runs = [];
  for (const id of ids) {
    if (!LAB.details.has(id)) {
      try {
        LAB.details.set(id, await labFetch(`/lab/runs/${id}`));
      } catch (_) {
        continue;
      }
    }
    runs.push(LAB.details.get(id));
  }
  const mixed = new Set(runs.map((run) => quoteCcy(run.result.recording.symbol))).size > 1;
  box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Run</th><th>Settings</th><th class="numeric">Against holding</th>
      <th class="numeric">Final value</th><th class="numeric">Fills</th><th class="numeric">Fees</th><th class="numeric">Duration</th></tr></thead><tbody>
      ${runs.map((run) => {
        const r = run.result;
        const ccy = quoteCcy(r.recording.symbol);
        return `<tr><td><span class="run-swatch" style="background:${runColor(run.run_id)}"></span>${esc(runName(run))}<div class="muted">${esc(run.label)}</div></td>
          <td class="muted">${esc(describeRequest(run.request))}</td>
          <td class="numeric ${signClass(r.edge_vs_hold)}">${labSigned(r.edge_vs_hold, ccy)}</td>
          <td class="numeric">${labMoney(r.final_value, ccy)}</td><td class="numeric">${r.fill_count}</td>
          <td class="numeric">${labMoney(r.fees, ccy)}</td><td class="numeric">${seconds(r.duration_sec)}</td></tr>`;
      }).join("")}</tbody></table></div>
    <h3 style="margin-top:var(--space-4)">Strategy against holding over time</h3>
    ${mixed ? '<p class="muted">These runs are in different quote currencies; their curves share one axis but not one unit.</p>' : ""}
    <div class="chart-legend">${runs.map((run) => `<span><span class="run-swatch" style="background:${runColor(run.run_id)}"></span>${esc(runName(run))}</span>`).join("")}</div>
    <div class="chart-wrap"><svg class="lab-chart" id="lab-compare-chart" role="img" aria-label="Strategy value minus holding value over time for the selected runs"></svg></div>`;
  const chart = $("lab-compare-chart");
  if (chart.parentElement.offsetParent) {
    drawLineChart(chart, runs.map((run) => ({
      name: runName(run),
      color: runColor(run.run_id),
      points: run.result.series.map((p) => ({ x: p.t, y: p.value_pnl - p.hold_pnl })),
    })), { xFormat: seconds, yFormat: (y) => labSigned(y, "", 2), zeroLine: true });
  }
}

// ------------------------------------------------------------------ wiring

async function startLab() {
  if (LAB.started) return;
  LAB.started = true;
  try {
    LAB.options = await fetch("/lab/options").then((r) => {
      if (!r.ok) throw new Error(r.status === 503 ? "The simulation lab is turned off on this server." : `HTTP ${r.status}`);
      return r.json();
    });
  } catch (error) {
    LAB.started = false;
    $("lab-form-card").innerHTML = `<div class="empty-state"><strong>Lab unavailable</strong>${esc(error.message)}</div>`;
    return;
  }
  renderForm();
  refreshRunList();
}

document.addEventListener("DOMContentLoaded", () => {
  $("lab-form").addEventListener("submit", submitRun);
  $("lab-source").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source]");
    if (button) setSource(button.dataset.source);
  });
  $("lab-exchange").addEventListener("change", renderPairs);
  $("lab-pair").addEventListener("change", renderPairRules);
  $("lab-bundle").addEventListener("change", renderBundleInfo);
  $("lab-fill").addEventListener("change", renderFillHelp);
  $("lab-duration").addEventListener("input", () => {
    $("lab-duration-out").textContent = `${$("lab-duration").value} s`;
  });
  document.getElementById("panel-lab").addEventListener("click", (event) => {
    const open = event.target.closest("[data-open]");
    if (open) {
      clearMessage();
      openRun(open.dataset.open);
    }
  });
  $("lab-runs").addEventListener("change", (event) => {
    const box = event.target.closest("[data-compare]");
    if (!box) return;
    if (box.checked && LAB.selected.size >= MAX_COMPARE) {
      box.checked = false;
      return;
    }
    if (box.checked) LAB.selected.add(box.dataset.compare);
    else LAB.selected.delete(box.dataset.compare);
    renderComparison();
  });
});

onSectionShown((name) => {
  clearInterval(LAB.listTimer);
  if (name !== "lab") return;
  startLab();
  LAB.listTimer = setInterval(refreshRunList, 5000);
  const run = LAB.currentRunId && LAB.details.get(LAB.currentRunId);
  if (run?.result) drawRunCharts(run.result);
  renderComparison();
});
onThemeChange(() => {
  const run = LAB.currentRunId && LAB.details.get(LAB.currentRunId);
  if (run?.result && sectionFromHash() === "lab") drawRunCharts(run.result);
});
