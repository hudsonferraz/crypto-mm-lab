// Existing dashboard sections (live MM, DEX, research, LP, arb, mempool, testnet).
// Moved out of dashboard.html for the redesign (specs/009 FR-018); behavior unchanged
// except theme-aware chart colors and section-driven redraws.
"use strict";

const fmt = (value, digits = 4) =>
  value === null || value === undefined ? "-" : Number(value).toFixed(digits);

const fmtTime = (iso) => {
  if (!iso) return "-";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString();
};

const shortId = (value) => {
  if (!value) return "-";
  return value.length > 10 ? `${value.slice(0, 8)}…` : value;
};

let operatorToken = null;
let latestSweep = null;
let configCache = null;

async function fetchJson(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function renderChips(config, status) {
  const chips = [
    `exchange: ${config.exchange}`,
    `MM: ${config.mm_symbol}`,
    `compare: ${config.cex_compare_symbol}`,
    `strategy: ${config.strategy}`,
    `fill: ${config.fill_mode}`,
    `spread: ${config.quote_spread_bps} bps`,
    `maker fee: ${config.maker_fee_bps} bps`,
    `DEX: ${config.dex_enabled ? "on" : "off"}`,
    `V3 LP: ${config.lp_enabled ? `on (${config.lp_range_width_bps} bps)` : "off"}`,
    `RPC: ${config.rpc_provider || "-"}`,
    `market data: ${status.market_data_mode || config.market_data_mode}`,
  ];
  if (status.market_data_fallback) chips.push("data: fixture replay");
  if (status.market_data_source === "fixture") chips.push("demo fallback on");
  document.getElementById("config-chips").innerHTML = chips
    .map((text) => `<span class="chip">${text}</span>`)
    .join("");
  const fallbackBanner = document.getElementById("fallback-banner");
  if (fallbackBanner) {
    fallbackBanner.style.display = status.market_data_fallback ? "block" : "none";
  }
}

function renderHeaderStatus(status, market, config) {
  const pill = document.getElementById("live-pill");
  if (!pill) return;
  const fallback = status.market_data_source === "fixture";
  const ok = status.running && !status.last_error && !fallback;
  const state = !status.running ? "stopped" : fallback ? "fixture replay" : status.last_error ? "error" : "live";
  pill.innerHTML = `<span class="dot ${ok ? "dot-good" : fallback || status.last_error ? "dot-warning" : "dot-critical"}"></span>` +
    `${esc(config.mm_symbol)} · ${state}${market.mid ? ` · ${fmt(market.mid, 2)}` : ""}`;
}

function renderOverview(status, market, pnl, config) {
  const set = (id, html) => {
    const node = document.getElementById(id);
    if (node) node.innerHTML = html;
  };
  set("ov-pair", `${esc(config.mm_symbol)} on ${esc(config.exchange)}`);
  set("ov-mid", fmt(market.mid, 2));
  set("ov-spread", `${fmt(market.spread_bps, 2)} bps`);
  const total = pnl.total_pnl;
  const cls = total > 0 ? "positive" : total < 0 ? "negative" : "";
  set("ov-pnl", total === null || total === undefined ? "-" : `<span class="${cls}">${total < 0 ? "−" : "+"}${fmt(Math.abs(total), 4)}</span>`);
  set("ov-tick", `${status.tick}<span class="sub">${status.running ? "loop running" : "loop stopped"} · ${status.market_data_source === "fixture" ? "replaying a bundled book" : "live books"}</span>`);
  set("ov-strategy", `${esc(config.strategy)}<span class="sub">${esc(config.fill_mode)} · ${config.quote_spread_bps} bps spread</span>`);
}

function renderQuotes(items) {
  const body = document.getElementById("quotes-body");
  if (!items || items.length === 0) {
    body.innerHTML = "<tr><td colspan='5'>No open quotes</td></tr>";
    return;
  }
  body.innerHTML = items.map((item) => `
    <tr>
      <td class="side-${item.side}">${item.side}</td>
      <td class="numeric">${fmt(item.price, 2)}</td>
      <td class="numeric">${fmt(item.size, 6)}</td>
      <td class="numeric">${item.crossed_streak ?? 0}</td>
      <td class="mono" title="${item.quote_id || ""}">${shortId(item.quote_id)}</td>
    </tr>
  `).join("");
}

function renderOpportunities(payload) {
  const body = document.getElementById("opportunities-body");
  const note = document.getElementById("dex-note");
  if (!payload.dex_enabled) {
    note.textContent = `DEX disabled (${payload.disabled_reason || "DEX_ENABLED=false"}). MM book is ${payload.mm_symbol}; compare symbol would be ${payload.cex_compare_symbol}.`;
    body.innerHTML = "<tr><td colspan='6'>DEX disabled — no opportunities</td></tr>";
    return;
  }
  note.textContent = `${payload.quote_asset_basis_note || ""} MM=${payload.mm_symbol}, compare=${payload.cex_compare_symbol}.`;
  const items = payload.latest_tick?.length ? payload.latest_tick : payload.recent;
  if (!items || items.length === 0) {
    body.innerHTML = "<tr><td colspan='6'>No opportunities yet</td></tr>";
    return;
  }
  body.innerHTML = items.map((item) => `
    <tr>
      <td>${item.venue || "uniswap_v2"}${item.fee_tier_bps ? ` (${item.fee_tier_bps} bps)` : ""}</td>
      <td>${item.direction}</td>
      <td class="numeric">${fmt(item.net_edge_bps, 2)}</td>
      <td class="numeric">${fmt(item.net_edge, 4)}</td>
      <td class="numeric">${fmt(item.trial_trade_size, 4)}</td>
      <td class="numeric">${item.optimal_trade_size ? `${fmt(item.optimal_trade_size, 3)} → ${fmt(item.optimal_net_edge, 2)}` : "-"}</td>
    </tr>
  `).join("");
}

function renderFills(items) {
  const body = document.getElementById("fills-body");
  if (!items || items.length === 0) {
    body.innerHTML = "<tr><td colspan='6'>No fills yet</td></tr>";
    return;
  }
  body.innerHTML = items.map((item) => `
    <tr>
      <td>${fmtTime(item.timestamp)}</td>
      <td class="side-${item.side}">${item.side}</td>
      <td class="numeric">${fmt(item.price, 2)}</td>
      <td class="numeric">${fmt(item.size, 6)}</td>
      <td class="numeric">${fmt(item.fee, 4)}</td>
      <td class="mono" title="${item.quote_id || ""}">${shortId(item.quote_id)}</td>
    </tr>
  `).join("");
}

function renderEquityCurve(points) {
  const canvas = document.getElementById("equity-curve");
  const empty = document.getElementById("equity-empty");
  const startLabel = document.getElementById("equity-start");
  const endLabel = document.getElementById("equity-end");
  if (!points || points.length < 2) {
    canvas.style.display = "none";
    empty.style.display = "block";
    startLabel.textContent = "-";
    endLabel.textContent = "-";
    return;
  }
  if (!canvas.parentElement.offsetParent) return; // section hidden: draw when shown
  canvas.style.display = "block";
  empty.style.display = "none";
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const height = 180;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const values = points.map((point) => point.total_pnl);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const range = maxValue - minValue || 1;
  const padding = 16;
  const xForIndex = (index) => padding + (index / (points.length - 1)) * (width - padding * 2);
  const yForValue = (value) => height - padding - ((value - minValue) / range) * (height - padding * 2);
  if (minValue < 0 && maxValue > 0) {
    const zeroY = yForValue(0);
    context.strokeStyle = chartColors().grid;
    context.beginPath();
    context.moveTo(padding, zeroY);
    context.lineTo(width - padding, zeroY);
    context.stroke();
  }
  context.strokeStyle = chartColors().series1;
  context.lineWidth = 2;
  context.beginPath();
  points.forEach((point, index) => {
    const x = xForIndex(index);
    const y = yForValue(point.total_pnl);
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
  startLabel.textContent = `Start: ${fmt(points[0].total_pnl, 4)}`;
  endLabel.textContent = `Latest: ${fmt(points[points.length - 1].total_pnl, 4)}`;
}

function renderCompare(rows) {
  const body = document.getElementById("compare-body");
  if (!rows?.length) {
    body.innerHTML = "<tr><td colspan='6'>No rows</td></tr>";
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${row.strategy}</td>
      <td class="numeric">${fmt(row.total_pnl, 4)}</td>
      <td class="numeric">${fmt(row.max_drawdown, 4)}</td>
      <td class="numeric">${fmt(row.sharpe_ratio, 3)}</td>
      <td class="numeric">${fmt(row.fill_rate, 3)}</td>
      <td class="numeric">${row.fill_count ?? "-"}</td>
    </tr>
  `).join("");
}

function renderSweep(rows) {
  const body = document.getElementById("sweep-body");
  if (!rows?.length) {
    body.innerHTML = "<tr><td colspan='6'>No rows</td></tr>";
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${row.strategy}</td>
      <td class="numeric">${fmt(row.quote_spread_bps, 1)}</td>
      <td>${row.fill_mode}</td>
      <td class="numeric">${fmt(row.total_pnl, 4)}</td>
      <td class="numeric">${fmt(row.sharpe_ratio, 3)}</td>
      <td class="numeric">${fmt(row.fill_rate, 3)}</td>
    </tr>
  `).join("");
}

async function refresh() {
  try {
    const [status, config, market, amm, position, pnl, pnlHistory, fills, opportunities, quotes] =
      await Promise.all([
        fetchJson("/status"),
        fetchJson("/config"),
        fetchJson("/market"),
        fetchJson("/amm"),
        fetchJson("/position"),
        fetchJson("/pnl"),
        fetchJson("/pnl/history?limit=200"),
        fetchJson("/fills?limit=20"),
        fetchJson("/opportunities?limit=5"),
        fetchJson("/quotes"),
      ]);
    configCache = config;
    renderChips(config, status);
    renderHeaderStatus(status, market, config);
    renderOverview(status, market, pnl, config);

    const errorBanner = document.getElementById("error-banner");
    if (status.last_error) {
      errorBanner.style.display = "block";
      errorBanner.textContent = `last_error: ${status.last_error}`;
    } else {
      errorBanner.style.display = "none";
    }

    const stale = market.is_stale ? " (stale)" : "";
    document.getElementById("status-line").textContent =
      `Tick ${status.tick} | Running: ${status.running} | Kill: ${status.kill_switch_active ? "ON" : "OFF"}${stale}`;

    const audit = document.getElementById("audit-link");
    if (status.last_tick_id) {
      audit.href = `/audit/ticks/${status.last_tick_id}`;
      audit.textContent = `Tick audit ${status.last_tick_id.slice(0, 8)}…`;
    } else {
      audit.href = "#";
      audit.textContent = "Tick audit (waiting)";
    }

    document.getElementById("best-bid").textContent = fmt(market.best_bid, 2);
    document.getElementById("best-ask").textContent = fmt(market.best_ask, 2);
    document.getElementById("mid").textContent = fmt(market.mid, 2);
    document.getElementById("spread").textContent = fmt(market.spread_bps, 2);
    document.getElementById("cex-compare").textContent = fmt(amm.cex_compare_mid, 2);
    document.getElementById("amm-price").textContent = fmt(amm.spot_price, 2);
    document.getElementById("dex-spread").textContent = fmt(amm.spread_bps, 2);
    document.getElementById("base").textContent = fmt(position.base_amount, 6);
    document.getElementById("quote").textContent = fmt(position.quote_amount, 2);
    document.getElementById("total-pnl").textContent = fmt(pnl.total_pnl, 4);
    document.getElementById("pnl-breakdown").textContent =
      `${fmt(pnl.realized_pnl, 4)} / ${fmt(pnl.unrealized_pnl, 4)} / −${fmt(pnl.total_fees, 4)}`;

    renderEquityCurve(pnlHistory.points);
    renderFills(fills.fills);
    renderQuotes(quotes.quotes);
    renderOpportunities(opportunities);

    const btn = document.getElementById("kill-switch-btn");
    btn.textContent = status.kill_switch_active ? "Disable kill switch" : "Enable kill switch";
    btn.dataset.active = status.kill_switch_active ? "true" : "false";
    btn.dataset.auth = status.operator_auth_required ? "true" : "false";
  } catch (error) {
    document.getElementById("status-line").textContent = `Error: ${error.message}`;
  }
}

const usd = (value, digits = 2) =>
  value === null || value === undefined ? "-" : `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const signedUsd = (value) =>
  value === null || value === undefined ? "-" : `${Number(value) < 0 ? "−" : "+"}${usd(Math.abs(value))}`;
const compactInt = (raw) => {
  if (!raw) return "-";
  const n = Number(raw);
  return n >= 1e15 ? `${(n / 1e18).toFixed(3)}e18` : n.toLocaleString("en-US");
};
let lpPoolCache = null;
let lpPositionCache = null;

function renderLiquidityChart(pool, position) {
  const canvas = document.getElementById("liquidity-chart");
  const empty = document.getElementById("liquidity-empty");
  const buckets = pool?.window?.distribution || [];
  if (!buckets.length) {
    canvas.style.display = "none";
    empty.style.display = "block";
    return;
  }
  if (!canvas.parentElement.offsetParent) return; // section hidden: draw when shown
  canvas.style.display = "block";
  empty.style.display = "none";
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const height = 200;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 8, right: 8, top: 12, bottom: 22 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const values = buckets.map((b) => Number(b.liquidity));
  const maxValue = Math.max(...values) || 1;
  const lo = buckets[0].price_lower;
  const hi = buckets[buckets.length - 1].price_upper;
  const xFor = (price) => pad.left + ((price - lo) / (hi - lo)) * plotW;
  const slot = plotW / buckets.length;
  const barW = Math.max(slot - 2, 1); // 2px surface gap between bars

  // LP range band behind the bars
  if (position?.price_lower && position?.price_upper) {
    const x1 = Math.max(xFor(position.price_lower), pad.left);
    const x2 = Math.min(xFor(position.price_upper), pad.left + plotW);
    if (x2 > x1) {
      ctx.fillStyle = chartColors().series2Wash;
      ctx.fillRect(x1, pad.top, x2 - x1, plotH);
      ctx.strokeStyle = chartColors().series2;
      ctx.lineWidth = 2;
      ctx.strokeRect(x1, pad.top, x2 - x1, plotH);
    }
  }
  // bars: thin, rounded data-end, anchored to the baseline
  ctx.fillStyle = chartColors().series1;
  buckets.forEach((bucket, i) => {
    const h = (values[i] / maxValue) * plotH;
    if (h <= 0) return;
    const x = pad.left + i * slot + 1;
    const y = pad.top + plotH - h;
    const r = Math.min(4, barW / 2, h);
    ctx.beginPath();
    ctx.moveTo(x, pad.top + plotH);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.lineTo(x + barW - r, y);
    ctx.quadraticCurveTo(x + barW, y, x + barW, y + r);
    ctx.lineTo(x + barW, pad.top + plotH);
    ctx.closePath();
    ctx.fill();
  });
  // baseline + pool price line
  ctx.strokeStyle = chartColors().axis;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top + plotH + 0.5);
  ctx.lineTo(pad.left + plotW, pad.top + plotH + 0.5);
  ctx.stroke();
  const px = xFor(pool.price);
  ctx.strokeStyle = chartColors().ink;
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(px, pad.top);
  ctx.lineTo(px, pad.top + plotH);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = chartColors().muted;
  ctx.font = "12px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText(fmt(lo, 0), pad.left, height - 6);
  ctx.textAlign = "right";
  ctx.fillText(fmt(hi, 0), pad.left + plotW, height - 6);
  ctx.textAlign = "center";
  ctx.fillStyle = chartColors().ink;
  ctx.fillText(`price ${fmt(pool.price, 2)}`, Math.min(Math.max(px, 60), width - 60), height - 6);

  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const index = Math.floor((event.clientX - rect.left - pad.left) / slot);
    const tooltip = document.getElementById("liquidity-tooltip");
    if (index < 0 || index >= buckets.length) {
      tooltip.style.display = "none";
      return;
    }
    const b = buckets[index];
    tooltip.style.display = "block";
    tooltip.style.left = `${Math.min(event.clientX - rect.left + 12, width - 190)}px`;
    tooltip.style.top = "8px";
    tooltip.innerHTML = `<strong>${fmt(b.price_lower, 2)} – ${fmt(b.price_upper, 2)}</strong><br>` +
      `liquidity ${compactInt(b.liquidity)}<br>ticks ${b.tick_lower}…${b.tick_upper}`;
  };
  canvas.onmouseleave = () => {
    document.getElementById("liquidity-tooltip").style.display = "none";
  };
  document.getElementById("liquidity-table").innerHTML = buckets
    .filter((b) => Number(b.liquidity) > 0)
    .map((b) => `<tr><td class="numeric">${fmt(b.price_lower, 2)}</td><td class="numeric">${fmt(b.price_upper, 2)}</td><td class="numeric mono">${compactInt(b.liquidity)}</td></tr>`)
    .join("");
}

function renderPool(pool) {
  const note = document.getElementById("v3-note");
  if (pool.disabled_reason) {
    note.textContent = `V3 disabled (${pool.disabled_reason}).`;
    return;
  }
  if (pool.price === undefined) {
    note.textContent = pool.status_note || "Waiting for first V3 snapshot";
    return;
  }
  const flags = [];
  if (pool.is_stale) flags.push("STALE (last good snapshot reused)");
  if (pool.is_incomplete) flags.push("incomplete read");
  if (pool.data_source === "fixture") flags.push("FIXTURE REPLAY of recorded mainnet data");
  note.textContent = `${pool.pool_address} · window block ${pool.window?.block_number ?? "-"}` +
    `${pool.window?.is_current ? " (current → quotes exact)" : " (older → quotes approximate)"}` +
    `${flags.length ? " · " + flags.join(" · ") : ""}${pool.status_note ? " · " + pool.status_note : ""}`;
  document.getElementById("v3-price").textContent = fmt(pool.price, 2);
  document.getElementById("v3-tick").textContent = `${pool.tick} · ${pool.fee_tier_bps} bps`;
  document.getElementById("v3-liquidity").textContent = compactInt(pool.liquidity);
  document.getElementById("v3-block").textContent = `${pool.block_number} · ${pool.data_source}`;
}

function renderLpPosition(lp) {
  const status = document.getElementById("lp-status");
  if (lp.disabled_reason) {
    status.textContent = `LP book disabled (${lp.disabled_reason})`;
    return;
  }
  const label = {
    in_range: "IN RANGE — earning fees",
    out_of_range: "OUT OF RANGE — not earning fees",
    frozen: "FROZEN — kill switch active",
    not_opened: "Not opened yet",
  }[lp.status] || lp.status;
  status.textContent = `${label}${lp.status_note ? " · " + lp.status_note : ""}`;
  const track = document.getElementById("range-track");
  const band = document.getElementById("range-band");
  const marker = document.getElementById("range-marker");
  if (lp.price_lower && lp.price_upper && lp.pool_price) {
    const span = lp.price_upper - lp.price_lower;
    const lo = lp.price_lower - span;
    const hi = lp.price_upper + span;
    const pct = (p) => `${Math.min(Math.max(((p - lo) / (hi - lo)) * 100, 0), 100)}%`;
    band.style.left = pct(lp.price_lower);
    band.style.width = `calc(${pct(lp.price_upper)} - ${pct(lp.price_lower)})`;
    marker.style.left = pct(lp.pool_price);
    track.style.display = "block";
    document.getElementById("range-lower").textContent = `lower ${fmt(lp.price_lower, 2)}`;
    document.getElementById("range-upper").textContent = `upper ${fmt(lp.price_upper, 2)}`;
  }
  document.getElementById("lp-fees").textContent = usd(lp.fees_quote, 4);
  document.getElementById("lp-il").textContent = signedUsd(lp.il_quote);
  document.getElementById("lp-hodl").textContent = signedUsd(lp.hodl_pnl_quote);
  document.getElementById("lp-costs").textContent =
    lp.swap_cost_quote === undefined ? "-" : `${usd(lp.swap_cost_quote)} + ${usd(lp.gas_cost_quote)}`;
  document.getElementById("lp-net").textContent =
    lp.net_pnl_quote === undefined ? "-" : `${signedUsd(lp.net_pnl_quote)} · ${fmt(lp.return_on_capital_pct, 3)}%`;
  document.getElementById("lp-rebalances").textContent =
    lp.rebalance_count === undefined ? "-" : `${lp.rebalance_count} · ${lp.time_in_range_pct === null ? "-" : fmt(lp.time_in_range_pct, 1) + "%"}`;
  document.getElementById("lp-accounting").textContent = lp.fee_accounting
    ? `Capital ${usd(lp.capital_quote, 0)} · fee accounting: ${lp.fee_accounting} · share of active liquidity ${fmt(lp.pool_share_pct, 3)}% (fees diluted accordingly) · reconciliation error ${fmt(lp.reconciliation_error_quote, 6)} USDC`
    : "";
}

function renderBooks(books) {
  const body = document.getElementById("books-body");
  if (!books?.cex) return;
  const row = (side) => `
    <tr>
      <td>${side.label}</td>
      <td class="numeric">${signedUsd(side.revenue_quote)}</td>
      <td class="numeric">${usd(side.costs_quote)}</td>
      <td class="numeric">${signedUsd(side.inventory_effect_quote)}</td>
      <td class="numeric">${signedUsd(side.net_pnl_quote)}</td>
      <td class="numeric">${fmt(side.return_pct, 4)}%</td>
    </tr>`;
  body.innerHTML = row(books.cex) + row(books.lp);
  const winner = { cex: "CEX book ahead", lp: "LP book ahead", tie: "Tie" }[books.winner];
  document.getElementById("books-notes").innerHTML =
    `<strong>${winner}</strong> on return on capital. ` + (books.mismatch_notes || []).join(" ");
}

function renderRebalances(items) {
  const body = document.getElementById("rebalances-body");
  if (!items?.length) {
    body.innerHTML = "<tr><td colspan='6'>No rebalances yet</td></tr>";
    return;
  }
  body.innerHTML = items.map((item) => `
    <tr>
      <td>${fmtTime(item.timestamp)}</td>
      <td>${item.reason}</td>
      <td class="mono">${item.new_tick_lower} → ${item.new_tick_upper}</td>
      <td class="numeric">${usd(item.swap_cost_quote, 4)}</td>
      <td class="numeric">${usd(item.gas_cost_quote, 4)}</td>
      <td class="numeric">${signedUsd(item.realized_il_quote)}</td>
    </tr>`).join("");
}

const DIRECTION_LABEL = { v3_to_v2: "buy on V3, sell on V2", v2_to_v3: "buy on V2, sell on V3" };
const FEES_BPS = 35;
let arbHistoryCache = [];

const outcomeBadge = (outcome, id = "") => {
  const kind = outcome?.startsWith("reverted") ? "reverted" : outcome;
  return `<span ${id ? `id="${id}" ` : ""}class="badge badge-${kind}">${outcome}</span>`;
};

function renderArbStatus(payload) {
  const status = payload.status;
  const backend = {
    "public-rpc": "free public RPC (state overrides)",
    "local-fork": "local anvil fork",
    "custom-rpc": "custom RPC",
  }[status.backend] || "-";
  const state = {
    ok: "Simulator OK",
    waiting: "Waiting",
    unavailable: "Simulator UNAVAILABLE",
    disabled: "Simulator off",
  }[status.state] || status.state;
  document.getElementById("arb-note").textContent = `${state} · backend: ${backend}` +
    `${status.reason ? " · " + status.reason : ""}` +
    `${status.skipped_total ? ` · ${status.skipped_total} tick(s) skipped` : ""}` +
    " · every result is a read-only simulation; nothing is ever signed or sent.";
}

function renderArbLatest(latest) {
  if (!latest) return;
  const sim = latest.simulation;
  document.getElementById("arb-gap").textContent = `${fmt(Math.abs(latest.gap_bps), 1)} bps / ${FEES_BPS} bps`;
  document.getElementById("arb-trade").textContent =
    `${DIRECTION_LABEL[latest.direction]} · ${usd(latest.notional_quote, 0)}` +
    (latest.size_reason === "reference" ? " (reference size)" : " (best size)");
  document.getElementById("arb-estimate").textContent =
    `${signedUsd(latest.estimate.gross_quote)} / ${signedUsd(latest.estimate.net_quote)}`;
  document.getElementById("arb-simulated").textContent =
    sim.net_quote === null ? "-" : `${signedUsd(sim.gross_quote)} / ${signedUsd(sim.net_quote)}`;
  document.getElementById("arb-gas").textContent = sim.gas_used === null ? "-" :
    `${Number(sim.gas_used).toLocaleString("en-US")} · ${usd(sim.gas_cost_quote, 4)}`;
  document.getElementById("arb-error").textContent = latest.estimate_error_quote === null ? "-" :
    `${signedUsd(latest.estimate_error_quote)} · ${fmt(latest.estimate_error_bps, 3)} bps` +
    (latest.estimate.exact ? " (exact estimate)" :
      latest.estimate_error_quote === 0 ? " (tick window from an older block, still matched)" :
      " (approximate: tick window from an older block)");
  document.getElementById("arb-outcome").outerHTML = outcomeBadge(latest.outcome, "arb-outcome");
  document.getElementById("arb-outcome-note").textContent = latest.outcome_note;
  document.getElementById("arb-meta").textContent =
    `block ${latest.block_number} · ${fmtTime(latest.timestamp)} · ${fmt(latest.latency_ms, 0)} ms`;
}

function renderAccuracy(acc) {
  const counts = Object.entries(acc.outcome_counts || {}).map(([k, v]) => `${v} ${k}`).join(", ");
  document.getElementById("acc-total").textContent = acc.total ? `${acc.total} · ${counts}` : "-";
  document.getElementById("acc-agree").textContent =
    acc.agreement_rate === null ? "-" : `${fmt(acc.agreement_rate * 100, 1)}%`;
  document.getElementById("acc-error").textContent = acc.median_abs_error_bps === null ? "-" :
    `${fmt(acc.median_abs_error_bps, 3)} / ${fmt(acc.p95_abs_error_bps, 3)} bps`;
  document.getElementById("acc-exact").textContent =
    acc.exact_match_rate === null ? "-" : `${fmt(acc.exact_match_rate * 100, 1)}%`;
}

function renderArbHistory(items) {
  if (!items.length) return;
  document.getElementById("arb-history").innerHTML = items.slice(0, 20).map((item) => `
    <tr>
      <td>${fmtTime(item.timestamp)}</td>
      <td class="numeric">${item.block_number}</td>
      <td>${DIRECTION_LABEL[item.direction]}</td>
      <td class="numeric">${usd(item.notional_quote, 0)}</td>
      <td class="numeric">${fmt(Math.abs(item.gap_bps), 1)} bps</td>
      <td class="numeric">${signedUsd(item.estimate.net_quote)}</td>
      <td class="numeric">${signedUsd(item.simulation.net_quote)}</td>
      <td class="numeric">${item.simulation.gas_used === null ? "-" : Number(item.simulation.gas_used).toLocaleString("en-US")}</td>
      <td>${outcomeBadge(item.outcome)}</td>
    </tr>`).join("");
  document.getElementById("gap-table").innerHTML = items.map((item) =>
    `<tr><td>${fmtTime(item.timestamp)}</td><td class="numeric">${item.block_number}</td>` +
    `<td class="numeric">${fmt(Math.abs(item.gap_bps), 1)} bps</td><td>${item.outcome}</td></tr>`).join("");
}

function renderGapChart(items) {
  const canvas = document.getElementById("gap-chart");
  const empty = document.getElementById("gap-empty");
  const points = [...items].reverse(); // oldest first
  if (!points.length) {
    canvas.style.display = "none";
    empty.style.display = "block";
    return;
  }
  if (!canvas.parentElement.offsetParent) return; // tab hidden: draw when shown
  canvas.style.display = "block";
  empty.style.display = "none";
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const height = 200;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 40, right: 12, top: 14, bottom: 22 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const gaps = points.map((p) => Math.abs(p.gap_bps));
  const top = Math.max(FEES_BPS * 1.3, Math.max(...gaps) * 1.1);
  const xFor = (i) => pad.left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const yFor = (v) => pad.top + plotH - (v / top) * plotH;
  // recessive grid and y labels (bps)
  ctx.strokeStyle = chartColors().axis;
  ctx.lineWidth = 1;
  ctx.fillStyle = chartColors().muted;
  ctx.font = "12px system-ui, sans-serif";
  ctx.textAlign = "right";
  [0, top / 2, top].forEach((v) => {
    const y = Math.round(yFor(v)) + 0.5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + plotW, y); ctx.stroke();
    ctx.fillText(fmt(v, 0), pad.left - 6, y + 4);
  });
  // fee threshold
  const feeY = yFor(FEES_BPS);
  ctx.strokeStyle = chartColors().ink;
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(pad.left, feeY); ctx.lineTo(pad.left + plotW, feeY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = chartColors().ink;
  ctx.textAlign = "left";
  ctx.fillText(`fees ${FEES_BPS} bps`, pad.left + 6, feeY - 6);
  // gap series: 2px line, 8px markers with a 2px surface ring
  ctx.strokeStyle = chartColors().series1;
  ctx.lineWidth = 2;
  ctx.beginPath();
  gaps.forEach((g, i) => (i ? ctx.lineTo(xFor(i), yFor(g)) : ctx.moveTo(xFor(i), yFor(g))));
  ctx.stroke();
  gaps.forEach((g, i) => {
    ctx.beginPath();
    ctx.arc(xFor(i), yFor(g), 4, 0, Math.PI * 2);
    ctx.fillStyle = chartColors().series1;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = chartColors().surface;
    ctx.stroke();
  });
  ctx.fillStyle = chartColors().muted;
  ctx.textAlign = "left";
  ctx.fillText(fmtTime(points[0].timestamp), pad.left, height - 6);
  ctx.textAlign = "right";
  ctx.fillText(fmtTime(points[points.length - 1].timestamp), pad.left + plotW, height - 6);

  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    let index = 0;
    points.forEach((_, i) => { if (Math.abs(xFor(i) - x) < Math.abs(xFor(index) - x)) index = i; });
    const p = points[index];
    const tooltip = document.getElementById("gap-tooltip");
    tooltip.style.display = "block";
    tooltip.style.left = `${Math.min(x + 12, width - 200)}px`;
    tooltip.style.top = "8px";
    tooltip.innerHTML = `<strong>block ${p.block_number}</strong> · ${fmtTime(p.timestamp)}<br>` +
      `gap ${fmt(Math.abs(p.gap_bps), 1)} bps (fees ${FEES_BPS})<br>` +
      `simulated net ${signedUsd(p.simulation.net_quote)}<br>${p.outcome}`;
  };
  canvas.onmouseleave = () => { document.getElementById("gap-tooltip").style.display = "none"; };
}


// --- Mempool watcher (specs/004-mempool-watcher) ------------------------------
let mpBlocksCache = [];
const pct = (value, digits = 1) =>
  value === null || value === undefined ? "-" : `${(value * 100).toFixed(digits)}%`;
const bps = (value, digits = 2) =>
  value === null || value === undefined ? "-" : `${Number(value).toFixed(digits)} bps`;
const esc = (text) => String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const shortHash = (hash) => (hash ? `${hash.slice(0, 10)}…${hash.slice(-4)}` : "-");
const txLink = (hash) => `<a class="mono" href="https://etherscan.io/tx/${esc(hash)}" target="_blank" rel="noreferrer">${shortHash(hash)}</a>`;

function legText(leg) {
  const units = leg.direction === "sell_weth" ? ["WETH", 1e18] : ["USDC", 1e6];
  const amount = leg.amount_specified_raw ? Number(leg.amount_specified_raw) / units[1] : null;
  const kind = leg.kind === "exact_out" ? "for exactly" : leg.kind === "exact_amounts" ? "set" : "";
  const side = leg.direction === "sell_weth" ? "sell WETH" : "buy WETH";
  const shown = amount === null ? "?" : amount.toLocaleString(undefined, { maximumFractionDigits: 4 });
  const flags = leg.flags && leg.flags.length ? ` <span class="muted">[${leg.flags.join(", ")}]</span>` : "";
  return `${leg.pool.toUpperCase()} ${side} ${kind} ${shown} ${leg.kind === "exact_out" ? (leg.direction === "sell_weth" ? "USDC" : "WETH") : units[0]}${flags}`;
}

function renderMempoolStatus(payload) {
  const status = payload.status;
  const modeBadge = document.getElementById("mp-mode");
  const stateBadge = document.getElementById("mp-state");
  if (!status.enabled) {
    modeBadge.textContent = "disabled";
    modeBadge.className = "badge";
    stateBadge.textContent = status.state;
    stateBadge.className = "badge";
    document.getElementById("mp-status").textContent = `Watcher off (${status.reason}).`;
    return;
  }
  const live = status.mode === "live";
  modeBadge.textContent = live ? "live" : "replay (not live)";
  modeBadge.className = `badge ${live ? "badge-live" : "badge-replay"}`;
  stateBadge.textContent = status.state;
  stateBadge.className = `badge ${status.state === "ok" ? "badge-ok" : "badge-warn"}`;
  const c = status.counters || {};
  const source = status.source ? ` · ${status.source}` : "";
  const reason = status.reason ? ` · ${status.reason}` : "";
  document.getElementById("mp-status").textContent =
    `${status.backend || "-"}${source} · block ${status.last_block ?? "-"} · ` +
    `${(c.seen || 0).toLocaleString()} pending seen · ${c.decoded || 0} decoded · ` +
    `${c.not_decoded || 0} not decoded · ${c.pending_watched || 0} waiting · ` +
    `${c.state_mismatches || 0} state mismatches${reason}`;
  document.getElementById("mp-mode-note").textContent = payload.explanations.D2;
  const order = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"];
  const titles = {
    D1: "We only see a minority of the swaps.",
    D2: "Live by default, block replay when live is impossible.",
    D3: "Predict against the latest block, then explain the miss.",
    D4: "Decode the common routes, count the rest.",
    D5: "Back-runs are a stated rule, not a guess.",
    D6: "Beside the trading loop, never inside it.",
    D7: "Read-only and a fixed scope.",
  };
  document.getElementById("mp-explanations").innerHTML = order
    .map((key) => `<p><strong>${titles[key]}</strong> ${esc(payload.explanations[key])}</p>`)
    .join("");
  const switches = payload.mode_changes || [];
  document.getElementById("mp-switches").innerHTML = switches.length
    ? switches.map((s) => `<tr><td>${fmtTime(s.at)}</td><td>${s.from_mode || "-"}</td><td>${s.to_mode}</td><td>${s.source}</td><td>${esc(s.reason)}</td></tr>`).join("")
    : "<tr><td colspan='5'>None yet</td></tr>";
}

function renderMempoolSummary(payload, status) {
  const ex = payload.explanations;
  const live = payload.live;
  const replay = payload.replay;
  const none = "none verified yet";
  const set = (id, value, note) => {
    document.getElementById(id).innerHTML = `${value}${note ? `<span class="sub">${esc(note)}</span>` : ""}`;
  };
  const errors = (summary, digits) => summary && summary.math_error_bps_p50 !== null
    ? `${bps(summary.math_error_bps_p50, digits)} / ${bps(summary.math_error_bps_p95, digits)}`
    : none;

  // Live group (D1, D4): visibility, decoding, inclusion, and the rare live predictions.
  const liveMode = status && status.status && status.status.mode === "live";
  if (live) {
    set("mp-visible", pct(live.visible_share), ex.visible_share);
    set("mp-coverage", pct(live.decode_coverage), ex.decode_coverage);
    const latency = live.latency_blocks_p50 !== null
      ? ` · ${fmt(live.latency_blocks_p50, 0)} blk · ${fmt(live.latency_seconds_p50, 1)} s` : "";
    set("mp-inclusion", live.inclusion_rate === null ? none : `${pct(live.inclusion_rate)}${latency}`, ex.inclusion_rate);
    set("mp-live-math", errors(live, 4), live.exact_rate === null ? ex.math_error_bps : `${pct(live.exact_rate)} exact`);
    const quiet = live.watched === 0
      ? " No public swap on our pools was decodable in this window: most of their flow is private (D1) and most public router flow now goes to Uniswap V4 pools (D4, out of scope). The replay group below checks every swap that did land."
      : "";
    document.getElementById("mp-window").textContent =
      `Last ${payload.window_minutes} min: ${live.watched} live swaps watched over ${live.blocks} blocks.${quiet}`;
  } else {
    ["mp-visible", "mp-coverage", "mp-inclusion", "mp-live-math"].forEach((id) => set(id, liveMode ? none : "n/a (not live)"));
    document.getElementById("mp-window").textContent = liveMode
      ? `No live records in the last ${payload.window_minutes} minutes yet.`
      : "The watcher is not live right now, so these numbers are not available (D2).";
  }

  // Replay group (D2, D3, D5): every swap that landed, checked with exact math.
  if (replay) {
    set("mp-r-count", `${replay.watched} · ${replay.blocks}`, "Swap transactions on our pools in recent blocks, not watched live.");
    set("mp-math", errors(replay, 4), replay.exact_rate === null ? ex.math_error_bps : `${pct(replay.exact_rate)} exact. ${ex.math_error_bps}`);
    set("mp-ordering", errors({
      math_error_bps_p50: replay.ordering_effect_bps_p50,
      math_error_bps_p95: replay.ordering_effect_bps_p95,
    }, 2), ex.ordering_effect_bps);
    set("mp-opps", `${replay.opportunities_flagged} · ${replay.opportunities_appeared} · ${replay.backruns}`, ex.capture_rate);
    document.getElementById("mp-replay-window").textContent =
      "Each swap is predicted from the state before its block, as if it had been seen pending, then checked against its own event. Kept apart from live numbers.";
  } else {
    ["mp-r-count", "mp-math", "mp-ordering", "mp-opps"].forEach((id) => set(id, "-"));
    document.getElementById("mp-replay-window").textContent =
      status && status.status && status.status.replay_alongside === false && liveMode
        ? "Replay alongside live is off (MEMPOOL_REPLAY_ALONGSIDE=false)."
        : `No replayed swaps in the last ${payload.window_minutes} minutes yet.`;
  }
}

function renderMempoolSwaps(pending, verified) {
  document.getElementById("mp-pending").innerHTML = pending.length
    ? pending.map((s) => `<tr>
        <td>${fmtTime(s.first_seen_at)}</td><td>${txLink(s.tx_hash)}</td><td>${s.route}</td>
        <td>${s.legs.map(legText).join("<br>")}</td>
        <td class="numeric">${bps(s.gap_before_bps)} → ${bps(s.gap_after_bps)}</td>
        <td>${s.opportunity ? `${s.opportunity} (${s.opportunity_direction})` : "-"}</td>
        <td class="muted">${esc(s.note)}</td></tr>`).join("")
    : "<tr><td colspan='7'>Nothing pending on our pools right now (see D1)</td></tr>";
  const done = verified.filter((s) => s.status !== "pending");
  document.getElementById("mp-verified").innerHTML = done.length
    ? done.map((s) => {
        const o = s.outcome || {};
        const legs = o.legs || [];
        const math = legs.length ? Math.max(...legs.map((l) => l.math_error_bps)) : null;
        const order = legs.length ? Math.max(...legs.map((l) => l.ordering_effect_bps)) : null;
        const waited = s.mode === "live" && o.blocks_waited !== null && o.blocks_waited !== undefined
          ? `${o.blocks_waited} blk${o.seconds_waited !== null ? ` · ${fmt(o.seconds_waited, 1)} s` : ""}` : "-";
        return `<tr>
          <td>${fmtTime(s.first_seen_at)}</td><td>${txLink(s.tx_hash)}</td>
          <td><span class="badge ${s.mode === "live" ? "badge-live" : "badge-replay"}">${s.mode}</span></td>
          <td>${s.status}</td><td class="numeric">${o.block_number ?? "-"} · ${o.tx_index ?? "-"}</td>
          <td class="numeric">${waited}</td>
          <td class="numeric">${math === null ? "-" : math === 0 ? "0 (exact)" : bps(math, 4)}</td>
          <td class="numeric">${order === null ? "-" : bps(order)}</td>
          <td class="muted">${esc(s.note)}</td></tr>`;
      }).join("")
    : "<tr><td colspan='9'>No verified swaps yet</td></tr>";
}

function renderVisibilityChart(items) {
  const canvas = document.getElementById("visibility-chart");
  const empty = document.getElementById("visibility-empty");
  const points = [...items].reverse();
  document.getElementById("visibility-table").innerHTML = items.slice(0, 50).map((b) => `<tr>
    <td class="numeric">${b.block_number}</td><td>${b.mode}</td><td class="numeric">${b.pool_swap_txs}</td>
    <td class="numeric">${b.mode === "live" ? b.seen_pending : "-"}</td>
    <td class="numeric">${bps(b.gap_end_bps)}</td><td class="numeric">${b.scan_net_end_quote === null ? "-" : `$${fmt(b.scan_net_end_quote, 2)}`}</td></tr>`).join("");
  if (!points.length) {
    canvas.style.display = "none";
    empty.style.display = "block";
    return;
  }
  if (!canvas.parentElement.offsetParent) return;
  canvas.style.display = "block";
  empty.style.display = "none";
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const height = 200;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { left: 32, right: 12, top: 12, bottom: 22 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const top = Math.max(2, ...points.map((p) => p.pool_swap_txs));
  const slot = plotW / points.length;
  const barW = Math.max(2, Math.min(18, slot - 2));
  const yFor = (v) => pad.top + plotH - (v / top) * plotH;
  ctx.strokeStyle = chartColors().axis;
  ctx.lineWidth = 1;
  ctx.fillStyle = chartColors().muted;
  ctx.font = "12px system-ui, sans-serif";
  ctx.textAlign = "right";
  [0, Math.round(top / 2), top].forEach((v) => {
    const y = Math.round(yFor(v)) + 0.5;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + plotW, y); ctx.stroke();
    ctx.fillText(String(v), pad.left - 6, y + 4);
  });
  const roundTop = (x, y, w, h) => {
    if (h <= 0) return;
    const r = Math.min(4, w / 2, h);
    ctx.beginPath();
    ctx.moveTo(x, y + h); ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r); ctx.lineTo(x + w, y + h);
    ctx.closePath(); ctx.fill();
  };
  points.forEach((p, i) => {
    const x = pad.left + i * slot + (slot - barW) / 2;
    const seen = p.mode === "live" ? p.seen_pending : 0;
    const unseen = p.pool_swap_txs - seen;
    const base = yFor(0);
    const seenTop = yFor(seen);
    if (seen > 0) {
      ctx.fillStyle = chartColors().series1;
      if (unseen > 0) ctx.fillRect(x, seenTop, barW, base - seenTop);
      else roundTop(x, seenTop, barW, base - seenTop);
    }
    if (unseen > 0) {
      const totalTop = yFor(p.pool_swap_txs);
      const gap = seen > 0 ? 2 : 0;
      ctx.fillStyle = chartColors().series2;
      roundTop(x, totalTop, barW, seenTop - totalTop - gap);
    }
  });
  ctx.fillStyle = chartColors().muted;
  ctx.textAlign = "left";
  ctx.fillText(`#${points[0].block_number}`, pad.left, height - 6);
  ctx.textAlign = "right";
  ctx.fillText(`#${points[points.length - 1].block_number}`, pad.left + plotW, height - 6);
  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const index = Math.min(points.length - 1, Math.max(0, Math.floor((event.clientX - rect.left - pad.left) / slot)));
    const p = points[index];
    const tooltip = document.getElementById("visibility-tooltip");
    const seen = p.mode === "live" ? p.seen_pending : "n/a (replay)";
    tooltip.innerHTML = `<div class="mono">block ${p.block_number}</div>` +
      `<div>Pool swaps: ${p.pool_swap_txs}</div><div>Seen pending: ${seen}</div>` +
      `<div>Gap at end: ${bps(p.gap_end_bps)}</div>`;
    tooltip.style.display = "block";
    const left = Math.min(rect.width - 180, Math.max(0, event.clientX - rect.left + 12));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = "8px";
  };
  canvas.onmouseleave = () => { document.getElementById("visibility-tooltip").style.display = "none"; };
}

async function refreshMempool() {
  try {
    const [status, summary, pending, verified, liveBlocks, replayBlocks] = await Promise.all([
      fetchJson("/mempool/status"),
      fetchJson("/mempool/summary?window_minutes=60"),
      fetchJson("/mempool/swaps?status=pending&limit=20"),
      fetchJson("/mempool/swaps?limit=40"),
      fetchJson("/mempool/blocks?limit=60&mode=live"),
      fetchJson("/mempool/blocks?limit=60&mode=replay"),
    ]);
    // One row per block: the live row while live, the replay row otherwise.
    const blocks = status.status.mode === "replay" ? replayBlocks : liveBlocks;
    renderMempoolStatus(status);
    renderMempoolSummary(summary, status);
    renderMempoolSwaps(pending.items, verified.items);
    mpBlocksCache = blocks.items;
    renderVisibilityChart(blocks.items);
  } catch (error) {
    document.getElementById("mp-status").textContent = `Mempool error: ${error.message}`;
  }
}

// --- Binance spot testnet (specs/005-binance-testnet-execution) ----------------
const MODEL_LABELS = {
  full_cross_fill: "full_cross_fill",
  partial_fill: "partial_fill",
  latency_prob_fill: "latency_prob_fill",
  calibrated_fill: "calibrated_fill",
};
const secs = (value) => (value === null || value === undefined ? "-" : `${Number(value).toFixed(1)} s`);
const qty = (value) => (value === null || value === undefined ? "-" : Number(value).toFixed(5));
const usd2 = (value) => (value === null || value === undefined ? "-" : `${Number(value) >= 0 ? "" : "−"}$${Math.abs(Number(value)).toFixed(2)}`);

function renderTestnetStatus(payload, sessions) {
  const status = payload.status;
  const source = payload.source;
  const sourceBadge = document.getElementById("tn-source");
  sourceBadge.textContent = source === "recorded" ? "recorded (not live)" : source === "live" ? "live" : "no data";
  sourceBadge.className = `badge ${source === "live" ? "badge-live" : source === "recorded" ? "badge-replay" : ""}`;
  const stateBadge = document.getElementById("tn-state");
  stateBadge.textContent = status.state;
  stateBadge.className = `badge ${["running", "paused"].includes(status.state) ? "badge-ok" : "badge-warn"}`;
  const c = status.counters || {};
  const reason = status.reason ? ` · ${status.reason}` : "";
  document.getElementById("tn-status").textContent = source === "live"
    ? `session ${status.session_id} · ${status.open_orders} open · ${c.placed || 0} placed · ${c.cancelled || 0} cancelled · ` +
      `${c.filled || 0} filled · ${c.partially_filled || 0} partial · weight ${pct(status.weight_used_ratio, 0)}${reason}`
    : `Trader off${reason}.`;
  const note = document.getElementById("tn-source-note");
  if (source === "recorded" && payload.recorded) {
    const r = payload.recorded;
    const set = r.settings || {};
    note.textContent = `Showing a recorded real testnet session from ${new Date(r.recorded_at).toLocaleString()} ` +
      `(${r.minutes} min, spread ${set.spread_bps} bps, replace threshold ${set.replace_threshold_bps} bps). ` +
      `${payload.how_to_enable}`;
  } else if (source === "none") {
    note.textContent = payload.how_to_enable;
  } else {
    note.textContent = payload.explanations.D3;
  }
  const titles = {
    D1: "Testnet only, enforced by the lab.",
    D2: "Without keys, a recorded session.",
    D3: "Same inputs, different fill engines.",
    D4: "Maker-only orders and a kill switch that cancels.",
    D5: "Fill-model accuracy, not testnet profit.",
    D6: "Every order reconciled with the exchange.",
    D7: "Beside the trading loop, within the limits.",
  };
  document.getElementById("tn-explanations").innerHTML = Object.keys(titles)
    .map((key) => `<p><strong>${titles[key]}</strong> ${esc(payload.explanations[key])}</p>`)
    .join("");
  const rec = status.reconcile || {};
  const reconcile = source === "recorded"
    ? `<span class="badge badge-ok">all orders final</span><span class="sub">${esc(payload.explanations.D6.split(". ")[0])}.</span>`
    : rec.ok === null || rec.ok === undefined
      ? "-"
      : `<span class="badge ${rec.ok ? "badge-ok" : "badge-warn"}">${rec.ok ? "matches the exchange" : "checking"}</span>` +
        `<span class="sub">last ${fmtTime(rec.last_at)} · ${rec.unexplained || 0} unexplained · ${c.leftovers_cancelled || 0} leftovers cancelled · ${c.resets || 0} resets</span>`;
  document.getElementById("tn-reconcile").innerHTML = reconcile;
}

function renderTestnetComparison(payload, balances) {
  const models = payload.models || [];
  const first = models[0];
  const ex = payload.explanations || {};
  document.getElementById("tn-orders").innerHTML = first
    ? `${first.orders} · ${first.real_filled}<span class="sub">${esc(ex.fill_rate)}</span>`
    : "-";
  const r = payload.rejects || {};
  const rejected = Object.values(r).reduce((a, b) => a + b, 0);
  document.getElementById("tn-rejects").innerHTML =
    `${rejected}<span class="sub">post-only ${r.post_only || 0} · min size ${r.min_size || 0} · min value ${r.min_notional || 0} · unfundable ${r.unfundable || 0}. ${esc(ex.rejects)}</span>`;
  const v = balances.virtual;
  document.getElementById("tn-pnl").innerHTML = v && v.pnl !== null && v.pnl !== undefined
    ? `${usd2(v.pnl)}<span class="sub">${qty(v.base)} BTC · ${usd2(v.quote)} USDT at mid ${fmt(v.mid, 2)}. Testnet PnL, not a mainnet forecast (D5).</span>`
    : "-";
  const none = "no fills yet";
  document.getElementById("tn-compare").innerHTML = models.length && first.orders
    ? models.map((m) => `<tr>
        <td class="mono">${MODEL_LABELS[m.fill_model] || m.fill_model}</td>
        <td class="numeric">${m.real_fill_rate === null ? none : `${pct(m.real_fill_rate)} / ${pct(m.paper_fill_rate)}`}</td>
        <td class="numeric">${m.matched}</td><td class="numeric">${m.paper_only}</td>
        <td class="numeric">${m.real_only}</td><td class="numeric">${m.neither}</td>
        <td class="numeric">${secs(m.median_time_to_fill_real_sec)} / ${secs(m.median_time_to_fill_paper_sec)}</td>
        <td class="numeric">${m.mean_size_diff === null ? "-" : `${qty(m.mean_size_diff)} · ${bps(m.mean_price_diff_bps)}`}</td>
        <td class="numeric">${usd2(m.real_pnl)} / ${usd2(m.paper_pnl)} / ${usd2(m.pnl_gap)}</td>
      </tr>`).join("")
    : `<tr><td colspan="9">${payload.source === "none" ? "No testnet data: set free testnet keys to run it live." : "No orders yet"}</td></tr>`;
  const order = ["matched", "paper_only", "real_only", "time_to_fill", "size_price", "pnl_gap"];
  document.getElementById("tn-field-explanations").innerHTML = order
    .map((key) => `<p><strong>${key.replace(/_/g, " ")}:</strong> ${esc(ex[key])}</p>`)
    .join("");
}

function renderTestnetOrders(open, recent, fills) {
  document.getElementById("tn-open").innerHTML = open.length
    ? open.map((o) => `<tr><td>${fmtTime(o.placed_at)}</td><td class="mono">${esc(o.client_order_id)}</td><td>${o.side}</td>
        <td class="numeric">${fmt(o.price, 2)}</td><td class="numeric">${qty(o.size)}</td><td>${o.state}</td><td class="numeric">${qty(o.filled_size)}</td></tr>`).join("")
    : "<tr><td colspan='7'>No open orders</td></tr>";
  document.getElementById("tn-recent").innerHTML = recent.length
    ? recent.map((o) => {
        const p = o.paper || {};
        const events = (o.events || []).map((e) => e.kind).join(" → ");
        return `<tr><td>${fmtTime(o.placed_at)}</td><td class="mono">${esc(o.client_order_id)}</td><td>${o.side}</td>
          <td class="numeric">${fmt(o.price, 2)}</td><td>${o.state}${o.reject_reason ? ` (${o.reject_reason})` : ""}</td>
          <td class="numeric">${qty(o.filled_size)}</td>
          <td class="numeric">${qty(p.full_cross_fill || 0)} · ${qty(p.partial_fill || 0)} · ${qty(p.latency_prob_fill || 0)} · ${qty(p.calibrated_fill || 0)}</td>
          <td class="muted">${esc(events)}</td></tr>`;
      }).join("")
    : "<tr><td colspan='8'>No orders yet</td></tr>";
  document.getElementById("tn-fills").innerHTML = fills.length
    ? fills.map((f) => `<tr><td>${fmtTime(f.at)}</td><td class="mono">${esc(f.client_order_id)}</td><td>${f.side}</td>
        <td class="numeric">${fmt(f.price, 2)}</td><td class="numeric">${qty(f.size)}</td><td class="numeric">${fmt(f.fee, 6)} ${esc(f.fee_asset)}</td></tr>`).join("")
    : "<tr><td colspan='6'>No fills yet</td></tr>";
}

// --- fill-model calibration (specs/006-fill-model-calibration) ------------------
function renderCalibration(payload) {
  const cal = payload.calibration;
  const titles = {
    D1: "A fill chance for touches the snapshots cannot see.",
    D2: "Fitted from recorded real sessions, offline.",
    D3: "Judged on sessions it was not fitted on.",
    D4: "An option, not the default; labeled outside its range.",
    D5: "Random but repeatable; expected count shown.",
    D6: "Several spreads, every recording replayed.",
    trades: "What the public trades show.",
    volatility: "Volatility: tested, not adopted.",
    volatility_measure: "Measured from the snapshots the model already has.",
    volatility_gate: "Kept only if it wins on held-out sessions.",
    volatility_sessions: "Spreads alternated; replay checks every model.",
  };
  document.getElementById("tn-cal-explanations").innerHTML = Object.keys(titles)
    .map((key) => `<p><strong>${titles[key]}</strong> ${esc(payload.explanations[key])}</p>`)
    .join("");
  if (!payload.available || !cal) {
    document.getElementById("tn-cal-summary").textContent =
      `No calibration file: calibrated_fill behaves like full_cross_fill. Create one with ${payload.how_to_calibrate}`;
    document.getElementById("tn-cal-heldout").innerHTML = "<tr><td colspan='8'>No calibration yet</td></tr>";
    document.getElementById("tn-cal-curve").innerHTML = "<tr><td colspan='5'>No calibration yet</td></tr>";
    document.getElementById("tn-cal-forms").innerHTML = "<tr><td colspan='8'>No calibration yet</td></tr>";
    document.getElementById("tn-cal-gate").textContent = "";
    return;
  }
  const range = cal.distance_range_bps || [];
  const decay = cal.decay_bps === null ? "no decay" : `falls by e every ${fmt(cal.decay_bps, 2)} bps`;
  const v = cal.validation;
  const verdict = v
    ? ` Held out, calibrated_fill was closest to the real count in ${v.calibrated_closest_in} of ${v.held_out.length} sessions` +
      ` (goal: ${Math.ceil(0.75 * v.held_out.length)}) — ${v.goal_met ? "goal met" : "goal not met"}.`
    : " Leave-one-out needs at least two sessions.";
  const vol = cal.volatility_exponent === null || cal.volatility_exponent === undefined
    ? ""
    : `, scales as (volatility / ${fmt(cal.volatility_reference, 2)} bps/√s)^${fmt(cal.volatility_exponent, 2)}`;
  document.getElementById("tn-cal-summary").textContent =
    `Fill rate at the touch ${fmt(cal.touch_rate_per_sec, 5)} per second, ${decay}${vol}; fitted on ${cal.sources.length} sessions, ` +
    `${cal.intervals} intervals, ${cal.events} fills the snapshots missed, distances ${fmt(range[0], 2)} to ${fmt(range[1], 2)} bps.${verdict}`;
  const rows = v ? v.held_out.map((r) => `<tr>
      <td class="mono">${esc(r.session_id)}</td><td class="numeric">${fmt(r.spread_bps, 0)} bps</td>
      <td class="numeric">${r.orders}</td><td class="numeric"><strong>${r.real_fills}</strong></td>
      <td class="numeric">${r.models.full_cross_fill}</td><td class="numeric">${r.models.partial_fill}</td>
      <td class="numeric">${r.models.latency_prob_fill}</td>
      <td class="numeric">${fmt(r.calibrated_expected, 1)} / ${r.models.calibrated_fill}</td></tr>`) : [];
  if (v) {
    const e = v.mean_abs_error;
    rows.push(`<tr><td colspan="4"><strong>Mean |model − real|</strong></td>
      <td class="numeric">${fmt(e.full_cross_fill, 1)}</td><td class="numeric">${fmt(e.partial_fill, 1)}</td>
      <td class="numeric">${fmt(e.latency_prob_fill, 1)}</td><td class="numeric">${fmt(e.calibrated_fill, 1)} (expected)</td></tr>`);
  }
  document.getElementById("tn-cal-heldout").innerHTML = rows.length ? rows.join("") : "<tr><td colspan='8'>Leave-one-out needs at least two sessions</td></tr>";
  const gate = cal.gate;
  document.getElementById("tn-cal-gate").textContent = gate
    ? `Volatility term: scored better on ${gate.held_out_wins} of ${gate.sessions} held-out sessions (needs ${gate.needed}), ` +
      `k above zero: ${gate.k_positive ? "yes" : "no"} — ${gate.passed ? "kept, in use" : "not kept, base form in use"}. ` +
      "Score: log-likelihood of every look at every order in the held-out session; higher is better."
    : "Volatility term: not tested (needs at least two sessions).";
  document.getElementById("tn-cal-forms").innerHTML = v && v.held_out[0] && v.held_out[0].forms
    ? v.held_out.map((r) => {
        const b = r.forms.base, w = r.forms.volatility;
        const better = w.log_likelihood > b.log_likelihood;
        return `<tr>
          <td class="mono">${esc(r.session_id)}</td><td class="numeric">${fmt(r.spread_bps, 0)} bps</td>
          <td class="numeric">${fmt(r.volatility_bps_sqrt_s, 2)}</td><td class="numeric"><strong>${r.unseen_fills}</strong></td>
          <td class="numeric">${fmt(b.expected, 1)} / ${b.drawn}</td><td class="numeric">${fmt(w.expected, 1)} / ${w.drawn}</td>
          <td class="numeric">${fmt(b.log_likelihood, 1)}</td>
          <td class="numeric">${better ? "<strong>" : ""}${fmt(w.log_likelihood, 1)}${better ? "</strong>" : ""}</td></tr>`;
      }).join("")
    : "<tr><td colspan='8'>Not available</td></tr>";
  const chanceCell = (value) => (value === null || value === undefined ? "—" : pct(value, 2));
  document.getElementById("tn-cal-curve").innerHTML = payload.rate_curve.map((p) => `<tr>
      <td class="numeric">${fmt(p.distance_bps, 1)} bps</td><td class="numeric">${pct(p.fill_chance_per_tick, 2)}</td>
      <td class="numeric">${chanceCell(p.fill_chance_calm)}</td><td class="numeric">${chanceCell(p.fill_chance_busy)}</td>
      <td>${p.extended ? '<span class="badge badge-warn">extended, not measured</span>' : '<span class="badge badge-ok">calibrated</span>'}</td></tr>`).join("");
}

let calibrationLoaded = false;
async function refreshCalibration() {
  if (calibrationLoaded) return;
  try {
    renderCalibration(await fetchJson("/testnet/calibration"));
    calibrationLoaded = true;
  } catch (error) {
    document.getElementById("tn-cal-summary").textContent = `Calibration error: ${error.message}`;
  }
}

async function refreshTestnet() {
  refreshCalibration();
  try {
    const [status, comparison, balances, open, recent, fills] = await Promise.all([
      fetchJson("/testnet/status"),
      fetchJson("/testnet/comparison?window_minutes=1440"),
      fetchJson("/testnet/balances"),
      fetchJson("/testnet/orders?state=open&limit=20"),
      fetchJson("/testnet/orders?state=final&limit=25"),
      fetchJson("/testnet/fills?limit=20"),
    ]);
    renderTestnetStatus(status);
    renderTestnetComparison(comparison, balances);
    renderTestnetOrders(open.items, recent.items, fills.items);
  } catch (error) {
    document.getElementById("tn-status").textContent = `Testnet error: ${error.message}`;
  }
}

async function refreshArb() {
  try {
    const [latest, history, accuracy] = await Promise.all([
      fetchJson("/arb/simulation"),
      fetchJson("/arb/simulations?limit=120"),
      fetchJson("/arb/accuracy"),
    ]);
    renderArbStatus(latest);
    renderArbLatest(latest.latest);
    arbHistoryCache = history.items;
    renderArbHistory(history.items);
    renderGapChart(history.items);
    renderAccuracy(accuracy);
  } catch (error) {
    document.getElementById("arb-note").textContent = `Flash-arb error: ${error.message}`;
  }
}

async function refreshLp() {
  try {
    const [pool, lp, books, rebalances] = await Promise.all([
      fetchJson("/v3/pool"),
      fetchJson("/lp/position"),
      fetchJson("/research/books"),
      fetchJson("/lp/rebalances?limit=10"),
    ]);
    lpPoolCache = pool;
    lpPositionCache = lp;
    renderPool(pool);
    renderLpPosition(lp);
    renderLiquidityChart(pool, lp);
    renderBooks(books);
    renderRebalances(rebalances.items);
  } catch (error) {
    document.getElementById("v3-note").textContent = `V3/LP error: ${error.message}`;
  }
}

document.getElementById("quote-btn").addEventListener("click", async () => {
  const side = document.getElementById("quote-side").value;
  const amount = document.getElementById("quote-amount").value;
  const out = document.getElementById("quote-result");
  out.textContent = "Quoting…";
  try {
    const q = await fetchJson(`/v3/quote?side=${side}&amount=${encodeURIComponent(amount)}`);
    if (q.disabled_reason) {
      out.textContent = `V3 disabled (${q.disabled_reason})`;
      return;
    }
    const outUnit = side === "sell_base" ? "USDC" : "WETH";
    const limit = q.limit_reason ? ` · LIMITED: ${q.limit_reason} (consumed ${fmt(q.amount_in_consumed, 6)})` : "";
    out.textContent = `Out ${fmt(q.amount_out, 6)} ${outUnit} · exec price ${fmt(q.execution_price, 2)} · ` +
      `impact ${fmt(q.price_impact_bps, 2)} bps · ticks crossed ${q.ticks_crossed} · ` +
      `${q.exact ? "exact at block " + q.block_number : "window from block " + q.window_block_number}${limit}`;
  } catch (error) {
    out.textContent = `Quote failed: ${error.message}`;
  }
});

document.getElementById("run-lp-backtest-btn").addEventListener("click", async () => {
  const body = document.getElementById("lp-backtest-body");
  body.innerHTML = "<tr><td colspan='7'>Running…</td></tr>";
  try {
    const payload = await fetchJson("/research/lp-backtest?widths_bps=20,50,200&thresholds_bps=10,40");
    body.innerHTML = payload.rows.map((r) => `
      <tr>
        <td class="numeric">${r.range_width_bps}</td>
        <td class="numeric">${r.rebalance_threshold_bps}</td>
        <td class="numeric">${r.rebalance_count}</td>
        <td class="numeric">${fmt(r.time_in_range_pct, 1)}%</td>
        <td class="numeric">${usd(r.fees_quote, 4)}</td>
        <td class="numeric">${signedUsd(r.il_quote)}</td>
        <td class="numeric">${signedUsd(r.net_pnl_quote)}</td>
      </tr>`).join("");
  } catch (error) {
    body.innerHTML = `<tr><td colspan='7'>Error: ${error.message}</td></tr>`;
  }
});

// Hidden canvases have no width: draw a section's charts when it is shown.
function redrawCharts(name) {
  if (name === "live") {
    fetchJson("/pnl/history?limit=200").then((p) => renderEquityCurve(p.points)).catch(() => {});
  }
  if (name === "lp") renderLiquidityChart(lpPoolCache, lpPositionCache);
  if (name === "arb") renderGapChart(arbHistoryCache);
  if (name === "mempool") renderVisibilityChart(mpBlocksCache);
}
onSectionShown(redrawCharts);
onThemeChange(() => redrawCharts(sectionFromHash()));

document.getElementById("kill-switch-btn").addEventListener("click", async () => {
  const btn = document.getElementById("kill-switch-btn");
  const enabling = btn.dataset.active !== "true";
  const action = enabling ? "ENABLE" : "DISABLE";
  if (!window.confirm(`${action} kill switch? This cancels open quotes when enabling.`)) {
    return;
  }
  const headers = { "Content-Type": "application/json" };
  if (btn.dataset.auth === "true") {
    if (!operatorToken) {
      operatorToken = window.prompt("Operator token required for kill switch");
    }
    if (!operatorToken) return;
    headers["X-Operator-Token"] = operatorToken;
  }
  try {
    await fetchJson("/kill-switch", {
      method: "POST",
      headers,
      body: JSON.stringify({ active: enabling }),
    });
    await refresh();
  } catch (error) {
    operatorToken = null;
    window.alert(`Kill switch failed: ${error.message}`);
  }
});

document.getElementById("run-compare-btn").addEventListener("click", async () => {
  const body = document.getElementById("compare-body");
  body.innerHTML = "<tr><td colspan='6'>Running…</td></tr>";
  try {
    const payload = await fetchJson("/research/compare");
    renderCompare(payload.rows);
  } catch (error) {
    body.innerHTML = `<tr><td colspan='6'>Error: ${error.message}</td></tr>`;
  }
});

document.getElementById("run-sweep-btn").addEventListener("click", async () => {
  const body = document.getElementById("sweep-body");
  body.innerHTML = "<tr><td colspan='6'>Running…</td></tr>";
  try {
    const payload = await fetchJson("/research/sweep", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spreads_bps: configCache ? [configCache.quote_spread_bps, configCache.quote_spread_bps * 1.5] : [30, 45],
        fill_modes: configCache ? [configCache.fill_mode] : ["full_cross_fill"],
      }),
    });
    latestSweep = payload;
    renderSweep(payload.rows);
    document.getElementById("export-sweep-btn").disabled = false;
  } catch (error) {
    body.innerHTML = `<tr><td colspan='6'>Error: ${error.message}</td></tr>`;
  }
});

document.getElementById("export-sweep-btn").addEventListener("click", () => {
  if (!latestSweep) return;
  const blob = new Blob([JSON.stringify(latestSweep, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "crypto-mm-lab-sweep.json";
  link.click();
  URL.revokeObjectURL(url);
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => redrawCharts(sectionFromHash()), 150);
});

refresh();
refreshLp();
setInterval(refresh, 2000);
setInterval(refreshLp, 4000);
refreshArb();
setInterval(refreshArb, 4000);
refreshMempool();
setInterval(refreshMempool, 4000);
refreshTestnet();
setInterval(refreshTestnet, 4000);
