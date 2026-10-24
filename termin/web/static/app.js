// Termin: frontend bez frameworka (ADR-0004). DOM budujemy przez textContent (dane NFZ to
// treść z zewnątrz), teksty pochodzą z /static/i18n/<język>.json.

const $ = (sel) => document.querySelector(sel);
const LANGS = ["pl", "en", "uk"];
const STORE = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(`termin:${key}`); return v === null ? fallback : JSON.parse(v); }
    catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`termin:${key}`, JSON.stringify(value)); } catch { /* tryb prywatny */ }
  },
};

const state = {
  lang: "pl",
  dict: {},
  config: null,
  point: null,          // { lat, lon } z GPS
  variant: null,        // "dorosli" | "dzieci"
  last: null,           // ostatnia odpowiedź API
  lastParams: null,
  map: null,
  mapLayer: null,
  heatLayer: null,
  suggestTimer: null,
  activeSuggestion: -1,
  watchFor: null,
};

// ------------------------------------------------------------------ i18n

function lookup(key) {
  return key.split(".").reduce((node, part) => (node && typeof node === "object" ? node[part] : undefined), state.dict);
}

function plural(word, n) {
  const forms = lookup(`plurals.${word}`) || {};
  const rule = new Intl.PluralRules(state.lang).select(n);
  return forms[rule] || forms.other || forms.many || "";
}

function fmtNumber(value) {
  if (typeof value !== "number") return value;
  return new Intl.NumberFormat(state.lang, { maximumFractionDigits: 1 }).format(value);
}

function t(key, params = {}) {
  let text = lookup(key);
  if (typeof text !== "string") return key;
  const values = { ...params };
  if (typeof params.days === "number") values.days_word = plural("days", params.days);
  if (typeof params.km === "number") values.km = fmtNumber(params.km);
  for (const k of ["date", "as_at"]) if (typeof values[k] === "string" && /^\d{4}-\d{2}-\d{2}$/.test(values[k])) values[k] = fmtDate(values[k]);
  return text.replace(/\{(\w+)\}/g, (_, name) => (values[name] ?? `{${name}}`));
}

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat(state.lang === "en" ? "en-GB" : state.lang, { day: "2-digit", month: "2-digit", year: "numeric" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

function whenText(days) {
  if (days === 0) return t("when.today");
  if (days === 1) return t("when.tomorrow");
  if (days > 1) return t("when.in_days", { days });
  return t("when.days_ago", { days: -days });
}

async function loadLang(lang) {
  if (!LANGS.includes(lang)) lang = "pl";
  const res = await fetch(`/static/i18n/${lang}.json`);
  state.dict = await res.json();
  state.lang = lang;
  document.documentElement.lang = lang;
  $("#lang").value = lang;
  for (const el of document.querySelectorAll("[data-i18n]")) {
    const text = lookup(el.dataset.i18n);
    if (typeof text === "string") el.textContent = text;
  }
  document.title = t("app.title");
  updateKmOutput();
  if (state.last) renderResponse(state.last);
}

// ------------------------------------------------------------------ DOM

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function announce(text) { $("#status").textContent = text; }

// ------------------------------------------------------------------ podpowiedzi (ARIA combobox)

function closeSuggestions() {
  const list = $("#co-list");
  list.hidden = true;
  list.replaceChildren();
  $("#co").setAttribute("aria-expanded", "false");
  $("#co").removeAttribute("aria-activedescendant");
  state.activeSuggestion = -1;
}

function chooseSuggestion(item) {
  $("#co").value = item.query;
  state.variant = null;
  closeSuggestions();
}

async function suggest() {
  const q = $("#co").value.trim();
  if (q.length < 2) { closeSuggestions(); return; }
  try {
    const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`);
    const items = res.ok ? await res.json() : [];
    const list = $("#co-list");
    list.replaceChildren(...items.map((item, i) => h("li", {
      id: `sug-${i}`, role: "option", "aria-selected": "false",
      onmousedown: (e) => { e.preventDefault(); chooseSuggestion(item); },
    }, item.label)));
    list._items = items;
    list.hidden = items.length === 0;
    $("#co").setAttribute("aria-expanded", String(items.length > 0));
  } catch { closeSuggestions(); }
}

function moveSuggestion(delta) {
  const list = $("#co-list");
  const items = [...list.children];
  if (!items.length) return;
  state.activeSuggestion = (state.activeSuggestion + delta + items.length) % items.length;
  items.forEach((li, i) => li.setAttribute("aria-selected", String(i === state.activeSuggestion)));
  $("#co").setAttribute("aria-activedescendant", items[state.activeSuggestion].id);
}

// ------------------------------------------------------------------ wyszukiwanie

function formParams() {
  const form = $("#search-form");
  const data = new FormData(form);
  const params = new URLSearchParams();
  params.set("q", (data.get("q") || "").trim());
  if (state.point) {
    params.set("lat", state.point.lat.toFixed(5));
    params.set("lon", state.point.lon.toFixed(5));
  } else if ((data.get("where") || "").trim()) {
    params.set("where", data.get("where").trim());
  }
  params.set("radius", data.get("radius") || "50");
  const mode = data.get("mode") || "kompromis";
  params.set("mode", mode);
  if (mode === "kompromis") params.set("km_per_day", data.get("km_per_day") || "2");
  params.set("case", data.get("case") || "1");
  if (state.variant) params.set("variant", state.variant);
  return params;
}

async function runSearch(params) {
  const error = $("#form-error");
  error.hidden = true;
  if (!params.get("q") || (!params.get("where") && !params.get("lat"))) {
    error.textContent = t("form.required");
    error.hidden = false;
    (params.get("q") ? $("#gdzie") : $("#co")).focus();
    return;
  }
  const submit = $("#search-form button[type=submit]");
  submit.disabled = true;
  announce(t("results.loading"));
  state.lastParams = params;
  updateShareUrl(params);
  try {
    const res = await fetch(`/api/search?${params}`);
    const data = await res.json();
    if (!res.ok) {
      error.textContent = data.detail || t("results.error");
      error.hidden = false;
      announce("");
      return;
    }
    state.last = data;
    if (data.status === "ok") STORE.set("last", { params: params.toString(), data, savedAt: new Date().toISOString() });
    renderResponse(data);
    // Fokus na wynikach: czytnik ekranu przeczyta nagłówek, a telefon przewinie do listy.
    if (data.status === "ok" && !location.hash.includes("wynik=")) $("#results-heading").focus();
  } catch {
    const saved = STORE.get("last");
    if (saved && saved.params === params.toString()) {
      state.last = saved.data;
      renderResponse(saved.data);
      announce(t("results.offline", { date: fmtDate(saved.savedAt.slice(0, 10)) }));
    } else {
      error.textContent = t("results.error");
      error.hidden = false;
      announce("");
    }
  } finally {
    submit.disabled = false;
  }
}

function updateShareUrl(params) {
  const share = new URLSearchParams();
  share.set("q", params.get("q"));
  if (params.get("where")) share.set("gdzie", params.get("where"));
  // Do linku nie trafia dokładna pozycja GPS: zaokrąglamy do ~1 km.
  if (params.get("lat")) share.set("lat", Number(params.get("lat")).toFixed(2));
  if (params.get("lon")) share.set("lon", Number(params.get("lon")).toFixed(2));
  for (const [k, name] of [["radius", "r"], ["mode", "tryb"], ["case", "przypadek"], ["variant", "wariant"], ["km_per_day", "km"]]) {
    if (params.get(k)) share.set(name, params.get(k));
  }
  history.replaceState(null, "", `?${share}`);
}

function showChoices(label, options) {
  const box = $("#choices");
  $("#choices-label").textContent = label;
  $("#choices-list").replaceChildren(...options.map((opt) =>
    h("button", { type: "button", class: "btn btn-secondary chip", onclick: opt.onclick }, opt.label)));
  box.hidden = false;
  $("#choices-list button")?.focus();
}

function renderResponse(data) {
  $("#choices").hidden = true;
  if (data.status === "choose_variant") {
    $("#results").hidden = true;
    announce("");
    showChoices(t("form.variant_question"), ["dorosli", "dzieci"].map((v) => ({
      label: t(`form.variant_${v}`),
      onclick: () => { state.variant = v; runSearch(formParams()); },
    })));
    return;
  }
  if (data.status === "ambiguous") {
    $("#results").hidden = true;
    announce("");
    showChoices(t("form.candidates_question"), data.candidates.map((c) => ({
      label: c.label,
      onclick: () => { $("#co").value = c.query; state.variant = null; runSearch(formParams()); },
    })));
    return;
  }
  renderResults(data);
}

function renderResults(data) {
  const section = $("#results");
  section.hidden = false;
  $("#results-heading").textContent = t("results.heading", { label: data.label });
  const meta = [];
  if (data.point?.label) meta.push(t("results.near", { place: data.point.label }));
  if (data.total_found > data.results.length) meta.push(t("results.found_partial", { shown: data.results.length, n: data.total_found }));
  if (data.without_location) meta.push(t("results.skipped_no_location", { n: data.without_location }));
  if (data.benefit_names?.length > 1) meta.push(t("results.benefit_names", { names: data.benefit_names.join("; ") }));
  $("#results-meta").textContent = meta.join(" · ");

  const list = $("#result-list");
  list.replaceChildren(...data.results.map((r, i) => renderCard(r, i, data)));
  $("#stats").hidden = true;
  $("#show-stats").setAttribute("aria-expanded", "false");
  $("#show-stats").hidden = data.provinces.length === 0;
  if (state.map) drawMarkers(data);

  announce(data.results.length ? t("results.found", { n: data.total_found }) : t("results.none"));
  const target = location.hash.match(/wynik=([\w-]+)/);
  if (target) document.getElementById(`r-${target[1]}`)?.scrollIntoView();
}

function telHref(phone) {
  const digits = (phone || "").replace(/[^\d+]/g, "");
  if (!digits) return null;
  if (digits.startsWith("+")) return `tel:${digits}`;
  return digits.length === 9 ? `tel:+48${digits}` : `tel:${digits}`;
}

function directionsHref(r, data) {
  if (r.latitude == null) {
    const q = encodeURIComponent(`${r.address || ""}, ${r.locality || ""}`);
    return `https://www.openstreetmap.org/search?query=${q}`;
  }
  const to = `${r.latitude}%2C${r.longitude}`;
  const from = data.point ? `${data.point.lat}%2C${data.point.lon}` : "";
  return `https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=${from}%3B${to}`;
}

function renderCard(r, index, data) {
  const dateBlock = r.first_date
    ? h("p", { class: "date" }, fmtDate(r.first_date), h("small", {}, whenText(r.days_until)))
    : h("p", { class: "date none" }, t("card.no_date"));
  const distance = [];
  if (r.distance_km != null) distance.push(t("card.distance", { km: r.distance_km }));
  if (r.travel_min != null) distance.push(t("card.travel", { min: Math.round(r.travel_min) }));

  const reasons = r.reasons.map((x) => t(`reasons.${x.code}`, x.params));
  const why = reasons.length ? h("p", { class: "why" }, h("strong", {}, t("card.why")), " ", capitalize(reasons.join(", "))) : null;
  const alerts = r.alerts.length ? h("ul", { class: "alerts" }, r.alerts.map((a) => h("li", {}, t(`alerts.${a.code}`, a.params)))) : null;

  const access = Object.entries(r.accessibility).filter(([, v]) => v).map(([k]) => t(`card.${k}`));
  const tags = [];
  if (r.for_children) tags.push(t("card.children"));
  if (r.awaiting != null) tags.push(t("card.waiting", { n: r.awaiting }));
  if (access.length) tags.push(`${t("card.accessibility")} ${access.join(", ")}`);

  const tel = telHref(r.phone);
  const actions = h("div", { class: "actions" },
    tel ? h("a", { class: "btn btn-primary", href: tel }, `📞 ${t("card.call")}: ${r.phone}`) : null,
    h("a", { class: "btn btn-secondary", href: directionsHref(r, data), rel: "noopener", target: "_blank" }, t("card.directions")),
    h("button", { type: "button", class: "btn btn-secondary", onclick: () => openWatch(r, data) }, t("card.watch")),
    h("button", { type: "button", class: "btn btn-secondary", onclick: () => share(data) }, t("card.share")),
    h("a", { class: "btn btn-secondary", href: `/api/ics/${encodeURIComponent(r.id)}`, download: "zadzwon.ics" }, t("card.reminder")),
  );

  const history = h("details", { class: "history", ontoggle: (e) => e.target.open && loadHistory(r.id, e.target) },
    h("summary", {}, t("card.history")), h("div", {}));
  const script = h("details", { class: "script" }, h("summary", {}, t("card.script")), renderScript(r, data));

  return h("li", {},
    h("article", { class: "card result", id: `r-${r.id}`, "aria-label": `${t("card.rank", { n: index + 1 })}: ${r.provider || ""}` },
      h("div", { class: "result-top" }, dateBlock, distance.length ? h("p", { class: "distance" }, distance.join(" · ")) : null),
      h("h3", { class: "provider" }, r.provider || r.place || "—"),
      r.place && r.place !== r.benefit ? h("p", { class: "address" }, r.place) : null,
      h("p", { class: "address" }, [r.address, r.locality].filter(Boolean).join(", ")),
      r.as_at ? h("p", { class: "asat" }, t("card.as_at", { date: r.as_at })) : null,
      why, alerts,
      tags.length ? h("ul", { class: "tags" }, tags.map((x) => h("li", {}, x))) : null,
      actions, script, history,
    ));
}

function capitalize(text) { return text ? text[0].toUpperCase() + text.slice(1) : text; }

function renderScript(r, data) {
  const urgent = data.case === 2 ? t("script.urgent") : "";
  return h("div", {},
    h("p", {}, t("script.intro")),
    h("ol", {},
      h("li", {}, t("script.hello", { benefit: r.benefit.toLowerCase(), urgent })),
      h("li", {}, r.first_date && r.days_until >= 0 ? t("script.check", { date: r.first_date }) : t("script.check_nodate")),
      h("li", {}, t("script.earlier")),
      h("li", {}, t("script.referral"))),
    h("p", {}, h("strong", {}, t("script.prepare"))),
    h("ul", {}, ["item_referral", "item_pesel", "item_docs", "item_pen"].map((k) => h("li", {}, t(`script.${k}`)))),
    h("p", { class: "hint" }, t("script.note")));
}

// ------------------------------------------------------------------ historia

async function loadHistory(id, details) {
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
      h("div", { class: "table-wrap" }, h("table", {},
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
  const palette = ["#1a7f37", "#4d9a2a", "#b58900", "#cb6d00", "#b42318"];
  return palette[Math.min(palette.length - 1, Math.floor(ratio * palette.length))];
}

async function toggleStats() {
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
    box.replaceChildren(
      h("h3", {}, t("stats.title", { label: s.label, province: s.province_name })),
      h("p", {}, t("stats.summary", { median: fmtNumber(s.median), p25: fmtNumber(s.p25), p75: fmtNumber(s.p75), p90: fmtNumber(s.p90), n: s.with_date })),
      h("div", { class: "table-wrap" }, h("table", {},
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

async function toggleMap() {
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

function drawMarkers(data) {
  const L = window.L;
  if (!L || !state.map || !data) return;
  state.mapLayer?.remove();
  state.mapLayer = L.layerGroup().addTo(state.map);
  const bounds = [];
  if (data.point) {
    L.circleMarker([data.point.lat, data.point.lon], { radius: 8, color: "#0b4f8a" }).addTo(state.mapLayer);
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

// ------------------------------------------------------------------ obserwowanie, udostępnianie

function updateWatchTarget() {
  const channel = new FormData($("#watch-form")).get("channel") || "ntfy";
  $("#watch-target-label").textContent = t(`watch.target_${channel}`);
  const input = $("#watch-target");
  input.type = channel === "email" ? "email" : "text";
  $("#watch-target-hint").textContent = channel === "email" ? "" : t(`watch.target_${channel}_hint`);
  if (channel === "ntfy" && !input.value) input.value = `termin-${crypto.getRandomValues(new Uint32Array(2)).join("")}`;
}

function openWatch(r, data) {
  state.watchFor = { r, data };
  $("#watch-title").textContent = t("watch.title", { label: data.label });
  $("#watch-result").textContent = "";
  $("#watch-target").value = "";
  $("#watch-channels").replaceChildren(...state.config.channels.map((c, i) =>
    h("label", {}, h("input", { type: "radio", name: "channel", value: c, checked: i === 0, onchange: updateWatchTarget }), " ", t(`watch.channel_${c}`))));
  updateWatchTarget();
  $("#watch-dialog").showModal();
}

async function submitWatch() {
  const { data } = state.watchFor;
  const form = new FormData($("#watch-form"));
  const p = state.lastParams;
  const payload = {
    q: p.get("q"),
    variant: data.variant === "wszystkie" ? null : data.variant,
    radius: Number(p.get("radius")) || null,
    case: Number(p.get("case")) || 1,
    channel: form.get("channel"),
    target: (form.get("target") || "").trim(),
    before: form.get("before") || null,
  };
  if (p.get("lat")) { payload.lat = Number(p.get("lat")); payload.lon = Number(p.get("lon")); }
  else payload.where = p.get("where");
  const out = $("#watch-result");
  try {
    const res = await fetch("/api/subscriptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const body = await res.json();
    out.textContent = res.ok ? t("watch.ok", { label: body.label, url: body.unsubscribe_url }) : (typeof body.detail === "string" ? body.detail : t("results.error"));
  } catch {
    out.textContent = t("results.error");
  }
}

async function share(data) {
  const url = location.href;
  const text = t("share.text", { label: data.label });
  if (navigator.share) {
    try { await navigator.share({ title: "Termin", text, url }); return; } catch { /* anulowano */ }
  }
  try { await navigator.clipboard.writeText(url); announce(t("share.copied")); }
  catch { prompt(text, url); }
}

// ------------------------------------------------------------------ ustawienia i start

function updateKmOutput() {
  const value = Number($("#kmday").value);
  $("#kmday-out").textContent = t("form.km_per_day_value", { km: value });
}

function setLargeText(on) {
  document.documentElement.classList.toggle("large-text", on);
  $("#large-text").setAttribute("aria-pressed", String(on));
  STORE.set("large", on);
}

function applyUrlParams() {
  const q = new URLSearchParams(location.search);
  if (!q.get("q")) return false;
  $("#co").value = q.get("q");
  if (q.get("gdzie")) $("#gdzie").value = q.get("gdzie");
  if (q.get("lat") && q.get("lon")) state.point = { lat: Number(q.get("lat")), lon: Number(q.get("lon")) };
  if (q.get("r")) $("#promien").value = q.get("r");
  const mode = document.querySelector(`input[name=mode][value="${q.get("tryb")}"]`);
  if (mode) mode.checked = true;
  const kase = document.querySelector(`input[name=case][value="${q.get("przypadek")}"]`);
  if (kase) kase.checked = true;
  if (q.get("km")) $("#kmday").value = q.get("km");
  if (["dorosli", "dzieci"].includes(q.get("wariant"))) state.variant = q.get("wariant");
  return true;
}

function bind() {
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); closeSuggestions(); runSearch(formParams()); });
  const co = $("#co");
  co.addEventListener("input", () => { state.variant = null; clearTimeout(state.suggestTimer); state.suggestTimer = setTimeout(suggest, 150); });
  co.addEventListener("keydown", (e) => {
    const list = $("#co-list");
    if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) suggest(); else moveSuggestion(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSuggestion(-1); }
    else if (e.key === "Escape") closeSuggestions();
    else if (e.key === "Enter" && !list.hidden && state.activeSuggestion >= 0) { e.preventDefault(); chooseSuggestion(list._items[state.activeSuggestion]); }
  });
  co.addEventListener("blur", () => setTimeout(closeSuggestions, 100));
  $("#gdzie").addEventListener("input", () => { state.point = null; $("#gps-status").textContent = ""; });
  $("#gps").addEventListener("click", () => {
    const status = $("#gps-status");
    if (!navigator.geolocation) { status.textContent = t("form.gps_error"); return; }
    status.textContent = t("form.gps_busy");
    navigator.geolocation.getCurrentPosition(
      (pos) => { state.point = { lat: pos.coords.latitude, lon: pos.coords.longitude }; $("#gdzie").value = ""; status.textContent = t("form.gps_ok"); },
      () => { status.textContent = t("form.gps_error"); },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 });
  });
  $("#kmday").addEventListener("input", updateKmOutput);
  $("#lang").addEventListener("change", (e) => { STORE.set("lang", e.target.value); loadLang(e.target.value); });
  $("#large-text").addEventListener("click", () => setLargeText(!document.documentElement.classList.contains("large-text")));
  $("#toggle-map").addEventListener("click", toggleMap);
  $("#show-stats").addEventListener("click", toggleStats);
  $("#watch-submit").addEventListener("click", submitWatch);
}

async function init() {
  setLargeText(Boolean(STORE.get("large", false)));
  bind();
  // Polski domyślnie; ukraiński podpowiadamy, jeśli przeglądarka jest po ukraińsku.
  const preferred = STORE.get("lang") || ((navigator.language || "").startsWith("uk") ? "uk" : "pl");
  const [config] = await Promise.all([fetch("/api/config").then((r) => r.json()), loadLang(LANGS.includes(preferred) ? preferred : "pl")]);
  state.config = config;
  $("#kmday").value = $("#kmday").value || config.km_per_day;
  updateKmOutput();
  if (applyUrlParams()) runSearch(formParams());
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

init();
