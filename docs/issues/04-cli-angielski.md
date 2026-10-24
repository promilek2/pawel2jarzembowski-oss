# Angielskie teksty CLI i powiadomień

Etykiety: `good first issue`, `i18n`

## Kontekst
Teksty uzasadnień i ostrzeżeń w CLI i powiadomieniach pochodzą z `termin/locales/pl.toml`
(funkcja `t()` w `termin/core/i18n.py` obsługuje już parametr `lang`). Brakuje wersji angielskiej.

## Do zrobienia
- Dodaj `termin/locales/en.toml` z tymi samymi kluczami co `pl.toml` (odmiana: `[plurals]`
  `days = ["day", "days", "days"]`). Funkcja `plural_pl` nie pasuje do angielskiego, więc dodaj
  prostą regułę one/other.
- Dodaj opcję `--jezyk/-j` do `termin szukaj` i przekaż ją do `RankingOptions.lang`.
- Test: klucze `en.toml` = klucze `pl.toml`.

## Gotowe, gdy
- `termin szukaj okulista -g Gdynia -j en` pokazuje „Why here” po angielsku,
- `pytest` przechodzi.
