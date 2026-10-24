"""Teksty backendu z plików `termin/locales/<język>.toml` (domyślnie polski)."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import resources
from typing import Any

from termin.core.text import plural_pl

DEFAULT_LANG = "pl"


@lru_cache(maxsize=8)
def _load(lang: str) -> dict[str, Any]:
    try:
        raw = resources.files("termin.locales").joinpath(f"{lang}.toml").read_text("utf-8")
    except FileNotFoundError:
        if lang == DEFAULT_LANG:
            raise
        return _load(DEFAULT_LANG)
    return tomllib.loads(raw)


def t(key: str, lang: str = DEFAULT_LANG, **params: Any) -> str:
    """Tekst dla klucza 'sekcja.nazwa' z parametrami. Brak klucza → sam klucz."""
    data = _load(lang)
    section, _, name = key.partition(".")
    template = data.get(section, {}).get(name)
    if not isinstance(template, str):
        return key
    values: dict[str, Any] = {
        k: format_number(v, lang) if isinstance(v, float) else v for k, v in params.items()
    }
    for param, forms in data.get("plurals", {}).items():
        count = params.get(param)
        if isinstance(count, int):
            values[f"{param}_word"] = plural_pl(count, *forms)
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def format_number(value: float, lang: str = DEFAULT_LANG) -> str:
    """2.0 → '2', 2.5 → '2,5' (po polsku i ukraińsku przecinek dziesiętny)."""
    text = f"{value:.1f}".removesuffix(".0")
    return text.replace(".", ",") if lang in {"pl", "uk"} else text


def when(days: int, lang: str = DEFAULT_LANG) -> str:
    """'dziś', 'jutro', 'za 5 dni', '3 dni temu'."""
    if days == 0:
        return t("when.today", lang)
    if days == 1:
        return t("when.tomorrow", lang)
    if days > 1:
        return t("when.in_days", lang, days=days)
    return t("when.days_ago", lang, days=-days)
