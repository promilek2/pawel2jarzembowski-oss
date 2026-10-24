// Service worker: powłoka aplikacji offline + ostatnie wyniki (network-first dla API).
const VERSION = "termin-v1";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/i18n/pl.json",
  "/static/i18n/en.json",
  "/static/i18n/uk.json",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
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

  if (url.pathname.startsWith("/api/search") || url.pathname === "/api/config") {
    // network-first: świeże dane, a bez sieci ostatnia odpowiedź z cache
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then((hit) => hit || Response.error())),
    );
    return;
  }
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
