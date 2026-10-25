"""Rozumienie, czego człowiek szuka: synonimy + fuzzy matching (ADR-0008).

„okulsta”, „kardilog”, „zacma”, „MRI kolana” → wpis słownika → fragment nazwy
świadczenia do wyszukania w NFZ i filtr wariantu (dzieci / dorośli).
"""

from __future__ import annotations

import itertools
import re
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Literal

from rapidfuzz import fuzz, process

from termin.core.models import QueueEntry
from termin.core.text import fold

ACCEPT_SCORE = 82.0
"""Minimalny wynik, żeby przyjąć dopasowanie bez pytania."""
MARGIN = 6.0
"""O ile najlepszy kandydat musi wyprzedzać drugiego, żeby nie pytać."""
SUGGEST_SCORE = 60.0
"""Minimalny wynik, żeby w ogóle coś podpowiedzieć."""

_CHILD_PATTERNS = re.compile(
    r"\b(dla dzieci|dzieci|dziecka|dziecko|dzieciecy|dziecieca|dzieciece|pediatr\w*|niemowl\w*)\b"
)
_ADULT_PATTERNS = re.compile(r"\b(dla doroslych|doroslych|dorosly|dorosla|dorosli)\b")


class Variant(StrEnum):
    ADULT = "dorosli"
    CHILD = "dzieci"
    ANY = "wszystkie"


@dataclass(frozen=True, slots=True)
class BenefitEntry:
    id: str
    label: str
    query: str
    aliases: tuple[str, ...]
    kind: str = "poradnia"
    children_query: str | None = None
    exclude: tuple[str, ...] = ()
    note: str | None = None
    """Informacja pokazywana przy wynikach, np. że zapisy prowadzi Centralna e-Rejestracja."""

    @property
    def has_child_variant(self) -> bool:
        return self.children_query is not None


@dataclass(frozen=True, slots=True)
class Candidate:
    entry: BenefitEntry
    score: float
    matched: str = ""
    """Klucz słownika (znormalizowany alias), który dał ten wynik."""

    def is_confident_for(self, cleaned: str) -> bool:
        """Czy to literówka/odmiana, a nie tylko fragment dłuższej nazwy.

        „okulsta” → „okulista”: tak. „kolano” → „endoproteza kolana”: nie, trzeba dopytać.
        """
        if not self.matched or len(self.matched) <= 4:
            return True
        if len(cleaned) >= 0.6 * len(self.matched):
            return True
        return fuzz.ratio(cleaned, self.matched) >= 80


ResolutionStatus = Literal["ok", "ambiguous", "choose_variant", "fallback"]


@dataclass(frozen=True, slots=True)
class Resolution:
    """Wynik interpretacji zapytania.

    - ``ok``: wiadomo, czego szukać (``query``, ``variant``),
    - ``choose_variant``: wiadomo co, ale trzeba zapytać: dla dzieci czy dla dorosłych,
    - ``ambiguous``: kilku kandydatów, trzeba zapytać który (``candidates``),
    - ``fallback``: nic nie pasuje, wysyłamy tekst do NFZ wprost.
    """

    status: ResolutionStatus
    text: str
    query: str
    entry: BenefitEntry | None = None
    variant: Variant = Variant.ANY
    candidates: tuple[Candidate, ...] = ()
    exclude: tuple[str, ...] = field(default=())

    @property
    def needs_input(self) -> bool:
        return self.status in {"ambiguous", "choose_variant"}

    @property
    def label(self) -> str:
        if self.entry is None:
            return self.query
        suffix = {Variant.CHILD: " dla dzieci", Variant.ADULT: " dla dorosłych"}.get(
            self.variant, ""
        )
        return self.entry.label + suffix

    def matches(self, entry: QueueEntry) -> bool:
        """Czy pozycja z NFZ pasuje do wariantu (dzieci/dorośli, wykluczenia)."""
        name = fold(entry.benefit)
        # Świadczenie ze słownika: nazwa musi zawierać fragment (chroni przed szerszym
        # dopasowaniem pełnotekstowym NFZ albo zignorowanym parametrem `benefit`).
        if self.entry is not None:
            fragments = (self.entry.query, self.entry.children_query or self.entry.query)
            if not any(fold(f) in name for f in fragments):
                return False
        if any(fold(ex) in name for ex in self.exclude):
            return False
        if self.variant is Variant.CHILD:
            return entry.is_for_children
        if self.variant is Variant.ADULT:
            return not entry.is_for_children
        return True

    def with_variant(self, variant: Variant) -> Resolution:
        if self.entry is None:
            return self
        return _resolution_for(self.entry, self.text, variant)


def detect_variant(text: str) -> tuple[Variant | None, str]:
    """Wykrywa „dla dzieci”/„dla dorosłych” w tekście i zwraca tekst bez tych słów."""
    folded = fold(text)
    variant: Variant | None = None
    if _CHILD_PATTERNS.search(folded):
        variant = Variant.CHILD
        folded = _CHILD_PATTERNS.sub(" ", folded)
    elif _ADULT_PATTERNS.search(folded):
        variant = Variant.ADULT
        folded = _ADULT_PATTERNS.sub(" ", folded)
    return variant, " ".join(folded.split())


def _resolution_for(entry: BenefitEntry, text: str, variant: Variant) -> Resolution:
    if variant is Variant.CHILD and entry.children_query:
        return Resolution("ok", text, entry.children_query, entry, Variant.CHILD)
    if variant is Variant.ADULT and entry.has_child_variant:
        return Resolution("ok", text, entry.query, entry, Variant.ADULT, exclude=entry.exclude)
    return Resolution("ok", text, entry.query, entry, Variant.ANY)


class BenefitCatalog:
    def __init__(self, entries: list[BenefitEntry]) -> None:
        self.entries = entries
        self._by_id = {e.id: e for e in entries}
        self._choices: list[str] = []
        self._owners: list[BenefitEntry] = []
        self._exact: dict[str, BenefitEntry] = {}
        for entry in entries:
            keys = {fold(a) for a in entry.aliases} | {fold(entry.id.replace("-", " "))}
            keys.add(fold(entry.label.split("(")[0]))
            for key in sorted(keys):
                self._exact.setdefault(key, entry)
                self._choices.append(key)
                self._owners.append(entry)

    @classmethod
    def load(cls, path: Path | None = None) -> BenefitCatalog:
        if path is None:
            raw = resources.files("termin.data").joinpath("synonyms.toml").read_text("utf-8")
        else:
            raw = path.read_text("utf-8")
        data = tomllib.loads(raw)
        entries = [
            BenefitEntry(
                id=item["id"],
                label=item["label"],
                query=item["query"],
                aliases=tuple(item.get("aliases", ())),
                kind=item.get("kind", "poradnia"),
                children_query=item.get("children_query"),
                exclude=tuple(item.get("exclude", ())),
                note=item.get("note"),
            )
            for item in data.get("benefit", [])
        ]
        return cls(entries)

    def get(self, entry_id: str) -> BenefitEntry | None:
        return self._by_id.get(entry_id)

    def suggest(self, text: str, limit: int = 8) -> list[Candidate]:
        """Kandydaci posortowani malejąco po wyniku (do autouzupełniania i pytań)."""
        _, cleaned = detect_variant(text)
        if not cleaned:
            return []
        best: dict[str, Candidate] = {}
        # Dokładne trafienia: całe zapytanie albo pojedyncze słowo/para słów („rezonans kolana”).
        tokens = cleaned.split()
        grams = [cleaned, *tokens, *(" ".join(p) for p in itertools.pairwise(tokens))]
        for gram in grams:
            hit = self._exact.get(gram)
            if hit is not None and hit.id not in best:
                best[hit.id] = Candidate(hit, 100.0, gram)
        matches = process.extract(cleaned, self._choices, scorer=_score, limit=max(limit * 4, 20))
        for choice, score, index in matches:
            owner = self._owners[index]
            if score >= SUGGEST_SCORE and (owner.id not in best or best[owner.id].score < score):
                best[owner.id] = Candidate(owner, float(score), choice)
        ranked = sorted(best.values(), key=lambda c: (-c.score, c.entry.label))
        return ranked[:limit]

    def resolve(self, text: str, variant: Variant | None = None) -> Resolution:
        detected, cleaned = detect_variant(text)
        variant = variant or detected
        if not cleaned:
            return Resolution("fallback", text, text.strip().upper())

        candidates = self.suggest(text, limit=5)
        exact = self._exact.get(cleaned)
        if exact is not None:
            chosen: BenefitEntry | None = exact
        elif (
            candidates
            and candidates[0].score >= ACCEPT_SCORE
            and candidates[0].is_confident_for(cleaned)
            and (len(candidates) == 1 or candidates[0].score - candidates[1].score >= MARGIN)
        ):
            chosen = candidates[0].entry
        elif candidates:
            return Resolution(
                "ambiguous", text, candidates[0].entry.query, candidates=tuple(candidates)
            )
        else:
            return Resolution("fallback", text, text.strip().upper())

        assert chosen is not None
        if variant is None and chosen.has_child_variant:
            return Resolution(
                "choose_variant",
                text,
                chosen.query,
                chosen,
                Variant.ANY,
                candidates=tuple(candidates),
            )
        return _resolution_for(chosen, text, variant or Variant.ANY)


def _score(query: str, choice: str, **_: object) -> float:
    """WRatio, ale krótkie aliasy (np. „tk”, „endo”) liczą się tylko jako całe słowo."""
    if len(choice) <= 4:
        return 100.0 if choice in query.split() else 0.0
    score = float(fuzz.WRatio(query, choice))
    if len(query) >= 3:
        # autouzupełnianie: „kard” → „kardiolog” przed „echokardiografia”
        if choice.startswith(query):
            score = max(score, 95.0)
        elif any(word.startswith(query) for word in choice.split()):
            score = max(score, 92.0)
    return score
