// Shared shell for the dashboard (specs/009 FR-016, FR-017, FR-019).
// Plain script, no build step: theme, section router, colors for canvas charts.
"use strict";

const SECTIONS = {
  overview: "Overview",
  live: "Live market making",
  lab: "Simulation lab",
  opportunities: "DEX vs CEX",
  research: "Research",
  lp: "Uniswap V3 and LP",
  arb: "Flash arbitrage",
  mempool: "Mempool",
  testnet: "Testnet",
};
const DEFAULT_SECTION = "overview";
const sectionListeners = [];
const themeListeners = [];

function onSectionShown(listener) {
  sectionListeners.push(listener);
}

function onThemeChange(listener) {
  themeListeners.push(listener);
}

// Canvas charts cannot read CSS variables directly; read them at draw time.
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartColors() {
  return {
    surface: cssVar("--surface-1"),
    ink: cssVar("--ink-1"),
    muted: cssVar("--ink-3"),
    grid: cssVar("--grid"),
    axis: cssVar("--axis"),
    series1: cssVar("--series-1"),
    series2: cssVar("--series-2"),
    series2Wash: cssVar("--series-2-wash"),
  };
}

// ------------------------------------------------------------------ tables

// Number columns are right-aligned; align each header with the cells under it.
function alignTableHeaders(root = document) {
  root.querySelectorAll("table").forEach((table) => {
    const head = table.tHead?.rows[0];
    const row = table.tBodies[0]?.rows[0];
    if (!head || !row || row.cells.length !== head.cells.length) return;
    [...row.cells].forEach((cell, index) => {
      head.cells[index].classList.toggle("numeric", cell.classList.contains("numeric"));
    });
  });
}

let alignTimer = null;
new MutationObserver(() => {
  clearTimeout(alignTimer);
  alignTimer = setTimeout(() => alignTableHeaders(), 100);
}).observe(document.documentElement, { childList: true, subtree: true });

// ------------------------------------------------------------------ theme

const THEME_KEY = "mmlab-theme";

function currentTheme() {
  const stored = document.documentElement.dataset.theme;
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme, persist) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {}
  }
  const button = document.getElementById("theme-toggle");
  if (button) {
    const next = theme === "dark" ? "light" : "dark";
    button.setAttribute("aria-label", `Switch to ${next} theme`);
    button.title = `Switch to ${next} theme`;
    button.textContent = theme === "dark" ? "☀" : "☾";
  }
  themeListeners.forEach((listener) => listener(theme));
}

(function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(THEME_KEY);
  } catch (_) {}
  if (stored === "dark" || stored === "light") {
    document.documentElement.dataset.theme = stored;
  }
})();

// ------------------------------------------------------------------ router

function showSection(name, { focus = false } = {}) {
  if (!SECTIONS[name]) name = DEFAULT_SECTION;
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `panel-${name}`);
  });
  document.querySelectorAll(".nav a[data-section]").forEach((link) => {
    if (link.dataset.section === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const title = document.getElementById("topbar-title");
  if (title) title.textContent = SECTIONS[name];
  document.title = `${SECTIONS[name]} · crypto-mm-lab`;
  document.querySelector(".app")?.classList.remove("nav-open");
  if (focus) {
    const heading = document.querySelector(`#panel-${name} h1, #panel-${name} h2`);
    if (heading) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
    window.scrollTo({ top: 0 });
  }
  sectionListeners.forEach((listener) => listener(name));
}

function sectionFromHash() {
  // Old links (#live, #arb, ...) keep working: the hash is the section name (FR-019).
  return location.hash.slice(1) || DEFAULT_SECTION;
}

window.addEventListener("hashchange", () => showSection(sectionFromHash(), { focus: true }));

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(currentTheme(), false);
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!localStorage.getItem(THEME_KEY)) applyTheme(currentTheme(), false);
  });
  const app = document.querySelector(".app");
  document.getElementById("menu-toggle")?.addEventListener("click", () => {
    const open = app.classList.toggle("nav-open");
    document.getElementById("menu-toggle").setAttribute("aria-expanded", String(open));
  });
  document.querySelector(".scrim")?.addEventListener("click", () => app.classList.remove("nav-open"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") app.classList.remove("nav-open");
  });
  showSection(sectionFromHash());
});
