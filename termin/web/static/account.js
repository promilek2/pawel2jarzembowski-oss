// Konto i funkcje dla zalogowanych: ładowane leniwie z app.js (nie wchodzą w pierwszy pakiet).
import { $, h, t, state, api, errorText, announce, icon, telHref, closeOnButtons, isIos, isStandalone, updateAccountButton, openAuth, onAuthChange } from "./app.js";

// ------------------------------------------------------------------ kody odzyskiwania

function showCodes(codes) {
  if (!codes?.length) return;
  state.codes = codes;
  $("#codes-list").replaceChildren(...codes.map((c) => h("li", {}, h("code", {}, c))));
  $("#codes-status").textContent = "";
  $("#codes-dialog").showModal();
}

function codesText() {
  return [t("codes.file_header", { email: state.auth?.user?.email || "" }), "", ...state.codes, "", t("codes.file_footer")].join("\n");
}

async function copyCodes() {
  try { await navigator.clipboard.writeText(codesText()); $("#codes-status").textContent = t("codes.copied"); }
  catch { $("#codes-status").textContent = t("codes.copy_failed"); }
}

function downloadCodes() {
  const url = URL.createObjectURL(new Blob([codesText()], { type: "text/plain;charset=utf-8" }));
  const a = h("a", { href: url, download: "termin-kody-odzyskiwania.txt" });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ------------------------------------------------------------------ ustawienia konta

async function openAccount() {
  if (!state.auth?.user) { openAuth("login"); return; }
  const user = state.auth.user;
  $("#account-email").textContent = user.email;
  const verify = $("#account-verify");
  verify.replaceChildren();
  if (!user.verified && state.config?.mail_enabled) {
    verify.append(t("account.unverified"), " ",
      h("button", { type: "button", class: "link-btn", onclick: async () => {
        const r = await api("/api/auth/resend-verification", { method: "POST" });
        $("#account-status").textContent = r.ok ? t("account.verification_sent") : errorText(r.body);
      } }, t("account.resend")));
  }
  $("#account-status").textContent = "";
  await Promise.all([renderAccountSubs(), renderSecurity(), renderSessions(), renderPush()]);
  $("#account-dialog").showModal();
}

async function renderSecurity() {
  const { ok, body } = await api("/api/account/security");
  if (!ok) return;
  const on = body.two_factor;
  $("#security-2fa").textContent = on ? t("account.twofa_status_on") : t("account.twofa_status_off");
  $("#security-2fa").dataset.on = String(on);
  const left = body.recovery_codes_left;
  $("#security-codes").textContent = t("account.codes_left", { n: left });
  $("#security-codes").dataset.on = String(left > 3);
  $("#twofa-off").hidden = on;
  $("#twofa-disable").hidden = !on;
  $("#twofa-setup").hidden = true;
  $("#twofa-start").hidden = false;
}

async function renderSessions() {
  const list = $("#account-sessions");
  const { ok, body } = await api("/api/account/sessions");
  if (!ok) { list.replaceChildren(h("li", {}, errorText(body))); return; }
  const when = (iso) => new Date(iso).toLocaleString(state.lang, { dateStyle: "medium", timeStyle: "short" });
  list.replaceChildren(...body.map((s) => h("li", {},
    h("span", {}, s.device || t("account.unknown_device"),
      h("small", {}, s.current ? t("account.this_device") : t("account.last_seen", { when: when(s.last_seen_at) }))),
    s.current ? null : h("button", { type: "button", class: "btn btn-outline", onclick: async () => {
      const r = await api(`/api/account/sessions/${encodeURIComponent(s.id)}`, { method: "DELETE" });
      $("#account-status").textContent = r.ok ? t("account.session_ended") : errorText(r.body);
      renderSessions();
    } }, t("account.logout")))));
}

function drawQr(canvas, rows) {
  const ctx = canvas.getContext("2d");
  const n = rows.length + 8; // 4 moduły marginesu z każdej strony
  const scale = Math.floor(canvas.width / n);
  const offset = Math.floor((canvas.width - scale * rows.length) / 2);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#000000";
  rows.forEach((row, y) => { for (let x = 0; x < row.length; x++) if (row[x] === "1") ctx.fillRect(offset + x * scale, offset + y * scale, scale, scale); });
}

async function startTwoFactor(e) {
  e.preventDefault();
  const r = await api("/api/account/2fa/setup", { method: "POST", json: { password: $("#twofa-password").value } });
  if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
  e.target.reset();
  $("#twofa-start").hidden = true;
  $("#twofa-setup").hidden = false;
  drawQr($("#twofa-qr"), r.body.qr);
  $("#twofa-link").href = r.body.uri;
  $("#twofa-secret").textContent = r.body.secret;
  $("#twofa-code").focus();
}

async function confirmTwoFactor(e) {
  e.preventDefault();
  const r = await api("/api/account/2fa/enable", { method: "POST", json: { code: $("#twofa-code").value.trim() } });
  if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
  e.target.reset();
  state.auth.user.two_factor = true;
  $("#account-status").textContent = t("account.twofa_enabled");
  await renderSecurity();
  showCodes(r.body.recovery_codes);
}

async function disableTwoFactor(e) {
  e.preventDefault();
  const r = await api("/api/account/2fa/disable", { method: "POST", json: { password: $("#twofa-off-password").value, code: $("#twofa-off-code").value.trim() } });
  if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
  e.target.reset();
  state.auth.user.two_factor = false;
  $("#account-status").textContent = t("account.twofa_disabled");
  renderSecurity();
}

async function regenerateCodes(e) {
  e.preventDefault();
  const r = await api("/api/account/recovery-codes", { method: "POST", json: { password: $("#codes-password").value } });
  if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
  e.target.reset();
  renderSecurity();
  showCodes(r.body.recovery_codes);
}

async function changeEmail(e) {
  e.preventDefault();
  const r = await api("/api/account/email", { method: "POST", json: { email: $("#email-new").value.trim(), password: $("#email-password").value } });
  if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
  e.target.reset();
  state.auth = { ...state.auth, user: r.body.user };
  $("#account-email").textContent = r.body.user.email;
  $("#account-status").textContent = r.body.verification_sent ? t("account.email_changed_verify") : t("account.email_changed");
}

async function renderAccountSubs() {
  const list = $("#account-subs");
  const { ok, body } = await api("/api/account/subscriptions");
  if (!ok) { list.replaceChildren(h("li", {}, errorText(body))); return; }
  if (!body.length) { list.replaceChildren(h("li", {}, t("account.no_subscriptions"))); return; }
  list.replaceChildren(...body.map((sub) => h("li", {},
    h("span", {}, sub.label, h("small", {}, sub.area_label || "")),
    h("button", { type: "button", class: "btn btn-outline", onclick: async () => {
      const r = await api(`/api/account/subscriptions/${encodeURIComponent(sub.token)}`, { method: "DELETE" });
      $("#account-status").textContent = r.ok ? t("account.subscription_removed") : errorText(r.body);
      renderAccountSubs();
    } }, t("account.remove")))));
}

async function logout(all = false) {
  await api(all ? "/api/account/logout-all" : "/api/auth/logout", { method: "POST" });
  state.auth = { user: null, csrf: null };
  updateAccountButton();
  $("#account-dialog").close();
  announce(t("auth.logged_out"));
  onAuthChange();
}

// Zapis wymaga zgody na dane o zdrowiu: 428 → okno zgody → ponowienie.
function askHealthConsent() {
  return new Promise((resolve) => {
    const dialog = $("#health-dialog");
    $("#health-check").checked = false;
    $("#health-error").hidden = true;
    const done = (ok) => { dialog.removeEventListener("close", onClose); resolve(ok); };
    const onClose = () => done(false);
    dialog.addEventListener("close", onClose);
    $("#health-accept").onclick = async () => {
      if (!$("#health-check").checked) { $("#health-error").textContent = t("health.required"); $("#health-error").hidden = false; return; }
      const r = await api("/api/me/health-consent", { method: "POST", json: { consent: true } });
      if (!r.ok) { $("#health-error").textContent = errorText(r.body); $("#health-error").hidden = false; return; }
      if (state.features) state.features.health_consent = true;
      dialog.removeEventListener("close", onClose);
      dialog.close();
      resolve(true);
    };
    dialog.showModal();
  });
}

async function personalApi(path, opts = {}) {
  let res = await api(path, opts);
  if (res.status === 428 && await askHealthConsent()) res = await api(path, opts);
  return res;
}

async function loadPersonal(data) {
  if (!state.auth?.user || !data.results?.length) return;
  const ids = data.results.map((r) => r.id).join(",");
  const [summary, notes] = await Promise.all([api(`/api/reports/summary?ids=${encodeURIComponent(ids)}`), api(`/api/notes?ids=${encodeURIComponent(ids)}`)]);
  if (summary.ok) for (const [id, s] of Object.entries(summary.body)) showCommunity(id, s);
  if (notes.ok) for (const [id, text] of Object.entries(notes.body)) showNote(id, text);
}

function communityText(s) {
  const parts = [t("report.summary_head", { n: s.reports, days: s.days })];
  if (s.same) parts.push(t("report.summary_same", { n: s.same }));
  if (s.other) parts.push(t("report.summary_other", { n: s.other }));
  if (s.none) parts.push(t("report.summary_none", { n: s.none }));
  if (s.not_reached) parts.push(t("report.summary_not_reached", { n: s.not_reached }));
  let text = parts.join(" · ");
  if (s.median_delta_days) text += `. ${t(s.median_delta_days > 0 ? "report.summary_later" : "report.summary_earlier", { n: Math.abs(s.median_delta_days) })}`;
  return text;
}

function showCommunity(id, s) {
  const el = document.getElementById(`cm-${id}`);
  if (!el || !s) return;
  el.textContent = communityText(s);
  el.hidden = false;
}

function showNote(id, text) {
  const el = document.getElementById(`note-${id}`);
  if (!el) return;
  el.replaceChildren(...(text ? [h("strong", {}, t("notes.mine")), " ", text] : []));
  el.hidden = !text;
}

function openReport(r) {
  state.reportFor = r;
  const form = $("#report-form");
  form.reset();
  $("#report-provider").textContent = [r.provider, r.locality].filter(Boolean).join(", ");
  $("#report-same").textContent = r.first_date ? t("report.outcome_same", { date: r.first_date }) : t("report.outcome_same_nodate");
  $("#report-outcome").hidden = true;
  $("#report-date-field").hidden = true;
  $("#report-case-field").hidden = true;
  $("#report-error").hidden = true;
  $("#report-dialog").showModal();
}

function updateReportForm() {
  const data = new FormData($("#report-form"));
  const reached = data.get("reached") === "yes";
  const outcome = data.get("outcome");
  $("#report-outcome").hidden = !reached;
  $("#report-date-field").hidden = !reached || outcome !== "other";
  $("#report-case-field").hidden = !reached || !(outcome === "same" || outcome === "other");
}

async function submitReport(e) {
  e.preventDefault();
  const r = state.reportFor;
  const data = new FormData(e.target);
  const error = $("#report-error");
  const reached = data.get("reached");
  const outcome = data.get("outcome");
  const fail = (msg) => { error.textContent = msg; error.hidden = false; };
  if (!reached) return fail(t("report.need_reached"));
  if (reached === "yes" && !outcome) return fail(t("report.need_outcome"));
  const offered = outcome === "other" ? $("#report-date").value : null;
  if (outcome === "other" && !offered) return fail(t("report.need_date"));
  const res = await personalApi("/api/reports", { method: "POST", json: {
    queue_id: r.id, reached: reached === "yes", outcome: reached === "yes" ? outcome : null,
    nfz_date: r.first_date, offered_date: offered,
  } });
  if (!res.ok) return fail(errorText(res.body));
  showCommunity(r.id, res.body.summary);
  if ($("#report-case").checked) {
    await createCase(r, { status: "booked", visit_date: offered || r.first_date });
  }
  $("#report-dialog").close();
  announce(t("report.thanks"));
}

function caseFrom(r) {
  return {
    label: state.last?.label || r.benefit,
    queue_id: r.id,
    provider: r.provider,
    address: [r.address, r.locality].filter(Boolean).join(", "),
    phone: r.phone,
  };
}

async function createCase(r, extra = {}) {
  const res = await personalApi("/api/cases", { method: "POST", json: { ...caseFrom(r), ...extra } });
  if (!res.ok) { announce(errorText(res.body)); return null; }
  return res.body;
}

async function addCaseFrom(r) {
  if (await createCase(r)) announce(t("cases.added"));
}

async function editNote(r) {
  const el = document.getElementById(`note-${r.id}`);
  const box = el.parentElement;
  if (box.querySelector(".note-editor")) return;
  const current = el.hidden ? "" : el.textContent.replace(t("notes.mine"), "").trim();
  const area = h("textarea", { id: `note-edit-${r.id}`, maxlength: "1000", rows: "3" });
  area.value = current;
  const editor = h("div", { class: "note-editor field" },
    h("label", { for: `note-edit-${r.id}` }, t("notes.label")), area,
    h("p", { class: "hint" }, t("notes.private")),
    h("div", { class: "account-actions" },
      h("button", { type: "button", class: "btn btn-ink", onclick: async () => {
        const res = await personalApi(`/api/notes/${encodeURIComponent(r.id)}`, { method: "PUT", json: { note: area.value } });
        if (!res.ok) { announce(errorText(res.body)); return; }
        showNote(r.id, res.body.note);
        editor.remove();
        announce(t(res.body.note ? "notes.saved" : "notes.deleted"));
      } }, t("notes.save")),
      h("button", { type: "button", class: "btn btn-outline", onclick: () => editor.remove() }, t("watch.cancel"))));
  el.after(editor);
  area.focus();
}

// ------------------------------------------------------------------ „Moje sprawy”

const CASE_STATUSES = ["to_call", "waiting", "booked", "done", "rejected"];

async function openCases() {
  $("#account-dialog").close();
  $("#cases-status").textContent = "";
  await renderCases();
  $("#cases-dialog").showModal();
}

async function patchCase(id, json) {
  const res = await personalApi(`/api/cases/${id}`, { method: "PATCH", json });
  $("#cases-status").textContent = res.ok ? t("cases.saved") : errorText(res.body);
  return res.ok;
}

async function renderCases() {
  const list = $("#cases-list");
  const { ok, body } = await api("/api/cases");
  if (!ok) { list.replaceChildren(h("li", {}, errorText(body))); return; }
  if (!body.length) { list.replaceChildren(h("li", { class: "empty" }, t("cases.empty"))); return; }
  list.replaceChildren(...body.map((c) => {
    const sid = `case-status-${c.id}`, did = `case-date-${c.id}`, nid = `case-note-${c.id}`;
    const status = h("select", { id: sid, onchange: (e) => patchCase(c.id, { status: e.target.value }).then(renderCases) },
      CASE_STATUSES.map((st) => h("option", { value: st, selected: st === c.status }, t(`cases.status_${st}`))));
    const date = h("input", { id: did, type: "date", value: c.visit_date || "", onchange: (e) =>
      patchCase(c.id, e.target.value ? { visit_date: e.target.value } : { clear_visit_date: true }).then(renderCases) });
    const note = h("textarea", { id: nid, rows: "2", maxlength: "1000", onchange: (e) => patchCase(c.id, { note: e.target.value }) });
    note.value = c.note || "";
    const tel = telHref(c.phone);
    return h("li", { class: `case case--${c.status}` },
      h("div", { class: "case-head" },
        h("strong", {}, c.label),
        c.visit_date ? h("span", { class: "case-date" }, t("cases.visit_on", { date: c.visit_date })) : null),
      c.provider || c.address ? h("p", { class: "hint" }, [c.provider, c.address].filter(Boolean).join(" · ")) : null,
      h("div", { class: "case-fields" },
        h("div", { class: "field" }, h("label", { for: sid }, t("cases.status")), status),
        h("div", { class: "field" }, h("label", { for: did }, t("cases.visit_date")), date)),
      h("div", { class: "field" }, h("label", { for: nid }, t("cases.note")), note),
      h("div", { class: "account-actions" },
        tel ? h("a", { class: "btn btn-ink", href: tel }, icon("phone"), t("card.call")) : null,
        c.visit_date ? h("a", { class: "btn btn-outline", href: `/api/cases/${c.id}/ics`, download: "wizyta.ics" }, icon("calendar"), t("cases.calendar")) : null,
        h("button", { type: "button", class: "btn btn-outline", onclick: async () => {
          if (!confirm(t("cases.confirm_delete", { label: c.label }))) return;
          const r = await api(`/api/cases/${c.id}`, { method: "DELETE" });
          $("#cases-status").textContent = r.ok ? t("cases.deleted") : errorText(r.body);
          renderCases();
        } }, t("account.remove"))));
  }));
}

async function addCase(e) {
  e.preventDefault();
  const input = $("#case-new-label");
  const label = input.value.trim();
  if (!label) { input.focus(); return; }
  const res = await personalApi("/api/cases", { method: "POST", json: { label } });
  $("#cases-status").textContent = res.ok ? t("cases.added") : errorText(res.body);
  if (res.ok) { input.value = ""; renderCases(); }
}

async function withdrawHealthConsent() {
  if (!confirm(t("health.withdraw_confirm"))) return;
  const r = await api("/api/me/health-consent", { method: "POST", json: { consent: false } });
  $("#cases-status").textContent = r.ok ? t("health.withdrawn") : errorText(r.body);
  if (r.ok && state.features) state.features.health_consent = false;
  renderCases();
}

// ------------------------------------------------------------------ powiadomienia Web Push

function pushSupported() { return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window; }

function keyBytes(b64) {
  const raw = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function sameKey(sub, b64) {
  const current = sub?.options?.applicationServerKey;
  if (!current) return true;
  const a = new Uint8Array(current), b = keyBytes(b64);
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

async function subscribePush(reg) {
  const key = state.features.push.public_key;
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub, key)) { await sub.unsubscribe(); sub = null; } // nowy klucz serwera
  sub = sub || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  const r = await api("/api/push/subscribe", { method: "POST", json: sub.toJSON() });
  if (r.ok) state.features.push.devices = r.body.devices;
  return r.ok;
}

async function enablePush() {
  const status = $("#push-status");
  if (!state.features?.push?.enabled) { status.textContent = t("push.disabled_server"); return false; }
  if (!pushSupported()) { status.textContent = isIos() && !isStandalone() ? t("push.ios_install") : t("push.unsupported"); return false; }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") { status.textContent = t("push.denied"); return false; }
  try {
    const ok = await subscribePush(await navigator.serviceWorker.ready);
    if (!ok) status.textContent = t("push.failed");
    return ok;
  } catch {
    status.textContent = t("push.failed");
    return false;
  }
}

// Po zmianie klucza serwera (np. nowa baza) odnawiamy subskrypcję po cichu, bez pytania.
async function syncPush() {
  if (!state.features?.push?.enabled || !pushSupported() || Notification.permission !== "granted") return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && (!sameKey(sub, state.features.push.public_key) || !state.features.push.devices)) await subscribePush(reg);
  } catch { /* bez powiadomień aplikacja działa dalej */ }
}

async function renderPush() {
  const status = $("#push-status");
  const on = $("#push-enable"), off = $("#push-disable"), test = $("#push-test");
  let subscribed = false;
  if (pushSupported() && state.features?.push?.enabled) {
    try { subscribed = Boolean(await (await navigator.serviceWorker.ready).pushManager.getSubscription()) && Notification.permission === "granted"; } catch { subscribed = false; }
  }
  on.hidden = subscribed; off.hidden = !subscribed; test.hidden = !subscribed;
  if (!state.features?.push?.enabled) status.textContent = t("push.disabled_server");
  else if (!pushSupported()) status.textContent = isIos() && !isStandalone() ? t("push.ios_install") : t("push.unsupported");
  else status.textContent = subscribed ? t("push.on", { n: state.features.push.devices || 1 }) : t("push.off");
}

async function disablePush() {
  try {
    const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
    if (sub) {
      const r = await api("/api/push/unsubscribe", { method: "POST", json: { endpoint: sub.endpoint } });
      if (r.ok) state.features.push.devices = r.body.devices;
      await sub.unsubscribe();
    }
  } catch { /* nic */ }
  renderPush();
}

async function testPush() {
  const r = await api("/api/push/test", { method: "POST" });
  $("#push-status").textContent = r.ok ? t("push.test_sent") : errorText(r.body);
}

// ------------------------------------------------------------------ podpięcie zdarzeń (raz, przy pierwszym imporcie)

function bindAccount() {
  closeOnButtons($("#codes-dialog"));
  $("#codes-copy").addEventListener("click", copyCodes);
  $("#codes-download").addEventListener("click", downloadCodes);
  $("#twofa-start").addEventListener("submit", startTwoFactor);
  $("#twofa-confirm").addEventListener("submit", confirmTwoFactor);
  $("#twofa-disable").addEventListener("submit", disableTwoFactor);
  $("#codes-form").addEventListener("submit", regenerateCodes);
  $("#email-form").addEventListener("submit", changeEmail);
  for (const d of ["#report-dialog", "#health-dialog", "#cases-dialog"]) closeOnButtons($(d));
  $("#report-form").addEventListener("change", updateReportForm);
  $("#report-form").addEventListener("submit", submitReport);
  $("#account-cases").addEventListener("click", openCases);
  $("#case-new").addEventListener("submit", addCase);
  $("#health-withdraw").addEventListener("click", withdrawHealthConsent);
  $("#push-enable").addEventListener("click", async () => { await enablePush(); renderPush(); });
  $("#push-disable").addEventListener("click", disablePush);
  $("#push-test").addEventListener("click", testPush);
  $("#account-logout").addEventListener("click", () => logout(false));
  $("#account-logout-all").addEventListener("click", () => logout(true));
  $("#password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await api("/api/account/password", { method: "POST", json: { current: $("#pw-current").value, new: $("#pw-new").value } });
    if (r.ok) { state.auth = r.body; e.target.reset(); }
    $("#account-status").textContent = r.ok ? t("account.password_changed") : errorText(r.body);
  });
  $("#delete-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await api("/api/account/delete", { method: "POST", json: { password: $("#delete-password").value } });
    if (!r.ok) { $("#account-status").textContent = errorText(r.body); return; }
    state.auth = { user: null, csrf: null };
    updateAccountButton();
    $("#account-dialog").close();
    announce(t("account.deleted"));
  });
}

bindAccount();

export { showCodes, openAccount, openCases, loadPersonal, openReport, addCaseFrom, editNote, enablePush, syncPush };
