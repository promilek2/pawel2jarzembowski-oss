# Dodaj synonimy 10 kolejnych świadczeń

Etykiety: `good first issue`, `synonimy`

## Kontekst
Słownik `termin/data/synonyms.toml` tłumaczy zwykły język („laryngolog”) na fragmenty nazw NFZ.
Brakuje w nim wielu świadczeń, więc takie zapytania trafiają do NFZ „na surowo” i często nic nie znajdują.

## Do zrobienia
Dodaj wpisy (albo aliasy) dla co najmniej 10 z listy: proktolog, foniatra, hepatolog,
ortodonta, spirometria, próba wysiłkowa (EKG wysiłkowe), scyntygrafia, PET, badanie
urodynamiczne, poradnia leczenia uzależnień, poradnia osteoporozy, poradnia wad postawy.

1. Sprawdź nazwy w NFZ: `termin swiadczenia <fragment>`.
2. Pamiętaj: `query` to FRAGMENT nazwy wielkimi literami, a aliasy to nazwy świadczenia
   (nie objawy).
3. Dla każdego nowego wpisu dopisz przypadek do `test_resolves_to_entry` w `tests/test_benefits.py`.

## Gotowe, gdy
- `pytest tests/test_benefits.py` przechodzi,
- każdy nowy wpis zwraca wyniki w `termin szukaj <alias> -w mazowieckie`.
