# ADR-0008: Synonimy mapują na fragmenty nazw świadczeń

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Ludzie piszą „okulista”, „rezonans”, „zacma”. NFZ używa nazw w rodzaju
`PORADNIA OKULISTYCZNA`. Parametr `benefit` w `/queues` działa pełnotekstowo
(dopasowuje fragment). Dokładne nazwy części świadczeń (zwłaszcza szpitalnych)
nie są stabilne między latami.

## Decyzja

- `termin/data/synonyms.toml`: każdy wpis ma `aliases` (język potoczny),
  `query` (fragment do wyszukiwania w NFZ), opcjonalne `children_query` oraz
  `exclude` (fragmenty nazw do odfiltrowania, np. „DLA DZIECI” w wariancie dla
  dorosłych).
- Dopasowanie: normalizacja (małe litery, bez polskich znaków), potem dokładne
  dopasowanie aliasu, a jeśli go nie ma, `rapidfuzz` (WRatio) z progiem i marginesem
  nad drugim kandydatem. Jeśli wynik jest niejednoznaczny, zwracamy kandydatów do wyboru.
- Wyniki z NFZ grupujemy po faktycznej nazwie świadczenia. Użytkownik widzi
  dokładnie, co zwróciło NFZ.
- Gdy nic nie pasuje, wysyłamy tekst bezpośrednio jako `benefit` (fallback).

## Konsekwencje

- Słownik jest odporny na drobne zmiany nazw w NFZ.
- Fragment może złapać za dużo (np. kilka rodzajów poradni). Stąd `exclude` i
  pokazywanie faktycznej nazwy świadczenia w wynikach.
