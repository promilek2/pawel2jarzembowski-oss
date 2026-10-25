// Tryb „Dzwonię” i lista do druku: ładowane leniwie z app.js (nie wchodzą w pierwszy pakiet).
import { $, h, t, icon, telHref, needLogin, account, fmtDate, renderScript, announce } from "./app.js";

let cssReady = null;

// Style tego modułu (duże litery trybu rozmowy, arkusz do druku) ładujemy razem z nim.
function loadCss() {
  cssReady ??= new Promise((resolve) => {
    const link = h("link", { rel: "stylesheet", href: "/static/extras.css" });
    link.addEventListener("load", resolve);
    link.addEventListener("error", resolve);
    document.head.append(link);
  });
  return cssReady;
}

function whenLine(r) {
  if (r.forecast && r.pcus) return t("print.forecast", { pcus: r.pcus });
  if (r.first_date) return fmtDate(r.first_date);
  return r.in_cer ? t("card.cer") : t("card.no_date");
}

// ------------------------------------------------------------------ tryb „Dzwonię”

export async function openCallMode(r, data) {
  await loadCss();
  let dialog = $("#call-dialog");
  if (!dialog) {
    dialog = h("dialog", { id: "call-dialog", "aria-labelledby": "call-title" });
    document.body.append(dialog);
  }
  const tel = telHref(r.phone);
  const after = (fn) => () => { dialog.close(); if (!needLogin()) account().then(fn); };
  dialog.replaceChildren(h("div", { class: "dialog-body call-body" },
    h("div", { class: "dialog-head" },
      h("h2", { id: "call-title" }, r.provider || r.place || "—"),
      h("button", { type: "button", class: "icon-close", "aria-label": t("common.close"), onclick: () => dialog.close() }, "×")),
    h("p", { class: "call-address" }, [r.address, r.locality].filter(Boolean).join(", ")),
    h("p", { class: "call-when" }, whenLine(r)),
    tel ? h("a", { class: "btn btn-signal btn-big call-number", href: tel }, icon("phone"), r.phone) : h("p", { class: "error" }, t("alerts.no_phone")),
    h("section", { "aria-labelledby": "call-script-title" },
      h("h3", { id: "call-script-title" }, t("call.say")),
      renderScript(r, data)),
    h("section", { class: "call-after", "aria-labelledby": "call-after-title" },
      h("h3", { id: "call-after-title" }, t("call.after")),
      h("div", { class: "account-actions" },
        h("button", { type: "button", class: "btn btn-ink", onclick: after((m) => m.openReport(r)) }, t("report.button")),
        h("button", { type: "button", class: "btn btn-outline", onclick: after((m) => m.addCaseFrom(r)) }, t("cases.add_from")),
        h("button", { type: "button", class: "btn btn-outline", onclick: () => dialog.close() }, t("common.close"))))));
  dialog.showModal();
  dialog.querySelector(".call-number")?.focus();
}

// ------------------------------------------------------------------ lista do druku

export async function printList(data) {
  if (!data?.results?.length) { announce(t("results.none")); return; }
  await loadCss();
  let sheet = $("#print-sheet");
  if (!sheet) {
    sheet = h("section", { id: "print-sheet", "aria-hidden": "true" });
    document.body.append(sheet);
  }
  const rows = data.results.slice(0, 12).map((r, i) => h("tr", {},
    h("td", {}, String(i + 1)),
    h("td", {}, h("strong", {}, r.provider || r.place || "—"), h("br", {}), [r.address, r.locality].filter(Boolean).join(", "),
      r.distance_km != null ? ` (${r.location_approx ? "~" : ""}${t("card.km", { km: r.distance_km })})` : ""),
    h("td", { class: "print-phone" }, r.phone || "—"),
    h("td", {}, whenLine(r), r.as_at ? h("small", {}, h("br", {}), t("card.as_at", { date: r.as_at })) : ""),
    h("td", { class: "print-check" }, "☐")));
  sheet.replaceChildren(
    h("h1", {}, t("results.heading", { label: data.label })),
    h("p", {}, [data.point?.label, t("print.printed", { date: fmtDate(new Date().toISOString().slice(0, 10)) })].filter(Boolean).join(" · ")),
    h("table", {},
      h("thead", {}, h("tr", {}, ["#", t("print.col_place"), t("print.col_phone"), t("print.col_when"), t("print.col_called")].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows)),
    h("p", { class: "print-tip" }, t("print.tip")),
    h("p", { class: "print-source" }, t("print.source")));
  window.print();
}

