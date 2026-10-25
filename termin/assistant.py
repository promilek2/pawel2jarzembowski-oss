"""Pomocnik „Jak to działa?”: krótkie odpowiedzi o obsłudze aplikacji (ADR-0014).

Dwa tryby:
- `faq`: bez klucza API. Odpowiadamy najlepiej pasującym wpisem z `data/assistant_faq.toml`.
- `ai`: z kluczem `TERMIN_AI_API_KEY` (albo `ANTHROPIC_API_KEY`) pytanie trafia do Claude
  (Anthropic API) razem z bazą wiedzy jako instrukcją. Gdy API zawiedzie, limit dzienny się
  skończy albo model odmówi, wracamy do trybu `faq`.

Pomocnik nie udziela porad medycznych. Treści rozmów nie zapisujemy ani nie logujemy.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

import anthropic
import httpx2

from termin.config import Settings
from termin.core.text import fold
from termin.logs import get_logger

log = get_logger(__name__)

Mode = Literal["ai", "faq"]
Lang = Literal["pl", "en", "uk"]

MAX_QUESTION_CHARS = 500
MAX_HISTORY = 8
MAX_OUTPUT_TOKENS = 2048
FALLBACK_BETA = "server-side-fallback-2026-07-01"
LANG_NAMES = {"pl": "po polsku", "en": "po angielsku (in English)", "uk": "po ukraińsku"}

UNKNOWN_ANSWER = (
    "Nie mam gotowej odpowiedzi na to pytanie. Mogę podpowiedzieć, jak szukać terminu, "
    "co znaczy „prognoza NFZ”, jak zadzwonić i co powiedzieć, jak obserwować termin, "
    "zainstalować aplikację albo odzyskać konto. Spróbuj zapytać innymi słowami."
)
UNKNOWN_ANSWERS = {
    "pl": UNKNOWN_ANSWER,
    "en": (
        "I don't have a ready answer to that. I can explain how to search, what “NFZ forecast” "
        "means, how to call and what to say, how to watch for an earlier date, install the app "
        "or recover your account. Try asking in other words."
    ),
    "uk": (
        "У мене немає готової відповіді на це запитання. Можу підказати, як шукати, що означає "
        "«прогноз NFZ», як подзвонити й що сказати, як стежити за терміном, встановити "
        "застосунок чи відновити акаунт. Спробуйте запитати іншими словами."
    ),
}
REFUSAL_ANSWER = (
    "Nie mogę pomóc w tej sprawie. Pytaj o obsługę aplikacji Termin: wyszukiwanie, "
    "prognozy NFZ, zapisy przez telefon albo IKP, konto i powiadomienia."
)


REFUSAL_ANSWERS = {
    "pl": REFUSAL_ANSWER,
    "en": (
        "I can't help with that. Ask about using Termin: searching, NFZ forecasts, booking by "
        "phone or IKP, your account and notifications."
    ),
    "uk": (
        "Я не можу допомогти з цим. Запитуйте про користування Termin: пошук, прогнози NFZ, "
        "запис телефоном або через IKP, акаунт і сповіщення."
    ),
}


@dataclass(frozen=True)
class Topic:
    id: str
    keywords: tuple[str, ...]
    answers: dict[str, str]
    weight: int = 1

    @property
    def answer(self) -> str:
        return self.answers["pl"]

    def answer_in(self, lang: str) -> str:
        return self.answers.get(lang) or self.answers["pl"]


@dataclass(frozen=True)
class Answer:
    reply: str
    mode: Mode
    topic: str | None = None


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str


def _keyword(raw: str) -> str:
    # Spacja na końcu znaczy „całe słowo” (np. „leki ”), więc zostaje po normalizacji.
    return fold(raw) + (" " if raw.endswith(" ") else "")


@lru_cache(maxsize=1)
def topics() -> tuple[Topic, ...]:
    return tuple(
        Topic(
            id=t["id"],
            keywords=tuple(
                dict.fromkeys(  # bez powtórzeń: „nfz” w trzech językach liczy się raz
                    _keyword(k)
                    for key in ("keywords", "keywords_en", "keywords_uk")
                    for k in t.get(key, [])
                )
            ),
            answers={
                lang: t[key].strip()
                for lang, key in (("pl", "answer"), ("en", "answer_en"), ("uk", "answer_uk"))
                if key in t
            },
            weight=int(t.get("weight", 1)),
        )
        for t in _data()["topic"]
    )


def _haystack(text: str) -> str:
    # Słowo kluczowe pasuje od początku wyrazu: szukamy „ rdzeń” w „ tekst ”.
    return f" {fold(text)} "


def match_topic(question: str) -> Topic | None:
    """Najlepiej pasujący temat (suma wag trafionych słów); None, gdy nic nie pasuje."""
    text = _haystack(question)
    best: Topic | None = None
    best_score = 0
    for topic in topics():
        score = sum(topic.weight for kw in topic.keywords if f" {kw}" in text)
        if score > best_score:
            best, best_score = topic, score
    return best


def faq_answer(question: str, lang: str = "pl") -> Answer:
    topic = match_topic(question)
    if topic is None:
        return Answer(UNKNOWN_ANSWERS.get(lang, UNKNOWN_ANSWER), "faq")
    return Answer(topic.answer_in(lang), "faq", topic.id)


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    raw = resources.files("termin.data").joinpath("assistant_faq.toml").read_text("utf-8")
    return tomllib.loads(raw)


@lru_cache(maxsize=4)
def system_prompt(lang: Lang = "pl") -> str:
    """Instrukcja dla modelu: zasady z `rules` i cała baza wiedzy (stała, więc trafia w cache)."""
    knowledge = "\n\n".join(t.answer for t in topics())
    rules = str(_data()["rules"]).replace("{lang}", LANG_NAMES[lang])
    return f"{rules.strip()}\n\nWiedza o aplikacji:\n\n{knowledge}"


def clean_history(turns: list[Turn]) -> list[Turn]:
    """Ostatnie tury zaczynające się od pytania; skraca zbyt długie wiadomości."""
    recent = turns[-MAX_HISTORY:]
    while recent and recent[0].role != "user":
        recent = recent[1:]
    return [Turn(t.role, t.content.strip()[: MAX_QUESTION_CHARS * 8]) for t in recent]


def _supports_fallbacks(model: str) -> bool:
    # Haiku nie ma zapasowego modelu po stronie serwera (lista modeli zwraca 400).
    return re.match(r"claude-(opus|sonnet|fable)-", model) is not None


def _supports_effort(model: str) -> bool:
    return re.match(r"claude-[a-z]+-5", model) is not None


class Assistant:
    """Odpowiada na pytania o obsługę aplikacji. Jedna instancja na proces."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self.settings = settings
        self._today = today
        self._day: dt.date | None = None
        self._used_today = 0
        key = settings.ai_api_key.get_secret_value() if settings.ai_api_key else None
        self._client: anthropic.AsyncAnthropic | None = None
        if key and settings.ai_enabled:
            http_client = (
                anthropic.DefaultAsyncHttpxClient(transport=transport)
                if transport is not None
                else None
            )
            self._client = anthropic.AsyncAnthropic(
                api_key=key,
                timeout=settings.ai_timeout_s,
                max_retries=1,
                http_client=http_client,
            )

    @property
    def ai_available(self) -> bool:
        return self._client is not None

    def _take_budget(self) -> bool:
        today = self._today()
        if self._day != today:
            self._day, self._used_today = today, 0
        if self._used_today >= self.settings.ai_daily_limit:
            return False
        self._used_today += 1
        return True

    async def answer(self, turns: list[Turn], lang: Lang = "pl") -> Answer:
        history = clean_history(turns)
        if not history or history[-1].role != "user":
            return Answer(UNKNOWN_ANSWERS[lang], "faq")
        question = history[-1].content
        if self._client is None:
            return faq_answer(question, lang)
        if not self._take_budget():
            log.warning("assistant.daily_limit")
            return faq_answer(question, lang)
        try:
            return await self._ask(history, lang)
        except anthropic.APIStatusError as exc:
            log.warning("assistant.api_error", status=exc.status_code)
        except anthropic.APIConnectionError as exc:
            log.warning("assistant.connection_error", reason=type(exc).__name__)
        return faq_answer(question, lang)

    async def _ask(self, history: list[Turn], lang: Lang) -> Answer:
        assert self._client is not None
        model = self.settings.ai_model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system_prompt(lang),
            "messages": [{"role": t.role, "content": t.content} for t in history],
            "cache_control": {"type": "ephemeral"},
        }
        if _supports_effort(model):
            params["output_config"] = {"effort": "low"}
        if _supports_fallbacks(model):
            response = await self._client.beta.messages.create(
                **params, betas=[FALLBACK_BETA], fallbacks="default"
            )
        else:
            response = await self._client.beta.messages.create(**params)
        if response.stop_reason == "refusal":
            log.info("assistant.refusal")
            return Answer(REFUSAL_ANSWERS[lang], "ai")
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            log.warning("assistant.empty_reply", stop_reason=response.stop_reason)
            return faq_answer(history[-1].content, lang)
        usage = response.usage
        log.info(
            "assistant.reply",
            model=response.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cache_read_input_tokens or 0,
        )
        return Answer(text, "ai")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
