# ADR-0009: Kierunek wizualny „Numerek”

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Pierwsza wersja panelu była poprawna i dostępna, ale bezosobowa: wyglądała jak dowolny
formularz. Chcemy wyglądu, który od razu mówi, o co chodzi, i jest łatwy do zapamiętania,
a przy tym zostaje czytelny dla seniorów i spełnia WCAG 2.1 AA.

## Decyzja

- Metafora: **bilet z automatu w rejestracji przychodni** („numerek”). Każdy wynik to bilet:
  odcinek z dużą datą (jak numer w kolejce), perforacja z wcięciami, część z placówką i
  akcjami. Pierwszy wynik ma ciemny odcinek i pomarańczowy znacznik „Nr 1”. Na telefonie
  perforacja jest pozioma, na komputerze bilet jest poziomy, a perforacja pionowa.
- Wyszukiwarka to ciemny „automat” (panel w kolorze atramentu) z przełącznikami
  segmentowymi zamiast list rozwijanych.
- Paleta: tło `#edeff3`, bilet `#ffffff`, atrament `#12182b`, tekst drugorzędny `#4a5163`,
  pomarańcz sygnałowy `#ff6a1a` (zawsze z atramentowym tekstem, kontrast 6,2:1), pomarańcz
  jako tekst i ramka `#b23f00`/`#8a3300`. Mapa cieplna: skala od niebieskiego do pomarańczowego
  (różnica jasności, nie tylko barwy).
- Typografia: Bricolage Grotesque (OFL, hostowany lokalnie, ~60 KB woff2) na nagłówki i daty,
  krój systemowy na tekst.
- Makiety: canvas w Claude Design („Termin: nowy wygląd”), trzy ekrany: start i wyniki na
  telefonie, wyniki i statystyki na komputerze.

## Konsekwencje

- Pomarańcz nigdy nie służy jako kolor tekstu na jasnym tle (za mały kontrast). Do tego jest
  `--signal-ink`.
- Dochodzi ~60 KB kroju (cache w service workerze). Kod własny dalej mieści się w budżecie
  150 KB (test).
- Testy e2e i axe obejmują nowy wygląd, w tym ciemny motyw i tryb dużego tekstu.
