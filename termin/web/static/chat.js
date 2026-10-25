// Pomocnik „Jak to działa?”: małe okienko w rogu, ładowane leniwie z app.js (ADR-0014).
// Rozmowa żyje tylko w pamięci strony: nie zapisujemy jej w przeglądarce ani na serwerze.
import { $, h, t, state, api, errorText } from "./app.js";

const MAX_QUESTION = 500;
const history = [];     // [{ role: "user" | "assistant", content }]
let info = null;        // { enabled, ai } z /api/assistant
let noticeAccepted = false;
let busy = false;
let cssReady = null;

function loadCss() {
  cssReady ??= new Promise((resolve) => {
    const link = h("link", { rel: "stylesheet", href: "/static/chat.css" });
    link.addEventListener("load", resolve);
    link.addEventListener("error", resolve);
    document.head.append(link);
  });
  return cssReady;
}

function panel() {
  return $("#chat-panel");
}

function bubble(role, text, extra = "") {
  return h("div", { class: `chat-msg chat-${role}${extra}` },
    h("span", { class: "visually-hidden" }, role === "user" ? t("chat.you") : t("chat.helper")),
    h("p", {}, text));
}

function renderLog() {
  const log = $("#chat-log");
  log.replaceChildren(...history.map((m) => bubble(m.role, m.content)));
  if (busy) log.append(bubble("assistant", t("chat.thinking"), " chat-busy"));
  $("#chat-suggest").hidden = history.length > 0 || !canAsk();
  log.scrollTop = log.scrollHeight;
}

function canAsk() {
  return !info?.ai || noticeAccepted;
}

function renderGate() {
  const needNotice = Boolean(info?.ai) && !noticeAccepted;
  $("#chat-notice").hidden = !needNotice;
  $("#chat-form").hidden = needNotice;
  $("#chat-intro").textContent = info?.ai ? t("chat.intro_ai") : t("chat.intro_faq");
  renderLog();
}

async function ask(question) {
  question = question.trim().slice(0, MAX_QUESTION);
  if (!question || busy || !canAsk()) return;
  history.push({ role: "user", content: question });
  busy = true;
  renderLog();
  const res = await api("/api/assistant", { method: "POST", json: { messages: history.slice(-8), lang: state.lang } })
    .catch(() => ({ ok: false, body: null }));
  busy = false;
  if (res.ok && res.body?.reply) {
    history.push({ role: "assistant", content: res.body.reply });
    renderLog();
  } else {
    history.pop();
    renderLog();
    $("#chat-log").append(bubble("assistant", res.status === 429 || res.body ? errorText(res.body) : t("chat.error"), " chat-error"));
    $("#chat-input").value = question;
  }
  $("#chat-input").focus();
}

function build() {
  const input = h("input", { id: "chat-input", type: "text", maxlength: String(MAX_QUESTION), autocomplete: "off",
    placeholder: t("chat.placeholder"), "aria-label": t("chat.placeholder") });
  const form = h("form", { id: "chat-form", class: "chat-form" },
    input,
    h("button", { type: "submit", class: "btn btn-signal" }, t("chat.send")));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    ask(q);
  });
  const box = h("dialog", { id: "chat-panel", class: "chat-panel", "aria-labelledby": "chat-title" },
    h("div", { class: "chat-head" },
      h("h2", { id: "chat-title" }, t("chat.title")),
      h("button", { type: "button", class: "icon-close", "aria-label": t("common.close"), onclick: closeChat }, "×")),
    h("p", { id: "chat-intro", class: "chat-intro" }),
    h("div", { id: "chat-log", class: "chat-log", role: "log", "aria-live": "polite", tabindex: "0", "aria-label": t("chat.title") }),
    h("div", { id: "chat-suggest", class: "chat-suggest" }),
    h("div", { id: "chat-notice", class: "chat-notice", hidden: true },
      h("p", {}, t("chat.privacy_ai")),
      h("button", { type: "button", class: "btn btn-ink", onclick: () => { noticeAccepted = true; renderGate(); input.focus(); } }, t("chat.ok"))),
    form,
    h("p", { class: "chat-foot" }, t("chat.foot")));
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); closeChat(); } });
  document.body.append(box);
  return box;
}

// Podpowiedzi pytań: najpierw te, które pasują do tego, co widać w wynikach.
function suggestionKeys() {
  const results = state.last?.results;
  const keys = [];
  if (results && !results.length) keys.push("chat.s_none");
  if (results?.some((r) => r.in_cer)) keys.push("chat.s_cer");
  if (results?.some((r) => r.location_approx)) keys.push("chat.s_approx");
  return [...keys, "chat.s1", "chat.s2", "chat.s3"].slice(0, 3);
}

function renderSuggest() {
  $("#chat-suggest").replaceChildren(...suggestionKeys().map((key) =>
    h("button", { type: "button", class: "chip", onclick: () => ask(t(key)) }, t(key))));
}

function closeChat() {
  panel()?.close();
  $("#help-btn").setAttribute("aria-expanded", "false");
  $("#help-btn").focus();
}

export async function toggleChat() {
  if (panel()?.open) { closeChat(); return; }
  await loadCss();
  if (!info) {
    const res = await api("/api/assistant").catch(() => ({ ok: false }));
    info = res.ok ? res.body : { enabled: true, ai: false };
  }
  // Zmiana języka strony przebudowuje okienko (napisy są w nim stałe).
  if (panel() && panel().dataset.lang !== state.lang) panel().remove();
  const box = panel() || build();
  box.dataset.lang = state.lang;
  renderSuggest();
  renderGate();
  box.show();
  $("#help-btn").setAttribute("aria-expanded", "true");
  (canAsk() ? $("#chat-input") : box.querySelector("#chat-notice button")).focus();
}
