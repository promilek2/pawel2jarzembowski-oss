# ADR-0014: Pomocnik „Jak to działa?” (małe okienko, Claude API opcjonalnie)

- Status: przyjęty
- Data: 2026-10-25

## Kontekst

Użytkownik poprosił o „małe okienko czatu AI, które mówi, gdzie co jest i jak”, ale „nie tak,
że wpychasz”. Aplikacja ma dużo funkcji (prognoza PCUŚ, zapisy przez IKP, obserwowanie, konto,
2FA, instalacja PWA), a odpowiedzi na typowe pytania są krótkie i stałe. Zasady projektu: bez
porad medycznych, bez śledzenia, nie logujemy danych wrażliwych, testy bez sieci.

## Decyzja

- Przycisk „? Pomoc” w nagłówku (obok „A+”; pływający przycisk w rogu zasłaniał wyniki na telefonie). Nic nie wyskakuje samo. Kod okienka
  (`static/chat.js`, `chat.css`) ładuje się dopiero po kliknięciu, więc pierwszy pakiet rośnie
  o ok. 2 KB (budżet 150 KB, osobny budżet 12 KB na czat w teście).
- Pod odpowiedzią przycisk „Pokaż mi” otwiera właściwe miejsce (instalacja, logowanie, opcje, mapa, Moje sprawy); temat odpowiedzi AI ustalamy dopasowaniem pytania do bazy wiedzy.
- Okienko to nie-modalny `<dialog>`: strona działa dalej, Escape zamyka i oddaje fokus
  przyciskowi, log ma `role="log"` i `aria-live`.
- Baza wiedzy w `termin/data/assistant_faq.toml` (po polsku, angielsku i ukraińsku): tematy ze słowami kluczowymi i krótkimi
  odpowiedziami (gdzie kliknąć). Bez klucza API (domyślnie) pomocnik odpowiada najlepiej
  pasującym tematem. Działa od razu na każdej instancji i nic nie wychodzi poza serwer.
- Z kluczem `TERMIN_AI_API_KEY` (albo `ANTHROPIC_API_KEY`) pytanie idzie do Claude przez
  oficjalne SDK `anthropic` (`AsyncAnthropic`). Instrukcja systemowa = zasady z pliku TOML +
  cała baza wiedzy (stała, z `cache_control`, więc kolejne pytania czytają ją z cache).
  Domyślny model `claude-opus-5-5` z `effort: low` (myślenie jest w nim zawsze włączone);
  tańsza opcja: `TERMIN_AI_MODEL=claude-haiku-5-5`. Dla Opus/Sonnet/Fable wysyłamy
  `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`); Haiku nie ma zapasowego
  modelu po stronie serwera, więc dla niego tego pola nie ma. `stop_reason == "refusal"` daje
  grzeczną odmowę.
- Każdy błąd API (klucz, limit, sieć, przeciążenie), pusta odpowiedź albo wyczerpany limit
  dzienny (`TERMIN_AI_DAILY_LIMIT`, domyślnie 200 pytań na instancję) → odpowiedź z bazy FAQ.
  Limit na IP: `TERMIN_AI_RATE_LIMIT` (domyślnie 6/min i 40/dzień). Pytanie maks. 500 znaków,
  historia maks. 8 wiadomości.
- Prywatność: rozmowa tylko w pamięci strony (bez localStorage, bez bazy). Treść nie trafia do
  logów (`messages`, `question`, `reply`, `content`, `ai_api_key` w `SENSITIVE_KEYS`; logujemy
  tylko liczbę tokenów). W trybie AI przed pierwszym pytaniem okienko pokazuje informację, że
  pytanie trafi do Anthropic i żeby nie wpisywać danych osobowych ani o zdrowiu. Polityka
  prywatności wymienia Anthropic jako odbiorcę tylko wtedy, gdy instancja ma klucz.
- Zasady modelu: odpowiadać krótko, zwykłym tekstem, tylko o aplikacji/NFZ/zapisach; bez porad
  medycznych (odesłanie do lekarza, 112), bez wskazywania „najlepszego lekarza”.

## Konsekwencje

- Pomocnik działa bez żadnej konfiguracji; AI to opcja operatora, który płaci za API
  i powinien zaakceptować umowę powierzenia (DPA) Anthropic.
- Nowa zależność `anthropic` (z `httpx2`). Testy wstrzykują `httpx2.MockTransport` przez
  `Transports.ai`, więc nie wychodzą do sieci.
- Bazę wiedzy trzeba aktualizować razem z interfejsem (nazwy przycisków). Testy sprawdzają
  dopasowanie typowych pytań do tematów.
