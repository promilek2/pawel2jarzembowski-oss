// Termin: frontend bez frameworka (ADR-0004). DOM budujemy przez textContent (dane NFZ to
// treść z zewnątrz), teksty pochodzą z /static/i18n/<język>.json.

const $ = (sel) => document.querySelector(sel);
// Konto, 2FA, „Moje sprawy” i powiadomienia ładujemy dopiero, gdy są potrzebne (budżet 150 KB).
const account = () => import("./account.js");
// Tryb „Dzwonię” i lista do druku: też dopiero na żądanie.
const extras = () => import("./extras.js");
const chat = () => import("./chat.js");
const insights = () => import("./insights.js");
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
  features: null,       // stan funkcji dla zalogowanych (/api/me/features)
  reportFor: null,
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
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    const text = lookup(el.dataset.i18nPlaceholder);
    if (typeof text === "string") el.setAttribute("placeholder", text);
  }
  for (const el of document.querySelectorAll("[data-i18n-label]")) {
    const text = lookup(el.dataset.i18nLabel);
    if (typeof text === "string") el.setAttribute("aria-label", text);
  }
  if (state.auth) updateAccountButton();
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
  if (data.get("accessible")) params.set("accessible", "true");
  return params;
}

async function runSearch(params, { auto = false } = {}) {
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
  const slow = setTimeout(() => announce(t("results.slow")), 4000); // limit NFZ: 10 zapytań / 10 s
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
    // Wyniki offline zapisujemy tylko za zgodą (zawierają wpisane świadczenie i miejsce).
    if (data.status === "ok" && STORE.get("consent") === "granted") {
      STORE.set("last", { params: params.toString(), data, savedAt: new Date().toISOString() });
    }
    renderResponse(data);
    // Fokus na wynikach: czytnik ekranu przeczyta nagłówek, a telefon przewinie do listy.
    // Przy wyszukiwaniu z udostępnionego linku (bez akcji użytkownika) nie przewijamy strony,
    // żeby nie było przeskoku układu (CLS).
    if (data.status === "ok" && !location.hash.includes("wynik=")) $("#results-heading").focus({ preventScroll: auto });
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
    clearTimeout(slow);
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
  for (const [k, name] of [["radius", "r"], ["mode", "tryb"], ["case", "przypadek"], ["variant", "wariant"], ["km_per_day", "km"], ["accessible", "dostepne"]]) {
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
  const note = $("#results-note");
  note.textContent = data.note || "";
  note.hidden = !data.note;

  const list = $("#result-list");
  list.replaceChildren(...data.results.map((r, i) => renderCard(r, i, data)));
  $("#stats").hidden = true;
  $("#show-stats").setAttribute("aria-expanded", "false");
  $("#show-stats").hidden = data.provinces.length === 0;
  if (state.map) insights().then((m) => m.drawMarkers(data));

  announce(data.results.length ? t("results.found", { n: data.total_found }) : t("results.none"));
  renderWiden(data);
  renderTip(data);
  const target = location.hash.match(/wynik=([\w-]+)/);
  if (target) document.getElementById(`r-${target[1]}`)?.scrollIntoView();
  if (state.auth?.user) account().then((m) => m.loadPersonal(data));
}

// „Pojedziesz 23 km dalej, a prognoza jest krótsza o 39 dni”: porównanie najbliższej
// placówki z najszybszą (bez pustych kolejek i placówek bez odległości).
function renderTip(data) {
  const box = $("#results-tip");
  const ok = data.results.filter((r) => r.days_until >= 0 && r.distance_km != null && !r.alerts.some((a) => a.code === "empty_queue"));
  const nearest = ok.reduce((a, b) => (b.distance_km < a.distance_km ? b : a), ok[0]);
  const fastest = ok.reduce((a, b) => (b.days_until < a.days_until || (b.days_until === a.days_until && b.distance_km < a.distance_km) ? b : a), ok[0]);
  const gain = nearest && fastest ? nearest.days_until - fastest.days_until : 0;
  box.hidden = gain < 7;
  if (box.hidden) { box.replaceChildren(); return; }
  const place = (fastest.locality || "").toLowerCase().replace(/(^|[\s-])\p{L}/gu, (m) => m.toUpperCase()) || fastest.provider || "";
  box.replaceChildren(
    t("results.tip", { km: fastest.distance_km, days: gain, place }), " ",
    h("button", { type: "button", class: "link-btn", onclick: () => { const el = document.getElementById(`r-${fastest.id}`); el?.scrollIntoView({ block: "start" }); el?.focus(); } }, t("results.tip_show")));
}

// Pusty albo skromny wynik: jednym dotknięciem większy promień albo całe województwo.
function renderWiden(data) {
  const box = $("#results-widen");
  const radius = Number(state.lastParams?.get("radius")) || 50;
  const few = data.results.length < 3 && data.status === "ok" && !data.note;
  box.hidden = !few;
  if (!few) { box.replaceChildren(); return; }
  const go = (patch) => () => {
    const params = new URLSearchParams(state.lastParams);
    for (const [k, v] of Object.entries(patch)) params.set(k, v);
    const radio = document.querySelector(`input[name=radius][value="${params.get("radius")}"]`);
    if (radio) radio.checked = true;
    runSearch(params);
  };
  const buttons = [100, 200, 300].filter((r) => r > radius).slice(0, 2)
    .map((r) => h("button", { type: "button", class: "chip", onclick: go({ radius: String(r) }) }, t("results.widen", { km: r })));
  box.replaceChildren(h("span", { class: "choices-label" }, t("results.widen_label")), ...buttons);
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

const SVG_NS = "http://www.w3.org/2000/svg";
const ICONS = {
  phone: "M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z",
  route: "M3 11l19-9-9 19-2-8-8-2z",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0",
  share: "M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8M16 6l-4-4-4 4M12 2v13",
  calendar: "M3 6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM16 2v4M8 2v4M3 10h18",
  check: "M20 6L9 17l-5-5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 11v6M9 14h6",
  note: "M4 4h16v12l-4 4H4zM16 20v-4h4M8 9h8M8 13h5",
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({ width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) svg.setAttribute(k, v);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

function fmtDayMonth(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Intl.DateTimeFormat(state.lang === "en" ? "en-GB" : state.lang, { day: "2-digit", month: "2-digit" })
    .format(new Date(Date.UTC(y, m - 1, d)));
}

function renderStub(r, index, top) {
  let date;
  if (r.forecast && r.pcus) {
    // Od 07.2026 NFZ podaje tylko prognozę (PCUŚ), np. „1 mies. 3 tyg.”, a nie wolny termin.
    date = h("p", { class: "ticket-date forecast" },
      h("span", { class: "visually-hidden" }, t("card.forecast_sr", { pcus: r.pcus, date: r.first_date })),
      h("span", { class: "dm", "aria-hidden": "true" }, `~${r.pcus}`),
      h("span", { class: "yr", "aria-hidden": "true" }, t("card.forecast_label", { date: r.first_date })));
  } else if (r.in_cer && !r.first_date) {
    date = h("p", { class: "ticket-date none" }, t("card.cer"));
  } else if (r.first_date) {
    // Czytnik ekranu dostaje pełną datę, wzrok: duże „03.11” jak numerek z automatu.
    date = h("p", { class: "ticket-date" },
      h("span", { class: "visually-hidden" }, `${fmtDate(r.first_date)}, ${whenText(r.days_until)}`),
      h("span", { class: "dm", "aria-hidden": "true" }, fmtDayMonth(r.first_date)),
      h("span", { class: "yr", "aria-hidden": "true" }, `${r.first_date.slice(0, 4)} · ${whenText(r.days_until)}`));
  } else {
    date = h("p", { class: "ticket-date none" }, t("card.no_date"));
  }
  let dist = null;
  if (r.travel_min != null || r.distance_km != null) {
    const main = r.distance_km != null ? `${r.location_approx ? "~" : ""}${t("card.km", { km: r.distance_km })}` : t("card.travel", { min: Math.round(r.travel_min) });
    const sub = r.distance_km != null && r.travel_min != null ? t("card.travel", { min: Math.round(r.travel_min) }) : t("card.from_you");
    dist = h("p", { class: "stub-dist" }, h("strong", {}, main), h("small", {}, sub));
  }
  return h("div", { class: "ticket-stub" },
    h("div", { class: "stub-main" }, h("span", { class: "rank" }, t("card.rank_short", { n: index + 1 })), date),
    dist);
}

function renderCard(r, index, data) {
  const top = index === 0 && r.first_date != null && r.days_until >= 0;
  const reasons = r.reasons.map((x) => t(`reasons.${x.code}`, x.params));
  const why = reasons.length ? h("p", { class: "why" }, h("strong", {}, t("card.why")), " ", capitalize(reasons.join(", "))) : null;
  const alerts = r.alerts.length ? h("ul", { class: "alerts" }, r.alerts.map((a) => h("li", {}, t(`alerts.${a.code}`, a.params)))) : null;

  const access = Object.entries(r.accessibility).filter(([, v]) => v).map(([k]) => t(`card.${k}`));
  const transport = r.transport ? h("p", { class: "transport" }, h("strong", {}, t("card.transport")), " ", capitalize(r.transport.toLowerCase())) : null;
  const tags = [];
  if (r.for_children) tags.push(t("card.children"));
  if (r.awaiting != null) tags.push(t("card.waiting", { n: r.awaiting }));
  if (r.average_period) tags.push(t("card.average", { n: r.average_period }));
  for (const a of access) tags.push(a);

  const tel = telHref(r.phone);
  const call = tel
    ? h("a", { class: `btn ${top ? "btn-signal" : "btn-ink"} call`, href: tel, onclick: () => { state.lastCall = { r, at: Date.now() }; } }, icon("phone"), `${t("card.call")}: ${r.phone}`)
    : null;
  // Na komputerze link tel: zwykle nic nie robi: dajemy „Kopiuj numer”.
  const copy = tel && matchMedia("(hover: hover) and (pointer: fine)").matches
    ? h("button", { type: "button", class: "btn btn-outline copy-phone", onclick: () => navigator.clipboard?.writeText(r.phone).then(() => announce(t("card.copied", { phone: r.phone })), () => {}) }, t("card.copy"))
    : null;
  const actions = h("div", { class: "actions" },
    h("a", { class: "btn btn-outline", href: directionsHref(r, data), rel: "noopener", target: "_blank" }, icon("route"), t("card.directions")),
    h("button", { type: "button", class: "btn btn-outline", onclick: () => openWatch(r, data) }, icon("bell"), t("card.watch")),
    h("button", { type: "button", class: "btn btn-outline", onclick: () => share(data) }, icon("share"), t("card.share")),
    h("a", { class: "btn btn-outline", href: `/api/ics/${encodeURIComponent(r.id)}`, download: "zadzwon.ics" }, icon("calendar"), t("card.reminder")),
  );

  const personal = h("div", { class: "personal" },
    h("p", { class: "community", id: `cm-${r.id}`, hidden: true }),
    h("p", { class: "my-note", id: `note-${r.id}`, hidden: true }),
    h("div", { class: "actions actions-personal" },
      h("button", { type: "button", class: "btn btn-outline", onclick: () => needLogin() || account().then((m) => m.openReport(r)) }, icon("check"), t("report.button")),
      h("button", { type: "button", class: "btn btn-outline", onclick: () => needLogin() || account().then((m) => m.addCaseFrom(r)) }, icon("folder"), t("cases.add_from")),
      h("button", { type: "button", class: "btn btn-outline", onclick: () => needLogin() || account().then((m) => m.editNote(r)) }, icon("note"), t("notes.button"))));

  const history = h("details", { class: "history", ontoggle: (e) => e.target.open && insights().then((m) => m.loadHistory(r.id, e.target)) },
    h("summary", {}, t("card.history")), h("div", {}));
  const script = h("details", { class: "script" }, h("summary", {}, t("card.script")),
    h("button", { type: "button", class: "btn btn-ink", onclick: () => extras().then((m) => m.openCallMode(r, data)) }, icon("phone"), t("call.button")),
    renderScript(r, data));

  return h("li", {},
    h("article", { class: `ticket${top ? " ticket--top" : ""}`, id: `r-${r.id}`, tabindex: "-1", "aria-label": `${t("card.rank", { n: index + 1 })}: ${r.provider || ""}` },
      renderStub(r, index, top),
      h("div", { class: "ticket-perf", "aria-hidden": "true" }),
      h("div", { class: "ticket-body" },
        h("h3", { class: "provider" }, r.provider || r.place || "—"),
        r.place && r.place !== r.benefit ? h("p", { class: "address" }, r.place) : null,
        h("p", { class: "address" }, [r.address, r.locality].filter(Boolean).join(", ")),
        r.as_at ? h("p", { class: "asat" }, t("card.as_at", { date: r.as_at })) : null,
        transport, why, alerts,
        tags.length ? h("ul", { class: "tags" }, tags.map((x) => h("li", {}, x))) : null,
        copy ? h("div", { class: "call-row" }, call, copy) : call, actions, personal, script, history)));
}

function capitalize(text) { return text ? text[0].toUpperCase() + text.slice(1) : text; }

function renderScript(r, data) {
  const urgent = data.case === 2 ? t("script.urgent") : "";
  return h("div", {},
    h("p", {}, t("script.intro")),
    h("ol", {},
      h("li", {}, t("script.hello", { benefit: r.benefit.toLowerCase(), urgent })),
      h("li", {}, r.forecast ? t("script.check_forecast", { pcus: r.pcus }) : r.first_date && r.days_until >= 0 ? t("script.check", { date: r.first_date }) : t("script.check_nodate")),
      h("li", {}, t("script.earlier")),
      h("li", {}, t("script.referral"))),
    h("p", {}, h("strong", {}, t("script.prepare"))),
    h("ul", {}, ["item_referral", "item_pesel", "item_docs", "item_pen"].map((k) => h("li", {}, t(`script.${k}`)))),
    h("p", { class: "hint" }, t("script.note")));
}

// ------------------------------------------------------------------ obserwowanie, udostępnianie

function updateWatchTarget() {
  const channel = new FormData($("#watch-form")).get("channel") || "ntfy";
  $("#watch-target-field").hidden = channel === "push";
  if (channel === "push") return;
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
  $("#watch-consent").checked = false;
  $("#watch-target").value = "";
  const channels = state.auth?.user && state.features?.push?.enabled ? [...state.config.channels, "push"] : state.config.channels;
  $("#watch-channels").replaceChildren(...channels.map((c, i) =>
    h("label", {}, h("input", { type: "radio", name: "channel", value: c, checked: i === 0, onchange: updateWatchTarget }), " ", t(`watch.channel_${c}`))));
  updateWatchTarget();
  $("#watch-dialog").showModal();
}

async function submitWatch() {
  const { data } = state.watchFor;
  const form = new FormData($("#watch-form"));
  const p = state.lastParams;
  const out = $("#watch-result");
  if (!$("#watch-consent").checked) {
    out.textContent = t("consent.required");
    $("#watch-consent").focus();
    return;
  }
  if (form.get("channel") === "push" && !(await (await account()).enablePush())) { out.textContent = $("#push-status").textContent || t("push.unsupported"); return; }
  const payload = {
    q: p.get("q"),
    variant: data.variant === "wszystkie" ? null : data.variant,
    radius: Number(p.get("radius")) || null,
    case: Number(p.get("case")) || 1,
    channel: form.get("channel"),
    target: form.get("channel") === "push" ? "push" : (form.get("target") || "").trim(),
    before: form.get("before") || null,
    consent: true,
  };
  if (p.get("lat")) { payload.lat = Number(p.get("lat")); payload.lon = Number(p.get("lon")); }
  else payload.where = p.get("where");
  try {
    const { ok, body } = await api("/api/subscriptions", { method: "POST", json: payload });
    out.textContent = ok ? t(body.channel === "push" ? "watch.ok_push" : "watch.ok", { label: body.label, url: body.unsubscribe_url }) : errorText(body);
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

// ------------------------------------------------------------------ API z ochroną CSRF

async function api(path, { method = "GET", json } = {}) {
  const headers = {};
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && state.auth?.csrf) headers["X-CSRF-Token"] = state.auth.csrf;
  const res = await fetch(path, { method, headers, body: json === undefined ? undefined : JSON.stringify(json), credentials: "same-origin" });
  let body = null;
  try { body = res.status === 204 ? null : await res.json(); } catch { body = null; }
  return { ok: res.ok, status: res.status, body };
}

function errorText(body) {
  if (body && typeof body.detail === "string") return body.detail;
  if (body && Array.isArray(body.detail)) return t("auth.invalid");
  return t("results.error");
}

// ------------------------------------------------------------------ konto

function updateAccountButton() {
  const user = state.auth?.user;
  $("#account-label").textContent = user ? t("auth.account_button") : t("auth.login_button");
  $("#account-btn").hidden = state.config ? !state.config.accounts_enabled : false;
}

async function loadMe() {
  try {
    const { body } = await api("/api/auth/me");
    state.auth = body;
  } catch {
    state.auth = { user: null, csrf: null };
  }
  updateAccountButton();
  await onAuthChange();
}

// Po zalogowaniu i wylogowaniu: stan funkcji osobistych i odświeżenie kart wyników.
async function onAuthChange() {
  state.features = null;
  if (state.auth?.user) {
    const r = await api("/api/me/features");
    if (r.ok) state.features = r.body;
    account().then((m) => m.syncPush());
  }
  if (state.last?.status === "ok") renderResults(state.last);
}

function closeOnButtons(dialog) {
  for (const b of dialog.querySelectorAll("[data-close]")) b.addEventListener("click", () => dialog.close());
}

const AUTH_VIEWS = {
  login: { title: "auth.title_login", submit: "auth.submit_login", email: true, password: "current-password", remember: true, forgot: true },
  register: { title: "auth.title_register", submit: "auth.submit_register", email: true, password: "new-password", terms: true },
  forgot: { title: "auth.title_forgot", submit: "auth.submit_forgot", email: true, intro: "auth.forgot_intro", alt: ["auth.have_code", "recover"] },
  recover: { title: "auth.title_recover", submit: "auth.submit_reset", email: true, password: "new-password", code: "auth.code_recovery", intro: "auth.recover_intro", alt: ["auth.send_link", "forgot"] },
  reset: { title: "auth.title_reset", submit: "auth.submit_reset", password: "new-password" },
  mfa: { title: "auth.title_mfa", submit: "auth.submit_mfa", code: "auth.code_mfa", intro: "auth.mfa_intro" },
};

function setAuthView(view, token = null) {
  const cfg = AUTH_VIEWS[view];
  const mail = Boolean(state.config?.mail_enabled);
  state.authView = view;
  state.resetToken = token ?? state.resetToken;
  $("#auth-title").textContent = t(cfg.title);
  $("#auth-submit").textContent = t(cfg.submit);
  $("#auth-intro").textContent = cfg.intro ? t(cfg.intro) : "";
  $("#auth-email-field").hidden = !cfg.email;
  $("#auth-password-field").hidden = !cfg.password;
  $("#auth-code-field").hidden = !cfg.code;
  $("#auth-remember-field").hidden = !cfg.remember;
  $("#auth-terms-field").hidden = !cfg.terms;
  $("#auth-forgot").hidden = !cfg.forgot;
  $("#auth-tabs").hidden = !(view === "login" || view === "register");
  $("#auth-back").hidden = view === "login" || view === "register";
  const alt = $("#auth-alt");
  alt.hidden = !cfg.alt || !mail;
  if (cfg.alt) { alt.textContent = t(cfg.alt[0]); alt.dataset.view = cfg.alt[1]; }
  const pw = $("#auth-password");
  pw.setAttribute("autocomplete", cfg.password || "off");
  pw.toggleAttribute("data-strength", cfg.password === "new-password");
  updateStrength(pw);
  $("#auth-password-label").textContent = cfg.password === "new-password" && view !== "register" ? t("account.new_password") : t("auth.password");
  $("#auth-password-hint").textContent = cfg.password === "new-password" ? t("auth.password_rules") : "";
  if (cfg.code) {
    $("#auth-code-label").textContent = t(cfg.code);
    $("#auth-code").setAttribute("inputmode", "text");
    $("#auth-code-hint").textContent = view === "mfa" ? t("auth.code_mfa_hint") : t("auth.code_recovery_hint");
  }
  for (const tab of document.querySelectorAll("#auth-tabs [role=tab]")) tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  $("#auth-error").hidden = true;
  $("#auth-status").textContent = "";
}

function focusAuth() {
  const cfg = AUTH_VIEWS[state.authView];
  (cfg.email ? $("#auth-email") : cfg.code ? $("#auth-code") : $("#auth-password")).focus();
}

function openAuth(view = "login", token = null) {
  setAuthView(view, token);
  if (!$("#auth-dialog").open) $("#auth-dialog").showModal();
  focusAuth();
}

function forgotView() { return state.config?.mail_enabled ? "forgot" : "recover"; }

async function submitAuth(event) {
  event.preventDefault();
  const view = state.authView;
  const email = $("#auth-email").value.trim();
  const password = $("#auth-password").value;
  const code = $("#auth-code").value.trim();
  const error = $("#auth-error");
  error.hidden = true;
  const post = (path, json) => api(path, { method: "POST", json });
  let res;
  if (view === "login") res = await post("/api/auth/login", { email, password, remember: $("#auth-remember").checked });
  else if (view === "register") {
    if (!$("#auth-terms").checked) { error.textContent = t("auth.terms_required"); error.hidden = false; return; }
    res = await post("/api/auth/register", { email, password, accept_terms: true });
  } else if (view === "forgot") res = await post("/api/auth/request-reset", { email });
  else if (view === "recover") res = await post("/api/auth/recover", { email, code, password });
  else if (view === "mfa") res = await post("/api/auth/login/2fa", { challenge: state.challenge, code });
  else res = await post("/api/auth/reset", { token: state.resetToken, password });

  if (!res.ok) {
    error.textContent = errorText(res.body); error.hidden = false;
    if (view === "mfa" && res.status === 401 && /wygas|expired/i.test(errorText(res.body))) setTimeout(() => openAuth("login"), 1500);
    return;
  }
  if (view === "forgot") { $("#auth-status").textContent = errorText(res.body); return; }
  $("#auth-password").value = "";
  $("#auth-code").value = "";
  if (res.body.mfa_required) {
    state.challenge = res.body.challenge;
    setAuthView("mfa");
    $("#auth-status").textContent = view === "recover" ? t("auth.password_saved_mfa") : "";
    focusAuth();
    return;
  }
  state.auth = res.body;
  state.challenge = null;
  updateAccountButton();
  $("#auth-dialog").close();
  onAuthChange();
  if (view === "register") { announce(t(state.config?.mail_enabled ? "auth.registered" : "auth.registered_nomail")); account().then((m) => m.showCodes(res.body.recovery_codes)); return; }
  const left = res.body.recovery_codes_left;
  announce(typeof left === "number" && left <= 3 ? t("auth.codes_low", { n: left }) : t(view === "recover" || view === "reset" ? "auth.password_reset_done" : "auth.logged_in"));
}

// ------------------------------------------------------------------ hasła: Caps Lock i siła

function passwordStrength(pw) {
  if (!pw) return 0;
  if (pw.length < 10) return 1;
  const kinds = [/[a-ząćęłńóśźż]/, /[A-ZĄĆĘŁŃÓŚŹŻ]/, /\d/, /[^\p{L}\d]/u].filter((r) => r.test(pw)).length;
  const words = pw.split(/[\s\-_.]+/).filter((w) => w.length >= 3).length;
  if (pw.length >= 16 || (pw.length >= 12 && (kinds >= 3 || words >= 3))) return 3;
  return 2;
}

function updateStrength(input) {
  const meter = input.closest(".field")?.querySelector(".strength");
  if (!meter) return;
  const show = input.hasAttribute("data-strength") && input.value.length > 0;
  meter.hidden = !show;
  if (!show) return;
  const level = passwordStrength(input.value);
  meter.dataset.level = String(level);
  meter.querySelector(".strength-label").textContent = t(`auth.strength_${level}`);
}

function setupPasswordHelpers() {
  for (const input of document.querySelectorAll('input[type="password"]')) {
    const field = input.closest(".field");
    if (!field) continue;
    const caps = h("p", { class: "caps-warning", hidden: true, "data-i18n": "auth.caps_lock" }, t("auth.caps_lock"));
    const meter = h("div", { class: "strength", hidden: true, "aria-live": "polite" },
      h("span", { class: "strength-bar", "aria-hidden": "true" }, h("span", {})), h("span", { class: "strength-label" }));
    field.append(meter, caps);
    const check = (e) => { if (e.getModifierState) caps.hidden = !e.getModifierState("CapsLock"); };
    input.addEventListener("keydown", check);
    input.addEventListener("keyup", check);
    input.addEventListener("blur", () => { caps.hidden = true; });
    input.addEventListener("input", () => updateStrength(input));
  }
}

async function handleAccountLinks() {
  const q = new URLSearchParams(location.search);
  const action = q.get("konto");
  const token = q.get("token");
  if (action === "panel") { history.replaceState(null, "", location.pathname); account().then((m) => m.openAccount()); return; }
  if (action === "sprawy") { history.replaceState(null, "", location.pathname); if (state.auth?.user) account().then((m) => m.openCases()); else openAuth("login"); return; }
  if (!action || !token) return;
  history.replaceState(null, "", location.pathname); // token nie zostaje w historii ani w linku
  if (action === "weryfikacja") {
    const r = await api("/api/auth/verify", { method: "POST", json: { token } });
    announce(r.ok ? t("auth.verified") : errorText(r.body));
    await loadMe();
  } else if (action === "reset") {
    openAuth("reset", token);
  }
}


// ------------------------------------------------------------------ funkcje dla zalogowanych

function needLogin() {
  if (state.auth?.user) return false;
  openAuth("login");
  $("#auth-status").textContent = t("personal.login_needed");
  return true;
}

// ------------------------------------------------------------------ zapisane wyszukiwania

// W tym urządzeniu (localStorage), tylko po kliknięciu „Zapisz wyszukiwanie” (strona /cookies).
const SAVED_MAX = 8;

function renderSaved() {
  const saved = STORE.get("saved", []);
  $("#saved-searches").hidden = !saved.length;
  $("#saved-list").replaceChildren(...saved.map((s, i) => h("span", { class: "saved-chip" },
    h("button", { type: "button", class: "chip", onclick: () => {
      history.replaceState(null, "", s.query);
      if (applyUrlParams()) runSearch(formParams());
    } }, s.label),
    h("button", { type: "button", class: "chip-remove", "aria-label": t("saved.remove", { label: s.label }), onclick: () => {
      STORE.set("saved", STORE.get("saved", []).filter((_, j) => j !== i));
      renderSaved();
    } }, "×"))));
}

function saveSearch() {
  if (!state.last || state.last.status !== "ok") return;
  const where = state.last.point?.label ? ` · ${state.last.point.label}` : "";
  const entry = { label: `${state.last.label}${where}`, query: location.search };
  const saved = STORE.get("saved", []).filter((s) => s.query !== entry.query);
  STORE.set("saved", [entry, ...saved].slice(0, SAVED_MAX));
  renderSaved();
  announce(t("saved.saved"));
}

// ------------------------------------------------------------------ po rozmowie: prośba o zgłoszenie

// Po powrocie z rozmowy (aplikacja znów widoczna) prosimy o krótkie zgłoszenie wyniku:
// tylko raz na połączenie i tylko gdy rozmowa mogła się odbyć (co najmniej 8 s poza aplikacją).
function onReturnFromCall() {
  const call = state.lastCall;
  if (document.visibilityState !== "visible" || !call || Date.now() - call.at < 8000) return;
  state.lastCall = null;
  const toast = $("#toast");
  toast.replaceChildren(
    h("p", {}, t("report.after_call", { provider: call.r.provider || "" })),
    h("div", { class: "toast-actions" },
      h("button", { type: "button", class: "btn btn-signal", onclick: () => { toast.hidden = true; if (!needLogin()) account().then((m) => m.openReport(call.r)); } }, t("report.button")),
      h("button", { type: "button", class: "btn btn-outline", onclick: () => { toast.hidden = true; } }, t("report.later"))));
  toast.hidden = false;
}

// ------------------------------------------------------------------ instalacja (PWA)

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function setupInstall() {
  const button = $("#install-btn");
  if (isStandalone()) return;
  if (isIos()) button.hidden = false; // Safari nie ma zdarzenia instalacji: pokazujemy instrukcję
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    button.hidden = false;
  });
  window.addEventListener("appinstalled", () => { button.hidden = true; state.installPrompt = null; announce(t("install.done")); });
  button.addEventListener("click", async () => {
    if (state.installPrompt) {
      state.installPrompt.prompt();
      const choice = await state.installPrompt.userChoice;
      if (choice.outcome === "accepted") button.hidden = true;
      state.installPrompt = null;
    } else {
      $("#install-dialog").showModal();
    }
  });
}

// ------------------------------------------------------------------ pamięć w urządzeniu (zgoda)

function setupStorageBanner() {
  const banner = $("#storage-banner");
  if (STORE.get("consent") === null) banner.hidden = false;
  const decide = (value) => {
    STORE.set("consent", value);
    if (value !== "granted") { try { localStorage.removeItem("termin:last"); } catch { /* */ } }
    banner.hidden = true;
  };
  $("#storage-accept").addEventListener("click", () => decide("granted"));
  $("#storage-deny").addEventListener("click", () => decide("denied"));
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

function setTheme(theme) {
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
  $("#theme").value = document.documentElement.dataset.theme || "auto";
  STORE.set("theme", document.documentElement.dataset.theme || null);
}

function applyUrlParams() {
  const q = new URLSearchParams(location.search);
  if (!q.get("q")) return false;
  $("#co").value = q.get("q");
  if (q.get("gdzie")) $("#gdzie").value = q.get("gdzie");
  if (q.get("lat") && q.get("lon")) state.point = { lat: Number(q.get("lat")), lon: Number(q.get("lon")) };
  const radius = document.querySelector(`input[name=radius][value="${q.get("r")}"]`);
  if (radius) radius.checked = true;
  const mode = document.querySelector(`input[name=mode][value="${q.get("tryb")}"]`);
  if (mode) mode.checked = true;
  const kase = document.querySelector(`input[name=case][value="${q.get("przypadek")}"]`);
  if (kase) kase.checked = true;
  if (q.get("km")) $("#kmday").value = q.get("km");
  if (["dorosli", "dzieci"].includes(q.get("wariant"))) state.variant = q.get("wariant");
  $("#accessible").checked = q.get("dostepne") === "true";
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
  for (const chip of document.querySelectorAll(".popular .chip")) {
    chip.addEventListener("click", () => {
      $("#co").value = chip.dataset.q;
      state.variant = null;
      if ($("#gdzie").value.trim() || state.point) runSearch(formParams());
      else $("#gdzie").focus();
    });
  }
  $("#lang").addEventListener("change", (e) => { STORE.set("lang", e.target.value); loadLang(e.target.value); });
  $("#theme").addEventListener("change", (e) => setTheme(e.target.value));
  $("#large-text").addEventListener("click", () => setLargeText(!document.documentElement.classList.contains("large-text")));
  $("#toggle-map").addEventListener("click", () => insights().then((m) => m.toggleMap()));
  $("#show-stats").addEventListener("click", () => insights().then((m) => m.toggleStats()));
  $("#watch-submit").addEventListener("click", submitWatch);
  for (const d of ["#auth-dialog", "#account-dialog", "#install-dialog"]) closeOnButtons($(d));
  $("#account-btn").addEventListener("click", () => (state.auth?.user ? account().then((m) => m.openAccount()) : openAuth("login")));
  $("#auth-form").addEventListener("submit", submitAuth);
  for (const tab of document.querySelectorAll("#auth-tabs [role=tab]")) tab.addEventListener("click", () => setAuthView(tab.dataset.view));
  $("#auth-forgot").addEventListener("click", () => { setAuthView(forgotView()); focusAuth(); });
  $("#auth-alt").addEventListener("click", () => { setAuthView($("#auth-alt").dataset.view); focusAuth(); });
  $("#auth-back").addEventListener("click", () => { state.challenge = null; setAuthView("login"); focusAuth(); });
  setupPasswordHelpers();
  $("#auth-show").addEventListener("click", () => {
    const input = $("#auth-password");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("#auth-show").setAttribute("aria-pressed", String(show));
  });
  document.addEventListener("visibilitychange", onReturnFromCall);
  $("#save-search").addEventListener("click", saveSearch);
  $("#print-list").addEventListener("click", () => extras().then((m) => m.printList(state.last)));
  $("#send-list").addEventListener("click", () => extras().then((m) => m.sendList(state.last)));
  renderSaved();
  setupInstall();
  setupStorageBanner();
}

async function init() {
  setLargeText(Boolean(STORE.get("large", false)));
  bind();
  setTheme(STORE.get("theme"));
  // Polski domyślnie; ukraiński podpowiadamy, jeśli przeglądarka jest po ukraińsku.
  const preferred = STORE.get("lang") || ((navigator.language || "").startsWith("uk") ? "uk" : "pl");
  const [config] = await Promise.all([fetch("/api/config").then((r) => r.json()), loadLang(LANGS.includes(preferred) ? preferred : "pl")]);
  state.config = config;
  await loadMe();
  handleAccountLinks();
  $("#kmday").value = $("#kmday").value || config.km_per_day;
  updateKmOutput();
  if (config.assistant?.enabled) {
    $("#help-btn").hidden = false;
    $("#help-btn").addEventListener("click", () => chat().then((m) => m.toggleChat()));
    if (new URLSearchParams(location.search).has("pomoc")) chat().then((m) => m.toggleChat());
  }
  if (applyUrlParams()) runSearch(formParams(), { auto: true });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

init();

export { $, h, t, state, fmtNumber, plural, api, errorText, announce, icon, telHref, closeOnButtons, isIos, isStandalone, updateAccountButton, openAuth, onAuthChange, needLogin, account, fmtDate, renderScript };
