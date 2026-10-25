// Historia placówki, statystyki województwa i mapa: ładowane leniwie z app.js.
import { $, h, t, state, fmtDate, fmtNumber, plural } from "./app.js";

// ------------------------------------------------------------------ historia

export async function loadHistory(id, details) {
  const box = details.querySelector("div");
  if (box.dataset.loaded) return;
  box.dataset.loaded = "1";
  try {
    const res = await fetch(`/api/history/${encodeURIComponent(id)}`);
    const trend = await res.json();
    const points = trend.points.filter((p) => p.wait_days != null);
    if (points.length < 2) { box.replaceChildren(h("p", {}, t("history.empty"))); return; }
    const change = Math.abs(trend.change_days || 0);
    box.replaceChildren(
      h("p", {}, t(`history.${trend.direction}`, { days: change })),
      chart(points),
      h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": t("history.title") }, h("table", {},
        h("caption", {}, t("history.title")),
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, t("history.col_day")), h("th", { scope: "col" }, t("history.col_date")), h("th", { scope: "col" }, t("history.col_wait")))),
        h("tbody", {}, trend.points.map((p) => h("tr", {}, h("td", {}, fmtDate(p.taken_on)), h("td", {}, fmtDate(p.first_date) || "—"), h("td", {}, p.wait_days ?? "—")))))));
  } catch {
    box.replaceChildren(h("p", {}, t("results.error")));
  }
}

function chart(points) {
  const ns = "http://www.w3.org/2000/svg";
  const W = 320, H = 140, P = 24;
  const max = Math.max(...points.map((p) => p.wait_days), 1);
  const xs = (i) => P + (i * (W - 2 * P)) / Math.max(points.length - 1, 1);
  const ys = (v) => H - P - (v * (H - 2 * P)) / max;
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", t("history.chart_label"));
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("points", points.map((p, i) => `${xs(i)},${ys(p.wait_days)}`).join(" "));
  svg.append(line);
  points.forEach((p, i) => {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", xs(i)); c.setAttribute("cy", ys(p.wait_days)); c.setAttribute("r", 4);
    svg.append(c);
  });
  for (const [x, y, text] of [[P, 14, `${max}`], [P, H - 6, fmtDate(points[0].taken_on)], [W - P - 70, H - 6, fmtDate(points.at(-1).taken_on)]]) {
    const el = document.createElementNS(ns, "text");
    el.setAttribute("x", x); el.setAttribute("y", y); el.textContent = text;
    svg.append(el);
  }
  return svg;
}

// ------------------------------------------------------------------ statystyki i mapa cieplna

function heatColor(days, min, max) {
  const ratio = max > min ? (days - min) / (max - min) : 0;
  const palette = ["#1f4fb8", "#5677c2", "#d08a3c", "#e2671f", "#b23f00"]; // niebieski → pomarańcz
  return palette[Math.min(palette.length - 1, Math.floor(ratio * palette.length))];
}

export async function toggleStats() {
  const box = $("#stats");
  const button = $("#show-stats");
  if (!box.hidden) { box.hidden = true; button.setAttribute("aria-expanded", "false"); return; }
  const data = state.last;
  if (!data) return;
  const province = data.provinces.includes(data.point?.province) ? data.point.province : data.provinces[0];
  const params = new URLSearchParams({ q: state.lastParams.get("q"), province, case: String(data.case) });
  if (data.variant !== "wszystkie") params.set("variant", data.variant);
  box.hidden = false;
  button.setAttribute("aria-expanded", "true");
  box.replaceChildren(h("p", {}, t("results.loading")));
  try {
    const res = await fetch(`/api/stats?${params}`);
    const s = await res.json();
    if (!res.ok || s.median == null) { box.replaceChildren(h("p", {}, t("stats.none"))); return; }
    const medians = s.districts.map((d) => d.median_days);
    const [min, max] = [Math.min(...medians), Math.max(...medians)];
    const exportParams = new URLSearchParams({ q: state.lastParams.get("q"), province });
    const days = (v) => `${fmtNumber(v)} ${plural("days", Math.round(v))}`;
    const tile = (label, value, extra = "") => h("div", { class: `stat-tile${extra}` }, h("span", {}, label), h("strong", {}, value));
    box.replaceChildren(
      h("h3", {}, t("stats.title", { label: s.label, province: s.province_name })),
      h("div", { class: "stat-tiles", "aria-hidden": "true" },
        tile(t("stats.tile_median"), days(s.median)),
        tile(t("stats.tile_half"), `${fmtNumber(s.p25)}–${fmtNumber(s.p75)}`),
        tile(t("stats.tile_p90"), days(s.p90)),
        tile(t("stats.tile_min"), days(s.minimum), " stat-tile--signal")),
      h("p", {}, t("stats.summary", { median: fmtNumber(s.median), p25: fmtNumber(s.p25), p75: fmtNumber(s.p75), p90: fmtNumber(s.p90), n: s.with_date })),
      h("div", { class: "table-wrap", tabindex: "0", role: "region", "aria-label": t("stats.button") }, h("table", {},
        h("thead", {}, h("tr", {}, ...["col_district", "col_places", "col_n", "col_median"].map((k) => h("th", { scope: "col" }, t(`stats.${k}`))))),
        h("tbody", {}, [...s.districts].sort((a, b) => a.median_days - b.median_days).map((d) => h("tr", {},
          h("td", {}, d.teryt), h("td", {}, d.localities.slice(0, 3).join(", ")), h("td", {}, d.n),
          h("td", {}, h("span", { class: "heat", "aria-hidden": "true", "data-color": heatColor(d.median_days, min, max) }), fmtNumber(d.median_days))))))),
      h("p", {}, h("a", { href: `/api/export.csv?${exportParams}` }, t("stats.export"))));
    for (const dot of box.querySelectorAll(".heat")) dot.style.background = dot.dataset.color;
    if (state.map) drawHeat(s.districts, min, max);
  } catch {
    box.replaceChildren(h("p", {}, t("results.error")));
  }
}

function loadAsset(tag, attrs) {
  return new Promise((resolve, reject) => {
    const el = Object.assign(document.createElement(tag), attrs);
    el.onload = resolve; el.onerror = reject;
    document.head.append(el);
  });
}

export async function toggleMap() {
  const box = $("#map");
  const button = $("#toggle-map");
  if (!box.hidden) {
    box.hidden = true;
    button.setAttribute("aria-expanded", "false");
    button.textContent = t("results.show_map");
    return;
  }
  box.hidden = false;
  box.setAttribute("aria-label", t("results.map_label"));
  button.setAttribute("aria-expanded", "true");
  button.textContent = t("results.hide_map");
  if (!window.L) {
    await loadAsset("link", { rel: "stylesheet", href: "/static/vendor/leaflet/leaflet.css" });
    await loadAsset("script", { src: "/static/vendor/leaflet/leaflet.js" });
  }
  if (!state.map) {
    state.map = window.L.map(box, { scrollWheelZoom: false });
    window.L.tileLayer(state.config.tile_url, { attribution: state.config.tile_attribution, maxZoom: 18 }).addTo(state.map);
  }
  drawMarkers(state.last);
}

export function drawMarkers(data) {
  const L = window.L;
  if (!L || !state.map || !data) return;
  state.mapLayer?.remove();
  state.mapLayer = L.layerGroup().addTo(state.map);
  const bounds = [];
  if (data.point) {
    L.circleMarker([data.point.lat, data.point.lon], { radius: 9, color: "#12182b", fillColor: "#ff6a1a", fillOpacity: 1 }).addTo(state.mapLayer);
    bounds.push([data.point.lat, data.point.lon]);
  }
  data.results.forEach((r, i) => {
    if (r.latitude == null) return;
    const popup = document.createElement("div");
    popup.append(h("strong", {}, `${i + 1}. ${r.first_date ? fmtDate(r.first_date) : t("card.no_date")}`), h("br"), r.provider || "", h("br"), [r.address, r.locality].filter(Boolean).join(", "));
    L.marker([r.latitude, r.longitude], { title: `${i + 1}. ${r.provider || ""}`, alt: `${i + 1}` }).bindPopup(popup).addTo(state.mapLayer);
    bounds.push([r.latitude, r.longitude]);
  });
  if (bounds.length) state.map.fitBounds(bounds, { padding: [24, 24], maxZoom: 13 });
  else state.map.setView([52.1, 19.4], 6);
  setTimeout(() => state.map.invalidateSize(), 0);
}

function drawHeat(districts, min, max) {
  const L = window.L;
  state.heatLayer?.remove();
  state.heatLayer = L.layerGroup().addTo(state.map);
  for (const d of districts) {
    if (d.lat == null) continue;
    L.circle([d.lat, d.lon], { radius: 6000, color: heatColor(d.median_days, min, max), fillOpacity: 0.35 })
      .bindTooltip(`${d.localities.slice(0, 2).join(", ")}: ${fmtNumber(d.median_days)} ${plural("days", Math.round(d.median_days))}`)
      .addTo(state.heatLayer);
  }
}
