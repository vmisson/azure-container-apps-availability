"use strict";

// Azure Container Apps availability dashboard.
// Loads data/latest.json + data/history.json, with no external dependency.

const STATUS_META = {
  OK: { label: "Available", color: "#22c55e" },
  CAPACITY: { label: "Saturated", color: "#f59e0b" },
  ERROR: { label: "Error", color: "#ef4444" },
  TIMEOUT: { label: "Timeout", color: "#a855f7" },
};
const STATUS_ORDER = ["OK", "CAPACITY", "ERROR", "TIMEOUT"];
const REFRESH_MS = 5 * 60 * 1000; // 5 minutes

const state = {
  latest: null,
  history: [],
  filter: "ALL",
  search: "",
};

const el = (id) => document.getElementById(id);

async function loadJSON(path) {
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

async function refresh() {
  document.body.classList.add("is-loading");
  try {
    const [latest, history] = await Promise.all([
      loadJSON("data/latest.json"),
      loadJSON("data/history.json").catch(() => []),
    ]);
    state.latest = latest;
    state.history = Array.isArray(history) ? history : [];
    renderAll();
  } catch (err) {
    el("updated").textContent = "Data unavailable";
    console.error(err);
  } finally {
    document.body.classList.remove("is-loading");
  }
}

function renderAll() {
  renderUpdated();
  renderCards();
  renderFilters();
  renderChart();
  renderTable();
}

function renderUpdated() {
  const node = el("updated");
  const ts = state.latest && state.latest.generated_at;
  if (!ts) {
    node.textContent = "—";
    return;
  }
  const date = new Date(ts);
  node.textContent = `Updated ${formatRelative(date)} · ${date.toLocaleString(
    "en-US"
  )}`;
  node.title = date.toISOString();
}

function formatRelative(date) {
  const diff = Math.round((Date.now() - date.getTime()) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return `${Math.floor(diff / 86400)} d ago`;
}

function renderCards() {
  const summary = (state.latest && state.latest.summary) || {};
  const cards = STATUS_ORDER.map((key) => {
    const meta = STATUS_META[key];
    return `
      <div class="card" style="--card-color:${meta.color}">
        <div class="card__value">${summary[key] || 0}</div>
        <div class="card__label">${meta.label}</div>
      </div>`;
  });
  cards.push(`
    <div class="card" style="--card-color:#4f8cff">
      <div class="card__value">${summary.total || 0}</div>
      <div class="card__label">Total tested</div>
    </div>`);
  el("cards").innerHTML = cards.join("");
}

function renderFilters() {
  const summary = (state.latest && state.latest.summary) || {};
  const chips = [
    `<button class="chip" data-status="ALL" aria-pressed="${
      state.filter === "ALL"
    }" style="--chip-color:#4f8cff">All (${summary.total || 0})</button>`,
  ];
  for (const key of STATUS_ORDER) {
    const meta = STATUS_META[key];
    chips.push(
      `<button class="chip" data-status="${key}" aria-pressed="${
        state.filter === key
      }" style="--chip-color:${meta.color}">${meta.label} (${
        summary[key] || 0
      })</button>`
    );
  }
  const container = el("filters");
  container.innerHTML = chips.join("");
  container.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      state.filter = chip.dataset.status;
      renderFilters();
      renderTable();
    });
  });
}

function renderTable() {
  const regions = (state.latest && state.latest.regions) || [];
  const search = state.search.trim().toLowerCase();
  const rows = regions.filter((r) => {
    if (state.filter !== "ALL" && r.status !== state.filter) return false;
    if (search && !r.region.toLowerCase().includes(search)) return false;
    return true;
  });

  el("empty").hidden = rows.length > 0;
  el("rows").innerHTML = rows
    .map((r) => {
      const meta = STATUS_META[r.status] || { label: r.status };
      return `
        <tr>
          <td><span class="badge badge--${escapeAttr(r.status)}">${escapeHtml(
        meta.label
      )}</span></td>
          <td class="region-name">${escapeHtml(r.region)}</td>
          <td class="detail">${escapeHtml(r.detail)}</td>
        </tr>`;
    })
    .join("");
}

function renderChart() {
  const history = state.history;
  const container = el("chart");
  const legend = el("legend");

  if (!history.length) {
    container.innerHTML =
      '<p class="empty">No history yet: it grows with every run.</p>';
    legend.innerHTML = "";
    return;
  }

  const W = 900;
  const H = 280;
  const pad = { t: 14, r: 14, b: 26, l: 30 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const n = history.length;

  const series = ["OK", "CAPACITY", "ERROR"].filter((key) =>
    history.some((h) => (h[key] || 0) > 0)
  );
  const maxY = Math.max(
    1,
    ...history.map((h) => h.total || 0),
    ...history.map((h) => Math.max(h.OK || 0, h.CAPACITY || 0, h.ERROR || 0))
  );

  const xAt = (i) => pad.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const yAt = (v) => pad.t + ih - (v / maxY) * ih;

  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const y = pad.t + ih - f * ih;
      const val = Math.round(f * maxY);
      return `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="var(--border)" stroke-width="1" />
        <text x="${pad.l - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="var(--text-dim)">${val}</text>`;
    })
    .join("");

  const paths = series
    .map((key) => {
      const color = STATUS_META[key].color;
      const points = history
        .map((h, i) => `${xAt(i).toFixed(1)},${yAt(h[key] || 0).toFixed(1)}`)
        .join(" ");
      const dots =
        n <= 60
          ? history
              .map(
                (h, i) =>
                  `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(
                    h[key] || 0
                  ).toFixed(1)}" r="2" fill="${color}" />`
              )
              .join("")
          : "";
      return `<polyline fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${points}" />${dots}`;
    })
    .join("");

  const first = new Date(history[0].timestamp);
  const last = new Date(history[n - 1].timestamp);
  const xLabels = `
    <text x="${pad.l}" y="${H - 8}" font-size="10" fill="var(--text-dim)">${formatShort(
    first
  )}</text>
    <text x="${W - pad.r}" y="${H - 8}" text-anchor="end" font-size="10" fill="var(--text-dim)">${formatShort(
    last
  )}</text>`;

  container.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="History of counters">
      ${gridLines}${paths}${xLabels}
    </svg>`;

  legend.innerHTML = series
    .map(
      (key) =>
        `<span class="legend__item"><span class="legend__dot" style="background:${STATUS_META[key].color}"></span>${STATUS_META[key].label}</span>`
    )
    .join("");
}

function formatShort(date) {
  return date.toLocaleString("en-US", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return String(value).replace(/[^A-Za-z0-9_-]/g, "");
}

function init() {
  el("refresh").addEventListener("click", refresh);
  el("search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderTable();
  });
  refresh();
  setInterval(refresh, REFRESH_MS);
}

init();
