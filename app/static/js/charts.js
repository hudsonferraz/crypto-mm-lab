// Shared SVG line chart with a crosshair tooltip (specs/009 FR-007, FR-014).
// Colors are CSS variables, so charts follow the theme without redrawing.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(lo, hi, count = 4) {
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.01 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) || raw;
  const start = Math.floor(lo / step) * step;
  const ticks = [];
  for (let v = start; v <= hi + step * 0.5; v += step) {
    const tick = Number(v.toPrecision(12));
    ticks.push(Math.abs(tick) < step * 1e-6 ? 0 : tick); // no "−0.00" on the zero line
  }
  return ticks;
}

/**
 * Draw series into an <svg> element.
 * series: [{ name, color: "var(--series-1)", dash?: "4 3", points: [{x, y}] }]
 * options: { xFormat, yFormat, tooltipTitle(x), zeroLine }
 */
function drawLineChart(svg, series, options = {}) {
  const xFormat = options.xFormat || ((x) => String(x));
  const yFormat = options.yFormat || ((y) => String(y));
  svg.innerHTML = "";
  const box = svg.getBoundingClientRect();
  const width = Math.max(box.width, 280);
  const height = Math.max(box.height, 120);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const all = series.flatMap((s) => s.points);
  if (!all.length) return;

  const xs = all.map((p) => p.x);
  const ys = all.map((p) => p.y);
  if (options.zeroLine) ys.push(0);
  const ticks = niceTicks(Math.min(...ys), Math.max(...ys));
  const yLo = ticks[0];
  const yHi = ticks[ticks.length - 1];
  const xLo = Math.min(...xs);
  const xHi = Math.max(...xs) || 1;
  const labelWidth = Math.max(...ticks.map((t) => yFormat(t).length)) * 6.5 + 10;
  const pad = { left: labelWidth, right: 12, top: 10, bottom: 24 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const xFor = (x) => pad.left + ((x - xLo) / (xHi - xLo || 1)) * plotW;
  const yFor = (y) => pad.top + plotH - ((y - yLo) / (yHi - yLo || 1)) * plotH;

  ticks.forEach((t) => {
    const y = Math.round(yFor(t)) + 0.5;
    svgEl("line", { x1: pad.left, x2: pad.left + plotW, y1: y, y2: y, class: t === 0 ? "baseline" : "gridline" }, svg);
    const label = svgEl("text", { x: pad.left - 6, y: y + 4, "text-anchor": "end" }, svg);
    label.textContent = yFormat(t);
  });
  [xLo, xHi].forEach((x, index) => {
    const label = svgEl("text", {
      x: xFor(x),
      y: height - 6,
      "text-anchor": index === 0 ? "start" : "end",
    }, svg);
    label.textContent = xFormat(x);
  });

  series.forEach((s) => {
    if (!s.points.length) return;
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${xFor(p.x).toFixed(1)},${yFor(p.y).toFixed(1)}`).join("");
    svgEl("path", {
      d,
      fill: "none",
      stroke: s.color,
      "stroke-width": 2,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      "stroke-dasharray": s.dash || "",
    }, svg);
  });

  // Hover layer: crosshair, markers and a tooltip (bigger hit area than the line).
  const crosshair = svgEl("line", { y1: pad.top, y2: pad.top + plotH, class: "crosshair", visibility: "hidden" }, svg);
  const markers = series.map((s) =>
    svgEl("circle", { r: 4, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" }, svg));
  const hit = svgEl("rect", { x: pad.left, y: pad.top, width: plotW, height: plotH, fill: "transparent" }, svg);
  const wrap = svg.parentElement;
  let tooltip = wrap.querySelector(".chart-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    wrap.appendChild(tooltip);
  }
  const nearest = (points, x) => {
    let best = points[0];
    for (const p of points) if (Math.abs(p.x - x) < Math.abs(best.x - x)) best = p;
    return best;
  };
  const move = (clientX) => {
    const rect = svg.getBoundingClientRect();
    const scale = width / rect.width;
    const x = xLo + (((clientX - rect.left) * scale - pad.left) / plotW) * (xHi - xLo);
    const anchor = nearest(series[0].points, x);
    const px = xFor(anchor.x);
    crosshair.setAttribute("x1", px);
    crosshair.setAttribute("x2", px);
    crosshair.setAttribute("visibility", "visible");
    const rows = series.map((s, i) => {
      const p = nearest(s.points, anchor.x);
      markers[i].setAttribute("cx", xFor(p.x));
      markers[i].setAttribute("cy", yFor(p.y));
      markers[i].setAttribute("visibility", "visible");
      return `<div><span class="run-swatch" style="background:${s.color}"></span>${esc(s.name)}: <strong>${esc(yFormat(p.y))}</strong></div>`;
    });
    const title = options.tooltipTitle ? options.tooltipTitle(anchor.x) : xFormat(anchor.x);
    tooltip.innerHTML = `<div class="muted">${esc(title)}</div>${rows.join("")}`;
    tooltip.style.display = "block";
    const left = Math.min(rect.width - tooltip.offsetWidth - 4, Math.max(0, px / scale + 12));
    tooltip.style.left = `${left}px`;
    tooltip.style.top = "4px";
  };
  const leave = () => {
    tooltip.style.display = "none";
    crosshair.setAttribute("visibility", "hidden");
    markers.forEach((m) => m.setAttribute("visibility", "hidden"));
  };
  hit.addEventListener("mousemove", (event) => move(event.clientX));
  hit.addEventListener("mouseleave", leave);
  hit.addEventListener("touchstart", (event) => move(event.touches[0].clientX), { passive: true });
  hit.addEventListener("touchmove", (event) => move(event.touches[0].clientX), { passive: true });
  hit.addEventListener("touchend", leave);
}
