# Filtr „tylko z podjazdem/windą” w panelu

Etykiety: `good first issue`, `frontend`, `dostępność`

## Kontekst
NFZ podaje udogodnienia placówek (`toilet`, `ramp`, `car-park`, `elevator`). Panel już je pokazuje
w karcie wyniku (`accessibility` w odpowiedzi `/api/search`), ale nie da się po nich filtrować.
Dla osób na wózku albo z balkonikiem to kluczowa informacja.

## Do zrobienia
- W sekcji „Więcej opcji” (`termin/web/static/index.html`) dodaj pola wyboru „Tylko z podjazdem”
  i „Tylko z windą”.
- Filtrowanie zrób po stronie przeglądarki w `renderResults` (`app.js`) albo jako parametr
  `/api/search`. Opisz w PR, który wariant wybrałeś/wybrałaś i dlaczego.
- Teksty dodaj do `pl.json`, `en.json` i `uk.json`.

## Gotowe, gdy
- test e2e w `tests/e2e/test_e2e.py` sprawdza, że filtr ukrywa placówki bez podjazdu,
- axe nadal nie zgłasza naruszeń (`pytest -m e2e`).
