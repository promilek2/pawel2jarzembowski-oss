// Instalacja PWA i aktualizacje service workera: ładowane po starcie (poza pierwszym pakietem).
import { $, h, t, state, announce, isIos, isStandalone } from "./app.js";

// Nowa wersja aplikacji (nowy service worker): stara mogła przyjść z pamięci telefonu.
export function registerWorker() {
  const hadWorker = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.register("/sw.js").then((reg) => {
    reg.addEventListener("updatefound", () => {
      const worker = reg.installing;
      worker?.addEventListener("statechange", () => {
        if (worker.state !== "activated" || !hadWorker) return;
        const toast = $("#toast");
        toast.replaceChildren(h("p", {}, t("update.ready")), h("div", { class: "toast-actions" },
          h("button", { type: "button", class: "btn btn-signal", onclick: () => location.reload() }, t("update.reload")),
          h("button", { type: "button", class: "btn btn-outline", onclick: () => { toast.hidden = true; } }, t("report.later"))));
        toast.hidden = false;
      });
    });
  }).catch(() => {});
}

export function setupInstall() {
  const button = $("#install-btn");
  if (isStandalone()) return;
  if (isIos() || state.installPrompt) button.hidden = false; // Safari: instrukcja zamiast zdarzenia
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
