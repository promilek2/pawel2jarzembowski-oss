"""Normalizacja tekstu: małe litery, bez polskich znaków, pojedyncze spacje."""

from __future__ import annotations

import re
import unicodedata

_POLISH = str.maketrans({"ł": "l", "Ł": "l"})
_SPACES = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s-]")


def fold(text: str) -> str:
    """'Poradnia  Okulistyczna' → 'poradnia okulistyczna'; 'Zaćma' → 'zacma'."""
    text = text.translate(_POLISH)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = _NON_WORD.sub(" ", stripped.lower())
    return _SPACES.sub(" ", stripped).strip()


def plural_pl(n: int, one: str, few: str, many: str) -> str:
    """Polska odmiana liczebnika: 1 dzień, 2 dni, 5 dni; 1 osoba, 2 osoby, 5 osób."""
    n_abs = abs(n)
    if n_abs == 1:
        return one
    if 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
        return few
    return many


_PCUS_UNITS = (
    (re.compile(r"(\d+)\s*(?:lat|lata|rok|roku)\b"), 365),
    (re.compile(r"(\d+)\s*mies"), 30),
    (re.compile(r"(\d+)\s*tyg"), 7),
    (re.compile(r"(\d+)\s*(?:dni|dzien|d)\b"), 1),
)


def parse_pcus(text: str | None) -> int | None:
    """Prognozowany czas oczekiwania NFZ (PCUŚ) w dniach, np. „1 mies. 3 tyg.” → 51.

    NFZ podaje PCUŚ tekstem: poniżej miesiąca w dniach, powyżej w miesiącach i tygodniach.
    Miesiąc liczymy jako 30 dni (próg, przy którym NFZ przechodzi z dni na miesiące).
    """
    if not text:
        return None
    folded = fold(text)
    if folded.isdigit():
        return int(folded)
    total, found = 0, False
    for pattern, days in _PCUS_UNITS:
        for match in pattern.finditer(folded):
            total += int(match.group(1)) * days
            found = True
    return total if found else None
