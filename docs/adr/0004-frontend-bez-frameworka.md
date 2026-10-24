# ADR-0004: Frontend w czystym HTML + JS, Leaflet ładowany leniwie i hostowany u nas

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Panel ma działać na tanim telefonie (360 px), być dostępny (WCAG 2.1 AA),
działać offline (PWA) i mieścić się w ~150 KB bez kafelków mapy. Kontrybutorzy
to często osoby bez doświadczenia z frontendowymi toolchainami.

## Decyzja

- Czysty HTML + CSS + JS (moduły ES), **bez kroku budowania**. Svelte/Vite
  nie dałby tu proporcjonalnej korzyści.
- Lista wyników jest głównym nośnikiem informacji. Mapa jest dodatkiem,
  ładowanym dopiero po kliknięciu „Pokaż mapę”.
- Leaflet 1.9.4 (BSD-2) jest skopiowany do `termin/web/static/vendor/` z
  rejestru npm, bez CDN. Żadnych żądań do stron trzecich poza kafelkami mapy
  (konfigurowalny URL, domyślnie OSM).
- Teksty interfejsu w `termin/web/static/i18n/{pl,en,uk}.json`.

## Konsekwencje

- Kod własny (HTML+CSS+JS) zajmuje kilkadziesiąt KB. Leaflet (~150 KB, ~43 KB gzip)
  dochodzi tylko, gdy ktoś otworzy mapę.
- Brak reaktywnego frameworka oznacza ręczne renderowanie DOM. Przy tej
  skali UI to akceptowalne.
