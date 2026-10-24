// Service worker: pliki aplikacji offline (stale-while-revalidate), bez danych z API.
const VERSION = "termin-v3";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/i18n/pl.json",
  "/static/i18n/en.json",
  "/static/i18n/uk.json",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/vendor/bricolage/bricolage-grotesque-latin-wght-normal.woff2",
  "/static/vendor/bricolage/bricolage-grotesque-latin-ext-wght-normal.woff2",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;

  // Odpowiedzi API (w tym wyniki wyszukiwania) nie trafiają do cache: mogą zawierać dane
  // osobowe. Wyniki offline zapisuje sama aplikacja, tylko za zgodą (strona /cookies).
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/stop/")) return;

  // stale-while-revalidate dla plików statycznych
  event.respondWith(
    caches.match(event.request, { ignoreSearch: url.pathname === "/" }).then((hit) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => hit);
      return hit || network;
    }),
  );
});
