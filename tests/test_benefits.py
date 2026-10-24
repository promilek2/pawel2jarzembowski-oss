from __future__ import annotations

import tomllib
from importlib import resources

import pytest

from termin.core.benefits import BenefitCatalog, Variant, detect_variant
from termin.core.models import QueueEntry


@pytest.fixture(scope="module")
def catalog() -> BenefitCatalog:
    return BenefitCatalog.load()


@pytest.mark.parametrize(
    ("text", "entry_id"),
    [
        ("okulsta", "okulista"),  # literówka
        ("kardilog", "kardiolog"),
        ("neurolg", "neurolog"),
        ("zacma", "zacma"),  # brak polskich znaków
        ("Zaćma", "zacma"),
        ("rezonans", "rezonans"),
        ("MRI", "rezonans"),
        ("MRI kolana", "rezonans"),
        ("rezonans kolana", "rezonans"),
        ("ortopeda", "ortopeda"),
        ("endokrynolog", "endokrynolog"),
        ("okulista", "okulista"),
        ("USG", "usg"),
        ("tomograf", "tomograf"),
        ("TK głowy", "tomograf"),
        ("gastroskopia", "gastroskopia"),
        ("endoproteza biodra", "endoproteza-biodra"),
        ("endoproteza bioder", "endoproteza-biodra"),
        ("kolonoskopa", "kolonoskopia"),
        ("echo serca", "echo-serca"),
    ],
)
def test_resolves_to_entry(catalog: BenefitCatalog, text: str, entry_id: str) -> None:
    res = catalog.resolve(text)
    assert res.entry is not None, res
    assert res.entry.id == entry_id
    assert res.status in {"ok", "choose_variant"}


def test_ambiguous_child_adult_requires_choice(catalog: BenefitCatalog) -> None:
    res = catalog.resolve("okulista")
    assert res.status == "choose_variant"
    assert res.needs_input
    adult = res.with_variant(Variant.ADULT)
    assert adult.status == "ok"
    assert adult.query == "PORADNIA OKULISTYCZNA"
    assert adult.exclude == ("DLA DZIECI",)
    child = res.with_variant(Variant.CHILD)
    assert child.query == "PORADNIA OKULISTYCZNA DLA DZIECI"
    assert child.label.endswith("dla dzieci")


@pytest.mark.parametrize(
    ("text", "variant"),
    [
        ("okulista dla dzieci", Variant.CHILD),
        ("ortopeda dziecięcy", Variant.CHILD),
        ("kardiolog dla dorosłych", Variant.ADULT),
        ("okulsta dorosly", Variant.ADULT),
    ],
)
def test_variant_from_text(catalog: BenefitCatalog, text: str, variant: Variant) -> None:
    res = catalog.resolve(text)
    assert res.status == "ok"
    assert res.variant is variant


def test_explicit_variant_wins(catalog: BenefitCatalog) -> None:
    res = catalog.resolve("okulista", variant=Variant.CHILD)
    assert res.status == "ok"
    assert res.variant is Variant.CHILD


def test_no_child_variant_means_no_question(catalog: BenefitCatalog) -> None:
    res = catalog.resolve("rezonans")
    assert res.status == "ok"
    assert res.variant is Variant.ANY
    assert res.with_variant(Variant.CHILD).variant is Variant.ANY


def test_partial_word_asks_instead_of_guessing(catalog: BenefitCatalog) -> None:
    res = catalog.resolve("kolano")
    assert res.status == "ambiguous"
    ids = [c.entry.id for c in res.candidates]
    assert "endoproteza-kolana" in ids


def test_unknown_falls_back_to_raw_query(catalog: BenefitCatalog) -> None:
    res = catalog.resolve("qwxz brrr")
    assert res.status == "fallback"
    assert res.query == "QWXZ BRRR"
    assert res.label == "QWXZ BRRR"
    assert catalog.resolve("   ").status == "fallback"


def test_suggest_for_autocomplete(catalog: BenefitCatalog) -> None:
    ids = [c.entry.id for c in catalog.suggest("kard")]
    assert ids[0] == "kardiolog"
    assert catalog.suggest("") == []


def test_matches_filters_variant(catalog: BenefitCatalog) -> None:
    adult = catalog.resolve("okulista dla dorosłych")
    child = catalog.resolve("okulista dla dzieci")
    e_adult = QueueEntry(id="1", benefit="PORADNIA OKULISTYCZNA")
    e_child = QueueEntry(id="2", benefit="PORADNIA OKULISTYCZNA DLA DZIECI")
    e_flag = QueueEntry(id="3", benefit="PORADNIA OKULISTYCZNA", benefits_for_children="Y")
    assert adult.matches(e_adult)
    assert not adult.matches(e_child)
    assert not adult.matches(e_flag)
    assert child.matches(e_child)
    assert child.matches(e_flag)
    assert not child.matches(e_adult)


def test_detect_variant() -> None:
    assert detect_variant("Okulista dla DZIECI") == (Variant.CHILD, "okulista")
    assert detect_variant("okulista") == (None, "okulista")


def test_synonyms_file_is_well_formed() -> None:
    raw = resources.files("termin.data").joinpath("synonyms.toml").read_text("utf-8")
    data = tomllib.loads(raw)
    ids = [b["id"] for b in data["benefit"]]
    assert len(ids) == len(set(ids)), "zduplikowane id"
    seen: dict[str, str] = {}
    for item in data["benefit"]:
        assert item["query"] == item["query"].upper(), item["id"]
        assert item["aliases"], item["id"]
        assert item.get("kind", "poradnia") in {"poradnia", "badanie", "zabieg", "szpital"}
        for alias in item["aliases"]:
            from termin.core.text import fold

            key = fold(alias)
            assert key not in seen or seen[key] == item["id"], f"alias {alias!r} w 2 wpisach"
            seen[key] = item["id"]
    required = {"kardiolog", "okulista", "ortopeda", "endokrynolog", "rezonans", "usg",
                "tomograf", "gastroskopia", "endoproteza-biodra", "zacma"}  # fmt: skip
    assert required <= set(ids)
